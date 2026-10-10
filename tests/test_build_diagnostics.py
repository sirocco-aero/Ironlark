"""Compiler diagnostics and failed stages on real processes and log files."""

import hashlib
import importlib.util
import json
from importlib.machinery import SourceFileLoader
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import forest_pipeline
import forest_process
import receipts

loader = SourceFileLoader("launcher", str(ROOT / "ironlark"))  # the launcher script, as a module
launcher = importlib.util.module_from_spec(importlib.util.spec_from_loader("launcher", loader))
loader.exec_module(launcher)


class Diagnostics(unittest.TestCase):
    def test_interleaved_early_errors_and_invalid_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "build log.txt"
            for error in (b"fatal error: GL/gl.h: missing", b"undefined reference to glGetError",
                          b"Permission denied", b"No space left on device", b"Killed signal terminated program"):
                log.write_bytes(b"context\xff\n" + error + b"\n" + b"compiling other file\n" * 100)
                excerpt = forest_process.error_excerpt(log)
                self.assertIn(error.decode(), excerpt)
                self.assertLessEqual(len(excerpt.splitlines()), 18)
                self.assertIn("context", excerpt)

    @unittest.skipUnless(shutil.which("g++"), "C++ compiler required")
    def test_missing_header_printed_with_complete_log_and_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, log = root / "broken.cpp", root / "build.log"
            source.write_text('#include "ironlark-missing-test-header.h"\n')
            with self.assertRaisesRegex(RuntimeError, "ironlark-missing-test-header.h"):
                forest_process.run_stage(["g++", "-c", str(source), "-o", str(root / "broken.o")], log)
            self.assertIn("fatal error:", log.read_text())
            metrics = json.loads(log.with_suffix(".metrics.json").read_text())
            self.assertNotEqual(metrics["exit_code"], 0)
            self.assertIn("exited", metrics["error"])

    def test_spawn_failure_writes_log_and_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "build.log"
            with self.assertRaisesRegex(RuntimeError, "No such file"):
                forest_process.run_stage([str(root / "missing-compiler")], log)
            self.assertIn("No such file", log.read_text())
            metrics = json.loads(log.with_suffix(".metrics.json").read_text())
            self.assertIsNone(metrics["exit_code"])
            self.assertIn("No such file", metrics["error"])



@unittest.skipUnless(shutil.which("cc") or shutil.which("gcc"), "C compiler required")
class Lockstep(unittest.TestCase):
    """The launcher compiles the flight bridge's C loop for each run against the pinned Webots, or stops the flight."""

    def build(self, source):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        (root / "controllers/flight_bridge").mkdir(parents=True)
        (root / "controllers/flight_bridge/lockstep.c").write_text(source)
        self.addCleanup(setattr, launcher, "ROOT", launcher.ROOT)
        launcher.ROOT = root
        return launcher.build_lockstep(ROOT / "webots")

    def test_the_library_is_built_from_the_source_at_hand(self):
        source = (ROOT / "controllers/flight_bridge/lockstep.c").read_text()
        resend = source.replace("readable(sock, 1.0)", "readable(sock, 2.0)")
        self.assertNotEqual(resend, source)
        self.assertTrue(self.build(source).startswith(b"\x7fELF"))
        self.assertNotEqual(self.build(resend), self.build(source))

    def test_a_source_that_does_not_compile_stops_the_flight(self):
        source = (ROOT / "controllers/flight_bridge/lockstep.c").read_text().replace("int motor_count,", "int motor_count")
        with self.assertRaisesRegex(RuntimeError, "(?s)does not compile.*error"):
            self.build(source)


