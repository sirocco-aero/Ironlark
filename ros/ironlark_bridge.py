"""ROS 2 side of the sensor stream: publish what the Webots controller sends, in Webots time.

Listens on config/sensors.json's stream address for sensing/protocol.py messages and publishes
/clock, /imu/data, /lidar/points, /camera/image_raw, /camera/camera_info and static transforms
from base_link to each sensor. Ground truth goes to /ground_truth/* only: autonomy topics never
carry it. Frames follow REP-103 (sensing/frames.py): Webots' world and bodies are ENU and FLU.
"""
import array
import json
import math
import socket
import sys
import threading
import time
from pathlib import Path

import rclpy
from builtin_interfaces.msg import Time
from geometry_msgs.msg import PoseStamped, TransformStamped, Vector3Stamped
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
from rosgraph_msgs.msg import Clock
from sensor_msgs.msg import CameraInfo, Image, Imu, PointCloud2, PointField
from tf2_msgs.msg import TFMessage

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sensing import frames, protocol  # noqa: E402


def stamp(time_ns):
    return Time(sec=time_ns // 1_000_000_000, nanosec=time_ns % 1_000_000_000)


class Bridge(Node):
    def __init__(self, config):
        super().__init__("ironlark_bridge")
        self.config = config
        reliable = QoSProfile(depth=20, reliability=ReliabilityPolicy.RELIABLE)
        self.clock = self.create_publisher(Clock, "/clock", reliable)
        self.imu = self.create_publisher(Imu, "/imu/data", reliable)
        self.points = self.create_publisher(PointCloud2, "/lidar/points", reliable)
        self.image = self.create_publisher(Image, "/camera/image_raw", reliable)
        self.camera_info = self.create_publisher(CameraInfo, "/camera/camera_info", reliable)
        self.truth_pose = self.create_publisher(PoseStamped, "/ground_truth/pose", reliable)
        self.truth_velocity = self.create_publisher(Vector3Stamped, "/ground_truth/velocity", reliable)
        latched = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                             durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.static_tf = self.create_publisher(TFMessage, "/tf_static", latched)
        self.static_published = False

        imu = config["imu"]
        noise = (imu["accelerometer_noise"] ** 2, imu["gyro_noise"] ** 2)
        self.accel_covariance = [noise[0], 0.0, 0.0, 0.0, noise[0], 0.0, 0.0, 0.0, noise[0]]
        self.gyro_covariance = [noise[1], 0.0, 0.0, 0.0, noise[1], 0.0, 0.0, 0.0, noise[1]]
        self.point_fields = [
            PointField(name="x", offset=0, datatype=PointField.FLOAT32, count=1),
            PointField(name="y", offset=4, datatype=PointField.FLOAT32, count=1),
            PointField(name="z", offset=8, datatype=PointField.FLOAT32, count=1),
            PointField(name="ring", offset=12, datatype=PointField.INT32, count=1),
            PointField(name="time", offset=16, datatype=PointField.FLOAT32, count=1),
        ]

    def publish_static(self, time_ns):
        transforms = []

        def add(parent, child, translation, quaternion):
            t = TransformStamped()
            t.header.stamp = stamp(time_ns)
            t.header.frame_id, t.child_frame_id = parent, child
            t.transform.translation.x, t.transform.translation.y, t.transform.translation.z = translation
            (t.transform.rotation.x, t.transform.rotation.y, t.transform.rotation.z,
             t.transform.rotation.w) = quaternion
            transforms.append(t)

        for key in ("imu", "lidar", "camera"):
            sensor = self.config[key]
            add("base_link", sensor["frame_id"], sensor["translation"],
                frames.axis_angle_to_quaternion(sensor["rotation"]))
        camera = self.config["camera"]
        add(camera["frame_id"], camera["optical_frame_id"], (0.0, 0.0, 0.0), frames.CAMERA_LINK_TO_OPTICAL)
        self.static_tf.publish(TFMessage(transforms=transforms))
        self.static_published = True

    def on_message(self, kind, time_ns, payload):
        if not self.static_published:
            self.publish_static(time_ns)
        header_stamp = stamp(time_ns)
        if kind == protocol.CLOCK:
            self.clock.publish(Clock(clock=header_stamp))
        elif kind == protocol.IMU:
            qx, qy, qz, qw, gx, gy, gz, ax, ay, az = protocol.VECTOR10.unpack(payload)
            msg = Imu()
            msg.header.stamp, msg.header.frame_id = header_stamp, self.config["imu"]["frame_id"]
            msg.orientation.x, msg.orientation.y, msg.orientation.z, msg.orientation.w = qx, qy, qz, qw
            msg.angular_velocity.x, msg.angular_velocity.y, msg.angular_velocity.z = gx, gy, gz
            msg.linear_acceleration.x, msg.linear_acceleration.y, msg.linear_acceleration.z = ax, ay, az
            msg.angular_velocity_covariance = self.gyro_covariance
            msg.linear_acceleration_covariance = self.accel_covariance
            msg.orientation_covariance[0] = -1.0  # orientation from the IMU's attitude filter: unknown accuracy
            self.imu.publish(msg)
        elif kind == protocol.LIDAR:
            (count,) = protocol.COUNT.unpack_from(payload)
            msg = PointCloud2()
            msg.header.stamp, msg.header.frame_id = header_stamp, self.config["lidar"]["frame_id"]
            msg.height, msg.width = 1, count
            msg.fields = self.point_fields
            msg.is_bigendian, msg.is_dense = False, False  # returns past the range are infinite
            msg.point_step = protocol.LIDAR_POINT_BYTES
            msg.row_step = count * protocol.LIDAR_POINT_BYTES
            # array('B'): rclpy copies it; assigning bytes checks each byte in Python.
            msg.data = array.array("B", payload[protocol.COUNT.size:])
            self.points.publish(msg)
        elif kind == protocol.IMAGE:
            width, height = protocol.SIZE.unpack_from(payload)
            camera = self.config["camera"]
            msg = Image()
            msg.header.stamp, msg.header.frame_id = header_stamp, camera["optical_frame_id"]
            msg.height, msg.width, msg.encoding, msg.step = height, width, "bgra8", 4 * width
            msg.data = array.array("B", payload[protocol.SIZE.size:])
            self.image.publish(msg)
            info = CameraInfo()
            info.header = msg.header
            info.width, info.height, info.distortion_model = width, height, "plumb_bob"
            focal = width / 2.0 / math.tan(camera["field_of_view"] / 2.0)
            cx, cy = width / 2.0, height / 2.0
            info.k = [focal, 0.0, cx, 0.0, focal, cy, 0.0, 0.0, 1.0]
            info.d = [0.0] * 5
            info.r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
            info.p = [focal, 0.0, cx, 0.0, 0.0, focal, cy, 0.0, 0.0, 0.0, 1.0, 0.0]
            self.camera_info.publish(info)
        elif kind == protocol.TRUTH:
            px, py, pz, qx, qy, qz, qw, vx, vy, vz = protocol.VECTOR10.unpack(payload)
            pose = PoseStamped()
            pose.header.stamp, pose.header.frame_id = header_stamp, "map"
            pose.pose.position.x, pose.pose.position.y, pose.pose.position.z = px, py, pz
            (pose.pose.orientation.x, pose.pose.orientation.y, pose.pose.orientation.z,
             pose.pose.orientation.w) = qx, qy, qz, qw
            self.truth_pose.publish(pose)
            velocity = Vector3Stamped()
            velocity.header = pose.header
            velocity.vector.x, velocity.vector.y, velocity.vector.z = vx, vy, vz
            self.truth_velocity.publish(velocity)


def serve(node, address):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(address)
    server.listen(1)
    node.get_logger().info(f"waiting for the Webots controller on {address[0]}:{address[1]}")
    while rclpy.ok():
        connection, _ = server.accept()
        node.get_logger().info("controller connected")
        received, started = 0, time.monotonic()
        try:
            while True:
                node.on_message(*protocol.read_message(connection))
                received += 1
                if received % 5000 == 0:
                    rate = received / (time.monotonic() - started)
                    node.get_logger().info(f"{received} messages, {rate:.0f} per second")
        except (EOFError, OSError, ValueError) as error:
            node.get_logger().info(f"controller disconnected ({error or 'closed'})")
            connection.close()


def main():
    config = json.loads((ROOT / "config/sensors.json").read_text())
    rclpy.init()
    node = Bridge(config)
    stream = config["stream"]
    threading.Thread(target=serve, args=(node, (stream["host"], stream["port"])), daemon=True).start()
    rclpy.spin(node)


if __name__ == "__main__":
    main()
