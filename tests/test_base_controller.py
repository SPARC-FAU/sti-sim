import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
try:
    from geometry_msgs.msg import Twist
except ImportError:
    Twist = None


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipIf(Twist is None, 'ROS 2 unavailable')
class Base(unittest.TestCase):
    def test_only_planar_motion_reaches_the_base(self):
        controller = load('scripts/base_controller.py', 'base_controller')
        command = Twist()
        command.linear.x, command.linear.y, command.linear.z = 0.1, 0.2, 0.5
        command.angular.x, command.angular.y, command.angular.z = 0.3, 0.4, 0.6
        result = controller.planar(command)
        self.assertEqual((result.linear.x, result.linear.y, result.angular.z), (0.1, 0.2, 0.6))
        self.assertEqual((result.linear.z, result.angular.x, result.angular.y), (0, 0, 0))


@unittest.skipIf(Twist is None, 'ROS 2 unavailable')
class ExampleTeleop(unittest.TestCase):
    def setUp(self):
        self.teleop = load('starter/src/sti_examples/sti_examples/keyboard_teleop.py', 'keyboard_teleop')

    def test_no_key_commands_vertical_motion(self):
        for key in self.teleop.BASE_KEYS:
            velocity = self.teleop.base_velocity(key)
            self.assertEqual((velocity.linear.z, velocity.angular.x, velocity.angular.y), (0, 0, 0))

    def test_every_joint_key_has_limits_and_a_controller(self):
        controlled = [joint for joints in self.teleop.CONTROLLERS.values() for joint in joints]
        for joint, _ in self.teleop.JOINT_KEYS.values():
            self.assertIn(joint, self.teleop.JOINT_LIMITS)
            self.assertIn(joint, controlled)

    def test_torso_target_stays_within_its_limits(self):
        limits = self.teleop.JOINT_LIMITS['torso_lift_joint']
        self.assertEqual(self.teleop.clamp(0.36, limits), 0.35)
        self.assertEqual(self.teleop.clamp(-0.02, limits), 0.0)

    def test_joint_command_addresses_all_joints_of_the_controller(self):
        command = self.teleop.joint_command(['head_1_joint', 'head_2_joint'],
                                            {'head_1_joint': 0.1, 'head_2_joint': -0.2, 'torso_lift_joint': 0.3},
                                            {'head_1_joint': 0.0, 'head_2_joint': 0.0, 'torso_lift_joint': 0.3})
        self.assertEqual(list(command.points[0].positions), [0.1, -0.2])

    def test_a_long_torso_move_is_given_enough_time(self):
        command = self.teleop.joint_command(['torso_lift_joint'], {'torso_lift_joint': 0.35},
                                            {'torso_lift_joint': 0.0})
        seconds = command.points[0].time_from_start.sec
        self.assertGreaterEqual(seconds, 0.35 / 0.035)  # the joint's speed limit


if __name__ == '__main__':
    unittest.main()
