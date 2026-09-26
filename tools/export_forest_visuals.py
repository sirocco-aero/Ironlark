"""Memory-bounded source-tree conversion. Run inside Blender 4.2.

Bake individual source twigs to crossed photographic cards BEFORE evaluating
any tree. Geometry Nodes then places these small cards using the authored
branch graph. No full-resolution tree is ever realized. All changes are in
memory; the source .blend is never saved.
"""

import argparse
import json
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).parent))
from export_forest import loop_uvs, loop_normals, loop_vert_indices
from forest_mesh import write_material_obj

TWIG_OBJECTS = {
    "pine": [f"pine_twig_{i:02}" for i in range(1, 8)],
    "fir": [
        "fir_twig_main_a",
        "fir_twig_main_b",
        "fir_twig_main_c",
        "fir_twig_tip_a",
        "fir_twig_tip_b",
        "fir_twig_tip_c",
        "fir_twig_tip_d",
    ],
    "pine_sapling": [f"pine_twig_{i:02}.001" for i in range(1, 8)],
}


def realize_outputs(obj):
    for modifier in obj.modifiers:
        if modifier.type != "NODES":
            continue
        group = modifier.node_group.copy()
        modifier.node_group = group
        for output in [n for n in group.nodes if n.type == "GROUP_OUTPUT"]:
            socket = output.inputs.get("Geometry")
            if socket and socket.links:
                upstream = socket.links[0].from_socket
                realize = group.nodes.new("GeometryNodeRealizeInstances")
                group.links.new(upstream, realize.inputs["Geometry"])
                group.links.new(realize.outputs["Geometry"], socket)


def log(*args):
    print("FOREST_VISUALS", *args, flush=True)


def simple_material(species):
    # Retain the source's UV attribute, tint, variation and alpha graph.
    # Only replace illumination with emission for a neutral color photograph.
    mat = bpy.data.materials[species.split("_")[0] + "_twig"].copy()
    mat.name = "bake_" + species
    ns, ls = mat.node_tree.nodes, mat.node_tree.links
    out = next(n for n in ns if n.type == "OUTPUT_MATERIAL")
    principled = next(n for n in ns if n.type == "BSDF_PRINCIPLED")
    emission = ns.new("ShaderNodeEmission")
    emission.name = "Ironlark photograph"
    transparent = ns.new("ShaderNodeBsdfTransparent")
    mix = ns.new("ShaderNodeMixShader")
    for name, target in [
        ("Base Color", emission.inputs["Color"]),
        ("Alpha", mix.inputs[0]),
    ]:
        source = principled.inputs[name]
        if source.links:
            ls.new(source.links[0].from_socket, target)
        else:
            target.default_value = source.default_value
    ls.new(transparent.outputs[0], mix.inputs[1])
    ls.new(emission.outputs[0], mix.inputs[2])
    ls.new(mix.outputs[0], out.inputs["Surface"])
    return mat


