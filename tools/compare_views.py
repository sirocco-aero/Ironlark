#!/usr/bin/env python3
"""Score Webots views against reference renders of the same cameras.

    .venv/bin/python tools/compare_views.py runs/lighting-reference runs/preview
        ->  compare.json, and <view>-compare.png: reference | Webots | difference

Per view: mean luminance ratio, contrast (luminance spread) ratio, mean
saturation of each, per-channel mean ratio, and SSIM of luminance.
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


def main():
    p = argparse.ArgumentParser()
    p.add_argument("reference", type=Path)
    p.add_argument("webots", type=Path)
    args = p.parse_args()
    report = {}
    for ref_path in sorted(args.reference.glob("*.png")):
        ours_path = args.webots / ref_path.name
        if not ours_path.exists() or ref_path.name.endswith("-compare.png"):
            continue
        ref = Image.open(ref_path).convert("RGB")
        ours = Image.open(ours_path).convert("RGB").resize(ref.size, Image.Resampling.LANCZOS)
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
