#!/usr/bin/env bash
# Open the Gazebo and RViz windows that are not open. The simulation does not
# depend on them: closing one leaves it running, and `sim.py up` runs this
# again to bring the window back.
#
# --verify: also fail, with the graphics cause if known, when a window does
#           not stay open.
set -eo pipefail
source /opt/ros/humble/setup.bash
export XDG_RUNTIME_DIR=/tmp/sti/runtime
scene=${STI_SCENE:-hospital}

# The Gazebo window loads the robot meshes itself.
resources=/opt/sti/worlds
IFS=: read -ra prefixes <<< "${AMENT_PREFIX_PATH:-}"
for prefix in "${prefixes[@]}"; do resources+=":$prefix/share"; done
export IGN_GAZEBO_RESOURCE_PATH="$resources${IGN_GAZEBO_RESOURCE_PATH:+:$IGN_GAZEBO_RESOURCE_PATH}"
export GZ_SIM_RESOURCE_PATH="$resources${GZ_SIM_RESOURCE_PATH:+:$GZ_SIM_RESOURCE_PATH}"

running() {
  local processes
  processes=$(ps -eo args=)
  grep -qF -- "$1" <<< "$processes"
}
gazebo_open() { running 'ign gazebo -g'; }
rviz_open() { running 'rviz2 -d'; }

opened=()
if ! gazebo_open; then
  /opt/sti/scripts/render.sh ign gazebo -g -v 3 < /dev/null > /tmp/sti/gazebo-gui.log 2>&1 &
  opened+=(Gazebo)
fi
if ! rviz_open; then
  /opt/sti/scripts/render.sh rviz2 -d "/opt/sti/config/$scene.rviz" \
    --ros-args -r __node:=rviz -p use_sim_time:=true < /dev/null > /tmp/sti/rviz.log 2>&1 &
  opened+=(RViz)
fi

[[ "${1:-}" == --verify ]] || exit 0
if ((${#opened[@]})); then
  # A window that cannot use the graphics driver exits within seconds.
  sleep 8
  if gazebo_open && rviz_open; then
    names=${opened[*]}
    echo "Opened ${names// / and }."
  fi
fi
if ! gazebo_open || ! rviz_open; then
  echo 'A window did not stay open. Inspect /tmp/sti/gazebo-gui.log and /tmp/sti/rviz.log.' >&2
  # demo.err is where the launcher looks for a graphics failure.
  python3 /opt/sti/scripts/diagnose_render.py /tmp/sti/gazebo-gui.log /tmp/sti/rviz.log \
    | tee -a /tmp/sti/demo.err >&2 || true
  exit 1
fi
