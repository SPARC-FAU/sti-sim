#!/usr/bin/env bash
set -euo pipefail
if [[ "$(cat /tmp/sti/render-mode)" == gpu ]]; then
  unset LIBGL_ALWAYS_SOFTWARE
  if [[ "${STI_WSL_GPU:-0}" == 1 ]]; then export GALLIUM_DRIVER=d3d12; fi
  if [[ "${STI_DISPLAY:-browser}" == native ]]; then
    exec "$@"
  fi
  exec /opt/VirtualGL/bin/vglrun -d "${STI_EGL_DEVICE:-egl0}" -c proxy "$@"
else
  unset GALLIUM_DRIVER MESA_LOADER_DRIVER_OVERRIDE
  export LIBGL_ALWAYS_SOFTWARE=1
  exec "$@"
fi
