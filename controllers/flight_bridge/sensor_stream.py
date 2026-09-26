"""Sample the drone's sensors on Webots time and stream them to the ROS 2 bridge.

Called after every physics step. Each sensor is read at the steps its period divides (config/
sensors.json), stamped with that step's Webots time, and queued; a sender thread writes the queue
to the bridge over TCP (sensing/protocol.py). A full queue drops messages and counts them: the
stream never stalls physics, and pausing Webots stops its clock.
"""
import ctypes
import json
import random
import os
import queue
import socket
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sensing import protocol  # noqa: E402


class SensorStream:
    def __init__(self, robot, config_path=ROOT / "config/sensors.json"):
        from controller.wb import wb

        self._wb = wb
        self.robot = robot
        self.config = json.loads(Path(config_path).read_text())
        self.step_ms = int(robot.getBasicTimeStep())
        self.dropped = 0
        self.sent = 0

        def period(rate_hz):
            ms = int(round(1000.0 / rate_hz))
            if ms % self.step_ms:
                raise ValueError(f"{rate_hz} Hz is not a whole number of {self.step_ms} ms steps")
            return ms

        imu, lidar, camera = self.config["imu"], self.config["lidar"], self.config["camera"]
        # IMU noise: absolute, white, from a fixed seed so a run's sensor data repeats.
        self.noise = random.Random(imu.get("seed", 0))
        self.accel_noise, self.gyro_noise = imu["accelerometer_noise"], imu["gyro_noise"]
        self.imu_period = period(imu["rate_hz"])
        self.accelerometer = robot.getDevice(f"{imu['name']} accelerometer")
        self.gyro = robot.getDevice(f"{imu['name']} gyro")
        self.attitude = robot.getDevice(f"{imu['name']} inertial unit")
        for device in (self.accelerometer, self.gyro, self.attitude):
            device.enable(self.imu_period)
        self.lidar_period = period(lidar["rate_hz"])
        self.lidar = robot.getDevice(lidar["name"])
        self.lidar.enable(self.lidar_period)
        self.lidar.enablePointCloud()
        self.camera_period = period(camera["rate_hz"])
        self.camera = robot.getDevice(camera["name"])
        self.camera.enable(self.camera_period)
        # Ground truth: the Iris's noiseless GPS and inertial unit (enabled by the flight bridge).
        self.gps = robot.getDevice("gps")
        self.truth_attitude = robot.getDevice("inertial unit")

        stream = self.config["stream"]
        self.address = (stream["host"], stream["port"])
        self.queue = queue.Queue(maxsize=stream["queue"])
        threading.Thread(target=self._send, daemon=True).start()

    def _put(self, message):
        try:
            self.queue.put_nowait(message)
        except queue.Full:
            self.dropped += 1

    def _send(self):
        while True:
            try:
                connection = socket.create_connection(self.address, timeout=2.0)
            except OSError:
                time.sleep(1.0)  # the bridge is not up (yet): nothing is queued meanwhile
                continue
            connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.connected = True
            try:
                while True:
                    connection.sendall(self.queue.get())
                    self.sent += 1
            except OSError:
                connection.close()
                self.connected = False

    connected = False

    def after_step(self):
        """Queue what this step measured. Nothing is queued while the bridge is away."""
        if not self.connected:
            return
        now_ms = int(round(self.robot.getTime() * 1000))
        stamp = now_ms * 1_000_000
        if now_ms % 5000 == 0:
            print(f"sensing: {self.sent} sent, {self.dropped} dropped, {self.queue.qsize()} queued at {now_ms / 1000:.0f} s",
                  flush=True)
        self._put(protocol.pack(protocol.CLOCK, stamp))
        if now_ms % self.imu_period == 0:
            q = self.attitude.getQuaternion()
            gyro = [v + self.noise.gauss(0.0, self.gyro_noise) for v in self.gyro.getValues()]
            accel = [v + self.noise.gauss(0.0, self.accel_noise) for v in self.accelerometer.getValues()]
            values = (*q, *gyro, *accel)
            self._put(protocol.pack(protocol.IMU, stamp, protocol.VECTOR10.pack(*values)))
            truth = (*self.gps.getValues(), *self.truth_attitude.getQuaternion(), *self.gps.getSpeedVector())
            self._put(protocol.pack(protocol.TRUTH, stamp, protocol.VECTOR10.pack(*truth)))
        if now_ms % self.lidar_period == 0:
            # Layer-major points, top layer first: the configured layers are the first ones.
            count = min(self.lidar.getNumberOfPoints(),
                        self.config["lidar"]["layers"] * self.lidar.getHorizontalResolution())
            pointer = self._wb.wb_lidar_get_point_cloud(self.lidar._tag)
            if count and pointer:
                points = ctypes.string_at(pointer, count * protocol.LIDAR_POINT_BYTES)
                self._put(protocol.pack(protocol.LIDAR, stamp, protocol.COUNT.pack(count) + points))
        if now_ms % self.camera_period == 0:
            image = self.camera.getImage()
            if image:
                size = protocol.SIZE.pack(self.camera.getWidth(), self.camera.getHeight())
                self._put(protocol.pack(protocol.IMAGE, stamp, size + bytes(image)))


def enabled():
    return os.environ.get("IRONLARK_SENSING") == "1"
