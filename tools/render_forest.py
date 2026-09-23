#!/usr/bin/env python3
"""Render repeatable in-engine forest views on the current X display/GPU.

Does not run or command a flight. Uses Webots Camera nodes (same WREN scene),
records process RSS and nvidia-smi evidence, and exits after three views.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import time

from forest_process import memory_fields

SIM = Path(__file__).resolve().parents[1]
CONTROLLER = """import json,os,sys,time
from pathlib import Path
from controller import Supervisor
sys.path.insert(0, os.environ['LOITER_OBSERVER'])
from minimap import look_at
r=Supervisor(); camera=r.getDevice('preview');camera.enable(100)
node=r.getFromDef('PREVIEW_CAMERA')
scene=json.loads(Path(os.environ['LOITER_SCENE']).read_text())
for name,pose in scene['cameras'].items():
    node.getField('translation').setSFVec3f(pose['eye'])
    node.getField('rotation').setSFRotation(look_at(pose['eye'],pose['target']))
    start=time.monotonic()
    for _ in range(6):r.step(100)
    camera.saveImage(os.environ['LOITER_PREVIEW']+'/'+name+'.png',100)
    print('PREVIEW',name,'seconds',time.monotonic()-start,flush=True)
r.simulationQuit(0)
"""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--timeout", type=int, default=240)
    p.add_argument("--webots-home", type=Path, default=SIM / "webots")
    args = p.parse_args()
    out = Path(args.out).resolve()
    worlds = out / "worlds"
    worlds.mkdir(parents=True, exist_ok=True)
    controller = out / "controllers/forest_preview"
    controller.mkdir(parents=True, exist_ok=True)
    (controller / "forest_preview.py").write_text(CONTROLLER)
    source = SIM / "worlds/pine_forest"
    assets = worlds / "meshes"
    if not assets.exists():
        assets.symlink_to(source / "meshes", target_is_directory=True)
    world = (
        (source / "pine_forest.wbt")
        .read_text()
        .replace("../.cache/", str(SIM / ".cache") + "/")
    )
    world = world.replace('controller "flight_bridge"', 'controller "<none>"').replace(
        'controller "flight_observer"', 'controller "<none>"'
    )
    world += """\nDEF PREVIEW_CAMERA Robot {
      children [ Camera { name "preview" width 1280 height 720 fieldOfView 1.0
        near 0.08 far 250 exposure 1.0 ambientOcclusionRadius 2 } ]
      controller "forest_preview" supervisor TRUE
    }\n"""
    target = worlds / "preview.wbt"
    target.write_text(world)
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "xcb"
    env["PATH"] = str(SIM / ".venv/bin") + ":" + env.get("PATH", "")
    env.update(
        LOITER_OBSERVER=str(SIM / "controllers/flight_observer"),
        LOITER_SCENE=str(source / "scene.json"),
        LOITER_PREVIEW=str(out),
    )
    cmd = [
        str(args.webots_home.resolve() / "webots"),
        "--batch",
        "--mode=fast",
        "--minimize",
        "--log-performance=" + str(out / "performance.log"),
        "--stdout",
        "--stderr",
        str(target),
    ]
    started = time.monotonic()
    peak = 0
    samples = []
    peak_gpu = 0
    error = None
    with (out / "webots.log").open("w") as log:
        proc = subprocess.Popen(
            cmd, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )
        try:
            while proc.poll() is None:
                if time.monotonic() - started > args.timeout:
                    raise RuntimeError("Preview timed out")
                procs = subprocess.check_output(
                    ["ps", "-eo", "pid,rss,comm"], text=True
                )
                rss = sum(
                    int(row.split()[1])
                    for row in procs.splitlines()[1:]
                    if "webots-bin" in row
                )
                peak = max(peak, rss)
                if rss > 4800 * 1024:
                    raise RuntimeError("Preview exceeded 4.8 GiB process RAM budget")
                if memory_fields(Path("/proc/meminfo"))["MemAvailable"] < 768 * 1024:
                    raise RuntimeError(
                        "Preview stopped before exhausting available system RAM"
                    )
                if len(samples) < 30:
                    smi = subprocess.run(["nvidia-smi"], capture_output=True, text=True)
                    if "webots-bin" in smi.stdout:
                        samples.append(smi.stdout)
                gpu = subprocess.run(
                    [
                        "nvidia-smi",
                        "--query-gpu=memory.used",
                        "--format=csv,noheader,nounits",
                    ],
                    capture_output=True,
                    text=True,
                )
                if gpu.returncode == 0:
                    peak_gpu = max(peak_gpu, max(int(v) for v in gpu.stdout.split()))
                time.sleep(0.25)
            if proc.returncode:
                raise RuntimeError(f"Webots exited {proc.returncode}")
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            if proc.poll() is None:
                import signal

                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
            (out / "metrics.json").write_text(
                json.dumps(
                    {
                        "peak_rss_mib": round(peak / 1024, 1),
                        "peak_total_gpu_mib": peak_gpu,
                        "wall_seconds": round(time.monotonic() - started, 1),
                        "gpu_process_observed": bool(samples),
                        "error": error,
                    },
                    indent=2,
                )
            )
            (out / "nvidia-smi.txt").write_text("\n".join(samples[-2:]))
    print(out)


if __name__ == "__main__":
    main()
