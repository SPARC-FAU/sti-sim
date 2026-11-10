# STI Sim

TIAGo Pro in the RoboCup hospital arena, with ROS 2 Humble, Gazebo and RViz.
It runs in Docker and you use it in your browser.

## 1. Start the simulation

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/)
(Windows, macOS) or Docker Engine (Linux), and Python 3.10 or newer.

Start Docker, open a terminal in this folder and run:

```sh
python3 sim.py up
```

On Windows, type `py -3` instead of `python3`.

The first start takes 10 to 20 minutes. When it prints `PASS`, open:

<http://localhost:6080/vnc.html?autoconnect=true&resize=scale>

You should see Gazebo with the robot in the hospital, and RViz with its camera
and lidar.

## 2. Drive the robot

Open a terminal inside the simulation:

```sh
python3 sim.py shell
```

and drive with the keyboard:

```sh
ros2 run teleop_twist_keyboard teleop_twist_keyboard --ros-args \
  -r cmd_vel:=/mobile_base_controller/cmd_vel_unstamped
```

The robot keeps moving until you send a stop (`k`).

## 3. Write your own code

The first start creates a `workspace/` folder next to `sim.py`. It is a normal
folder on your computer, so use any editor. Inside the simulation it is
`/workspace`.

Put your ROS 2 packages in `workspace/src/`, then build and run them in a
simulation terminal (`python3 sim.py shell`):

```sh
cd /workspace
colcon build --symlink-install
source /colcon/install/setup.bash
ros2 run <your_package> <your_node> --ros-args -p use_sim_time:=true
```

Topics to start with (`ros2 topic list` shows all of them):

| Topic | Content |
| --- | --- |
| `/mobile_base_controller/cmd_vel_unstamped` | Drive the base (`geometry_msgs/Twist`) |
| `/mobile_base_controller/odom` | Odometry |
| `/scan_front_raw`, `/scan_rear_raw` | Lidars |
| `/head_front_camera/color/image_raw` | Head camera |
| `/joint_states`, `/tf` | Joints and transforms |

**Your code is safe.** No `sim.py` command changes or deletes `workspace/`.
To move to another computer or a new version of the simulation, copy the
`workspace/` folder into the new simulation folder and run `colcon build` again.

## Commands

| Command | What it does |
| --- | --- |
| `python3 sim.py up` | Start |
| `python3 sim.py down` | Stop |
| `python3 sim.py shell` | Terminal inside the simulation |
| `python3 sim.py check` | Test that the robot's sensors deliver data |
| `python3 sim.py clean` | Stop and delete build output (not your code) |

## If something goes wrong

| Problem | Try this |
| --- | --- |
| `Docker is not running` | Start Docker Desktop, wait until it is ready, run `up` again. |
| `permission denied` (Linux) | Run `sudo usermod -aG docker "$USER"`, log out and back in. Do not use `sudo` with `sim.py`. |
| Startup ends with `FAIL` | Run `python3 sim.py down`, then `python3 sim.py up` again. |
| Still `FAIL`, or a black or broken picture | Run `python3 sim.py down`, then `python3 sim.py up --mode cpu`. |
| The browser page does not load | Check that `up` printed `PASS`. If port 6080 is in use, set `STI_PORT` to another port before `up`. |
| Slow or stuttering | In Docker Desktop, *Settings → Resources*: at least 4 CPUs and 8 GB memory. On macOS it always runs slower than real time. |
| Your package stops building | Run `python3 sim.py clean`, then `up` and `colcon build` again. |

Still stuck? Post the output of `python3 sim.py doctor` and
`python3 sim.py logs` when you ask for help.
