"""Wire format from the Webots controller to the ROS 2 bridge: framed messages over TCP.

Each message: a 20-byte header (magic, type, Webots time in nanoseconds, payload length), then the
payload. Little-endian. Plain Python (struct), so both sides load it without extra packages.
"""
import struct

MAGIC = b"IRLK"
HEADER = struct.Struct("<4sBxxxqI")

CLOCK = 1  # no payload: Webots time advanced to this step
IMU = 2  # orientation quaternion (x, y, z, w), angular velocity, linear acceleration: 10 doubles
LIDAR = 3  # uint32 count, then count Webots LidarPoints (x, y, z float32, layer int32, time float32)
IMAGE = 4  # uint16 width, uint16 height, then width * height BGRA bytes, rows from the top
TRUTH = 5  # position, orientation (x, y, z, w), linear velocity, world ENU: 10 doubles

VECTOR10 = struct.Struct("<10d")
COUNT = struct.Struct("<I")
SIZE = struct.Struct("<HH")
LIDAR_POINT_BYTES = 20


def pack(kind, time_ns, payload=b""):
    return HEADER.pack(MAGIC, kind, time_ns, len(payload)) + payload


def read_exact(stream, size):
    data = bytearray()
    while len(data) < size:
        chunk = stream.recv(size - len(data))
        if not chunk:
            raise EOFError
        data += chunk
    return bytes(data)


def read_message(stream):
    """(kind, time_ns, payload) from a socket; EOFError when it closes."""
    magic, kind, time_ns, length = HEADER.unpack(read_exact(stream, HEADER.size))
    if magic != MAGIC:
        raise ValueError("stream out of sync")
    return kind, time_ns, read_exact(stream, length)
