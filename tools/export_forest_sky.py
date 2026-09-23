"""Render the source world as Webots background and lighting cubemaps.

The source world shows cameras the sunset HDRI (strength 0.2, rotated by its
Mapping node) and lights the scene with a Nishita sky (strength 0.7). Cycles
renders each as an equirectangular panorama, so Blender evaluates its own nodes;
the panoramas are then resampled into cube faces:

  sky_{face}.hdr        what the camera sees
  sky_light_{face}.hdr  what lights the scene (image-based lighting)
"""
import argparse
import math
import sys
from pathlib import Path

import bpy
import numpy as np

p = argparse.ArgumentParser()
p.add_argument("--out", required=True)
out = Path(p.parse_args(sys.argv[sys.argv.index("--") + 1:]).out)
source = out.resolve().parent / "pine-forest/source/polyhaven_pine_fir_forest.blend"

bpy.ops.wm.read_factory_settings(use_empty=True)
with bpy.data.libraries.load(str(source), link=False) as (_, data):
    data.worlds = ["belfast_sunset_puresky"]
world = data.worlds[0]
scene = bpy.context.scene
scene.world = world
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = 8
scene.cycles.use_denoising = False
scene.view_settings.view_transform = "Standard"
scene.render.image_settings.file_format = "OPEN_EXR"
camera = bpy.data.objects.new("panorama", bpy.data.cameras.new("panorama"))
camera.data.type = "PANO"
camera.data.panorama_type = "EQUIRECTANGULAR"
# With this rotation a panorama matches Blender's environment-texture layout:
# u = atan2(y, -x) / 2pi + 1/2, v = atan2(z, |xy|) / pi + 1/2, rows from the bottom.
camera.rotation_euler = (math.pi / 2, 0, -math.pi / 2)
scene.collection.objects.link(camera)
scene.camera = camera


def panorama(name, width):
    scene.render.resolution_x, scene.render.resolution_y = width, width // 2
    path = out / f"{name}_panorama.exr"
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(str(path))
    pixels = np.array(image.pixels[:], np.float32).reshape(width // 2, width, 4)
    bpy.data.images.remove(image)
    path.unlink()
    return pixels


def cube(pixels, prefix, size):
    height, width = pixels.shape[:2]
    u, v = np.meshgrid(np.linspace(-1, 1, size), np.linspace(-1, 1, size))
    one = np.ones_like(u)
    faces = {"right": (one, v, -u), "left": (-one, v, u), "top": (u, one, -v),
             "bottom": (u, -one, v), "front": (-u, v, -one), "back": (u, v, one)}
    for name, parts in faces.items():
        d = np.stack(parts, axis=-1)
        d /= np.linalg.norm(d, axis=-1, keepdims=True)
        # Faces are defined in Webots' Y-up background frame; the world is Z-up.
        x, y, z = d[..., 0], -d[..., 2], d[..., 1]
        sx = ((np.arctan2(y, -x) / (2 * math.pi) + 0.5) * width - 0.5) % width
        sy = np.clip((np.arctan2(z, np.hypot(x, y)) / math.pi + 0.5) * height - 0.5, 0, height - 1)
        ix, iy = np.floor(sx).astype(int), np.floor(sy).astype(int)
        fx, fy = (sx - ix)[..., None], (sy - iy)[..., None]
        ix1, iy1 = (ix + 1) % width, np.minimum(iy + 1, height - 1)
        color = ((pixels[iy, ix] * (1 - fx) + pixels[iy, ix1] * fx) * (1 - fy)
                 + (pixels[iy1, ix] * (1 - fx) + pixels[iy1, ix1] * fx) * fy)
        color[..., 3] = 1
        face = bpy.data.images.new(name, width=size, height=size, float_buffer=True)
        face.pixels.foreach_set(color.astype(np.float32).ravel())
        face.file_format = "HDR"
        face.filepath_raw = str(out / f"{prefix}_{name}.hdr")
        face.save()
        bpy.data.images.remove(face)


mix = next(n for n in world.node_tree.nodes if n.type == "MIX_SHADER")
cube(panorama("camera", 2048), "sky", 512)
# Lighting rays see the first mix input, the Nishita sky.
for link in list(mix.inputs[0].links):
    world.node_tree.links.remove(link)
mix.inputs[0].default_value = 0.0
cube(panorama("light", 512), "sky_light", 128)
print("SKY done", flush=True)
