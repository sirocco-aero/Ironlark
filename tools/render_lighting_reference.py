"""Cycles render of the built forest, lit exactly as the source scene is.

The full source needs more than ~26 GB to render. This answers the lighting
half of that comparison within a few GB: the geometry Webots draws (every
instanced Shape, with its transforms and its per-camera distance band),
lit by the source's own suns, world, fog volume and Filmic view. Differences
from tools/render_forest.py are then renderer differences only.

    .venv/bin/python tools/render_lighting_reference.py [--views forest backlit]
        ->  runs/lighting-reference/<view>.png
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))
from forest_view import CAMERAS, ORIGIN  # noqa: E402

try:
    import bpy
    import numpy as np
    from mathutils import Vector
except ImportError:  # outside Blender: run this script in Blender
    from forest_pipeline import blender_executable, source_blend

    subprocess.run([str(blender_executable()), "--background", "--factory-startup", "--python", __file__,
                    "--", "--source", str(source_blend()), *sys.argv[1:]], check=True)
    sys.exit(0)

p = argparse.ArgumentParser()
p.add_argument("--source", type=Path, required=True)
p.add_argument("--world", type=Path, default=ROOT / "worlds/pine_forest")
p.add_argument("--build", type=Path, default=ROOT / ".cache/forest-build")
p.add_argument("--out", type=Path, default=ROOT / "runs/lighting-reference")
p.add_argument("--width", type=int, default=1634)
p.add_argument("--height", type=int, default=861)
p.add_argument("--fov", type=float, default=1.0)
p.add_argument("--samples", type=int, default=128)
p.add_argument("--views", nargs="*")
p.add_argument("--lights", choices=["all", "sky", "main", "secondary"], default="all",
               help="light the scene with one of the source's lights only (fog off), to compare components")
args = p.parse_args(sys.argv[sys.argv.index("--") + 1:])

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene

# The source's lighting, untouched apart from shifting the fog box into world coordinates.
with bpy.data.libraries.load(str(args.source), link=False) as (_, data):
    data.objects = ["sun_main", "sun_secondary", "fog"]
    data.worlds = ["belfast_sunset_puresky"]
for obj in data.objects:
    scene.collection.objects.link(obj)
    obj.location -= Vector((*ORIGIN, 0))
scene.world = data.worlds[0]
if args.lights != "all":
    # One light alone, fog off: the camera still sees the visible sky (HDRI), lighting only
    # comes from the chosen source.
    scene.collection.objects.unlink(next(o for o in data.objects if o.name == "fog"))
    for sun in data.objects:
        if sun.type == "LIGHT" and sun.name != f"sun_{args.lights}":
            sun.data.energy = 0
    if args.lights != "sky":
        nodes = scene.world.node_tree.nodes
        next(n for n in nodes if n.type == "BACKGROUND" and n.name == "Background").inputs["Strength"].default_value = 0
scene.view_settings.view_transform = "Filmic"
scene.view_settings.look = "Medium High Contrast"
scene.render.engine = "CYCLES"
scene.cycles.samples = args.samples
scene.cycles.use_denoising = True
scene.render.resolution_x, scene.render.resolution_y = args.width, args.height
scene.render.image_settings.file_format = "PNG"

images = {}


def image(path, color=True):
    key = (str(path), color)
    if key not in images:
        images[key] = bpy.data.images.load(str(path), check_existing=True)
        images[key].colorspace_settings.name = "sRGB" if color else "Non-Color"
    return images[key]


def material(name, base=None, normal=None, roughness=None, alpha_cutoff=None, base_color=None, rough_value=0.9):
    """The Webots PBRAppearance, as a Principled BSDF."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes["Principled BSDF"]
    bsdf.inputs["Metallic"].default_value = 0
    bsdf.inputs["Roughness"].default_value = rough_value
    if base_color:
        bsdf.inputs["Base Color"].default_value = (*base_color, 1)
    if base:
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = image(base)
        links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        if alpha_cutoff:  # glTF MASK, as Webots' alphaCutoff renders it
            step = nodes.new("ShaderNodeMath")
            step.operation = "GREATER_THAN"
            step.inputs[1].default_value = alpha_cutoff
            links.new(tex.outputs["Alpha"], step.inputs[0])
            links.new(step.outputs[0], bsdf.inputs["Alpha"])
    if normal:
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = image(normal, color=False)
        map_ = nodes.new("ShaderNodeNormalMap")
        links.new(tex.outputs["Color"], map_.inputs["Color"])
        links.new(map_.outputs["Normal"], bsdf.inputs["Normal"])
    if roughness:
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = image(roughness, color=False)
        links.new(tex.outputs["Color"], bsdf.inputs["Roughness"])
    return mat


