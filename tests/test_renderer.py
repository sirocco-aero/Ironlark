"""Offline renderer onboarding and publishing regression tests."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import renderer_support as renderer
import build_webots_renderer as builder


class Prerequisites(unittest.TestCase):
    def setUp(self):
        sdk = patch.object(renderer, "sdk_problems", return_value=[])
        sdk.start()
        self.addCleanup(sdk.stop)
        environment = patch.dict(renderer.os.environ, {"CC": "gcc", "CXX": "g++", "CPPFLAGS": "",
                                                      "CFLAGS": "", "CXXFLAGS": "", "LDFLAGS": ""})
        environment.start()
        self.addCleanup(environment.stop)
    def test_all_missing_libraries_reported_together(self):
        with patch.object(renderer, "host_platform", return_value=("test", True, None)), \
             patch.object(renderer, "probe_command", side_effect=lambda command: "probe.cpp" not in " ".join(command)) as run, \
             patch.object(renderer, "package_suggestions", return_value=([], [])):
            missing = renderer.renderer_preflight().missing
        for label in ("OpenGL", "GLU", "OpenAL", "FreeType", "zlib"):
            self.assertTrue(any(item.startswith(label + ":") for item in missing))
        self.assertEqual(run.call_count, len(renderer.PROBES) + 5)

    def test_package_alternative_and_unavailable_packages(self):
        available = {"libfreetype-dev": True, "libfreetype6-dev": False,
                     "libssh-gcrypt-dev": False, "libssh-dev": True, "absent": False}
        packages, unavailable = renderer.package_suggestions(
            [("libfreetype6-dev", "libfreetype-dev"), ("libssh-gcrypt-dev", "libssh-dev"), ("absent",)],
            available.get)
        self.assertEqual(packages, ["libfreetype-dev", "libssh-dev"])
        self.assertEqual(unavailable, ["absent"])

    def test_unknown_apt_uses_conservative_fallback(self):
        self.assertEqual(renderer.package_suggestions([("libgl-dev", "libgl1-mesa-dev")],
                                                     lambda name: None), ([], ["libgl-dev/libgl1-mesa-dev"]))

    def test_apt_unavailable_or_no_candidate(self):
        with patch.object(renderer.shutil, "which", return_value=None):
            self.assertIsNone(renderer.apt_candidate("package"))
        for output, expected in (("Candidate: (none)\n", False), ("Candidate: 1.2\n", True), ("", False)):
            with patch.object(renderer.shutil, "which", return_value="apt-cache"), \
                 patch.object(renderer.subprocess, "run") as run:
                run.return_value.returncode = 0
                run.return_value.stdout = output
                self.assertIs(renderer.apt_candidate("package"), expected)

    def test_alternative_compilers_and_invalid_quote(self):
        with patch.dict(renderer.os.environ, {}, clear=True), \
             patch.object(renderer.shutil, "which", side_effect=lambda name: name if name in ("cc", "c++") else None):
            self.assertEqual(renderer.compiler_command("c"), ["cc"])
            self.assertEqual(renderer.compiler_command("c++"), ["c++"])
        with patch.dict(renderer.os.environ, {"CC": "'invalid"}), \
             patch.object(renderer, "probe_command", return_value=False), \
             patch.object(renderer, "package_suggestions", return_value=([], [])):
            result = renderer.renderer_preflight()
            self.assertTrue(any("Invalid CC" in item for item in result.missing))
            self.assertTrue(any("make" in item for item in result.missing))

    def test_unsupported_platform_fails_before_probing(self):
        for system, machine in (("darwin", "x86_64"), ("linux", "aarch64")):
            with patch.object(renderer.sys, "platform", system), \
                 patch.object(renderer.platform, "machine", return_value=machine), \
                 patch.object(renderer, "probe_command") as probe:
                result = renderer.renderer_preflight()
                self.assertTrue(result.missing)
                probe.assert_not_called()

    def test_unknown_linux_never_suggests_apt(self):
        with patch.object(renderer.platform, "freedesktop_os_release", return_value={"ID": "alien"}), \
             patch.object(renderer, "probe_command", return_value=False), \
             patch.object(renderer, "package_suggestions") as suggestions:
            result = renderer.renderer_preflight()
            self.assertTrue(result.missing)
            self.assertEqual(result.packages, [])
            suggestions.assert_not_called()

    def test_custom_compiler_and_link_failure(self):
        commands = []
        def probe(command):
            commands.append(command)
            return "-lGL" not in command
        with patch.dict(renderer.os.environ, {"CC": "cc --sysroot=/custom", "CXX": "'compiler with spaces' --sysroot=/custom"}), \
             patch.object(renderer, "probe_command", side_effect=probe), \
             patch.object(renderer, "package_suggestions", return_value=([], [])):
            result = renderer.renderer_preflight()
        self.assertEqual(len(result.missing), 1)
        self.assertIn("OpenGL", result.missing[0])
        self.assertTrue(any(command[0] == "compiler with spaces" and "--sysroot=/custom" in command for command in commands))

    def test_preflight_precedes_downloads(self):
        with patch.object(builder, "require_renderer_prerequisites", side_effect=RuntimeError("missing")), \
             patch.object(builder, "call") as call:
            with self.assertRaisesRegex(RuntimeError, "missing"):
                builder.main()
            call.assert_not_called()


class Selection(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        for name in renderer.NATIVE_NAMES:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name)
        patches = self.root / "native/patches"
        patches.mkdir()
        (patches / "0001.patch").write_text("patch")
        self.stock = self.root / "webots"
        self.home = self.root / ".cache/webots-renderer"
        for home in (self.stock, self.home):
            (home / "resources").mkdir(parents=True)
            (home / "resources/version.txt").write_text(renderer.RELEASE)
            (home / "bin").mkdir()
            for name in ("webots", "bin/webots-bin"):
                (home / name).write_text("executable")
                (home / name).chmod(0o755)
        self.manifest = self.home / "ironlark-renderer.json"

    def current(self):
        overlay_path = self.home / "resources/nodes/Background.wrl"
        overlay_path.parent.mkdir(parents=True, exist_ok=True)
        overlay_path.write_text("patched")
        overlay = {"nodes/Background.wrl": hashlib.sha256(overlay_path.read_bytes()).hexdigest()}
        self.manifest.write_text(json.dumps({**renderer.renderer_identity(self.root), "resource_overlay": overlay,
            "binaries": {name: hashlib.sha256((self.home / name).read_bytes()).hexdigest()
                         for name in ("webots", "bin/webots-bin")}}))

    def test_forest_rejects_stock_override(self):
        self.current()
        with self.assertRaisesRegex(RuntimeError, "WEBOTS_HOME.*stock Webots"):
            renderer.select_webots(self.root, "forest", str(self.stock))

    def test_forest_rejects_missing_manifest(self):
        with self.assertRaisesRegex(RuntimeError, "build-renderer"):
            renderer.select_webots(self.root, "forest")

    def test_forest_rejects_malformed_manifest(self):
        for contents in ("", "{", "[]", "null", '{"build_in_progress": true}'):
            self.manifest.write_text(contents)
            with self.assertRaises(RuntimeError):
                renderer.select_webots(self.root, "forest")

    def test_manifest_metadata_and_actual_release(self):
        for field in ("resource_overlay", "binaries"):
            self.current()
            recorded = json.loads(self.manifest.read_text())
            del recorded[field]
            self.manifest.write_text(json.dumps(recorded))
            with self.assertRaises(RuntimeError):
                renderer.select_webots(self.root, "forest", str(self.home))
        self.current()
        recorded = json.loads(self.manifest.read_text())
        recorded["schema"] = True
        self.manifest.write_text(json.dumps(recorded))
        with self.assertRaisesRegex(RuntimeError, "schema"):
            renderer.select_webots(self.root, "forest")
        self.current()
        (self.home / "resources/version.txt").write_text("R2024a")
        with self.assertRaisesRegex(RuntimeError, "R2025a"):
            renderer.select_webots(self.root, "forest")

    def test_missing_override_directory(self):
        with self.assertRaisesRegex(RuntimeError, "WEBOTS_HOME"):
            renderer.select_webots(self.root, "empty", str(self.root / "missing"))

    def test_incomplete_installed_sdk_reports_all_missing_parts(self):
        missing = renderer.sdk_problems(self.stock)
        self.assertTrue(any("include/ode" in item for item in missing))
        self.assertTrue(any("libQt6Core" in item for item in missing))

    def test_every_identity_field_is_checked(self):
        for field in renderer.renderer_identity(self.root):
            recorded = renderer.renderer_identity(self.root)
            recorded[field] = "stale"
            self.manifest.write_text(json.dumps(recorded))
            with self.assertRaisesRegex(RuntimeError, field):
                renderer.select_webots(self.root, "forest")

    def test_changed_native_source_and_patch_rejected(self):
        for name in (renderer.NATIVE_NAMES[0], "native/patches/0001.patch"):
            self.current()
            (self.root / name).write_text("changed")
            with self.assertRaisesRegex(RuntimeError, "stale"):
                renderer.select_webots(self.root, "forest")

    def test_empty_allows_stock_even_with_stale_renderer(self):
        self.manifest.write_text("{}")
        self.assertEqual(renderer.select_webots(self.root), self.stock)
        self.assertEqual(renderer.select_webots(self.root, "empty", str(self.stock)), self.stock)

    def test_current_manifest_accepted(self):
        self.current()
        self.assertEqual(renderer.select_webots(self.root, "forest"), self.home)

    def test_incomplete_runtime_rejected(self):
        self.current()
        (self.home / "bin/webots-bin").chmod(0o644)
        with self.assertRaisesRegex(RuntimeError, "missing executable"):
            renderer.select_webots(self.root, "forest")

    def test_required_overlay_cannot_be_omitted(self):
        (self.root / "native/patches/0001.patch").write_text("+++ b/resources/wren/shaders/pbr.frag\n")
        self.current()
        with self.assertRaisesRegex(RuntimeError, "incomplete patched resource overlay"):
            renderer.select_webots(self.root, "forest")

    def test_binary_identity_and_overlay_contents(self):
        for path in ("bin/webots-bin", "resources/nodes/Background.wrl"):
            self.current()
            (self.home / path).write_text("changed")
            with self.assertRaises(RuntimeError):
                renderer.select_webots(self.root, "forest")

    def test_spaces_relative_paths_and_symlinks(self):
        self.current()
        alias = self.root / "a path with spaces"
        alias.symlink_to(self.home, target_is_directory=True)
        self.assertEqual(renderer.select_webots(self.root, "forest", str(alias)), self.home)
        with patch.object(renderer.Path, "resolve", autospec=True, side_effect=lambda path: self.home if path == Path("relative") else path):
            self.assertEqual(renderer.select_webots(self.root, "forest", "relative"), self.home)


class Publication(unittest.TestCase):
    def test_failed_build_retains_completed_manifest(self):
        for failure in (RuntimeError("compiler failure"), KeyboardInterrupt()):
            with tempfile.TemporaryDirectory() as scratch:
                cache = Path(scratch)
                runtime = cache / "webots-renderer"
                runtime.mkdir()
                manifest = runtime / "ironlark-renderer.json"
                manifest.write_text("previous completed build")
                with patch.object(builder, "CACHE", cache), \
                     patch.object(builder, "require_renderer_prerequisites"), \
                     patch.object(builder.shutil, "disk_usage") as disk, \
                     patch.object(builder, "build_renderer", side_effect=failure):
                    disk.return_value.free = 10 * 1024**3
                    with self.assertRaises(type(failure)):
                        builder.main()
                self.assertEqual(manifest.read_text(), "previous completed build")

    def test_low_disk_fails_before_build(self):
        with tempfile.TemporaryDirectory() as scratch:
            with patch.object(builder, "CACHE", Path(scratch)), \
                 patch.object(builder, "require_renderer_prerequisites"), \
                 patch.object(builder.shutil, "disk_usage") as disk, \
                 patch.object(builder, "build_renderer") as build:
                disk.return_value.free = 1
                with self.assertRaisesRegex(RuntimeError, "free cache disk"):
                    builder.main()
                build.assert_not_called()

    def test_network_failure_preserves_previous_archive(self):
        with tempfile.TemporaryDirectory() as scratch:
            archive = Path(scratch) / "archive"
            archive.write_bytes(b"previous")
            def download(url, target):
                Path(target).write_bytes(b"partial")
                raise OSError("network disconnected")
            with patch.object(builder, "urlretrieve", side_effect=download):
                with self.assertRaisesRegex(OSError, "network disconnected"):
                    builder.fetch_archive("unused", archive, "expected")
            self.assertEqual(archive.read_bytes(), b"previous")

    def test_atomic_manifest_preserves_old_file_on_failure(self):
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "manifest.json"
            path.write_text("old")
            with patch.object(Path, "replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    builder.atomic_json(path, {"schema": 1})
            self.assertEqual(path.read_text(), "old")

    def test_failed_or_interrupted_publish_preserves_previous(self):
        for failure in (OSError("disk full"), KeyboardInterrupt()):
            with tempfile.TemporaryDirectory() as scratch:
                root = Path(scratch)
                old, staged = root / "runtime", root / "staged"
                old.mkdir()
                (old / "old").write_text("kept")
                staged.mkdir()
                with patch.object(Path, "replace", side_effect=failure):
                    with self.assertRaises(type(failure)):
                        builder.publish_runtime(staged, old)
                self.assertEqual((old / "old").read_text(), "kept")

    def test_successful_publish_and_replacement_retain_generations(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            destination = root / "runtime"
            stages = [root / "first", root / "second"]
            for stage in stages:
                stage.mkdir()
                builder.publish_runtime(stage, destination)
                self.assertEqual(destination.resolve(), stage)
            self.assertTrue(stages[0].is_dir())

    def test_concurrent_build_lock_and_crash_file(self):
        with tempfile.TemporaryDirectory() as scratch:
            cache = Path(scratch)
            (cache / "renderer-build.lock").write_text("stale lock contents")
            with builder.build_lock(cache):
                with self.assertRaisesRegex(RuntimeError, "Another renderer build"):
                    with builder.build_lock(cache):
                        pass
            with builder.build_lock(cache):
                pass

    def test_interrupted_archive_is_not_reused(self):
        with tempfile.TemporaryDirectory() as scratch:
            archive = Path(scratch) / "archive"
            archive.write_bytes(b"incomplete")
            expected = hashlib.sha256(b"good").hexdigest()
            def download(url, target):
                Path(target).write_bytes(b"good")
            with patch.object(builder, "urlretrieve", side_effect=download) as fetch:
                builder.fetch_archive("unused", archive, expected)
                builder.fetch_archive("unused", archive, expected)
            self.assertEqual(archive.read_bytes(), b"good")
            self.assertEqual(fetch.call_count, 1)


if __name__ == "__main__":
    unittest.main()
