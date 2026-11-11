"""Drive TIAGo Pro from the keyboard: base, torso height and head.

Run it in a simulation terminal:

    ros2 run sti_examples keyboard_teleop

The robot moves for as long as a key is held and stops when it is released.

Each part of the robot has its own short section below. They don't depend on
each other, so you can copy one into your own node.
"""
import select
import sys
import termios
import time
import tty

import rclpy
from geometry_msgs.msg import Twist
from rclpy.duration import Duration
from rclpy.node import Node
from sensor_msgs.msg import JointState
from std_srvs.srv import Trigger
from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint

HELP = """
Hold a key to move, release it to stop.

Base            Torso          Head
  w  forward      t  up          i  look up
  s  backward     b  down        k  look down
  a  turn left                   j  look left
  d  turn right                  l  look right
  q  slide left
  e  slide right

r  reset the simulation: robot back to where and how it started
x or Ctrl-C quits.
"""

# ---------------------------------------------------------------- base
# The base takes a velocity: forward (x) and sideways (y) in m/s, turning
# (angular z) in rad/s. It stops when commands stop arriving, so a velocity
# has to be published repeatedly for as long as the robot should move.
# /key_vel is the input for keyboard control; your own nodes use /cmd_vel.
LINEAR_SPEED = 0.3   # m/s
ANGULAR_SPEED = 0.6  # rad/s
BASE_KEYS = {        # key: (forward, sideways, turning)
    'w': (1, 0, 0), 's': (-1, 0, 0),
    'a': (0, 0, 1), 'd': (0, 0, -1),
    'q': (0, 1, 0), 'e': (0, -1, 0),
}


def base_velocity(key):
    """The velocity for a held key; zero for any other key or none."""
    forward, sideways, turning = BASE_KEYS.get(key, (0, 0, 0))
    velocity = Twist()
    velocity.linear.x = forward * LINEAR_SPEED
    velocity.linear.y = sideways * LINEAR_SPEED
    velocity.angular.z = turning * ANGULAR_SPEED
    return velocity


# ------------------------------------------------------ torso and head
# Joints take a target position, not a velocity. A target is sent as a
# trajectory with a single point to the controller that owns the joint. The
# point also says when to arrive: allow enough time for the distance, because
# a controller that cannot arrive in time gives up and stops where it is.
# The arms and grippers work the same way through their own controllers
# (`ros2 control list_controllers` shows all of them).
JOINT_LIMITS = {  # lowest and highest position the joint can reach
    'torso_lift_joint': (0.0, 0.35),  # metres
    'head_1_joint': (-1.3, 1.3),      # radians, left is positive
    'head_2_joint': (-1.0, 0.34),     # radians, up is positive
}
JOINT_SPEED = {  # how fast to move; the torso cannot exceed 0.035 m/s
    'torso_lift_joint': 0.03,  # m/s
    'head_1_joint': 0.5,       # rad/s
    'head_2_joint': 0.5,
}
CONTROLLERS = {  # controller topic: the joints it moves, in its order
    '/torso_controller/joint_trajectory': ['torso_lift_joint'],
    '/head_controller/joint_trajectory': ['head_1_joint', 'head_2_joint'],
}


def joint_command(joints, targets, positions):
    """A trajectory that moves `joints` from `positions` to `targets`."""
    seconds = max(abs(targets[joint] - positions[joint]) / JOINT_SPEED[joint] for joint in joints)
    point = JointTrajectoryPoint()
    point.positions = [targets[joint] for joint in joints]
    point.time_from_start = Duration(seconds=max(seconds, 0.5)).to_msg()
    command = JointTrajectory()
    command.joint_names = joints
    command.points = [point]
    return command


# To move a joint only while a key is held: pressing the key sends the joint
# towards its limit, releasing it sets the target to the current position,
# which stops the joint. The controller's other joints stay where they are.
JOINT_KEYS = {  # key: (joint, direction)
    't': ('torso_lift_joint', 1), 'b': ('torso_lift_joint', -1),
    'j': ('head_1_joint', 1), 'l': ('head_1_joint', -1),
    'i': ('head_2_joint', 1), 'k': ('head_2_joint', -1),
}


def joint_limit(joint, direction):
    low, high = JOINT_LIMITS[joint]
    return high if direction > 0 else low


# ------------------------------------------------------------ keyboard
# A terminal reports a held key as the same key arriving again and again, and
# reports nothing on release. A key therefore counts as held until it has not
# arrived for KEY_HOLD seconds. This must be longer than the keyboard's pause
# before it starts repeating, or motion stutters at the start.
KEY_HOLD = 0.5


def read_key(timeout):
    """Return the pressed key, or None after `timeout` seconds without one."""
    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    return sys.stdin.read(1) if ready else None


def main():
    rclpy.init()
    node = Node('keyboard_teleop')
    base = node.create_publisher(Twist, '/key_vel', 1)
    controllers = {topic: node.create_publisher(JointTrajectory, topic, 1) for topic in CONTROLLERS}

    # /reset_simulation exists only in the simulation: it puts the robot back
    # where and how it started. A real robot has no such service.
    reset = node.create_client(Trigger, '/reset_simulation')

    # Where the joints are now, from /joint_states.
    positions = {}

    def joint_states(states):
        for name, position in zip(states.name, states.position):
            if name in JOINT_LIMITS:
                positions[name] = position

    node.create_subscription(JointState, '/joint_states', joint_states, 1)

    def move(joint, target):
        """Send `joint` to `target`; other joints of its controller stay put."""
        targets = dict(positions)
        targets[joint] = target
        for topic, names in CONTROLLERS.items():
            if joint in names:
                controllers[topic].publish(joint_command(names, targets, positions))

    held = None        # the key currently held down
    last_seen = 0.0    # when it last arrived
    moving = None      # the joint key that is currently moving a joint
    terminal = termios.tcgetattr(sys.stdin)
    print(HELP)
    try:
        tty.setcbreak(sys.stdin)  # deliver keys immediately, without Enter
        while True:
            key = read_key(timeout=0.1)
            if key in ('x', '\x03'):
                break
            if key == 'r' and held != 'r':  # once per press, not while held
                reset.call_async(Trigger.Request())
            if key is not None:
                held, last_seen = key, time.monotonic()
            elif time.monotonic() - last_seen > KEY_HOLD:
                held = None

            base.publish(base_velocity(held))  # zero unless a base key is held

            ready = len(positions) == len(JOINT_LIMITS)
            joint_key = held if held in JOINT_KEYS else None
            if joint_key != moving and ready:
                if moving:  # released: stop where the joint is now
                    joint, _ = JOINT_KEYS[moving]
                    move(joint, positions[joint])
                if joint_key:  # pressed: head for the limit
                    joint, direction = JOINT_KEYS[joint_key]
                    move(joint, joint_limit(joint, direction))
                moving = joint_key
            rclpy.spin_once(node, timeout_sec=0)
    except KeyboardInterrupt:
        pass
    finally:
        base.publish(Twist())  # stop
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, terminal)
        rclpy.shutdown()


if __name__ == '__main__':
    main()
