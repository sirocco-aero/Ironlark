"""The backdrop: the exported region mirrored across its edges.

Tile (i, j) is the region reflected across x when i is odd and across y when j
is odd, then shifted by (i, j) region sizes, so the ground meets itself at every
seam. The viewer never leaves the region (tile 0, 0); the tiles around it are
what it sees beyond the source's end. Mirrored instances keep their placement
but turn a seeded random yaw about their own up axis, so no tree or rock faces
its twin across a seam. The ground is truly mirrored: Webots patch 0015 draws
mirrored instances.
"""
import math

import numpy as np

from forest_view import ORIGIN, REGION, VIEW_MARGIN

# The region in world coordinates, and the part the viewer is fenced into.
AREA = (REGION[0] - ORIGIN[0], REGION[1] - ORIGIN[0], REGION[2] - ORIGIN[1], REGION[3] - ORIGIN[1])
FENCE = (AREA[0] + VIEW_MARGIN, AREA[1] - VIEW_MARGIN, AREA[2] + VIEW_MARGIN, AREA[3] - VIEW_MARGIN)
# What is mirrored: the rectangle the terrain fully covers (forest_terrain.inner_rect),
# set by the world builder; the terrain's ragged edges are clipped to it.
BOUNDS = AREA
RINGS = 1
# Impostor trees and coarse ground reach this far: from the 70 m ceiling, fog hides
# more than 95% of what lies past it, so the forest's end never shows.
HORIZON_REACH = 2000.0


def far_rings():
    return math.ceil(HORIZON_REACH / min(BOUNDS[1] - BOUNDS[0], BOUNDS[3] - BOUNDS[2]))


def tiles(rings=RINGS, first=1):
    """(i, j, 3x4 affine) of every backdrop tile in rings first..rings."""
    width, depth = BOUNDS[1] - BOUNDS[0], BOUNDS[3] - BOUNDS[2]
    for i in range(-rings, rings + 1):
        for j in range(-rings, rings + 1):
            if max(abs(i), abs(j)) < first:
                continue
            sx, sy = (-1 if i % 2 else 1), (-1 if j % 2 else 1)
            tx = i * width + (BOUNDS[0] + BOUNDS[1] if i % 2 else 0)
            ty = j * depth + (BOUNDS[2] + BOUNDS[3] if j % 2 else 0)
            yield i, j, np.array([[sx, 0, 0, tx], [0, sy, 0, ty], [0, 0, 1, 0]], np.float64)


def fence_distance(points):
    dx = np.maximum.reduce([FENCE[0] - points[:, 0], np.zeros(len(points)), points[:, 0] - FENCE[1]])
    dy = np.maximum.reduce([FENCE[2] - points[:, 1], np.zeros(len(points)), points[:, 1] - FENCE[3]])
    return np.hypot(dx, dy)


def copies(rows, far=None, yaw=True, seed=0, rings=RINGS):
    """Backdrop rows (n, 3, 4) for instance rows placed in the region, and the index
    of each copy's original.

    Objects are placed at their mirrored position but not mirrored themselves: a
    local x flip undoes the tile's reflection, keeping the up axis on the mirrored
    ground. far: drop copies whose origin stays this far from every point the viewer
    can reach (their visibility range ends there)."""
    rows = np.asarray(rows, np.float64).reshape(-1, 3, 4)
    rng = np.random.default_rng(seed)
    result, sources = [], []
    for i, j, tile in tiles(rings):
        placed = np.einsum("ab,nbc->nac", tile[:, :3], rows)
        placed[:, :, 3] += tile[:, 3]
        if (i + j) % 2:
            placed[:, :, 0] *= -1
        index = np.arange(len(rows))
        if far is not None:
            keep = fence_distance(placed[:, :, 3]) < far
            placed, index = placed[keep], index[keep]
        sources.append(index)
        if yaw and len(placed):
            angle = rng.uniform(0, 2 * math.pi, len(placed))
            c, s = np.cos(angle), np.sin(angle)
            spin = np.zeros((len(placed), 3, 3))
            spin[:, 0, 0], spin[:, 0, 1], spin[:, 1, 0], spin[:, 1, 1], spin[:, 2, 2] = c, -s, s, c, 1
            placed[:, :, :3] = placed[:, :, :3] @ spin
        result.append(placed)
    return np.concatenate(result), np.concatenate(sources)


def ground_rows(rings=RINGS, first=1):
    """One row per tile: the whole region, mirrored into place."""
    return np.array([tile for _, _, tile in tiles(rings, first)])
