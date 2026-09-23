"""Read the source's render-time tree choices and transforms without foliage.

Only the tree-scatter outputs are evaluated. Disabling the referenced tree
modifiers avoids realizing any needle geometry; it does not alter the scatter
graph or its instance transforms. The source file is never saved.
"""
import argparse
import json
from pathlib import Path
import re
import sys

import bpy

sys.path.insert(0, str(Path(__file__).parent))
from export_forest import REGION, HOME, CLEAR_RADIUS
from export_floor_cover import prune


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    out = Path(args.out).resolve()
    source = out.parent / 'pine-forest/source/polyhaven_pine_fir_forest.blend'
    bpy.ops.wm.read_factory_settings(use_empty=True)
    with bpy.data.libraries.load(str(source), link=False) as (_, data):
        data.objects = ['terrain_main']
    terrain = data.objects[0]
    bpy.context.scene.collection.objects.link(terrain)
    group = bpy.data.node_groups['terrain']
    join = group.nodes['Join Geometry']
    tree_groups = {'Group.006', 'Group.014', 'Group.007', 'Group.015', 'Group.016', 'Group.017'}
    for link in list(join.inputs[0].links):
        if link.from_node.name not in tree_groups:
            group.links.remove(link)
    prune(group)
    pattern = re.compile(r'^(pine_\d+|silver_fir_\d+|.*sapling.*)$')
    names = set()
    for obj in bpy.data.objects:
        if pattern.match(obj.name):
            names.add(obj.name)
            obj.modifiers.clear()
    bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
    for group in bpy.data.node_groups:
        for node in list(group.nodes):
            if node.bl_idname == 'GeometryNodeIsViewport':
                value = group.nodes.new('FunctionNodeInputBool')
                value.boolean = False
                for link in list(node.outputs[0].links):
                    group.links.new(value.outputs[0], link.to_socket)
                group.nodes.remove(node)
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    records = []
    xmin, xmax, ymin, ymax = REGION
    for inst in dg.object_instances:
        if not inst.is_instance or not inst.instance_object:
            continue
        name = inst.instance_object.name
        if name not in names:
            continue
        p = inst.matrix_world.translation
        if not (xmin <= p.x <= xmax and ymin <= p.y <= ymax):
            continue
        if (p.x-HOME[0])**2 + (p.y-HOME[1])**2 < CLEAR_RADIUS**2:
            continue
        records.append({'variant': name, 'matrix': [list(row) for row in inst.matrix_world]})
    (out / 'authored_trees.json').write_text(json.dumps(records))
    counts = {name: sum(r['variant'] == name for r in records) for name in sorted({r['variant'] for r in records})}
    print('AUTHORED_TREES', len(records), counts, flush=True)


if __name__ == '__main__':
    main()
