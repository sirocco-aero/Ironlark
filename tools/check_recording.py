"""Check a recorded flight's sensors against the empty world's calibration wall.

Runs in the ROS 2 container on a run folder holding a bag (./ironlark check --record):

    ./ironlark check-recording runs/<run>

For LiDAR scans taken while hovering it places the returns in the world (ground truth pose and the
static transforms), fits the wall (face at x = 5.9 m, normal -x) and checks their distance and
orientation, then projects them into the camera frame taken at the same instant and checks that
they fall on the wall's red. Writes sensor_check.json beside the bag; exits nonzero on failure.
"""
import json
import math
import struct
import sys
from pathlib import Path

import rosbag2_py
from rclpy.serialization import deserialize_message
from rosidl_runtime_py.utilities import get_message

WALL_X = 5.9  # the calibration wall's face (worlds/flight_foundation.wbt)
WALL = {"x": (5.0, 7.0), "y": (-2.2, 2.2), "z": (0.05, 3.0)}
LIMITS = {"distance_error_m": 0.05, "normal_error_deg": 2.0, "camera_agreement": 0.95}


def q_mul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz)


def rotate(q, v):
    x, y, z, w = q
    r = q_mul(q_mul(q, (*v, 0.0)), (-x, -y, -z, w))
    return r[:3]


def inverse(q):
    return (-q[0], -q[1], -q[2], q[3])


def add(a, b):
    return tuple(p + q for p, q in zip(a, b))


def sub(a, b):
    return tuple(p - q for p, q in zip(a, b))


def solve3(m, v):
    """Solve a 3 x 3 linear system (Cramer)."""
    def det(a):
        return (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1]) - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
                + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))
    d = det(m)
    out = []
    for k in range(3):
        mk = [row[:] for row in m]
        for i in range(3):
            mk[i][k] = v[i]
        out.append(det(mk) / d)
    return out


def main():
    run = Path(sys.argv[1])
    reader = rosbag2_py.SequentialReader()
    reader.open(rosbag2_py.StorageOptions(uri=str(run / "bag"), storage_id="sqlite3"),
                rosbag2_py.ConverterOptions("cdr", "cdr"))
    types = {t.name: t.type for t in reader.get_all_topics_and_types()}
    reader.set_filter(rosbag2_py.StorageFilter(topics=["/tf_static", "/ground_truth/pose", "/ground_truth/velocity",
                                                       "/lidar/points", "/camera/image_raw", "/camera/camera_info"]))
    static, pose, speed, info = {}, None, 1e9, None
    scans, images = [], {}

    def ns(stamp):
        return stamp.sec * 1_000_000_000 + stamp.nanosec

    while reader.has_next():
        topic, data, _ = reader.read_next()
        msg = deserialize_message(data, get_message(types[topic]))
        if topic == "/tf_static":
            for t in msg.transforms:
                r, tr = t.transform.rotation, t.transform.translation
                static[t.child_frame_id] = ((tr.x, tr.y, tr.z), (r.x, r.y, r.z, r.w))
        elif topic == "/ground_truth/pose":
            p, o = msg.pose.position, msg.pose.orientation
            pose = ((p.x, p.y, p.z), (o.x, o.y, o.z, o.w))
        elif topic == "/ground_truth/velocity":
            speed = math.sqrt(msg.vector.x ** 2 + msg.vector.y ** 2 + msg.vector.z ** 2)
        elif topic == "/camera/camera_info":
            info = msg
        elif topic == "/lidar/points" and pose and pose[0][2] > 2.0 and speed < 0.3 and len(scans) < 20:
            scans.append((ns(msg.header.stamp), pose, msg))
        elif topic == "/camera/image_raw" and scans and ns(msg.header.stamp) in {s[0] for s in scans}:
            images[ns(msg.header.stamp)] = msg

    lidar_t, lidar_q = static["lidar_link"]
    camera_t, camera_q = static["camera_link"]
    optical_q = static["camera_optical_frame"][1]
    fx, cx, fy, cy = info.k[0], info.k[2], info.k[4], info.k[5]
    wall, on_red, in_view = [], 0, 0
    for time_ns, (base_t, base_q), msg in scans:
        image = images.get(time_ns)
        for i in range(msg.width):
            x, y, z, _, _ = struct.unpack_from("<fffif", msg.data, i * msg.point_step)
            if not all(math.isfinite(c) for c in (x, y, z)):
                continue
            world = add(rotate(base_q, add(rotate(lidar_q, (x, y, z)), lidar_t)), base_t)
            if not all(lo <= c <= hi for c, (lo, hi) in zip(world, (WALL["x"], WALL["y"], WALL["z"]))):
                continue
            wall.append(world)
            if image is None:
                continue
            # World to base, to camera_link, to the optical frame (x right, y down, z forward).
            link = sub(rotate(inverse(base_q), sub(world, base_t)), camera_t)
            ox, oy, oz = rotate(inverse(optical_q), rotate(inverse(camera_q), link))
            if oz <= 0.1:
                continue
            u, v = int(fx * ox / oz + cx), int(fy * oy / oz + cy)
            if not (0 <= u < image.width and 0 <= v < image.height):
                continue
            in_view += 1
            b, g, r = image.data[v * image.step + 4 * u: v * image.step + 4 * u + 3]
            on_red += r > 60 and r > 1.6 * g and r > 1.6 * b
    if len(wall) < 100:
        raise SystemExit(f"Too few wall returns ({len(wall)}) in {len(scans)} hovering scans.")
    # Fit x = a + b y + c z over the wall's returns.
    n = len(wall)
    sy, sz = sum(p[1] for p in wall), sum(p[2] for p in wall)
    m = [[n, sy, sz], [sy, sum(p[1] ** 2 for p in wall), sum(p[1] * p[2] for p in wall)],
         [sz, sum(p[1] * p[2] for p in wall), sum(p[2] ** 2 for p in wall)]]
    v = [sum(p[0] for p in wall), sum(p[0] * p[1] for p in wall), sum(p[0] * p[2] for p in wall)]
    a, b, c = solve3(m, v)
    normal = (1.0, -b, -c)
    angle = math.degrees(math.acos(1.0 / math.sqrt(sum(k * k for k in normal))))
    centre = (sy / n, sz / n)
    distance = a + b * centre[0] + c * centre[1]
    residual = math.sqrt(sum((p[0] - (a + b * p[1] + c * p[2])) ** 2 for p in wall) / n)
    report = {
        "hovering_scans": len(scans),
        "wall_returns": n,
        "wall_distance_m": round(distance, 4),
        "distance_error_m": round(abs(distance - WALL_X), 4),
        "normal_error_deg": round(angle, 3),
        "plane_residual_m": round(residual, 4),
        "camera_points": in_view,
        "camera_agreement": round(on_red / in_view, 4) if in_view else 0.0,
    }
    report["passed"] = (report["distance_error_m"] <= LIMITS["distance_error_m"]
                        and report["normal_error_deg"] <= LIMITS["normal_error_deg"]
                        and report["camera_agreement"] >= LIMITS["camera_agreement"])
    (run / "sensor_check.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    sys.exit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
