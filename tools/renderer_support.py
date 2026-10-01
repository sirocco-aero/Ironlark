"""Linux renderer capabilities and identity, shared by the launcher and builder."""

import hashlib
import json
import os
import platform
import re
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass

ROOT = Path(__file__).resolve().parents[1]
RELEASE = "R2025a"
COMMIT = "c6793d8f7230a311c4bc2a3101d9f1a8bc0aa01b"
SCHEMA = 1
NATIVE_NAMES = ("native/vertex_index.hpp", "native/meshoptimizer/meshoptimizer.h",
                "native/meshoptimizer/vcacheoptimizer.cpp", "native/meshoptimizer/allocator.cpp")
# Only host capabilities used by glad, wren and webots; Qt and other SDK headers
# come from the installed runtime or the builder's pinned downloads.
PROBES = (
    ("OpenGL", "GL/gl.h", "glGetError()", "GL", (), ("libgl-dev", "libgl1-mesa-dev")),
    ("GLU", "GL/glu.h", "gluErrorString(0)", "GLU", (), ("libglu1-mesa-dev",)),
    ("OpenAL", "AL/al.h", "alGetError()", "openal", (), ("libopenal-dev",)),
    ("FreeType", "ft2build.h", "FT_Library library; FT_Init_FreeType(&library)",
     "freetype", ("-I/usr/include/freetype2",), ("libfreetype-dev", "libfreetype6-dev")),
    ("zlib", "zlib.h", "zlibVersion()", "z", (), ("zlib1g-dev",)),
)


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
    }


def required_overlay(root=ROOT):
    """Resource paths from the checked-in series, without consulting a checkout."""
    required = set()
    for patch in sorted((Path(root) / "native/patches").glob("*.patch")):
        required.update(re.findall(r"^\+\+\+ b/resources/(.+)$", patch.read_text(), re.M))
        required.difference_update(re.findall(r"^--- a/resources/(.+)\n\+\+\+ /dev/null$",
                                             patch.read_text(), re.M))
    return required


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
        if (home / "resources/version.txt").read_text().strip() != RELEASE:
            return f"patched runtime must be Webots {RELEASE}"
        if not all((home / path).is_file() and os.access(home / path, os.X_OK)
                   for path in ("webots", "bin/webots-bin")):
            return "incomplete patched renderer (missing executable)"
        overlay = recorded.get("resource_overlay")
        if not isinstance(overlay, dict) or not overlay:
            return "missing patched resource overlay"
        if not required_overlay(root) <= set(overlay):
            return "incomplete patched resource overlay"
        for name, digest in overlay.items():
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts:
                return "malformed patched resource overlay"
            path = home / "resources" / relative
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                return "missing or changed patched resource overlay: " + name
        binaries = recorded.get("binaries")
        if not isinstance(binaries, dict):
            return "missing renderer binary identity"
        for name in ("webots", "bin/webots-bin"):
            if binaries.get(name) != hashlib.sha256((home / name).read_bytes()).hexdigest():
                return "changed renderer executable: " + name
    except (OSError, ValueError, TypeError):
        return "malformed or incomplete patched-renderer manifest/runtime"
    return None


def select_webots(root=ROOT, world="empty", override=None):
    root = Path(root)
    patched = root / ".cache/webots-renderer"
    home = Path(override).resolve() if override else (
        patched if world == "forest" or renderer_problem(patched, root) is None else root / "webots")
    if world == "forest" and (problem := renderer_problem(home, root)):
        prefix = f"WEBOTS_HOME points to {home}: {problem}. " if override else f"{problem}. "
        raise RuntimeError(prefix + "The forest requires Ironlark's patched Webots renderer. "
                           "Run ./ironlark build-renderer first; unset WEBOTS_HOME to use that build.")
    if override and not (home / "webots").is_file():
        raise RuntimeError(f"WEBOTS_HOME points to an invalid installation: {home}; unset it or select Webots {RELEASE}.")
    return home.resolve()


def apt_candidate(package):
    """True/False for a known APT result, None when querying is unavailable."""
    if not shutil.which("apt-cache"):
        return None
    try:
        result = subprocess.run(["apt-cache", "policy", package], capture_output=True, text=True, timeout=10,
                                env={**os.environ, "LC_ALL": "C"})
        if result.returncode:
            return None
        for line in result.stdout.splitlines():
            if line.strip().startswith("Candidate:"):
                return line.split(":", 1)[1].strip() != "(none)"
        return False
    except (OSError, subprocess.TimeoutExpired):
        return None


def package_suggestions(groups, candidate=apt_candidate):
    packages, unavailable = [], []
    for alternatives in groups:
        states = [(name, candidate(name)) for name in alternatives]
        chosen = next((name for name, state in states if state is True), None)
        if chosen and chosen not in packages:
            packages.append(chosen)
        elif not chosen:
            unavailable.append("/".join(alternatives))
    return packages, unavailable


@dataclass
class Preflight:
    missing: list
    packages: list
    unavailable: list
    platform: str


