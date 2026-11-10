#!/usr/bin/env bash
set -eo pipefail
source /opt/ros/humble/setup.bash
if [[ "${STI_SCENE:-hospital}" == hospital ]]; then
  /opt/sti/scripts/render.sh ros2 launch /opt/sti/scripts/hospital.launch.py > /tmp/sti/gazebo.log 2>&1 || true
  # ROS launch can return zero when an essential child requested shutdown.
  # A disappearing simulation is not a successful long-running desktop service.
  echo 'Hospital launch stopped unexpectedly; inspect /tmp/sti/gazebo.log.' >&2
  python3 /opt/sti/scripts/diagnose_render.py /tmp/sti/gazebo.log >&2 || true
  exit 1
elif [[ "$STI_SCENE" != demo ]]; then
  echo "Unknown STI_SCENE: $STI_SCENE (expected hospital or demo)"
  exit 2
fi
children=()
cleanup() {
  trap - EXIT TERM INT
  if ((${#children[@]})); then kill "${children[@]}" 2>/dev/null || true; fi
  wait || true
}
trap cleanup EXIT
trap 'exit 0' TERM INT
# Separate server/GUI, as in the hospital launch: a stuck GUI can't stop physics.
/opt/sti/scripts/render.sh ign gazebo -s -r /opt/sti/worlds/demo.sdf > /tmp/sti/gazebo.log 2>&1 &
children+=("$!")
/opt/sti/scripts/render.sh ign gazebo -g > /tmp/sti/gazebo-gui.log 2>&1 &
children+=("$!")
ros2 run ros_gz_bridge parameter_bridge --ros-args -p config_file:=/opt/sti/config/bridge.yaml > /tmp/sti/bridge.log 2>&1 &
children+=("$!")
ros2 run tf2_ros static_transform_publisher --x 0.23 --y 0 --z 0.18 --roll 0 --pitch 0 --yaw 0 --frame-id base_link --child-frame-id robot/base_link/lidar > /tmp/sti/tf.log 2>&1 &
children+=("$!")
/opt/sti/scripts/render.sh rviz2 -d /opt/sti/config/demo.rviz --ros-args -p use_sim_time:=true > /tmp/sti/rviz.log 2>&1 &
children+=("$!")
# Exit visibly if any essential application exits; don't silently loop a broken simulation.
set +e
wait -n "${children[@]}"
status=$?
echo "A demo process exited (status $status). Inspect /tmp/sti/*.log." >&2
python3 /opt/sti/scripts/diagnose_render.py /tmp/sti/gazebo.log /tmp/sti/gazebo-gui.log /tmp/sti/rviz.log >&2 || true
exit 1
