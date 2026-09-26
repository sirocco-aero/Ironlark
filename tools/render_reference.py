"""Render the source scene in Cycles from the forest's preview cameras.

The images are the ground truth for tools/render_forest.py: same poses, field
of view and resolution, with the source's own lights, world, fog and Filmic
view untouched. Needs more RAM than the simulator (about 16 GB); it does not
need a built world.

    ./ironlark render-reference [--views forest backlit]   ->  runs/reference/*.png
"""
import argparse
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
from forest_view import CAMERAS, ORIGIN  # noqa: E402

try:
    import bpy
    from mathutils import Vector
except ImportError:  # outside Blender: fetch the inputs and run this script in Blender
    from forest_pipeline import blender_executable, source_blend

    subprocess.run([str(blender_executable()), "--background", str(source_blend()), "--python", __file__,
                    "--", *sys.argv[1:]], check=True)
    sys.exit(0)

p = argparse.ArgumentParser()
p.add_argument("--out", type=Path, default=TOOLS.parent / "runs/reference")
p.add_argument("--width", type=int, default=1634)
p.add_argument("--height", type=int, default=861)
p.add_argument("--fov", type=float, default=1.0, help="Webots Viewpoint fieldOfView (largest dimension)")
p.add_argument("--samples", type=int, default=256)
p.add_argument("--views", nargs="*")
args = p.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])

# The source trees end by realizing every twig instance into one mesh. Keeping the twigs
# instanced renders the same image (pine_01: mean difference 0.26/255, sampling noise)
# with 3.6x less memory, which is what lets the full scene fit in 16 GB.
for group in bpy.data.node_groups:
    for node in list(getattr(group, "nodes", [])):
        if node.bl_idname == "GeometryNodeRealizeInstances" and node.inputs[0].links and any(
            link.to_node.bl_idname == "NodeGroupOutput" for link in node.outputs[0].links
        ):
            source = node.inputs[0].links[0].from_socket
            for link in list(node.outputs[0].links):
                group.links.new(source, link.to_socket)
            group.nodes.remove(node)

scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.samples = args.samples
scene.cycles.use_denoising = True
scene.render.resolution_x, scene.render.resolution_y = args.width, args.height
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
preferences = bpy.context.preferences.addons["cycles"].preferences
for backend in ("OPTIX", "CUDA", "HIP", "ONEAPI"):
    try:
        preferences.compute_device_type = backend
    except TypeError:
        continue
    preferences.get_devices()
    if any(d.type == backend for d in preferences.devices):
        for device in preferences.devices:
            device.use = device.type == backend
        scene.cycles.device = "GPU"
        break

camera = bpy.data.objects.new("reference", bpy.data.cameras.new("reference"))
camera.data.sensor_fit = "AUTO"  # like Webots: the angle spans the larger dimension
camera.data.angle = args.fov
camera.data.clip_start = 0.05
camera.data.clip_end = 1000
scene.collection.objects.link(camera)
scene.camera = camera

args.out.mkdir(parents=True, exist_ok=True)
for name, pose in CAMERAS.items():
    if args.views and name not in args.views:
        continue
    eye = Vector(pose["eye"]) + Vector((*ORIGIN, 0))
    target = Vector(pose["target"]) + Vector((*ORIGIN, 0))
    camera.location = eye
    camera.rotation_euler = (target - eye).to_track_quat("-Z", "Y").to_euler()
    scene.render.filepath = str(args.out / f"{name}.png")
    bpy.ops.render.render(write_still=True)
    print("REFERENCE", name, flush=True)
