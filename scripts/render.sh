#!/usr/bin/env bash
set -euo pipefail
if [[ "$(cat /tmp/sti/render-mode)" == gpu ]]; then
  unset LIBGL_ALWAYS_SOFTWARE
  if [[ "${STI_GPU_BACKEND:-}" == wsl ]]; then
    # Pinned Mesa d3d12 renders on the Windows GPU and presents to the X display.
    export LD_LIBRARY_PATH="/opt/mesa-d3d12/lib:/usr/lib/wsl/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    exec "$@"
  fi
  if [[ "${STI_DISPLAY:-browser}" == native ]]; then
    exec "$@"
  fi
  exec /opt/VirtualGL/bin/vglrun -d "${STI_EGL_DEVICE:-egl0}" -c proxy "$@"
else
  export LIBGL_ALWAYS_SOFTWARE=1
  exec "$@"
fi