def bake_twig(obj, species, index, scene, camera):
    """Two orthographic albedo projections, retaining the source object origin."""
    tmp = obj.copy()
    realize_outputs(tmp)
    scene.collection.objects.link(tmp)
    tmp.matrix_world.identity()
    # Most twigs already are meshes. The one procedural twig is small enough
    # to evaluate alone, with its original needle components.
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(
        tmp.evaluated_get(dg), preserve_all_data_layers=True, depsgraph=dg
    )
    bpy.data.objects.remove(tmp, do_unlink=True)
    if not mesh.uv_layers:
        source_uv = loop_uvs(mesh)
        if source_uv is None:
            raise RuntimeError(f"{obj.name}: source twig UVs are missing")
        layer = mesh.uv_layers.new(name="IronlarkSourceUV")
        layer.data.foreach_set("uv", source_uv.astype(np.float32).ravel())
    ob = bpy.data.objects.new("bake_twig", mesh)
    scene.collection.objects.link(ob)
    mesh.materials.clear()
    mesh.materials.append(MATERIALS[species])
    for p in mesh.polygons:
        p.material_index = 0
    vs = np.array([v.co[:] for v in mesh.vertices])
    lo, hi = vs.min(axis=0), vs.max(axis=0)
    center = (lo + hi) / 2
    cards = []
    for view, normal, up in [
        ("top", Vector((0, 0, 1)), Vector((0, 1, 0))),
        ("side", Vector((1, 0, 0)), Vector((0, 0, 1))),
    ]:
        right = up.cross(normal)
        width = float((hi - lo) @ np.abs(right)) * 1.04
        height = float((hi - lo) @ np.abs(up)) * 1.04
        camera.location = Vector(center) + normal * 4
        camera.rotation_euler = (-normal).to_track_quat("-Z", "Y").to_euler()
        camera.data.ortho_scale = width
        scene.render.resolution_x = 512
        scene.render.resolution_y = max(64, round(512 * height / width))
        # Fixed 512-square cells, render rectangular then atlas pack outside Blender.
        path = OUT / f"twig_{species}_{index}_{view}.png"
        if not path.exists() or FORCE:
            scene.view_settings.view_transform = "Standard"
            scene.render.filepath = str(path)
            bpy.ops.render.render(write_still=True)
        # Retain the source needle normals instead of lighting every needle as
        # the flat card. Encode them in this projection's tangent basis.
        mat = MATERIALS[species]
        nodes, links = mat.node_tree.nodes, mat.node_tree.links
        emit = nodes["Ironlark photograph"]
        color_socket = emit.inputs[0].links[0].from_socket
        bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
        normal_socket = bsdf.inputs["Normal"].links[0].from_socket
        created = []
        combine = nodes.new("ShaderNodeCombineXYZ")
        created.append(combine)
        for axis, basis in enumerate((right, up, normal)):
            dot = nodes.new("ShaderNodeVectorMath")
            dot.operation = "DOT_PRODUCT"
            dot.inputs[1].default_value = basis
            links.new(normal_socket, dot.inputs[0])
            links.new(dot.outputs["Value"], combine.inputs[axis])
            created.append(dot)
        scaled = nodes.new("ShaderNodeVectorMath")
        scaled.operation = "MULTIPLY_ADD"
        scaled.inputs[1].default_value = (0.5, 0.5, 0.5)
        scaled.inputs[2].default_value = (0.5, 0.5, 0.5)
        links.new(combine.outputs[0], scaled.inputs[0])
        links.new(scaled.outputs[0], emit.inputs[0])
        created.append(scaled)
        normal_path = OUT / f"twig_{species}_{index}_{view}_normal.png"
        scene.view_settings.view_transform = "Raw"
        scene.render.filepath = str(normal_path)
        bpy.ops.render.render(write_still=True)
        links.new(color_socket, emit.inputs[0])
        for node in created:
            nodes.remove(node)
        corners = [
            Vector(center) + right * a * width / 2 + up * b * height / 2
            for a, b in [(-1, -1), (1, -1), (1, 1), (-1, 1)]
        ]
        cards.append(
            {
                "file": path.name,
                "normal": normal_path.name,
                "corners": [list(p) for p in corners],
                "cell": index * 2 + (view == "side"),
            }
        )
    bpy.data.objects.remove(ob, do_unlink=True)
    bpy.data.meshes.remove(mesh)
    return cards


def replacement(obj, cards, species):
    mesh = bpy.data.meshes.new("ironlark_" + obj.name)
    verts, faces = [], []
    for card in cards:
        n = len(verts)
        verts.extend(card["corners"])
        faces.append(tuple(range(n, n + 4)))
    mesh.from_pydata(verts, [], faces)
    mesh.materials.append(CARD_MATERIALS[species])
    uv = mesh.uv_layers.new()
    # 4x4 atlas; each image is stretched into its cell while geometry retains
    # its measured aspect. Half-texel padding prevents neighbouring cell leaks.
    for poly, card in zip(mesh.polygons, cards):
        col, row = card["cell"] % 4, card["cell"] // 4
        for li, (u, v) in zip(poly.loop_indices, [(0, 0), (1, 0), (1, 1), (0, 1)]):
            uv.data[li].uv = (
                (col + 0.003 + u * 0.994) / 4,
                1 - (row + 1) / 4 + (0.003 + v * 0.994) / 4,
            )
    new = bpy.data.objects.new("ironlark_" + obj.name, mesh)
    new.matrix_world = obj.matrix_world.copy()
    for col in list(obj.users_collection):
        col.objects.unlink(obj)
        col.objects.link(new)
    for group in bpy.data.node_groups:
        for node in group.nodes:
            if (
                node.bl_idname == "GeometryNodeObjectInfo"
                and node.inputs["Object"].default_value == obj
            ):
                node.inputs["Object"].default_value = new


