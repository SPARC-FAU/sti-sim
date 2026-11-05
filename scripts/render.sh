#!/usr/bin/env bash
set -euo pipefail
if [[ "$(cat /tmp/sti/render-mode)" == gpu ]]; then
  unset LIBGL_ALWAYS_SOFTWARE
  if [[ "${STI_DISPLAY:-browser}" == native ]]; then
    exec "$@"
  fi
  exec /opt/VirtualGL/bin/vglrun -d "${STI_EGL_DEVICE:-egl0}" -c proxy "$@"
else
  export LIBGL_ALWAYS_SOFTWARE=1
  exec "$@"
fi
