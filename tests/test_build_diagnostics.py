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
import forest_process

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
        (self.root / "protos/drone/receipt.json").write_text(json.dumps({
            "inputs": {"config/sensors.json": digests["config/sensors.json"]},
            "outputs": {name: digests[name] for name in files if name.startswith("protos/")}}))

    def test_a_complete_build_is_current(self):
        self.assertIsNone(launcher.drone_stale())

    def test_a_changed_input_or_output_or_a_missing_one_is_stale(self):
        (self.root / "config/sensors.json").write_text('{"camera": {}}')
        self.assertEqual(launcher.drone_stale(), "config/sensors.json changed")
        (self.root / "config/sensors.json").write_text("{}")
        (self.root / "protos/drone/body.obj").unlink()
        self.assertEqual(launcher.drone_stale(), "protos/drone/body.obj changed")
        (self.root / "protos/drone/receipt.json").unlink()
        self.assertEqual(launcher.drone_stale(), "no completed build")


if __name__ == "__main__":
    unittest.main()
