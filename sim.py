#!/usr/bin/env python3
"""Host launcher. Python standard library only; Docker Compose v2.30+ required."""
import argparse
import glob
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
STATE = ROOT / '.sti-state.json'
# The hospital model is not part of the repository; it is downloaded separately.
ARENA = ROOT / 'worlds/2026_09_01_RoboCup_HCC_Arena_V1.glb'


def is_wsl():
    return 'microsoft' in platform.release().lower()


def choose_backend(mode, requested, display='browser'):
    if mode == 'cpu':
        return 'cpu'
    if requested != 'auto':
        return requested
    if platform.system() == 'Linux' and is_wsl():
        if (display == 'native' and Path('/dev/dxg').exists()
                and Path('/usr/lib/wsl/lib/libd3d12.so').exists()
                and Path('/usr/lib/wsl/lib/libdxcore.so').exists()):
            return 'wslgpu'
        return 'cpu'
    if platform.system() == 'Linux':
        if Path('/dev/nvidiactl').exists():
            return 'nvidia'
        if glob.glob('/dev/dri/renderD*'):
            return 'dri'
    return 'cpu'


def compose(backend, display='browser'):
    result = ['docker', 'compose', '-f', str(ROOT / 'compose.yaml')]
    if backend != 'cpu':
        result += ['-f', str(ROOT / f'compose.{backend}.yaml')]
    if display == 'native':
        overlay = 'compose.wslg.yaml' if is_wsl() else 'compose.native.yaml'
        result += ['-f', str(ROOT / overlay)]
    return result


def host_user():
    """UID/GID for the container's student account.

    Only native Linux bind mounts expose raw numeric ownership (this includes
    Docker Engine inside WSL). Docker Desktop on Windows/macOS maps it itself.
    Root is never mirrored: sim.py must not be run with sudo.
    """
    if platform.system() != 'Linux' or not hasattr(os, 'getuid') or os.getuid() == 0:
        return {}
    return {'STI_UID': str(os.getuid()), 'STI_GID': str(os.getgid())}


def prepare_workspace():
    """Create workspace/ from starter/ once; never modify an existing workspace."""
    workspace = ROOT / 'workspace'
    if workspace.is_dir():
        return False
    if workspace.exists():
        raise RuntimeError(f'{workspace} exists but is not a folder. Rename it, then start again.')
    temporary = Path(tempfile.mkdtemp(dir=ROOT, prefix='.workspace-'))
    try:
        starter = ROOT / 'starter'
        if starter.is_dir():
            shutil.copytree(starter, temporary, dirs_exist_ok=True)
        temporary.chmod(0o755)  # mkdtemp creates 0700
        # Rename last so an interrupted copy never looks like a finished workspace.
        os.replace(temporary, workspace)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    return True


def environment(mode, backend):
    env = os.environ.copy()
    env.update(host_user())
    env['STI_RENDER_MODE'] = mode
    # Build attestations carry a timestamp, which gives an unchanged image a new
    # ID on every build and makes `up` replace a running simulation.
    env.setdefault('BUILDX_NO_DEFAULT_ATTESTATIONS', '1')
    if backend == 'wslgpu':
        env['STI_DXG_GID'] = str(os.stat('/dev/dxg').st_gid)
    if backend == 'dri':
        renders = sorted(glob.glob('/dev/dri/renderD*'))
        cards = sorted(glob.glob('/dev/dri/card*'))
        if not renders:
            raise RuntimeError('No /dev/dri render device found.')
        env['STI_RENDER_GID'] = str(os.stat(renders[0]).st_gid)
        env['STI_VIDEO_GID'] = str(os.stat(cards[0] if cards else renders[0]).st_gid)
    return env


def run(args, env):
    return subprocess.run(args, cwd=ROOT, env=env, check=True)


