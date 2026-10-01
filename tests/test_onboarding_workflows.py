"""Launcher workflow gates without Docker, display, network, or generated assets."""

from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
loader = SourceFileLoader("ironlark_workflow_tests", str(ROOT / "ironlark"))
launcher = module_from_spec(spec_from_loader(loader.name, loader))
loader.exec_module(launcher)


class Workflows(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ironlark workflow ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name, value in (("ROOT", self.root), ("CACHE", self.root / ".cache")):
            mocked = patch.object(launcher, name, value)
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_forest_rejection_precedes_any_run_artifacts(self):
        with patch.object(launcher, "webots_home", side_effect=RuntimeError("forest requires patched renderer")), \
             patch.object(launcher, "doctor") as doctor, patch.object(launcher, "build_drone") as drone:
            with self.assertRaisesRegex(RuntimeError, "patched renderer"):
                launcher.flight(headless=True, world="forest")
        doctor.assert_not_called()
        drone.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_missing_headless_tool_fails_before_drone_build(self):
        with patch.object(launcher, "webots_home", return_value=self.root / "webots"), \
             patch.object(launcher, "doctor"), patch.dict(launcher.os.environ, {"HOME": str(self.root)}, clear=True), \
             patch.object(launcher.shutil, "which", return_value=None), \
             patch.object(launcher, "build_drone") as drone, patch.object(launcher, "check_ports"):
            with self.assertRaisesRegex(RuntimeError, "xvfb-run"):
                launcher.flight(headless=True, world="empty")
        drone.assert_not_called()

    def test_visible_run_does_not_need_ros_or_xvfb(self):
        with patch.object(launcher, "webots_home", return_value=self.root / "webots"), \
             patch.object(launcher, "doctor"), \
             patch.dict(launcher.os.environ, {"HOME": str(self.root), "DISPLAY": ":1"}, clear=True), \
             patch.object(launcher.shutil, "which", return_value=None), \
             patch.object(launcher.subprocess, "run") as run, \
             patch.object(launcher, "build_drone", side_effect=RuntimeError("drone build boundary")):
            with self.assertRaisesRegex(RuntimeError, "drone build boundary"):
                launcher.flight(headless=False, world="empty", record=False)
        run.assert_not_called()

    def test_missing_display_fails_before_drone_build(self):
        with patch.object(launcher, "webots_home", return_value=self.root / "webots"), \
             patch.object(launcher, "doctor"), patch.dict(launcher.os.environ, {"HOME": str(self.root)}, clear=True), \
             patch.object(launcher, "build_drone") as drone:
            with self.assertRaisesRegex(RuntimeError, "DISPLAY|display"):
                launcher.flight(headless=False, world="empty")
        drone.assert_not_called()

    def test_wayland_only_requires_xwayland_for_stock_launcher(self):
        with patch.object(launcher, "webots_home", return_value=self.root / "webots"), \
             patch.object(launcher, "doctor"), \
             patch.dict(launcher.os.environ, {"HOME": str(self.root), "WAYLAND_DISPLAY": "wayland-0"}, clear=True), \
             patch.object(launcher, "build_drone", side_effect=RuntimeError("unexpected drone build")) as drone:
            with self.assertRaisesRegex(RuntimeError, "XWayland|DISPLAY|display"):
                launcher.flight(headless=False, world="empty")
        drone.assert_not_called()

    def test_recording_requires_ros_image_before_drone_build(self):
        with patch.object(launcher, "webots_home", return_value=self.root / "webots"), \
             patch.object(launcher, "doctor"), \
             patch.dict(launcher.os.environ, {"HOME": str(self.root), "DISPLAY": ":1"}, clear=True), \
             patch.object(launcher.subprocess, "run", return_value=Mock(returncode=1)), \
             patch.object(launcher, "build_drone") as drone:
            with self.assertRaisesRegex(RuntimeError, "ROS"):
                launcher.flight(headless=False, world="empty", record=True)
        drone.assert_not_called()

    def test_replay_reports_missing_docker_without_launch(self):
        with patch.object(launcher.shutil, "which", return_value=None), \
             patch.object(launcher.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "Docker"):
                launcher.ros_container("record", self.root, "ros2 bag play bag")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
