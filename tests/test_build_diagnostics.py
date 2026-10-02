"""Compiler diagnostics and failed stages on real processes and log files."""

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import forest_process


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


if __name__ == "__main__":
    unittest.main()
