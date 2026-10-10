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
import build_forest_world
import forest_pipeline
import forest_process
import receipts
import stage_cache

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

    def test_forked_workers_count_their_shared_pages_once(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "stage.log"
            # 200 MiB written by the parent, then shared by two forked children: 600 MiB resident, 200 MiB of it.
            forest_process.run_stage([sys.executable, "-c", (
                "import os, time\n"
                "data = bytearray(200 << 20)\n"
                "for i in range(0, len(data), 4096): data[i] = 1\n"
                "for _ in range(2):\n"
                "    if os.fork() == 0:\n"
                "        time.sleep(1.5)\n"
                "        os._exit(0)\n"
                "time.sleep(1.5)\n")], log)
            peak = json.loads(log.with_suffix(".metrics.json").read_text())["peak_pss_mib"]
            self.assertGreater(peak, 200)
            self.assertLess(peak, 400)

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

    def test_the_export_recipe_leaves_out_the_stages_after_the_export(self):
        code = forest_pipeline.recipes(ROOT)["export"]["code"]
        for name in ("forest_pipeline", "export_forest", "export_forest_visuals", "bake_tree_materials",
                     "compose_forest_floor", "export_forest_sky", "stage_cache"):
            self.assertIn(f"tools/{name}.py", code)
        for name in ("build_forest_world", "bake_forest_light", "bake_impostors", "forest_valley", "dds"):
            self.assertNotIn(f"tools/{name}.py", code)

    def test_a_build_resumes_only_under_the_same_export_code(self):
        build = self.root / "forest-build"
        today = {"export": "export today", "full": "today"}
        receipts.write_world(self.root, self.world)
        forest_pipeline.start_build(build, self.world, today, skip_export=False)
        self.assertFalse((self.world / "receipt.json").exists())  # until the build completes
        (build / "terrain.obj").write_text("stage output")
        assembly_edited = {"export": "export today", "full": "tomorrow"}
        forest_pipeline.start_build(build, self.world, assembly_edited, skip_export=False)
        self.assertTrue((build / "terrain.obj").exists())  # the exports stand
        export_edited = {"export": "export tomorrow", "full": "tomorrow"}
        forest_pipeline.start_build(build, self.world, export_edited, skip_export=False)
        self.assertFalse((build / "terrain.obj").exists())
        self.assertEqual(json.loads((build / "recipe.json").read_text()), "export tomorrow")

    def test_skip_export_needs_bakes_today_s_pipeline_finished(self):
        build = self.root / "forest-build"
        today = {"export": "export today", "full": "today"}
        forest_pipeline.start_build(build, self.world, today, skip_export=False)
        with self.assertRaisesRegex(RuntimeError, "did not finish"):  # its bakes have not run
            forest_pipeline.start_build(build, self.world, today, skip_export=True)
        (build / "baked.json").write_text(json.dumps("today"))  # as build_world writes after the bakes
        forest_pipeline.start_build(build, self.world, today, skip_export=True)
        bake_edited = {"export": "export today", "full": "tomorrow"}
        with self.assertRaisesRegex(RuntimeError, "made by other code"):
            forest_pipeline.start_build(build, self.world, bake_edited, skip_export=True)
        forest_pipeline.start_build(build, self.world, today, skip_export=False)  # interrupted before its bakes
        with self.assertRaisesRegex(RuntimeError, "did not finish"):
            forest_pipeline.start_build(build, self.world, today, skip_export=True)

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


class StageStore(unittest.TestCase):
    """The stage store hands back a result only for the same code, inputs and toolchain, and only intact."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.build, self.store = self.root / "build", stage_cache.Store(self.root / "store")
        self.build.mkdir()
        self.input = self.root / "tree.obj"
        self.input.write_text("v 0 0 0\n")
        self.outputs = ["impostor_pine_01.npz"]

    def recipe(self):
        return stage_cache.descriptor(ROOT, "impostor", "tools/bake_impostors.py", ["pine_01"], {"tree": self.input},
                                      {"blender": "test"})

    def bake(self, text="capture"):
        (self.build / "impostor_pine_01.npz").write_text(text)
        self.store.save(self.recipe(), self.build, self.outputs, {"radius": 1})

    def manifest(self):
        return self.store.place(self.recipe()) / "manifest.json"

    def test_a_result_comes_back_after_the_build_folder_is_emptied(self):
        self.bake()
        shutil.rmtree(self.build)
        self.build.mkdir()
        self.assertEqual(self.store.restore(self.recipe(), self.build, self.outputs), {"value": {"radius": 1}})
        restored = self.build / "impostor_pine_01.npz"
        self.assertEqual(restored.read_text(), "capture")
        restored.write_text("rewritten")  # a file of its own: an older checkout's stage rewrites it in place
        self.assertEqual(self.store.restore(self.recipe(), self.build, self.outputs), {"value": {"radius": 1}})
        self.assertEqual(restored.read_text(), "capture")

    def test_another_input_byte_is_another_result(self):
        self.bake()
        self.input.write_text("v 0 0 1\n")
        self.assertIsNone(self.store.restore(self.recipe(), self.build, self.outputs))

    def test_a_damaged_result_is_discarded(self):
        self.bake()
        stored = self.store.place(self.recipe()) / "outputs/impostor_pine_01.npz"
        stored.chmod(0o644)
        stored.write_text("captura")  # the same size
        self.assertIsNone(self.store.restore(self.recipe(), self.build, self.outputs))
        self.assertFalse(self.store.place(self.recipe()).exists())

    def test_a_result_without_its_manifest_is_never_found(self):
        self.bake()
        self.manifest().unlink()
        self.assertIsNone(self.store.restore(self.recipe(), self.build, self.outputs))

    def test_a_result_missing_an_output_the_stage_names_is_discarded(self):
        self.bake()  # stored before the stage named its second output
        self.assertIsNone(self.store.restore(self.recipe(), self.build, [*self.outputs, "impostor_pine_01.json"]))
        self.assertFalse(self.store.place(self.recipe()).exists())

    def test_after_a_build_the_store_keeps_what_each_checkout_s_latest_build_used(self):
        import time

        recipes = [stage_cache.descriptor(ROOT, "impostor", "tools/bake_impostors.py", [name], {"tree": self.input},
                                          {"blender": "test"}) for name in ("old", "main's", "branch's")]
        (self.build / "impostor_pine_01.npz").write_text("capture")
        for recipe in recipes:
            self.store.save(recipe, self.build, self.outputs)
        (self.store.place(recipes[0]).with_name("abandoned.part")).mkdir()
        main, branch = self.root / "main checkout", self.root / "worktree"
        main.mkdir()
        branch.mkdir()
        started = time.time()
        self.assertTrue(self.store.restore(recipes[2], self.build, self.outputs))
        self.store.collect(branch, started)  # the worktree's build used one; no build of main's has said what it uses
        started = time.time()
        self.assertIsNone(self.store.restore(recipes[1], self.build, self.outputs))
        self.store.save(recipes[1], self.build, self.outputs)  # main's build misses, and stores it again
        self.store.collect(main, started)  # from now on the worktree's stays as well
        self.assertEqual([self.store.place(recipe).exists() for recipe in recipes], [False, True, True])
        self.assertEqual(list(self.store.folder.glob("*/*.part")), [])
        shutil.rmtree(branch)  # a removed worktree keeps nothing
        self.store.collect(main, time.time())  # main's next build used none: nothing is left
        self.assertEqual([self.store.place(recipe).exists() for recipe in recipes], [False, False, False])

    def test_a_manifest_of_another_recipe_or_cut_short_is_discarded(self):
        for damage in (lambda text: text.replace('"pine_01"', '"pine_02"', 1), lambda text: text[: len(text) // 2]):
            self.bake()
            self.manifest().write_text(damage(self.manifest().read_text()))
            self.assertIsNone(self.store.restore(self.recipe(), self.build, self.outputs))
            self.assertFalse(self.store.place(self.recipe()).exists())


class Bakes(unittest.TestCase):
    """forest_pipeline.bakes reruns a bake exactly when a byte it reads changed (the stage itself, Blender, stands in
    as a function writing its outputs: these tests are about which bakes run)."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.build, self.world, self.source, self.stages = (self.root / name for name in ("build", "world", "source",
                                                                                         "stages"))
        (self.world / "meshes").mkdir(parents=True)
        (self.source / "textures").mkdir(parents=True)
        self.build.mkdir()
        for name in ("pine.obj", "fir.obj", "bark.png", "twigs.png", "bark_normal.png", "twigs_normal.png",
                     *(f"terrain_{x}_{y}.obj" for x in range(2) for y in range(2))):
            (self.world / "meshes" / name).write_text(name)
        (self.source / "polyhaven_pine_fir_forest.blend").write_text("scene")
        (self.source / "textures/evening_road_01_puresky_8k.exr").write_text("sky")
        self.place([0.0, 0.0, 0.0])
        self.declare("meshes/bark.png", "meshes/twigs.png")

    def place(self, translation):
        (self.world / "visual_instances.json").write_text(json.dumps(
            [{"mesh": "meshes/pine.obj", "texture": "meshes/twigs.png", "translation": translation}]))

    def declare(self, first, second):
        """Two variants; the second's two meshes wear first and second."""
        (self.build / "impostor_variants.json").write_text(json.dumps({
            "fir_01": [{"mesh": "meshes/fir.obj", "tex": "meshes/twigs.png", "normal": ""}],
            "pine_01": [{"mesh": "meshes/pine.obj", "tex": first, "normal": "meshes/bark_normal.png"},
                        {"mesh": "meshes/terrain_0_0.obj", "tex": second, "normal": "meshes/twigs_normal.png"}]}))

    def bake(self, world=None):
        ran = []

        def blender_stage(name, script, *args):
            run = len(ran) + 1
            if name == "canopy-light":
                ran.append(name)
                for output in [f"terrain_{x}_{y}_layers.npy" for x in range(2) for y in range(2)] + ["light_layers.json"]:
                    (self.build / output).write_text(f"light {run}")
            else:
                variants = args[args.index("--variants") + 1:]
                ran.append((name, *variants))
                for variant in variants:
                    (self.build / f"impostor_{variant}.npz").write_text(f"{variant} {run}")
                    (self.build / f"impostor_{variant}.json").write_text(json.dumps({"run": run}))

        forest_pipeline.bakes(self.build, world or self.world, self.source, self.stages, blender_stage)
        return ran

    def test_unchanged_bakes_come_back_without_running(self):
        self.assertEqual(self.bake(), ["canopy-light", ("impostors", "fir_01", "pine_01")])
        for path in self.build.glob("*_layers.npy"):
            path.unlink()
        self.assertEqual(self.bake(), [])
        self.assertEqual((self.build / "terrain_0_0_layers.npy").read_text(), "light 1")
        self.assertEqual(json.loads((self.build / "impostors.json").read_text()), {"fir_01": {"run": 2},
                                                                                   "pine_01": {"run": 2}})

    def test_another_checkout_with_the_same_world_bakes_nothing(self):
        self.bake()
        shutil.copytree(self.world, self.root / "other checkout/world")
        self.assertEqual(self.bake(self.root / "other checkout/world"), [])

    def test_a_moved_tree_bakes_the_light_again(self):
        self.bake()
        self.place([0.0, 1.0, 0.0])
        self.assertEqual(self.bake(), ["canopy-light"])

    def test_a_texture_on_another_mesh_captures_only_that_impostor_again(self):
        self.bake()
        self.declare("meshes/twigs.png", "meshes/bark.png")  # the same files, worn by the other mesh
        self.assertEqual(self.bake(), [("impostors", "pine_01")])
        self.assertEqual((self.build / "impostor_fir_01.npz").read_text(), "fir_01 2")  # the first run's, linked back
        self.assertEqual((self.build / "impostor_pine_01.npz").read_text(), "pine_01 1")
        self.assertEqual(json.loads((self.build / "impostors.json").read_text()), {"fir_01": {"run": 2},
                                                                                   "pine_01": {"run": 1}})


