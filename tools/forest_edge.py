"""The hillside just past the source's edges: the region's ground and forest mirrored across each
edge (as tools/forest_backdrop.py places them), falling away towards the valley
(tools/forest_valley.py carries the land on from BAND metres out).

The region is a hilltop. Past each edge the land rounds over a brow and falls steeply, with
spurs and gullies; beyond the south-east corner, where the river rises, a knoll stands above the
region before it too falls away. offset(x, y) is how far the mirrored ground is moved up or down.
"""

import numpy as np
from PIL import Image

import forest_backdrop

# The mirrored ground and forest reach this far from the region's edge.
BAND = 120.0
# The ground is merged into NEAR_CELL squares up to NEAR from the edge, FAR_CELL squares past it;
# corners on seams (the edge, texture tiles, copies, the two bands) stay.
NEAR = 30.0
NEAR_CELL = 1.5
FAR_CELL = 4.0
# The knoll past the south-east corner: centre, radius, height, metres.
KNOLL = (95.0, -55.0, 60.0, 22.0)


def smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def distance(x, y):
    """Distance from the mirrored rectangle (0 inside it)."""
    b = forest_backdrop.BOUNDS
    dx = np.maximum(np.maximum(b[0] - x, x - b[1]), 0.0)
    dy = np.maximum(np.maximum(b[2] - y, y - b[3]), 0.0)
    return np.hypot(dx, dy)


def offset(x, y):
    """Height added to the mirrored ground at (x, y): 0 at the edge, where the slope starts level."""
    x, y = np.asarray(x, np.float64), np.asarray(y, np.float64)
    b = forest_backdrop.BOUNDS
    d = distance(x, y)
    theta = np.arctan2(y - 0.5 * (b[2] + b[3]), x - 0.5 * (b[0] + b[1]))
    # Past a brow a few metres wide, the land falls at `steep` metres per metre, so the hilltop ends
    # where the region does.
    steep = 0.7 + 0.12 * np.sin(2.0 * theta + 1.3) + 0.08 * np.sin(5.0 * theta + 0.4)
    brow = 10.0 + 4.0 * np.sin(3.0 * theta + 2.1)
    fall = steep * np.where(d < brow, d * d / (2.0 * brow), d - 0.5 * brow)
    # Spurs and gullies, growing away from the edge.
    ripple = np.sin(x / 23.0 + 1.7 * np.sin(y / 41.0)) * np.cos(y / 29.0 + 1.3 * np.sin(x / 37.0))
    rough = 4.0 * ripple * smoothstep(10.0, 60.0, d)
    # The knoll holds its ground up: little of the fall reaches its crown.
    kx, ky, kr, kh = KNOLL
    around = np.exp(-((x - kx) ** 2 + (y - ky) ** 2) / kr ** 2)
    knoll = kh * around * smoothstep(0.0, 35.0, d)
    return -fall * (1.0 - 0.85 * around) + rough + knoll


def gradient(x, y, h=0.5):
    return ((offset(x + h, y) - offset(x - h, y)) / (2 * h), (offset(x, y + h) - offset(x, y - h)) / (2 * h))


def read_obj(path):
    positions, uvs, normals, faces = [], [], [], []
    for line in path.read_text().splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "v":
            positions.append([float(v) for v in parts[1:4]])
        elif parts[0] == "vt":
            uvs.append([float(v) for v in parts[1:3]])
        elif parts[0] == "vn":
            normals.append([float(v) for v in parts[1:4]])
        elif parts[0] == "f":
            faces.append([[int(i) - 1 for i in c.split("/")] for c in parts[1:4]])
    faces = np.array(faces)  # (n, 3 corners, v/t/n)
    return np.array(positions)[faces[:, :, 0]], np.array(uvs)[faces[:, :, 1]], np.array(normals)[faces[:, :, 2]]


def write_obj(path, p, uv, n, header):
    """One triangle per three corners, as forest_terrain.write_tile."""
    p, uv, n = p.reshape(-1, 3), uv.reshape(-1, 2), n.reshape(-1, 3)
    with path.open("w") as f:
        f.write(f"# {header}\n")
        f.writelines(f"v {x:.4f} {y:.4f} {z:.4f}\n" for x, y, z in p)
        f.writelines(f"vt {u:.6f} {v:.6f}\n" for u, v in uv)
        f.writelines(f"vn {x:.4f} {y:.4f} {z:.4f}\n" for x, y, z in n)
        f.writelines(f"f {i}/{i}/{i} {i + 1}/{i + 1}/{i + 1} {i + 2}/{i + 2}/{i + 2}\n" for i in range(1, len(p) + 1, 3))


