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

A file's sha256 is computed once, by whichever build process first reads it, and known from then on (Digests).
"""
import contextlib
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def read_digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


# The project's code as this process started, read before any of it is imported (forest_pipeline and
# build_forest_world import this module first): what it, and the stages it starts, run.
AT_START = {str(path.relative_to(ROOT)): read_digest(path) for folder in ("tools", "controllers")
            for path in sorted((ROOT / folder).rglob("*.py")) if "__pycache__" not in path.parts}

from receipts import code_closure, digest  # noqa: E402, after the snapshot


class Digests:
    """The sha256 of files a build read, by path, valid while the path holds the same file: the same device, inode,
    size, mtime and ctime, which a write, a chmod or another file in its place all change (a write right after a stat
    too: the kernel then stamps it finer than its clock tick). Not a write through a shared memory mapping to a page
    already dirty, which updates no time: nothing the build runs writes files so. Its log, appended to by every build
    process and read back by the others, carries what one learns to the rest and to later builds, so a build hashes
    only bytes no build hashed before. The log is a cache: a line lost costs one hash, and without it the next build
    hashes everything again."""

    def __init__(self, log):
        self.log, self.known, self.read = Path(log), {}, (None, 0)

    @staticmethod
    def identity(path):
        found = os.stat(path)
        return [found.st_dev, found.st_ino, found.st_size, found.st_mtime_ns, found.st_ctime_ns]

    def catch_up(self):
        """Learn what the log gained since this process last read it (all of it, if rewritten since: forget)."""
        try:
            with self.log.open("rb") as stream:
                inode = os.fstat(stream.fileno()).st_ino
                stream.seek(self.read[1] if self.read[0] == inode else 0)
                data = stream.read()
                end = stream.tell()
        except FileNotFoundError:
            return
        complete = data.rfind(b"\n") + 1
        for line in data[:complete].splitlines():
            with contextlib.suppress(ValueError):  # cut short by a crash
                path, *entry = json.loads(line)
                self.known[path] = entry
        self.read = (inode, end - len(data) + complete)

    def remember(self, path, sha256, identity=None):
        self.known[path] = entry = [*(identity or self.identity(path)), sha256]
        self.log.parent.mkdir(parents=True, exist_ok=True)
        with self.log.open("a") as stream:  # one line per write: appends from processes never interleave
            stream.write(json.dumps([path, *entry]) + "\n")
        return sha256

    def __call__(self, path):
        """path's sha256: known, or hashed now. Its identity is read first, so a write while it is hashed leaves the
        entry already stale."""
        path = os.path.abspath(path)
        identity = self.identity(path)
        if self.known.get(path, [])[:5] != identity:
            self.catch_up()
        entry = self.known.get(path, [])
        return entry[5] if entry[:5] == identity else self.remember(path, digest(path), identity)

    def copied(self, source, target):
        """target was given source's bytes: known by them, if source was still the file hashed when it was."""
        entry = self.known.get(os.path.abspath(source), [])
        if entry[:5] == self.identity(source):
            self.remember(os.path.abspath(target), entry[5])

    def moved(self, old, new):
        """Folder old was renamed new: its files are known there."""
        old, new = os.path.abspath(old) + os.sep, os.path.abspath(new) + os.sep
        for path, entry in list(self.known.items()):
            if path.startswith(old):
                with contextlib.suppress(FileNotFoundError):
                    if self.identity(moved := new + path[len(old):]) == entry[:5]:
                        self.remember(moved, entry[5], entry[:5])

    def forget(self):
        """Rewrite the log with what still holds; only while no other process appends (a build's end)."""
        self.catch_up()
        lines = []
        for path, entry in self.known.items():
            with contextlib.suppress(FileNotFoundError):
                if self.identity(path) == entry[:5]:
                    lines.append(json.dumps([path, *entry]) + "\n")
        temporary = self.log.with_name(self.log.name + ".tmp")
        temporary.write_text("".join(lines))
        temporary.rename(self.log)


