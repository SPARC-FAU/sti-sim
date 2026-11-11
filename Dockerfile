# The image is built natively for the Docker engine's architecture. Emulating
# amd64 on Apple Silicon is too slow for the robot's controllers to start.
# Only the packages stage matching that architecture is built.
FROM ros:humble-ros-base-jammy AS base
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive
COPY docker/apt-packages.txt /tmp/sti/

# amd64 (Linux, Windows, Intel Macs): every package is released.
FROM base AS packages-amd64
RUN apt-get update \
    && sed 's/\r$//; /^[[:space:]]*#/d; /^[[:space:]]*$/d' /tmp/sti/apt-packages.txt \
      | xargs -r apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* /tmp/sti

# arm64 (Apple Silicon): three PAL packages are not released, only because they
# (indirectly) list Gazebo Classic plugins that this simulation (Gazebo Fortress)
# does not use. They hold no compiled code, so the same released versions are
# installed from PAL's tagged sources into the ROS prefix; arm64-packages.txt
# lists what they need at run time.
FROM base AS packages-arm64
COPY docker/arm64-packages.txt /tmp/sti/
ARG ARM64_SOURCE_PACKAGES="ros-humble-tiago-pro-gazebo ros-humble-tiago-pro-description ros-humble-tiago-pro-moveit-config"
ARG OMNI_BASE_ROBOT_COMMIT=251ecc4cf57ed1870e84dcc135be917676e318f2
ARG TIAGO_PRO_ROBOT_COMMIT=59eff25559b0f071ac5cf6d3c4c72c2e33b8951a
ARG TIAGO_PRO_MOVEIT_CONFIG_COMMIT=fa1f97542f85c15c464d9685b93584316b8ceead
RUN apt-get update \
    && packages() { sed 's/\r$//; /^[[:space:]]*#/d; /^[[:space:]]*$/d' "$@"; } \
    && { packages /tmp/sti/apt-packages.txt | grep -vxF -f <(tr ' ' '\n' <<< "$ARM64_SOURCE_PACKAGES"); \
         packages /tmp/sti/arm64-packages.txt; } | xargs -r apt-get install -y --no-install-recommends \
    && source /opt/ros/humble/setup.bash \
    && fetch() { git init -q "/tmp/pal/$1" && git -C "/tmp/pal/$1" fetch -q --depth 1 \
         "https://github.com/pal-robotics/$1" "$2" && git -C "/tmp/pal/$1" checkout -q FETCH_HEAD; } \
    && fetch omni_base_robot "$OMNI_BASE_ROBOT_COMMIT" \
    && fetch tiago_pro_robot "$TIAGO_PRO_ROBOT_COMMIT" \
    && fetch tiago_pro_moveit_config "$TIAGO_PRO_MOVEIT_CONFIG_COMMIT" \
    && for package in omni_base_robot/omni_base_description tiago_pro_robot/tiago_pro_description \
         tiago_pro_moveit_config; do \
         cmake -S "/tmp/pal/$package" -B /tmp/pal/build -DCMAKE_INSTALL_PREFIX=/opt/ros/humble -DBUILD_TESTING=OFF \
         && cmake --build /tmp/pal/build --target install && rm -rf /tmp/pal/build || exit 1; \
       done \
    && rm -rf /tmp/pal /var/lib/apt/lists/* /tmp/sti

FROM packages-${TARGETARCH}
ARG TARGETARCH

# Ubuntu 22.04's Mesa 23.2 exposes only OpenGL 4.2 through WSL's Direct3D 12
# driver, and Gazebo's ogre2 renderer aborts without OpenGL 4.3/GL_ARB_copy_image.
# Kisak's stable Mesa PPA ("turtle"; the faster kisak-mesa PPA dropped 22.04)
# backports Mesa 25. It serves every graphics path in the image (software,
# Intel/AMD, WSL), so all machines share one stack. The PPA keeps only its latest
# release; the version check catches it disappearing or regressing.
RUN apt-get update \
    && apt-get install -y --no-install-recommends software-properties-common gpg-agent \
    && add-apt-repository -y ppa:kisak/turtle \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
      libgl1-mesa-dri libglx-mesa0 libegl-mesa0 libgbm1 \
    && mesa="$(dpkg-query -W -f='${Version}' libgl1-mesa-dri)" \
    && echo "Mesa $mesa" \
    && dpkg --compare-versions "$mesa" ge 25 \
    && rm -rf /var/lib/apt/lists/*

# Upstream release asset, pinned and checked independently of apt packages.
RUN case "$TARGETARCH" in \
      amd64) checksum=df3f7788ce41b182a47c0d298e5cd6d2d63579522cb41825970b7726e825485e ;; \
      arm64) checksum=9ac238e8a18c06d84ef65444a591a7a5bb4c4ce9e8cfdd92f8a2aea35edbb5d7 ;; \
      *) echo "Unsupported architecture: $TARGETARCH"; exit 1 ;; \
    esac \
    && curl -fL --retry 3 \
    "https://github.com/VirtualGL/virtualgl/releases/download/3.1.5/virtualgl_3.1.5_${TARGETARCH}.deb" \
    -o /tmp/virtualgl.deb \
    && echo "$checksum  /tmp/virtualgl.deb" | sha256sum -c - \
    && apt-get update && apt-get install -y --no-install-recommends /tmp/virtualgl.deb \
    && rm -rf /tmp/virtualgl.deb /var/lib/apt/lists/*

RUN mkdir -p /opt/sti
COPY config /opt/sti/config
COPY worlds /opt/sti/worlds
COPY scripts /opt/sti/scripts
RUN chmod +x /opt/sti/scripts/*.sh \
    && python3 /opt/sti/scripts/prepare_hospital.py \
      /opt/sti/worlds/2026_09_01_RoboCup_HCC_Arena_V1.glb /opt/sti/worlds/hospital \
    && source /opt/ros/humble/setup.bash \
    && python3 /opt/sti/scripts/check_dependencies.py \
    && ros2 launch /opt/sti/scripts/hospital.launch.py --show-args \
    && python3 /opt/sti/scripts/validate_world.py
# The student account matches the host user on Linux (see compose.yaml), so the
# bind-mounted workspace/ is writable from both sides. Kept after the hospital
# preparation so a different UID only rebuilds this layer.
ARG STI_UID=1000
ARG STI_GID=1000
RUN getent group "$STI_GID" >/dev/null || groupadd --gid "$STI_GID" student \
    && useradd --create-home --non-unique --uid "$STI_UID" --gid "$STI_GID" --shell /bin/bash student \
    && mkdir -p /workspace /colcon /tmp/sti \
    && chown student:"$STI_GID" /workspace /colcon /tmp/sti \
    && echo 'source /opt/ros/humble/setup.bash' >> /home/student/.bashrc \
    && echo 'if [[ -f /colcon/install/setup.bash ]]; then source /colcon/install/setup.bash; fi' >> /home/student/.bashrc
# Build output goes to the /colcon volume, never into the student's workspace/.
ENV COLCON_DEFAULTS_FILE=/opt/sti/config/colcon.yaml COLCON_LOG_PATH=/colcon/log
ENV DISPLAY=:1 QT_X11_NO_MITSHM=1 QT_LOGGING_RULES="qt.qpa.*=false" \
    ROS_LOCALHOST_ONLY=1 ROS_DOMAIN_ID=42 RMW_IMPLEMENTATION=rmw_cyclonedds_cpp \
    CYCLONEDDS_URI=file:///opt/sti/config/cyclonedds.xml \
    STI_RENDER_MODE=auto STI_EGL_DEVICE=egl0 STI_SCENE=hospital \
    DESKTOP_SIZE=1280x800 NVIDIA_DRIVER_CAPABILITIES=graphics,utility,display
USER student
WORKDIR /workspace
EXPOSE 6080
HEALTHCHECK --start-period=90s --interval=15s --timeout=5s --retries=3 \
    CMD python3 /opt/sti/scripts/health.py
ENTRYPOINT ["/opt/sti/scripts/entrypoint.sh"]
