#!/usr/bin/env python3
"""Measure forest responsiveness as a viewer sees it: load time, real-time
factor with the main view rendering, each view's frames and GPU and CPU time
per render pass (the frame log, tools/frame_log.py), and peak RAM and GPU memory.

No flight runs. The main viewpoint visits each scene camera in turn. Headless:
xvfb-run -a -s "-screen 0 1920x1080x24" .venv/bin/python tools/profile_forest.py --out runs/NAME
"""

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = """import json,os,sys,time
from pathlib import Path
from controller import Supervisor
sys.path.insert(0, os.environ['IRONLARK_OBSERVER'])
from minimap import look_at
r = Supervisor()
report = {'first_step_wall': time.time()}
view = r.getFromDef('IRONLARK_VIEW')
view.getField('follow').setSFString('')
scene = json.loads(Path(os.environ['IRONLARK_SCENE']).read_text())
step = int(r.getBasicTimeStep())
seconds = float(os.environ['IRONLARK_SECONDS'])
views = []
for name, pose in scene['cameras'].items():
    rotation = look_at(pose['eye'], pose['target'])
    sim0, wall0 = r.getTime(), time.monotonic()
    count = 0
    while r.getTime() - sim0 < seconds:
        # Re-pin the view so mouse navigation cannot change what is measured.
        if count % 10 == 0:
            view.getField('position').setSFVec3f(pose['eye'])
            view.getField('orientation').setSFRotation(rotation)
        count += 1
        if r.step(step) == -1:
            break
    wall = time.monotonic() - wall0
    views.append({'view': name, 'sim_start': sim0, 'sim_seconds': round(r.getTime() - sim0, 3),
                  'wall_seconds': round(wall, 3),
                  'real_time_factor': round((r.getTime() - sim0) / wall, 3)})
# Moving view: fly the forest camera forward at 4 m/s, as a drone or a dragging viewer would.
pose = scene['cameras']['forest']
eye, target = [float(v) for v in pose['eye']], [float(v) for v in pose['target']]
heading = [t - e for t, e in zip(target, eye)]
norm = sum(h * h for h in heading) ** 0.5
heading = [h / norm for h in heading]
sim0, wall0 = r.getTime(), time.monotonic()
while r.getTime() - sim0 < seconds:
    travelled = 4.0 * (r.getTime() - sim0)
    position = [e + h * travelled for e, h in zip(eye, heading)]
    view.getField('position').setSFVec3f(position)
    view.getField('orientation').setSFRotation(look_at(position, [p + h for p, h in zip(position, heading)]))
    if r.step(step * 10) == -1:
        break
wall = time.monotonic() - wall0
views.append({'view': 'moving', 'sim_start': sim0, 'sim_seconds': round(r.getTime() - sim0, 3), 'wall_seconds': round(wall, 3),
              'real_time_factor': round((r.getTime() - sim0) / wall, 3)})
report['views'] = views
Path(os.environ['IRONLARK_REPORT']).write_text(json.dumps(report))
r.simulationQuit(0)
"""


def sample(pid):
    rss = 0
    for row in subprocess.check_output(["ps", "-eo", "rss,comm"], text=True).splitlines()[1:]:
        if "webots-bin" in row:
            rss += int(row.split()[0])
    gpu = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used,utilization.gpu", "--format=csv,noheader,nounits"],
        capture_output=True, text=True,
    )
    used, util = (int(v) for v in gpu.stdout.split(",")) if gpu.returncode == 0 else (0, 0)
    return rss // 1024, used, util


