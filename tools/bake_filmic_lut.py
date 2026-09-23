"""Bake Blender's Filmic "Medium High Contrast" view into a 65³ LUT image.

Run inside Blender. Samples are spaced on Filmic's own log shaper
(log2(x / 0.18) in [-12.473931, 12.526069]) so the table covers the
source's full scene-linear range. Slices of blue run left to right; within a
slice red runs along x and green down the rows (PNG row 0 is green 0).
"""
import sys
import bpy
import numpy as np

N = 65
LOW, HIGH = -12.473931188, 12.526068812
out = sys.argv[sys.argv.index("--") + 1]

scene = bpy.context.scene
scene.display_settings.display_device = "sRGB"
scene.view_settings.view_transform = "Filmic"
for look in ("Medium High Contrast", "Filmic - Medium High Contrast"):
    try:
        scene.view_settings.look = look
        break
    except TypeError:
        continue
assert scene.view_settings.look.endswith("Medium High Contrast"), scene.view_settings.look
scene.view_settings.exposure = 0
scene.view_settings.gamma = 1
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_depth = "16"
scene.render.image_settings.color_mode = "RGB"

axis = 0.18 * np.exp2(LOW + (HIGH - LOW) * np.arange(N) / (N - 1))
pixels = np.zeros((N, N * N, 4), np.float32)
for k in range(N):
    pixels[:, k * N:(k + 1) * N, 0] = axis[None, :]
    pixels[:, k * N:(k + 1) * N, 1] = axis[:, None]
    pixels[:, k * N:(k + 1) * N, 2] = axis[k]
pixels[..., 3] = 1
image = bpy.data.images.new("filmic lut", N * N, N, float_buffer=True, alpha=False)
image.colorspace_settings.name = "Linear Rec.709"
# Blender stores rows bottom-up; flip so the saved PNG's first row is green 0.
image.pixels.foreach_set(pixels[::-1].ravel())
image.save_render(out, scene=scene)
print("LUT", scene.view_settings.view_transform, scene.view_settings.look, out, flush=True)