meshes = {}


def mesh(url, mat):
    if (url, mat.name) not in meshes:
        bpy.ops.wm.obj_import(filepath=str(args.world / url), forward_axis="Y", up_axis="Z")
        obj = bpy.context.selected_objects[0]
        data = obj.data
        data.transform(obj.matrix_world)
        data.materials.clear()
        data.materials.append(mat)
        bpy.data.objects.remove(obj, do_unlink=True)
        meshes[(url, mat.name)] = data
    return meshes[(url, mat.name)]


def instancer():
    """Instance a prototype on points with per-point rotation and scale attributes."""
    g = bpy.data.node_groups.new("instancer", "GeometryNodeTree")
    g.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    g.interface.new_socket("Prototype", in_out="INPUT", socket_type="NodeSocketObject")
    g.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nodes, links = g.nodes, g.links
    gi, go = nodes.new("NodeGroupInput"), nodes.new("NodeGroupOutput")
    info = nodes.new("GeometryNodeObjectInfo")
    info.transform_space = "ORIGINAL"
    info.inputs["As Instance"].default_value = True
    place = nodes.new("GeometryNodeInstanceOnPoints")
    rot = nodes.new("GeometryNodeInputNamedAttribute")
    rot.data_type = "QUATERNION"
    rot.inputs["Name"].default_value = "rot"
    scl = nodes.new("GeometryNodeInputNamedAttribute")
    scl.data_type = "FLOAT_VECTOR"
    scl.inputs["Name"].default_value = "scl"
    links.new(gi.outputs["Geometry"], place.inputs["Points"])
    links.new(gi.outputs["Prototype"], info.inputs["Object"])
    links.new(info.outputs["Geometry"], place.inputs["Instance"])
    links.new(rot.outputs["Attribute"], place.inputs["Rotation"])
    links.new(scl.outputs["Attribute"], place.inputs["Scale"])
    links.new(place.outputs["Instances"], go.inputs["Geometry"])
    return g


def decompose(rows):
    """3x4 rows -> origins, rotation quaternions and signed scales (mirrors keep det > 0 rotations)."""
    basis = rows[:, :, :3]
    scales = np.linalg.norm(basis, axis=1)  # column norms
    rotation = basis / scales[:, None, :]
    mirrored = np.linalg.det(rotation) < 0
    scales[mirrored, 0] *= -1
    rotation[mirrored, :, 0] *= -1
    from mathutils import Matrix

    quats = [Matrix(r.tolist()).to_quaternion() for r in rotation]
    return rows[:, :, 3], quats, scales


