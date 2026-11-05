"""Seed the simulated TIAGo with the installed PAL home pose before creation."""
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import yaml

MOTION_FILE = 'tiago_pro_motions_general_spherical-wrist.yaml'


def home_positions(motions):
    motion = motions['/**']['ros__parameters']['motions']['home']
    names = motion['joints']
    values = motion['positions']
    times = motion['times_from_start']
    if not names or len(set(names)) != len(names) or len(values) != len(names) * len(times):
        raise ValueError('PAL home motion has inconsistent joint/waypoint dimensions')
    positions = dict(zip(names, (float(v) for v in values[-len(names):])))
    required = {f'arm_{side}_{index}_joint' for side in ('left', 'right') for index in range(1, 8)}
    required.add('torso_lift_joint')
    if set(positions) != required or not all(math.isfinite(v) for v in positions.values()):
        raise ValueError('PAL home pose must describe the torso and both seven-joint arms')
    return positions


def seed_description(description, positions):
    robot = ET.fromstring(description)
    systems = [system for system in robot.findall('ros2_control')
               if system.findtext('hardware/plugin') == 'gz_ros2_control/GazeboSimSystem']
    if len(systems) != 1:
        raise ValueError('Initial pose may only be applied to a gz_ros2_control simulation description')
    controls = {joint.get('name'): joint for joint in systems[0].findall('joint')}
    joints = {joint.get('name'): joint for joint in robot.findall('joint')}
    for name, value in positions.items():
        if name not in joints or name not in controls:
            raise ValueError(f'Home pose joint is absent from the installed robot: {name}')
        limit = joints[name].find('limit')
        if limit is None or not float(limit.get('lower', '-inf')) <= value <= float(limit.get('upper', 'inf')):
            raise ValueError(f'Home pose is outside the installed robot limits: {name}={value}')
        interface = controls[name].find("state_interface[@name='position']")
        if interface is None:
            raise ValueError(f'Simulated joint has no position state interface: {name}')
        initial = interface.find("param[@name='initial_value']")
        if initial is None:
            initial = ET.SubElement(interface, 'param', {'name': 'initial_value'})
        initial.text = str(value)
    return ET.tostring(robot, encoding='unicode')


def configure_initial_pose():
    """Update RSP's parameter and wait for its latched topic before spawning.

    Gazebo's controller plugin reads the same description from RSP, so physical
    joint state and controller state both start folded. No motion action or
    MoveIt server is needed, and no installed PAL package is modified.
    """
    import time
    from ament_index_python.packages import get_package_share_directory
    import rclpy
    from rcl_interfaces.srv import GetParameters, SetParameters
    from rclpy.parameter import Parameter
    from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy
    from std_msgs.msg import String

    config = yaml.safe_load(Path('/opt/sti/config/hospital.yaml').read_text())
    if config.get('initial_pose', 'home') is None:
        return
    if config.get('initial_pose', 'home') != 'home':
        raise ValueError('Supported initial_pose values: home or null')
    path = Path(get_package_share_directory('tiago_pro_bringup'))/'config/motions'/MOTION_FILE
    positions = home_positions(yaml.safe_load(path.read_text()))
    rclpy.init()
    node = rclpy.create_node('sti_initial_pose')
    latest = []

    def received(message):
        latest[:] = [message.data]

    subscription = node.create_subscription(String, '/robot_description', received,
        QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL,
                   reliability=ReliabilityPolicy.RELIABLE))

    def call(service_type, suffix, request):
        client = node.create_client(service_type, '/robot_state_publisher/' + suffix)
        deadline = time.monotonic() + 90
        while not client.wait_for_service(timeout_sec=1):
            if time.monotonic() >= deadline:
                raise RuntimeError(f'Robot description service unavailable: {suffix}')
        future = client.call_async(request)
        rclpy.spin_until_future_complete(node, future, timeout_sec=15)
        if not future.done() or future.exception():
            raise RuntimeError(f'Robot description service failed: {suffix}')
        return future.result()

    try:
        request = GetParameters.Request()
        request.names = ['robot_description']
        response = call(GetParameters, 'get_parameters', request)
        if len(response.values) != 1 or not response.values[0].string_value:
            raise RuntimeError('Robot state publisher returned an empty description')
        description = seed_description(response.values[0].string_value, positions)
        request = SetParameters.Request()
        request.parameters = [Parameter('robot_description', value=description).to_parameter_msg()]
        response = call(SetParameters, 'set_parameters', request)
        if len(response.results) != 1 or not response.results[0].successful:
            raise RuntimeError('Robot state publisher rejected the seeded initial pose')
        # Parameter-event handling republishes asynchronously. Wait for the exact
        # updated description, otherwise the spawner could latch the old one.
        deadline = time.monotonic() + 20
        while latest != [description]:
            if time.monotonic() >= deadline:
                raise RuntimeError('Seeded robot description was not republished before spawn')
            rclpy.spin_once(node, timeout_sec=0.1)
        Path('/tmp/sti/initial-pose.json').write_text(json.dumps(positions, indent=2) + '\n')
        print(f'Initial pose: PAL home, {len(positions)} joints seeded before spawn', flush=True)
    finally:
        node.destroy_node()
        rclpy.shutdown()
