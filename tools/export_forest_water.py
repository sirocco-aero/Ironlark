"""Export the source river surface without polygon reduction."""

import argparse
import json
from pathlib import Path
import sys

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from export_forest import (
    REGION,
    HOME,
    TERRAIN_MARGIN,
    loop_normals,
    loop_vert_indices,
    mesh_world_verts,
)
from export_floor_cover import prune
from forest_mesh import write_material_obj


def flow_attribute(mesh, name, loop_vertices):
    """A scalar attribute of the river's Geometry Nodes, per loop."""
    attribute = mesh.attributes[name]
    size = len(attribute.data)
    values = np.empty(size * (4 if attribute.data_type in ("FLOAT_COLOR", "BYTE_COLOR") else 1), dtype=np.float32)
    key = "color" if attribute.data_type in ("FLOAT_COLOR", "BYTE_COLOR") else "value"
    attribute.data.foreach_get(key, values)
    values = values.reshape(size, -1)[:, 0]
    return values[loop_vertices] if attribute.domain == "POINT" else values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    out = Path(args.out).resolve()
    source = out.parent / "pine-forest/source/polyhaven_pine_fir_forest.blend"
    bpy.ops.wm.read_factory_settings(use_empty=True)
    with bpy.data.libraries.load(str(source), link=False) as (_, loaded):
        loaded.objects = ["river_water"]
    obj = loaded.objects[0]
    bpy.context.scene.collection.objects.link(obj)
    terrain = bpy.data.node_groups.get("terrain")
    if terrain:
        terrain.links.new(
            terrain.nodes["landscape_subdivide"].outputs["Geometry"],
            terrain.nodes["Group Output"].inputs["Geometry"],
        )
        prune(terrain)
    bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(dg)
    mesh = evaluated.to_mesh()
    mesh.calc_loop_triangles()
    vertices = mesh_world_verts(obj, mesh)
    lv = loop_vert_indices(mesh)
    triangles = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int32)
    mesh.loop_triangles.foreach_get("loops", triangles)
    triangles = triangles.reshape(-1, 3)
    centers = vertices[lv[triangles]].mean(axis=1)
    xmin, xmax, ymin, ymax = REGION
    margin = TERRAIN_MARGIN
    keep = (
        (centers[:, 0] >= xmin - margin)
        & (centers[:, 0] <= xmax + margin)
        & (centers[:, 1] >= ymin - margin)
        & (centers[:, 1] <= ymax + margin)
    )
    # The source's flow coordinates: UV1 runs along the river, UV2 across it.
    uv = np.stack([flow_attribute(mesh, name, lv) for name in ("UV1", "UV2")], axis=1)
    vertices[:, :2] -= HOME[:2]
    count = write_material_obj(
        out / "river.obj", vertices, lv, uv, loop_normals(mesh), triangles[keep]
    )
    evaluated.to_mesh_clear()
    (out / "river_meta.json").write_text(
        json.dumps({"tris": count, "geometry": "source surface"})
    )
    print("SOURCE_RIVER", count, flush=True)


if __name__ == "__main__":
    main()
