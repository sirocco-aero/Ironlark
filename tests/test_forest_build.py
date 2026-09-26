"""Regression checks for the forest's coordinate and material conversion."""

import json
import math
from pathlib import Path
import re
import sys
import tempfile
import unittest
import numpy as np

SIM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM / "controllers/flight_observer"))
sys.path.insert(0, str(SIM / "tools"))
from minimap import look_at, WorldView
from build_forest_world import enu, copy_mesh, cover_placement, tree_placement
from forest_terrain import clip_polygon
from forest_mesh import write_material_obj


def rotate(aa, vector):
    axis = np.array(aa[:3])
    v = np.array(vector)
    angle = aa[3]
    return (
        v * math.cos(angle)
        + np.cross(axis, v) * math.sin(angle)
        + axis * np.dot(axis, v) * (1 - math.cos(angle))
    )


class Coordinates(unittest.TestCase):
    def test_authored_tree_tilt_scale_and_mesh_offset_survive_export(self):
        # A leaning tree with nonuniform and mirrored scales must still put
        # every source vertex at its authored world position.
        angle = 0.31
        rotation = np.array(
            [
                [1, 0, 0],
                [0, math.cos(angle), -math.sin(angle)],
                [0, math.sin(angle), math.cos(angle)],
            ]
        )
        home = np.array([37.1, -80, 0])
        for scales in ([1.2, 0.8, 1.4], [-1.2, 0.8, 1.4]):
            matrix = np.eye(4)
            matrix[:3, :3] = rotation @ np.diag(scales)
            matrix[:3, 3] = [23, -48, 2]
            base_z = -0.73
            result = tree_placement(matrix, base_z, home)
            aa = (*enu(result["aa"][:3]), result["aa"][3])
            for vertex in ([0.4, 0.7, base_z], [-0.2, 0.3, 8]):
                rebased = np.array(vertex) - [0, 0, base_z]
                actual = rotate(aa, rebased * result["scale"][[0, 2, 1]]) + enu(
                    result["pos"]
                )
                expected = matrix[:3, :3] @ vertex + matrix[:3, 3] - home
                np.testing.assert_allclose(actual, expected, atol=1e-9)

    def test_foliage_backfaces_do_not_corrupt_the_next_triangle_normals(self):
        # A two-triangle card shares two corners. Its backfaces need opposite
        # normals without shifting the normal indices of later frontfaces.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "card.obj"
            write_material_obj(
                path,
                np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]]),
                np.arange(4),
                np.array([[0, 0], [1, 0], [1, 1], [0, 1]]),
                np.tile([0, 0, 1], (4, 1)),
                np.array([[0, 1, 2], [0, 2, 3]]),
                two_sided=True,
            )
            normals = []
            faces = []
            for line in path.read_text().splitlines():
                parts = line.split()
                if parts[0] == "vn":
                    normals.append(list(map(float, parts[1:])))
                if parts[0] == "f":
                    faces.append([int(p.split("/")[2]) - 1 for p in parts[1:]])
            for i, face in enumerate(faces):
                for n in face:
                    self.assertEqual(normals[n], [0, 1 if i % 2 == 0 else -1, 0])

    def test_camera_uses_the_flight_world_up_axis(self):
        for eye, target in [
            ([4, -5, 2.6], [0, 0, 0.8]),
            ([-45, 100, 55], [-45, 40, 0]),
            ([0, 0, 0], [0, 1, 0]),
            ([0, 0, 0], [-1, 0, 0]),
        ]:
            aa = look_at(eye, target)
            forward = np.array(target) - eye
            forward = forward / np.linalg.norm(forward)
            np.testing.assert_allclose(rotate(aa, [1, 0, 0]), forward, atol=1e-9)
            # A level horizon: camera's left axis is horizontal in ENU.
            self.assertAlmostEqual(rotate(aa, [0, 1, 0])[2], 0, places=9)
            self.assertGreater(rotate(aa, [0, 0, 1])[2], 0)

    def test_export_frame_agrees_with_gravity_and_map(self):
        self.assertEqual(enu([10, 3, -7]), (10, 7, 3))
        self.assertEqual(WorldView.ground(enu([10, 3, -7])), (10, 7))
        with tempfile.TemporaryDirectory() as d:
            src, dst = Path(d) / "source.obj", Path(d) / "world.obj"
            src.write_text("v 10 3 -7\nvn 0 1 0\nvt 0.2 0.7\nf 1/1/1 1/1/1 1/1/1\n")
            copy_mesh(src, dst)
            self.assertIn("v 10.000000 7.000000 3.000000", dst.read_text())
            self.assertIn("vn 0.000000 -0.000000 1.000000", dst.read_text())
            self.assertIn("vt 0.2 0.7", dst.read_text())

    def test_cover_rotation_survives_both_frame_conversions(self):
        angle = 0.73
        scale = 1.27
        m = scale * np.array(
            [
                [math.cos(angle), -math.sin(angle), 0],
                [math.sin(angle), math.cos(angle), 0],
                [0, 0, 1],
            ]
        )
        s, aa = cover_placement(m.ravel())
        axis = enu(aa[:3])
        world_rotation = (*axis, aa[3])
        point = [0.3, -0.8, 1.2]
        np.testing.assert_allclose(
            rotate(world_rotation, np.array(point) * s), m @ point, atol=1e-9
        )

    def test_tile_clipping_preserves_the_surface(self):
        # Position and UV both describe an inclined plane; clipping must not
        # create cracks by changing its elevation at the shared tile edge.
        points = [
            np.array([u, 2 * u + v, v, 0, 1, 0, u, v], float)
            for u, v in [(0, 0), (1, 0), (0, 1)]
        ]
        clipped = clip_polygon(points, 0, 0.5, True)
        for p in clipped:
            self.assertGreaterEqual(p[6], 0.5 - 1e-9)
            self.assertAlmostEqual(p[1], 2 * p[0] + p[2])
        self.assertEqual(sum(abs(p[6] - 0.5) < 1e-9 for p in clipped), 2)


class BuiltWorld(unittest.TestCase):
    def test_no_normal_maps_used_as_albedo(self):
        assets = SIM / ".cache/forest-build/cover_assets.json"
        if not assets.exists():
            self.skipTest("source assets not exported")
        for asset in json.loads(assets.read_text()).values():
            for mat in asset["textures"].values():
                self.assertNotRegex(mat["diff"], r"normal|_nor|rough|_mask")
        pine = json.loads(assets.read_text())["cover_01_lod0"]["textures"]["pine_cover"]
        self.assertEqual(pine["diff"], "pine_cover_01_diffuse.png")
        self.assertTrue(pine["invert_alpha"])

    def test_generated_world_has_no_missing_visual_assets(self):
        world = SIM / "worlds/pine_forest/pine_forest.wbt"
        if not world.exists():
            self.skipTest("world not built")
        text = world.read_text()
        self.assertIn('coordinateSystem "ENU"', text)
        self.assertIn("DEF IRONLARK_DRONE IronlarkDrone", text)
        for path in re.findall(r'"(meshes/[^"\n]+)"', text):
            self.assertTrue((world.parent / path).is_file(), path)
        self.assertNotIn("needles_", text)
        self.assertNotIn("crown_", text)
        scene = json.loads((world.parent / "scene.json").read_text())
        self.assertEqual(scene["coordinate_system"], "ENU")


if __name__ == "__main__":
    unittest.main()
