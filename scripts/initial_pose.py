"""Prepare the simulated TIAGo's description before creation: PAL home pose and base drive."""
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import yaml

MOTION_FILE = 'tiago_pro_motions_general_spherical-wrist.yaml'
BASE_TOPIC = '/mobile_base_controller'


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


def add_base_drive(description):
    """Make the base drivable and report odometry on PAL's topic names.

    PAL's omni_drive_controller is not part of the public ROS packages, so no
    ros2_control controller can drive the base. As PAL's public Gazebo Classic
    simulation does with its planar-move plugin, the base is moved as a whole:
    Gazebo applies the commanded body velocity and reports odometry. Like that
    plugin, it keeps the last command until a new one (or zero) arrives.
    """
    robot = ET.fromstring(description)
    gazebo = ET.SubElement(robot, 'gazebo')
    control = ET.SubElement(gazebo, 'plugin', {
        'filename': 'ignition-gazebo-velocity-control-system',
        'name': 'ignition::gazebo::systems::VelocityControl'})
    ET.SubElement(control, 'topic').text = BASE_TOPIC + '/cmd_vel_unstamped'
    odometry = ET.SubElement(gazebo, 'plugin', {
        'filename': 'ignition-gazebo-odometry-publisher-system',
        'name': 'ignition::gazebo::systems::OdometryPublisher'})
    for name, value in (('odom_topic', BASE_TOPIC + '/odom'), ('tf_topic', BASE_TOPIC + '/odom_tf'),
                        ('odom_frame', 'odom'), ('robot_base_frame', 'base_footprint'),
                        ('odom_publish_frequency', '50'), ('dimensions', '2')):
        ET.SubElement(odometry, name).text = value
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
    positions = {}
    if config.get('initial_pose', 'home') is not None:
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
        description = add_base_drive(seed_description(response.values[0].string_value, positions))
        request = SetParameters.Request()
        request.parameters = [Parameter('robot_description', value=description).to_parameter_msg()]
        response = call(SetParameters, 'set_parameters', request)
        if len(response.results) != 1 or not response.results[0].successful:
            raise RuntimeError('Robot state publisher rejected the prepared description')
        # Parameter-event handling republishes asynchronously. Wait for the exact
        # updated description, otherwise the spawner could latch the old one.
        deadline = time.monotonic() + 20
        while latest != [description]:
            if time.monotonic() >= deadline:
                raise RuntimeError('Prepared robot description was not republished before spawn')
            rclpy.spin_once(node, timeout_sec=0.1)
        Path('/tmp/sti/initial-pose.json').write_text(json.dumps(positions, indent=2) + '\n')
        print(f'Robot description prepared: base drive added, {len(positions)} home-pose joints seeded', flush=True)
    finally:
        node.destroy_node()
        rclpy.shutdown()
