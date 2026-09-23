"""Export authored cover meshes with original UVs and normals, in isolation."""

import argparse
import json
from pathlib import Path
import sys

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from export_forest import loop_uvs, loop_normals, loop_vert_indices
from forest_materials import asset_textures
from forest_mesh import write_material_obj


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    out = Path(args.out).resolve()
    source = out.parent / "pine-forest/source/polyhaven_pine_fir_forest.blend"
    records = json.loads((out / "cover.json").read_text())
    names = sorted({r["asset"] for r in records})
    assets = {}
    for name in names:
        # Release source image/mesh buffers between assets as well as scenes.
        bpy.ops.wm.read_factory_settings(use_empty=True)
        with bpy.data.libraries.load(str(source), link=False) as (_, loaded):
            loaded.objects = [name]
        obj = loaded.objects[0]
        if obj is None:
            raise RuntimeError(f"Missing source cover object: {name}")
        bpy.context.scene.collection.objects.link(obj)
        obj.location = (0, 0, 0)
        bpy.context.view_layer.update()
        dg = bpy.context.evaluated_depsgraph_get()
        evaluated = obj.evaluated_get(dg)
        mesh = evaluated.to_mesh()
        mesh.calc_loop_triangles()
        uv, normals, lv = loop_uvs(mesh), loop_normals(mesh), loop_vert_indices(mesh)
        if uv is None:
            raise RuntimeError(f"{name}: missing source UVs")
        textures = asset_textures(obj)
        if len(textures) != 1:
            raise RuntimeError(f"{name}: needs an explicit material split")
        vertices = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", vertices)
        triangles = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int32)
        mesh.loop_triangles.foreach_get("loops", triangles)
        filename = f"cover_{name}.obj"
        count = write_material_obj(
            out / filename,
            vertices.reshape(-1, 3),
            lv,
            uv,
            normals,
            triangles.reshape(-1, 3),
        )
        assets[name] = {
            "file": filename,
            "tris": count,
            "textures": textures,
            "geometry": "source authored LOD; no extra decimation",
        }
        print("SOURCE_COVER", name, count, flush=True)
        evaluated.to_mesh_clear()
    (out / "cover_assets.json").write_text(json.dumps(assets, indent=2))


if __name__ == "__main__":
    main()