def cluster(p, uv, n, cell, fixed):
    """Vertex clustering (as forest_terrain.decimate_obj) of triangles given per corner: corners in
    each cell-sized square merge, fixed corners stay; degenerate triangles go."""
    p, uv, n, fixed = p.reshape(-1, 3), uv.reshape(-1, 2), n.reshape(-1, 3), fixed.reshape(-1)
    key = np.floor(p[:, :2] / cell).astype(np.int64)
    key = np.where(fixed[:, None], np.round(p[:, :2] * 1000).astype(np.int64) + (1 << 40), key)
    _, label = np.unique(key, axis=0, return_inverse=True)
    label = label.ravel()
    count = np.bincount(label).astype(float)[:, None]
    mean = lambda v: np.stack([np.bincount(label, v[:, k]) for k in range(v.shape[1])], 1) / count
    cp, cuv, cn = mean(p), mean(uv), mean(n)
    cn /= np.maximum(np.linalg.norm(cn, axis=1, keepdims=True), 1e-9)
    tri = label.reshape(-1, 3)
    tri = tri[(tri[:, 0] != tri[:, 1]) & (tri[:, 1] != tri[:, 2]) & (tri[:, 0] != tri[:, 2])]
    return cp[tri], cuv[tri], cn[tri]


def ground(tile_path, out_path):
    """A terrain tile's mirrored copies around the region, moved by offset(); returns triangles kept."""
    p, uv, n = read_obj(tile_path)
    # Corners on the tile's texture edges or on the mirrored rectangle are shared with the
    # neighbouring tile or copy: they stay where they are, so the seams stay closed.
    b = forest_backdrop.BOUNDS
    seam = ((np.abs(uv - np.round(uv)) < 1e-5).any(axis=-1) | (np.abs(p[..., 0] - b[0]) < 1e-3) |
            (np.abs(p[..., 0] - b[1]) < 1e-3) | (np.abs(p[..., 1] - b[2]) < 1e-3) | (np.abs(p[..., 1] - b[3]) < 1e-3))
    parts = []
    for _, _, m in forest_backdrop.tiles():
        q = p @ m[:, :3].T + m[:, 3]
        normal = n * np.diag(m[:, :3])
        d = distance(q[..., 0], q[..., 1])
        keep = d.min(axis=1) < BAND
        q, normal, t, d, fixed = q[keep], normal[keep], uv[keep], d[keep], seam[keep]
        q[..., 2] += offset(q[..., 0], q[..., 1])
        # The slope's normal: the mirrored ground's own tilt plus the offset's.
        gx, gy = gradient(q[..., 0], q[..., 1])
        nz = np.maximum(normal[..., 2], 1e-3)
        normal = np.stack([normal[..., 0] / nz - gx, normal[..., 1] / nz - gy, np.ones_like(gx)], -1)
        normal /= np.linalg.norm(normal, axis=-1, keepdims=True)
        if np.linalg.det(m[:, :3]) < 0:  # a reflection turns the triangles over
            q, t, normal, d, fixed = q[:, ::-1], t[:, ::-1], normal[:, ::-1], d[:, ::-1], fixed[:, ::-1]
        near = d.max(axis=1) < NEAR
        # Corners the two bands share.
        key = lambda c: set(map(tuple, np.round(c.reshape(-1, 3)[:, :2] * 1000).astype(np.int64).tolist()))
        shared = key(q[near]) & key(q[~near])
        both = np.array([tuple(k) in shared for k in np.round(q[..., :2].reshape(-1, 2) * 1000).astype(np.int64).tolist()],
                        bool)
        pinned = fixed | both.reshape(fixed.shape) | (d < 1e-3)
        parts.append(cluster(q[near], t[near], normal[near], NEAR_CELL, pinned[near]))
        parts.append(cluster(q[~near], t[~near], normal[~near], FAR_CELL, pinned[~near]))
    q, t, normal = (np.concatenate([part[k] for part in parts]) for k in range(3))
    write_obj(out_path, q, t, normal, f"{tile_path.name} mirrored around the region, falling away (forest_edge.py).")
    return len(q)


def ground_shapes(out):
    """The four terrain tiles' copies past the edges (meshes/edge_{x}_{y}.obj, with their tile's
    maps), as Shapes."""
    shapes = []
    for x in range(2):
        for y in range(2):
            ground(out / f"meshes/terrain_{x}_{y}.obj", out / f"meshes/edge_{x}_{y}.obj")
            shapes.append(f"""Shape {{
      appearance PBRAppearance {{
        baseColorMap ImageTexture {{ url "meshes/terrain_{x}_{y}_diff.png" repeatS FALSE repeatT FALSE }}
        normalMap ImageTexture {{ url "meshes/terrain_{x}_{y}_normal.png" repeatS FALSE repeatT FALSE }}
        roughness 0.95 metalness 0
        terrainDetail TRUE
      }}
      geometry Mesh {{ url "meshes/edge_{x}_{y}.obj" }}
      castShadows TRUE
    }}""")
    return shapes


