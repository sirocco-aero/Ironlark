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
        # Upper trunks tile the bark: their UVs run past the unit square, where the renderer
        # wraps them back onto it. The bake writes the square only: those faces bake shifted
        # into it (by whole tiles: where the renderer reads them) and first; the faces inside
        # it then bake over them, exact. Every texel the trunk reads is written.
        uv = uv.astype(np.float32).reshape(-1, 2)
        starts = np.array([p.loop_start for p in mesh.polygons])
        counts = np.array([p.loop_total for p in mesh.polygons])
        face_of_loop = np.repeat(np.arange(len(starts)), counts)
        low = np.minimum.reduceat(uv, starts)
        high = np.maximum.reduceat(uv, starts)
        tiled = ((low < -1e-4) | (high > 1 + 1e-4)).any(axis=1)
        centre = np.add.reduceat(uv, starts) / counts[:, None]
        shifted = uv - np.floor(centre)[face_of_loop] * tiled[face_of_loop, None]
        target_uv = mesh.uv_layers.new(name='IronlarkBake')
        target_uv.data.foreach_set('uv', shifted.ravel())
        mesh.uv_layers.active = target_uv
        target_uv.active_render = True

        def part(faces, name):
            """An object holding only the given faces (with both UV layers)."""
            copy = mesh.copy()
            bm = bmesh.new()
            bm.from_mesh(copy)
            bm.faces.ensure_lookup_table()
            bmesh.ops.delete(bm, geom=[f for f in bm.faces if not faces[f.index]], context='FACES')
            bm.to_mesh(copy)
            bm.free()
            obj = bpy.data.objects.new(name, copy)
            scene.collection.objects.link(obj)
            return obj

        passes = [part(tiled, 'tiled trunk'), part(~tiled, 'trunk')] if tiled.any() else [part(~tiled, 'trunk')]

        def bake_all(kind, kwargs):
            for k, obj in enumerate(passes):
                for other in scene.objects:
                    other.select_set(False)
                obj.select_set(True)
                bpy.context.view_layer.objects.active = obj
                scene.render.bake.use_clear = k == 0
                bpy.ops.object.bake(type=kind, **kwargs)
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
            bake_all(kind, kwargs)
            name = f'baked_{args.variant}_{material.name}_{suffix}.png'
            image.file_format = 'PNG'
            image.filepath_raw = str(out / name)
            image.save()
            files[suffix] = name
            bpy.data.images.remove(image)
            print('TRUNK_MATERIAL', args.variant, material.name, suffix, flush=True)
        result[material.name] = files
        nodes.remove(target)
        for obj in passes:
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.meshes.remove(data)
        bpy.data.meshes.remove(mesh)
    (out / f'baked_{args.variant}.json').write_text(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
