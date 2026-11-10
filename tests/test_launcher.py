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


class BackendSelection(unittest.TestCase):
    def setUp(self):
        access = patch.object(sim, 'check_docker_access')
        access.start()
        self.addCleanup(access.stop)

    def test_cpu_never_requests_gpu_even_if_explicit_backend(self):
        self.assertEqual(sim.choose_backend('cpu', 'nvidia'), 'cpu')

    def test_mac_does_not_request_linux_devices(self):
        with patch.object(sim.platform, 'system', return_value='Darwin'):
            self.assertEqual(sim.choose_backend('auto', 'auto'), 'cpu')

    def test_wsl_is_not_mistaken_for_native_linux_gpu_support(self):
        with patch.object(sim.platform, 'system', return_value='Linux'), patch.object(sim, 'is_wsl', return_value=True):
            self.assertEqual(sim.choose_backend('auto', 'auto'), 'cpu')

    def test_wsl2_docker_daemon_selects_d3d12_backend_from_windows_or_wsl(self):
        kernel = '6.6.87.2-microsoft-standard-WSL2'
        for system, wsl in (('Windows', False), ('Linux', True)):
            with patch.object(sim.platform, 'system', return_value=system), \
                 patch.object(sim, 'is_wsl', return_value=wsl):
                self.assertEqual(sim.choose_backend('auto', 'auto', kernel), 'wsl')

    def test_cpu_mode_overrides_wsl_gpu(self):
        self.assertEqual(sim.choose_backend('cpu', 'auto', '6.6.87.2-microsoft-standard-WSL2'), 'cpu')

    def test_docker_desktop_on_mac_stays_cpu(self):
        with patch.object(sim.platform, 'system', return_value='Darwin'):
            self.assertEqual(sim.choose_backend('auto', 'auto', '6.12.76-linuxkit'), 'cpu')

    def test_wsl_backend_adds_d3d12_overrides(self):
        command = sim.compose('wsl')
        self.assertIn(str(ROOT/'compose.wsl.yaml'), command)
        self.assertTrue((ROOT/'compose.wsl.yaml').exists())

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


class DockerAccess(unittest.TestCase):
    def test_permission_error_explains_fix_without_invoking_sudo(self):
        result = sim.subprocess.CompletedProcess(['docker'], 1, '', 'permission denied connecting to docker.sock')
        with patch.object(sim.subprocess, 'run', return_value=result) as run:
            with self.assertRaisesRegex(RuntimeError, 'Do not run sim.py with sudo'):
                sim.check_docker_access()
        self.assertEqual(run.call_args.args[0][0], 'docker')

    def test_access_check_returns_daemon_kernel(self):
        result = sim.subprocess.CompletedProcess(['docker'], 0, 'linux/x86_64 6.6.87.2-microsoft-standard-WSL2\n', '')
        with patch.object(sim.subprocess, 'run', return_value=result):
            self.assertEqual(sim.check_docker_access(), '6.6.87.2-microsoft-standard-WSL2')

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


class NativeDisplay(unittest.TestCase):
    def test_native_and_gpu_overrides_are_combined(self):
        command = sim.compose('dri', 'native')
        self.assertIn(str(ROOT/'compose.dri.yaml'), command)
        self.assertIn(str(ROOT/'compose.native.yaml'), command)

    def test_browser_needs_no_host_authentication(self):
        with patch.object(sim.subprocess, 'run') as run:
            self.assertEqual(sim.display_environment('browser', prepare=True), {})
        run.assert_not_called()

    def test_native_mode_rejects_mac_and_wsl(self):
        with patch.object(sim.platform, 'system', return_value='Darwin'):
            with self.assertRaisesRegex(RuntimeError, 'desktop Linux'):
                sim.display_environment('native')
        with patch.object(sim.platform, 'system', return_value='Linux'), patch.object(sim, 'is_wsl', return_value=True):
            with self.assertRaisesRegex(RuntimeError, 'desktop Linux'):
                sim.display_environment('native')

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
