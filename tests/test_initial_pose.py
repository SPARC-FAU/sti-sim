import importlib.util
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('initial_pose', ROOT/'scripts/initial_pose.py')
pose = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pose)


class InitialPose(unittest.TestCase):
    def motion(self):
        names = ['torso_lift_joint'] + [f'arm_{side}_{i}_joint' for side in ('left', 'right') for i in range(1, 8)]
        final = [0.1] + [0.36, -1.83, 0.47, -2.35, 0, -1.2, 0] + [-0.36, -1.83, -0.47, -2.35, 0, -1.2, 0]
        return {'/**': {'ros__parameters': {'motions': {'home': {
            'joints': names, 'times_from_start': [0, 10], 'positions': [0]*15 + final}}}}}

    def description(self, positions, plugin='gz_ros2_control/GazeboSimSystem'):
        robot = ET.Element('robot', name='test')
        control = ET.SubElement(robot, 'ros2_control')
        ET.SubElement(ET.SubElement(control, 'hardware'), 'plugin').text = plugin
        for name in positions:
            joint = ET.SubElement(robot, 'joint', name=name)
            ET.SubElement(joint, 'limit', lower='-3', upper='3')
            joint = ET.SubElement(control, 'joint', name=name)
            ET.SubElement(joint, 'state_interface', name='position')
        return ET.tostring(robot, encoding='unicode')

    def test_final_home_waypoint_is_used(self):
        positions = pose.home_positions(self.motion())
        self.assertEqual(len(positions), 15)
        self.assertEqual(positions['arm_left_1_joint'], 0.36)
        self.assertEqual(positions['arm_right_1_joint'], -0.36)
        self.assertEqual(positions['torso_lift_joint'], 0.1)

    def test_seeds_all_control_states_without_altering_kinematics(self):
        positions = pose.home_positions(self.motion())
        before = ET.fromstring(self.description(positions))
        after = ET.fromstring(pose.seed_description(ET.tostring(before, encoding='unicode'), positions))
        self.assertEqual([ET.tostring(j) for j in before.findall('joint')],
                         [ET.tostring(j) for j in after.findall('joint')])
        for joint in after.findall('ros2_control/joint'):
            value = joint.findtext("state_interface[@name='position']/param[@name='initial_value']")
            self.assertEqual(float(value), positions[joint.get('name')])

    def test_hardware_description_is_rejected(self):
        positions = pose.home_positions(self.motion())
        with self.assertRaisesRegex(ValueError, 'simulation description'):
            pose.seed_description(self.description(positions, 'real_robot/Hardware'), positions)

    def test_out_of_limit_joint_is_rejected(self):
        positions = pose.home_positions(self.motion())
        description = self.description(positions)
        positions['arm_left_4_joint'] = -100
        with self.assertRaisesRegex(ValueError, 'outside'):
            pose.seed_description(description, positions)

    def test_incomplete_pose_is_rejected(self):
        motion = self.motion()
        motion['/**']['ros__parameters']['motions']['home']['positions'].pop()
        with self.assertRaisesRegex(ValueError, 'dimensions'):
            pose.home_positions(motion)


if __name__ == '__main__':
    unittest.main()
