"""PAL robot bring-up, explicit Fortress spawn and sensor bridge.

The Gazebo and RViz windows are opened by windows.sh, outside this launch, so
closing one does not stop the simulation.
"""
import os
from pathlib import Path

from ament_index_python.packages import get_package_prefix, get_package_share_directory
from launch import LaunchDescription
from launch.actions import (EmitEvent, ExecuteProcess, IncludeLaunchDescription,
                            RegisterEventHandler, SetEnvironmentVariable)
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch_ros.actions import Node

ROOT = Path('/opt/sti')


def generate_launch_description():
    packages = ('tiago_pro_description', 'pal_sea_arm_description',
                'omni_base_description', 'pal_pro_gripper_description',
                'tiago_pro_head_description', 'pal_urdf_utils')
    resource_paths = [str(ROOT / 'worlds')]
    resource_paths.extend(str(Path(get_package_prefix(p)) / 'share') for p in packages)
    resource_paths = list(dict.fromkeys(resource_paths))
    plugins = str(Path(get_package_prefix('gz_ros2_control')) / 'lib')
    actions = []
    # Fortress uses IGN_*; newer PAL description files also use GZ_*.
    for name in ('IGN_GAZEBO_RESOURCE_PATH', 'GZ_SIM_RESOURCE_PATH'):
        actions.append(SetEnvironmentVariable(name, os.pathsep.join(resource_paths + [os.environ.get(name, '')])))
    for name in ('IGN_GAZEBO_SYSTEM_PLUGIN_PATH', 'GZ_SIM_SYSTEM_PLUGIN_PATH'):
        actions.append(SetEnvironmentVariable(name, os.pathsep.join([plugins, os.environ.get(name, '')])))
    bringup = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(str(Path(get_package_share_directory('tiago_pro_bringup')) / 'launch/tiago_pro_bringup.launch.py')),
        launch_arguments={'use_sim_time': 'True', 'is_public_sim': 'True',
                          'gazebo_version': 'gazebo'}.items(),
    )
    # The base isn't a ros2_control controller here: PAL's omni_drive_controller
    # isn't publicly released for Humble. initial_pose.add_planar_base adds the
    # Gazebo systems that move the base and publish odometry, and
    # base_controller.py serves /mobile_base_controller/cmd_vel_unstamped.
    # Server only: in combined mode Fortress's server waits for the GUI to hand
    # over the world, so a GUI stuck in graphics-driver initialisation would
    # also stop physics, sensors and spawning.
    gazebo = ExecuteProcess(cmd=['ign', 'gazebo', '-s', '-r', '-v', '3', str(ROOT / 'worlds/hospital.sdf')],
                            name='gazebo_server', output='screen')
    spawn = ExecuteProcess(cmd=['python3', str(ROOT / 'scripts/spawn_robot.py')], output='screen')
    bridge = Node(package='ros_gz_bridge', executable='parameter_bridge', name='hospital_sensor_bridge',
                  parameters=[{'config_file': str(ROOT / 'config/tiago_bridge.yaml'), 'use_sim_time': True}], output='screen')
    base = ExecuteProcess(cmd=['python3', str(ROOT / 'scripts/base_controller.py')],
                          name='base_controller', output='screen')
    for process in (gazebo, bridge, base):
        actions.append(RegisterEventHandler(OnProcessExit(target_action=process,
            on_exit=[EmitEvent(event=Shutdown(reason='An essential hospital process exited; inspect gazebo.log'))])))
    def spawn_exited(event, context):
        if event.returncode != 0:
            return [EmitEvent(event=Shutdown(reason='TIAGo spawn failed; inspect gazebo.log'))]
        return []
    actions.append(RegisterEventHandler(OnProcessExit(target_action=spawn, on_exit=spawn_exited)))
    actions += [gazebo, bringup, spawn, bridge, base]
    return LaunchDescription(actions)
