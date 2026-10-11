"""What a built artifact was built from, so a check never runs on one built from other code.

The forest world's receipt (worlds/pine_forest/receipt.json, written last by tools/forest_pipeline.py) records the
sha256 of every file of the pipeline's code: forest_pipeline.py, each script it runs, and the local modules they
import (found by reading them, so no list to keep), which also pins Blender and the source scene by checksum; the
versions of the Python packages that compute the world; and every output file's size and sha256. Standard library
only: the launcher runs on the system Python.
"""
import ast
import hashlib
import json
from pathlib import Path
import re

PACKAGES = ("numpy", "pillow", "etcpak")


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def packages(root, names=PACKAGES):
    """{name: version} as installed in the project's environment (.venv), None where missing."""
    installed = {}
    for info in Path(root, ".venv/lib").glob("python*/site-packages/*.dist-info"):
        name, _, version = info.name[: -len(".dist-info")].partition("-")
        installed[name.lower()] = version
    return {name: installed.get(name) for name in names}


def code_closure(root, entry, named_scripts=False, leave_out=()):
    """sha256 of every file of a script's code: itself and its imports from tools/ or from a repository directory a
    script names (build_forest_world.py adds controllers/flight_observer), and with named_scripts the tools scripts
    it names too (the pipeline's stages), but those in leave_out."""
    root = Path(root)
    folders = [root / "tools"]

    def module(name):
        return next((folder / f"{name}.py" for folder in folders if (folder / f"{name}.py").is_file()), None)

    def constants(tree):
        return [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]

    first = root / entry
    pending = [first]
    if named_scripts:
        pending += [path for value in constants(ast.parse(first.read_text())) if value.endswith(".py")
                    and value not in leave_out and (path := module(value[:-3]))]
    found = set()
    while pending:
        path = pending.pop()
        if path in found:
            continue
        found.add(path)
        tree = ast.parse(path.read_text())
        folders += [root / value for value in constants(tree) if re.fullmatch(r"[\w.-]+(/[\w.-]+)+", value)
                    and (root / value).is_dir() and root / value not in folders]
        for node in ast.walk(tree):
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else \
                [node.module] if isinstance(node, ast.ImportFrom) and node.module else []
            pending += [found_path for name in names if (found_path := module(name.split(".")[0]))]
    return {str(path.relative_to(root)): digest(path) for path in sorted(found)}


def pipeline_code(root):
    """The forest pipeline's code: tools/forest_pipeline.py, the tools scripts it names, and what they import."""
    return code_closure(root, "tools/forest_pipeline.py", named_scripts=True)


def recipe(root):
    """What a forest build is made from: the pipeline's code and the packages it runs on."""
    return {"code": pipeline_code(root), "packages": packages(root)}


def differences(recorded, current):
    """How the current recipe differs from a recorded one: each file of code and each package, named."""
    changed = [f"{name} changed" for name in sorted(set(recorded["code"]) | set(current["code"]))
               if recorded["code"].get(name) != current["code"].get(name)]
    return changed + [f"{name} {recorded['packages'].get(name)} is now {version}"
                      for name, version in current["packages"].items() if recorded["packages"].get(name) != version]


def write_world(world, built_from):
    """The world's receipt, written after its build succeeded: the recipe it was built from (read when the build began)
    and every output file."""
    world = Path(world)
    outputs = {str(path.relative_to(world)): [path.stat().st_size, digest(path)]
               for path in sorted(world.rglob("*")) if path.is_file() and path.name != "receipt.json"}
    temporary = world / "receipt.json.tmp"
    temporary.write_text(json.dumps({**built_from, "outputs": outputs}, indent=1) + "\n")
    temporary.rename(world / "receipt.json")


def world_problems(root, world, contents=False):
    """Why the world is not what today's code builds, none if it is: its recipe and its outputs (their sizes, or with
    contents their sha256 too, about 4 s for the 1.4 GB forest)."""
    world = Path(world)
    receipt = world / "receipt.json"
    if not receipt.is_file():
        return ["it has no receipt: built before receipts, or not finished"]
    recorded = json.loads(receipt.read_text())
    problems = differences(recorded, recipe(root))
    for name, (size, sha256) in recorded["outputs"].items():
        path = world / name
        if not path.is_file() or path.stat().st_size != size or (contents and digest(path) != sha256):
            problems.append(f"{name} is not the file the build wrote")
    return problems
