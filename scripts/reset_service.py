"""/reset_simulation: put the robot back the way the simulation started.

A simulation-only service (std_srvs/Trigger). It moves the robot to its spawn
pose in Gazebo and sends every joint back to the position it started in. The
hospital itself has no movable parts, so this resets the whole scene.
"""
import math
from pathlib import Path
import subprocess

import rclpy
import yaml
from control_msgs.msg import JointTrajectoryControllerState
from rclpy.duration import Duration
from rclpy.node import Node
from sensor_msgs.msg import JointState
from std_srvs.srv import Trigger
from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint

CONTROLLERS = ('torso', 'head', 'arm_left', 'arm_right', 'gripper_left', 'gripper_right')
TORSO_SPEED = 0.03  # m/s; the torso's limit is 0.035
JOINT_SPEED = 1.0   # rad/s


def spawn_request(spawn):
    """The Gazebo set_pose request that puts the robot at its spawn pose."""
    half = spawn['yaw'] / 2
    # z is the floor, where the robot ends up, not the spawn height slightly
    # above it from which it takes a while to settle.
    return (f'name: "tiago_pro", position: {{x: {spawn["x"]}, y: {spawn["y"]}, z: 0.0}}, '
            f'orientation: {{z: {math.sin(half)}, w: {math.cos(half)}}}')


def move_seconds(joints, targets, positions):
    """Time the slowest joint needs to reach its target."""
    needed = [abs(target - positions.get(joint, target)) / (TORSO_SPEED if 'torso' in joint else JOINT_SPEED)
              for joint, target in zip(joints, targets)]
    return max([1.0] + needed)


def main():
    rclpy.init()
    node = Node('reset_simulation')
    spawn = yaml.safe_load(Path('/opt/sti/config/hospital.yaml').read_text())['spawn']
    initial = {}    # controller: (joint names, positions) from its first state message
    positions = {}  # current joint positions
    publishers = {}

    def remember(controller):
        def callback(state):
            if controller not in initial and state.feedback.positions:
                initial[controller] = (list(state.joint_names), list(state.feedback.positions))
        return callback

    for controller in CONTROLLERS:
        prefix = f'/{controller}_controller'
        node.create_subscription(JointTrajectoryControllerState, prefix + '/controller_state',
                                 remember(controller), 1)
        publishers[controller] = node.create_publisher(JointTrajectory, prefix + '/joint_trajectory', 1)
    node.create_subscription(JointState, '/joint_states',
                             lambda states: positions.update(zip(states.name, states.position)), 1)

    def reset(request, response):
        try:
            result = subprocess.run(
                ['ign', 'service', '-s', '/world/hospital/set_pose', '--reqtype', 'ignition.msgs.Pose',
                 '--reptype', 'ignition.msgs.Boolean', '--timeout', '5000', '--req', spawn_request(spawn)],
                capture_output=True, text=True, timeout=15)
            moved = 'data: true' in result.stdout
        except subprocess.TimeoutExpired:
            moved = False
        for controller, (joints, targets) in initial.items():
            point = JointTrajectoryPoint()
            point.positions = targets
            point.time_from_start = Duration(seconds=move_seconds(joints, targets, positions)).to_msg()
            command = JointTrajectory()
            command.joint_names = joints
            command.points = [point]
            publishers[controller].publish(command)
        response.success = moved
        response.message = 'Robot reset.' if moved else 'Gazebo did not move the robot.'
        return response

    node.create_service(Trigger, '/reset_simulation', reset)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
