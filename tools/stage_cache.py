"""The forest build's expensive results, kept outside the build folder by what made them.

A stage's key is the sha256 of its descriptor: the stage, its arguments (the files among them by their contents,
as inputs), the sha256 of every file of its code (receipts.code_closure), of every input file it reads, and its
toolchain (the Blender build). The device does not decide a result: the canopy light baked on the GPU equals its CPU
bake within sampling noise, so a result serves either. The result lives in .cache/forest-stages/<stage>/<key>/: the
outputs, read-only, and manifest.json written last (the descriptor, each output's size and sha256, and a JSON value
the stage returns), so a half-stored result is never found. A hit checks the stored result is this descriptor's, with
exactly the outputs the stage names, each intact, and clones them into the build folder: reflinks (btrfs), which
share the stored blocks, so no extra disk, yet are files of their own, so writing one, as an older checkout's stages
do, leaves the store as it is (a copy where the filesystem cannot share). Another input or code byte is another key.
After a build, the store keeps what each checkout's latest build used, and nothing else (Store.collect).
"""
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat

from receipts import code_closure, digest


FICLONE = 0x40049409  # linux/fs.h: make the target share the source's blocks


def clone(source, target):
    """target, a file of its own with source's bytes: sharing its blocks where the filesystem can (btrfs), copied
    where it cannot (tmpfs, as /tmp is) or across filesystems."""
    with open(source, "rb") as original, open(target, "wb") as copy:
        try:
            fcntl.ioctl(copy.fileno(), FICLONE, original.fileno())
        except OSError as error:
            if error.errno not in (errno.EOPNOTSUPP, errno.EXDEV):
                raise
            shutil.copyfileobj(original, copy)


CODE = {}  # (root, script): its code_closure, read once per process: code does not change during a build


def descriptor(root, stage, script, args, inputs, toolchain):
    """What a stage's result is made of: inputs maps logical names to the files it reads; args are its arguments,
    without the paths among them (their files are inputs, by content, wherever the checkout is)."""
    code = CODE.get((str(root), script)) or CODE.setdefault((str(root), script), code_closure(root, script))
    return {"stage": stage, "args": [arg for arg in args if not (isinstance(arg, str) and Path(arg).is_absolute())],
            "code": code, "inputs": {name: digest(path) for name, path in sorted(inputs.items())},
            "toolchain": toolchain}


class Store:
    def __init__(self, folder):
        self.folder = Path(folder)

    def place(self, recipe):
        key = hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()
        return self.folder / recipe["stage"] / key

    def restore(self, recipe, build, outputs):
        """Clone the stored result's outputs into build and return its value as {"value": ...}; None, and the result
        discarded, unless it is this recipe's, with exactly these outputs, each intact."""
        place = self.place(recipe)
        manifest = place / "manifest.json"
        if not manifest.is_file():
            return None
        try:
            stored = json.loads(manifest.read_text())
        except json.JSONDecodeError:
            stored = None
        intact = stored is not None and stored["recipe"] == recipe and set(stored["outputs"]) == set(outputs) and all(
            (path := place / "outputs" / name).is_file() and path.stat().st_size == size and digest(path) == sha256
            for name, (size, sha256) in stored["outputs"].items())
        if not intact:
            shutil.rmtree(place)
            return None
        for name in outputs:
            target = Path(build) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.unlink(missing_ok=True)
            clone(place / "outputs" / name, target)
        os.utime(manifest)  # used by this build (collect)
        return {"value": stored["value"]}

    def save(self, recipe, build, outputs, value=None):
        """Keep clones of the named files of build, read-only, and a JSON value as this recipe's result."""
        place = self.place(recipe)
        partial = place.with_name(place.name + ".part")
        shutil.rmtree(partial, ignore_errors=True)
        files = {}
        for name in outputs:
            kept = partial / "outputs" / name
            kept.parent.mkdir(parents=True, exist_ok=True)
            clone(Path(build) / name, kept)
            kept.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            files[name] = [kept.stat().st_size, digest(kept)]
        manifest = {"recipe": recipe, "outputs": files, "value": value}
        (partial / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
        shutil.rmtree(place, ignore_errors=True)
        partial.rename(place)

    def collect(self, checkout, since):
        """After a build of checkout, begun at since (time.time()): record the results it used (restored or stored
        since then) as checkout's, then remove every result no checkout's latest build used, and half-stored ones.
        A checkout that no longer exists keeps none."""
        roots = self.folder / "roots"
        roots.mkdir(parents=True, exist_ok=True)
        used = sorted(str(manifest.parent.relative_to(self.folder)) for manifest in self.folder.glob("*/*/manifest.json")
                      if manifest.stat().st_mtime >= since)
        name = hashlib.sha256(str(checkout).encode()).hexdigest()[:16]
        (roots / f"{name}.json").write_text(json.dumps({"checkout": str(checkout), "results": used}, indent=1) + "\n")
        live = set()
        for root in roots.glob("*.json"):
            kept = json.loads(root.read_text())
            if Path(kept["checkout"]).is_dir():
                live |= set(kept["results"])
            else:
                root.unlink()
        for place in self.folder.glob("*/*"):
            if place.parent != roots and str(place.relative_to(self.folder)) not in live:
                shutil.rmtree(place)
