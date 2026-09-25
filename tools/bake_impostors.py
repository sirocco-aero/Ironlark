"""Capture each tree variant as an impostor atlas (Webots Shape.impostor, patch 0016).

Reads the variants' parts as the built world draws them (meshes, textures, the 0.5
alpha mask) from <build>/impostor_variants.json and captures FRAMES x FRAMES
orthographic views on the hemi-octahedral grid pbr_impostor.vert reads. One render
per pass captures a whole atlas: every view is an instanced copy of the tree, turned
so that a single orthographic camera sees it from that view's direction, in its cell.

    blender --background --python bake_impostors.py -- --build <forest-build> --world <world>
        ->  <build>/impostor_<variant>.npz  (albedo: linear RGB, alpha, normal: capture space,
                                            depth: towards the view, 0..1 over the sphere)
            <build>/impostors.json          (capture sphere per variant, in the tree's frame)
"""
import argparse
import json
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector

FRAMES = 8  # must match pbr_impostor.vert and pbr.frag

p = argparse.ArgumentParser()
p.add_argument("--out", "--build", dest="build", type=Path, required=True)
p.add_argument("--world", type=Path, required=True)
p.add_argument("--samples", type=int, default=16)
p.add_argument("--size", type=int, default=256, help="pixels per view")
p.add_argument("--report", type=Path, help="write npz and capture spheres here instead of the build")
p.add_argument("--variants", nargs="*")
args = p.parse_args(sys.argv[sys.argv.index("--") + 1:])
SIZE = args.size
OUT = args.report or args.build

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.samples = args.samples
scene.cycles.use_denoising = False
scene.cycles.max_bounces = 0
scene.cycles.transparent_max_bounces = 128
scene.cycles.pixel_filter_type = "BOX"
scene.view_settings.view_transform = "Standard"
scene.render.image_settings.file_format = "OPEN_EXR"
scene.render.image_settings.color_depth = "32"
scene.render.resolution_x = scene.render.resolution_y = FRAMES * SIZE
world = bpy.data.worlds.new("black")
world.color = (0, 0, 0)
scene.world = world
try:  # a GPU when Blender finds one
    prefs = bpy.context.preferences.addons["cycles"].preferences
    for backend in ("OPTIX", "CUDA", "HIP"):
        try:
            prefs.compute_device_type = backend
        except TypeError:
            continue
        prefs.get_devices()
        if any(d.type == backend for d in prefs.devices):
            for d in prefs.devices:
                d.use = d.type == backend
            scene.cycles.device = "GPU"
            break
except Exception:
    pass
print("IMPOSTOR device", scene.cycles.device, flush=True)


def grid_to_direction(u, v):
    e = np.array([u, v]) * 2 - 1
    x, y = (e[0] + e[1]) / 2, (e[0] - e[1]) / 2
    d = np.array([x, y, 1 - abs(x) - abs(y)])
    return d / np.linalg.norm(d)


def view_basis(d):
    right = np.cross([0, 0, 1], d)
    right /= np.linalg.norm(right)
    return right, np.cross(d, right), d


def image(path, color):
    img = bpy.data.images.load(str(path), check_existing=True)
    img.colorspace_settings.name = "sRGB" if color else "Non-Color"
    return img


# One material per part and pass. Each pass emits one quantity; texels under the
# 0.5 alpha mask let rays through, as Webots' alphaCutoff discards them.
PASSES = ("albedo", "normal", "coverage", "depth")


def material(part, output, radius):
    mat = bpy.data.materials.new(f"{part['mesh']}:{output}")
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    emission = nodes.new("ShaderNodeEmission")
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = image(args.world / part["tex"], True)
    if output == "albedo":
        links.new(tex.outputs["Color"], emission.inputs["Color"])
    elif output == "coverage":
        emission.inputs["Color"].default_value = (1, 1, 1, 1)
    elif output == "depth":
        # Towards the camera (-y) from the view's centre, over the sphere's radius, as 0..1.
        position = nodes.new("ShaderNodeSeparateXYZ")
        links.new(nodes.new("ShaderNodeNewGeometry").outputs["Position"], position.inputs[0])
        depth = nodes.new("ShaderNodeMath")
        depth.operation = "MULTIPLY_ADD"
        depth.inputs[1].default_value, depth.inputs[2].default_value = -0.5 / radius, 0.5
        links.new(position.outputs["Y"], depth.inputs[0])
        links.new(depth.outputs[0], emission.inputs["Color"])
    else:
        # Back faces' normals turned to the camera, as Cycles and Webots (patch 0017) shade foliage.
        normal = nodes.new("ShaderNodeNewGeometry").outputs["Normal"]
        if part.get("normal"):
            normal_tex = nodes.new("ShaderNodeTexImage")
            normal_tex.image = image(args.world / part["normal"], False)
            normal_map = nodes.new("ShaderNodeNormalMap")
            links.new(normal_tex.outputs["Color"], normal_map.inputs["Color"])
            normal = normal_map.outputs["Normal"]
        # The copy's object space is the tree's frame: the capture space.
        to_object = nodes.new("ShaderNodeVectorTransform")
        to_object.vector_type = "NORMAL"
        to_object.convert_from, to_object.convert_to = "WORLD", "OBJECT"
        links.new(normal, to_object.inputs["Vector"])
        # Emission cannot carry negative values: encode as n * 0.5 + 0.5.
        encode = nodes.new("ShaderNodeVectorMath")
        encode.operation = "MULTIPLY_ADD"
        encode.inputs[1].default_value = (0.5, 0.5, 0.5)
        encode.inputs[2].default_value = (0.5, 0.5, 0.5)
        links.new(to_object.outputs["Vector"], encode.inputs[0])
        links.new(encode.outputs["Vector"], emission.inputs["Color"])
    mask = nodes.new("ShaderNodeMath")
    mask.operation = "GREATER_THAN"
    mask.inputs[1].default_value = 0.5
    links.new(tex.outputs["Alpha"], mask.inputs[0])
    mix = nodes.new("ShaderNodeMixShader")
    links.new(mask.outputs[0], mix.inputs["Fac"])
    links.new(nodes.new("ShaderNodeBsdfTransparent").outputs[0], mix.inputs[1])
    links.new(emission.outputs[0], mix.inputs[2])
    links.new(mix.outputs[0], out.inputs["Surface"])
    return mat