def export_variant(name, scene):
    obj = bpy.data.objects[name].copy()
    procedural = obj.type != "MESH"
    # Some source variants leave foliage as instances at the group output.
    # Mesh extraction alone silently drops those instances. Twigs have already
    # been converted above, so realizing this output is now memory-bounded.
    realize_outputs(obj)
    scene.collection.objects.link(obj)
    obj.location = (0, 0, 0)
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(
        obj.evaluated_get(dg), preserve_all_data_layers=True, depsgraph=dg
    )
    bpy.data.objects.remove(obj, do_unlink=True)
    mesh.calc_loop_triangles()
    log(name, len(mesh.loop_triangles), "source layout triangles")
    uv, norms, lv = loop_uvs(mesh), loop_normals(mesh), loop_vert_indices(mesh)
    if uv is None:
        raise RuntimeError(f"{name}: missing source UVMap")
    positions = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", positions)
    positions = positions.reshape(-1, 3)
    triangle_loops = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int32)
    mesh.loop_triangles.foreach_get("loops", triangle_loops)
    triangle_loops = triangle_loops.reshape(-1, 3)
    material_indices = np.empty(len(mesh.loop_triangles), dtype=np.int32)
    mesh.loop_triangles.foreach_get("material_index", material_indices)
    base = float(positions[:, 2].min())
    # Keep the evaluated material inputs for baking in a fresh process. This
    # contains one tree, with shared source images referenced by path.
    material_object = bpy.data.objects.new(name, mesh)
    from forest_materials import asset_textures

    source_textures = asset_textures(material_object)
    bpy.data.libraries.write(
        str(OUT / f"material_{name}.blend"),
        {material_object},
        path_remap="ABSOLUTE",
        compress=True,
    )
    bpy.data.objects.remove(material_object, do_unlink=True)
    log("attributes", [(a.name, a.domain, a.data_type) for a in mesh.attributes])
    properties_path = OUT / "tree_properties.json"
    properties = (
        json.loads(properties_path.read_text()) if properties_path.exists() else {}
    )
    foliage_materials = [
        i
        for i, m in enumerate(mesh.materials)
        if m and ("twig" in m.name or "ironlark_cards" in m.name)
    ]
    foliage_vertices = lv[
        triangle_loops[np.isin(material_indices, foliage_materials)].ravel()
    ]
    crown_base = (
        float(positions[foliage_vertices, 2].min() - base)
        if len(foliage_vertices)
        else float(np.ptp(positions[:, 2])) * 0.5
    )
    stem = positions[(positions[:, 2] - base > 0.5) & (positions[:, 2] - base < 1.5)]
    trunk_radius = (
        float(np.quantile(np.linalg.norm(stem[:, :2], axis=1), 0.25))
        if len(stem)
        else 0.1
    )
    properties[name] = {
        "base_z": base,
        "height": float(np.ptp(positions[:, 2])),
        "crown_base": max(0.1, crown_base),
        "trunk_r": max(0.05, trunk_radius),
    }
    properties_path.write_text(json.dumps(properties, indent=2))
    parts = []
    for mi, mat in enumerate(mesh.materials):
        if mat is None:
            continue
        tris = triangle_loops[material_indices == mi]
        if not len(tris):
            continue
        inputs = source_textures.get(mat.name, {})
        foliage = (
            mat.name.startswith("ironlark_cards_")
            or "twig" in mat.name
            or bool(inputs.get("alpha"))
        )
        atlas = foliage and procedural
        atlas_name = (
            "pine_sapling"
            if name in ("pine_sapling_medium_a", "pine_sapling_medium_b")
            else "pine"
            if name.startswith("pine")
            else "fir"
        )
        # Preserve the evaluated source's corner normals. Reconstructing each
        # triangle as disconnected geometry destroys the smooth branch normals
        # and prevents the importer from sharing otherwise identical vertices.
        file = f"visual_{name}_{mi}.obj"
        count = write_material_obj(
            OUT / file,
            positions,
            lv,
            uv,
            norms,
            tris,
            base_z=base,
            uv_scale=(1.2, 0.1) if mat.name in ("pine_bark", "fir_bark") else (1, 1),
            two_sided=foliage,
        )
        parts.append(
            {
                "file": file,
                "material": mat.name,
                "foliage": foliage,
                "atlas": atlas,
                "atlas_name": atlas_name,
                "textures": inputs,
                "tris": count,
            }
        )
        log(file, count)
    bpy.data.meshes.remove(mesh)
    return parts