DIGESTS = Digests(ROOT / ".cache/digests.jsonl")


FICLONE = 0x40049409  # linux/fs.h: make the target share the source's blocks


def clone(source, target, read_only=False):
    """target, a file of its own with source's bytes, known by source's sha256: sharing its blocks where the
    filesystem can (btrfs), copied where it cannot (tmpfs, as /tmp is) or across filesystems."""
    DIGESTS(source)
    with open(source, "rb") as original, open(target, "wb") as copy:
        try:
            fcntl.ioctl(copy.fileno(), FICLONE, original.fileno())
        except OSError as error:
            if error.errno not in (errno.EOPNOTSUPP, errno.EXDEV):
                raise
            shutil.copyfileobj(original, copy)
    if read_only:
        Path(target).chmod(0o444)
    DIGESTS.copied(source, target)


CODE = {}  # (root, script): its code_closure, read once per process: code does not change during a build (Store.save)


def descriptor(root, stage, script, args, inputs, toolchain):
    """What a stage's result is made of: inputs maps logical names to the files it reads; args are its arguments,
    without the paths among them (their files are inputs, by content, wherever the checkout is)."""
    code = CODE.get((str(root), script)) or CODE.setdefault((str(root), script), code_closure(root, script))
    return {"stage": stage, "args": [arg for arg in args if not (isinstance(arg, str) and Path(arg).is_absolute())],
            "code": code, "inputs": {name: DIGESTS(path) for name, path in sorted(inputs.items())},
            "toolchain": toolchain}


class Store:
    def __init__(self, folder):
        self.folder = Path(folder)

    def place(self, recipe):
        key = hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()
        return self.folder / recipe["stage"] / key

    def restore(self, recipe, build, outputs=None):
        """Clone the stored result's outputs into build and return {"value": ..., "outputs": {name: [size, sha256]}};
        None, and the result discarded, unless it is this recipe's, with exactly these outputs (or, for a stage whose
        outputs are what it wrote, those it stored), each intact."""
        place = self.place(recipe)
        manifest = place / "manifest.json"
        if not manifest.is_file():
            return None
        try:
            stored = json.loads(manifest.read_text())
        except json.JSONDecodeError:
            stored = None
        if stored is not None and outputs is None:
            outputs = list(stored["outputs"])
        intact = stored is not None and stored["recipe"] == recipe and set(stored["outputs"]) == set(outputs) and all(
            (path := place / "outputs" / name).is_file() and path.stat().st_size == size and DIGESTS(path) == sha256
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
        return {"value": stored["value"], "outputs": stored["outputs"]}

    def save(self, recipe, build, outputs, value=None):
        """Keep clones of the named files of build, read-only, and a JSON value as this recipe's result; none when a
        file of its code is not, byte for byte, what its recipe names, as this process started and on disk now: the
        result may be another code's than its key names."""
        if changed := [name for name, sha256 in recipe["code"].items() if not (
                sha256 == AT_START.get(name) and (ROOT / name).is_file() and digest(ROOT / name) == sha256)]:
            raise RuntimeError(f"{', '.join(changed)} changed while the world was building. Build the world again.")
        place = self.place(recipe)
        partial = place.with_name(place.name + ".part")
        shutil.rmtree(partial, ignore_errors=True)
        files = {}
        for name in outputs:
            kept = partial / "outputs" / name
            kept.parent.mkdir(parents=True, exist_ok=True)
            clone(Path(build) / name, kept, read_only=True)
            files[name] = [kept.stat().st_size, DIGESTS(kept)]
        manifest = {"recipe": recipe, "outputs": files, "value": value}
        (partial / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
        shutil.rmtree(place, ignore_errors=True)
        partial.rename(place)
        DIGESTS.moved(partial, place)
        return {"value": value, "outputs": files}

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
