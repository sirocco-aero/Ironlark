"""Webots cube map faces of the forest's skies (tools/export_forest_sky.py writes them)."""
import math

import numpy as np

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


def read_hdr(path):
    """Radiance RGBE as written by export_forest_sky.write_hdr (flat, rows from the top)."""
    data = path.read_bytes()
    start = data.index(b"\n\n") + 2
    end = data.index(b"\n", start)
    _, height, _, width = data[start:end].split()
    rgbe = np.frombuffer(data[end + 1:], np.uint8)[: int(height) * int(width) * 4].reshape(int(height), int(width), 4)
    scale = np.where(rgbe[..., 3:] > 0, np.ldexp(1.0, rgbe[..., 3:].astype(int) - 136), 0.0)
    return rgbe[..., :3] * scale


def irradiance(folder, prefix, normal):
    """Irradiance / pi of the sky faces prefix_{face}.hdr on a surface facing `normal`."""
    total, weight = np.zeros(3), 0.0
    for name in WEBOTS_FACES:
        radiance = read_hdr(folder / f"{prefix}_{name}.hdr")
        d = face_directions(name, radiance.shape[0])
        solid = np.abs(d).max(axis=-1) ** 3  # a cube texel's solid angle, up to a constant
        total += (radiance * (solid * np.clip(d @ np.asarray(normal, float), 0, None))[..., None]).sum(axis=(0, 1))
        weight += solid.sum()
    return total * (4 * math.pi / weight) / math.pi
