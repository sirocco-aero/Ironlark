#!/usr/bin/env python3
"""Summarize a frame log (IRONLARK_FRAME_LOG, Webots patch 0042): main-view frames, physics steps, and the
CPU and GPU time of each rendered view's passes.

    tools/frame_log.py LOG [--from S] [--to S] [--json]

Lines: F begin swap end sim_ms (a main-view frame presented), S begin end sim_ms (a physics step, robot
cameras included), G label begin cpu us gpu us [pass cpu_us gpu_us]... (a rendered view: main, or a device by
size). Times are CLOCK_MONOTONIC nanoseconds. --from/--to select simulated seconds.
"""
import argparse
from collections import defaultdict
import json
import statistics


def percentile(values, p):
    v = sorted(values)
    k = (len(v) - 1) * p / 100
    f = int(k)
    return v[f] + (v[min(f + 1, len(v) - 1)] - v[f]) * (k - f)


def stats(values):
    if not values:
        return None
    return {"n": len(values), "mean": round(statistics.fmean(values), 2), "p50": round(percentile(values, 50), 2),
            "p99": round(percentile(values, 99), 2), "max": round(max(values), 2)}


def parse(path):
    frames, steps, views = [], [], []
    for line in open(path, errors="replace"):
        p = line.split()
        if p[:1] == ["F"] and len(p) >= 5:
            frames.append((int(p[1]), int(p[2]), int(p[3]), float(p[4]) / 1000))
        elif p[:1] == ["S"] and len(p) >= 4:
            steps.append((int(p[1]), int(p[2]), float(p[3]) / 1000))
        elif p[:1] == ["G"] and len(p) >= 7:
            passes = defaultdict(lambda: [0.0, 0.0])
            rest = p[7:]
            for i in range(0, len(rest) - 2, 3):
                passes[rest[i]][0] += float(rest[i + 1])
                passes[rest[i]][1] += float(rest[i + 2])
            views.append({"label": p[1], "begin": int(p[2]), "cpu": float(p[4]) / 1000, "gpu": float(p[6]) / 1000,
                          "passes": passes})
    return sorted(frames), sorted(steps), sorted(views, key=lambda v: v["begin"])


def summarize(frames, steps, views, start=-1e9, end=1e9):
    out = {}
    frames = [f for f in frames if start <= f[3] < end]
    steps = [s for s in steps if start <= s[2] < end]
    if len(frames) >= 2:
        intervals = [(b[0] - a[0]) / 1e6 for a, b in zip(frames, frames[1:])]
        wall = (frames[-1][0] - frames[0][0]) / 1e9
        worst = sorted(intervals)[-max(1, len(intervals) // 100):]
        out["frames"] = {
            "count": len(frames), "fps": round((len(frames) - 1) / wall, 2),
            "interval_ms": stats(intervals), "worst_1pct_ms": round(statistics.fmean(worst), 2),
            "over_50ms": sum(i > 50 for i in intervals), "over_100ms": sum(i > 100 for i in intervals),
            # Simulated time shown per frame: steady when motion is smooth.
            "sim_advance_ms": stats([(b[3] - a[3]) * 1000 for a, b in zip(frames, frames[1:])]),
            "real_time_factor": round((frames[-1][3] - frames[0][3]) / wall, 3),
        }
    # Views rendered within the selected span of frames and steps.
    bounds = [t for f in frames for t in (f[0], f[2])] + [t for s in steps for t in (s[0], s[1])]
    if bounds:
        views = [v for v in views if min(bounds) - 1 <= v["begin"] <= max(bounds) + 1]
    if len(steps) >= 2:
        wall = (steps[-1][1] - steps[0][0]) / 1e9
        out["steps"] = {"count": len(steps), "real_time_factor": round((steps[-1][2] - steps[0][2]) / wall, 3),
                        "step_ms": stats([(s[1] - s[0]) / 1e6 for s in steps]),
                        "between_ms": stats([(b[0] - a[1]) / 1e6 for a, b in zip(steps, steps[1:])])}
    by_label = defaultdict(list)
    for v in views:
        by_label[v["label"]].append(v)
    out["views"] = {}
    for label, vs in sorted(by_label.items()):
        passes = defaultdict(lambda: [0.0, 0.0])
        for v in vs:
            for name, (c, g) in v["passes"].items():
                passes[name][0] += c
                passes[name][1] += g
        out["views"][label] = {
            "gpu_ms": stats([v["gpu"] for v in vs]), "cpu_ms": stats([v["cpu"] for v in vs]),
            "passes_ms": {name: {"cpu": round(c / len(vs) / 1000, 2), "gpu": round(g / len(vs) / 1000, 2)}
                          for name, (c, g) in passes.items()
                          if name not in ("triangles", "instances") and not name.startswith("draw")},
            "triangles_per_frame": round(passes["triangles"][0] / len(vs)) if "triangles" in passes else None,
        }
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("log")
    parser.add_argument("--from", dest="start", type=float, default=-1e9, help="simulated seconds")
    parser.add_argument("--to", dest="end", type=float, default=1e9, help="simulated seconds")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = summarize(*parse(args.log), args.start, args.end)
    if args.json:
        print(json.dumps(summary, indent=1))
        return
    f = summary.get("frames")
    if f:
        i, a = f["interval_ms"], f["sim_advance_ms"]
        print(f"main view: {f['count']} frames, {f['fps']} fps, interval {i['mean']} ms (p99 {i['p99']}, max {i['max']}, "
              f"worst 1% {f['worst_1pct_ms']}), simulated per frame {a['p50']} ms (p99 {a['p99']}), "
              f"{f['real_time_factor']}x real time")
    s = summary.get("steps")
    if s:
        print(f"physics: {s['count']} steps, {s['real_time_factor']}x real time, step {s['step_ms']['mean']} ms "
              f"(p99 {s['step_ms']['p99']}), between steps {s['between_ms']['mean']} ms (p99 {s['between_ms']['p99']})")
    for label, v in summary["views"].items():
        passes = " ".join(f"{n} {x['gpu']}" for n, x in v["passes_ms"].items() if x["gpu"] >= 0.1)
        print(f"{label}: {v['gpu_ms']['n']} frames, GPU {v['gpu_ms']['mean']} ms (p99 {v['gpu_ms']['p99']}), "
              f"CPU {v['cpu_ms']['mean']} ms; GPU by pass: {passes}")


if __name__ == "__main__":
    main()
