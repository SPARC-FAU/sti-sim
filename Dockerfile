# Windows 11/WSL2 GPU path. Mesa's d3d12 driver translates OpenGL to Direct3D 12.
# Gazebo Fortress uses ogre-next 2.2, which needs GL_ARB_copy_image; the WSL
# fallback that avoids it exists only in ogre-next 2.3+. d3d12 provides it from
# Mesa 24.0, but Jammy ships 23.2. Build a pinned release into its own prefix.
# Only the WSL GPU backend loads it; Linux GPU and CPU rendering keep Ubuntu's Mesa.
# It is only built for amd64: on arm64 every step is skipped and the prefix stays empty.
FROM ubuntu:jammy AS mesa-d3d12
ARG TARGETARCH
ARG MESA_VERSION=24.3.4
ARG DXHEADERS_VERSION=1.614.1
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive
RUN mkdir -p /opt/mesa-d3d12
RUN [[ "$TARGETARCH" == amd64 ]] || exit 0; \
    apt-get update && apt-get install -y --no-install-recommends \
    build-essential pkg-config ninja-build python3-pip python3-mako python3-packaging \
    python3-yaml bison flex curl ca-certificates xz-utils \
    libdrm-dev libexpat1-dev zlib1g-dev libzstd-dev libglvnd-dev \
    libx11-dev libx11-xcb-dev libxext-dev libxfixes-dev libxxf86vm-dev libxrandr-dev \
    libxshmfence-dev libxcb1-dev libxcb-glx0-dev libxcb-dri2-0-dev libxcb-dri3-dev \
    libxcb-present-dev libxcb-shm0-dev libxcb-sync-dev libxcb-xfixes0-dev libxcb-randr0-dev \
    && rm -rf /var/lib/apt/lists/* \
    && pip3 install --no-cache-dir meson==1.4.1
WORKDIR /src
# Mesa's checksum is published in its release notes; DirectX-Headers is pinned here.
RUN [[ "$TARGETARCH" == amd64 ]] || exit 0; \
    curl -fL --retry 3 "https://archive.mesa3d.org/mesa-${MESA_VERSION}.tar.xz" -o mesa.tar.xz \
    && echo "e641ae27191d387599219694560d221b7feaa91c900bcec46bf444218ed66025  mesa.tar.xz" | sha256sum -c - \
    && curl -fL --retry 3 "https://github.com/microsoft/DirectX-Headers/archive/refs/tags/v${DXHEADERS_VERSION}.tar.gz" -o dxheaders.tar.gz \
    && echo "344eb454c979ea68d8255d82c818bf7daf01f5109d26ac104f9911d18fae3b21  dxheaders.tar.gz" | sha256sum -c - \
    && tar xf mesa.tar.xz \
    && mkdir "mesa-${MESA_VERSION}/subprojects/DirectX-Headers-1.0" \
    && tar xf dxheaders.tar.gz --strip-components=1 -C "mesa-${MESA_VERSION}/subprojects/DirectX-Headers-1.0"
# softpipe is included so a missing/broken GPU reports a software renderer
# (and the probe falls back) instead of failing to create any context.
RUN [[ "$TARGETARCH" == amd64 ]] || exit 0; \
    cd "mesa-${MESA_VERSION}" \
    && meson setup build --prefix=/opt/mesa-d3d12 --libdir=lib --buildtype=release \
      --wrap-mode=nodownload -Dplatforms=x11 -Dgallium-drivers=d3d12,softpipe \
      -Dvulkan-drivers= -Dglx=dri -Degl=enabled -Dgbm=disabled -Dglvnd=enabled \
      -Dshared-glapi=enabled -Dgles1=disabled -Dgles2=enabled -Dllvm=disabled \
      -Dvideo-codecs= -Dgallium-va=disabled -Dgallium-vdpau=disabled -Dgallium-xa=disabled \
      -Dgallium-d3d12-video=disabled -Dmicrosoft-clc=disabled -Dvalgrind=disabled \
      -Dlibunwind=disabled -Dlmsensors=disabled -Dxlib-lease=disabled \
    && ninja -C build install \
    && rm -rf /src

FROM ros:humble-ros-base-jammy

# Built natively for the Docker engine's architecture: amd64 or arm64 (Apple Silicon).
ARG TARGETARCH
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    ros-humble-ros-gz ros-humble-rviz2 ros-humble-tf2-ros \
    ros-humble-rqt-image-view ros-humble-gz-ros2-control \
    ros-humble-teleop-twist-keyboard \
    xvfb x11vnc novnc websockify openbox xterm supervisor \
    mesa-utils libgl1-mesa-dri libegl1 libglu1-mesa x11-utils \
    curl ca-certificates python3-yaml git nano procps \
    && rm -rf /var/lib/apt/lists/*

# PAL's TIAGo Pro. On amd64 everything is a released package. On arm64 three
# packages are not released, only because they (indirectly) list Gazebo Classic
# plugins that this simulation (Gazebo Fortress) does not use. They hold no
# compiled code, so the same released versions are installed from PAL's tagged
# sources into the ROS prefix, next to the released arm64 packages. The apt list
# keeps navigation and MoveIt available on arm64 exactly as on amd64.
ARG OMNI_BASE_ROBOT_COMMIT=251ecc4cf57ed1870e84dcc135be917676e318f2
ARG TIAGO_PRO_ROBOT_COMMIT=59eff25559b0f071ac5cf6d3c4c72c2e33b8951a
ARG TIAGO_PRO_MOVEIT_CONFIG_COMMIT=fa1f97542f85c15c464d9685b93584316b8ceead
RUN apt-get update \
    && if [[ "$TARGETARCH" == amd64 ]]; then \
      apt-get install -y --no-install-recommends \
        ros-humble-tiago-pro-gazebo ros-humble-tiago-pro-description; \
    else \
      apt-get install -y --no-install-recommends \
        ros-humble-tiago-pro-bringup ros-humble-tiago-pro-controller-configuration \
        ros-humble-tiago-pro-head-description ros-humble-pal-sea-arm-description \
        ros-humble-pal-pro-gripper-description ros-humble-pal-urdf-utils \
        ros-humble-omni-base-controller-configuration ros-humble-realsense2-description \
        ros-humble-launch-pal ros-humble-launch-param-builder \
        ros-humble-robot-state-publisher ros-humble-xacro \
        ros-humble-tiago-pro-2dnav ros-humble-tiago-pro-laser-sensors \
        ros-humble-tiago-pro-rgbd-sensors ros-humble-omni-base-2dnav \
        ros-humble-pal-sea-arm-moveit-config ros-humble-moveit-planners-chomp \
        ros-humble-moveit-ros-perception ros-humble-moveit-ros-control-interface \
        ros-humble-moveit-task-constructor-capabilities \
        ros-humble-navigation2 ros-humble-nav2-bringup ros-humble-nav2-route \
        ros-humble-slam-toolbox ros-humble-smclib ros-humble-camera-info-manager \
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
      && rm -rf /tmp/pal; \
    fi \
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
COPY --from=mesa-d3d12 /opt/mesa-d3d12 /opt/mesa-d3d12

RUN mkdir -p /opt/sti
COPY config /opt/sti/config
COPY worlds /opt/sti/worlds
COPY scripts /opt/sti/scripts
RUN chmod +x /opt/sti/scripts/*.sh \
    && python3 /opt/sti/scripts/prepare_hospital.py \
      /opt/sti/worlds/2026_09_01_RoboCup_HCC_Arena_V1.glb /opt/sti/worlds/hospital \
    && source /opt/ros/humble/setup.bash \
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
    ROS_LOCALHOST_ONLY=1 ROS_DOMAIN_ID=42 \
    STI_RENDER_MODE=auto STI_EGL_DEVICE=egl0 STI_SCENE=hospital \
    DESKTOP_SIZE=1280x800 NVIDIA_DRIVER_CAPABILITIES=graphics,utility,display
USER student
WORKDIR /workspace
EXPOSE 6080
HEALTHCHECK --start-period=90s --interval=15s --timeout=5s --retries=3 \
    CMD python3 /opt/sti/scripts/health.py
ENTRYPOINT ["/opt/sti/scripts/entrypoint.sh"]
