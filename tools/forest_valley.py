"""The valley around the forest's hill: land, water and a village, past the edge's near band.

The region is a hilltop. Its mirrored ground and forest fall away as tools/forest_edge.py places
them; past its near band this module carries the hillside down to a valley floor VALLEY metres
below, with meadows, fields, woods, a river, a lake, roads and a village, and forested hills and
mountains around. The land is one crack-free mesh of rings around the region, graded from metres
near the hill to hundreds far out, split in two where its painted land-use texture changes.
"""
import json
import math

import numpy as np
from PIL import Image

import forest_backdrop
import forest_edge

VALLEY = -250.0
# Rings from FAR_START (overlapping the near band, below it) out to FAR_END; the inner texture's
# land ends at SPLIT.
FAR_START = 95.0
SPLIT = 1350.0
FAR_END = 7500.0
TEXTURE = 2048
# Where the lake, the village and the river run, as offsets from the region's centre.
LAKE = (-760.0, -60.0, 250.0, 140.0, 0.35)  # centre x, y, semi-axes, turn (rad)
# Its basin: the bed falls LAKE_DEPTH metres over LAKE_SHELF from the shore (the shallows along it); a
# bank rises LAKE_BANK above the water within 15 m, so no land outside lies under it, and meets the
# valley floor over LAKE_BLEND out (lake_distance's units: stretched along the lake's long axis, which
# points at the hill; past it the land is the valley's own, so the hillside and its light layers keep
# theirs). Land within LAKE_FINE of the shore is meshed in cells an eighth of the rings' (3-6 m), so
# the waterline follows the shore.
LAKE_DEPTH = 8.0
LAKE_SHELF = 60.0
LAKE_BANK = 1.6
LAKE_BLEND = (15.0, 35.0)
LAKE_FINE = 60.0
VILLAGE = (-980.0, -520.0)
SEED = 3


def center():
    b = forest_backdrop.BOUNDS
    return 0.5 * (b[0] + b[1]), 0.5 * (b[2] + b[3])


class Noise:
    """Gradient noise on a lattice, vectorised; about -0.7..0.7."""

    def __init__(self, seed):
        rng = np.random.default_rng(seed)
        angles = rng.uniform(0, 2 * math.pi, 1024)
        self.g = np.stack([np.cos(angles), np.sin(angles)], -1)
        self.p = rng.permutation(1024)

    def __call__(self, x, y):
        x, y = np.asarray(x, np.float64), np.asarray(y, np.float64)
        xi, yi = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
        fx, fy = x - xi, y - yi
        u, v = fx * fx * fx * (fx * (fx * 6 - 15) + 10), fy * fy * fy * (fy * (fy * 6 - 15) + 10)

        def corner(i, j):
            h = self.p[(self.p[(xi + i) & 1023] + yi + j) & 1023]
            g = self.g[h]
            return g[..., 0] * (fx - i) + g[..., 1] * (fy - j)

        a, b, c, d = corner(0, 0), corner(1, 0), corner(0, 1), corner(1, 1)
        return a + u * (b - a) + v * (c - a) + u * v * (a - b - c + d)

    def fbm(self, x, y, octaves=4):
        total, amplitude, scale = 0.0, 1.0, 1.0
        for k in range(octaves):
            total = total + amplitude * self(x * scale + 17.3 * k, y * scale - 9.1 * k)
            amplitude *= 0.5
            scale *= 2.03
        return total


NOISE = Noise(SEED)


def smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def smooth_max(a, b, k):
    h = np.clip(0.5 + 0.5 * (a - b) / k, 0.0, 1.0)
    return b + (a - b) * h + k * h * (1.0 - h)


# The river: a line around the hill, from the north-west round the west and south, meandering at its
# own scale (bends a few hundred metres long). It comes down from the hills to the north as a stream,
# widening, and leaves the valley towards the south-east, into the haze: no end of it shows from the
# hill.
def river_line():
    cx, cy = center()
    t = np.linspace(math.radians(70), math.radians(360), 2200)
    r = 820 + 170 * np.sin(3 * t + 0.6) + 70 * np.sin(9 * t + 1.1)
    away = np.maximum(smoothstep(math.radians(100), math.radians(70), t), smoothstep(math.radians(330), math.radians(360), t))
    r += 1400 * smoothstep(math.radians(100), math.radians(70), t) + 2600 * smoothstep(math.radians(330), math.radians(360), t)
    line = np.stack([cx + r * np.cos(t), cy + r * np.sin(t)], -1)
    # Where it runs out across the valley, it meanders.
    tangent = np.gradient(line, axis=0)
    normal = np.column_stack([-tangent[:, 1], tangent[:, 0]]) / np.linalg.norm(tangent, axis=1, keepdims=True)
    along = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(line, axis=0), axis=1))])
    bends = smoothstep(0.0, 0.6, away) * (90.0 * np.sin(along / 260.0) + 35.0 * np.sin(along / 97.0 + 1.3))
    wander = 1.0 + 0.6 * NOISE(along / 400.0, 5.3)
    bends += wander * (22.0 * np.sin(along / 40.0 + 0.4) + 6.0 * np.sin(along / 22.0 + 2.1))
    return line + normal * bends[:, None]


def river_width(line):
    """The river's width along its line: a stream widening over its first 1.2 km, then wider and
    narrower by a quarter."""
    along = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(line, axis=0), axis=1))])
    return RIVER_WIDTH * (0.25 + 0.75 * smoothstep(0.0, 1200.0, along)) * (1.0 + 0.35 * NOISE(along / 150.0, 1.7))


RIVER_WIDTH = 14.0
# Its channel: RIVER_DEPTH deep (less for the stream), its water RIVER_BANK under the banks. Land within
# RIVER_FINE of it is meshed as finely as the lake's shore, out to RIVER_CARVED from the region (the
# finer valley mesh's reach); past it the river is a ribbon over the land, too far to tell.
RIVER_DEPTH = 1.8
RIVER_BANK = 0.9
RIVER_FINE = 18.0
RIVER_CARVED = (1150.0, 1250.0)
RIVER = None


