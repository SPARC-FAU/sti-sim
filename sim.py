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


def is_wsl():
    return 'microsoft' in platform.release().lower()


def choose_backend(mode, requested, daemon_kernel=''):
    if mode == 'cpu':
        return 'cpu'
    if requested != 'auto':
        return requested
    # The Docker daemon's kernel, not the client OS, decides which GPU path exists:
    # Docker Desktop on Windows and Docker Engine inside WSL both run on WSL2.
    if 'microsoft' in daemon_kernel.lower():
        return 'wsl'
    if platform.system() == 'Linux' and not is_wsl():
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
        result += ['-f', str(ROOT / 'compose.native.yaml')]
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
    # Without a GPU mapping there is nothing to probe (macOS, or Linux without a GPU).
    env['STI_RENDER_MODE'] = 'cpu' if backend == 'cpu' else mode
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
    if platform.system() != 'Linux' or is_wsl():
        raise RuntimeError('Native windows currently support desktop Linux only. Use --display browser on Windows/macOS.')
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
    """Return the Docker daemon's kernel version after confirming access."""
    result = subprocess.run(['docker', 'info', '--format', '{{.OSType}}/{{.Architecture}} {{.KernelVersion}}'],
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
    return result.stdout.strip().partition(' ')[2]


def wait_ready(command, env):
    # Compose --wait checks process/desktop health. Sensor checks follow separately.
    run(command + ['up', '-d', '--wait', '--wait-timeout', '180'], env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['build', 'up', 'down', 'clean', 'logs', 'shell', 'check', 'doctor'])
    parser.add_argument('--mode', choices=['auto', 'gpu', 'cpu'], default='auto')
    parser.add_argument('--backend', choices=['auto', 'dri', 'nvidia', 'wsl'], default='auto')
    parser.add_argument('--motion', action='store_true', help='check: move demo robot briefly')
    parser.add_argument('--scene', choices=['hospital', 'demo'], default=os.environ.get('STI_SCENE', 'hospital'))
    parser.add_argument('--display', choices=['browser', 'native'], default='browser')
    args = parser.parse_args()
    if not shutil.which('docker'):
        parser.exit(1, 'Install Docker Engine + Compose on Linux, or Docker Desktop on Windows/macOS.\n')
    try:
        daemon_kernel = check_docker_access()
        run(['docker', 'compose', 'version'], os.environ.copy())
        backend = choose_backend(args.mode, args.backend, daemon_kernel)
        mode = args.mode
        scene = args.scene
        display = args.display
        if args.command not in ('build', 'up') and STATE.exists():
            state = json.loads(STATE.read_text())
            backend, mode = state['backend'], state['mode']
            scene = state.get('scene', 'demo')
            display = state.get('display', 'browser')
        if args.command == 'up' and mode == 'gpu' and backend == 'cpu':
            raise RuntimeError('No supported GPU backend detected. GPU rendering needs Linux GPU access '
                               'or Windows 11 with Docker on WSL2; otherwise use --mode cpu.')
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
            try:
                wait_ready(command, env)
            except subprocess.CalledProcessError:
                if mode != 'auto' or backend == 'cpu':
                    raise
                print('GPU startup failed. Saving logs, then retrying with CPU rendering.', flush=True)
                subprocess.run(command + ['logs', '--no-color', '--tail', '80'], cwd=ROOT, env=env)
                run(command + ['down'], env)
                backend, mode = 'cpu', 'cpu'
                env = environment(mode, backend)
                env['STI_SCENE'] = scene
                env.update(display_environment(display))
                command = compose(backend, display)
                wait_ready(command, env)
            write_state({'backend': backend, 'mode': mode, 'scene': scene, 'display': display})
            # Do not mask missing sensor data as a successful start.
            run(command + ['exec', '-T', 'sim', 'bash', '-lc',
                           'source /opt/ros/humble/setup.bash && python3 /opt/sti/scripts/check.py'], env)
            run(command + ['exec', '-T', 'sim', 'bash', '-c',
                           'grep "OpenGL renderer string" /tmp/sti/renderer.log; if [[ -s /tmp/sti/gpu-fallback-reason ]]; then '
                           'echo "GPU not used: $(cat /tmp/sti/gpu-fallback-reason). See /tmp/sti/gpu-probe.log."; fi'], env)
            if display == 'native':
                print('Gazebo and RViz open as windows on your Linux desktop.')
            else:
                print(f"Open http://localhost:{env.get('STI_PORT', '6080')}/vnc.html?autoconnect=true&resize=scale")
            print(f"Your code: {ROOT / 'workspace'} (/workspace in the container).\n"
                  'To move to a new version or another computer, copy that workspace/ folder across.')
        elif args.command == 'doctor':
            print('Host:', platform.system(), platform.machine(), 'WSL:', is_wsl(), 'Docker kernel:', daemon_kernel)
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
