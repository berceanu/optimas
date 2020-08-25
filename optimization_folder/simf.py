import os
import time
import numpy as np
import jinja2

from libensemble.executors.executor import Executor
from libensemble.message_numbers import WORKER_DONE, TASK_FAILED

# Import user-defined parameters
from sim_specific.varying_parameters import varying_parameters

"""
This file is part of the suite of scripts to use LibEnsemble on top of WarpX
simulations. It defines a sim_f function that takes LibEnsemble history and
input parameters, run a WarpX simulation and returns 'f'.
"""


def run_fbpic(H, persis_info, sim_specs, libE_info):
    """
    This function runs a WarpX simulation and returns quantity 'f' as well as
    other physical quantities measured in the run for convenience. Status check
    is done periodically on the simulation, provided by LibEnsemble.
    """
    # By default, indicate that task failed
    calc_status = TASK_FAILED

    # Modify the input script, with the value passed in H
    values = H['x'][0]
    names = varying_parameters.keys() 
    # Note: The order of keys is well-defined here, 
    # since `varying_parameters` is an OrderedDict
    values_dict = { n: v for n, v in zip(names, values) }
    with open('template_fbpic_script.py', 'r') as f:
        template = jinja2.Template( f.read() )
    with open('fbpic_script.py', 'w') as f:
        f.write( template.render(values_dict) )

    # Passed to command line in addition to the executable.
    exctr = Executor.executor  # Get Executor
    machine_specs = sim_specs['user']['machine_specs']
    time_limit = machine_specs['sim_kill_minutes'] * 60.
    # Launch the executor to actually run the WarpX simulation
    if machine_specs['name'] == 'summit':
        task = exctr.submit(calc_type='sim',
                            extra_args=machine_specs['extra_args'],
                            app_args='fbpic_script.py',
                            stdout='out.txt',
                            stderr='err.txt',
                            wait_on_run=True)
    else:
        task = exctr.submit(calc_type='sim',
                            num_procs=machine_specs['cores'],
                            app_args='fbpic_script.py',
                            stdout='out.txt',
                            stderr='err.txt',
                            wait_on_run=True)

    # Periodically check the status of the simulation
    poll_interval = 10  # secs
    while(not task.finished):
        time.sleep(poll_interval)
        task.poll()
        if task.runtime > time_limit:
            task.kill()  # Timeout

    # Set calc_status with optional prints.
    if task.finished:
        if task.state == 'FINISHED':
            calc_status = WORKER_DONE
        elif task.state == 'FAILED':
            calc_status = TASK_FAILED
        if task.state not in ['FINISHED', 'FAILED', 'USER_KILLED']:
            print("Warning: Task {} in unknown state {}. Error code {}"
                  .format(task.name, task.state, task.errcode))

    # Data analysis from the last simulation
    if calc_status == WORKER_DONE:
        from openpmd_viewer.addons import LpaDiagnostics
        ts = LpaDiagnostics( os.path.join(task.workdir, 'lab_diags/hdf5') )

        select = {'x':[-100.e-6,100.e-6], 'y':[-100.e-6, 100.e-6]}

        charge_i = ts.get_charge( iteration=ts.iterations[0], select=select )
        emittance_i = ts.get_emittance( iteration=ts.iterations[0], 
                                        select=select )[0]
        charge_f = ts.get_charge( iteration=ts.iterations[-1],
                                  select=select )
        emittance_f = ts.get_emittance( iteration=ts.iterations[-1],
                                        select=select )[0]
        energy_avg, energy_std = ts.get_mean_gamma( 
            iteration=ts.iterations[-1], select=select ) 
    
        # Pass the sim output values to LibEnsemble.
        # When optimization is ON, 'f' is then passed to the generating function
        # gen_f to generate new inputs for next runs.
        # All other parameters are here just for convenience.
        libE_output = np.zeros(1, dtype=sim_specs['out'])
        # Build a quantity to minimize (f) that encompasses
        # emittance AND charge loss 1% charge loss has the
        # same impact as doubling the initial emittance.
        # we minimize f!
        libE_output['f'] = emittance_f + emittance_i*(1.-charge_f/charge_i)*100
        libE_output['energy_std'] = energy_std
        libE_output['energy_avg'] = energy_avg
        libE_output['charge'] = charge_f
        libE_output['emittance'] = emittance_f
        for i, name in enumerate(varying_parameters.keys()):
            libE_output[name] = H['x'][0][i]
        
    return libE_output, persis_info, calc_status