def river():
    """The river along its line: points, arc lengths, water level (world z), half widths, curvature
    (1/m, positive turning left) and how much of its channel is carved (1 near, 0 far out)."""
    global RIVER
    if RIVER is None:
        line = river_line()
        along = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(line, axis=0), axis=1))])
        step = along[-1] / (len(along) - 1)
        # The water lies RIVER_BANK under the valley floor's course smoothed over ~150 m, level with the
        # lake where it meets it; far out, a metre over the land.
        floor = natural_floor(line[:, 0], line[:, 1])
        sigma = 60.0 / step
        k = np.arange(-int(3 * sigma), int(3 * sigma) + 1)
        kernel = np.exp(-0.5 * (k / sigma) ** 2)
        smooth = np.convolve(np.pad(floor, len(k) // 2, mode="edge"), kernel / kernel.sum(), "valid")
        level = smooth - RIVER_BANK
        level = lake_level() + smoothstep(10.0, 60.0, lake_distance(line[:, 0], line[:, 1])) * (level - lake_level())
        carved = 1.0 - smoothstep(*RIVER_CARVED, forest_edge.distance(line[:, 0], line[:, 1]))
        level = floor + 1.0 + carved * (level - floor - 1.0)
        v, a = np.gradient(line, axis=0), np.gradient(np.gradient(line, axis=0), axis=0)
        curvature = (v[:, 0] * a[:, 1] - v[:, 1] * a[:, 0]) / np.linalg.norm(v, axis=1) ** 3
        k = np.arange(-int(3 * 10.0 / step), int(3 * 10.0 / step) + 1)
        kernel = np.exp(-0.5 * (k * step / 10.0) ** 2)
        curvature = np.convolve(np.pad(curvature, len(k) // 2, mode="edge"), kernel / kernel.sum(), "valid")
        RIVER = {"line": line, "along": along, "level": level, "half": 0.5 * river_width(line), "curvature": curvature,
                 "carved": carved}
    return RIVER


def channel(x, y, reach):
    """Where points lie by the river: distance to its line, arc length of the nearest point, the water
    level, half width and carving there, and u, across the channel between its wandering edges (-1 at
    the right bank, 1 at the left: each bank wanders on its own)."""
    r = river()
    dist, station, side = line_frame(x, y, r["line"], reach)
    level, half, carved = (np.interp(station, r["along"], r[key]) for key in ("level", "half", "carved"))
    edge = half * (1.0 + 0.2 * NOISE(station / 23.0, side * 3.1))
    return dist, station, level, half, carved, side * dist / edge, edge


def river_basin(x, y, floor):
    """The land along the river: its channel, deepest towards the outside of each bend (the inside
    shoals into gravel bars), and low banks meeting the valley floor."""
    x, y, floor = np.broadcast_arrays(np.asarray(x, np.float64), np.asarray(y, np.float64), np.asarray(floor, np.float64))
    dist, station, level, half, carved, u, edge = channel(x, y, 40.0)
    near = (dist < edge + 15.0) & (carved > 0.0)
    if not near.any():
        return floor
    out = floor.copy()
    dist, station, level, half, carved, u, edge, floor = (a[near] for a in (dist, station, level, half, carved, u, edge, floor))
    deep = np.clip(-np.interp(station, river()["along"], river()["curvature"]) * 150.0, -0.55, 0.55)
    bump = np.clip(1.0 - ((u - deep) / (1.0 + np.abs(deep))) ** 2, 0.0, 1.0)
    depth = RIVER_DEPTH * np.sqrt(np.clip(half / (0.5 * RIVER_WIDTH), 0.1, 1.5))
    bed = level + 0.3 - (depth + 0.3) * bump
    bank = level + 0.3 + (RIVER_BANK - 0.3) * smoothstep(0.0, 5.0, dist - edge)
    shape = np.where(np.abs(u) <= 1.0, bed, bank)
    out[near] = floor + carved * (1.0 - smoothstep(edge + 5.0, edge + 15.0, dist)) * (shape - floor)
    return out
# Impostor trees stand on the hillside from the near band out to HILLSIDE, all of them up to
# SPARSE_FROM, a share SPARSE_KEEP past it: the land under them is painted as forest, so they
# give it its silhouettes, not its cover.
HILLSIDE = 300.0
SPARSE_FROM = 170.0
SPARSE_KEEP = 0.35


def hillside(rows, build, seed=SEED):
    """Copies of the region's trees (instance rows) past the near band and down the hillside, standing
    on the land; those on the valley floor are dropped. Returns the rows and the mask of those kept."""
    rows = np.array(rows, np.float64).reshape(-1, 3, 4)
    x, y = rows[:, 0, 3], rows[:, 1, 3]
    d = forest_edge.distance(x, y)
    kept = (d < SPARSE_FROM) | (np.random.default_rng(seed).random(len(rows)) < SPARSE_KEEP)
    keep = (d >= forest_edge.BAND) & (d < HILLSIDE) & kept
    z = np.full(len(rows), -1e9)
    z[keep] = land(x[keep], y[keep], build)
    keep &= z > valley_floor(x, y) + 8.0
    rows = rows[keep]
    rows[:, 2, 3] = z[keep] - 0.2
    return rows, keep


def line_frame(x, y, line, reach=60.0):
    """Distance from points to a polyline where it is under reach (farther points get 1e9 or less),
    the arc length of the nearest point along it, and the side points lie on (1 left, -1 right)."""
    x, y = np.asarray(x, np.float64), np.asarray(y, np.float64)
    dist, station, side = np.full(x.shape, 1e9), np.zeros(x.shape), np.ones(x.shape)
    along = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(line, axis=0), axis=1))])
    lo = np.array([np.min(x), np.min(y)]) - reach
    hi = np.array([np.max(x), np.max(y)]) + reach
    near = np.all((np.minimum(line[:-1], line[1:]) <= hi) & (np.maximum(line[:-1], line[1:]) >= lo), axis=1)
    for k in np.nonzero(near)[0]:
        a, ab = line[k], line[k + 1] - line[k]
        length2 = max(ab @ ab, 1e-9)
        t = np.clip(((x - a[0]) * ab[0] + (y - a[1]) * ab[1]) / length2, 0, 1)
        d = np.hypot(x - (a[0] + t * ab[0]), y - (a[1] + t * ab[1]))
        closer = d < dist
        dist[closer] = d[closer]
        station[closer] = along[k] + t[closer] * math.sqrt(length2)
        side[closer] = np.where(ab[0] * (y[closer] - a[1]) - ab[1] * (x[closer] - a[0]) >= 0.0, 1.0, -1.0)
    return dist, station, side


