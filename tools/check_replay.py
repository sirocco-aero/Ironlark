"""Check that replaying a recorded flight keeps its timestamps and transforms.

Runs in the ROS 2 container on a run folder holding a bag (./ironlark check --record):

    ./ironlark check-replay runs/<run>

Plays the bag (its own /clock carries Webots time: the player publishes no clock of its own) while
recording what it publishes, then compares the two bags topic by topic: the same messages, with the
same header stamps in the same order, and the same static transforms. Writes replay_check.json
beside the bag; exits nonzero on a difference.
"""
import json
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import rosbag2_py  # noqa: F401 (StorageFilter)
from bag_reader import open_bag
from rclpy.serialization import deserialize_message
from rosidl_runtime_py.utilities import get_message

TOPICS = ["/clock", "/imu/data", "/lidar/points", "/camera/image_raw", "/camera/camera_info", "/tf_static",
          "/ground_truth/pose", "/ground_truth/velocity"]


def stamps(bag):
    """Per topic: the header stamps (ns) in order; for /tf_static, the transforms."""
    reader = open_bag(bag)
    types = {t.name: t.type for t in reader.get_all_topics_and_types()}
    reader.set_filter(rosbag2_py.StorageFilter(topics=[t for t in TOPICS if t in types]))
    out = {}
    while reader.has_next():
        topic, data, _ = reader.read_next()
        msg = deserialize_message(data, get_message(types[topic]))
        if topic == "/tf_static":
            for t in msg.transforms:
                r, p = t.transform.rotation, t.transform.translation
                out.setdefault(topic, set()).add((t.header.frame_id, t.child_frame_id, round(p.x, 6), round(p.y, 6),
                                                  round(p.z, 6), round(r.x, 6), round(r.y, 6), round(r.z, 6),
                                                  round(r.w, 6)))
            continue
        stamp = msg.clock if topic == "/clock" else msg.header.stamp
        out.setdefault(topic, []).append(stamp.sec * 1_000_000_000 + stamp.nanosec)
    return out


def main():
    run = Path(sys.argv[1])
    replay = run / "replay"
    shutil.rmtree(replay, ignore_errors=True)
    qos = "/project/ros/record_qos.yaml"  # reliable, deep: the check measures the bag, not a lagging recorder
    recorder = subprocess.Popen(["ros2", "bag", "record", "-o", str(replay), "--qos-profile-overrides-path", qos,
                                 *TOPICS],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3)  # the recorder subscribes before the player starts
    # The delay lets the recorder match every topic before the first message goes out.
    subprocess.run(["ros2", "bag", "play", str(run / "bag"), "--delay", "3", "--qos-profile-overrides-path", qos,
                    "--topics", *TOPICS], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    recorder.send_signal(signal.SIGINT)
    recorder.wait(timeout=30)

    original, replayed = stamps(run / "bag"), stamps(replay)
    report = {"topics": {}}
    for topic in TOPICS:
        a, b = original.get(topic), replayed.get(topic)
        if topic == "/tf_static":
            report["topics"][topic] = {"transforms": len(a or ()), "equal": a == b}
        else:
            entry = {"recorded": len(a or ()), "replayed": len(b or ()), "equal": a == b}
            if a and b and a != b:
                first = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
                entry["first_difference"] = first
            report["topics"][topic] = entry
    report["passed"] = all(entry["equal"] for entry in report["topics"].values())
    (run / "replay_check.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    sys.exit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
