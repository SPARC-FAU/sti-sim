FROM ros:humble-ros-base-jammy

ARG TARGETARCH
RUN test "${TARGETARCH:-amd64}" = amd64
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    ros-humble-ros-gz ros-humble-rviz2 ros-humble-tf2-ros \
    ros-humble-rqt-image-view ros-humble-tiago-pro-gazebo \
    ros-humble-tiago-pro-description ros-humble-gz-ros2-control \
    ros-humble-teleop-twist-keyboard \
    xvfb x11vnc novnc websockify openbox xterm supervisor \
    mesa-utils libgl1-mesa-dri libegl1 libglu1-mesa x11-utils \
    curl ca-certificates python3-yaml git nano procps \
    && rm -rf /var/lib/apt/lists/*

# Upstream release asset, pinned and checked independently of apt packages.
RUN curl -fL --retry 3 \
    https://github.com/VirtualGL/virtualgl/releases/download/3.1.5/virtualgl_3.1.5_amd64.deb \
    -o /tmp/virtualgl.deb \
    && echo 'df3f7788ce41b182a47c0d298e5cd6d2d63579522cb41825970b7726e825485e  /tmp/virtualgl.deb' | sha256sum -c - \
    && apt-get update && apt-get install -y --no-install-recommends /tmp/virtualgl.deb \
    && rm -rf /tmp/virtualgl.deb /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 --shell /bin/bash student \
    && mkdir -p /workspace /opt/sti /tmp/sti \
    && chown student:student /workspace /tmp/sti \
    && echo 'source /opt/ros/humble/setup.bash' >> /home/student/.bashrc
COPY config /opt/sti/config
COPY worlds /opt/sti/worlds
COPY scripts /opt/sti/scripts
RUN chmod +x /opt/sti/scripts/*.sh \
    && python3 /opt/sti/scripts/prepare_hospital.py \
      /opt/sti/worlds/2026_09_01_RoboCup_HCC_Arena_V1.glb /opt/sti/worlds/hospital \
    && source /opt/ros/humble/setup.bash \
    && ros2 launch /opt/sti/scripts/hospital.launch.py --show-args \
    && python3 /opt/sti/scripts/validate_world.py
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
