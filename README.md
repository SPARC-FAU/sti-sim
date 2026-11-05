# STI Sim

A ROS 2 Humble / Gazebo Fortress environment with a browser desktop, the
supplied RoboCup hospital arena, PAL's TIAGo Pro robot and RViz. The default
scene spawns TIAGo Pro in the central corridor and displays its robot model,
front/rear lidar and head camera. A small differential-drive demo remains
available for graphics troubleshooting. No host X server or VNC client is required.

## Status

Initial amd64 integration. The supplied hospital asset has been converted and its
scale, orientation and spawn clearance checked against the geometry. Configuration,
conversion and launcher tests can run without Docker. An image build and real
GUI/GPU acceptance runs are still required before distributing this as the tested
course environment. Docker daemon access was unavailable in the implementation
session, so PAL package installation and runtime compatibility are not yet verified.
PAL controllers are launched, but autonomous navigation and MoveIt are not started.

| Host | First-release graphics path |
| --- | --- |
| Native Linux, Intel/AMD | Try VirtualGL/EGL using `/dev/dri`; CPU fallback |
| Native Linux, NVIDIA | Try NVIDIA Container Toolkit + VirtualGL/EGL; CPU fallback |
| Windows with Docker Desktop/WSL2 | Browser desktop with CPU rendering |
| Intel macOS with Docker Desktop | Browser desktop with CPU rendering |
| Apple Silicon | amd64 emulation plus CPU rendering; potentially very slow |

Windows GPU integration is deliberately not selected automatically: WSLg GPU
access and acceleration in a browser desktop are different paths and require
their own validation. No claim of hardware coverage or real-time performance has
been established yet.

## Start

Install Docker Engine and its Compose plugin on Linux, or Docker Desktop with
Linux containers on Windows/macOS. Use Compose v2.30 or later and Python 3.10+.
Allocate roughly 4 CPUs and 6–8 GB RAM to Docker as a starting point; measure the
actual workload before choosing course requirements.

From this repository:

```sh
python3 sim.py up
```

On Windows, use `py -3 sim.py up` instead. Open:

<http://localhost:6080/vnc.html?autoconnect=true&resize=scale>

Startup builds the image, waits for the desktop and application processes, and
checks live clock, head camera, both lidars, odometry, arm joint states and sensor
TF. It prints the selected OpenGL renderer. Gazebo starts unpaused. The hospital
mesh and robot can take longer to initialise than the small demo.
Use the window manager's Alt+mouse drag or task switching if windows overlap.

```sh
python3 sim.py up --mode cpu     # Portable rendering, no GPU device mappings
python3 sim.py up --mode gpu     # Fail visibly rather than accept CPU fallback
python3 sim.py up --scene demo   # Small original robot/world for troubleshooting
python3 sim.py check             # Check live data again, without moving the robot
python3 sim.py check --motion    # Also command a short forward movement
python3 sim.py shell             # ROS-enabled terminal
python3 sim.py doctor            # Docker access, selected backend and Compose config
python3 sim.py logs
python3 sim.py down              # Stops the container; preserves workspace volume
```

Linux NVIDIA hosts require the NVIDIA Container Toolkit configured for Docker.
Native Linux windows are also supported:

```sh
python3 sim.py up --display native
python3 sim.py up --display native --mode gpu  # Require hardware acceleration
```

ROS stays inside Docker; Gazebo and RViz open on the host desktop through X11 or
Xwayland. Run from a desktop terminal with `DISPLAY`, `XAUTHORITY` and the host
`xauth` utility available. On the Radeon 680M machine, automatic selection should
use Mesa and `/dev/dri` when those devices are accessible to Docker. Startup
prints the actual renderer; `auto` retains CPU fallback, while `--mode gpu` fails
if acceleration cannot be established.

The launcher exports the active display's authentication records to an ignored
`.sti-native/` directory, readable by your primary group. The cookie directory and
X socket are mounted read-only. No `xhost +` or privileged container is needed.
Native mode bypasses Xvfb, VNC and VirtualGL. Windows/macOS/WSL native windows are
not covered by this option. Configuration and launcher tests pass; a live native
desktop acceptance run is still required.

Use `python3 sim.py up --display browser` to return to the browser desktop at
<http://localhost:6080/vnc.html?autoconnect=true&resize=scale>. These are two modes
of the same container: switching recreates it and resets the simulation, while
preserving `/workspace`. The browser URL is only active in browser mode. `check`,
`logs`, `shell` and `down` remember the selected display mode.

The launcher prefers NVIDIA when `/dev/nvidiactl` exists; use
`python3 sim.py up --backend dri` to choose Intel/AMD rendering instead.
Set `STI_EGL_DEVICE` to choose another VirtualGL EGL device (default `egl0`).
The DRI configuration grants the container user the primary render/card device
groups; hosts with multiple GPUs and different group ownership may need an
explicit configuration.

If Python is unavailable, the CPU path can be started directly:

```sh
docker compose up --build -d --wait
docker compose exec sim bash
```

