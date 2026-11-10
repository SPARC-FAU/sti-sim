"""Verify live clock, rendered camera/lidar, TF and optionally commanded motion.

Run with the ROS environment sourced. --motion moves the demo robot briefly.
Uses wall time so a missing /clock cannot hang the test.
"""
import argparse
import math
import os
import time

import rclpy
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from rclpy.qos import qos_profile_sensor_data
from rosgraph_msgs.msg import Clock
from sensor_msgs.msg import Image, JointState, LaserScan
from tf2_ros import Buffer, TransformListener


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--motion', action='store_true')
    parser.add_argument('--timeout', type=float, default=300)  # allows for a start-up retry
    parser.add_argument('--scene', choices=['hospital', 'demo'], default=os.environ.get('STI_SCENE', 'hospital'))
    args = parser.parse_args()
    rclpy.init()
    node = rclpy.create_node('sti_acceptance_check')
    counts = dict(clock=0, scan=0, image=0, odom=0)
    hospital = args.scene == 'hospital'
    if hospital:
        counts.update(joints=0, rear_scan=0)
    positions = []
    scan_frames = set()
    failures = []

    def clock(msg):
        if msg.clock.sec or msg.clock.nanosec:
            counts['clock'] += 1

    def scan(msg):
        if any(math.isfinite(x) and msg.range_min < x < msg.range_max for x in msg.ranges):
            counts['scan'] += 1
            scan_frames.add(msg.header.frame_id)

    def rear_scan(msg):
        if any(math.isfinite(x) and msg.range_min < x < msg.range_max for x in msg.ranges):
            counts['rear_scan'] += 1

    def joints(msg):
        if (any('arm_left_' in name for name in msg.name)
                and any('arm_right_' in name for name in msg.name)
                and len(msg.position) == len(msg.name)):
            counts['joints'] += 1

    def image(msg):
        if msg.width > 0 and msg.height > 0 and len(msg.data) >= msg.height * msg.step:
            # A plain wall may legitimately have one colour. Reject all-zero
            # buffers; scene geometry is independently checked through lidar.
            if any(msg.data):
                counts['image'] += 1

    def odom(msg):
        counts['odom'] += 1
        positions.append((msg.pose.pose.position.x, msg.pose.pose.position.y))

    subscriptions = [
        node.create_subscription(Clock, '/clock', clock, qos_profile_sensor_data),
        node.create_subscription(LaserScan, '/scan_front_raw' if hospital else '/scan', scan, qos_profile_sensor_data),
        node.create_subscription(Image, '/head_front_camera/color/image_raw' if hospital else '/camera/image_raw', image, qos_profile_sensor_data),
        node.create_subscription(Odometry, '/mobile_base_controller/odom' if hospital else '/odom', odom, qos_profile_sensor_data),
    ]
    if hospital:
        subscriptions += [node.create_subscription(JointState, '/joint_states', joints, qos_profile_sensor_data),
                          node.create_subscription(LaserScan, '/scan_rear_raw', rear_scan, qos_profile_sensor_data)]
    buffer = Buffer()
    listener = TransformListener(buffer, node)
    publisher = node.create_publisher(Twist, '/mobile_base_controller/cmd_vel_unstamped' if hospital else '/cmd_vel', 10)

    def has_lidar_transform():
        # Hospital RViz uses the robot base; odometry isn't required to view sensors.
        frame = 'base_footprint' if hospital else 'odom'
        return bool(scan_frames) and all(buffer.can_transform(frame, sensor, rclpy.time.Time()) for sensor in scan_frames)
    try:
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.1)
            if min(counts.values()) >= 3 and has_lidar_transform():
                break
        failures.extend(name for name, count in counts.items() if count < 3)
        if not has_lidar_transform():
            failures.append('lidar transform')
        if args.motion and not failures:
            start = positions[-1]
            command = Twist()
            command.linear.x = 0.15
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                publisher.publish(command)
                rclpy.spin_once(node, timeout_sec=0.05)
            publisher.publish(Twist())
            # Permit bridge/odometry delivery before checking displacement.
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                rclpy.spin_once(node, timeout_sec=0.05)
            if math.dist(start, positions[-1]) < 0.05:
                failures.append('commanded motion')
        print('Valid messages:', counts)
        if failures:
            raise SystemExit('FAIL: ' + ', '.join(failures) + '. Inspect /tmp/sti/{gazebo,bridge,rviz}.log')
        print('PASS: rendered sensors, clock, odometry, TF' + (' and motion' if args.motion else ''))
    finally:
        for _ in range(3):
            publisher.publish(Twist())
            rclpy.spin_once(node, timeout_sec=0.05)
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
