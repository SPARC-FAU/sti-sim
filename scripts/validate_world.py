"""Build-time physics smoke test: load the real hospital and step DART five times.

No GUI, robot or GPU required. Check output as well as exit status because the
Fortress command wrapper can return zero after its server subprocess crashes.
"""
import os
from pathlib import Path
import re
import subprocess
import uuid


def validate(world):
    world = Path(world).resolve()
    env = os.environ.copy()
    env['IGN_PARTITION'] = 'sti-world-test-' + uuid.uuid4().hex
    env['IGN_GAZEBO_RESOURCE_PATH'] = str(world.parent)
    env['GZ_SIM_RESOURCE_PATH'] = str(world.parent)
    result = subprocess.run(['ign', 'gazebo', '-s', '-r', '-v', '4', '--iterations', '5', str(world)],
                            env=env, capture_output=True, text=True, timeout=90)
    output = re.sub(r'\x1b\[[0-9;]*m', '', result.stdout + result.stderr)
    fatal = ('[Err]', 'Stack trace', 'Segmentation fault', 'Aborted', 'does not have a normal count')
    if result.returncode or any(text in output for text in fatal) or f'World [{world.stem}] initialized' not in output:
        print(output)
        raise RuntimeError('Hospital physics smoke test failed; refusing to build a broken scene')
    print('PASS: hospital loaded in Fortress and completed five physics iterations')


if __name__ == '__main__':
    validate('/opt/sti/worlds/hospital.sdf')
