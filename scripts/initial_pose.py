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


VELOCITY_CONTROL = 'ignition::gazebo::systems::VelocityControl'
ODOMETRY_PUBLISHER = 'ignition::gazebo::systems::OdometryPublisher'
PLANAR_BASE = (
    ('ignition-gazebo-velocity-control-system', VELOCITY_CONTROL,
     (('topic', '/mobile_base_controller/cmd_vel_unstamped'),)),
    ('ignition-gazebo-odometry-publisher-system', ODOMETRY_PUBLISHER,
     (('odom_frame', 'odom'), ('robot_base_frame', 'base_footprint'),
      ('odom_topic', '/mobile_base_controller/odom'), ('tf_topic', '/mobile_base_controller/tf'),
      ('odom_publish_frequency', '50'), ('dimensions', '2'))),
)


def add_planar_base(description):
    """Drive the omni base like PAL's public simulation does.

    PAL's mobile_base_controller type (omni_drive_controller) isn't publicly
    released for Humble. PAL's public Classic simulation instead moves the base
    with a planar-move plugin on the same ROS topics; these are the Fortress
    equivalents. Commands on /mobile_base_controller/cmd_vel_unstamped
    (x, y, yaw) move the robot and ground-truth odometry is published on
    /mobile_base_controller/odom with the odom -> base_footprint transform.
    Wheels don't model mecanum rollers.
    """
    robot = ET.fromstring(description)
    present = {plugin.get('name') for plugin in robot.findall('gazebo/plugin')}
    gazebo = None
    for filename, name, parameters in PLANAR_BASE:
        if name in present:
            continue
        if gazebo is None:
            gazebo = ET.SubElement(robot, 'gazebo')
        plugin = ET.SubElement(gazebo, 'plugin', filename=filename, name=name)
        for key, value in parameters:
            ET.SubElement(plugin, key).text = value
    return ET.tostring(robot, encoding='unicode')


def configure_robot_description():
    """Update RSP's parameter and wait for its latched topic before spawning.

    Gazebo's controller plugin reads the same description from RSP, so physical
    joint state and controller state both start folded. No motion action or
    MoveIt server is needed, and no installed PAL package is modified. The
    simulated base systems are added to the same description, so the spawned
    model and /robot_description stay identical.
    """
    import time
    from ament_index_python.packages import get_package_share_directory
    import rclpy
    from rcl_interfaces.srv import GetParameters, SetParameters
    from rclpy.parameter import Parameter
    from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy
    from std_msgs.msg import String

    config = yaml.safe_load(Path('/opt/sti/config/hospital.yaml').read_text())
    setting = config.get('initial_pose', 'home')
    if setting not in ('home', None):
        raise ValueError('Supported initial_pose values: home or null')
    positions = None
    if setting == 'home':
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
        description = response.values[0].string_value
        if positions is not None:
            description = seed_description(description, positions)
        description = add_planar_base(description)
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
        if positions is not None:
            Path('/tmp/sti/initial-pose.json').write_text(json.dumps(positions, indent=2) + '\n')
            print(f'Initial pose: PAL home, {len(positions)} joints seeded before spawn', flush=True)
        print('Base: planar velocity control and ground-truth odometry added before spawn', flush=True)
    finally:
        node.destroy_node()
        rclpy.shutdown()
