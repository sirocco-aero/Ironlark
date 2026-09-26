"""Frames and wire format of the sensor stream (sensing/)."""
import json
import math
import socket
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sensing import frames, protocol  # noqa: E402


def rotate(q, v):
    x, y, z, w = q
    r = frames.quaternion_multiply(frames.quaternion_multiply(q, (*v, 0.0)), (-x, -y, -z, w))
    return r[:3]


class Frames(unittest.TestCase):
    def assertVector(self, a, b):
        for p, q in zip(a, b):
            self.assertAlmostEqual(p, q, places=6)

    def test_enu_to_ned(self):
        self.assertEqual(frames.enu_to_ned((1, 2, 3)), (2, 1, -3))
        self.assertVector(rotate(frames.ENU_TO_NED, (1, 0, 0)), (0, 1, 0))  # east
        self.assertVector(rotate(frames.ENU_TO_NED, (0, 0, 1)), (0, 0, -1))  # up

    def test_flu_to_frd(self):
        self.assertEqual(frames.flu_to_frd((1, 2, 3)), (1, -2, -3))

    def test_attitude(self):
        # Nose east, level: yaw 90 degrees in NED.
        x, y, z, w = frames.attitude_enu_flu_to_ned_frd((0, 0, 0, 1))
        self.assertAlmostEqual(math.degrees(math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))), 90.0)

    def test_camera_optical_frame(self):
        o = frames.CAMERA_LINK_TO_OPTICAL
        self.assertVector(rotate(o, (0, 0, 1)), (1, 0, 0))  # optical z: forward
        self.assertVector(rotate(o, (1, 0, 0)), (0, -1, 0))  # optical x: right
        self.assertVector(rotate(o, (0, 1, 0)), (0, 0, -1))  # optical y: down

    def test_config_rates_fit_the_physics_step(self):
        config = json.loads((ROOT / "config/sensors.json").read_text())
        for key in ("imu", "lidar", "camera"):
            self.assertEqual((1000 / config[key]["rate_hz"]) % 2, 0, key)  # 2 ms basicTimeStep


class Protocol(unittest.TestCase):
    def test_round_trip(self):
        a, b = socket.socketpair()
        payload = protocol.COUNT.pack(2) + struct.pack("<fffif", 1, 2, 3, 4, 0) * 2
        a.sendall(protocol.pack(protocol.LIDAR, 123_456_789, payload) + protocol.pack(protocol.CLOCK, 5))
        self.assertEqual(protocol.read_message(b), (protocol.LIDAR, 123_456_789, payload))
        self.assertEqual(protocol.read_message(b), (protocol.CLOCK, 5, b""))
        a.close()
        with self.assertRaises(EOFError):
            protocol.read_message(b)
        b.close()


if __name__ == "__main__":
    unittest.main()
