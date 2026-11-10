"""OpenGL capability selection and graphics-crash diagnosis, without a display."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT/'scripts'/f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


requirements = load('gl_requirements')
diagnosis = load('diagnose_render')


def report(renderer, core=None, compat='4.2', extensions=()):
    lines = [f'OpenGL renderer string: {renderer}']
    if core:
        lines.append(f'OpenGL core profile version string: {core} (Core Profile) Mesa 23.2.1')
    lines.append(f'OpenGL version string: {compat} (Compatibility Profile) Mesa 23.2.1')
    lines.append('OpenGL extensions:')
    lines.append('    ' + ', '.join(('GL_NV_copy_image',) + tuple(extensions)) + ', ')
    return '\n'.join(lines) + '\n'


class Requirements(unittest.TestCase):
    def test_wsl_d3d12_on_old_mesa_is_rejected(self):
        # Observed: Mesa 23.2.1 d3d12 on an RTX 3080 -> Gazebo aborts in copyTo.
        usable, reason = requirements.evaluate(report('D3D12 (NVIDIA GeForce RTX 3080)', core='4.2'), True)
        self.assertFalse(usable)
        self.assertIn('GL_ARB_copy_image', reason)

    def test_opengl_43_or_extension_is_accepted(self):
        self.assertTrue(requirements.evaluate(report('D3D12 (NVIDIA GeForce RTX 3080)', core='4.6'), True)[0])
        self.assertTrue(requirements.evaluate(
            report('Mesa Intel(R) HD Graphics', core='4.2', extensions=('GL_ARB_copy_image',)), True)[0])

    def test_core_profile_version_takes_precedence(self):
        self.assertFalse(requirements.evaluate(report('GPU', core='4.1', compat='4.6'))[0])
        self.assertTrue(requirements.evaluate(report('GPU', compat='4.5'))[0])

    def test_software_renderer_is_only_rejected_when_hardware_is_required(self):
        text = report('llvmpipe (LLVM 15.0.7, 256 bits)', core='4.5')
        self.assertFalse(requirements.evaluate(text, True)[0])
        self.assertTrue(requirements.evaluate(text, False)[0])

    def test_missing_context_is_rejected(self):
        usable, reason = requirements.evaluate('libGL error: failed to load driver: swrast\n')
        self.assertFalse(usable)
        self.assertIn('no OpenGL context', reason)


class Diagnosis(unittest.TestCase):
    OGRE = ("[ign-1] terminate called after throwing an instance of 'Ogre::UnimplementedException'\n"
            '[ign-1]   what():  OGRE EXCEPTION(9:UnimplementedException):  in GL3PlusTextureGpu::copyTo\n')

    def test_gpu_failure_is_marked_and_suggests_cpu(self):
        result = diagnosis.diagnose(self.OGRE, 'gpu')
        self.assertTrue(result.startswith('STI graphics failure (gpu): '))
        self.assertIn('lacks a feature', result)
        self.assertIn('--mode cpu', result)

    def test_cpu_failure_does_not_suggest_cpu_retry(self):
        result = diagnosis.diagnose('QGLXContext: Failed to create dummy context\n', 'cpu')
        self.assertTrue(result.startswith('STI graphics failure (cpu): '))
        self.assertNotIn('--mode cpu', result)

    def test_unrelated_exit_has_no_graphics_diagnosis(self):
        self.assertIsNone(diagnosis.diagnose('[ERROR] [spawner-6]: process has died\n', 'gpu'))


class PlanarBase(unittest.TestCase):
    def test_base_systems_are_added_once(self):
        pose = load('initial_pose') if importlib.util.find_spec('yaml') else None
        if pose is None:
            self.skipTest('PyYAML unavailable')
        description = '<robot name="tiago_pro"><link name="base_footprint"/></robot>'
        once = pose.add_planar_base(description)
        twice = pose.add_planar_base(once)
        self.assertEqual(once, twice)
        root = pose.ET.fromstring(once)
        plugins = {p.get('name'): p for p in root.findall('gazebo/plugin')}
        self.assertEqual(plugins[pose.VELOCITY_CONTROL].findtext('topic'),
                         '/mobile_base_controller/cmd_vel_unstamped')
        odometry = plugins[pose.ODOMETRY_PUBLISHER]
        self.assertEqual(odometry.findtext('odom_topic'), '/mobile_base_controller/odom')
        self.assertEqual(odometry.findtext('robot_base_frame'), 'base_footprint')

    def test_bridge_serves_the_base_topics(self):
        import yaml
        entries = yaml.safe_load((ROOT/'config/tiago_bridge.yaml').read_text())
        topics = {(e['ros_topic_name'], e['gz_topic_name'], e['direction']) for e in entries}
        self.assertIn(('/mobile_base_controller/cmd_vel_unstamped',
                       '/mobile_base_controller/cmd_vel_unstamped', 'ROS_TO_GZ'), topics)
        self.assertIn(('/mobile_base_controller/odom', '/mobile_base_controller/odom', 'GZ_TO_ROS'), topics)
        self.assertIn(('/tf', '/mobile_base_controller/tf', 'GZ_TO_ROS'), topics)


if __name__ == '__main__':
    unittest.main()
