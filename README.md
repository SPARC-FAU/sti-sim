# STI Sim

TIAGo Pro in the RoboCup hospital arena, with ROS 2 Humble, Gazebo and RViz.
It runs in Docker on Linux, Windows and macOS.

## 1. Start the simulation

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/)
(Windows, macOS) or Docker Engine (Linux), and Python 3.10 or newer. On
Windows, do everything below in an Ubuntu (WSL) terminal.

1. Get this folder: `git clone https://github.com/SPARC-FAU/sti-sim.git`, or
   *Code → Download ZIP* on GitHub.
2. Download `2026_09_01_RoboCup_HCC_Arena_V1.glb` (the hospital model) from the
   course forum and put it in the `worlds` folder.
3. Start Docker, open a terminal in the folder and run:

   ```sh
   python3 sim.py up
   ```

The first start takes 10 to 20 minutes. When it prints `PASS`, Gazebo (the
robot in the hospital) and RViz (its camera and lidar) open as windows. On
macOS they are shown in your browser instead, at the address `up` prints.

Closing a window does not stop the simulation: it keeps running in the
background. Run `python3 sim.py up` again to get the windows back, or
`python3 sim.py down` to stop it.

## 2. Drive the robot

Open a terminal inside the simulation:

```sh
python3 sim.py shell
```

then build the example package and start its keyboard control:

```sh
cd /workspace
colcon build --symlink-install
source /colcon/install/setup.bash
ros2 run sti_examples keyboard_teleop
```

The terminal lists the keys. Hold one to move; the robot stops when you
release it. The keys only work while that terminal is the active window.

## 3. Write your own code

The first start creates a `workspace/` folder next to `sim.py`. It is a normal
folder on your computer, so use any editor. Inside the simulation it is
`/workspace`.

The keyboard control you just used is
`workspace/src/sti_examples/sti_examples/keyboard_teleop.py`. It is one short
file that shows how to drive the base and how to move a joint, and is meant to
be copied from.

Put your own ROS 2 packages next to it in `workspace/src/`, then build and run
them the same way as the example:

```sh
ros2 run <your_package> <your_node> --ros-args -p use_sim_time:=true
```

Topics to start with (`ros2 topic list` shows all of them):

| Topic | Content |
| --- | --- |
| `/cmd_vel` | Drive the base (`geometry_msgs/Twist`). Publish repeatedly: the base stops 0.5 s after the last message. |
| `/torso_controller/joint_trajectory`, `/head_controller/joint_trajectory` | Move the torso and head (`trajectory_msgs/JointTrajectory`) |
| `/mobile_base_controller/odom` | Odometry |
| `/scan_front_raw`, `/scan_rear_raw` | Lidars |
| `/head_front_camera/color/image_raw` | Head camera |
| `/joint_states`, `/tf` | Joints and transforms |

Updating the simulation (`git pull`, then `python3 sim.py up`) leaves
`workspace/` untouched. To continue on another computer, copy the `workspace/`
folder there and run `colcon build` again.

## Commands

| Command | What it does |
| --- | --- |
| `python3 sim.py up` | Start, or re-open closed windows |
| `python3 sim.py down` | Stop |
| `python3 sim.py shell` | Terminal inside the simulation |
| `python3 sim.py check` | Test that the robot's sensors deliver data |
| `python3 sim.py clean` | Stop and delete build output, for a fresh `colcon build` |

## If something goes wrong

| Problem | Try this |
| --- | --- |
| `Docker is not running` | Start Docker Desktop, wait until it is ready, run `up` again. |
| `permission denied` (Linux) | Run `sudo usermod -aG docker "$USER"`, log out and back in. Do not use `sudo` with `sim.py`. |
| Startup ends with `FAIL` | Run `python3 sim.py down`, then `python3 sim.py up` again. |
| Still `FAIL`, or a black or broken picture | Run `python3 sim.py down`, then `python3 sim.py up --mode cpu`. |
| No windows appear, or they look broken | Run `python3 sim.py down`, then `python3 sim.py up --display browser` and open the address it prints. |
| The browser page does not load | Check that `up` printed `PASS`. If port 6080 is in use, set `STI_PORT` to another port before `up`. |
| Slow or stuttering | In Docker Desktop, *Settings → Resources*: at least 4 CPUs and 8 GB memory. On macOS it always runs slower than real time. |
| Your package stops building | Run `python3 sim.py clean`, then `up` and `colcon build` again. |

Still stuck? Post the output of `python3 sim.py doctor` and
`python3 sim.py logs` when you ask for help.
