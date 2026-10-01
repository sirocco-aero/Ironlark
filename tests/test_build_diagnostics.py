"""Offline failures and interruption checks for bounded build stages."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

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

    def test_spawn_failure_writes_log_and_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "build.log"
            with patch.object(forest_process.subprocess, "Popen", side_effect=PermissionError("Permission denied")):
                with self.assertRaisesRegex(RuntimeError, "Permission denied"):
                    forest_process.run_stage(["compiler"], log)
            self.assertIn("Permission denied", log.read_text())
            metrics = json.loads(log.with_suffix(".metrics.json").read_text())
            self.assertIsNone(metrics["exit_code"])
            self.assertIn("Permission denied", metrics["error"])

    def test_interrupt_records_metrics_and_terminates_process(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "build.log"
            proc = Mock(pid=123, returncode=-15)
            proc.poll.return_value = None
            with patch.object(forest_process.subprocess, "Popen", return_value=proc), \
                 patch.object(forest_process, "process_memory", side_effect=KeyboardInterrupt), \
                 patch.object(forest_process.os, "killpg") as terminate:
                with self.assertRaises(KeyboardInterrupt):
                    forest_process.run_stage(["compiler"], log)
            terminate.assert_called_once_with(123, forest_process.signal.SIGTERM)
            metrics = json.loads(log.with_suffix(".metrics.json").read_text())
            self.assertIn("interrupted", metrics["error"])
            self.assertEqual(metrics["exit_code"], -15)

    def test_memory_guard_preserves_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "build.log"
            proc = Mock(pid=123, returncode=-15)
            proc.poll.return_value = None
            with patch.object(forest_process.subprocess, "Popen", return_value=proc), \
                 patch.object(forest_process, "process_memory", return_value=(2000, 3000)), \
                 patch.object(forest_process, "memory_fields", return_value={"MemAvailable": 2000000}), \
                 patch.object(forest_process.os, "killpg"):
                with self.assertRaisesRegex(RuntimeError, "memory exhaustion"):
                    forest_process.run_stage(["compiler"], log, max_mib=1)
            metrics = json.loads(log.with_suffix(".metrics.json").read_text())
            self.assertIn("memory exhaustion", metrics["error"])
            self.assertGreater(metrics["peak_rss_and_swap_mib"], 1)


if __name__ == "__main__":
    unittest.main()