def host_platform():
    """Explicit platform policy, without claiming untested host releases."""
    if not sys.platform.startswith("linux"):
        return "unsupported non-Linux platform", False, "Linux x86-64 is required"
    if platform.machine().lower() not in ("x86_64", "amd64"):
        return "unsupported architecture", False, f"Linux x86-64 is required; found {platform.machine()}"
    try:
        release = platform.freedesktop_os_release()
    except OSError:
        release = {}
    distribution = release.get("ID", "unknown")
    version = release.get("VERSION_ID", "unknown")
    apt_family = distribution in ("ubuntu", "debian") or "debian" in release.get("ID_LIKE", "").split()
    if distribution == "ubuntu":
        description = f"Ubuntu {version} host: best effort (22.04 containers are the tested baseline)"
    elif apt_family:
        description = f"{distribution} {version}: Debian-derived host, best effort"
    else:
        description = f"{distribution} {version}: unknown Linux host, capability checks only"
    return description, apt_family, None


def compiler_command(language):
    variable, default = ("CC", "gcc") if language == "c" else ("CXX", "g++")
    if variable not in os.environ:
        alternatives = ("gcc", "cc", "clang") if language == "c" else ("g++", "c++", "clang++")
        default = next((name for name in alternatives if shutil.which(name)), default)
    try:
        command = shlex.split(os.environ.get(variable, default))
        if not command:
            raise RuntimeError(f"Invalid {variable}: empty compiler command")
        return command
    except ValueError as error:
        raise RuntimeError(f"Invalid {variable}: {error}") from error


def probe_command(command):
    try:
        return subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def renderer_preflight():
    description, apt_family, unsupported = host_platform()
    if unsupported:
        return Preflight([unsupported], [], [], description)
    missing, groups = [], []
    missing.extend(sdk_problems(ROOT / "webots"))
    # Webots assigns its own flags. Explicitly reject flag overrides rather than
    # probing options that its Makefiles would silently ignore. Sysroot/include/
    # link options can be embedded in CC/CXX and passed to make unchanged.
    for variable in ("CPPFLAGS", "CFLAGS", "CXXFLAGS", "LDFLAGS"):
        if os.environ.get(variable):
            missing.append(f"{variable} overrides are unsupported; unset it and put custom toolchain options in CC/CXX")
    cppflags, ldflags = [], []
    with tempfile.TemporaryDirectory(prefix="ironlark-renderer-") as scratch:
        binary = Path(scratch) / "probe"
        compilers = {}
        for language, extension in (("c", "c"), ("c++", "cpp")):
            try:
                compiler = compiler_command(language)
            except RuntimeError as error:
                compilers[language] = False
                missing.append(str(error))
                continue
            source = Path(scratch) / ("compiler." + extension)
            source.write_text("int main(void) { return 0; }\n")
            working = probe_command([*compiler, *cppflags, str(source), *ldflags, "-o", str(binary)])
            compilers[language] = working
            if not working:
                missing.append(f"{language} compiler compile/link: {' '.join(compiler)}")
                groups.append(("build-essential",))
        makefile = Path(scratch) / "Makefile"
        makefile.write_text("all:\n\t@echo ironlark-preflight\n")
        for executable, command, packages in (
            ("make", ["make", "-f", str(makefile)], ("build-essential",)),
            ("git", ["git", "--version"], ("git",)), ("uvx", ["uvx", "--version"], ())):
            if not probe_command(command):
                missing.append(f"working executable: {executable}" + (" (install uv)" if executable == "uvx" else ""))
                if packages:
                    groups.append(packages)
        if compilers["c++"]:
            source, binary = Path(scratch) / "probe.cpp", Path(scratch) / "probe"
            for label, header, expression, library, flags, packages in PROBES:
                extra = "#include FT_FREETYPE_H\n" if label == "FreeType" else ""
                source.write_text(f"#include <{header}>\n{extra}int main() {{ {expression}; return 0; }}\n")
                if not probe_command([*compiler_command("c++"), *cppflags,
                                      str(source), *flags, *ldflags, "-l" + library, "-o", str(binary)]):
                    missing.append(f"{label}: compile/link <{header}> with -l{library}")
                    groups.append(packages)
        else:
            missing.append("Header/library probes require a working C++ compiler; rerun after fixing it")
    packages, unavailable = package_suggestions(groups) if apt_family else ([], [])
    return Preflight(missing, packages, unavailable, description)


def preflight_report(result):
    missing, packages, unavailable = result.missing, result.packages, result.unavailable
    if not missing:
        return result.platform + "\nOK      Renderer build prerequisites (compile/link probes passed)"
    lines = [result.platform, "Renderer build prerequisites missing:", *(f"  - {item}" for item in missing)]
    if packages:
        lines += ["APT suggestion (run yourself):", "  sudo apt install " + " ".join(packages)]
    if unavailable:
        lines.append("APT candidate unavailable/unverified: " + ", ".join(unavailable) +
                     "; install equivalent development capabilities using your host's package tools.")
    return "\n".join(lines)


def require_renderer_prerequisites():
    result = renderer_preflight()
    if result.missing:
        raise RuntimeError(preflight_report(result))
