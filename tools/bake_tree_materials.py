"""Bake the source's blended trunk shaders, including their vertex masks.

Run in a fresh Blender process on one evaluated tree from export_forest_visuals.
The source UV attribute remains available to the shader; a separate UV layer
selects the bake destination. No geometry is simplified.
"""

import argparse
import json
from pathlib import Path
import sys

import bmesh
import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from export_forest import loop_uvs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    parser.add_argument('--variant', required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    out = Path(args.out).resolve()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    with bpy.data.libraries.load(str(out / f'material_{args.variant}.blend')) as (available, data):
        data.objects = available.objects
    if len(data.objects) != 1:
        raise RuntimeError('The material bake expects one evaluated tree')
    source = data.objects[0]
    for img in bpy.data.images:
        path = out / 'texture-source-2k' / Path(img.filepath).name
        if path.exists():
            img.filepath = str(path)
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.samples = 1
    scene.cycles.device = 'CPU'
    scene.render.bake.margin = 12
    result = {}
    for slot, material in enumerate(source.data.materials):
        if not material or '_trunk_' not in material.name:
            continue
        if not any(p.material_index == slot for p in source.data.polygons):
            continue
        mesh = source.data.copy()
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.material_index != slot], context='FACES')
        bm.to_mesh(mesh)
        bm.free()
        mesh.materials.clear()
        mesh.materials.append(material)
        for polygon in mesh.polygons:
            polygon.material_index = 0
        uv = loop_uvs(mesh)
        if uv is None:
            raise RuntimeError(f'{material.name}: source UVMap was lost')
        target_uv = mesh.uv_layers.new(name='LoiterBake')
        target_uv.data.foreach_set('uv', uv.astype(np.float32).ravel())
        mesh.uv_layers.active = target_uv
        target_uv.active_render = True
        ob = bpy.data.objects.new('trunk material', mesh)
        scene.collection.objects.link(ob)
        bpy.context.view_layer.objects.active = ob
        ob.select_set(True)
        nodes = material.node_tree.nodes
        target = nodes.new('ShaderNodeTexImage')
        nodes.active = target
        files = {}
        for suffix, kind in [('diff', 'DIFFUSE'), ('normal', 'NORMAL'), ('rough', 'ROUGHNESS')]:
            image = bpy.data.images.new('trunk bake', width=2048, height=2048)
            if kind != 'DIFFUSE':
                image.colorspace_settings.name = 'Non-Color'
            target.image = image
            kwargs = {'pass_filter': {'COLOR'}} if kind == 'DIFFUSE' else {}
            bpy.ops.object.bake(type=kind, **kwargs)
            name = f'baked_{args.variant}_{material.name}_{suffix}.png'
            image.file_format = 'PNG'
            image.filepath_raw = str(out / name)
            image.save()
            files[suffix] = name
            bpy.data.images.remove(image)
            print('TRUNK_MATERIAL', args.variant, material.name, suffix, flush=True)
        result[material.name] = files
        nodes.remove(target)
        bpy.data.objects.remove(ob, do_unlink=True)
        bpy.data.meshes.remove(mesh)
    (out / f'baked_{args.variant}.json').write_text(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
