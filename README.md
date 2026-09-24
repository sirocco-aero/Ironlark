# Loiter

A playable Linux world where real drone autonomy software perceives, plans and
flies. Missions will span departure, outdoor travel, GPS-denied exploration,
inspection and return. Everything is simulated. Named for the holding
pattern: sit back and watch autonomy fly.

Today: a reproducible Webots–ArduPilot flight (take off, hover, land on EKF3
with simulated GPS) over a Poly Haven pine forest. What comes next is in
[ROADMAP.md](ROADMAP.md).

## Quick start

Needs Linux x86-64, OpenGL, Docker, [uv](https://docs.astral.sh/uv/),
`xvfb-run`, and [Webots R2025a](https://github.com/cyberbotics/webots/releases/tag/R2025a)
extracted to `./webots/` (or set `WEBOTS_HOME`).

```sh
./loiter setup           # pinned ArduPilot image, Python env
./loiter build-world     # forest world (downloads Blender and the scene)
./loiter build-renderer  # optional: Loiter's patched Webots
./loiter run             # watch a flight, fullscreen
./loiter check           # same flight, headless, pass/fail
```

| Command | Does |
| --- | --- |
| `setup` | Builds ArduPilot Copter 4.7.1 (`dbe7921`) in an Ubuntu 22.04 image; extracts its Iris model and Webots bridge to `.cache/`; creates `.venv/` (Python 3.12.11, NumPy, Pillow). System Python is untouched. |
| `doctor` | Checks every prerequisite. |
| `build-world` | Downloads Blender 4.2.9 and the [Poly Haven Pine Forest](https://polyhaven.com/collections/pine_forest) scene (checksummed), exports it stage by stage, and assembles `worlds/pine_forest/`. Resumable; `--skip-export` only reassembles. |
| `build-renderer` | Builds patched Webots into `.cache/webots-renderer/`. See [Webots patches](#webots-patches). |
| `render-reference` | Cycles renders of the source scene at the preview cameras, into `runs/reference/`: the ground truth for looks. Needs more than ~26 GB RAM (15 GB + zram was not enough); needs no built world. |
| `run` | Flies over the forest in a fullscreen window, then exits. `--world empty` for the bare test area; `--editor` for Webots' UI. |
| `check` | The same flight on a virtual display, empty world by default. Exits nonzero on failure. |

Webots is chosen in order: `WEBOTS_HOME`, the patched build if present, `./webots/`.
Build stages run with two jobs and stop at 4.5 GiB RSS; logs go to `.cache/build-logs/`.
First setup needs internet and several GB of disk. OS packages in the image float,
so builds are source-pinned, not bit-identical.

Configure in files, not panels; restart to apply:
[`worlds/flight_foundation.wbt`](worlds/flight_foundation.wbt) (empty-world scene, vehicle, camera, timestep),
[`config/flight.parm`](config/flight.parm) (autopilot overrides),
[`tests/flight_smoke.py`](tests/flight_smoke.py) (the flight check),
[`loiter`](loiter) (startup, versions, cleanup),
[`tools/`](tools) (the forest pipeline).

## What `check` proves

Native Webots runs the physics; containerized ArduPilot SITL flies. The upstream
bridge carries dynamics and sensors to SITL and motor outputs back; a small Iris
adapter treats disabled outputs as stopped, not reversed. MAVLink uses TCP 5760,
the bridge UDP 9002–9003. Occupied ports and concurrent runs are refused.

Pass requires EKF3, GPS fix, a valid position estimate, accepted commands,
sustained hover and disarm after landing. A separate supervisor records ground
truth, read only after landing: peak height 2.5–4 m, final ≤ 0.2 m, drift
≤ 1.5 m, autopilot and Webots clocks within 1 s.

Parameters load as: Copter SITL defaults, the Iris example, then
`flight.parm`, which enables EKF3 and tunes gains for 4.7 at 500 Hz. Pre-arm
checks stay on. Every run starts with fresh autopilot storage.

[`docker/webots-clock.patch`](docker/webots-clock.patch) fixes the pinned
bridge: after a packet delay it could reset its timestamp against a stale
wall clock, repeatedly adding elapsed time to the autopilot. The patch keeps the
timestamp across delays and rebases only when Webots time goes backwards.

**Localization boundary.** The Iris example uses simulator truth
(`AHRS_EKF_TYPE=10`); Loiter sets EKF3 (`3`), and the check rejects truth mode.
Position still comes from simulated GPS, which ArduPilot synthesizes from the
physical state Webots reports. GPS denial must be cut at the measurement layer,
never by removing that state. Feeding evaluator pose in as odometry is not
localization.

Each run writes `runs/<timestamp>/`: `manifest.json` (versions, Webots patches,
world, estimator), `result.json` (verdict, transitions, measurements),
`telemetry.jsonl`, evaluator-only `ground_truth.jsonl`, `world-map.png` (forest), and Webots, SITL and
ArduPilot logs. Ctrl+C or failure tears everything down; that is cleanup, not a
recovery policy.

## Webots patches

Loiter patches Webots where Webots is the bottleneck, rather than degrading
assets to suit it. No fork: [`native/patches/`](native/patches) is a series
applied in filename order to [R2025a](https://github.com/cyberbotics/webots/tree/c6793d8f7230a311c4bc2a3101d9f1a8bc0aa01b)
(`c6793d8`, Apache 2.0). `./loiter build-renderer` fetches sparse sources and
matching headers, compiles `glad`, `wren` and `webots` against the installed
R2025a libraries, and installs to `.cache/webots-renderer/`, beside the
untouched stock `./webots/`. Unchanged patches are not reapplied or recompiled.

Measured with `tools/profile_forest.py` (GTX 1060 3 GB, i5-4670):

| Patch | Fixes | Measured |
| --- | --- | --- |
| **0001-static-mesh-memory** | Merges identical vertices ([`native/vertex_index.hpp`](native/vertex_index.hpp)), triangles untouched; shadow-volume buffers only for meshes that can cast them; frees CPU vectors after upload. | RAM 5.5 → ~4 GB; load 98 s |
| **0002-shared-static-mesh** | Builds each GPU mesh once, not once per `USE` copy. | load 79 s |
| **0003-normals-overlay-off-at-load** | The normals debug overlay started enabled, so every mesh built and discarded one during load. | load 49 s |
| **0004-pbr-alpha-cutoff** | `PBRAppearance.alphaCutoff` (glTF `MASK`): alpha-tested texels, not blended. The source's 53 materials are all alpha-hashed; blending was slower and could mis-sort foliage. Mip-scaled alpha keeps distant coverage. | frame 31 → 22 ms (shadows off) |
| **0005-filmic-tone-mapping** | Blender's Filmic "Medium High Contrast" as a 65³ table (`tools/bake_filmic_lut.py`) replaces `1 − e^(−x)` plus gamma. Matches Blender within 1.3/255 at the 99th percentile. | frame unchanged |
| **0006-hdr-background** | `Background` accepts `.hdr` faces for the visible sky, kept as half-float linear radiance instead of 8-bit. | +12 MB VRAM |
| **0007-pbr-light-intensity-once** | PBR diffuse light scaled with intensity squared; now linear, so intensity equals Blender sun strength. | — |
| **0008-scattering-fog** | `Fog { fogType "SCATTERING" }`: single-scattering medium in HDR (density, color, Henyey–Greenstein anisotropy, box), lit by the directional lights and an ambient term; sun visibility from a top-down occlusion map, so canopy shafts come from the baked canopy light. Main view only; `Camera` devices not yet. | ~1–2 ms |
| **0009-instancing-and-visibility-range** | `Shape.instancesUrl`: one shape drawn at every transform in a binary file, culled per instance and drawn with one instanced call. `Shape.visibilityRange`: draw only within a camera-distance band, per instance when instanced. The forest's 55,277 cover placements and all trees use it, with the source's own lod0/lod1 distances. | load 42 → 20 s, frame 25 → 5.7 ms, RAM 3.7 → 2.3 GB, 16,814 → 185 geometries |
| **0010-transmission-and-flow-foam** | `PBRAppearance.transmission` (thin surface, Fresnel for IOR 1.333, premultiplied blending, no refraction offset) and `flowFoam`: the source river's foam recipe on the UVs, animated by simulation time. Not used by the forest yet: without reflections, clear water at grazing angles shows only bright sky. | river pixels only |

To add a patch: edit `.cache/webots-source/` (the series is staged there; `git add -N` new files), then
`git -C .cache/webots-source diff --binary -- src include resources > native/patches/NNNN-name.patch`,
`./loiter build-renderer`, `./loiter check`, `./loiter check --world forest`,
and note here what it fixes and how it was measured. To amend patch N, stage
its files and regenerate it with `git diff --cached --binary HEAD -- <files>`
(valid while no earlier patch touches them). Keep each patch clean enough to
become an upstream pull request.

`tools/render_lighting_reference.py` renders the built world in Cycles under the
source's own suns, world, fog and Filmic view (4.6 GB; the full source needs more
than ~26 GB); `tools/compare_views.py runs/lighting-reference runs/preview` scores
`tools/render_forest.py` views against it.
`tools/profile_forest.py --out runs/<name>` reports load time, real-time factor,
per-step cost and peak RAM/GPU for the forest as a viewer sees it.
`tests/test_vertex_index.cpp` proves hard normals and UV seams survive indexing
and that a large mesh's attributes reconstruct byte-for-byte:
`g++ -std=c++11 -O2 tests/test_vertex_index.cpp -o .cache/test_vertex_index && .cache/test_vertex_index`.

## Research atlas

[`research/drone-autonomy-atlas.html`](research/drone-autonomy-atlas.html):
159 drone autonomy projects and 126 sourced relationships. Opens offline in a
browser; source links need internet.

## References and licenses

[ArduPilot Webots integration](https://ardupilot.org/dev/docs/sitl-with-webots-python.html) ·
[pinned example](https://github.com/ArduPilot/ardupilot/tree/dbe792162d06cab66c3475fd5556bf7a120f119e/libraries/SITL/examples/Webots_Python) ·
[ArduPilot ROS 2](https://ardupilot.org/dev/docs/ros2-install.html) ·
[GPS/non-GPS transitions](https://ardupilot.org/copter/docs/common-non-gps-to-gps.html)

ArduPilot, Webots, Blender, Poly Haven and saved research material keep their
own terms; setup saves ArduPilot's license beside its cache.