def load_mesh(url):
    bpy.ops.wm.obj_import(filepath=str(args.world / url), forward_axis="Y", up_axis="Z")
    obj = bpy.context.selected_objects[0]
    data = obj.data
    data.transform(obj.matrix_world)
    data.materials.clear()
    data.materials.append(None)  # one slot, set per pass
    bpy.data.objects.remove(obj, do_unlink=True)
    return data


camera = bpy.data.objects.new("capture", bpy.data.cameras.new("capture"))
camera.data.type = "ORTHO"
scene.collection.objects.link(camera)
scene.camera = camera

variants = json.loads((args.build / "impostor_variants.json").read_text())
report_path = OUT / "impostors.json"
report = json.loads(report_path.read_text()) if report_path.exists() else {}
for name, parts in sorted(variants.items()):
    if args.variants and name not in args.variants:
        continue
    meshes = [load_mesh(part["mesh"]) for part in parts]
    points = np.concatenate([np.array([v.co for v in m.vertices]) for m in meshes])
    low, high = points.min(0), points.max(0)
    center = (low + high) / 2
    radius = float(np.linalg.norm(points - center, axis=1).max()) * 1.01

    # The copies: view (i, j) sits in atlas column i, row j from the top, seen along +y
    # by the camera: image right = +x, up = +z, towards the camera = -y.
    target = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype=float).T  # columns: right, up, towards
    copies = bpy.data.collections.new(name)
    scene.collection.children.link(copies)
    for i in range(FRAMES):
        for j in range(FRAMES):
            d = grid_to_direction(i / (FRAMES - 1), j / (FRAMES - 1))
            rotation = target @ np.array(view_basis(d))  # rows right, up, d: tree frame -> atlas frame
            cell = np.array([(i - (FRAMES - 1) / 2) * 2 * radius, 0, ((FRAMES - 1) / 2 - j) * 2 * radius])
            matrix = Matrix.Translation(Vector(cell)) @ Matrix(rotation.tolist()).to_4x4() @ Matrix.Translation(Vector(-center))
            for k, mesh in enumerate(meshes):
                obj = bpy.data.objects.new(f"{name}:{i}:{j}:{k}", mesh)
                obj.matrix_world = matrix
                copies.objects.link(obj)
    camera.data.ortho_scale = FRAMES * 2 * radius
    camera.location = (0, -4 * radius, 0)
    camera.rotation_euler = (np.pi / 2, 0, 0)  # looking along +y, up +z
    camera.data.clip_start, camera.data.clip_end = 0.01, 8 * radius

    result = {}
    for output in PASSES:
        # On the shared mesh: object-linked materials would make Cycles copy the mesh per object.
        for mesh, part in zip(meshes, parts):
            mesh.materials[0] = material(part, output, radius)
        path = args.build / f"impostor_{name}_{output}.exr"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        img = bpy.data.images.load(str(path))
        pixels = np.array(img.pixels[:], np.float32).reshape(FRAMES * SIZE, FRAMES * SIZE, 4)[::-1, :, :3]
        bpy.data.images.remove(img)
        path.unlink()
        result[output] = pixels
    coverage = np.clip(result["coverage"][..., 0], 0, 1)

    np.savez_compressed(OUT / f"impostor_{name}.npz", albedo=result["albedo"], normal=result["normal"],
                        depth=result["depth"][..., 0], alpha=coverage)
    report[name] = {"center": center.tolist(), "radius": radius}
    report_path.write_text(json.dumps(report, indent=1))
    print("IMPOSTOR", name, "radius", round(radius, 2), flush=True)

    for obj in list(copies.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.collections.remove(copies)
    for mesh in meshes:
        bpy.data.meshes.remove(mesh)
    for collection in (bpy.data.materials, bpy.data.images):
        for block in list(collection):
            collection.remove(block)
