#!/usr/bin/env python3
"""Save the Webots source edits as the newest patch of native/patches.

    .venv/bin/python tools/save_webots_patch.py 0012-name.patch   # new patch
    .venv/bin/python tools/save_webots_patch.py                   # amend the newest patch

The patch is the difference between the sources as the earlier patches leave them
(rebuilt in a scratch repository from upstream files, never touching the working
sources) and the working sources now. The series is then staged in the working
sources, so ./ironlark build-renderer can reapply it safely.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".cache/webots-source"
PATCHES = ROOT / "native/patches"
TREES = ("src", "include", "resources")


def git(*args, cwd=SOURCE, **kwargs):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True, **kwargs).stdout


def touched(patch):
    return {line.split(" b/", 1)[1] for line in patch.read_text(errors="replace").splitlines() if line.startswith("diff --git ")}


def main():
    series = sorted(PATCHES.glob("*.patch"))
    target = PATCHES / sys.argv[1] if len(sys.argv) > 1 else series[-1]
    earlier = [p for p in series if p.name < target.name]
    if any(p.name > target.name for p in series):
        raise SystemExit(f"{target.name} is not the newest patch; only the newest can be saved.")

    # New files count once registered with `git add -N`; other untracked files (like the
    # builder's copy of native/vertex_index.hpp) are not part of any patch.
    changed = set(git("diff", "--name-only", "HEAD", "--", *TREES).split())
    files = changed | set().union(*(touched(p) for p in earlier)) if earlier else changed

    with tempfile.TemporaryDirectory() as scratch:
        base = Path(scratch)
        git("init", "-q", cwd=base)
        for name in files:
            blob = subprocess.run(["git", "-C", str(SOURCE), "show", f"HEAD:{name}"], capture_output=True)
            if blob.returncode == 0:
                (base / name).parent.mkdir(parents=True, exist_ok=True)
                (base / name).write_bytes(blob.stdout)
        git("add", "-A", cwd=base)
        for patch in earlier:
            git("apply", "--index", str(patch), cwd=base)
        git("-c", "user.name=ironlark", "-c", "user.email=ironlark@localhost", "commit", "-q", "--allow-empty", "-m", "earlier",
            cwd=base)
        for name in changed:
            current, copy = SOURCE / name, base / name
            if current.exists():
                copy.parent.mkdir(parents=True, exist_ok=True)
                copy.write_bytes(current.read_bytes())
            elif copy.exists():
                copy.unlink()
        git("add", "-A", cwd=base)
        diff = git("diff", "--cached", "--binary", cwd=base)

    if not diff:
        raise SystemExit("No edits beyond the earlier patches.")
    target.write_text(diff)
    git("add", "-A", "--", *(n for n in changed if (SOURCE / n).exists()))
    git("add", "-u", "--", *TREES)
    print(f"Saved {target.relative_to(ROOT)}: {len(touched(target))} files")


if __name__ == "__main__":
    main()
