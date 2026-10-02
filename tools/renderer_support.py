"""Linux renderer capabilities and identity, shared by the launcher and builder."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RELEASE = "R2025a"
COMMIT = "c6793d8f7230a311c4bc2a3101d9f1a8bc0aa01b"
SCHEMA = 2
NATIVE_NAMES = ("native/vertex_index.hpp", "native/meshoptimizer/meshoptimizer.h",
                "native/meshoptimizer/vcacheoptimizer.cpp", "native/meshoptimizer/allocator.cpp")
# Only host capabilities used by glad, wren and webots; Qt and other SDK headers
# come from the installed runtime or the builder's pinned downloads.
PROBES = (
    ("OpenGL", "GL/gl.h", "glGetError()", "GL", ()),
    ("GLU", "GL/glu.h", "gluErrorString(0)", "GLU", ()),
    ("OpenAL", "AL/al.h", "alGetError()", "openal", ()),
    ("FreeType", "ft2build.h", "FT_Library library; FT_Init_FreeType(&library)",
     "freetype", ("-I/usr/include/freetype2",)),
    ("zlib", "zlib.h", "zlibVersion()", "z", ()),
)


# Files in the folders under resources/ (shaders, node definitions) are what the runtime links from the
# checkout's working tree; the files at its top include the Makefiles the build reads.
LINKED_RESOURCES = "resources/*/**"


def checkout_identity(source):
    """Include staged and unstaged tracked edits so builds can test a patch in progress. Edits to linked
    resources are left out: one the runtime serves is live (renderer_problem checks that it is)."""
    head = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True, stderr=subprocess.PIPE).strip()
    diff = subprocess.check_output(["git", "-C", str(source), "diff", "--binary", "--no-ext-diff",
                                    "--no-textconv", "HEAD", "--", ".", ":(exclude,glob)" + LINKED_RESOURCES],
                                   stderr=subprocess.PIPE)
    return {"checkout_commit": head, "checkout_diff_sha256": hashlib.sha256(diff).hexdigest()}


def renderer_identity(root=ROOT):
    root = Path(root)
    patches = sorted((root / "native/patches").glob("*.patch"))
    return {
        "schema": SCHEMA,
        "release": RELEASE,
        "upstream_commit": COMMIT,
        "patches": [p.name for p in patches],
        "series_sha256": hashlib.sha256(b"".join(p.read_bytes() for p in patches)).hexdigest(),
        "native_sha256": hashlib.sha256(b"".join((root / p).read_bytes() for p in NATIVE_NAMES)).hexdigest(),
        **checkout_identity(root / ".cache/webots-source"),
    }


def sdk_problems(home):
    home = Path(home)
    missing = []
    version = home / "resources/version.txt"
    try:
        current_release = version.read_text().strip()
    except (OSError, ValueError):
        current_release = None
    if current_release != RELEASE:
        missing.append(f"installed SDK: extract Webots {RELEASE} to {home} (builder ignores WEBOTS_HOME)")
    for name in ("webots", "bin/webots-bin", "include/controller/c/webots/robot.h",
                 "include/ode/ode/ode.h", "include/qt/QtCore/QtCore/qglobal.h", "lib/webots/libQt6Core.so"):
        if not (home / name).is_file():
            missing.append("installed SDK missing: " + name)
    for name in ("webots", "bin/webots-bin"):
        if (home / name).is_file() and not os.access(home / name, os.X_OK):
            missing.append("installed SDK executable permission missing: " + name)
    return missing


def renderer_problem(home, root=ROOT):
    """Return a concise reason, or None for a complete, current patched runtime."""
    home = Path(home)
    manifest = home / "ironlark-renderer.json"
    if not manifest.is_file():
        return "stock Webots or missing patched-renderer manifest"
    try:
        recorded = json.loads(manifest.read_text())
        if not isinstance(recorded, dict):
            return "malformed patched-renderer manifest"
        if type(recorded.get("schema")) is not int or recorded["schema"] != SCHEMA:
            return "unsupported or missing renderer manifest schema"
        expected = renderer_identity(root)
        differences = [key for key, value in expected.items() if recorded.get(key) != value]
        if differences:
            return "stale patched renderer (" + ", ".join(differences) + ")"
        source = Path(root) / ".cache/webots-source"
        edited = subprocess.check_output(["git", "-C", str(source), "diff", "-z", "--name-only", "HEAD", "--",
                                          ":(glob)" + LINKED_RESOURCES], text=True, stderr=subprocess.PIPE)
        for name in filter(None, edited.split("\0")):
            if os.path.realpath(home / name) != os.path.realpath(source / name):
                return f"stale patched renderer ({name} is edited but served from stock Webots)"
        if (home / "resources/version.txt").read_text().strip() != RELEASE:
            return f"patched runtime must be Webots {RELEASE}"
        if not all((home / path).is_file() and os.access(home / path, os.X_OK)
                   for path in ("webots", "bin/webots-bin")):
            return "incomplete patched renderer (missing executable)"
    except (OSError, ValueError, TypeError, subprocess.CalledProcessError):
        return "malformed or incomplete patched-renderer manifest/runtime"
    return None


def select_webots(root=ROOT, world="empty", override=None):
    root = Path(root)
    patched = root / ".cache/webots-renderer"
    home = Path(override).resolve() if override else (
        patched if world == "forest" or renderer_problem(patched, root) is None else root / "webots")
    if world == "forest" and (problem := renderer_problem(home, root)):
        prefix = f"WEBOTS_HOME points to {home}: {problem}. " if override else f"{problem}. "
        suffix = ", then rerun with env -u WEBOTS_HOME to select that build" if override else ""
        raise RuntimeError(prefix + "The forest requires Ironlark's patched Webots renderer. "
                           "Run ./ironlark build-renderer first" + suffix + ".")
    if override and not (home / "webots").is_file():
        raise RuntimeError(f"WEBOTS_HOME points to an invalid installation: {home}; remove the override or select Webots {RELEASE}.")
    return home.resolve()


def probe_command(command):
    try:
        return subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def renderer_preflight(root=ROOT):
    missing = sdk_problems(Path(root) / "webots")
    # The Webots Makefiles compile with gcc and g++ and set their own flags.
    with tempfile.TemporaryDirectory(prefix="ironlark-renderer-") as scratch:
        binary = Path(scratch) / "probe"
        compilers = {}
        for language, compiler, extension in (("c", "gcc", "c"), ("c++", "g++", "cpp")):
            source = Path(scratch) / ("compiler." + extension)
            source.write_text("int main(void) { return 0; }\n")
            compilers[language] = probe_command([compiler, str(source), "-o", str(binary)])
            if not compilers[language]:
                missing.append(f"{language} compiler compile/link: {compiler}")
        makefile = Path(scratch) / "Makefile"
        makefile.write_text("all:\n\t@echo ironlark-preflight\n")
        for executable, command in (
            ("make", ["make", "-f", str(makefile)]), ("git", ["git", "--version"]),
            ("uvx", ["uvx", "--version"])):
            if not probe_command(command):
                missing.append(f"working executable: {executable}" + (" (install uv)" if executable == "uvx" else ""))
        if compilers["c++"]:
            source, binary = Path(scratch) / "probe.cpp", Path(scratch) / "probe"
            for label, header, expression, library, flags in PROBES:
                extra = "#include FT_FREETYPE_H\n" if label == "FreeType" else ""
                source.write_text(f"#include <{header}>\n{extra}int main() {{ {expression}; return 0; }}\n")
                if not probe_command(["g++", str(source), *flags, "-l" + library, "-o", str(binary)]):
                    missing.append(f"{label}: compile/link <{header}> with -l{library}")
        else:
            missing.append("Header/library probes require a working C++ compiler; rerun after fixing it")
    return missing


def preflight_report(missing):
    if not missing:
        return "OK      Renderer build prerequisites (compile/link probes passed)"
    return "\n".join(["Renderer build prerequisites missing:", *(f"  - {item}" for item in missing),
                      "Install the missing tools/development libraries, then rerun ./ironlark doctor."])


def require_renderer_prerequisites():
    missing = renderer_preflight()
    if missing:
        raise RuntimeError(preflight_report(missing))