class Valley(unittest.TestCase):
    """The assembly takes an unchanged valley from the stage store (forest_valley.shapes, 80 s, stands in as a
    function writing its files)."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.build, self.out, self.stages = root / "build", root / "world", root / "stages"
        self.build.mkdir()
        (self.out / "meshes").mkdir(parents=True)
        (self.build / "heights.json").write_text("[[0, 1], [1, 2]]")
        self.runs = 0
        shapes, bounds = build_forest_world.forest_valley.shapes, build_forest_world.forest_backdrop.BOUNDS
        self.addCleanup(setattr, build_forest_world.forest_valley, "shapes", shapes)
        self.addCleanup(setattr, build_forest_world.forest_backdrop, "BOUNDS", bounds)
        build_forest_world.forest_valley.shapes = self.shapes
        build_forest_world.forest_backdrop.BOUNDS = (-137.1, 62.70469, -17.65625, 177.27589)

    def shapes(self, build, out):
        self.runs += 1
        for name in build_forest_world.forest_valley.FILES:
            (out / name).write_text(f"{name} {self.runs}")
        return [f"Shape {self.runs}"]

    def test_an_unchanged_valley_is_linked_back(self):
        self.assertEqual(build_forest_world.valley_shapes(self.build, self.out, self.stages), ["Shape 1"])
        shutil.rmtree(self.out / "meshes")
        self.assertEqual(build_forest_world.valley_shapes(self.build, self.out, self.stages), ["Shape 1"])
        self.assertEqual(self.runs, 1)
        self.assertEqual((self.out / "meshes/valley_near.obj").read_text(), "meshes/valley_near.obj 1")

    def test_another_height_grid_or_mirror_computes_it_again(self):
        build_forest_world.valley_shapes(self.build, self.out, self.stages)
        (self.build / "heights.json").write_text("[[0, 1], [1, 3]]")
        self.assertEqual(build_forest_world.valley_shapes(self.build, self.out, self.stages), ["Shape 2"])
        build_forest_world.forest_backdrop.BOUNDS = (-137.1, 62.70469, -17.65625, 177.3)
        self.assertEqual(build_forest_world.valley_shapes(self.build, self.out, self.stages), ["Shape 3"])
        self.assertEqual((self.out / "meshes/valley_roofs.obj").read_text(), "meshes/valley_roofs.obj 3")


class MissingBakes(unittest.TestCase):
    """The full assembly fails, naming what to run, when a bake it draws is missing, instead of leaving it out."""

    def test_a_missing_impostor_capture_or_light_layer_fails_the_assembly(self):
        import forest_fog

        with tempfile.TemporaryDirectory() as directory:
            build, out = Path(directory) / "build", Path(directory) / "world"
            build.mkdir()
            (out / "meshes").mkdir(parents=True)
            trees = {"pine_01": [{"mesh_url": "meshes/pine.obj", "tex": "meshes/bark.png"}]}
            with self.assertRaisesRegex(RuntimeError, "No impostor capture of pine_01.*build-world"):
                build_forest_world.impostor_shapes(build, out, trees, [])
            self.assertEqual(build_forest_world.impostor_shapes(build, out, trees, [], declare_only=True), [])
            with self.assertRaisesRegex(RuntimeError, "lacks the canopy light bake's terrain_0_0_layers.npy.*build-world"):
                forest_fog.light_occlusion_fields(build, out, (0, 0, 0), (-1, 1, -1, 1))


class Textures(unittest.TestCase):
    """The assembly's BC7 textures come from the stage store while the PNG and the encoder are unchanged."""

    def test_an_unchanged_texture_is_linked_back_and_an_edited_one_encoded(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            out, stages = Path(directory) / "world", Path(directory) / "stages"
            (out / "meshes").mkdir(parents=True)
            png = out / "meshes/bark.png"
            Image.new("RGBA", (64, 64), (90, 60, 40, 255)).save(png)
            stored = []
            for colour in ((90, 60, 40, 255), (90, 60, 40, 255), (40, 60, 90, 255)):
                Image.new("RGBA", (64, 64), colour).save(png)
                (out / "pine_forest.wbt").write_text('url "meshes/bark.png"\n')
                build_forest_world.compress_textures(out, stages)
                self.assertEqual((out / "pine_forest.wbt").read_text(), 'url "meshes/bark.dds"\n')
                stored.append(sorted(path.read_bytes() for path in stages.glob("bc7/*/outputs/meshes/bark.dds")))
                self.assertIn((out / "meshes/bark.dds").read_bytes(), stored[-1])
            self.assertEqual([len(results) for results in stored], [1, 1, 2])  # encoded, taken back, encoded


if __name__ == "__main__":
    unittest.main()
