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
  echo 'Cannot connect to the selected X display. Check DISPLAY and Xauthority; see /tmp/sti/display-probe.log.'
  exit 1
fi
mode=cpu
if [[ "$STI_RENDER_MODE" != cpu ]]; then
  probe=(/opt/VirtualGL/bin/vglrun -d "$STI_EGL_DEVICE" -c proxy glxinfo -B)
  if [[ "${STI_DISPLAY:-browser}" == native ]]; then probe=(glxinfo -B); fi
  if timeout 15 "${probe[@]}" > /tmp/sti/gpu-probe.log 2>&1 \
      && ! grep -Eqi 'llvmpipe|softpipe|lavapipe|software rasterizer' /tmp/sti/gpu-probe.log \
      && grep -q 'OpenGL renderer string:' /tmp/sti/gpu-probe.log; then
    mode=gpu
  elif [[ "$STI_RENDER_MODE" == gpu ]]; then
    cat /tmp/sti/gpu-probe.log
    echo 'GPU required, but no accelerated GLX context was established.'
    exit 1
  else
    echo 'GPU context unavailable; selecting CPU rendering. See /tmp/sti/gpu-probe.log.'
  fi
fi
printf '%s\n' "$mode" > /tmp/sti/render-mode
/opt/sti/scripts/render.sh glxinfo -B > /tmp/sti/renderer.log
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
