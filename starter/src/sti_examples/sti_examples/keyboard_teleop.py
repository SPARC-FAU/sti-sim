"""Drive TIAGo Pro from the keyboard: base, torso height and head.

Run it in a simulation terminal:

    ros2 run sti_examples keyboard_teleop

Each part of the robot has its own short section below. They don't depend on
each other, so you can copy one into your own node.
"""
import select
import sys
import termios
import tty

import rclpy
from geometry_msgs.msg import Twist
from rclpy.duration import Duration
from rclpy.node import Node
from sensor_msgs.msg import JointState
from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint

HELP = """
Base            Torso          Head
  w  forward      t  up          i  look up
  s  backward     b  down        k  look down
  a  turn left                   j  look left
  d  turn right                  l  look right
  q  slide left
  e  slide right
  space  stop

r  reset: stop, torso and head back to their starting pose
x or Ctrl-C quits. The base keeps moving until you press space.
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
    ' ': (0, 0, 0),
}


def base_velocity(key):
    forward, sideways, turning = BASE_KEYS[key]
    velocity = Twist()
    velocity.linear.x = forward * LINEAR_SPEED
    velocity.linear.y = sideways * LINEAR_SPEED
    velocity.angular.z = turning * ANGULAR_SPEED
    return velocity


# ------------------------------------------------------ torso and head
# Joints take a target position, not a velocity. A target is sent once, as a
# trajectory with a single point, to the controller that owns the joint. The
# point also says when to arrive: allow enough time for the distance, because
# a controller that cannot arrive in time gives up and stops where it is.
# The arms and grippers work the same way through their own controllers
# (`ros2 control list_controllers` shows all of them).
JOINT_KEYS = {  # key: (joint, step)
    't': ('torso_lift_joint', 0.02), 'b': ('torso_lift_joint', -0.02),  # metres
    'j': ('head_1_joint', 0.1), 'l': ('head_1_joint', -0.1),            # radians
    'i': ('head_2_joint', 0.1), 'k': ('head_2_joint', -0.1),
}
JOINT_LIMITS = {  # lowest and highest position the joint can reach
    'torso_lift_joint': (0.0, 0.35),
    'head_1_joint': (-1.3, 1.3),
    'head_2_joint': (-1.0, 0.34),
}
HOME = {  # the pose the simulation starts in, restored by `r`
    'torso_lift_joint': 0.1,
    'head_1_joint': 0.0,
    'head_2_joint': 0.0,
}
CONTROLLERS = {  # controller topic: the joints it moves, in its order
    '/torso_controller/joint_trajectory': ['torso_lift_joint'],
    '/head_controller/joint_trajectory': ['head_1_joint', 'head_2_joint'],
}
JOINT_SPEED = {  # how fast to move; the torso cannot exceed 0.035 m/s
    'torso_lift_joint': 0.03,  # m/s
    'head_1_joint': 1.0,       # rad/s
    'head_2_joint': 1.0,
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


def clamp(value, limits):
    return max(limits[0], min(limits[1], value))


# ------------------------------------------------------------ keyboard
def read_key(timeout):
    """Return the pressed key, or None after `timeout` seconds without one."""
    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    return sys.stdin.read(1) if ready else None


def main():
    rclpy.init()
    node = Node('keyboard_teleop')
    base = node.create_publisher(Twist, '/key_vel', 1)
    joints = {topic: node.create_publisher(JointTrajectory, topic, 1) for topic in CONTROLLERS}

    # Where the joints are now (from /joint_states) and where they should go.
    positions = {}
    targets = {}

    def joint_states(states):
        for name, position in zip(states.name, states.position):
            if name in JOINT_LIMITS:
                positions[name] = position
                targets.setdefault(name, position)  # start from the current pose

    node.create_subscription(JointState, '/joint_states', joint_states, 1)

    velocity = Twist()
    terminal = termios.tcgetattr(sys.stdin)
    print(HELP)
    try:
        tty.setcbreak(sys.stdin)  # deliver keys immediately, without Enter
        while True:
            key = read_key(timeout=0.1)
            if key in ('x', '\x03'):
                break
            if key in BASE_KEYS:
                velocity = base_velocity(key)
            moved = []  # joints that got a new target
            if key in JOINT_KEYS and len(targets) == len(JOINT_LIMITS):
                joint, step = JOINT_KEYS[key]
                targets[joint] = clamp(targets[joint] + step, JOINT_LIMITS[joint])
                moved = [joint]
            if key == 'r' and len(targets) == len(JOINT_LIMITS):
                velocity = Twist()
                targets.update(HOME)
                moved = list(HOME)
            for topic, names in CONTROLLERS.items():
                if any(joint in names for joint in moved):
                    joints[topic].publish(joint_command(names, targets, positions))
            base.publish(velocity)  # every 0.1 s, also while standing still
            rclpy.spin_once(node, timeout_sec=0)
    except KeyboardInterrupt:
        pass
    finally:
        base.publish(Twist())  # stop
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, terminal)
        rclpy.shutdown()


if __name__ == '__main__':
    main()