def display_environment(display, prepare=False):
    if display == 'browser':
        return {}
    if platform.system() != 'Linux':
        raise RuntimeError('Native windows require desktop Linux or an Ubuntu WSL2 terminal with WSLg. On Windows, run this command inside WSL, not PowerShell. Use --display browser on macOS.')
    if is_wsl():
        host_display = os.environ.get('DISPLAY', ':0')
        socket_directory = Path('/mnt/wslg/.X11-unix')
        if prepare:
            if not host_display.startswith(':') or not host_display[1:].split('.')[0].isdigit():
                raise RuntimeError('WSLg needs its local DISPLAY (normally :0). Remove any custom DISPLAY override in your WSL shell.')
            socket = socket_directory / ('X' + host_display[1:].split('.')[0])
            if not socket.is_socket():
                raise RuntimeError('WSLg X11 socket is unavailable. Update WSL with `wsl --update` in PowerShell, restart WSL, and run from an Ubuntu WSL2 terminal.')
        return {'STI_HOST_DISPLAY': host_display, 'STI_WSLG_X11_DIR': str(socket_directory)}
    host_display = os.environ.get('DISPLAY', '')
    if not host_display.startswith(':'):
        if prepare:
            raise RuntimeError('Native windows need a local X11/Xwayland DISPLAY, for example :0. Run from your desktop terminal.')
        host_display = ':0'  # Inspection/stop commands don't open windows.
    directory = ROOT / '.sti-native'
    if prepare:
        if not shutil.which('xauth'):
            raise RuntimeError('Native windows require the host xauth utility (Ubuntu package: xauth).')
        result = subprocess.run(['xauth', 'nlist', host_display], capture_output=True, text=True)
        if result.returncode or not result.stdout.strip():
            raise RuntimeError('Cannot read an Xauthority cookie for DISPLAY. Check XAUTHORITY in your desktop terminal.')
        # FamilyWild allows the container hostname to differ from the host.
        # Cookie records must never appear in command arguments or logs.
        records = '\n'.join('ffff' + line[4:] for line in result.stdout.splitlines() if line.strip()) + '\n'
        directory.mkdir(mode=0o750, exist_ok=True)
        os.chown(directory, -1, os.getgid())
        directory.chmod(0o750)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=directory, prefix='.cookie-', delete=False) as stream:
                temporary = Path(stream.name)
            result = subprocess.run(['xauth', '-f', str(temporary), 'nmerge', '-'],
                                    input=records, capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError('Could not prepare the container Xauthority file.')
            os.chown(temporary, -1, os.getgid())
            temporary.chmod(0o640)
            os.replace(temporary, directory / 'Xauthority')
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return {'STI_HOST_DISPLAY': host_display, 'STI_NATIVE_AUTH_DIR': str(directory),
            'STI_HOST_GID': str(os.getgid())}


def choose_display(requested, prepare=False):
    """Native windows where the host can show them, otherwise the browser desktop."""
    if requested != 'auto':
        return requested
    if not prepare:
        return 'browser'  # Only `up` chooses; other commands follow the saved state.
    if platform.system() != 'Linux':
        return 'browser'  # macOS and Windows outside WSL have no X display to share.
    if not is_wsl() and not os.environ.get('DISPLAY', '').startswith(':'):
        return 'browser'  # No local desktop session, for example over SSH.
    try:
        display_environment('native', prepare=True)
    except (RuntimeError, OSError) as exc:
        print(f'Native windows are not available: {exc}\nUsing the browser desktop instead.', flush=True)
        return 'browser'
    return 'native'


def write_state(state):
    # Replace the directory entry rather than opening an old sudo-owned file.
    # The repository directory must be writable by the current user.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=ROOT, prefix='.sti-state-', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(state, stream)
            stream.write('\n')
        os.replace(temporary, STATE)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def check_docker_access():
    result = subprocess.run(['docker', 'info', '--format', '{{.OSType}}/{{.Architecture}}'],
                            capture_output=True, text=True)
    if result.returncode:
        detail = (result.stderr + result.stdout).strip()
        if 'permission denied' in detail.lower():
            raise RuntimeError(
                'Docker access denied. Run `docker info` in this same terminal. '
                'On Linux, configure Docker access for your account (README: Docker permissions), '
                'then log out/in or use `newgrp docker`. Do not run sim.py with sudo.\n' + detail)
        hint = ('Start Docker Desktop and wait until it says it is running, then try again.'
                if platform.system() in ('Darwin', 'Windows') else
                'Check that the Docker engine is running (`docker info`).')
        raise RuntimeError('Docker is not running. ' + hint + '\n' + detail)
    print(result.stdout.strip())


def wait_ready(command, env):
    # Compose --wait checks process/desktop health. Sensor checks follow separately.
    run(command + ['up', '-d', '--wait', '--wait-timeout', '180'], env)


def stop_dead_simulation(command, env):
    """Remove a container whose simulation has exited, so `up` starts a new one."""
    def output(arguments):
        return subprocess.run(command + arguments, cwd=ROOT, env=env, capture_output=True,
                              text=True, timeout=30).stdout
    try:
        if not output(['ps', '-q', '--status', 'running', 'sim']).strip():
            return
        status = output(['exec', '-T', 'sim', 'supervisorctl', '-c',
                         '/opt/sti/config/supervisord.conf', 'status', 'demo'])
    except (OSError, subprocess.TimeoutExpired):
        return
    if 'RUNNING' in status or 'STARTING' in status:
        return
    print('The simulation had stopped. Starting it again.', flush=True)
    run(command + ['down'], env)


GRAPHICS_FAILURE = 'STI graphics failure'


def graphics_failure(directory):
    """Return the container's graphics-failure explanation from saved logs, if any."""
    path = directory / 'demo.err'
    if not path.exists():
        return None
    for line in path.read_text(errors='replace').splitlines():
        if line.startswith(GRAPHICS_FAILURE):
            return line
    return None


def collect_diagnostics(command, env):
    """Keep the failing container's logs, including after its entrypoint exits.

    Returns the recorded graphics-failure explanation, or None.
    """
    try:
        print('Startup failed. Container status and logs:', flush=True)
        subprocess.run(command + ['ps', '-a'], cwd=ROOT, env=env, timeout=15)
        subprocess.run(command + ['logs', '--no-color', '--tail', '100'], cwd=ROOT, env=env, timeout=15)
        directory = ROOT / '.sti-diagnostics'
        directory.mkdir(exist_ok=True)
        destination = Path(tempfile.mkdtemp(prefix='startup-', dir=directory))
        # Compose cp works on stopped containers, unlike exec. Copy individual
        # logs to avoid runtime sockets and keep graphics errors after fallback.
        for name in ('display-probe.log', 'gpu-probe.log', 'gpu-check.log', 'renderer.log',
                     'renderer-check.log', 'gazebo.log', 'gazebo-gui.log', 'rviz.log',
                     'demo.err', 'supervisor.log'):
            subprocess.run(command + ['cp', 'sim:/tmp/sti/' + name, str(destination / name)],
                           cwd=ROOT, env=env, capture_output=True, text=True, timeout=10)
        print('Diagnostic files: ' + str(destination), flush=True)
        for name in ('renderer.log', 'demo.err', 'gazebo.log'):
            path = destination / name
            if path.exists():
                print('--- ' + name + ' (last 60 lines) ---', flush=True)
                print('\n'.join(path.read_text(errors='replace').splitlines()[-60:]), flush=True)
        failure = graphics_failure(destination)
        if failure:
            # Repeat the cause last, where it isn't buried under log output.
            print('\n' + failure, flush=True)
        return failure
    except (OSError, subprocess.TimeoutExpired) as exc:
        # Reporting must not replace the original startup error.
        print('Could not save all diagnostics: ' + str(exc), flush=True)
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['build', 'up', 'down', 'clean', 'logs', 'shell', 'check', 'doctor'])
    parser.add_argument('--mode', choices=['auto', 'gpu', 'cpu'], default='auto')
    parser.add_argument('--backend', choices=['auto', 'dri', 'nvidia'], default='auto')
    parser.add_argument('--motion', action='store_true', help='check: move demo robot briefly')
    parser.add_argument('--scene', choices=['hospital', 'demo'], default=os.environ.get('STI_SCENE', 'hospital'))
    parser.add_argument('--display', choices=['auto', 'browser', 'native'], default='auto',
                        help='auto: native windows on Linux and WSL, browser desktop elsewhere')
    args = parser.parse_args()
    if args.command in ('build', 'up') and not ARENA.exists():
        parser.exit(1, f'The hospital model is missing. Download {ARENA.name} from the course forum\n'
                       f'and put it in {ARENA.parent}, then run this command again.\n')
    if not shutil.which('docker'):
        parser.exit(1, 'Install Docker Engine + Compose on Linux, or Docker Desktop on Windows/macOS.\n')
    try:
        check_docker_access()
        run(['docker', 'compose', 'version'], os.environ.copy())
        display = choose_display(args.display, prepare=args.command == 'up')
        backend = choose_backend(args.mode, args.backend, display)
        mode = args.mode
        scene = args.scene
        if args.command not in ('build', 'up') and STATE.exists():
            state = json.loads(STATE.read_text())
            backend, mode = state['backend'], state['mode']
            scene = state.get('scene', 'demo')
            display = state.get('display', 'browser')
        if args.command == 'up' and mode == 'gpu' and backend == 'cpu':
            raise RuntimeError('No supported GPU backend detected. WSLg GPU mode requires --display native, /dev/dxg and WSL GPU libraries. Update WSL and the Windows GPU driver, or use --mode cpu.')
        # Without a GPU overlay (WSL browser display, macOS, Linux without a GPU)
        # there is nothing to probe: use the portable software path.
        if backend == 'cpu' and mode == 'auto':
            mode = 'cpu'
        env = environment(mode, backend)
        env['STI_SCENE'] = scene
        env.update(display_environment(display, prepare=args.command == 'up'))
        command = compose(backend, display)
        if args.command == 'build':
            run(compose('cpu') + ['build', '--pull'], env)
        elif args.command == 'up':
            if prepare_workspace():
                print(f"Created {ROOT / 'workspace'} from starter/.")
            # Build independently of GPU runtime availability.
            run(compose('cpu') + ['build'], env)
            stop_dead_simulation(command, env)
            while True:
                print(f'Starting {scene}: display={display}, backend={backend}, mode={mode}', flush=True)
                write_state({'backend': backend, 'mode': mode, 'scene': scene, 'display': display})
                started = False
                try:
                    wait_ready(command, env)
                    started = True
                    # Do not mask missing sensor data as a successful start.
                    run(command + ['exec', '-T', 'sim', 'bash', '-lc',
                                   'source /opt/ros/humble/setup.bash && python3 /opt/sti/scripts/check.py'], env)
                    run(command + ['exec', '-T', 'sim', 'grep', 'OpenGL renderer string', '/tmp/sti/renderer.log'], env)
                    # Re-open windows the user closed; a window the graphics
                    # driver cannot show counts as a failed start.
                    run(command + ['exec', '-T', 'sim', '/opt/sti/scripts/windows.sh', '--verify'], env)
                    break
                except subprocess.CalledProcessError:
                    failure = collect_diagnostics(command, env)
                    # In auto mode, a GPU that fails to start or crashes the
                    # simulator falls back to CPU. Other application or sensor
                    # failures are reported rather than hidden by a restart.
                    gpu_crash = bool(failure) and failure.startswith(GRAPHICS_FAILURE + ' (gpu)')
                    if mode != 'auto' or backend == 'cpu' or (started and not gpu_crash):
                        raise
                    reason = 'crashed the simulator' if started else 'startup failed'
                    print(f'GPU rendering {reason}. Logs saved; retrying with CPU rendering.', flush=True)
                    run(command + ['down'], env)
                    backend, mode = 'cpu', 'cpu'
                    env = environment(mode, backend)
                    env['STI_SCENE'] = scene
                    env.update(display_environment(display))
                    command = compose(backend, display)
            write_state({'backend': backend, 'mode': mode, 'scene': scene, 'display': display})
            if display == 'native':
                print('Gazebo and RViz are open as windows on your desktop.')
            else:
                print(f"Open http://localhost:{env.get('STI_PORT', '6080')}/vnc.html?autoconnect=true&resize=scale")
            print('Closing a window does not stop the simulation. Run "python3 sim.py up" again to\n'
                  're-open it, or "python3 sim.py down" to stop.')
            print(f"Your code: {ROOT / 'workspace'} (/workspace in the container).\n"
                  'To move to a new version or another computer, copy that workspace/ folder across.')
        elif args.command == 'doctor':
            print('Host:', platform.system(), platform.machine(), 'WSL:', is_wsl())
            print('Backend:', backend, 'Mode:', mode, 'Display:', display)
            print('Image: built for the Docker engine architecture shown above.')
            run(command + ['config', '--quiet'], env)
        elif args.command == 'check':
            script = 'source /opt/ros/humble/setup.bash && python3 /opt/sti/scripts/check.py'
            if args.motion:
                script += ' --motion'
            run(command + ['exec', '-T', 'sim', 'bash', '-lc', script], env)
        elif args.command == 'shell':
            run(command + ['exec', 'sim', 'bash'], env)
        elif args.command == 'logs':
            run(command + ['logs', '-f', '--tail', '100'], env)
        elif args.command == 'clean':
            # Only the colcon volume is removed: workspace/ is a host folder, not a volume.
            run(command + ['down', '--volumes'], env)
            STATE.unlink(missing_ok=True)
            print('Removed the build output (/colcon). Your code in workspace/ was not touched.')
        else:
            run(command + ['down'], env)
            STATE.unlink(missing_ok=True)
    except (subprocess.CalledProcessError, RuntimeError, OSError, ValueError, KeyError) as exc:
        parser.exit(1, f'{exc}\nSee README.md for diagnostics. Files in workspace/ were not changed.\n')


if __name__ == '__main__':
    main()
