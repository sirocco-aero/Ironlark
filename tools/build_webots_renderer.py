"""Build Ironlark's rendering-only Webots patch against the installed R2025a SDK."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
from urllib.request import urlretrieve

from forest_process import run_stage

SIM = Path(__file__).resolve().parents[1]
CACHE = SIM / ".cache"
COMMIT = "c6793d8f7230a311c4bc2a3101d9f1a8bc0aa01b"
PACKAGES = {
    "libOIS.1.4.tar.bz2": "ec13db6efd6901e80e9c4b437319c7949253a47fee768515a3aa1e6ca122225d",
    "libassimp-5.2.3.tar.bz2": "31c12e4e9f6bf52259dc599c8fcdb77c511c170e18cdc70543b10903aa28c697",
    "libpico.tar.bz2": "56870e42ce411f4e596b5859d373ae07e86dcca1c308c1226ec5ecd694d6ab56",
}


def call(*args):
    subprocess.run(list(map(str, args)), check=True)


def link(path, target):
    if not path.exists() and not path.is_symlink():
        path.symlink_to(target, target_is_directory=target.is_dir())


def overlay_resources(runtime, installed, source):
    """Mirror stock resources as symlinks, serving files the series changed
    (shaders, node definitions) from the patched source."""
    changed = subprocess.check_output(
        # Working tree, not just the staged series: a patch in progress is live too.
        ["git", "-C", str(source), "diff", "--name-only", "HEAD", "--", "resources"],
        text=True,
    ).split()
    changed = {Path(name).relative_to("resources") for name in changed}
    parents = {parent for name in changed for parent in name.parents if parent != Path(".")}
    root = runtime / "resources"
    if root.is_symlink():
        root.unlink()
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir()

    def mirror(folder):
        for child in (installed / "resources" / folder).iterdir():
            name = folder / child.name
            if name in parents:
                (root / name).mkdir()
                mirror(name)
            elif name in changed:
                link(root / name, source / "resources" / name)
            else:
                link(root / name, child)

    mirror(Path("."))
    for name in changed:  # files the series adds
        link(root / name, source / "resources" / name)


def main():
    source = CACHE / "webots-source"
    installed = SIM / "webots"
    if (installed / "resources/version.txt").read_text().strip() != "R2025a":
        raise RuntimeError("This patch requires the Webots R2025a installation.")
    if not source.exists():
        call(
            "git",
            "clone",
            "--depth",
            "1",
            "--branch",
            "R2025a",
            "--filter=blob:none",
            "--sparse",
            "https://github.com/cyberbotics/webots.git",
            source,
        )
        call(
            "git",
            "-C",
            source,
            "sparse-checkout",
            "set",
            "src",
            "include",
            "resources",
            "scripts",
            "dependencies",
        )
    head = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    if head != COMMIT:
        raise RuntimeError(f"Unexpected Webots checkout: {head}")
    call(
        "git",
        "-C",
        source,
        "submodule",
        "update",
        "--init",
        "--depth",
        "1",
        "src/glm",
        "src/stb",
    )
    # Patches apply in filename order to pristine sources, so the series can
    # grow without a fork. Staging them keeps `git diff` to new edits only;
    # a stamp avoids reapplying (and recompiling) them.
    patches = sorted((SIM / "native/patches").glob("*.patch"))
    series = hashlib.sha256(b"".join(p.read_bytes() for p in patches)).hexdigest()
    stamp = source / ".ironlark-patches"
    if not stamp.exists() or stamp.read_text().strip() != series:
        # Reapplying resets the sources: never discard edits not yet saved as a patch.
        unsaved = subprocess.check_output(
            ["git", "-C", str(source), "diff", "--name-only", "--", "src", "include", "resources"], text=True
        ).split()
        if unsaved and stamp.exists():
            raise RuntimeError(
                "Webots sources have edits not saved as a patch; save them to native/patches first: "
                + ", ".join(unsaved)
            )
        call("git", "-C", source, "checkout", "HEAD", "--", "src", "include", "resources")
        # Files the series adds survive the checkout; remove them so it reapplies.
        for patch in patches:
            summary = subprocess.check_output(
                ["git", "-C", str(source), "apply", "--summary", str(patch)], text=True
            )
            for line in summary.splitlines():
                if line.strip().startswith("create mode"):
                    added = line.split()[-1]
                    call("git", "-C", source, "rm", "-q", "-f", "--cached", "--ignore-unmatch", added)
                    (source / added).unlink(missing_ok=True)
        for patch in patches:
            call("git", "-C", source, "apply", "--index", patch)
        stamp.write_text(series + "\n")
    shutil.copyfile(
        SIM / "native/vertex_index.hpp", source / "src/wren/vertex_index.hpp"
    )
    for name, expected in PACKAGES.items():
        archive = source / "dependencies" / name
        if not archive.exists():
            urlretrieve(
                "https://cyberbotics.com/files/repository/dependencies/linux64/release/"
                + name,
                archive,
            )
        with archive.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                raise RuntimeError(f"Unexpected dependency archive contents: {name}")
        with tarfile.open(archive) as tar:
            tar.extractall(
                source,
                members=[m for m in tar if m.name.startswith("include/")],
                filter="data",
            )
    # The runtime package omits three header modules. Obtain the matching Qt
    # headers without replacing its working Qt libraries or the system Qt.
    qt = CACHE / "Qt/6.5.3/gcc_64/include"
    if not (qt / "QtOpenGLWidgets/QOpenGLWidget").exists():
        call(
            "uvx",
            "--from",
            "aqtinstall==3.3.0",
            "aqt",
            "install-qt",
            "linux",
            "desktop",
            "6.5.3",
            "gcc_64",
            "--outputdir",
            CACHE / "Qt",
            "--archives",
            "qtbase",
            "qtdeclarative",
        )
    include = source / "include/qt"
    include.mkdir(exist_ok=True)
    for module in (installed / "include/qt").iterdir():
        link(include / module.name, module)
    for name in ("QtOpenGLWidgets", "QtQml", "QtXml"):
        (include / name).mkdir(exist_ok=True)
        link(include / name / name, qt / name)
    (source / "bin").mkdir(exist_ok=True)
    link(source / "bin/qt", installed / "bin/qt")
    link(source / "lib", installed / "lib")
    link(source / "dependencies/libOIS.so", installed / "lib/webots/libOIS-1.4.0.so")
    logs = CACHE / "build-logs"
    for name in ("glad", "wren", "webots"):
        print(f"Compiling {name} (two jobs)", flush=True)
        run_stage(
            [
                "make",
                "-C",
                str(source / "src" / name),
                "-j2",
                "release",
                "WEBOTS_HOME=" + str(source),
                "LD_FLAGS=-rdynamic -L" + str(source / "dependencies"),
            ],
            logs / f"{name}-build.log",
        )
    # A separate installation keeps the stock binary available for comparison.
    runtime = CACHE / "webots-renderer"
    runtime.mkdir(exist_ok=True)
    for path in installed.iterdir():
        if path.name not in ("bin", "webots", "resources"):
            link(runtime / path.name, path)
    overlay_resources(runtime, installed, source)
    (runtime / "bin").mkdir(exist_ok=True)
    for path in (installed / "bin").iterdir():
        if path.name != "webots-bin":
            link(runtime / "bin" / path.name, path)
    shutil.copy2(source / "bin/webots-bin", runtime / "bin/webots-bin")
    shutil.copy2(installed / "webots", runtime / "webots")
    manifest = {
        "upstream_commit": COMMIT,
        "patches": [p.name for p in patches],
        "series_sha256": series,
        "vertex_index_sha256": hashlib.sha256(
            (SIM / "native/vertex_index.hpp").read_bytes()
        ).hexdigest(),
    }
    (runtime / "ironlark-renderer.json").write_text(json.dumps(manifest, indent=2))
    print(f"Patched Webots ready: {runtime}", flush=True)


if __name__ == "__main__":
    main()
