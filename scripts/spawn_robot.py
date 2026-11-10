"""Wait for the actual hospital create service before spawning the PAL robot."""
from pathlib import Path
import subprocess
import time
import yaml


STALLED = Path('/tmp/sti/startup-stalled')


def controllers_started(timeout=40):
    """Whether Gazebo's ros2_control plugin came up after the robot was created.

    The plugin fetches the robot description with a ROS service call and waits
    for the answer without a timeout. On a busy machine that answer is
    occasionally lost while the two nodes are still discovering each other
    ("failed to send response ... client will not receive response"), which
    freezes the Gazebo server for good. Normally this takes a few seconds.
    """
    import rclpy
    from controller_manager_msgs.srv import ListControllers
    rclpy.init()
    node = rclpy.create_node('sti_startup_check')
    try:
        client = node.create_client(ListControllers, '/controller_manager/list_controllers')
        return client.wait_for_service(timeout_sec=timeout)
    finally:
        node.destroy_node()
        rclpy.shutdown()


def main():
    from initial_pose import configure_initial_pose
    configure_initial_pose()
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(['ign', 'service', '-l'], capture_output=True, text=True, timeout=5)
            if '/world/hospital/create' in result.stdout.splitlines():
                break
        except subprocess.TimeoutExpired:
            pass
        time.sleep(0.5)
    else:
        raise SystemExit('Hospital create service was not available within 180 seconds')
    pose = yaml.safe_load(Path('/opt/sti/config/hospital.yaml').read_text())['spawn']
    command = ['ros2', 'run', 'ros_gz_sim', 'create', '-world', 'hospital',
               '-name', 'tiago_pro', '-topic', '/robot_description',
               '-x', str(pose['x']), '-y', str(pose['y']), '-z', str(pose['z']), '-Y', str(pose['yaw'])]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    output = result.stdout + result.stderr
    print(output, flush=True)
    # Some Humble versions return zero even for a failed create request.
    if result.returncode or 'Requested creation of entity' not in output:
        raise SystemExit('TIAGo creation was not acknowledged by Gazebo')
    if not controllers_started():
        # demo.sh restarts the launch when it finds this marker.
        STALLED.write_text('controller manager did not start\n')
        raise SystemExit('Robot controllers did not start; the simulation will be restarted')


if __name__ == '__main__':
    main()