def distance_to_line(x, y, line, reach=60.0):
    """Distance from points to a polyline where it is under reach (only the segments near the
    points are measured); farther points get 1e9 or less."""
    out = np.full(np.shape(x), 1e9)
    lo = np.array([np.min(x), np.min(y)]) - reach
    hi = np.array([np.max(x), np.max(y)]) + reach
    near = np.all((np.minimum(line[:-1], line[1:]) <= hi) & (np.maximum(line[:-1], line[1:]) >= lo), axis=1)
    for a, b in zip(line[:-1][near], line[1:][near]):
        ab = b - a
        t = np.clip(((x - a[0]) * ab[0] + (y - a[1]) * ab[1]) / max(ab @ ab, 1e-9), 0, 1)
        d = np.hypot(x - (a[0] + t * ab[0]), y - (a[1] + t * ab[1]))
        np.minimum(out, d, out=out)
    return out


def lake_distance(x, y):
    """Signed distance to the lake's shore, about (negative inside)."""
    cx, cy = center()
    lx, ly, ax, ay, turn = LAKE
    dx, dy = x - (cx + lx), y - (cy + ly)
    c, s = math.cos(turn), math.sin(turn)
    u, v = c * dx + s * dy, -s * dx + c * dy
    wobble = 1.0 + 0.12 * NOISE(x / 90.0, y / 90.0)
    return (np.hypot(u / ax, v / ay) - wobble) * min(ax, ay)


def rise(x, y):
    """How far the hills and mountains around the valley rise above its floor."""
    cx, cy = center()
    r = np.hypot(x - cx, y - cy)
    hills = smoothstep(1700.0, 3400.0, r + 500.0 * NOISE(x / 1800.0, y / 1800.0)) * (190.0 + 150.0 * NOISE.fbm(x / 1300.0, y / 1300.0, 3))
    mountains = smoothstep(4200.0, 7000.0, r) * (520.0 + 380.0 * NOISE.fbm(x / 2200.0, y / 2200.0, 4))
    return hills + mountains


def natural_floor(x, y):
    """The valley's floor before the lake, and the hills and mountains around it (world z)."""
    return VALLEY + 5.0 * NOISE.fbm(x / 420.0, y / 420.0, 3) + rise(x, y)


def lake_basin(x, y, floor):
    """The land around the lake: its bed under the water, its bank, a mouth where the river meets it."""
    x, y, floor = np.broadcast_arrays(np.asarray(x, np.float64), np.asarray(y, np.float64), np.asarray(floor, np.float64))
    d = lake_distance(x, y)
    near = d < LAKE_BLEND[1]
    if not near.any():
        return floor
    out = floor.copy()
    x, y, d, floor = x[near], y[near], d[near], floor[near]
    level = lake_level()
    # Where the river comes through the bank, its own channel and banks (floor has them); it runs on
    # into the shallows.
    dist, _, _, _, _, _, edge = channel(x, y, 40.0)
    mouth = smoothstep(edge + 6.0, edge + 1.0, dist) * (1.0 - smoothstep(20.0, LAKE_BLEND[1], d))
    bed = np.minimum(level - LAKE_DEPTH * smoothstep(0.0, -LAKE_SHELF, d), floor)
    bank = level + LAKE_BANK * smoothstep(0.0, 15.0, d)
    bank += mouth * (floor - bank)
    shore = np.where(d < 0.0, bed, bank)
    out[near] = shore + smoothstep(*LAKE_BLEND, d) * (floor - shore)
    return out


def valley_floor(x, y):
    """The valley's floor with the river's channel and the lake's basin, and the hills and mountains
    around it (world z)."""
    return lake_basin(x, y, river_basin(x, y, natural_floor(x, y)))


def shoreline(offset, count=720):
    """Points around the lake where lake_distance is offset, along rays from its centre (the shore is
    star-shaped about it)."""
    cx, cy = center()
    lx, ly = cx + LAKE[0], cy + LAKE[1]
    t = np.linspace(0.0, 2.0 * math.pi, count, endpoint=False)
    lo, hi = np.zeros(count), np.full(count, LAKE[2] + 250.0)
    for _ in range(40):
        middle = 0.5 * (lo + hi)
        inside = lake_distance(lx + middle * np.cos(t), ly + middle * np.sin(t)) < offset
        lo, hi = np.where(inside, middle, lo), np.where(inside, hi, middle)
    r = 0.5 * (lo + hi)
    return lx + r * np.cos(t), ly + r * np.sin(t)


GROUND = None


def mirrored_ground(x, y, build=None):
    """The region's ground at the mirrored place (the copies' ground), from the build's height grid."""
    global GROUND
    if GROUND is None:
        grid = json.loads((build / "heights.json").read_text())
        GROUND = (np.array(grid["grid"], np.float64), grid["x"], (-grid["z"][1], -grid["z"][0]))
    heights, gx, gy = GROUND
    b = forest_backdrop.BOUNDS
    u = 1.0 - np.abs(np.mod((np.asarray(x) - b[0]) / (b[1] - b[0]), 2.0) - 1.0)
    v = 1.0 - np.abs(np.mod((np.asarray(y) - b[2]) / (b[3] - b[2]), 2.0) - 1.0)
    px = np.clip((b[0] + u * (b[1] - b[0]) - gx[0]) / (gx[1] - gx[0]) * (heights.shape[1] - 1), 0, heights.shape[1] - 1.001)
    py = np.clip((b[2] + v * (b[3] - b[2]) - gy[0]) / (gy[1] - gy[0]) * (heights.shape[0] - 1), 0, heights.shape[0] - 1.001)
    i, j = py.astype(int), px.astype(int)
    fy, fx = py - i, px - j
    return ((heights[i, j] * (1 - fx) + heights[i, j + 1] * fx) * (1 - fy) + (heights[i + 1, j] * (1 - fx) + heights[i + 1, j + 1] * fx) * fy)


