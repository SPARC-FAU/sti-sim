"""Stand-in for PAL's base controller, which isn't publicly released.

Takes velocity commands on /mobile_base_controller/cmd_vel_unstamped, as the
real controller does, and passes them to the simulated base:

- Only forward (x), sideways (y) and turning (yaw) are used. The base cannot
  leave the floor or tilt, whatever else a command contains.
- The base stops when no command has arrived for TIMEOUT seconds, so a node
  that crashes or exits doesn't leave the robot driving.
"""
import rclpy
from geometry_msgs.msg import Twist
from rclpy.duration import Duration
from rclpy.node import Node
from rclpy.parameter import Parameter

TIMEOUT = 0.5  # seconds of simulation time


def planar(command):
    """The part of a velocity command a wheeled base can execute."""
    result = Twist()
    result.linear.x = command.linear.x
    result.linear.y = command.linear.y
    result.angular.z = command.angular.z
    return result


def main():
    rclpy.init()
    node = Node('mobile_base_controller',
                parameter_overrides=[Parameter('use_sim_time', value=True)])
    state = {'command': Twist(), 'received': None}

    def receive(command):
        state['command'] = planar(command)
        state['received'] = node.get_clock().now()

    def send():
        received = state['received']
        expired = received is None or node.get_clock().now() - received > Duration(seconds=TIMEOUT)
        output.publish(Twist() if expired else state['command'])

    output = node.create_publisher(Twist, '/mobile_base_controller/sim_velocity', 1)
    node.create_subscription(Twist, '/mobile_base_controller/cmd_vel_unstamped', receive, 1)
    node.create_timer(0.05, send)  # follows simulation time
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
