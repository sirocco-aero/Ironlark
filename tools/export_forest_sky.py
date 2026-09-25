"""Render the source world as Webots background and lighting cubemaps.

The source world shows cameras the sunset HDRI (strength 0.2, rotated by its
Mapping node) and lights the scene with a Nishita sky (strength 0.7). Cycles
renders each as an equirectangular panorama, so Blender evaluates its own nodes;
the panoramas are then resampled into cube faces:

  sky_{face}.hdr        what the camera sees
  sky_light_{face}.hdr  what lights the scene (image-based lighting)
  sky_light_mean.json   its mean radiance over the sphere (linear RGB)
  sky_horizon.json      the visible sky just above the horizon, 16 azimuths (linear RGB)
  sky_phase_radiance.json  sky light the fog scatters along each view direction (9 x 8 grid)
"""
import argparse
import json
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
# u = -atan2(y, x) / 2pi + 1/2 (+x at the centre), v = atan2(z, |xy|) / pi + 1/2, rows
# from the bottom.
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


# Webots (ENU) fills OpenGL cube face g (+X, -X, +Y, -Y, +Z, -Z) from the url field
# named here, rotated as its loader does, and samples it with (x, y, -z) of a world
# direction (WbBackground.cpp, skybox.frag, pbr.frag).
WEBOTS_FACES = {"back": (0, 90), "front": (1, -90), "right": (2, 0), "left": (3, 180), "bottom": (4, -90), "top": (5, -90)}


def face_directions(name, size):
    """World direction shown by each file pixel (rows from the top) of a Webots face."""
    face, rotation = WEBOTS_FACES[name]
    row, col = np.mgrid[0:size, 0:size]
    last = size - 1
    # File pixel -> texel of the rotated face uploaded to OpenGL (row = t, column = s).
    r, c = {0: (row, col), 90: (col, last - row), -90: (last - col, row), 180: (last - row, last - col)}[rotation]
    sc, tc = 2 * (c + 0.5) / size - 1, 2 * (r + 0.5) / size - 1
    one = np.ones_like(sc)
    sample = [(one, -tc, -sc), (-one, -tc, sc), (sc, one, tc), (sc, -one, -tc), (sc, -tc, one), (-sc, -tc, -one)][face]
    d = np.stack([sample[0], sample[1], -sample[2]], axis=-1)
    return d / np.linalg.norm(d, axis=-1, keepdims=True)


def write_hdr(path, rgb):
    """Radiance RGBE, rows from the top, values as given: Blender's own save applies the
    display transform, which left the sky sRGB-encoded (2.4x too bright at 0.18)."""
    rgb = np.maximum(rgb.astype(np.float64), 0)
    peak = rgb.max(axis=-1)
    mantissa, exponent = np.frexp(peak)
    scale = np.where(peak > 1e-32, mantissa * 256 / np.maximum(peak, 1e-300), 0)
    rgbe = np.zeros(rgb.shape[:2] + (4,), np.uint8)
    rgbe[..., :3] = np.clip(rgb * scale[..., None], 0, 255).astype(np.uint8)
    rgbe[..., 3] = np.where(peak > 1e-32, exponent + 128, 0)
    height, width = rgb.shape[:2]
    with open(path, "wb") as stream:
        stream.write(f"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y {height} +X {width}\n".encode())
        stream.write(rgbe.tobytes())


def cube(pixels, prefix, size):
    height, width = pixels.shape[:2]
    for name in WEBOTS_FACES:
        d = face_directions(name, size)
        x, y, z = d[..., 0], d[..., 1], d[..., 2]
        sx = ((0.5 - np.arctan2(y, x) / (2 * math.pi)) * width - 0.5) % width
        sy = np.clip((np.arctan2(z, np.hypot(x, y)) / math.pi + 0.5) * height - 0.5, 0, height - 1)
        ix, iy = np.floor(sx).astype(int), np.floor(sy).astype(int)
        fx, fy = (sx - ix)[..., None], (sy - iy)[..., None]
        ix1, iy1 = (ix + 1) % width, np.minimum(iy + 1, height - 1)
        color = ((pixels[iy, ix] * (1 - fx) + pixels[iy, ix1] * fx) * (1 - fy)
                 + (pixels[iy1, ix] * (1 - fx) + pixels[iy1, ix1] * fx) * fy)
        write_hdr(out / f"{prefix}_{name}.hdr", color[..., :3])


def horizon(pixels, count=16, low=0.5, high=1.5):
    """Mean radiance between low and high degrees above the horizon, at count azimuths
    from +x counterclockwise (Fog.horizonRadiance)."""
    height, width = pixels.shape[:2]
    rows = slice(int((low / 180 + 0.5) * height), int((high / 180 + 0.5) * height) + 1)  # rows from the bottom
    band = pixels[rows, :, :3].mean(0)
    samples = []
    for i in range(count):
        column = int(((0.5 - i / count) * width) % width)
        around = [(column + k) % width for k in range(-width // (4 * count), width // (4 * count) + 1)]
        samples.append(band[around].mean(0).tolist())
    return samples


mix = next(n for n in world.node_tree.nodes if n.type == "MIX_SHADER")
camera_sky = panorama("camera", 2048)
cube(camera_sky, "sky", 512)
(out / "sky_horizon.json").write_text(json.dumps(horizon(camera_sky)))
# Lighting rays see the first mix input, the Nishita sky.
for link in list(mix.inputs[0].links):
    world.node_tree.links.remove(link)
mix.inputs[0].default_value = 0.0
light = panorama("light", 512)
cube(light, "sky_light", 128)
# Equirectangular rows cover equal latitude steps; weight by cos(latitude) for solid angle.
latitude = (np.arange(light.shape[0]) + 0.5) / light.shape[0] * math.pi - math.pi / 2
weights = np.cos(latitude)[:, None]
mean = (light[..., :3] * weights[..., None]).sum((0, 1)) / (weights.sum() * light.shape[1])
(out / "sky_light_mean.json").write_text(json.dumps([float(c) for c in mean]))


def phase_radiance(pixels, anisotropy, elevations=9, azimuths=8):
    """Sky light a forward-scattering medium sends along each view direction: the integral of
    L(w) * HG(w . d) over the sphere, for d on an elevation (-90..90) by azimuth (from +x,
    counterclockwise) grid. Below the horizon the lighting sky is black, as the ground it stands
    for is dark."""
    height, width = pixels.shape[:2]
    lat = (np.arange(height) + 0.5) / height * math.pi - math.pi / 2  # rows from the bottom
    lon = (0.5 - (np.arange(width) + 0.5) / width) * 2 * math.pi       # u = -atan2(y, x) / 2pi + 1/2
    lat, lon = np.meshgrid(lat, lon, indexing="ij")
    w = np.stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)], -1)
    solid = np.cos(lat) * (math.pi / height) * (2 * math.pi / width)
    radiance = pixels[..., :3] * solid[..., None]
    g = anisotropy
    table = []
    for e in np.linspace(-math.pi / 2, math.pi / 2, elevations):
        for a in np.arange(azimuths) * 2 * math.pi / azimuths:
            d = np.array([math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)])
            c = w @ d
            phase = (1 - g * g) / (4 * math.pi * (1 + g * g - 2 * g * c) ** 1.5)
            table.append((radiance * phase[..., None]).sum((0, 1)).tolist())
    return table


# The source's fog: Volume Scatter, anisotropy 0.8 (tools/forest_fog.py).
(out / "sky_phase_radiance.json").write_text(json.dumps(phase_radiance(light, 0.8)))
print("SKY done", flush=True)