def without_shapes(world, text):
    """The world without the Shape nodes whose text contains text."""
    kept, start = [], 0
    while (begin := world.find("Shape {", start)) != -1:
        depth, end = 0, begin
        for end in range(begin, len(world)):
            depth += {"{": 1, "}": -1}.get(world[end], 0)
            if depth == 0 and world[end] == "}":
                break
        block = world[begin:end + 1]
        kept.append(world[start:begin] + ("" if text in block else block))
        start = end + 1
    return "".join(kept) + world[start:]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--webots-home", type=Path, default=None)
    p.add_argument("--seconds", type=float, default=5.0, help="sim seconds per view")
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--pref", action="append", default=[], metavar="GROUP/KEY=VALUE",
                   help="Webots preference for this run only")
    p.add_argument("--without", action="append", default=[], metavar="TEXT",
                   help="drop every Shape whose text contains TEXT, to attribute its cost (repeatable)")
    args = p.parse_args()
    home = (args.webots_home or Path(os.environ.get("WEBOTS_HOME", ROOT / ".cache/webots-renderer"))).resolve()
    out = args.out.resolve()
    (out / "worlds").mkdir(parents=True, exist_ok=True)
    controller = out / "controllers/forest_profile"
    controller.mkdir(parents=True, exist_ok=True)
    (controller / "forest_profile.py").write_text(CONTROLLER)
    source = ROOT / "worlds/pine_forest"
    meshes = out / "worlds/meshes"
    if not meshes.exists():
        meshes.symlink_to(source / "meshes", target_is_directory=True)
    world = ((source / "pine_forest.wbt").read_text().replace("../.cache/", str(ROOT / ".cache") + "/")
             .replace("../protos/", str(ROOT / "protos") + "/"))
    world = world.replace('controller "flight_bridge"', 'controller "<none>"')
    world = world.replace('controller "flight_observer"', 'controller "forest_profile"')
    for text in args.without:
        world = without_shapes(world, text)
    target = out / "worlds/profile.wbt"
    target.write_text(world)
    from render_forest import private_preferences

    env = os.environ | {
        "XDG_CONFIG_HOME": str(private_preferences(out, args.pref)),
        "PATH": str(ROOT / ".venv/bin") + ":" + os.environ.get("PATH", ""),
        "IRONLARK_OBSERVER": str(ROOT / "controllers/flight_observer"),
        "IRONLARK_SCENE": str(source / "scene.json"),
        "IRONLARK_REPORT": str(out / "controller.json"),
        "IRONLARK_SECONDS": str(args.seconds),
        "IRONLARK_FRAME_LOG": str(out / "frames.log"),
    }
    (out / "frames.log").unlink(missing_ok=True)
    cmd = [str(home / "webots"), "--batch", "--mode=realtime", "--fullscreen",
           "--log-performance=" + str(out / "performance.log"), "--stdout", "--stderr", str(target)]
    started = time.time()
    samples = []
    with (out / "webots.log").open("w") as log:
        proc = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            while proc.poll() is None:
                if time.time() - started > args.timeout:
                    raise RuntimeError("Profile timed out")
                rss, gpu, util = sample(proc.pid)
                samples.append({"t": round(time.time() - started, 1), "rss_mib": rss, "gpu_mib": gpu, "gpu_util": util})
                if rss > 5000:
                    raise RuntimeError("Stopped at 5 GiB Webots RSS")
                time.sleep(0.5)
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
            report = json.loads((out / "controller.json").read_text()) if (out / "controller.json").exists() else {}
            report["load_seconds"] = round(report.pop("first_step_wall", started) - started, 1)
            report["webots_home"] = str(home)
            report["peak_rss_mib"] = max((s["rss_mib"] for s in samples), default=0)
            report["peak_gpu_mib"] = max((s["gpu_mib"] for s in samples), default=0)
            if (out / "frames.log").exists():
                # Each view's frames and render passes (tools/frame_log.py), its first simulated second left out.
                from frame_log import parse, summarize
                log = parse(out / "frames.log")
                for view in report.get("views", []):
                    start = view.pop("sim_start") + 1.0
                    summary = summarize(*log, start, start + view["sim_seconds"] - 1.0)
                    main = summary["views"].get("main", {})
                    view["frames"] = summary.get("frames")
                    view["main_gpu_ms"], view["main_cpu_ms"] = main.get("gpu_ms"), main.get("cpu_ms")
                    view["main_passes_ms"] = main.get("passes_ms")
            (out / "samples.json").write_text(json.dumps(samples))
            (out / "profile.json").write_text(json.dumps(report, indent=2))
            print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
