FROM ros:humble-ros-base-jammy

ARG TARGETARCH
RUN test "${TARGETARCH:-amd64}" = amd64
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive
COPY docker/apt-packages.txt /tmp/sti-apt-packages.txt
RUN apt-get update \
    && sed 's/\r$//; /^[[:space:]]*#/d; /^[[:space:]]*$/d' /tmp/sti-apt-packages.txt \
      | xargs -r apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* /tmp/sti-apt-packages.txt

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
    && python3 /opt/sti/scripts/check_dependencies.py \
    && ros2 launch /opt/sti/scripts/hospital.launch.py --show-args \
    && python3 /opt/sti/scripts/validate_world.py
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