def land(x, y, build=None):
    """Ground height past the region: the hillside (mirrored ground, fading out, moved by the
    edge's fall) meeting the valley floor in a rounded foot."""
    x, y = np.asarray(x, np.float64), np.asarray(y, np.float64)
    d = forest_edge.distance(x, y)
    hill = forest_edge.offset(x, y) + mirrored_ground(x, y, build) * (1.0 - smoothstep(130.0, 220.0, d))
    return smooth_max(hill, valley_floor(x, y), 45.0)


# -- the land's mesh ---------------------------------------------------------------------------

def ring(d, count):
    """count points evenly along the rounded rectangle d metres from the region's rectangle."""
    b = forest_backdrop.BOUNDS
    hx, hy = 0.5 * (b[1] - b[0]), 0.5 * (b[3] - b[2])
    cx, cy = center()
    sides = [2 * hx, 2 * hy, 2 * hx, 2 * hy]
    arc = 0.5 * math.pi * d
    total = sum(sides) + 4 * arc
    s = np.arange(count) / count * total
    # Walk: bottom side left to right, arc, right side up, arc, top right to left, arc, left side down, arc.
    corners = [(hx, -hy), (hx, hy), (-hx, hy), (-hx, -hy)]
    starts = [(-hx, -hy - d), (hx + d, -hy), (hx, hy + d), (-hx - d, hy)]
    dirs = [(1, 0), (0, 1), (-1, 0), (0, -1)]
    x, y = np.zeros(count), np.zeros(count)
    offset = 0.0
    for k in range(4):
        on = (s >= offset) & (s < offset + sides[k])
        x[on] = starts[k][0] + dirs[k][0] * (s[on] - offset)
        y[on] = starts[k][1] + dirs[k][1] * (s[on] - offset)
        offset += sides[k]
        on = (s >= offset) & (s < offset + arc)
        angle = -0.5 * math.pi + k * 0.5 * math.pi + (s[on] - offset) / max(d, 1e-9)
        x[on] = corners[k][0] + d * np.cos(angle)
        y[on] = corners[k][1] + d * np.sin(angle)
        offset += arc
    return np.stack([x + cx, y + cy], -1)


def rings():
    """Distances and point counts, graded: cells about 5% of their distance, 4 to 280 m."""
    b = forest_backdrop.BOUNDS
    perimeter = lambda d: 2 * (b[1] - b[0]) + 2 * (b[3] - b[2]) + 2 * math.pi * d
    out, d = [], FAR_START
    while True:
        step = min(max(0.05 * d, 4.0), 280.0)
        out.append((d, max(8, int(math.ceil(perimeter(d) / step)))))
        if d >= FAR_END:
            return out
        if d < SPLIT <= d + step:
            d = SPLIT
        else:
            d += step


def strip(inner, outer):
    """Triangles joining two rings (point arrays with even spacing from the same start)."""
    n, m = len(inner), len(outer)
    i = j = 0
    tris = []
    while i < n or j < m:
        # Advance whichever ring's next point lies earlier along the perimeter.
        if j >= m or (i < n and (i + 1) / n <= (j + 1) / m):
            tris.append((("a", i % n), ("b", j % m), ("a", (i + 1) % n)))
            i += 1
        else:
            tris.append((("a", i % n), ("b", j % m), ("b", (j + 1) % m)))
            j += 1
    return tris


def land_mesh(build, lo, hi):
    """Vertices (n, 3) and triangles (m, 3) of the rings from lo to hi metres."""
    points, index, faces = [], [], []
    chosen = [(d, count) for d, count in rings() if lo <= d <= hi]
    base = 0
    for k, (d, count) in enumerate(chosen):
        p = ring(d, count)
        points.append(p)
        index.append(base)
        base += count
    for k in range(len(chosen) - 1):
        a0, b0, n, m = index[k], index[k + 1], chosen[k][1], chosen[k + 1][1]
        for tri in strip(np.zeros(n), np.zeros(m)):
            faces.append([(a0 if w == "a" else b0) + v for w, v in tri])
    xy = np.concatenate(points)
    faces = np.array(faces)

    def height(p):
        z = land(p[:, 0], p[:, 1], build)
        # Under the near band's ground where they overlap, so its mirrored tiles show.
        d = forest_edge.distance(p[:, 0], p[:, 1])
        return z - 2.5 * (1.0 - smoothstep(forest_edge.BAND - 10.0, forest_edge.BAND + 20.0, d))

    z = height(xy)
    def fine(p):  # the lake's shore, the river's channel and banks
        dist, _, _, _, carved, _, edge = channel(p[:, 0], p[:, 1], 60.0)
        return (np.abs(lake_distance(p[:, 0], p[:, 1])) < LAKE_FINE) | ((dist < edge + RIVER_FINE) & (carved > 0.0))

    xy, z, faces = refine(xy, z, faces, height, fine, 8)
    return np.column_stack([xy, z]), faces


def refine(xy, z, faces, height, near, k):
    """Split the triangles with a corner or centre near() into k * k. New points take height(), but
    those on an edge shared with an unsplit triangle lie on that edge: no crack at the T-junctions."""
    tri = xy[faces]
    split = near(tri.mean(axis=1)) | near(tri[:, 0]) | near(tri[:, 1]) | near(tri[:, 2])
    if not split.any():
        return xy, z, faces
    sides = {}
    for f, (a, b, c) in enumerate(faces):
        for p, q in ((a, b), (b, c), (c, a)):
            sides.setdefault((min(p, q), max(p, q)), []).append(f)
    points, heights = [], []  # new points; interpolated heights, or NaN where height() gives them
    base = len(xy)

    def add(point, h=np.nan):
        points.append(point)
        heights.append(h)
        return base + len(points) - 1

    edge_points = {}

    def along(p, q, i):  # the i-th of the k - 1 points from p to q
        key = (min(p, q), max(p, q))
        if key not in edge_points:
            a, b = key
            on_unsplit = not all(split[f] for f in sides[key])
            edge_points[key] = [add(xy[a] + (xy[b] - xy[a]) * s / k, z[a] + (z[b] - z[a]) * s / k if on_unsplit else np.nan)
                                for s in range(1, k)]
        return edge_points[key][(i if p < q else k - i) - 1]

    kept = [f for f, s in zip(faces, split) if not s]
    new = []
    for a, b, c in faces[split]:
        grid = {}
        for i in range(k + 1):
            for j in range(k + 1 - i):
                if (i, j) == (0, 0):
                    grid[i, j] = a
                elif (i, j) == (k, 0):
                    grid[i, j] = b
                elif (i, j) == (0, k):
                    grid[i, j] = c
                elif j == 0:
                    grid[i, j] = along(a, b, i)
                elif i == 0:
                    grid[i, j] = along(a, c, j)
                elif i + j == k:
                    grid[i, j] = along(b, c, j)
                else:
                    grid[i, j] = add(xy[a] + (xy[b] - xy[a]) * i / k + (xy[c] - xy[a]) * j / k)
        for i in range(k):
            for j in range(k - i):
                new.append([grid[i, j], grid[i + 1, j], grid[i, j + 1]])
                if i + j < k - 1:
                    new.append([grid[i + 1, j], grid[i + 1, j + 1], grid[i, j + 1]])
    points, heights = np.array(points), np.array(heights)
    missing = np.isnan(heights)
    heights[missing] = height(points[missing])
    return np.vstack([xy, points]), np.concatenate([z, heights]), np.array(kept + new)


