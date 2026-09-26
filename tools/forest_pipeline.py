"""Resume the source-based forest build, one bounded Blender process at a time."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tarfile
from urllib.request import urlretrieve
import zipfile

from PIL import Image

from forest_process import run_stage

SIM = Path(__file__).resolve().parents[1]
CACHE = SIM / ".cache"
TOOLS = SIM / "tools"
INPUTS = {
    "blender/blender.tar.xz": (
        "https://download.blender.org/release/Blender4.2/blender-4.2.9-linux-x64.tar.xz",
        "dfbc127a7d28f9c2175b23bf9d6701b2855f31eedfb391f9a6e60adb24572846",
    ),
    "pine-forest/source.zip": (
        "https://dl.polyhaven.org/file/ph-assets/Scenes/pine_forest.zip",
        "9d08c4883a0b0eac2bd001ef874795d5120889b59e97c52f482cf1da77e42c33",
    ),
}


def fetch(name):
    """Download a pinned build input once and verify it before use."""
    url, expected = INPUTS[name]
    path = CACHE / name
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"Downloading {url}", flush=True)
        partial = path.with_suffix(path.suffix + ".part")
        urlretrieve(url, partial)
        partial.rename(path)
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1 << 20):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise RuntimeError(f"Checksum mismatch for {path}; delete it and retry.")
    return path


def unpack(archive, target, opener, member=None):
    """Extract once; a partial extraction never looks complete."""
    if target.exists():
        return
    partial = target.with_name(target.name + ".part")
    shutil.rmtree(partial, ignore_errors=True)
    with opener(archive) as source:
        source.extractall(partial)
    (partial / member if member else partial).rename(target)
    shutil.rmtree(partial, ignore_errors=True)


BLENDER = CACHE / "blender/blender-4.2.9-linux-x64/blender"
SOURCE = CACHE / "pine-forest/source"


def blender_executable():
    unpack(fetch("blender/blender.tar.xz"), BLENDER.parent, tarfile.open, member=BLENDER.parent.name)
    return BLENDER


def source_blend():
    unpack(fetch("pine-forest/source.zip"), SOURCE, zipfile.ZipFile)
    return SOURCE / "polyhaven_pine_fir_forest.blend"


def build_world(skip_export=False):
    build = CACHE / "forest-build"
    source = SOURCE
    world = SIM / "worlds/pine_forest"
    blender = BLENDER
    logs = CACHE / "build-logs"
    build.mkdir(parents=True, exist_ok=True)

    def blender_stage(name, script, *args):
        print(f"Forest build: {name}", flush=True)
        run_stage(
            [
                str(blender),
                "--background",
                "--factory-startup",
                "--threads",
                "2",
                "--python-exit-code",
                "1",
                "--python",
                str(TOOLS / script),
                "--",
                "--out",
                str(build),
                *args,
            ],
            logs / f"{name}.log",
        )

    def missing(*names):
        return any(not (build / name).is_file() for name in names)

    if not skip_export:
        for path in (blender_executable(), source_blend()):
            if not path.is_file():
                raise RuntimeError(f"Missing build input: {path}")
        # Decode and resize one texture at a time. Blender is pointed at these
        # files before loading pixels, so the 4K originals never all enter RAM.
        textures = build / "texture-source-2k"
        textures.mkdir(exist_ok=True)
        for path in sorted((source / "textures").glob("*.png")):
            target = textures / path.name
            if target.is_file():
                continue
            with Image.open(path) as original:
                original.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
                original.save(target)
        if missing("terrain.obj", "terrain_meta.json", "heights.json", "manifest.json"):
            blender_stage("terrain", "export_forest.py")
        if missing("authored_trees.json"):
            blender_stage("tree-layout", "export_tree_layout.py")
        desired = {
            r["variant"]
            for r in json.loads((build / "authored_trees.json").read_text())
        }
        visual_path = build / "visual_trees.json"
        visual = (
            json.loads(visual_path.read_text())["variants"]
            if visual_path.exists()
            else {}
        )
        for variant in sorted(desired):
            if variant not in visual or any(
                missing(part["file"]) for part in visual[variant]
            ):
                blender_stage(
                    f"tree-{variant}", "export_forest_visuals.py", "--variant", variant
                )
            baked = build / f"baked_{variant}.json"
            if not baked.exists() or any(
                missing(filename)
                for material in json.loads(baked.read_text()).values()
                for filename in material.values()
            ):
                blender_stage(
                    f"material-{variant}",
                    "bake_tree_materials.py",
                    "--variant",
                    variant,
                )
        if missing("cover.json"):
            blender_stage("cover-layout", "export_cover_layout.py")
        assets_path = build / "cover_assets.json"
        assets = json.loads(assets_path.read_text()) if assets_path.exists() else {}
        names = {
            r[key] for r in json.loads((build / "cover.json").read_text()) for key in ("asset", "lod1")
        }
        if names - assets.keys() or any(
            missing(asset["file"]) for asset in assets.values()
        ):
            blender_stage("cover", "export_forest_cover.py")
        if missing("river.obj", "river_meta.json"):
            blender_stage("river", "export_forest_water.py")
        for x in range(2):
            for y in range(2):
                if missing(f"terrain_{x}_{y}_diff.png", f"terrain_{x}_{y}_normal.png", f"terrain_{x}_{y}_mask.png",
                           "terrain_generated.json"):
                    blender_stage(
                        f"terrain-material-{x}-{y}",
                        "export_terrain_detail.py",
                        "--tile",
                        str(x),
                        str(y),
                    )
        if missing("floor_cover.jsonl.gz", "floor_cover_assets.json"):
            blender_stage("floor-cover", "export_floor_cover.py")
        if any(
            missing(f"terrain_{x}_{y}_floor.png") for x in range(2) for y in range(2)
        ):
            run_stage(
                [
                    sys.executable,
                    str(TOOLS / "compose_forest_floor.py"),
                    "--build",
                    str(build),
                ],
                logs / "floor-composite.log",
            )
        if any(
            missing(f"{prefix}_{face}.hdr")
            for face in ("back", "bottom", "front", "left", "right", "top")
            for prefix in ("sky", "sky_light")
        ) or missing("sky_light_mean.json", "sky_horizon.json", "sky_phase_radiance.json"):
            blender_stage("sky", "export_forest_sky.py")
    assemble = [
        sys.executable,
        str(TOOLS / "build_forest_world.py"),
        "--build",
        str(build),
        "--source",
        str(source / "textures"),
        "--out",
        str(world),
    ]
    print("Forest build: assemble world", flush=True)
    run_stage(assemble, logs / "assemble.log")
    if not skip_export:
        signature = json.loads((world / "build_manifest.json").read_text())[
            "light_signature"
        ]
        stamp = build / "light_stamp.json"
        current = (
            stamp.exists()
            and json.loads(stamp.read_text()).get("signature") == signature
        )
        if not current or any(
            missing(f"terrain_{x}_{y}_light.npy", f"terrain_{x}_{y}_layers.npy") for x in range(2) for y in range(2)
        ):
            blender_stage("canopy-light", "bake_forest_light.py", "--world", str(world))
            stamp.write_text(json.dumps({"signature": signature}))
            run_stage(assemble, logs / "assemble-lit.log")
        # Impostors capture the trees as the world draws them; the world then draws them far away.
        signature = hashlib.sha256(
            (build / "impostor_variants.json").read_bytes() + (TOOLS / "bake_impostors.py").read_bytes()
        ).hexdigest()
        stamp = build / "impostor_stamp.json"
        if not stamp.exists() or json.loads(stamp.read_text()).get("signature") != signature:
            (build / "impostors.json").unlink(missing_ok=True)
            blender_stage("impostors", "bake_impostors.py", "--world", str(world))
            stamp.write_text(json.dumps({"signature": signature}))
            run_stage(assemble, logs / "assemble-impostors.log")
    print(f"Forest world ready: {world / 'pine_forest.wbt'}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-export", action="store_true")
    build_world(parser.parse_args().skip_export)
