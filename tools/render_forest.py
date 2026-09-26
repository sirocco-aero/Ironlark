#!/usr/bin/env python3
"""Capture repeatable forest views exactly as the main 3D view shows them.

Does not run or command a flight. Opens fullscreen and exports the main view,
records process RSS and nvidia-smi evidence, and exits after three views.
"""

import argparse
import re
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
sys.path.insert(0, os.environ['IRONLARK_OBSERVER'])
from minimap import look_at
r=Supervisor()
view=r.getFromDef('IRONLARK_VIEW'); view.getField('follow').setSFString('')
scene=json.loads(Path(os.environ['IRONLARK_SCENE']).read_text())
for name,pose in scene['cameras'].items():
    view.getField('position').setSFVec3f(pose['eye'])
    view.getField('orientation').setSFRotation(look_at(pose['eye'],pose['target']))
    start=time.monotonic()
    for _ in range(10):r.step(int(r.getBasicTimeStep())*16)
    r.exportImage(os.environ['IRONLARK_PREVIEW']+'/'+name+'.png',100)
    print('PREVIEW',name,'seconds',time.monotonic()-start,flush=True)
r.simulationQuit(0)
"""


def private_preferences(out, overrides):
    """A copy of the user's Webots preferences for this run, with overrides: runs never
    change the user's own (a run's settings would otherwise leak into their next launch)."""
    import configparser

    home = out / "config"
    target = home / "Cyberbotics/Webots-R2025a.conf"
    target.parent.mkdir(parents=True, exist_ok=True)
    source = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "Cyberbotics/Webots-R2025a.conf"
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    if source.exists():
        config.read(source)
    for override in overrides:
        key, value = override.split("=", 1)
        group, name = key.split("/", 1)
        if not config.has_section(group):
            config.add_section(group)
        config.set(group, name, value)
    with target.open("w") as stream:
        config.write(stream, space_around_delimiters=False)
    return home


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--timeout", type=int, default=240)
    p.add_argument("--webots-home", type=Path, default=SIM / "webots")
    p.add_argument("--no-fog", action="store_true", help="without the scattering fog")
    p.add_argument("--fog-density", type=float, help="override the fog's density (0: a medium that does nothing)")
    p.add_argument("--exposure", type=float, help="override the view's exposure")
    p.add_argument("--bloom-threshold", type=float, help="override the view's bloom threshold (-1: off)")
    p.add_argument("--no-alpha-cutoff", action="store_true", help="draw alpha-masked materials opaque (debugging)")
    p.add_argument("--png-textures", action="store_true", help="the PNGs kept beside compressed textures (debugging)")
    p.add_argument("--no-bands", action="store_true", help="drop full trees' visibility band (debugging)")
    p.add_argument("--no-light-occlusion", action="store_true", help="without the baked light occlusion")
    p.add_argument("--sub", action="append", default=[], metavar="REGEX=>REPLACEMENT",
                   help="edit the world with a regular expression (debugging)")
    p.add_argument("--pref", action="append", default=[], metavar="GROUP/KEY=VALUE",
                   help="Webots preference for this run only, e.g. OpenGL/GTAO=0 (the user's own stay untouched)")
    p.add_argument("--lights", choices=["all", "sky", "main", "secondary"], default="all",
                   help="one light only, fog off (as tools/render_lighting_reference.py --lights)")
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
        .replace("../protos/", str(SIM / "protos") + "/")
    )
    world = world.replace('controller "flight_bridge"', 'controller "<none>"')
    # The minimap robot draws nothing without its controller; drop it from previews.
    world = re.sub(r"Robot \{\s*children \[\s*Display \{[^}]*\}\s*\][^}]*\}", "", world)
    world += """\nRobot {
      controller "forest_preview" supervisor TRUE
    }\n"""
    if args.lights != "all" or args.no_fog:
        world = re.sub(r"\nFog \{.*?\n\}", "", world, flags=re.S)
    if args.lights != "all":
        suns = list(re.finditer(r"DirectionalLight \{.*?\n\}", world, flags=re.S))
        for sun, name in reversed(list(zip(suns, ("main", "secondary")))):
            if name != args.lights:
                block = re.sub(r"intensity [\d.]+", "intensity 0", sun.group(0))
                world = world[:sun.start()] + block + world[sun.end():]
        if args.lights != "sky":
            world = world.replace("luminosity 1", "luminosity 0", 1)
    for sub in args.sub:
        pattern, replacement = sub.split("=>", 1)
        world = re.sub(pattern, replacement, world)
    if args.no_light_occlusion:
        world = re.sub(r"\n  lightOcclusion\w+ [^\n]*", "", world)
    if args.no_bands:
        world = world.replace("visibilityRange 0 150.0", "visibilityRange 0 0")
    if args.png_textures:
        world = world.replace('.dds"', '.png"')
    if args.no_alpha_cutoff:
        world = re.sub(r"\n\s*alphaCutoff [\d.]+", "", world)
    if args.bloom_threshold is not None:
        world = world.replace("  ambientOcclusionRadius", f"  bloomThreshold {args.bloom_threshold}\n  ambientOcclusionRadius", 1)
    if args.exposure is not None:
        world = re.sub(r"\n  exposure [\d.]+", f"\n  exposure {args.exposure}", world, count=1)
    if args.fog_density is not None:
        world = re.sub(r"(\nFog \{.*?\n  density )[\d.e-]+", lambda m: m.group(1) + str(args.fog_density), world, flags=re.S)
    target = worlds / "preview.wbt"
    target.write_text(world)
    # The world's perspective: hidden device overlays, disabled interactions.
    (worlds / ".preview.wbproj").write_text((source / ".pine_forest.wbproj").read_text())
    env = os.environ.copy()
    env["XDG_CONFIG_HOME"] = str(private_preferences(out, args.pref))
    env["QT_QPA_PLATFORM"] = "xcb"
    env["PATH"] = str(SIM / ".venv/bin") + ":" + env.get("PATH", "")
    env.update(
        IRONLARK_OBSERVER=str(SIM / "controllers/flight_observer"),
        IRONLARK_SCENE=str(source / "scene.json"),
        IRONLARK_PREVIEW=str(out),
    )
    cmd = [
        str(args.webots_home.resolve() / "webots"),
        "--batch",
        "--mode=realtime",
        "--fullscreen",
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
