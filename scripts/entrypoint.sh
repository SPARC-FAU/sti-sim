#!/usr/bin/env bash
set -euo pipefail
mkdir -p /tmp/sti
export XDG_RUNTIME_DIR=/tmp/sti/runtime
mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"
[[ "$DESKTOP_SIZE" =~ ^[0-9]{3,4}x[0-9]{3,4}$ ]] || { echo 'Invalid DESKTOP_SIZE'; exit 2; }
[[ "$STI_RENDER_MODE" =~ ^(auto|gpu|cpu)$ ]] || { echo 'Invalid STI_RENDER_MODE'; exit 2; }
[[ "${STI_DISPLAY:-browser}" =~ ^(browser|native)$ ]] || { echo 'Invalid STI_DISPLAY'; exit 2; }
xvfb_pid=''
cleanup() { if [[ -n "$xvfb_pid" ]]; then kill "$xvfb_pid" 2>/dev/null || true; fi; }
trap cleanup EXIT
if [[ "${STI_DISPLAY:-browser}" == browser ]]; then
  Xvfb :1 -screen 0 "${DESKTOP_SIZE}x24" -ac -nolisten tcp +extension GLX +render -noreset > /tmp/sti/xvfb.log 2>&1 &
  xvfb_pid=$!
  for attempt in {1..50}; do
    xdpyinfo >/dev/null 2>&1 && break
    kill -0 "$xvfb_pid" || { cat /tmp/sti/xvfb.log; exit 1; }
    sleep 0.1
  done
fi
if ! timeout 5 xdpyinfo > /tmp/sti/display-probe.log 2>&1; then
  cat /tmp/sti/display-probe.log
  echo 'Cannot connect to the selected X display. Check DISPLAY and Xauthority; see /tmp/sti/display-probe.log.'
  exit 1
fi
# Preserve the ROS library search path while adding Microsoft's WSL libraries.
if [[ "${STI_WSL_GPU:-0}" == 1 ]]; then
  export LD_LIBRARY_PATH="/usr/lib/wsl/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
mode=cpu
if [[ "$STI_RENDER_MODE" != cpu ]]; then
  # Full glxinfo output: the capability check needs the extension list.
  probe=(/opt/VirtualGL/bin/vglrun -d "$STI_EGL_DEVICE" -c proxy glxinfo)
  if [[ "${STI_DISPLAY:-browser}" == native ]]; then probe=(glxinfo); fi
  if [[ "${STI_WSL_GPU:-0}" == 1 ]]; then probe=(env GALLIUM_DRIVER=d3d12 glxinfo); fi
  # A hardware renderer must also support what Gazebo needs; an accelerated
  # driver that later aborts Gazebo is worse than CPU rendering.
  if timeout 15 "${probe[@]}" > /tmp/sti/gpu-probe.log 2>&1 \
      && python3 /opt/sti/scripts/gl_requirements.py --hardware /tmp/sti/gpu-probe.log > /tmp/sti/gpu-check.log 2>&1; then
    mode=gpu
  else
    [[ -s /tmp/sti/gpu-check.log ]] || echo 'no accelerated GLX context was established' > /tmp/sti/gpu-check.log
    if [[ "$STI_RENDER_MODE" == gpu ]]; then
      echo "GPU required, but it cannot run Gazebo: $(cat /tmp/sti/gpu-check.log). See /tmp/sti/gpu-probe.log."
      exit 1
    fi
    echo "GPU not usable ($(cat /tmp/sti/gpu-check.log)); selecting CPU rendering."
  fi
fi
printf '%s\n' "$mode" > /tmp/sti/render-mode
if ! timeout 20 /opt/sti/scripts/render.sh glxinfo -B > /tmp/sti/renderer.log 2>&1 \
    || ! timeout 20 /opt/sti/scripts/render.sh glxinfo > /tmp/sti/renderer-full.log 2>&1; then
  cat /tmp/sti/renderer.log
  echo "Renderer verification failed or timed out (selected mode: $mode)."
  exit 1
fi
if ! python3 /opt/sti/scripts/gl_requirements.py /tmp/sti/renderer-full.log > /tmp/sti/renderer-check.log 2>&1; then
  echo "Selected renderer cannot run Gazebo: $(cat /tmp/sti/renderer-check.log)"
  exit 1
fi
echo "STI rendering: $mode"
cat /tmp/sti/renderer.log
config=/opt/sti/config/supervisord.conf
if [[ "${STI_DISPLAY:-browser}" == native ]]; then config=/opt/sti/config/supervisord.native.conf; fi
supervisord -c "$config" &
supervisor_pid=$!
trap 'kill -TERM "$supervisor_pid" 2>/dev/null || true' TERM INT
set +e
wait "$supervisor_pid"
status=$?
if kill -0 "$supervisor_pid" 2>/dev/null; then wait "$supervisor_pid"; status=$?; fi
exit "$status"
