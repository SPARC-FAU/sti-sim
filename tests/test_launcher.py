import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('sim', ROOT / 'sim.py')
sim = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sim)
# `up` picks native windows from the host's DISPLAY; tests set it explicitly.
sim.os.environ.pop('DISPLAY', None)


class BackendSelection(unittest.TestCase):
    def setUp(self):
        diagnostics = patch.object(sim, 'collect_diagnostics', return_value=None)
        self.diagnostics = diagnostics.start()
        self.addCleanup(diagnostics.stop)
        access = patch.object(sim, 'check_docker_access')
        access.start()
        self.addCleanup(access.stop)
        arena = patch.object(sim, 'ARENA', ROOT / 'sim.py')  # any file that exists
        arena.start()
        self.addCleanup(arena.stop)

    def test_missing_hospital_model_is_explained_before_docker_runs(self):
        with patch.object(sim, 'ARENA', ROOT / 'worlds/missing.glb'), \
             patch.object(sim, 'run') as run, \
             patch.object(sim.sys, 'argv', ['sim.py', 'up']), \
             patch.object(sim.sys, 'stderr') as stderr:
            with self.assertRaises(SystemExit) as stopped:
                sim.main()
        self.assertEqual(stopped.exception.code, 1)
        self.assertIn('course forum', stderr.write.call_args.args[0])
        run.assert_not_called()

    def test_cpu_never_requests_gpu_even_if_explicit_backend(self):
        self.assertEqual(sim.choose_backend('cpu', 'nvidia'), 'cpu')

    def test_mac_does_not_request_linux_devices(self):
        with patch.object(sim.platform, 'system', return_value='Darwin'):
            self.assertEqual(sim.choose_backend('auto', 'auto'), 'cpu')

    def test_wsl_is_not_mistaken_for_native_linux_gpu_support(self):
        with patch.object(sim.platform, 'system', return_value='Linux'), patch.object(sim, 'is_wsl', return_value=True):
            self.assertEqual(sim.choose_backend('auto', 'auto'), 'cpu')

    def test_wslg_gpu_requires_native_display_and_host_resources(self):
        with patch.object(sim.platform, 'system', return_value='Linux'), \
             patch.object(sim, 'is_wsl', return_value=True), \
             patch.object(sim.Path, 'exists', return_value=True):
            self.assertEqual(sim.choose_backend('auto', 'auto', 'native'), 'wslgpu')
            self.assertEqual(sim.choose_backend('auto', 'auto', 'browser'), 'cpu')
            self.assertEqual(sim.choose_backend('cpu', 'auto', 'native'), 'cpu')
        with patch.object(sim.platform, 'system', return_value='Linux'), \
             patch.object(sim, 'is_wsl', return_value=True), \
             patch.object(sim.Path, 'exists', return_value=False):
            self.assertEqual(sim.choose_backend('auto', 'auto', 'native'), 'cpu')

    def test_wslg_gpu_failure_preserves_native_display_on_cpu_retry(self):
        calls = []

        def run(command, env):
            calls.append((command, env.copy()))
            if 'up' in command and any('compose.wslgpu.yaml' in part for part in command):
                raise sim.subprocess.CalledProcessError(1, command)

        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(sim, 'ROOT', Path(tmp)), \
             patch.object(sim, 'STATE', Path(tmp)/'state.json'), \
             patch.object(sim.shutil, 'which', return_value='/bin/docker'), \
             patch.object(sim, 'is_wsl', return_value=True), \
             patch.object(sim.platform, 'system', return_value='Linux'), \
             patch.object(sim, 'choose_backend', return_value='wslgpu'), \
             patch.object(sim.os, 'stat') as stat, \
             patch.object(sim, 'prepare_workspace', return_value=False), \
             patch.object(sim.Path, 'is_socket', return_value=True), \
             patch.dict(sim.os.environ, {'DISPLAY': ':0'}), \
             patch.object(sim, 'run', side_effect=run), \
             patch.object(sim.subprocess, 'run'), \
             patch.object(sim.sys, 'argv', ['sim.py', 'up', '--display', 'native']):
            stat.return_value.st_gid = 44
            sim.main()
            state = sim.json.loads(sim.STATE.read_text())
        starts = [(cmd, env) for cmd, env in calls if 'up' in cmd]
        self.assertEqual(len(starts), 2)
        self.assertTrue(any('compose.wslgpu.yaml' in part for part in starts[0][0]))
        self.assertFalse(any('compose.wslgpu.yaml' in part for part in starts[1][0]))
        self.assertTrue(any('compose.wslg.yaml' in part for part in starts[1][0]))
        self.assertEqual(starts[1][1]['STI_RENDER_MODE'], 'cpu')
        self.assertEqual(state['display'], 'native')
        self.assertEqual(state['backend'], 'cpu')

    def test_gpu_start_failure_retries_without_device_mapping(self):
        calls = []

        def run(command, env):
            calls.append(command)
            if 'up' in command and any('compose.nvidia.yaml' in part for part in command):
                raise sim.subprocess.CalledProcessError(1, command)

        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(sim, 'ROOT', Path(tmp)), \
             patch.object(sim, 'STATE', Path(tmp) / 'state.json'), \
             patch.object(sim.shutil, 'which', return_value='/bin/docker'), \
             patch.object(sim, 'choose_backend', return_value='nvidia'), \
             patch.object(sim, 'run', side_effect=run), \
             patch.object(sim.subprocess, 'run'), \
             patch.object(sim.sys, 'argv', ['sim.py', 'up']):
            sim.main()
            state = sim.json.loads(sim.STATE.read_text())
        self.assertEqual(state, {'backend': 'cpu', 'mode': 'cpu', 'scene': 'hospital', 'display': 'browser'})
        starts = [c for c in calls if 'up' in c]
        self.assertEqual(len(starts), 2)
        self.assertFalse(any('compose.nvidia.yaml' in part for part in starts[-1]))

    def test_strict_gpu_failure_does_not_fallback(self):
        def run(command, env):
            if 'up' in command:
                raise sim.subprocess.CalledProcessError(1, command)

        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(sim, 'ROOT', Path(tmp)), \
             patch.object(sim, 'STATE', Path(tmp)/'state.json'), \
             patch.object(sim.shutil, 'which', return_value='/bin/docker'), \
             patch.object(sim, 'choose_backend', return_value='nvidia'), \
             patch.object(sim, 'run', side_effect=run), \
             patch.object(sim.sys, 'argv', ['sim.py', 'up', '--mode', 'gpu']):
            with self.assertRaises(SystemExit) as error:
                sim.main()
        self.assertEqual(error.exception.code, 1)

    def test_sensor_failure_is_reported_without_restarting_workload(self):
        calls = []

        def run(command, env):
            calls.append(command)
            if 'exec' in command:
                raise sim.subprocess.CalledProcessError(1, command)

        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(sim, 'ROOT', Path(tmp)), \
             patch.object(sim, 'STATE', Path(tmp) / 'state.json'), \
             patch.object(sim.shutil, 'which', return_value='/bin/docker'), \
             patch.object(sim, 'choose_backend', return_value='nvidia'), \
             patch.object(sim, 'run', side_effect=run), \
             patch.object(sim.sys, 'argv', ['sim.py', 'up']):
            with self.assertRaises(SystemExit):
                sim.main()
        self.assertEqual(len([c for c in calls if 'up' in c]), 1)
        self.assertFalse(any('down' in c for c in calls))

    def check_failure_run(self, failure, argv=('sim.py', 'up')):
        """Fail the first sensor check with the given diagnosis; return compose calls."""
        calls = []
        self.diagnostics.side_effect = [failure, None]

        def run(command, env):
            calls.append(command)
            if 'exec' in command and len([c for c in calls if 'up' in c]) == 1:
                raise sim.subprocess.CalledProcessError(1, command)

        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(sim, 'ROOT', Path(tmp)), \
             patch.object(sim, 'STATE', Path(tmp)/'state.json'), \
             patch.object(sim.shutil, 'which', return_value='/bin/docker'), \
             patch.object(sim, 'choose_backend', return_value='nvidia'), \
             patch.object(sim, 'run', side_effect=run), \
             patch.object(sim.sys, 'argv', list(argv)):
            try:
                sim.main()
            except SystemExit:
                calls.append('exit')
        return calls

    def test_gpu_graphics_crash_after_start_retries_on_cpu(self):
        calls = self.check_failure_run('STI graphics failure (gpu): driver lacks a feature. Retry')
        starts = [c for c in calls if 'up' in c]
        self.assertEqual(len(starts), 2)
        self.assertFalse(any('compose.nvidia.yaml' in part for part in starts[1]))
        self.assertNotIn('exit', calls)

    def test_cpu_graphics_crash_is_reported_not_retried(self):
        calls = self.check_failure_run('STI graphics failure (cpu): no context. Send logs')
        self.assertEqual(len([c for c in calls if 'up' in c]), 1)
        self.assertIn('exit', calls)

    def test_strict_gpu_graphics_crash_is_reported_not_retried(self):
        calls = self.check_failure_run('STI graphics failure (gpu): x', ('sim.py', 'up', '--mode', 'gpu'))
        self.assertEqual(len([c for c in calls if 'up' in c]), 1)
        self.assertIn('exit', calls)

    def test_state_replaces_an_unwritable_previous_file(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(sim, 'ROOT', Path(tmp)), \
             patch.object(sim, 'STATE', Path(tmp)/'state.json'):
            sim.STATE.write_text('old state')
            sim.STATE.chmod(0o444)
            sim.write_state({'backend': 'cpu'})
            self.assertEqual(sim.json.loads(sim.STATE.read_text()), {'backend': 'cpu'})
            self.assertEqual(list(Path(tmp).glob('.sti-state-*')), [])

    def test_world_is_self_contained_and_sensors_match_bridge(self):
        tree = ET.parse(ROOT / 'worlds/demo.sdf')
        self.assertEqual(tree.findall('.//include'), [])
        topics = {s.findtext('topic') for s in tree.findall('.//sensor')}
        self.assertEqual(topics, {'/scan', '/camera/image'})
        self.assertEqual(tree.findtext('.//plugin/child_frame_id'), 'base_link')


class Workspace(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        root = patch.object(sim, 'ROOT', self.root)
        root.start()
        self.addCleanup(root.stop)

    def test_first_start_copies_starter(self):
        (self.root/'starter/src').mkdir(parents=True)
        (self.root/'starter/README.md').write_text('starter')
        self.assertTrue(sim.prepare_workspace())
        self.assertEqual((self.root/'workspace/README.md').read_text(), 'starter')
        self.assertTrue((self.root/'workspace/src').is_dir())
        self.assertEqual((self.root/'workspace').stat().st_mode & 0o777, 0o755)
        self.assertEqual(list(self.root.glob('.workspace-*')), [])

    def test_existing_workspace_is_never_modified(self):
        (self.root/'starter').mkdir()
        (self.root/'starter/README.md').write_text('new starter')
        (self.root/'workspace').mkdir()
        (self.root/'workspace/README.md').write_text('student notes')
        self.assertFalse(sim.prepare_workspace())
        self.assertEqual((self.root/'workspace/README.md').read_text(), 'student notes')
        self.assertEqual(sorted(p.name for p in (self.root/'workspace').iterdir()), ['README.md'])

    def test_missing_starter_still_creates_empty_workspace(self):
        self.assertTrue(sim.prepare_workspace())
        self.assertEqual(list((self.root/'workspace').iterdir()), [])

    def test_file_named_workspace_is_reported_not_replaced(self):
        (self.root/'workspace').write_text('keep me')
        with self.assertRaisesRegex(RuntimeError, 'not a folder'):
            sim.prepare_workspace()
        self.assertEqual((self.root/'workspace').read_text(), 'keep me')

    def test_failed_copy_leaves_no_partial_workspace(self):
        (self.root/'starter').mkdir()
        with patch.object(sim.shutil, 'copytree', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                sim.prepare_workspace()
        self.assertFalse((self.root/'workspace').exists())
        self.assertEqual(list(self.root.glob('.workspace-*')), [])

    def test_linux_build_uses_host_user(self):
        with patch.object(sim.platform, 'system', return_value='Linux'), \
             patch.object(sim.os, 'getuid', return_value=1234, create=True), \
             patch.object(sim.os, 'getgid', return_value=2345, create=True):
            self.assertEqual(sim.host_user(), {'STI_UID': '1234', 'STI_GID': '2345'})
            self.assertEqual(sim.environment('cpu', 'cpu')['STI_UID'], '1234')

    def test_root_and_docker_desktop_hosts_keep_image_default(self):
        with patch.object(sim.platform, 'system', return_value='Linux'), \
             patch.object(sim.os, 'getuid', return_value=0, create=True):
            self.assertEqual(sim.host_user(), {})
        for system in ('Darwin', 'Windows'):
            with patch.object(sim.platform, 'system', return_value=system):
                self.assertEqual(sim.host_user(), {})

    def test_compose_bind_mounts_workspace_without_creating_it(self):
        text = (ROOT/'compose.yaml').read_text()
        self.assertIn('source: ./workspace', text)
        self.assertIn('create_host_path: false', text)
        self.assertNotIn('workspace:/workspace', text)  # no named volume

    def test_build_output_stays_out_of_the_workspace(self):
        # workspace/ must hold only source so it can be copied to any platform.
        compose = (ROOT/'compose.yaml').read_text()
        self.assertIn('target: /colcon', compose)
        defaults = (ROOT/'config/colcon.yaml').read_text()
        self.assertIn('build-base: /colcon/build', defaults)
        self.assertIn('install-base: /colcon/install', defaults)
        self.assertNotIn('base: /workspace', defaults)
        dockerfile = (ROOT/'Dockerfile').read_text()
        self.assertIn('COLCON_DEFAULTS_FILE=/opt/sti/config/colcon.yaml', dockerfile)
        self.assertIn('COLCON_LOG_PATH=/colcon/log', dockerfile)

    def test_clean_removes_volumes_but_never_touches_workspace(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(sim, 'ROOT', Path(tmp)), \
             patch.object(sim, 'STATE', Path(tmp) / 'state.json'), \
             patch.object(sim.shutil, 'which', return_value='/bin/docker'), \
             patch.object(sim, 'check_docker_access', return_value=''), \
             patch.object(sim, 'run', side_effect=lambda command, env: calls.append(command)), \
             patch.object(sim.sys, 'argv', ['sim.py', 'clean']):
            (Path(tmp)/'workspace/src').mkdir(parents=True)
            (Path(tmp)/'workspace/src/node.py').write_text('student code')
            sim.main()
            self.assertEqual((Path(tmp)/'workspace/src/node.py').read_text(), 'student code')
        self.assertEqual(calls[-1][-2:], ['down', '--volumes'])

    def test_image_is_built_for_the_host_architecture(self):
        # A pinned amd64 image is emulated on Apple Silicon and too slow for the controllers.
        self.assertNotIn('platform:', (ROOT/'compose.yaml').read_text())
        dockerfile = (ROOT/'Dockerfile').read_text()
        self.assertIn('virtualgl_3.1.5_${TARGETARCH}.deb', dockerfile)
        self.assertNotIn('= amd64\n', dockerfile)

    def test_workspace_is_never_shipped_or_sent_to_docker(self):
        for name in ('.gitignore', '.dockerignore'):
            self.assertIn('workspace/', (ROOT/name).read_text().splitlines())


class DisplaySelection(unittest.TestCase):
    def test_explicit_choice_is_kept(self):
        self.assertEqual(sim.choose_display('browser', prepare=True), 'browser')
        self.assertEqual(sim.choose_display('native', prepare=True), 'native')

    def test_mac_and_windows_use_the_browser(self):
        for system in ('Darwin', 'Windows'):
            with patch.object(sim.platform, 'system', return_value=system):
                self.assertEqual(sim.choose_display('auto', prepare=True), 'browser')

    def test_linux_desktop_and_wslg_get_native_windows(self):
        with patch.object(sim.platform, 'system', return_value='Linux'), \
             patch.object(sim, 'display_environment', return_value={}) as prepare:
            with patch.object(sim, 'is_wsl', return_value=False), \
                 patch.dict(sim.os.environ, {'DISPLAY': ':0'}):
                self.assertEqual(sim.choose_display('auto', prepare=True), 'native')
            with patch.object(sim, 'is_wsl', return_value=True):
                self.assertEqual(sim.choose_display('auto', prepare=True), 'native')
        prepare.assert_called_with('native', prepare=True)

    def test_linux_without_a_local_desktop_uses_the_browser(self):
        with patch.object(sim.platform, 'system', return_value='Linux'), \
             patch.object(sim, 'is_wsl', return_value=False), \
             patch.object(sim, 'display_environment') as prepare:
            for display in ('', 'localhost:10.0'):
                with patch.dict(sim.os.environ, {'DISPLAY': display}):
                    self.assertEqual(sim.choose_display('auto', prepare=True), 'browser')
        prepare.assert_not_called()

    def test_unusable_native_display_falls_back_to_the_browser(self):
        with patch.object(sim.platform, 'system', return_value='Linux'), \
             patch.object(sim, 'is_wsl', return_value=True), \
             patch.object(sim, 'display_environment', side_effect=RuntimeError('WSLg X11 socket is unavailable.')):
            self.assertEqual(sim.choose_display('auto', prepare=True), 'browser')

    def test_only_up_chooses_a_display(self):
        with patch.object(sim.platform, 'system', return_value='Linux'), \
             patch.object(sim, 'display_environment') as prepare:
            self.assertEqual(sim.choose_display('auto'), 'browser')
        prepare.assert_not_called()


class Restart(unittest.TestCase):
    def outcome(self, container, status):
        results = iter([sim.subprocess.CompletedProcess([], 0, container, ''),
                        sim.subprocess.CompletedProcess([], 0, status, '')])
        with patch.object(sim.subprocess, 'run', side_effect=lambda *a, **k: next(results)), \
             patch.object(sim, 'run') as run, patch('builtins.print'):
            sim.stop_dead_simulation(['docker', 'compose'], {})
        return [call.args[0] for call in run.call_args_list]

    def test_running_simulation_is_kept(self):
        self.assertEqual(self.outcome('abc123\n', 'demo   RUNNING   pid 40, uptime 0:10:00\n'), [])

    def test_nothing_is_stopped_when_no_container_runs(self):
        self.assertEqual(self.outcome('', ''), [])

    def test_container_with_an_exited_simulation_is_removed(self):
        self.assertEqual(self.outcome('abc123\n', 'demo   EXITED   Nov 11 10:00 AM\n'),
                         [['docker', 'compose', 'down']])

    def test_windows_are_opened_outside_the_launch(self):
        launch = (sim.ROOT / 'scripts/hospital.launch.py').read_text()
        self.assertNotIn('rviz2', launch)
        self.assertNotIn("'-g'", launch)
        self.assertIn('windows.sh', (sim.ROOT / 'scripts/demo.sh').read_text())


class DockerAccess(unittest.TestCase):
    def test_permission_error_explains_fix_without_invoking_sudo(self):
        result = sim.subprocess.CompletedProcess(['docker'], 1, '', 'permission denied connecting to docker.sock')
        with patch.object(sim.subprocess, 'run', return_value=result) as run:
            with self.assertRaisesRegex(RuntimeError, 'Do not run sim.py with sudo'):
                sim.check_docker_access()
        self.assertEqual(run.call_args.args[0][0], 'docker')

    def test_daemon_not_running_is_not_reported_as_a_permission_error(self):
        result = sim.subprocess.CompletedProcess(['docker'], 1, '', 'Cannot connect to the Docker daemon')
        with patch.object(sim.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(RuntimeError, 'Docker is not running'):
                sim.check_docker_access()

    def test_docker_desktop_hosts_are_told_to_start_it(self):
        result = sim.subprocess.CompletedProcess(['docker'], 1, '', 'failed to connect to the docker API')
        with patch.object(sim.subprocess, 'run', return_value=result), \
             patch.object(sim.platform, 'system', return_value='Darwin'):
            with self.assertRaisesRegex(RuntimeError, 'Start Docker Desktop'):
                sim.check_docker_access()


class FailureDiagnostics(unittest.TestCase):
    def test_stopped_container_logs_are_copied_before_printing(self):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            if 'cp' in command:
                Path(command[-1]).write_text('saved graphics error')
            return sim.subprocess.CompletedProcess(command, 0)

        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(sim, 'ROOT', Path(tmp)), \
             patch.object(sim.subprocess, 'run', side_effect=run), \
             patch('builtins.print') as output:
            sim.collect_diagnostics(['docker', 'compose'], {})
            copies = list((Path(tmp)/'.sti-diagnostics').glob('startup-*/gpu-probe.log'))
            self.assertEqual(len(copies), 1)
            self.assertEqual(copies[0].read_text(), 'saved graphics error')
            self.assertTrue(any('saved graphics error' in str(c) for c in output.call_args_list))
        self.assertTrue(any('logs' in command for command in calls))
        self.assertFalse(any('exec' in command for command in calls))

    def test_graphics_failure_is_returned_from_saved_logs(self):
        failure = 'STI graphics failure (gpu): the OpenGL driver lacks a feature. Retry with: x'

        def run(command, **kwargs):
            if 'cp' in command and command[-1].endswith('demo.err'):
                Path(command[-1]).write_text('Hospital launch stopped unexpectedly\n' + failure + '\n')
            return sim.subprocess.CompletedProcess(command, 0)

        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(sim, 'ROOT', Path(tmp)), \
             patch.object(sim.subprocess, 'run', side_effect=run), \
             patch('builtins.print'):
            self.assertEqual(sim.collect_diagnostics(['docker', 'compose'], {}), failure)

    def test_failed_diagnostics_do_not_raise_another_error(self):
        with patch.object(sim.subprocess, 'run', side_effect=OSError('Docker unavailable')), \
             patch('builtins.print'):
            sim.collect_diagnostics(['docker', 'compose'], {})


class NativeDisplay(unittest.TestCase):
    def test_native_and_gpu_overrides_are_combined(self):
        with patch.object(sim, 'is_wsl', return_value=False):
            command = sim.compose('dri', 'native')
        self.assertIn(str(ROOT/'compose.dri.yaml'), command)
        self.assertIn(str(ROOT/'compose.native.yaml'), command)

    def test_browser_needs_no_host_authentication(self):
        with patch.object(sim.subprocess, 'run') as run:
            self.assertEqual(sim.display_environment('browser', prepare=True), {})
        run.assert_not_called()

    def test_native_mode_rejects_mac_and_powershell(self):
        for system in ('Darwin', 'Windows'):
            with patch.object(sim.platform, 'system', return_value=system):
                with self.assertRaisesRegex(RuntimeError, 'Ubuntu WSL2 terminal'):
                    sim.display_environment('native')

    def test_wslg_uses_socket_without_exporting_host_cookie(self):
        with patch.object(sim.platform, 'system', return_value='Linux'), \
             patch.object(sim, 'is_wsl', return_value=True), \
             patch.dict(sim.os.environ, {'DISPLAY': ':0'}), \
             patch.object(sim.Path, 'is_socket', return_value=True), \
             patch.object(sim.subprocess, 'run') as run:
            env = sim.display_environment('native', prepare=True)
            self.assertEqual(env, {'STI_HOST_DISPLAY': ':0', 'STI_WSLG_X11_DIR': '/mnt/wslg/.X11-unix'})
            self.assertIn(str(ROOT/'compose.wslg.yaml'), sim.compose('cpu', 'native'))
            self.assertNotIn(str(ROOT/'compose.native.yaml'), sim.compose('cpu', 'native'))
        run.assert_not_called()

    def test_wslg_reports_missing_socket_before_starting_container(self):
        with patch.object(sim.platform, 'system', return_value='Linux'), \
             patch.object(sim, 'is_wsl', return_value=True), \
             patch.dict(sim.os.environ, {'DISPLAY': ':0'}), \
             patch.object(sim.Path, 'is_socket', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'WSLg X11 socket is unavailable'):
                sim.display_environment('native', prepare=True)
            self.assertEqual(sim.display_environment('native')['STI_HOST_DISPLAY'], ':0')

    def test_wslg_rejects_old_external_x_server_override(self):
        with patch.object(sim.platform, 'system', return_value='Linux'), \
             patch.object(sim, 'is_wsl', return_value=True), \
             patch.dict(sim.os.environ, {'DISPLAY': '192.168.1.2:0'}):
            with self.assertRaisesRegex(RuntimeError, 'Remove any custom DISPLAY'):
                sim.display_environment('native', prepare=True)

    def test_inspection_does_not_require_display(self):
        with patch.object(sim.platform, 'system', return_value='Linux'), \
             patch.object(sim, 'is_wsl', return_value=False), \
             patch.dict(sim.os.environ, {'DISPLAY': ''}), \
             patch.object(sim.subprocess, 'run') as run:
            self.assertEqual(sim.display_environment('native')['STI_HOST_DISPLAY'], ':0')
        run.assert_not_called()

    def test_cookie_permissions_and_no_secrets_in_arguments(self):
        record = '0100 0004 686f7374 0001 30 0012 4d49542d4d414749432d434f4f4b49452d31 0004 12345678\n'
        calls = []

        def run(command, **kwargs):
            calls.append((command, kwargs))
            return sim.subprocess.CompletedProcess(command, 0, record if 'nlist' in command else '', '')

        with tempfile.TemporaryDirectory() as directory, \
             patch.object(sim, 'ROOT', Path(directory)), \
             patch.object(sim.platform, 'system', return_value='Linux'), \
             patch.object(sim, 'is_wsl', return_value=False), \
             patch.object(sim.shutil, 'which', return_value='/bin/xauth'), \
             patch.dict(sim.os.environ, {'DISPLAY': ':0'}), \
             patch.object(sim.subprocess, 'run', side_effect=run):
            env = sim.display_environment('native', prepare=True)
            cookie = Path(env['STI_NATIVE_AUTH_DIR'])/'Xauthority'
            self.assertEqual(cookie.stat().st_mode & 0o777, 0o640)
            self.assertEqual(cookie.parent.stat().st_mode & 0o777, 0o750)
            self.assertEqual(calls[0][0], ['xauth', 'nlist', ':0'])
            self.assertTrue(calls[1][1]['input'].startswith('ffff'))
            self.assertFalse(any('12345678' in arg for command, _ in calls for arg in command))


if __name__ == '__main__':
    unittest.main()
