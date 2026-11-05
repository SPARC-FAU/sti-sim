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


def choose_backend(mode, requested):
    if mode == 'cpu':
        return 'cpu'
    if requested != 'auto':
        return requested
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


def environment(mode, backend):
    env = os.environ.copy()
    env['STI_RENDER_MODE'] = mode
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
    result = subprocess.run(['docker', 'info', '--format', '{{.OSType}}/{{.Architecture}}'],
                            capture_output=True, text=True)
    if result.returncode:
        detail = (result.stderr + result.stdout).strip()
        if 'permission denied' in detail.lower():
            raise RuntimeError(
                'Docker access denied. Run `docker info` in this same terminal. '
                'On Linux, configure Docker access for your account (README: Docker permissions), '
                'then log out/in or use `newgrp docker`. Do not run sim.py with sudo.\n' + detail)
        raise RuntimeError('Docker is not available. Check the selected Docker context and that the engine is running.\n' + detail)
    print(result.stdout.strip())


def wait_ready(command, env):
    # Compose --wait checks process/desktop health. Sensor checks follow separately.
    run(command + ['up', '-d', '--wait', '--wait-timeout', '180'], env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['build', 'up', 'down', 'logs', 'shell', 'check', 'doctor'])
    parser.add_argument('--mode', choices=['auto', 'gpu', 'cpu'], default='auto')
    parser.add_argument('--backend', choices=['auto', 'dri', 'nvidia'], default='auto')
    parser.add_argument('--motion', action='store_true', help='check: move demo robot briefly')
    parser.add_argument('--scene', choices=['hospital', 'demo'], default=os.environ.get('STI_SCENE', 'hospital'))
    parser.add_argument('--display', choices=['browser', 'native'], default='browser')
    args = parser.parse_args()
    if not shutil.which('docker'):
        parser.exit(1, 'Install Docker Engine + Compose on Linux, or Docker Desktop on Windows/macOS.\n')
    try:
        check_docker_access()
        run(['docker', 'compose', 'version'], os.environ.copy())
        backend = choose_backend(args.mode, args.backend)
        mode = args.mode
        scene = args.scene
        display = args.display
        if args.command not in ('build', 'up') and STATE.exists():
            state = json.loads(STATE.read_text())
            backend, mode = state['backend'], state['mode']
            scene = state.get('scene', 'demo')
            display = state.get('display', 'browser')
        if args.command == 'up' and mode == 'gpu' and backend == 'cpu':
            raise RuntimeError('No supported GPU backend detected. Use Linux GPU access or --mode cpu.')
        env = environment(mode, backend)
        env['STI_SCENE'] = scene
        env.update(display_environment(display, prepare=args.command == 'up'))
        command = compose(backend, display)
        if args.command == 'build':
            run(compose('cpu') + ['build', '--pull'], env)
        elif args.command == 'up':
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
            run(command + ['exec', '-T', 'sim', 'cat', '/tmp/sti/renderer.log'], env)
            if display == 'native':
                print('Gazebo and RViz open as windows on your Linux desktop.')
            else:
                print(f"Open http://localhost:{env.get('STI_PORT', '6080')}/vnc.html?autoconnect=true&resize=scale")
        elif args.command == 'doctor':
            print('Host:', platform.system(), platform.machine(), 'WSL:', is_wsl())
            print('Backend:', backend, 'Mode:', mode, 'Display:', display)
            print('Image: linux/amd64. Apple Silicon requires emulation in this first release.')
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
        else:
            run(command + ['down'], env)
            STATE.unlink(missing_ok=True)
    except (subprocess.CalledProcessError, RuntimeError, OSError, ValueError, KeyError) as exc:
        parser.exit(1, f'{exc}\nSee README.md for diagnostics. No student volumes were deleted.\n')


if __name__ == '__main__':
    main()
