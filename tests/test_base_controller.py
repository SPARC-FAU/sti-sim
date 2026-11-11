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
class Reset(unittest.TestCase):
    def setUp(self):
        self.reset = load('scripts/reset_service.py', 'reset_service')

    def test_request_places_the_robot_at_its_spawn_pose(self):
        request = self.reset.spawn_request({'x': 1.0, 'y': 0.0, 'z': 0.05, 'yaw': 3.141592653589793})
        self.assertIn('name: "tiago_pro"', request)
        self.assertIn('position: {x: 1.0, y: 0.0, z: 0.0}', request)
        self.assertIn('orientation: {z: 1.0,', request)

    def test_the_torso_gets_enough_time_to_return(self):
        seconds = self.reset.move_seconds(['torso_lift_joint'], [0.1], {'torso_lift_joint': 0.35})
        self.assertGreaterEqual(seconds, 0.25 / 0.035)
        self.assertEqual(self.reset.move_seconds(['head_1_joint'], [0.0], {}), 1.0)


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

    def test_held_key_target_stays_within_the_joint_limits(self):
        self.assertEqual(self.teleop.joint_limit('torso_lift_joint', 1), 0.35)
        self.assertEqual(self.teleop.joint_limit('torso_lift_joint', -1), 0.0)

    def test_no_held_key_means_a_standing_base(self):
        for key in (None, 't', 'r', ' '):
            velocity = self.teleop.base_velocity(key)
            self.assertEqual((velocity.linear.x, velocity.linear.y, velocity.angular.z), (0, 0, 0))

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
