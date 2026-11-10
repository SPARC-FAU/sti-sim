"""Check the course development libraries without launching student services.

Run after sourcing /opt/ros/humble/setup.bash. Used by the image build as well
as by students diagnosing their environment. No task code or maps are generated.
"""
import importlib
from pathlib import Path
import shutil
import subprocess
import tempfile


def main():
    from ament_index_python.packages import get_package_prefix

    for tool in ('colcon', 'rosdep', 'vcs', 'git', 'cmake', 'g++'):
        if not shutil.which(tool):
            raise RuntimeError(f'Missing development tool: {tool}')
    for module in ('rclpy', 'cv2', 'cv_bridge', 'numpy', 'yaml'):
        importlib.import_module(module)
    packages = (
        'rclcpp', 'ament_cmake', 'nav2_bringup', 'nav2_simple_commander',
        'slam_toolbox', 'moveit_ros_move_group', 'moveit_ros_planning_interface',
        'moveit_task_constructor_core', 'moveit_task_constructor_visualization',
        'tiago_pro_moveit_config', 'ros2_control', 'ros2_controllers', 'xacro',
        'rmw_cyclonedds_cpp',
    )
    for package in packages:
        get_package_prefix(package)
    # Verify a ROS C++ program can find headers and link, not just that g++ exists.
    with tempfile.TemporaryDirectory(prefix='sti-dev-check-') as directory:
        root = Path(directory)
        (root/'main.cpp').write_text('#include <rclcpp/rclcpp.hpp>\nint main() { return 0; }\n')
        (root/'CMakeLists.txt').write_text(
            'cmake_minimum_required(VERSION 3.16)\n'
            'project(sti_dependency_check LANGUAGES CXX)\n'
            'set(CMAKE_CXX_STANDARD 17)\n'
            'find_package(ament_cmake REQUIRED)\n'
            'find_package(rclcpp REQUIRED)\n'
            'add_executable(check main.cpp)\n'
            'ament_target_dependencies(check rclcpp)\n')
        subprocess.run(['cmake', '-S', str(root), '-B', str(root/'build')], check=True)
        subprocess.run(['cmake', '--build', str(root/'build'), '--parallel', '1'], check=True)
        subprocess.run([str(root/'build/check')], check=True)
    print('PASS: course tools, ROS libraries, perception imports and C++17 ROS compilation')


if __name__ == '__main__':
    main()