def lift(rows):
    """Instance rows placed outside the region, moved onto the fallen ground; rows past BAND are
    dropped. Returns the rows and the mask of those kept."""
    rows = np.array(rows, np.float64).reshape(-1, 3, 4)
    x, y = rows[:, 0, 3], rows[:, 1, 3]
    dz = offset(x, y)
    keep = distance(x, y) < BAND
    rows = rows[keep]
    rows[:, 2, 3] += dz[keep]
    return rows, keep


def outer_light_occlusion(inner, rect, ground, count, out_png, land, reach, opens, texel=4.0):
    """Light visibility layers past the region (Background.lightOcclusionOuter*, Webots patch 0065):
    the inner layers (the image `inner` over `rect`, ground between `ground`) mirrored as the
    forest is, out to `reach` from the edge, each texel's ground as it lies there (land(x, y)),
    opening to full light between the distances `opens` (where the hillside's forest ends). Returns
    the Background fields."""
    image = np.asarray(Image.open(inner).convert("RGBA"), np.float32) / 255.0
    rows = image.shape[0] // (count + 1)
    layers = [image[k * rows:(k + 1) * rows][::-1] for k in range(count + 1)]  # rows from min y
    b = forest_backdrop.BOUNDS
    low_corner = np.floor([b[0] - reach, b[2] - reach])
    high_corner = np.ceil([b[1] + reach, b[3] + reach])
    xs = np.arange(low_corner[0], high_corner[0], texel) + 0.5 * texel
    ys = np.arange(low_corner[1], high_corner[1], texel) + 0.5 * texel
    x, y = np.meshgrid(xs, ys)
    # Where each texel's ground comes from in the inner layers: mirrored about their rectangle.
    u = (x - rect[0]) / (rect[2] - rect[0])
    v = (y - rect[1]) / (rect[3] - rect[1])
    u, v = 1.0 - np.abs(np.mod(u, 2.0) - 1.0), 1.0 - np.abs(np.mod(v, 2.0) - 1.0)
    height, width = layers[0].shape[:2]
    fx, fy = np.clip(u * width - 0.5, 0, width - 1), np.clip(v * height - 0.5, 0, height - 1)
    x0, y0 = np.minimum(fx.astype(int), width - 2), np.minimum(fy.astype(int), height - 2)
    ax, ay = (fx - x0)[..., None], (fy - y0)[..., None]

    def sample(layer):
        return ((layer[y0, x0] * (1 - ax) + layer[y0, x0 + 1] * ax) * (1 - ay) +
                (layer[y0 + 1, x0] * (1 - ax) + layer[y0 + 1, x0 + 1] * ax) * ay)

    lying = land(x, y)
    openness = smoothstep(opens[0], opens[1], distance(x, y))[..., None]
    sampled = [sample(layer) * (1.0 - openness) + openness for layer in layers[:count]]
    low, high = float(lying.min()), float(lying.max())
    alpha = (lying - low) / (high - low)
    sampled.append(np.dstack([alpha, alpha, alpha, np.ones_like(alpha)]))
    stacked = np.concatenate([layer[::-1] for layer in sampled])  # top row: max y
    Image.fromarray(np.round(np.clip(stacked, 0, 1) * 255).astype(np.uint8), "RGBA").save(out_png)
    return (f"  lightOcclusionOuterUrl [ \"meshes/{out_png.name}\" ]\n"
            f"  lightOcclusionOuterMin {low_corner[0]:.4f} {low_corner[1]:.4f}\n"
            f"  lightOcclusionOuterMax {low_corner[0] + len(xs) * texel:.4f} {low_corner[1] + len(ys) * texel:.4f}\n"
            f"  lightOcclusionOuterGround {low:.4f} {high:.4f}")


if __name__ == "__main__":
    # A quick look at the profile along each side.
    forest_backdrop.BOUNDS = (-137.1, 62.70469, -17.65625, 177.27589)
    b = forest_backdrop.BOUNDS
    for name, (x, y, dx, dy) in {"west": (b[0], 80, -1, 0), "north": (-40, b[3], 0, 1), "east": (b[1], 80, 1, 0),
                                 "south": (-40, b[2], 0, -1), "south-east": (b[1], b[2], 0.707, -0.707)}.items():
        print(f"{name:10s}", " ".join(f"{offset(x + dx * s, y + dy * s):6.1f}" for s in range(0, 200, 20)))
