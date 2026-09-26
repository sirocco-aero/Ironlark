FROM ubuntu:22.04

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential ca-certificates git python3 python3-dev python3-pip \
    python3-numpy python3-pexpect python3-serial pkg-config \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m pip install --no-cache-dir \
    empy==3.3.4 future==1.0.0 lxml==5.4.0 pymavlink==2.4.49 \
    pexpect==4.9.0 dronecan==1.0.27

# Copter-4.7.1. Pin source and its submodules; do not track a moving branch.
ARG ARDUPILOT_COMMIT=dbe792162d06cab66c3475fd5556bf7a120f119e
RUN git init /opt/ardupilot \
    && cd /opt/ardupilot \
    && git remote add origin https://github.com/ArduPilot/ardupilot.git \
    && git fetch --depth 1 origin ${ARDUPILOT_COMMIT} \
    && git checkout --detach FETCH_HEAD \
    && git submodule update --init --depth 1 \
       modules/waf modules/mavlink modules/gtest modules/DroneCAN/DSDL \
       modules/DroneCAN/pydronecan modules/DroneCAN/dronecan_dsdlc \
       modules/DroneCAN/libcanard modules/littlefs
WORKDIR /opt/ardupilot
RUN git submodule update --init --depth 1 modules/lwip
RUN ./waf configure --board sitl --disable-tests \
    && ./waf copter -j2

# Keep the upstream build cached while compiling this small backend correction.
COPY webots-clock.patch /tmp/webots-clock.patch
RUN git apply --check /tmp/webots-clock.patch \
    && git apply /tmp/webots-clock.patch \
    && ./waf copter -j2

ENV PYTHONUNBUFFERED=1
WORKDIR /run/ironlark
