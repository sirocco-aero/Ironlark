#!/usr/bin/env python3
"""Score Webots views against reference renders of the same cameras.

    .venv/bin/python tools/compare_views.py runs/lighting-reference runs/preview
        ->  compare.json, and <view>-compare.png: reference | Webots | difference

Per view: mean luminance ratio, contrast (luminance spread) ratio, mean
saturation of each, per-channel mean ratio, and SSIM of luminance.

    .venv/bin/python tools/compare_views.py runs/main runs/branch --tiles
        ->  the same, and per view the 60 px tiles where the branch changed

--tiles compares repeatable captures of the same views (tools/render_forest.py --repeatable: two of one build are
bit-identical), which must be the same size: a tile changed where more than 0.5% of its pixels changed by more than
8/255, or its mean by more than 0.5/255. A small missing object changes a few percent of a tile; a global shift of
1/255 moves every tile's mean. Changed tiles go to Zaid; they never pass or fail alone.
"""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image


def linear(srgb):
    return np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)


def box(a, r):
    """Mean over (2r+1)^2 windows, edges clamped."""
    p = np.pad(a, r + 1, mode="edge").cumsum(0).cumsum(1)
    k = 2 * r + 1
    return (p[k:, k:] - p[:-k, k:] - p[k:, :-k] + p[:-k, :-k])[: a.shape[0], : a.shape[1]] / k**2


def ssim(x, y, r=3):
    c1, c2 = 0.01**2, 0.03**2
    mx, my = box(x, r), box(y, r)
    vx, vy, cxy = box(x * x, r) - mx * mx, box(y * y, r) - my * my, box(x * y, r) - mx * my
    return float((((2 * mx * my + c1) * (2 * cxy + c2)) / ((mx**2 + my**2 + c1) * (vx + vy + c2))).mean())


def stats(rgb):
    lin = linear(rgb)
    luminance = lin @ [0.2126, 0.7152, 0.0722]
    saturation = (rgb.max(2) - rgb.min(2)) / np.maximum(rgb.max(2), 1e-6)
    return lin, luminance, saturation


TILE, CHANGED, SHARE, SHIFT = 60, 8, 0.005, 0.5  # px; /255; of a tile's pixels; /255 of its mean


def changed_tiles(a, b):
    """The tiles of TILE px, as (row, column), where two same-sized 8-bit RGB images differ: more than SHARE of their
    pixels changed by more than CHANGED/255 in some channel, or their mean change exceeds SHIFT/255."""
    change = np.abs(a.astype(np.int16) - b.astype(np.int16)).max(2).astype(np.float64)
    rows, cols = np.arange(0, change.shape[0], TILE), np.arange(0, change.shape[1], TILE)

    def per_tile(values):
        sums = np.add.reduceat(np.add.reduceat(values, rows, axis=0), cols, axis=1)
        return sums / np.add.reduceat(np.add.reduceat(np.ones_like(values), rows, axis=0), cols, axis=1)

    flagged = (per_tile((change > CHANGED).astype(np.float64)) > SHARE) | (per_tile(change) > SHIFT)
    return [tuple(int(i) for i in tile) for tile in np.argwhere(flagged)]


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("reference", type=Path)
    p.add_argument("webots", type=Path)
    p.add_argument("--tiles", action="store_true", help="repeatable captures of one size: list the tiles that changed")
    args = p.parse_args(argv)
    references = [path for path in sorted(args.reference.glob("*.png")) if not path.name.endswith("-compare.png")]
    if not references:
        raise SystemExit(f"No reference renders (*.png) in {args.reference}.")
    if missing := [path.stem for path in references if not (args.webots / path.name).exists()]:
        raise SystemExit(f"No Webots render of {', '.join(missing)} in {args.webots}. "
                         "Render the same views first (tools/render_forest.py).")
    report = {}
    for ref_path in references:
        ours_path = args.webots / ref_path.name
        ref = Image.open(ref_path).convert("RGB")
        ours = Image.open(ours_path).convert("RGB")
        if args.tiles and ours.size != ref.size:
            raise SystemExit(f"{ref_path.stem}: the reference is {ref.size}, Webots {ours.size}. "
                             "Capture both at the same resolution.")
        ours = ours.resize(ref.size, Image.Resampling.LANCZOS)  # a Cycles reference's size; none with --tiles
        a, b = np.asarray(ref) / 255.0, np.asarray(ours) / 255.0
        la, lum_a, sat_a = stats(a)
        lb, lum_b, sat_b = stats(b)
        report[ref_path.stem] = {
            "luminance_ratio": round(float(lum_b.mean() / lum_a.mean()), 3),
            "contrast_ratio": round(float(lum_b.std() / lum_a.std()), 3),
            "saturation": [round(float(sat_a.mean()), 3), round(float(sat_b.mean()), 3)],
            "channel_ratio": [round(float(lb[..., c].mean() / la[..., c].mean()), 3) for c in range(3)],
            "ssim": round(ssim(lum_a ** (1 / 2.2), lum_b ** (1 / 2.2)), 3),
        }
        if args.tiles:
            report[ref_path.stem]["changed_tiles"] = changed_tiles(np.asarray(ref), np.asarray(ours))
        difference = np.clip(np.abs(a - b).mean(2) * 4, 0, 1)
        heat = (np.stack([difference, difference * 0.35, 1 - difference], 2) * 255).astype(np.uint8)
        strip = Image.new("RGB", (ref.width * 3, ref.height))
        for i, img in enumerate((ref, ours, Image.fromarray(heat))):
            strip.paste(img, (i * ref.width, 0))
        strip.save(args.webots / f"{ref_path.stem}-compare.png")
    (args.webots / "compare.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
