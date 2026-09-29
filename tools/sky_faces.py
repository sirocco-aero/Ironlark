"""Webots cube map faces of the forest's skies (tools/export_forest_sky.py writes them)."""
import numpy as np

# The source shows cameras its sunset HDRI at strength 0.2; over the open valley the world shows it at
# 0.5 (x 2.5): at 0.2 the far haze, which meets that sky at the horizon, turned the valley grey.
CAMERA_SKY = 2.5

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