Then run `python3 /opt/sti/scripts/check.py` in that ROS-enabled shell.
Direct Compose commands do not run the launcher's sensor acceptance check.

## Hospital and TIAGo Pro

The original `worlds/2026_09_01_RoboCup_HCC_Arena_V1.glb` is retained unchanged.
During the image build, `scripts/prepare_hospital.py` converts it into a static
OBJ/MTL scene and a separate collision mesh. All geometry is transformed from
glTF Y-up to Gazebo Z-up in metres, with the floor centred at x=y=0 and its top at
z=0. The source floor is 16 × 13 m; interior walls occupy a smaller footprint.
No manual unit-scale multiplier is used.

`worlds/hospital.sdf` adds simulation systems, lighting, one collision floor and
the arena visual/collision meshes. The ceiling and top wall caps are omitted so
the interior can be viewed from above. Walls, doors and equipment retain their
authored positions and are **static**. Opening doors, movable objects, cleaning
states and the competition Hospital Controller are not implemented. Materials
preserve base colours and supported base-colour textures; PBR effects are
approximated. This is an initial physical environment, not a validated competition
benchmark.

To regenerate host-side meshes for inspection (no Python dependencies needed):

```sh
python3 scripts/prepare_hospital.py \
  worlds/2026_09_01_RoboCup_HCC_Arena_V1.glb worlds/hospital
```

Generated `worlds/hospital/` files are ignored by Git and Docker build context;
the image regenerates them from the original asset. `conversion.json` records
source SHA-256, origin, bounds and triangle count. The converter supports this
static GLB's triangle meshes, embedded textures and hierarchy; unsupported glTF
features fail rather than silently disappear.

Both exported meshes include explicit unit normals at every face corner, as
required by Fortress's DART mesh loader. Degenerate triangles are removed and
counts are recorded in `conversion.json`. The image build runs a server-only
five-step physics smoke test on the actual hospital to catch mesh-loader failures
before reporting a successful build; this doesn't validate robot spawning or GUI
rendering.

The Dockerfile installs PAL's released Humble simulation/description packages.
`scripts/hospital.launch.py` uses PAL's robot bring-up with
`is_public_sim=True`, `gazebo_version=gazebo`, and simulation time. It waits for
`/world/hospital/create` before spawning the robot using ROS/Gazebo's documented
`-name` argument. Spawn failures shut down the launch rather than leaving an empty
world looking successful. The image build also loads the launch description with
`--show-args` to catch missing packages/imports early.

Spawn coordinates are in `config/hospital.yaml`: x=1, y=0, z=0.05, facing +Y.
The point was checked against horizontal mesh sections for wall/equipment
clearance; full robot self-collision and stability still need runtime validation.
Edit this file and rebuild to change the initial pose.

The default `initial_pose: home` starts TIAGo Pro with both arms folded inward.
Before spawning, the launcher reads the final waypoint of PAL's installed
`tiago_pro_motions_general_spherical-wrist.yaml` home motion, validates its 14 arm
joints and torso value against the installed URDF limits, and seeds their
`ros2_control` position `initial_value` parameters. It updates the robot state
publisher and waits for the updated description to be republished before creating
the robot. This avoids an initial stretched-arm motion and doesn't need MoveIt.
Applied joint values are logged to `/tmp/sti/initial-pose.json`. Set
`initial_pose: null` and rebuild to restore PAL's unseeded joint defaults.
This is simulation initialisation only; the installed PAL packages remain unchanged.

RViz uses `base_footprint` as its fixed frame, so it does not require a navigation
map or a separate localisation node. The head depth cloud is available but
disabled initially to limit rendering load. Key topics are:

| Topic | Source |
| --- | --- |
| `/robot_description`, `/joint_states`, `/tf`, `/tf_static` | PAL bring-up/controllers |
| `/scan_front_raw`, `/scan_rear_raw` | Gazebo lidar bridge |
| `/head_front_camera/color/image_raw` | Gazebo RGB camera bridge |
| `/head_front_camera/depth/color/points` | Gazebo depth point-cloud bridge |
| `/mobile_base_controller/odom` | PAL base controller |

For keyboard driving, inside the container use:

```sh
ros2 run teleop_twist_keyboard teleop_twist_keyboard --ros-args \
  -r cmd_vel:=/mobile_base_controller/cmd_vel_unstamped
```

The motion check uses this same controller input directly, bypassing the optional
navigation/teleop multiplexer. Check the area ahead is clear before moving.

## Rendering and fallback

Applications draw into an Xvfb desktop. VirtualGL redirects their OpenGL work to
an EGL GPU and copies rendered frames into that desktop. x11vnc and noVNC deliver
the desktop to the browser. CPU mode uses Mesa software OpenGL instead.

`auto` first chooses host device mappings, then probes the real GLX context in
the desktop. An unavailable/software GPU context falls back to CPU. If a mapped
GPU prevents the container from becoming healthy, the launcher prints logs and
recreates it once without GPU mappings. `--mode gpu` never silently falls back.