class Drone(unittest.TestCase):
    """The launcher rebuilds the drone unless its receipt matches every input and output."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "protos/drone").mkdir(parents=True)
        self.addCleanup(setattr, launcher, "ROOT", launcher.ROOT)
        launcher.ROOT = self.root
        files = {"config/sensors.json": "{}", "protos/IronlarkDrone.proto": "PROTO", "protos/drone/body.obj": "v 0 0 0"}
        for name, text in files.items():
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text(text)
        digests = {name: hashlib.sha256(text.encode()).hexdigest() for name, text in files.items()}
        for package in ("numpy-2.2.6", "pillow-12.3.0"):
            (self.root / f".venv/lib/python3.12/site-packages/{package}.dist-info").mkdir(parents=True)
        (self.root / "protos/drone/receipt.json").write_text(json.dumps({
            "inputs": {"config/sensors.json": digests["config/sensors.json"]},
            "packages": {"numpy": "2.2.6", "pillow": "12.3.0"},
            "outputs": {name: digests[name] for name in files if name.startswith("protos/")}}))

    def test_a_complete_build_is_current(self):
        self.assertIsNone(launcher.drone_stale())

    def test_a_changed_input_or_output_or_a_missing_one_is_stale(self):
        (self.root / "config/sensors.json").write_text('{"camera": {}}')
        self.assertEqual(launcher.drone_stale(), "config/sensors.json changed")
        (self.root / "config/sensors.json").write_text("{}")
        (self.root / "protos/drone/body.obj").unlink()
        self.assertEqual(launcher.drone_stale(), "protos/drone/body.obj changed")
        (self.root / "protos/drone/body.obj").write_text("v 0 0 0")
        site = self.root / ".venv/lib/python3.12/site-packages"
        (site / "pillow-12.3.0.dist-info").rename(site / "pillow-12.4.0.dist-info")
        self.assertIn("now {'numpy': '2.2.6', 'pillow': '12.4.0'}", launcher.drone_stale())
        (self.root / "protos/drone/receipt.json").unlink()
        self.assertEqual(launcher.drone_stale(), "no completed build")



class Receipts(unittest.TestCase):
    """A forest world carries what it was built from; the pipeline resumes only a build of the same recipe."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        shutil.copytree(ROOT / "tools", self.root / "tools", ignore=shutil.ignore_patterns("__pycache__"))
        (self.root / "controllers/flight_observer").mkdir(parents=True)
        shutil.copyfile(ROOT / "controllers/flight_observer/minimap.py", self.root / "controllers/flight_observer/minimap.py")
        for package in ("numpy-2.2.6", "pillow-12.3.0", "etcpak-0.9.15"):
            (self.root / f".venv/lib/python3.12/site-packages/{package}.dist-info").mkdir(parents=True)
        self.world = self.root / "worlds/pine_forest"
        (self.world / "meshes").mkdir(parents=True)
        (self.world / "pine_forest.wbt").write_text("#VRML_SIM R2025a utf8\n")
        (self.world / "meshes/tree.obj").write_text("v 0 0 0\n")

    def test_the_recipe_covers_every_script_the_pipeline_runs_and_what_they_import(self):
        code = receipts.pipeline_code(ROOT)
        for name in ("forest_pipeline", "export_forest", "forest_mesh", "bake_impostors", "build_forest_world",
                     "forest_edge", "forest_process"):
            self.assertIn(f"tools/{name}.py", code)
        self.assertIn("controllers/flight_observer/minimap.py", code)  # the world's view, through look_at
        self.assertNotIn("tools/render_forest.py", code)

    def test_a_world_is_current_until_its_code_packages_or_files_change(self):
        receipts.write_world(self.root, self.world)
        self.assertEqual(receipts.world_problems(self.root, self.world), [])
        with (self.root / "tools/forest_mesh.py").open("a") as script:
            script.write("# changed\n")
        self.assertEqual(receipts.world_problems(self.root, self.world), ["tools/forest_mesh.py changed"])
        shutil.copyfile(ROOT / "tools/forest_mesh.py", self.root / "tools/forest_mesh.py")
        with (self.root / "controllers/flight_observer/minimap.py").open("a") as script:
            script.write("# changed\n")
        self.assertEqual(receipts.world_problems(self.root, self.world), ["controllers/flight_observer/minimap.py changed"])
        shutil.copyfile(ROOT / "controllers/flight_observer/minimap.py", self.root / "controllers/flight_observer/minimap.py")
        (self.root / ".venv/lib/python3.12/site-packages/numpy-2.2.6.dist-info").rename(
            self.root / ".venv/lib/python3.12/site-packages/numpy-2.3.0.dist-info")
        self.assertEqual(receipts.world_problems(self.root, self.world), ["numpy 2.2.6 is now 2.3.0"])

    def test_a_changed_or_missing_output_is_named(self):
        receipts.write_world(self.root, self.world)
        (self.world / "meshes/tree.obj").write_text("v 0 0 1\n")  # the same size: only its contents tell
        self.assertEqual(receipts.world_problems(self.root, self.world), [])
        self.assertEqual(receipts.world_problems(self.root, self.world, contents=True),
                         ["meshes/tree.obj is not the file the build wrote"])
        (self.world / "pine_forest.wbt").unlink()
        self.assertIn("pine_forest.wbt is not the file the build wrote", receipts.world_problems(self.root, self.world))
        (self.world / "receipt.json").unlink()
        self.assertEqual(receipts.world_problems(self.root, self.world),
                         ["it has no receipt: built before receipts, or not finished"])

    def test_a_build_resumes_only_under_the_same_recipe(self):
        build = self.root / "forest-build"
        receipts.write_world(self.root, self.world)
        forest_pipeline.start_build(build, self.world, {"code": "today"}, skip_export=False)
        self.assertFalse((self.world / "receipt.json").exists())  # until the build completes
        (build / "terrain.obj").write_text("stage output")
        forest_pipeline.start_build(build, self.world, {"code": "today"}, skip_export=True)
        self.assertTrue((build / "terrain.obj").exists())
        with self.assertRaisesRegex(RuntimeError, "made by other code"):
            forest_pipeline.start_build(build, self.world, {"code": "tomorrow"}, skip_export=True)
        forest_pipeline.start_build(build, self.world, {"code": "tomorrow"}, skip_export=False)
        self.assertFalse((build / "terrain.obj").exists())
        self.assertEqual(json.loads((build / "recipe.json").read_text()), {"code": "tomorrow"})

    def test_an_extraction_belongs_to_its_archive(self):
        import tarfile

        archive = self.root / "source.tar"
        (self.root / "packed").mkdir()
        (self.root / "packed/scene.blend").write_text("first")
        with tarfile.open(archive, "w") as tar:
            tar.add(self.root / "packed", arcname="packed")
        target = self.root / "source"
        forest_pipeline.unpack(archive, target, tarfile.open, "sha-1", member="packed")
        (target / "scene.blend").write_text("edited")
        forest_pipeline.unpack(archive, target, tarfile.open, "sha-1", member="packed")
        self.assertEqual((target / "scene.blend").read_text(), "edited")  # extracted once
        forest_pipeline.unpack(archive, target, tarfile.open, "sha-2", member="packed")
        self.assertEqual((target / "scene.blend").read_text(), "first")  # another archive: extracted again


if __name__ == "__main__":
    unittest.main()
