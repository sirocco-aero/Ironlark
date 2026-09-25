"""The source's fog volume as a Webots scattering Fog, shadowed by the canopy bake."""

import json

import numpy as np
from PIL import Image

# The source scene's `fog` object: a box with a Volume Scatter material
# (color 0.8, density 0.004, anisotropy 0.8), in Blender world coordinates.
# Loiter keeps its height but not its sides: from the world's edge the box's
# walls showed as hard lines, so the fog is a horizontal layer. Seen from above,
# its top was a hard line too: above the box, density decays over FOG_FALLOFF
# metres instead of stopping, and inside it stays the source's.
FOG_FALLOFF = 10.0
FOG_CENTER = (0.6179197, 2.4716792, 11.4727554)
FOG_SIZE = (208.8162231, 208.8162231, 29.1523743)
FOG_COLOR = 0.8
FOG_DENSITY = 0.004
FOG_ANISOTROPY = 0.8


def srgb(linear):
    return 12.92 * linear if linear <= 0.0031308 else 1.055 * linear ** (1 / 2.4) - 0.055


def sun_occlusion(build, out, home):
    """Stitch the canopy bakes into one top-down map in world coordinates.

    R: sun visibility at the ground, normalized like the terrain composite.
    G: ground height. Row 0 is the map's minimum y, as WREN uploads images.
    """
    tiles = {(x, y): np.load(build / f"terrain_{x}_{y}_light.npy").mean(axis=2) for x in range(2) for y in range(2)}
    reference = float(np.quantile(np.concatenate([t.ravel() for t in tiles.values()]), 0.995))
    size = next(iter(tiles.values())).shape[0]
    visibility = np.zeros((2 * size, 2 * size), np.float32)
    for (x, y), tile in tiles.items():
        # Bake rows run from high v to low v; flip so rows increase with v (world y).
        visibility[y * size:(y + 1) * size, x * size:(x + 1) * size] = tile[::-1]
    visibility = np.clip(visibility / reference, 0, 1)

    xmin, ymin, xmax, ymax = json.loads((build / "terrain_meta.json").read_text())["planar"]
    world_min = (xmin - home[0], ymin - home[1])
    world_max = (xmax - home[0], ymax - home[1])

    # heights.json: legacy (x, z) grid in world x and -y, rows from max z (min y).
    grid = json.loads((build / "heights.json").read_text())
    heights = np.array(grid["grid"], np.float32)
    grid_x = np.linspace(grid["x"][0], grid["x"][1], heights.shape[1])
    grid_y = np.linspace(-grid["z"][1], -grid["z"][0], heights.shape[0])
    columns = np.interp(np.linspace(world_min[0], world_max[0], 2 * size), grid_x, np.arange(len(grid_x)))
    rows = np.interp(np.linspace(world_min[1], world_max[1], 2 * size), grid_y, np.arange(len(grid_y)))
    c0, r0 = np.floor(columns).astype(int), np.floor(rows).astype(int)
    c1, r1 = np.minimum(c0 + 1, len(grid_x) - 1), np.minimum(r0 + 1, len(grid_y) - 1)
    fc, fr = (columns - c0)[None, :], (rows - r0)[:, None]
    ground = ((heights[np.ix_(r0, c0)] * (1 - fc) + heights[np.ix_(r0, c1)] * fc) * (1 - fr)
              + (heights[np.ix_(r1, c0)] * (1 - fc) + heights[np.ix_(r1, c1)] * fc) * fr)
    low, high = float(ground.min()), float(ground.max())

    image = np.zeros((2 * size, 2 * size, 3), np.uint8)
    image[..., 0] = np.round(visibility * 255)
    image[..., 1] = np.round((ground - low) / max(high - low, 1e-6) * 255)
    Image.fromarray(image).save(out / "meshes/sun_occlusion.png")
    return world_min, world_max, (low, high)


def fog_node(build, out, home):
    ambient = json.loads((build / "sky_light_mean.json").read_text())
    # The first assembly precedes the canopy bake; the lit reassembly adds the occlusion.
    baked = all((build / f"terrain_{x}_{y}_light.npy").exists() for x in range(2) for y in range(2))
    if baked:
        world_min, world_max, heights = sun_occlusion(build, out, home)
        occlusion = f"""
  sunOcclusionUrl [ "meshes/sun_occlusion.png" ]
  sunOcclusionMin {world_min[0]:.4f} {world_min[1]:.4f}
  sunOcclusionMax {world_max[0]:.4f} {world_max[1]:.4f}
  sunOcclusionHeight {heights[0]:.4f} {heights[1]:.4f}"""
    else:
        occlusion = ""
    # Far haze meets the visible sky at the horizon (exported with the sky).
    horizon_path = build / "sky_horizon.json"
    horizon = ""
    if horizon_path.exists():
        values = ", ".join(" ".join(f"{c:.4f}" for c in v) for v in json.loads(horizon_path.read_text()))
        horizon = f"\n  horizonRadiance [ {values} ]"
    center = (FOG_CENTER[0] - home[0], FOG_CENTER[1] - home[1], FOG_CENTER[2])
    color = srgb(FOG_COLOR)
    return f"""Fog {{
  fogType "SCATTERING"
  color {color:.4f} {color:.4f} {color:.4f}
  density {FOG_DENSITY}
  anisotropy {FOG_ANISOTROPY}
  boxCenter {center[0]:.4f} {center[1]:.4f} {center[2]:.4f}
  boxSize 0 0 {FOG_SIZE[2]:.4f}
  boxFalloff {FOG_FALLOFF}
  ambientColor {' '.join(f'{srgb(c):.4f}' for c in ambient)}{occlusion}{horizon}
}}"""
