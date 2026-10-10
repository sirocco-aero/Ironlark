"""Resume the source-based forest build, one bounded Blender process at a time."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import sys
import tarfile
import time
from urllib.request import urlretrieve
import zipfile

from forest_process import run_stage
from forest_textures import thumbnail
from receipts import code_closure, digest, packages, recipe, write_world
from stage_cache import Store, clone, descriptor

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


def freeze(folder, frozen=True):
    """Every file and folder in folder read-only (frozen) or writable again. Nothing can edit a frozen folder, nor save
    a file into it: Blender writes a save beside the file and renames it over."""
    for path in [folder, *folder.rglob("*")]:
        if not path.is_symlink():
            mode = path.stat().st_mode
            path.chmod(mode & ~0o222 if frozen else mode | stat.S_IWUSR)


def unpack(archive, target, opener, identity, member=None, added_ok=False):
    """Extract once per archive (identity: its sha256), and again when a file it extracted was changed or removed
    (opening the source scene in Blender and saving it, say), or a file was added: one named like an archived texture
    would be found in its place (build_forest_world.find_texture). The marker records each file's size and mtime.
    added_ok lets files added since stand (Blender writes __pycache__ into its own folder); a partial extraction never
    looks complete. Without added_ok the extraction is left read-only: the stages and the store take the source's files
    for the archive's, so a save into it during a build would be kept under the archive's checksum. A writable one,
    made before extractions were, or made writable since, is extracted again: only a read-only one is trusted by its
    marker."""
    marker = target / ".archive-sha256"

    def state(path):
        try:
            found = path.stat()
        except FileNotFoundError:
            return None
        return [found.st_size, found.st_mtime_ns]

    if marker.is_file() and marker.read_text().startswith("{"):  # an older marker, the sha256 alone: extract again
        recorded = json.loads(marker.read_text())
        added = not added_ok and any(str(path.relative_to(target)) not in recorded["files"]
                                     for path in target.rglob("*") if path.is_file() and path != marker)
        if recorded["archive"] == identity and not added and (added_ok or not os.access(target, os.W_OK)) and all(
                state(target / name) == kept for name, kept in recorded["files"].items()):
            if not added_ok:
                freeze(target)
            return
    if target.is_dir():
        freeze(target, False)
    shutil.rmtree(target, ignore_errors=True)
    partial = target.with_name(target.name + ".part")
    shutil.rmtree(partial, ignore_errors=True)
    with opener(archive) as source:
        source.extractall(partial)
    (partial / member if member else partial).rename(target)
    shutil.rmtree(partial, ignore_errors=True)
    files = {str(path.relative_to(target)): state(path) for path in sorted(target.rglob("*")) if path.is_file()}
    marker.write_text(json.dumps({"archive": identity, "files": files}) + "\n")
    if not added_ok:
        freeze(target)


BLENDER = CACHE / "blender/blender-4.2.9-linux-x64/blender"
SOURCE = CACHE / "pine-forest/source"


def blender_executable():
    name = "blender/blender.tar.xz"
    unpack(fetch(name), BLENDER.parent, tarfile.open, INPUTS[name][1], member=BLENDER.parent.name, added_ok=True)
    return BLENDER


def source_blend():
    name = "pine-forest/source.zip"
    unpack(fetch(name), SOURCE, zipfile.ZipFile, INPUTS[name][1])
    return SOURCE / "polyhaven_pine_fir_forest.blend"


# The stages after the export: the assembly computes the world again every build, and keeps what it would compute
# unchanged, as the bakes do, in the stage store by its own code (tools/stage_cache.py).
AFTER_EXPORT = ("build_forest_world.py", "bake_forest_light.py", "bake_impostors.py")


def recipes(root):
    """What the build folder's exports are made from (the pipeline's code but the stages after the export, and the
    packages), and what the whole build is (receipts.recipe)."""
    export = code_closure(root, "tools/forest_pipeline.py", named_scripts=True, leave_out=AFTER_EXPORT)
    return {"export": {"code": export, "packages": packages(root)}, "full": recipe(root)}


def start_build(build, world, current, skip_export):
    """Stages resume a build whose export ran under the same code (recipes); other export code's outputs, and the
    caches export stages keep inside the build folder, never feed this one. --skip-export reassembles only from bakes
    today's whole pipeline finished (baked.json, written after them and removed when a build starts). The world has no
    receipt until the build completes."""
    stamp, baked = build / "recipe.json", build / "baked.json"
    if skip_export and (json.loads(baked.read_text()) if baked.is_file() else None) != current["full"]:
        raise RuntimeError("The bakes were made by other code, or did not finish. "
                           "Run ./ironlark build-world without --skip-export.")
    if (json.loads(stamp.read_text()) if stamp.is_file() else None) != current["export"]:
        shutil.rmtree(build, ignore_errors=True)
    (world / "receipt.json").unlink(missing_ok=True)
    build.mkdir(exist_ok=True)
    if not skip_export:
        baked.unlink(missing_ok=True)
    stamp.write_text(json.dumps(current["export"], indent=1) + "\n")


TEXTURES_2K = "texture-source-2k"


def exports(build, source, stages, blender_stage, python_stage=None):
    """The export's stages, each kept in the stage store (tools/stage_cache.py) by its code, arguments, the source (its
    pinned archive, whose extraction source_blend checks) and the build files it reads: an unchanged one is cloned
    back into the build folder, not run. A stage that runs does so in a room of its own (.cache/forest-export/<stage>)
    holding only what it declares, beside a link to the source it finds through its folder's parent, so a file it reads
    undeclared fails it; every file it writes there is its result."""
    store = Store(stages)
    toolchain = {"blender": INPUTS["blender/blender.tar.xz"][1], "source": INPUTS["pine-forest/source.zip"][1]}
    rooms = stages.parent / "forest-export"
    rooms.mkdir(exist_ok=True)
    # To the source itself: a worktree reaches it through its link to the main checkout's .cache, which the rooms
    # outlive.
    link = rooms / "pine-forest"
    if not link.is_symlink() or link.readlink() != source.parent.resolve():
        link.unlink(missing_ok=True)
        link.symlink_to(source.parent.resolve())
    # The source's textures at 2048, which the Blender stages read in place of the 4K originals.
    (build / TEXTURES_2K).mkdir(exist_ok=True)
    thumbnails = {}
    for path in sorted((source / "textures").glob("*.png")):
        name = f"{TEXTURES_2K}/{path.name}"
        recipe = descriptor(SIM, "thumbnail", "tools/forest_textures.py", [path.name], {},
                            toolchain | {"packages": packages(SIM)})
        kept = store.restore(recipe, build, [name])
        if not kept:
            thumbnail(path, build / name)
            kept = store.save(recipe, build, [name])
        thumbnails[name] = kept["outputs"][name][1]
    textures = hashlib.sha256(json.dumps(thumbnails, sort_keys=True).encode()).hexdigest()

    def stage(name, script, args=(), inputs=(), reads_textures=False, values=(), run=None):
        """The stage's outputs in the build folder and the JSON files named in values, read (not cloned)."""
        recipe = descriptor(SIM, name, f"tools/{script}", list(args), {n: build / n for n in inputs},
                            toolchain | ({"textures": textures} if reads_textures else {}) | (
                                {"python": platform.python_version(), "packages": packages(SIM)} if run else {}))
        kept = store.restore(recipe, build)
        if kept:
            return kept["value"]
        room = rooms / name
        shutil.rmtree(room, ignore_errors=True)
        room.mkdir()
        for n in inputs:
            (room / n).parent.mkdir(parents=True, exist_ok=True)
            clone(build / n, room / n)
        if reads_textures:
            (room / TEXTURES_2K).symlink_to(build / TEXTURES_2K)
        before = {n: digest(room / n) for n in inputs}
        (run or blender_stage)(name, script, *args, out=room)
        written = sorted(str((Path(folder) / file).relative_to(room)) for folder, _, files in os.walk(room)
                         for file in files if not (Path(folder) / file).is_symlink())
        value = {n: json.loads((room / n).read_text()) for n in values}
        outputs = [n for n in written if n not in values and (n not in before or digest(room / n) != before[n])]
        store.save(recipe, room, outputs, value)
        for n in outputs:
            target = build / n
            target.parent.mkdir(parents=True, exist_ok=True)
            target.unlink(missing_ok=True)
            clone(room / n, target)
        shutil.rmtree(room)
        return value

    def run_python(name, script, *args, out):
        print(f"Forest build: {name}", flush=True)
        run_stage([sys.executable, str(TOOLS / script), "--build", str(out), *args], CACHE / "build-logs" / f"{name}.log")

    stage("terrain", "export_forest.py")
    stage("tree-layout", "export_tree_layout.py")
    # The twig cards every procedural tree is built from, then each tree on its own; their records are merged here,
    # in name order, as the trees' stages once merged them one after another.
    stage("twigs", "export_forest_visuals.py", ["--prepare-only"], reads_textures=True, values=["material_images.json"])
    atlas = json.loads((build / "twig_atlas.json").read_text())
    twigs = ["twig_atlas.json"] + sorted({card[key] for cards in atlas.values() for card in cards
                                         for key in ("file", "normal") if key in card})
    visual, properties, images = {}, {}, {}
    for variant in sorted({r["variant"] for r in json.loads((build / "authored_trees.json").read_text())}):
        made = stage(f"tree-{variant}", "export_forest_visuals.py", ["--variant", variant], twigs, True,
                     ["visual_trees.json", "tree_properties.json", "material_images.json"])
        visual |= made["visual_trees.json"]["variants"]
        properties |= made["tree_properties.json"]
        images.update(made["material_images.json"])
        stage(f"material-{variant}", "bake_tree_materials.py", ["--variant", variant], [f"material_{variant}.blend"],
              True)
    (build / "visual_trees.json").write_text(json.dumps({"version": 1, "variants": visual}, indent=1))
    (build / "tree_properties.json").write_text(json.dumps(properties, indent=2))
    (build / "material_images.json").write_text(json.dumps(images, indent=1))
    stage("cover-layout", "export_cover_layout.py")
    stage("cover", "export_forest_cover.py", inputs=["cover.json"])
    stage("river", "export_forest_water.py")
    for x in range(2):
        for y in range(2):
            stage(f"terrain-material-{x}-{y}", "export_terrain_detail.py", ["--tile", str(x), str(y)],
                  ["terrain_meta.json", "manifest.json"], True)
    stage("floor-cover", "export_floor_cover.py", reads_textures=True)
    photos = json.loads((build / "floor_cover_assets.json").read_text())
    stage("floor-composite", "compose_forest_floor.py",
          inputs=["floor_cover_assets.json", "floor_cover.jsonl.gz", "terrain_meta.json",
                  *(f"terrain_{x}_{y}_diff.png" for x in range(2) for y in range(2)),
                  *sorted({meta["image"] for meta in photos.values()})], run=python_stage or run_python)
    stage("sky", "export_forest_sky.py")