def normals(v, build, h=1.5):
    x, y = v[:, 0], v[:, 1]
    gx = (land(x + h, y, build) - land(x - h, y, build)) / (2 * h)
    gy = (land(x, y + h, build) - land(x, y - h, build)) / (2 * h)
    n = np.column_stack([-gx, -gy, np.ones_like(gx)])
    return n / np.linalg.norm(n, axis=1, keepdims=True)


def write_mesh(path, v, n, uv, faces, header):
    with path.open("w") as f:
        f.write(f"# {header}\n")
        f.writelines(f"v {a:.3f} {b:.3f} {c:.3f}\n" for a, b, c in v)
        f.writelines(f"vt {a:.6f} {b:.6f}\n" for a, b in uv)
        f.writelines(f"vn {a:.4f} {b:.4f} {c:.4f}\n" for a, b, c in n)
        f.writelines(f"f {a}/{a}/{a} {b}/{b}/{b} {c}/{c}/{c}\n" for a, b, c in faces + 1)


# -- what the land is covered with --------------------------------------------------------------

def srgb(values):
    return np.asarray(values, np.float64) / 255.0


PALETTE = {
    "green": srgb([(84, 104, 50), (94, 112, 54), (74, 94, 44), (102, 118, 58)]),
    "wheat": srgb([(166, 150, 92), (156, 140, 86), (174, 158, 102)]),
    "ploughed": srgb([(104, 88, 68), (114, 96, 74)]),
    "stubble": srgb([(158, 146, 110)]),
    "meadow": srgb([(96, 116, 56), (108, 124, 60), (88, 108, 52)]),
}


def hash2(i, j):
    """A well-mixed 31-bit hash of integer lattice coordinates."""
    h = (i.astype(np.uint64) * np.uint64(0x9E3779B1) + j.astype(np.uint64) * np.uint64(0x85EBCA77)) & np.uint64(0xFFFFFFFF)
    h ^= h >> np.uint64(15)
    h = (h * np.uint64(0x2C1B3C6D)) & np.uint64(0xFFFFFFFF)
    h ^= h >> np.uint64(12)
    h = (h * np.uint64(0x297A2D39)) & np.uint64(0xFFFFFFFF)
    h ^= h >> np.uint64(15)
    return (h >> np.uint64(1)).astype(np.int64)


def voronoi(x, y, cell):
    """The index and edge distance of each point's cell in a jittered grid of cell-sized cells."""
    gx, gy = np.floor(x / cell).astype(np.int64), np.floor(y / cell).astype(np.int64)
    best = np.full(np.shape(x), 1e18)
    second = np.full(np.shape(x), 1e18)
    ids = np.zeros(np.shape(x), np.int64)
    for i in (-1, 0, 1):
        for j in (-1, 0, 1):
            cx, cy = gx + i, gy + j
            h = hash2(cx, cy)
            jx = ((h >> 3) & 1023) / 1023.0
            jy = ((h >> 13) & 1023) / 1023.0
            px, py = (cx + 0.15 + 0.7 * jx) * cell, (cy + 0.15 + 0.7 * jy) * cell
            d = (x - px) ** 2 + (y - py) ** 2
            closer = d < best
            second = np.where(closer, best, np.minimum(second, d))
            ids = np.where(closer, h, ids)
            best = np.where(closer, d, best)
    return ids, 0.5 * (np.sqrt(second) - np.sqrt(best))