The live sensor check is a separate gate: missing images, lidar, clock or TF fail
startup with an error. It does not treat an arbitrary ROS/world error as proof
that GPU rendering failed. A failed sensor check leaves the container available
for diagnosis. To retry deliberately, run `python3 sim.py up --mode cpu`.
No automatic renderer changes occur during a running student session.

The container's Docker health check tests desktop and process liveness, not sensor
validity. `sim.py check` is the data-path acceptance check. Visual correctness of
Gazebo and RViz still needs manual inspection.

## Files and troubleshooting

Student files in `/workspace` persist in a named Docker volume. For example,
`docker compose cp sim:/workspace ./workspace-backup` copies them to the host.
Image files under `/opt/sti` are replaced when the container is recreated.
Do not use `docker compose down -v` unless you intend to delete the workspace.

Inside the container, logs are in `/tmp/sti`:

| File | Purpose |
| --- | --- |
| `render-mode`, `renderer.log` | Selected mode and OpenGL identity |
| `gpu-probe.log` | GPU probe, including failure reason |
| `gazebo.log` | Hospital: combined Gazebo, PAL, spawn, bridge and RViz output |
| `bridge.log`, `rviz.log` | Separate bridge/RViz logs for the small demo |
| `demo.err`, `supervisor.log` | Application lifecycle |

Read application logs with `docker compose exec sim cat /tmp/sti/gazebo.log`.
To inspect supervisor state:

```sh
docker compose exec sim supervisorctl -c /opt/sti/config/supervisord.conf status
```

### Docker permissions

Run `docker info` in the same terminal first. If it reports permission denied on
the Docker socket, this is host Docker access, not a need to run Python as root.
For a standard Linux Docker Engine installation, configure access once:

```sh
sudo usermod -aG docker "$USER"
newgrp docker
docker info
python3 sim.py up
```

The `docker` group normally already exists after Docker Engine installation.
Alternatively, fully log out and back in instead of using `newgrp`; opening a
new terminal in the same desktop login may not refresh group membership.
Docker group access grants root-equivalent control of the daemon. Rootless Docker
is an alternative if that is unsuitable; see the
[official post-install instructions](https://docs.docker.com/engine/install/linux-postinstall/).
Docker Desktop uses its own context; do not change the system socket permissions
to fix a context issue.

The launcher never runs sudo or changes host permissions. Its state file is
written with an atomic replacement, so an old sudo-owned `.sti-state.json` does
not require privileged Python when the repository directory is user-writable.
If the repository directory itself was created as root, correct ownership of that
specific directory before starting. Student volumes are unaffected.

The default desktop is 1280×800. Set `DESKTOP_SIZE` and `STI_PORT` as environment
variables before starting to change resolution or host port. Stop the existing
instance before changing ports. Closing a demo window stops the demo process;
use `python3 sim.py down` followed by `python3 sim.py up` to start fresh.

The browser port binds to **127.0.0.1 only**. There is no VNC password because
this is a local development desktop. Do not expose it on a public interface.
The container runs as a non-root user, without privileged mode or host networking.
All ROS nodes run inside it with localhost-only discovery. Remote team ROS
networking is outside this first version.

## Validation

```sh
python3 -B -m unittest discover -s tests -v
docker compose config --quiet
python3 sim.py up --mode cpu
python3 sim.py check --motion
python3 sim.py down
python3 sim.py up --mode gpu
python3 sim.py check --motion
```

Run the accelerated acceptance test on representative Linux Intel/AMD and NVIDIA
hosts. Run CPU tests on Windows/WSL2 and macOS. In the browser, verify both Gazebo
and RViz render, laser returns match the obstacle, and the camera image changes
when the robot moves. Record host/GPU, renderer, image ID, simulation real-time
factor and sensor rates. A slow desktop stream and slow simulation are distinct
problems. Software mode may not sustain real time.

ROS package versions and the upstream base tag follow their repositories; this
Dockerfile is not an immutable dependency lock. After a successful acceptance run,
record/publish the built image digest for the cohort rather than having students
rebuild against moving apt repositories.

## References

- [ROS/Gazebo pairing](https://gazebosim.org/docs/fortress/ros_installation/)
- [Fortress sensors](https://gazebosim.org/docs/fortress/sensors/)
- [Fortress differential drive](https://gazebosim.org/docs/fortress/moving_robot/)
- [VirtualGL](https://github.com/VirtualGL/virtualgl/releases/tag/3.1.5)
- [noVNC](https://novnc.com/)
- [WSLg container graphics](https://github.com/microsoft/wslg/blob/main/samples/container/Containers.md)
- [PAL TIAGo Pro description](https://github.com/pal-robotics/tiago_pro_robot/tree/humble-devel/tiago_pro_description)
- [PAL TIAGo Pro simulation](https://github.com/pal-robotics/tiago_pro_simulation/tree/humble-devel/tiago_pro_gazebo)

PAL sources and ROS package metadata were inspected on 2026-11-05. The supplied
hospital asset remains governed by its original distribution terms.
