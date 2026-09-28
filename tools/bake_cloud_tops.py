"""The cloud sea's tops as a tiling height map (Fog.cloudUrl, Webots patch 0065).

Cumuliform tops are puffs on puffs: rounded domes packed together, smaller ones on the larger,
with creases between them. Each layer scatters bell-shaped domes over a jittered grid and joins them with a
smooth maximum (so creases are rounded, not cut); the layers add. Distances wrap, so the map tiles.
Written as a 16-bit grey PNG: black at the lowest crease, white at the highest crown.

  bake_cloud_tops.py OUT.png [--size 512] [--texel 2.0]
"""
import argparse
from pathlib import Path

import numpy as np
from PIL import Image

# (spacing, radius range, dome height / radius, smooth-max sharpness per metre), metres.
LAYERS = (
    (110.0, (60.0, 110.0), 0.28, 0.3),
    (42.0, (20.0, 38.0), 0.28, 0.7),
    (16.0, (7.0, 14.0), 0.22, 1.6),
)


def layer(size, texel, spacing, radii, rise, sharpness, rng):
    n = max(1, round(size * texel / spacing))
    cell = size / n  # texels
    sums = np.ones((size, size))  # the floor, exp(0)
    for gy in range(n):
        for gx in range(n):
            cx, cy = (gx + rng.uniform(0.0, 1.0)) * cell, (gy + rng.uniform(0.0, 1.0)) * cell
            r = rng.uniform(*radii) / texel
            x0, y0 = int(np.floor(cx - r)), int(np.floor(cy - r))
            span = int(np.ceil(2 * r)) + 2
            xs, ys = np.arange(x0, x0 + span), np.arange(y0, y0 + span)
            d2 = ((xs[None, :] + 0.5 - cx) ** 2 + (ys[:, None] + 0.5 - cy) ** 2) / (r * r)
            # A bell: rounded crown, meeting the floor tangentially.
            dome = rise * r * texel * np.clip(1.0 - d2, 0.0, None) ** 1.5
            sums[np.ix_(ys % size, xs % size)] += np.expm1(sharpness * dome)
    return np.log(sums) / sharpness


def blur(h, sigma):
    """Gaussian blur, periodic (FFT)."""
    f = np.fft.fftfreq(h.shape[0])
    g = np.exp(-2 * (np.pi * sigma) ** 2 * (f[:, None] ** 2 + f[None, :] ** 2))
    return np.real(np.fft.ifft2(np.fft.fft2(h) * g))


def bake(out, size=512, texel=2.0, seed=5):
    """Write the map to out; returns metres per repeat."""
    rng = np.random.default_rng(seed)
    h = blur(sum(layer(size, texel, *spec, rng) for spec in LAYERS), 1.0 / texel)
    low, high = float(h.min()), float(h.max())
    Image.fromarray(np.round((h - low) / (high - low) * 65535).astype(np.uint16)).save(out)
    return size * texel


def main():
    p = argparse.ArgumentParser()
    p.add_argument("out", type=Path)
    p.add_argument("--size", type=int, default=512)
    p.add_argument("--texel", type=float, default=2.0)
    p.add_argument("--seed", type=int, default=5)
    a = p.parse_args()
    print(a.out, "repeats every", bake(a.out, a.size, a.texel, a.seed), "m")


if __name__ == "__main__":
    main()
