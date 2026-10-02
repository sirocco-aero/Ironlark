"""Renderer selection, live resources and prerequisites on real files and Git checkouts."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import renderer_support as renderer
import build_webots_renderer as builder


def git(source, *args):
    return subprocess.check_output(["git", "-C", str(source), *args], text=True, stderr=subprocess.PIPE).strip()


def make_project(root):
    for name in renderer.NATIVE_NAMES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
    patches = root / "native/patches"
    patches.mkdir()
    (patches / "0001.patch").write_text("patch")
    source = root / ".cache/webots-source"
    (source / "resources/wren/shaders").mkdir(parents=True)
    (source / "resources/wren/shaders/pbr.frag").write_text("stock shader")
    (source / "src").mkdir()
    (source / "src/renderer.cpp").write_text("int value = 1;\n")
    git(source, "init", "-q")
    git(source, "add", ".")
    git(source, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "Initial source")
    for home in (root / "webots", root / ".cache/webots-renderer"):
        (home / "resources").mkdir(parents=True)
        (home / "resources/version.txt").write_text(renderer.RELEASE)
        for name in ("webots", "bin/webots-bin"):
            path = home / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("#!/bin/sh\nexit 0\n")
            path.chmod(0o755)
    for name in ("include/controller/c/webots/robot.h", "include/ode/ode/ode.h",
                 "include/qt/QtCore/QtCore/qglobal.h", "lib/webots/libQt6Core.so"):
        path = root / "webots" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("SDK fixture")
    return source


def write_manifest(root):
    path = root / ".cache/webots-renderer/ironlark-renderer.json"
    path.write_text(json.dumps(renderer.renderer_identity(root)))
    return path


class Selection(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.source = make_project(self.root)
        self.stock = self.root / "webots"
        self.home = self.root / ".cache/webots-renderer"
        self.manifest = self.home / "ironlark-renderer.json"

    def test_forest_rejects_stock_override(self):
        write_manifest(self.root)
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

    def test_old_manifest_requires_rebuild(self):
        recorded = renderer.renderer_identity(self.root)
        del recorded["schema"]
        self.manifest.write_text(json.dumps(recorded))
        with self.assertRaisesRegex(RuntimeError, "schema"):
            renderer.select_webots(self.root, "forest")

    def test_every_identity_field_is_checked(self):
        for field in renderer.renderer_identity(self.root):
            recorded = renderer.renderer_identity(self.root)
            recorded[field] = "stale"
            self.manifest.write_text(json.dumps(recorded))
            with self.assertRaisesRegex(RuntimeError, field):
                renderer.select_webots(self.root, "forest")

    def test_changed_native_source_and_patch_rejected(self):
        for name in (renderer.NATIVE_NAMES[0], "native/patches/0001.patch"):
            write_manifest(self.root)
            (self.root / name).write_text("changed")
            with self.assertRaisesRegex(RuntimeError, "stale"):
                renderer.select_webots(self.root, "forest")

    def test_staged_and_unstaged_edits_require_build_but_dev_build_is_accepted(self):
        write_manifest(self.root)
        path = self.source / "src/renderer.cpp"
        path.write_text("int value = 2;\n")
        with self.assertRaisesRegex(RuntimeError, "checkout_diff"):
            renderer.select_webots(self.root, "forest")
        git(self.source, "add", "src/renderer.cpp")
        write_manifest(self.root)
        self.assertEqual(renderer.select_webots(self.root, "forest"), self.home)
        path.write_text("int value = 3;\n")
        with self.assertRaisesRegex(RuntimeError, "checkout_diff"):
            renderer.select_webots(self.root, "forest")

    def test_checkout_commit_change_requires_build(self):
        write_manifest(self.root)
        git(self.source, "-c", "user.name=Test", "-c", "user.email=test@example.com",
            "commit", "--allow-empty", "-qm", "New development commit")
        with self.assertRaisesRegex(RuntimeError, "checkout_commit"):
            renderer.select_webots(self.root, "forest")

    def test_empty_allows_stock_even_with_stale_renderer(self):
        self.manifest.write_text("{}")
        self.assertEqual(renderer.select_webots(self.root), self.stock)
        self.assertEqual(renderer.select_webots(self.root, "empty", str(self.stock)), self.stock)

    def test_current_manifest_and_symlink_override_accepted(self):
        write_manifest(self.root)
        alias = self.root / "a path with spaces"
        alias.symlink_to(self.home, target_is_directory=True)
        self.assertEqual(renderer.select_webots(self.root, "forest", str(alias)), self.home)

    def test_incomplete_runtime_rejected(self):
        write_manifest(self.root)
        (self.home / "bin/webots-bin").chmod(0o644)
        with self.assertRaisesRegex(RuntimeError, "missing executable"):
            renderer.select_webots(self.root, "forest")

    def test_actual_release_checked(self):
        write_manifest(self.root)
        (self.home / "resources/version.txt").write_text("R2024a")
        with self.assertRaisesRegex(RuntimeError, "R2025a"):
            renderer.select_webots(self.root, "forest")

    def test_live_overlay_serves_working_tree_and_new_nested_resources(self):
        stock_shader = self.stock / "resources/wren/shaders/pbr.frag"
        stock_shader.parent.mkdir(parents=True)
        stock_shader.write_text("stock shader")
        base = git(self.source, "rev-parse", "HEAD")
        shader = self.source / "resources/wren/shaders/pbr.frag"
        shader.write_text("development shader")
        added = self.source / "resources/new/nested/node.wrl"
        added.parent.mkdir(parents=True)
        added.write_text("new node")
        git(self.source, "add", "resources/new/nested/node.wrl")
        builder.overlay_resources(self.home, self.stock, self.source, base)
        served = self.home / "resources/wren/shaders/pbr.frag"
        self.assertTrue(served.is_symlink())
        self.assertEqual(served.read_text(), "development shader")
        shader.write_text("edited again")
        self.assertEqual(served.read_text(), "edited again")
        self.assertEqual((self.home / "resources/new/nested/node.wrl").read_text(), "new node")
        self.assertEqual(stock_shader.read_text(), "stock shader")
        builder.overlay_resources(self.home, self.stock, self.source, base)
        self.assertEqual(served.read_text(), "edited again")