def bakes(build, world, source, stages, blender_stage):
    """The canopy light and the impostor captures, kept in the stage store by the bytes they read
    (tools/stage_cache.py): an unchanged bake comes back, not rendered again, whatever else changed."""
    store = Store(stages)
    # The source scene (its sky world, belfast_sunset_puresky, and that world's 8k sky) by its pinned archive, whose
    # extraction source_blend checks.
    toolchain = {"blender": INPUTS["blender/blender.tar.xz"][1], "source": INPUTS["pine-forest/source.zip"][1]}
    records = json.loads((world / "visual_instances.json").read_text())
    named = {r["mesh"] for r in records} | {r["texture"] for r in records if r["texture"]}
    named |= {"visual_instances.json"} | {f"meshes/terrain_{x}_{y}.obj" for x in range(2) for y in range(2)}
    inputs = {name: world / name for name in named}
    args = ["--world", str(world)]
    light = descriptor(SIM, "canopy-light", "tools/bake_forest_light.py", args, inputs, toolchain)
    outputs = [f"terrain_{x}_{y}_layers.npy" for x in range(2) for y in range(2)] + ["light_layers.json"]
    if not store.restore(light, build, outputs):
        for name in outputs:
            (build / name).unlink(missing_ok=True)
        blender_stage("canopy-light", "bake_forest_light.py", *args)
        store.save(light, build, outputs)
    # Impostors capture the trees as the world draws them; the world then draws them far away. A variant's parts
    # (which texture each mesh wears) are among its arguments; their files, its inputs.
    declared = json.loads((build / "impostor_variants.json").read_text())
    captures = {variant: descriptor(SIM, "impostor", "tools/bake_impostors.py", [*args, variant, parts], {
        part[key]: world / part[key] for part in parts for key in ("mesh", "tex", "normal") if part[key]},
        toolchain) for variant, parts in declared.items()}
    files = {variant: [f"impostor_{variant}.npz", f"impostor_{variant}.json"] for variant in declared}
    missed = sorted(variant for variant, recipe in captures.items() if not store.restore(recipe, build, files[variant]))
    if missed:
        for variant in missed:
            for name in files[variant]:
                (build / name).unlink(missing_ok=True)
        blender_stage("impostors", "bake_impostors.py", *args, "--variants", *missed)
        for variant in missed:
            store.save(captures[variant], build, files[variant])
    (build / "impostors.json").write_text(json.dumps(
        {variant: json.loads((build / f"impostor_{variant}.json").read_text()) for variant in sorted(declared)},
        indent=1))