def cover(x, y, build):
    """Albedo (sRGB, 0..1) of the land at points."""
    cx, cy = center()
    z = land(x, y, build)
    hills = rise(x, y)
    d = forest_edge.distance(x, y)
    # Parcels: a jittered grid stretched along a slowly turning axis, so fields run long and
    # vary in size; some cut in two along their length.
    turn = 0.6 + 0.5 * NOISE(x / 2500.0, y / 2500.0)
    c, s = np.cos(turn), np.sin(turn)
    u, v = (c * x + s * y) / 1.7, -s * x + c * y
    ids, edge = voronoi(u + 25.0 * NOISE(x / 300.0, y / 300.0), v + 25.0 * NOISE(x / 300.0 + 5, y / 300.0), 150.0)
    kind = (ids >> 5) % 100
    pick = (ids >> 11) % 7
    split = ((ids >> 17) % 3 == 0) & (np.sin((v + (ids % 97)) / 150.0 * math.pi) > 0)
    kind = np.where(split, (kind + 37) % 100, kind)
    rgb = np.zeros(np.shape(x) + (3,))
    for name, lo, hi in (("green", 0, 40), ("wheat", 40, 60), ("ploughed", 60, 72), ("stubble", 72, 80), ("meadow", 80, 100)):
        colours = PALETTE[name]
        on = (kind >= lo) & (kind < hi)
        rgb[on] = colours[pick[on] % len(colours)]
    # Within a field: soil and growth vary, and tramlines run along it.
    patch = 0.9 + 0.1 * NOISE.fbm(x / 35.0, y / 35.0, 3) + 0.04 * np.sin(v / 9.0 + (ids % 13))
    rgb *= patch[..., None]
    # Meadows by the river and round the village.
    beside, station, _, half, carved, across, _ = channel(x, y, 220.0)
    vx, vy = cx + VILLAGE[0], cy + VILLAGE[1]
    village = np.hypot(x - vx, y - vy)
    meadow = (beside < 40.0 + 30.0 * NOISE(x / 120.0, y / 120.0)) | (village < 220.0 + 60.0 * NOISE(x / 150.0, y / 150.0))
    clumps = (0.86 + 0.22 * NOISE.fbm(x / 14.0, y / 14.0, 3))[..., None]
    grass = PALETTE["meadow"][pick % 3] * clumps * np.array([1.0, 1.0, 0.92]) ** (1.0 + NOISE(x / 60.0, y / 60.0))[..., None]
    rgb = np.where(meadow[..., None], grass, rgb)
    # Woods: clusters across the valley, strips along the river, the hills and the foot of our hill.
    forest = srgb((44, 60, 35)) * canopy(x, y)[..., None]
    # Along the river, trees in stretches: wide here, missing there, each bank on its own.
    riverside = 34.0 * np.clip(0.3 + 1.6 * NOISE(station / 220.0, np.sign(across) * 4.7), 0.0, 1.0)
    riverside *= 0.8 + 0.3 * NOISE(x / 25.0, y / 25.0)
    wooded = ((NOISE.fbm(x / 900.0, y / 900.0, 3) > 0.22) | ((beside > half + 3.0) & (beside < half + 3.0 + riverside))
              | (hills > 35.0 + 40.0 * NOISE(x / 600.0, y / 600.0)) | (d < 300.0 + 60.0 * NOISE(x / 200.0, y / 200.0)))
    wooded &= village > 150.0
    hedge = (edge < 1.4) & (NOISE(x / 90.0, y / 90.0) > 0.05) & ~meadow
    rgb = np.where(hedge[..., None], srgb((56, 70, 42)), rgb)
    rgb = np.where(wooded[..., None], forest, rgb)
    # High on the mountains: rock and scree.
    rock = srgb((118, 114, 106)) * (0.85 + 0.3 * NOISE.fbm(x / 40.0, y / 40.0, 3))[..., None]
    rgb = np.where((hills > 620.0 + 140.0 * NOISE(x / 900.0, y / 900.0))[..., None], rock, rgb)
    # Roads: a lane round the hill and one to the village.
    roads = road_lines()
    near_road = np.minimum(distance_to_line(x, y, roads[0]), distance_to_line(x, y, roads[1]))
    rgb = np.where((near_road < 2.5)[..., None], srgb((166, 158, 136)), rgb)
    # The village's yards and gardens; the lake's and river's beds.
    rgb = np.where(((village < 120.0 + 30.0 * NOISE(x / 50.0, y / 50.0)) & (near_road >= 2.5))[..., None],
                   srgb((106, 114, 78)) * (0.9 + 0.2 * NOISE(x / 8.0, y / 8.0))[..., None], rgb)
    # The lake's shore: along some stretches a narrow beach of pale sand, along others the meadow and
    # reeds down to the water, with a thin wet line at it; under the water, sand in the shallows
    # darkening to mud with depth. Blended by height, so the texels' grid does not show.
    height = (z - lake_level())[..., None]
    lake = lake_distance(x, y)[..., None]
    shore = lake < 20.0
    sandy = smoothstep(0.12, 0.3, NOISE(x / 70.0, y / 70.0))[..., None]
    sand = srgb((174, 162, 126)) * (0.95 + 0.1 * NOISE(x / 15.0, y / 15.0))[..., None]
    reeds = srgb((82, 96, 48)) * (0.9 + 0.2 * NOISE(x / 9.0, y / 9.0))[..., None]
    margin = reeds + sandy * (sand - reeds)
    reach = smoothstep(0.6, 0.25, height) * sandy + smoothstep(0.35, 0.1, height) * (1.0 - sandy)
    # On the bank only: past it the land is the valley's own, however low.
    bank = 1.0 - smoothstep(10.0, 20.0, lake)
    rgb = np.where(shore, rgb + bank * reach * (margin - rgb), rgb)
    rgb = np.where(shore, rgb + bank * 0.45 * smoothstep(0.12, 0.0, height) * (srgb((96, 90, 66)) - rgb), rgb)
    shallows = srgb((138, 128, 98)) + sandy * (srgb((160, 148, 114)) - srgb((138, 128, 98)))
    bed = shallows + smoothstep(0.4, 4.0, -height) * (srgb((66, 72, 54)) - shallows)
    rgb = np.where((lake < 0.0) & (height < 0.0), bed, rgb)
    # The river's bed, up to the lake's: in its channel, gravel where the inside of a bend shoals into a
    # bar, wet soil along the rest of its edges, darkening with depth; far out, where it is a ribbon
    # over the land, dark under it.
    rv = river()
    level = np.interp(station, rv["along"], rv["level"])[..., None]
    deep = np.clip(-np.interp(station, rv["along"], rv["curvature"]) * 150.0, -0.55, 0.55)
    bar = smoothstep(0.0, 0.25, -across * deep)[..., None]
    gravel = srgb((156, 146, 116)) * (0.9 + 0.15 * NOISE(x / 6.0, y / 6.0))[..., None]
    edge = srgb((84, 80, 60)) + bar * (gravel - srgb((84, 80, 60)))
    bed = edge + smoothstep(0.15, 1.3, level - z[..., None]) * (srgb((62, 66, 50)) - edge)
    inside = (1.0 - smoothstep(0.95, 1.1, np.abs(across)))[..., None] * carved[..., None]
    rgb = rgb + inside * smoothstep(-8.0, 0.0, lake) * (bed - rgb)
    ribbon = smoothstep(half + 3.0, half + 1.0, beside)[..., None] * (1.0 - carved[..., None]) * smoothstep(-8.0, 0.0, lake)
    return rgb + ribbon * (srgb((58, 66, 52)) - rgb)


# Towards the main sun (the first DirectionalLight, reversed).
SUN = np.array([-0.272788, 0.814025, 0.512786])