def main():
    global OUT, TEXTURES, MATERIALS, CARD_MATERIALS, FORCE
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--force", action="store_true")
    p.add_argument("--variant")
    p.add_argument("--prepare-only", action="store_true")
    args = p.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    OUT = Path(args.out)
    OUT.mkdir(exist_ok=True, parents=True)
    FORCE = args.force
    if not args.prepare_only and not args.variant:
        p.error("export one --variant per process, or use --prepare-only")
    source = OUT.resolve().parent / "pine-forest/source/polyhaven_pine_fir_forest.blend"
    TEXTURES = source.parent / "textures"
    # Load only the current asset and its dependencies. Opening the whole
    # project retains unrelated scans and consumes most of an 8 GB machine.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    names = TWIG_OBJECTS
    requested = [args.variant] if args.variant else []
    with bpy.data.libraries.load(str(source), link=False) as (_, loaded):
        loaded.objects = requested
    procedural = (
        args.prepare_only
        or not args.variant
        or bpy.data.objects[args.variant].type != "MESH"
    )
    if procedural:
        with bpy.data.libraries.load(str(source), link=False) as (_, loaded):
            loaded.objects = [
                name
                for family in names.values()
                for name in family
                if name not in bpy.data.objects
            ]
    for image in bpy.data.images:
        small = OUT / "texture-source-2k" / Path(image.filepath).name
        if small.exists():
            image.filepath = str(small.resolve())
    material_images = (
        json.loads((OUT / "material_images.json").read_text())
        if (OUT / "material_images.json").exists()
        else {}
    )
    material_images.update(
        {
            m.name: [
                n.image.name
                for n in m.node_tree.nodes
                if n.type == "TEX_IMAGE" and n.image
            ]
            for m in bpy.data.materials
            if m.use_nodes
        }
    )
    (OUT / "material_images.json").write_text(json.dumps(material_images, indent=1))
    scene = bpy.data.scenes.new("Ironlark isolated asset bake")
    bpy.context.window.scene = scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 8
    scene.cycles.device = "CPU"
    scene.cycles.transparent_max_bounces = 16
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0
    scene.view_settings.gamma = 1
    cam = bpy.data.objects.new("bake camera", bpy.data.cameras.new("bake camera"))
    scene.collection.objects.link(cam)
    cam.data.type = "ORTHO"
    cam.data.sensor_fit = "HORIZONTAL"
    scene.camera = cam
    cached_atlas = (
        json.loads((OUT / "twig_atlas.json").read_text())
        if (OUT / "twig_atlas.json").exists() and not FORCE
        else None
    )
    MATERIALS = {s: simple_material(s) for s in names} if procedural else {}
    CARD_MATERIALS = {s: bpy.data.materials.new("ironlark_cards_" + s) for s in names}
    atlas = {}
    for species, objects in names.items() if procedural else []:
        atlas[species] = []
        for i, name in enumerate(objects):
            log("bake", name)
            obj = bpy.data.objects[name]
            cards = (
                [c for c in cached_atlas[species] if c["cell"] // 2 == i]
                if cached_atlas and species in cached_atlas
                else bake_twig(obj, species, i, scene, cam)
            )
            atlas[species].extend(cards)
            replacement(obj, cards, species)
    if procedural:
        (OUT / "twig_atlas.json").write_text(json.dumps(atlas, indent=1))
    if args.prepare_only:
        return
    variants = sorted(
        {r["variant"] for r in json.loads((OUT / "authored_trees.json").read_text())}
    )
    result = (
        json.loads((OUT / "visual_trees.json").read_text())["variants"]
        if (OUT / "visual_trees.json").exists()
        else {}
    )
    for name in [args.variant] if args.variant else variants:
        if name not in bpy.data.objects:
            continue
        result[name] = export_variant(name, scene)
    (OUT / "visual_trees.json").write_text(
        json.dumps({"version": 1, "variants": result}, indent=1)
    )
    log("complete")


if __name__ == "__main__":
    main()