def build_world(skip_export=False):
    build = CACHE / "forest-build"
    source = SOURCE
    world = SIM / "worlds/pine_forest"
    blender = BLENDER
    logs = CACHE / "build-logs"
    build.parent.mkdir(parents=True, exist_ok=True)
    lock = (CACHE / "forest-build.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise RuntimeError("Another forest build is running (.cache is shared by every checkout linking it).") \
            from error
    started, current = time.time(), recipes(SIM)
    start_build(build, world, current, skip_export)

    def blender_stage(name, script, *args, out=build):
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
                str(out),
                *args,
            ],
            logs / f"{name}.log",
        )

    scene = source_blend()  # the assembly reads the source's photographs too, so --skip-export checks it as well
    if not skip_export:
        for path in (blender_executable(), scene):
            if not path.is_file():
                raise RuntimeError(f"Missing build input: {path}")
        exports(build, source, CACHE / "forest-stages", blender_stage)
    assemble = [
        sys.executable,
        str(TOOLS / "build_forest_world.py"),
        "--build",
        str(build),
        "--source",
        str(source / "textures"),
        "--out",
        str(world),
        "--stages",
        str(CACHE / "forest-stages"),
        "--source-archive",
        INPUTS["pine-forest/source.zip"][1],
    ]
    if not skip_export:
        # The bakes read only part of the world (bake-inputs: no valley, fog, light fields, atlases or compression),
        # so it is assembled for them first and in full once, after them.
        print("Forest build: the bakes' inputs", flush=True)
        run_stage([*assemble, "--bake-inputs"], logs / "assemble-bake-inputs.log")
        bakes(build, world, source, CACHE / "forest-stages", blender_stage)
        (build / "baked.json").write_text(json.dumps(current["full"], indent=1) + "\n")
    print("Forest build: assemble world", flush=True)
    run_stage(assemble, logs / "assemble.log")
    write_world(SIM, world)
    if not skip_export:  # which bakes it used: --skip-export ran none
        Store(CACHE / "forest-stages").collect(SIM, started)
    print(f"Forest world ready: {world / 'pine_forest.wbt'}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-export", action="store_true")
    build_world(parser.parse_args().skip_export)
