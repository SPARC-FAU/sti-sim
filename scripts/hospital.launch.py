"""PAL robot bring-up, explicit Fortress spawn, sensor bridge and RViz."""
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
    # PAL's nested default-controller launch does not forward gazebo_version to
    # the base launch in current Humble releases, so its Classic-only condition
    # can skip this controller. Include it explicitly with the modern simulator.
    base_controller = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(str(Path(get_package_share_directory('omni_base_controller_configuration')) / 'launch/mobile_base_controller.launch.py')),
        launch_arguments={'use_sim_time': 'True', 'is_public_sim': 'True',
                          'gazebo_version': 'gazebo'}.items(),
    )
    gazebo = ExecuteProcess(cmd=['ign', 'gazebo', '-r', '-v', '3', str(ROOT / 'worlds/hospital.sdf')], output='screen')
    spawn = ExecuteProcess(cmd=['python3', str(ROOT / 'scripts/spawn_robot.py')], output='screen')
    bridge = Node(package='ros_gz_bridge', executable='parameter_bridge', name='hospital_sensor_bridge',
                  parameters=[{'config_file': str(ROOT / 'config/tiago_bridge.yaml'), 'use_sim_time': True}], output='screen')
    rviz = Node(package='rviz2', executable='rviz2', name='rviz',
                arguments=['-d', str(ROOT / 'config/hospital.rviz')], parameters=[{'use_sim_time': True}], output='screen')
    for process in (gazebo, bridge, rviz):
        actions.append(RegisterEventHandler(OnProcessExit(target_action=process,
            on_exit=[EmitEvent(event=Shutdown(reason='An essential hospital process exited; inspect gazebo.log'))])))
    def spawn_exited(event, context):
        if event.returncode != 0:
            return [EmitEvent(event=Shutdown(reason='TIAGo spawn failed; inspect gazebo.log'))]
        return []
    actions.append(RegisterEventHandler(OnProcessExit(target_action=spawn, on_exit=spawn_exited)))
    actions += [gazebo, bringup, base_controller, spawn, bridge, rviz]
    return LaunchDescription(actions)
