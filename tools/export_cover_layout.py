"""Export authored props and upright plants without evaluating tree geometry.

The dense, low-growing moss/litter scatter is exported separately by
export_floor_cover.py for terrain material baking. Authored hero plants remain
3D. No random caps or replacement placements are applied here.

The source picks each placement's level of detail by distance to its Camera1
(lod0 within lod0_distance, lod1 up to lod1_distance, nothing beyond). A
drone's camera moves, so every placement is exported as lod0 with its lod1
twin and both distances; the renderer switches per frame.
"""

import argparse
import json
import re
from pathlib import Path
import sys

import bpy

sys.path.insert(0, str(Path(__file__).parent))
from export_forest import REGION, HOME, CLEAR_RADIUS, to_webots
from export_floor_cover import prune


def source_levels_of_detail(terrain):
    """Map each lod0 object to its lod1 twin and its group's distances, then
    disable the camera culling so every placement is evaluated as lod0."""
    levels = {}
    for node in terrain.nodes:
        if node.type != "GROUP" or not node.node_tree:
            continue
        inputs = {i.name.lower(): i for i in node.inputs}
        if "lod0_distance" not in inputs or "lod1_distance" not in inputs:
            continue
        near, far = inputs["lod0_distance"].default_value, inputs["lod1_distance"].default_value
        for inner in node.node_tree.nodes:
            collection = inner.inputs[0].default_value if inner.type == "COLLECTION_INFO" else None
            if not collection or not collection.name.endswith(("lod0", "lod_0")):
                continue
            for obj in collection.all_objects:
                twin = re.sub(r"lod_?0$", lambda m: m.group(0).replace("0", "1"), obj.name)
                if twin not in bpy.data.objects:
                    raise RuntimeError(f"{obj.name} has no lod1 twin {twin}")
                levels[obj.name] = {"lod1": twin, "lod0_distance": near, "lod1_distance": far}
        inputs["lod0_distance"].default_value = 1e9
        inputs["lod1_distance"].default_value = 2e9
    return levels


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    out = Path(args.out).resolve()
    source = out.parent / "pine-forest/source/polyhaven_pine_fir_forest.blend"
    bpy.ops.wm.read_factory_settings(use_empty=True)
    with bpy.data.libraries.load(str(source), link=False) as (_, loaded):
        loaded.objects = ["terrain_main"]
    bpy.context.scene.collection.objects.link(loaded.objects[0])
    group = bpy.data.node_groups["terrain"]
    excluded = {
        "Group.006",
        "Group.014",
        "Group.007",
        "Group.015",
        "Group.016",
        "Group.017",
        "Group.004",
        "Group.001",
    }
    for link in list(group.nodes["Join Geometry"].inputs[0].links):
        if link.from_node.name in excluded:
            group.links.remove(link)
    prune(group)
    levels = source_levels_of_detail(group)
    bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
    for group in bpy.data.node_groups:
        for node in list(group.nodes):
            if node.bl_idname == "GeometryNodeIsViewport":
                value = group.nodes.new("FunctionNodeInputBool")
                value.boolean = False
                for link in list(node.outputs[0].links):
                    group.links.new(value.outputs[0], link.to_socket)
                group.nodes.remove(node)
    bpy.context.view_layer.update()
    records = []
    xmin, xmax, ymin, ymax = REGION
    for inst in bpy.context.evaluated_depsgraph_get().object_instances:
        if not inst.is_instance or not inst.instance_object:
            continue
        name = inst.instance_object.name
        if name in ("terrain_main", "river_water") or "sapling" in name:
            continue
        p = inst.matrix_world.translation
        if not (xmin <= p.x <= xmax and ymin <= p.y <= ymax):
            continue
        if (p.x - HOME[0]) ** 2 + (p.y - HOME[1]) ** 2 < CLEAR_RADIUS**2:
            continue
        if name not in levels:
            raise RuntimeError(f"No source level of detail for {name}")
        records.append(
            {
                "asset": name,
                **levels[name],
                "pos": to_webots(p.x, p.y, p.z),
                "mat": [
                    float(inst.matrix_world[i][j]) for i in range(3) for j in range(3)
                ],
            }
        )
    (out / "cover.json").write_text(json.dumps(records))
    print(
        "AUTHORED_COVER", len(records), len({r["asset"] for r in records}), flush=True
    )


if __name__ == "__main__":
    main()