# Every instanced Shape of the Webots world, with its visibility band.
text = (args.world / "pine_forest.wbt").read_text()
shapes = []
for found in re.finditer(r"instancesUrl", text):
    # The block runs from this instance list's own Shape to its closing brace.
    start = text.rfind("Shape {", 0, found.start())
    block = text[start:text.index("\n        }", found.start())]
    def field(name):
        match = re.search(name + r' ImageTexture \{ url "([^"]+)"', block)
        # Compressed textures keep their PNG beside them (tools/build_forest_world.py).
        return args.world / match[1].replace(".dds", ".png") if match else None

    if "impostor TRUE" in block:
        continue  # the reference draws the trees themselves at every distance
    band = re.search(r"visibilityRange ([\d.]+) ([\d.]+)", block)
    if "meshes/visual_" in block:
        band = None
    rows = np.fromfile(args.world / re.search(r'instancesUrl \[ "([^"]+)" \]', block)[1], dtype="<f4")
    rows = rows.reshape(-1, 3, 4).astype(np.float64)
    origins, quats, scales = decompose(rows)
    shapes.append({
        "mesh": re.search(r'geometry Mesh \{ url "([^"]+)"', block)[1],
        "material": material("m%d" % len(shapes), field("baseColorMap"), field("normalMap"), field("roughnessMap"), 0.5),
        "origins": origins, "quats": quats, "scales": scales,
        "band": (float(band[1]), float(band[2])) if band else (0.0, 0.0),
    })
print("SHAPES", len(shapes), sum(len(s["origins"]) for s in shapes), flush=True)

# Terrain with its unlit albedo (Cycles casts the canopy shadows itself), river and pad.
for x in range(2):
    for y in range(2):
        albedo = args.build / f"terrain_{x}_{y}_floor.png"
        if not albedo.exists():
            albedo = args.build / f"terrain_{x}_{y}_diff.png"
        mat = material(f"terrain_{x}_{y}", albedo, args.world / f"meshes/terrain_{x}_{y}_normal.png", None, None)
        ob = bpy.data.objects.new(f"terrain_{x}_{y}", mesh(f"meshes/terrain_{x}_{y}.obj", mat))
        scene.collection.objects.link(ob)
river = material("river", base_color=(0.10, 0.23, 0.26), rough_value=0.15)
scene.collection.objects.link(bpy.data.objects.new("river", mesh("meshes/river.obj", river)))

tree = instancer()
socket = tree.interface.items_tree["Prototype"].identifier
instancers = []
for i, shape in enumerate(shapes):
    prototype = bpy.data.objects.new(f"prototype_{i}", mesh(shape["mesh"], shape["material"]))
    points = bpy.data.meshes.new(f"points_{i}")
    ob = bpy.data.objects.new(f"instances_{i}", points)
    scene.collection.objects.link(ob)
    modifier = ob.modifiers.new("instancer", "NODES")
    modifier.node_group = tree
    modifier[socket] = prototype
    instancers.append((shape, ob))

camera = bpy.data.objects.new("reference", bpy.data.cameras.new("reference"))
camera.data.sensor_fit = "AUTO"
camera.data.angle = args.fov
camera.data.clip_start = 0.05
camera.data.clip_end = 1000
scene.collection.objects.link(camera)
scene.camera = camera
args.out.mkdir(parents=True, exist_ok=True)

for name, pose in CAMERAS.items():
    if args.views and name not in args.views:
        continue
    eye, target = Vector(pose["eye"]), Vector(pose["target"])
    camera.location = eye
    camera.rotation_euler = (target - eye).to_track_quat("-Z", "Y").to_euler()
    # The same instances Webots draws from here: origin distance within each band.
    for shape, ob in instancers:
        distance = np.linalg.norm(shape["origins"] - np.array(eye), axis=1)
        near, far = shape["band"]
        keep = np.nonzero((distance >= near) & ((far <= 0) | (distance < far)))[0]
        points = bpy.data.meshes.new("points")
        points.vertices.add(len(keep))
        points.vertices.foreach_set("co", shape["origins"][keep].ravel())
        points.attributes.new("rot", "QUATERNION", "POINT").data.foreach_set(
            "value", [c for k in keep for c in shape["quats"][k]])
        points.attributes.new("scl", "FLOAT_VECTOR", "POINT").data.foreach_set("vector", shape["scales"][keep].ravel())
        old, ob.data = ob.data, points
        bpy.data.meshes.remove(old)
    scene.render.filepath = str(args.out / f"{name}.png")
    bpy.ops.render.render(write_still=True)
    print("LIGHTING_REFERENCE", name, flush=True)