def crowns(x, y, cell=6.5):
    """Height of a canopy of round crowns on a jittered grid (0 in the gaps)."""
    gx, gy = np.floor(x / cell).astype(np.int64), np.floor(y / cell).astype(np.int64)
    height = np.zeros(np.shape(x))
    for i in (-1, 0, 1):
        for j in (-1, 0, 1):
            cx, cy = gx + i, gy + j
            h = hash2(cx, cy)
            px = (cx + 0.1 + 0.8 * ((h >> 3) & 1023) / 1023.0) * cell
            py = (cy + 0.1 + 0.8 * ((h >> 13) & 1023) / 1023.0) * cell
            r = cell * (0.5 + 0.3 * ((h >> 23) & 255) / 255.0)
            d2 = ((x - px) ** 2 + (y - py) ** 2) / (r * r)
            np.maximum(height, r * np.sqrt(np.clip(1.0 - d2, 0.0, None)), out=height)
    return height


def canopy(x, y):
    """Brightness of a wood's canopy seen from afar: round crowns lit by the sun (the lighting is
    fixed), dark between them, over broad variation in tone."""
    e = 0.6
    h = crowns(x, y)
    gx = (crowns(x + e, y) - crowns(x - e, y)) / (2 * e)
    gy = (crowns(x, y + e) - crowns(x, y - e)) / (2 * e)
    n = np.stack([-gx, -gy, np.ones_like(gx)], -1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    lit = 0.45 + 0.8 * np.clip(n @ SUN, 0.0, 1.0)
    lit = np.where(h > 0.3, lit, 0.42)
    tone = 1.0 + 0.12 * NOISE(x / 150.0, y / 150.0) + 0.1 * NOISE(x / 23.0, y / 23.0)
    return np.clip(lit * tone, 0.4, 1.35)


def road_lines():
    cx, cy = center()
    t = np.linspace(0, 2 * math.pi, 900)
    r = 1050 + 90 * np.sin(4 * t + 0.3) + 40 * np.sin(11 * t)
    loop = np.stack([cx + r * np.cos(t), cy + r * np.sin(t)], -1)
    vx, vy = cx + VILLAGE[0], cy + VILLAGE[1]
    a = np.array([vx - 250, vy - 180])
    b = np.array([vx + 180, vy + 260])
    spur = np.stack([np.linspace(a[0], b[0], 60), np.linspace(a[1], b[1], 60)], -1)
    spur[:, 0] += 25 * np.sin(np.linspace(0, 3, 60))
    return loop, spur


def paint(path, build, half, size=TEXTURE, rows=128):
    """The land's cover over the square half metres around the region's centre, top row at max y."""
    cx, cy = center()
    step = 2 * half / size
    image = np.zeros((size, size, 3))
    xs = cx - half + (np.arange(size) + 0.5) * step
    for r0 in range(0, size, rows):
        ys = cy + half - (np.arange(r0, min(r0 + rows, size)) + 0.5) * step
        x, y = np.meshgrid(xs, ys)
        image[r0:r0 + len(ys)] = cover(x, y, build)
    Image.fromarray(np.round(np.clip(image, 0, 1) * 255).astype(np.uint8), "RGB").save(path)


# -- water and the village ------------------------------------------------------------------------

LEVEL = None


def lake_level():
    """The lake's water level: half a metre under the valley floor's mean along its shore."""
    global LEVEL
    if LEVEL is None:
        x, y = shoreline(0.0)
        LEVEL = float(np.mean(natural_floor(x, y))) - 0.5
    return LEVEL


def lake_mesh(build):
    """The lake's water, out to 3 m past the shore (the bank hides that much; the land meeting it draws
    the waterline): rings about 12 m apart shrinking to its centre, points about 12 m apart along
    them, so its triangles are not slivers (GPUs shade a fan's thin triangles many times over)."""
    cx, cy = center()
    lx, ly = cx + LAKE[0], cy + LAKE[1]
    points, starts, faces = [np.array([[lx, ly]])], [0], []
    scales = np.linspace(1.0, 0.08, 12)
    for s in scales:
        x, y = shoreline(3.0, max(8, int(110 * s)))
        starts.append(starts[-1] + len(points[-1]))
        points.append(np.column_stack([lx + s * (x - lx), ly + s * (y - ly)]))
    for k in range(1, len(scales)):  # outer ring k, inner ring k + 1
        a0, b0 = starts[k + 1], starts[k]
        for tri in strip(np.zeros(len(points[k + 1])), np.zeros(len(points[k]))):
            faces.append([(a0 if w == "a" else b0) + v for w, v in tri])
    inner = len(points[-1])
    faces += [[0, starts[-1] + k, starts[-1] + (k + 1) % inner] for k in range(inner)]
    xy = np.concatenate(points)
    return np.column_stack([xy, np.full(len(xy), lake_level())]), np.array(faces)


def river_mesh(build):
    """The river's water: level across, wider than its channel (the banks hide the rest, and the land
    meeting it draws its edges), coming level with the lake where it enters it and ending inside it
    (level with the lake's own water, as the same material, the overlap does not show). Far out, where
    its channel is not carved, a ribbon of its width over the land."""
    r = river()
    line, z = r["line"], r["level"]
    tangent = np.gradient(line, axis=0)
    tangent /= np.linalg.norm(tangent, axis=1, keepdims=True)
    wide = r["half"] + r["carved"] * (0.25 * r["half"] + 3.0)
    side = np.column_stack([-tangent[:, 1], tangent[:, 0]]) * wide[:, None]
    d = lake_distance(line[:, 0], line[:, 1])
    left, right = line + side, line - side
    v = np.vstack([np.column_stack([left, z]), np.column_stack([right, z])])
    n = len(line)
    keep = d > -6.0
    faces = [f for k in range(n - 1) if keep[k] and keep[k + 1] for f in ([k, n + k, k + 1], [k + 1, n + k, n + k + 1])]
    return v, np.array(faces)


def houses(build, count=34, seed=SEED):
    """Walls and roofs of a village: boxes with gabled roofs along its lane."""
    rng = np.random.default_rng(seed)
    cx, cy = center()
    vx, vy = cx + VILLAGE[0], cy + VILLAGE[1]
    walls, roofs = [], []
    for _ in range(count):
        along = rng.uniform(-1, 1)
        px = vx - 35 + 215 * along + rng.normal(0, 18)
        py = vy + 40 + 220 * along + rng.normal(0, 18)
        w, depth, h, pitch = rng.uniform(7, 11), rng.uniform(6, 8), rng.uniform(4, 6.5), rng.uniform(2.2, 3.4)
        turn = math.atan2(220, 215) + rng.choice([0, math.pi / 2]) + rng.normal(0, 0.08)
        z = float(land(np.array([px]), np.array([py]), build)[0]) - 0.3
        c, s = math.cos(turn), math.sin(turn)
        place = lambda a, b, cz: (px + c * a - s * b, py + s * a + c * b, z + cz)
        box = [place(a, b, cz) for cz in (0, h) for a, b in ((-w / 2, -depth / 2), (w / 2, -depth / 2), (w / 2, depth / 2), (-w / 2, depth / 2))]
        walls.append(box)
        ridge = [place(-w / 2, 0, h + pitch), place(w / 2, 0, h + pitch)]
        roofs.append([place(-w / 2 - 0.4, -depth / 2 - 0.4, h - 0.2), place(w / 2 + 0.4, -depth / 2 - 0.4, h - 0.2),
                      place(w / 2 + 0.4, depth / 2 + 0.4, h - 0.2), place(-w / 2 - 0.4, depth / 2 + 0.4, h - 0.2), *ridge])
    wall_faces = [[0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]]
    roof_faces = [[0, 1, 5], [0, 5, 4], [2, 3, 4], [2, 4, 5], [1, 2, 5], [3, 0, 4]]
    return walls, wall_faces, roofs, roof_faces


def write_flat(path, groups, faces, header):
    """Triangles with face normals (sharp edges), no texture coordinates."""
    corners = []
    for points in groups:
        p = np.array(points)
        for f in faces:
            corners.append(p[f])
    tri = np.array(corners)
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-9)
    with path.open("w") as out:
        out.write(f"# {header}\n")
        out.writelines(f"v {a:.3f} {b:.3f} {c:.3f}\n" for a, b, c in tri.reshape(-1, 3))
        out.writelines(f"vn {a:.4f} {b:.4f} {c:.4f}\n" for a, b, c in np.repeat(n, 3, axis=0))
        out.writelines(f"f {i}//{i} {i + 1}//{i + 1} {i + 2}//{i + 2}\n" for i in range(1, 3 * len(tri) + 1, 3))


def write_bc1_dds(image_path, dds_path):
    """A BC1 (opaque) DDS with a DX10 header and its mip levels, as Webots patch 0070 reads it."""
    import etcpak

    image = Image.open(image_path).convert("RGBA")
    levels = []
    while min(image.size) >= 4:  # BC1 blocks are 4 x 4: the chain stops there
        levels.append(etcpak.compress_bc1(np.ascontiguousarray(np.asarray(image)).tobytes(), *image.size))
        image = image.resize((image.size[0] // 2, image.size[1] // 2), Image.Resampling.BOX)
    w, h = Image.open(image_path).size
    header = bytearray(148)
    header[0:4] = b"DDS "
    fields = {4: 124, 8: 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000, 12: h, 16: w, 20: len(levels[0]), 28: len(levels),
              76: 32, 80: 0x4, 108: 0x1000 | 0x400000 | 0x8, 128: 71, 132: 3, 140: 1}
    for offset, value in fields.items():
        header[offset:offset + 4] = int(value).to_bytes(4, "little")
    header[84:88] = b"DX10"
    dds_path.write_bytes(bytes(header) + b"".join(levels))


def shapes(build, out):
    """Write the valley's meshes and textures to out/meshes; return its Shapes."""
    meshes = out / "meshes"
    parts = []
    cx, cy = center()
    # The far land is seen from 1.3 km out, through haze: half the texels there.
    for name, lo, hi, half, size in (("valley_near", FAR_START, SPLIT, 1500.0, TEXTURE),
                                     ("valley_far", SPLIT, FAR_END, FAR_END + 400.0, TEXTURE // 2)):
        v, faces = land_mesh(build, lo, hi)
        uv = np.column_stack([(v[:, 0] - (cx - half)) / (2 * half), (v[:, 1] - (cy - half)) / (2 * half)])
        write_mesh(meshes / f"{name}.obj", v, normals(v, build), uv, faces, "The valley's land (tools/forest_valley.py).")
        paint(meshes / f"{name}.png", build, half, size)
        write_bc1_dds(meshes / f"{name}.png", meshes / f"{name}.dds")
        (meshes / f"{name}.png").unlink()
        parts.append(f"""Shape {{
      appearance PBRAppearance {{
        baseColorMap ImageTexture {{ url "meshes/{name}.dds" repeatS FALSE repeatT FALSE }}
        roughness 0.95 metalness 0
      }}
      geometry Mesh {{ url "meshes/{name}.obj" }}
      castShadows FALSE
    }}""")
    # The lake and the river: clear water over their beds, greener with depth, rippled by the wind.
    water = """appearance PBRAppearance {
        baseColor 1 1 1
        roughness 0
        metalness 0
        transmission 1
        attenuationColor 0.42 0.66 0.64
        attenuationDistance 3
        scatterColor 0.03 0.1 0.12
        waves 0.1
      }"""
    for name, (v, faces) in (("valley_lake", lake_mesh(build)), ("valley_river", river_mesh(build))):
        n = np.tile([0.0, 0.0, 1.0], (len(v), 1))
        write_mesh(meshes / f"{name}.obj", v, n, np.zeros((len(v), 2)), faces, "The valley's water (tools/forest_valley.py).")
        parts.append(f"""Shape {{
      {water}
      geometry Mesh {{ url "meshes/{name}.obj" }}
      castShadows FALSE
    }}""")
    walls, wall_faces, roofs, roof_faces = houses(build)
    write_flat(meshes / "valley_walls.obj", walls, wall_faces, "The village's walls (tools/forest_valley.py).")
    write_flat(meshes / "valley_roofs.obj", roofs, roof_faces, "The village's roofs (tools/forest_valley.py).")
    for name, colour in (("valley_walls", "0.78 0.74 0.66"), ("valley_roofs", "0.52 0.24 0.17")):
        parts.append(f"""Shape {{
      appearance PBRAppearance {{ baseColor {colour} roughness 0.9 metalness 0 }}
      geometry Mesh {{ url "meshes/{name}.obj" }}
      castShadows FALSE
    }}""")
    return parts
