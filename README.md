# Ironlark

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
./ironlark setup           # pinned ArduPilot image, Python env
./ironlark build-world     # forest world (downloads Blender and the scene)
./ironlark build-renderer  # optional: Ironlark's patched Webots
./ironlark run             # watch a flight, fullscreen
./ironlark check           # same flight, headless, pass/fail
```

| Command | Does |
| --- | --- |
| `setup` | Builds ArduPilot Copter 4.7.1 (`dbe7921`) in an Ubuntu 22.04 image, which keeps only the SITL binary, its parameters and pymavlink (0.3 GB; Docker's build cache holds the rest); extracts its Iris model and Webots bridge to `.cache/`; creates `.venv/` (Python 3.12.11, NumPy, Pillow). System Python is untouched. |
| `doctor` | Checks every prerequisite. |
| `build-world` | Downloads Blender 4.2.9 and the [Poly Haven Pine Forest](https://polyhaven.com/collections/pine_forest) scene (checksummed), exports it stage by stage, and assembles `worlds/pine_forest/`. Resumable; `--skip-export` only reassembles. |
| `build-drone` | Models the drone in Blender and writes `protos/IronlarkDrone.proto` (also run by `build-world`, and by `run`/`check` when missing). |
| `build-renderer` | Builds patched Webots into `.cache/webots-renderer/`. See [Webots patches](#webots-patches). |
| `render-reference` | Cycles renders of the source scene at the preview cameras, into `runs/reference/`: the ground truth for looks. Needs more than ~26 GB RAM (15 GB + zram was not enough); needs no built world. |
| `run` | Flies over the forest in a fullscreen window, then exits. Until the autopilot can arm (EKF3 learning its gyro biases, ~40 s of simulated time) the simulation runs as fast as it can, the view at 10 fps; takeoff comes ~20 s after launch. `--world empty` for the bare test area; `--editor` for Webots' UI; `--record` streams the sensors to ROS 2 and records a rosbag2 in the run folder (in real time throughout). |
| `replay RUN` | Plays a recorded run's bag in the ROS 2 container; its `/clock` topic carries Webots time. |
| `check-recording RUN` | Checks a recorded empty-world flight against the calibration wall: LiDAR distance and orientation, camera ↔ LiDAR agreement. |
| `check-replay RUN` | Replays a recorded flight while recording it again and compares: same messages, stamps and static transforms. |
| `check` | The same flight on a virtual display, empty world by default. Exits nonzero on failure. |

Webots is chosen in order: `WEBOTS_HOME`, the patched build if present, `./webots/`.
Build stages run with two jobs and stop at 4.5 GiB RSS; logs go to `.cache/build-logs/`.
First setup needs internet and several GB of disk. OS packages in the image float,
so builds are source-pinned, not bit-identical.

Configure in files, not panels; restart to apply:
[`worlds/flight_foundation.wbt`](worlds/flight_foundation.wbt) (empty-world scene, vehicle, camera, timestep),
[`config/flight.parm`](config/flight.parm) (autopilot overrides),
[`config/sensors.json`](config/sensors.json) (sensor poses, rates, ranges, noise),
[`tests/flight_smoke.py`](tests/flight_smoke.py) (the flight check),
[`ironlark`](ironlark) (startup, versions, cleanup),
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
(`AHRS_EKF_TYPE=10`); Ironlark sets EKF3 (`3`), and the check rejects truth mode.
Position still comes from simulated GPS, which ArduPilot synthesizes from the
physical state Webots reports. GPS denial must be cut at the measurement layer,
never by removing that state. Feeding evaluator pose in as odometry is not
localization.

Each run writes `runs/<timestamp>/`: `manifest.json` (versions, Webots patches,
world, estimator), `result.json` (verdict, transitions, measurements),
`telemetry.jsonl`, evaluator-only `ground_truth.jsonl`, `world-map.png` (forest), and Webots, SITL and
ArduPilot logs. Ctrl+C or failure tears everything down; that is cleanup, not a
recovery policy.

## Sensors and ROS 2

The drone ([`tools/build_drone.py`](tools/build_drone.py)) keeps the Iris flight model (motors,
propeller constants, mass, inertia, collision box, and the noiseless IMU and GPS ArduPilot reads)
and carries the rig of `config/sensors.json`: a Mid-360-class LiDAR (360° × 59°, −7° to +52°,
0.1–40 m, 10 Hz, 32 × 900 returns), an IMU (250 Hz, seeded white noise) and a front camera
(640 × 480, 90°, 20 Hz, with the scattering fog, patch 0035). With `--record`, the flight
controller samples them after each 2 ms physics step, on Webots time, and streams them over local
TCP ([`sensing/protocol.py`](sensing/protocol.py)); a full queue drops and counts, never stalling
physics, and pausing stops the clock. A ROS 2 Humble container ([`ros/`](ros)) publishes `/clock`,
`/imu/data`, `/lidar/points` (x, y, z, ring, time), `/camera/image_raw` (bgra8),
`/camera/camera_info` and `/tf_static`, ground truth only on `/ground_truth/pose` and
`/ground_truth/velocity`, and records all of it with rosbag2 (reliable, deep queues: `ros/record_qos.yaml`; each message compressed with zstd, losslessly: a forest flight's minute is 1.2 GB, the empty world's 0.16 GB). A recorded check adds sensing's cost to `result.json` (real-time factor, messages sent and dropped). Frames are REP-103: Webots' world is
ENU and bodies FLU; [`sensing/frames.py`](sensing/frames.py) is the one place for ENU/FLU ↔ NED/FRD.

## Webots patches

Ironlark patches Webots where Webots is the bottleneck, rather than degrading
assets to suit it. No fork: [`native/patches/`](native/patches) is a series
applied in filename order to [R2025a](https://github.com/cyberbotics/webots/tree/c6793d8f7230a311c4bc2a3101d9f1a8bc0aa01b)
(`c6793d8`, Apache 2.0). `./ironlark build-renderer` fetches sparse sources and
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
| **0011-cascaded-shadow-maps** | Directional lights cast through three cascaded shadow maps (2048², 100 m), not stencil volumes: any mesh size, alpha masks included, instanced casters culled per cascade at the viewer's LOD. Far cascades are reused until the view leaves them; the near one redraws every frame. | frame 5.7 → 12.3 ms; brightness vs Cycles 1.4–2.3× → 1.15–1.96× |
| **0012-viewpoint-bounds** | `Viewpoint.boundsMin/boundsMax` and `groundClearance` (a physics ray down to the solid below): mouse navigation and scripts slide along the limits instead of leaving the world. | — |
| **0013-navigation-distance** | Panning and zooming scale with the distance to what is under the cursor; without a pick Webots used the distance to the world origin, so navigation near it crawled. Now a physics ray finds it (floor 2 m). Instanced shapes no longer draw, stacked at the origin, in passes that cannot place instances (picking caught those phantoms). | — |
| **0014-fog-layer** | A zero `boxSize` axis leaves the scattering fog unbounded along it; `boxSize 0 0 h` is a horizontal layer. The source's 209 m fog box showed its walls as hard lines from the world's edge. | — |
| **0015-mirrored-instances** | Instances with a mirroring transform draw with the front face inverted, in a second call; `reverseNormals` is now set on every draw, not only turned on. The backdrop's ground is the region mirrored. | — |
| **0016-foliage-shading** | Alpha-masked materials draw both faces, back faces shaded towards the viewer, as Blender does: single-faced needle cards had half their cards missing. Minified normal maps no longer use a frame from screen derivatives, which breaks once a pixel spans several needles; the mipmapped normal's length (how much its texels disagree) shades through a first-order model instead. | distant trees' direct light matched Cycles within 4% (was 27% dark) |
| **0017-impostors** | `Shape.impostor`: instances drawn as quads showing the three captured views (8 × 8 hemi-octahedral atlas) nearest the camera's direction, lit from captured mean normals, with per-texel depth for the depth buffer and shadows. Trees past 150 m use them. | backdrop 59 → 20 ms per step (12 ms without backdrop); impostors ≈ full trees within 4% |
| **0018-fog-horizon** | `Fog.boxFalloff`: density fades to zero over that distance inside the box's bounded faces, so the fog layer's top is soft. Past the 400 m marched with canopy shadow, a few unshadowed steps carry the haze to the horizon. From above, the layer's top showed as a hard line against the sky. | — |
| **0019-white-foam** | `flowFoam` foam is white and opaque, and its bump blends with the flat normal by the Bump node's strength, as in the source. Foam had only bumped normals: dark streaks with dark rims. | — |
| **0020-horizon-haze** | `Fog.horizonRadiance`: the visible sky just above the horizon, by azimuth; far haze converges to it, so haze below the horizon meets the sky above it. It converged to the medium's ambient light, bluer and darker, and a hard line showed from altitude. | — |
| **0021-fog-falloff-outside** | `boxFalloff` now thins density outside the box, exponentially, instead of inside it: the source's fog stays whole, and from above near-level rays gather haze towards the horizon. | — |
| **0022-instance-cells** | Instances sorted into 64 m cells; whole cells are culled by frustum and distance band before any instance is tested. The backdrop holds 1.5M tree copies before thinning. | — |
| **0023-impostor-early-depth** | Impostor quads sit at the front of their capture sphere, so texel depths only push fragments back, declared with `depth_greater`: the GPU rejects hidden fragments before shading again. | impostors 7.1 → 4.2 ms |
| **0024-compressed-textures** | `ImageTexture` reads BC7 DDS (DX10 header, mip levels included): a quarter of RGBA8's memory, no CPU copy kept. Cached copies of a compressed texture never regenerate mipmaps (that made the driver decompress them: a 13 s stall). The build compresses every texture but the terrain (`etcpak`; 41–47 dB on those, 22–32 dB on the terrain's fine noise). | GPU 2830 → 2376 MB, RAM 2199 → 1865 MB, frame 23.4 → 22.5 ms |
| **0025-multisampling** | Opt-in (`OpenGL/multisampling` in Webots' preferences, default 0): opaque geometry drawn multisampled, alpha-masked materials with alpha to coverage, resolved before ambient occlusion. | at 4 samples: foliage flicker −31%, frame +9.4 ms |
| **0026-light-occlusion** | `Background.lightOcclusion*`: baked light visibility layers at heights above the ground (sky, suns without shadow maps, the shadowed sun past its cascades, the sky overhead). PBR surfaces scale image based lighting by it (reflections by the overhead sky as they point up) and the unshadowed sun; every surface saw the whole sky, even under dense canopy. Mirrored outside, as the backdrop. | sky light under canopy vs Cycles 3.5–3.9× → 1.5–1.7×; +2 ms |
| **0027-stochastic-shadow-alpha** | Alpha-masked shadow casters keep each shadow texel with probability equal to their coverage (hard cutoff where texels are resolved): filtered lookups pass light through foliage. A boosted cutoff made crowns opaque. | main sun under canopy vs Cycles 0.30× → 0.79× |
| **0028-fog-light-occlusion** | The scattering fog is lit through the light occlusion layers, and by the sky it scatters along each view direction (`Fog.ambientRadiance`: the sky against its phase, 9 × 8 directions) instead of the sky's mean in every direction. | fog's added light matches Cycles (×1.34 vs ×1.34) |
| **0029-fog-depth** | The scattering fog decoded WREN's depth as `1 − near/(2d)`; with zero-to-one clip depth it is `1 − near/d`, so it put every surface at half its distance and too little haze in front of it. | brightness vs Cycles 0.79–1.20× → 0.96–1.23× |
| **0030-water-reflections** | The opaque scene (color, depth) is copied before translucent objects draw; transmissive surfaces march their reflection across its depth and mirror what they hit, the sky only where rays leave the screen. The clear river mirrored only sky: pale at grazing angles. | river colour vs Cycles within 10%; +0.3 ms |
| **0031-fog-shafts** | Near the viewer the fog reads the sun's shadow maps, in steps that grow with distance: shafts through the canopy. | — |
| **0032-backlit-foliage** | Foliage takes no light from behind it (the wrap floor is gone); the second sun, 25° from the first, is shaded by the first's shadow maps near the viewer. | — |
| **0033-fog-sky-phase** | Fog in front of geometry scatters no more sky than the phase-weighted table gives (looking down: the dark ground, not the sky's mean); the air is in the shade of either the maps or the bake. | overview brightness vs Cycles 1.66× → 1.14×; backlit shadows (p10) 3.1× → 1.55× |
| **0034-fog-box-sides** | `Fog.boxFalloffHorizontal`: the source's 209 m fog box, decaying over 30 m past its sides, replaces the unbounded layer that veiled the backdrop to the horizon. | region canopy from 60 m vs Cycles: 2.4× (checkpoint) → 1.6× |
| **0035-camera-fog** | Robot `Camera` devices (color) render the scattering fog; `WbFog` provides the medium to every view, headless ones included. | drone camera frames show the forest's haze |
| **0036-range-sensors-see-foliage** | Range sensors (RangeFinder, Lidar) draw instanced shapes (they skipped them all) and cut alpha-masked texels as the color pass does; impostors stay out. | forest scan at 2.5 m: 20.8 k of 28.8 k returns, median 17 m |
| **0037-normal-map-degenerate-frame** | A degenerate normal-map frame (tiny triangles in a pixel quad) falls back to the surface normal instead of NaN: no black foliage. | — |
| **0038-reflections-blocked-by-scene** | A reflected ray that passes behind the scene (under a crown, behind a trunk) takes that geometry's colour instead of missing to the sky map, which has no forest: the river shows its bed and banks, not a pale sheen. | river close-up matches Cycles by eye |
| **0039-terrain-detail** | `Background.terrainDetail*` and `PBRAppearance.terrainDetail`: near the viewer the ground takes the source's tiled layers (leaves, ground, rocky trail, by its `path`/`river` masks) as detail over its 2.5 cm bake: their colour over their own colour at the bake's resolution, and their fine slopes. The bake keeps its content (the painted floor plants). | — |
| **0040-device-overlay-perspective** | A camera or range finder's overlay, created after the world restored its perspective, takes that perspective's saved visibility, size and position instead of showing: the drone camera's hidden overlay stays hidden. | — |
| **0041-temporal-anti-aliasing** | The main view jitters its projection by a sub-pixel Halton offset each frame and blends the frame (HDR, after the fog) with its history reprojected through depth: a Catmull-Rom history, clamped to the neighbourhood's range, rejected where its stored depth disagrees (surfaces revealed behind moving foliage), taking more of the new frame in motion. Preference `OpenGL/temporalAntiAliasing` (on); robot cameras are left single-frame. | needles flickering under a quarter-pixel turn: 3.9% → 0.36% of canopy pixels; +1.5% frame time |
| **0042-frame-profiler** | `IRONLARK_FRAME_LOG=FILE`: each rendered view (main, robot cameras, range sensors) logs the CPU and GPU time of its passes (GPU timestamp queries, read back frames later), and each main-view frame and physics step its clock times; `IRONLARK_FRAME_LOG_DRAWS=1` times every draw. [`tools/frame_log.py`](tools/frame_log.py) summarizes. | nothing unless set |
| **0043-real-time-pacing** | Real time follows the clock: each step is due when the clock reaches the time it simulates, so steps a rendered frame delayed run at once (more than three frames late, the simulation slows instead of fast-forwarding). A frame is due one period after the last one started, once the simulation has caught up. Frames came one period after the last one ended, and a moving average of step times set the sleeps: frames showed 36, 136, 38 ms of simulated time in turn. | forest flight, 1920×1080: 6.8 → 7.8 fps, simulated time per frame p50/p99 136/175 → 116/124 ms, 0.77× → 0.90× real time; empty world with sensing 0.82× → 1.0× |
| **0044-vertex-transforms** | PBR vertex shaders transform points by the model rows and the view, and normals by the model's cofactors; the fragment shader takes the inverse view from the (rigid) view matrix. They inverted two 4×4 matrices per vertex and passed a constant 3×3 matrix as a varying. Equal up to float rounding (isolated needle-edge pixels). | forest view 92.7 → 89.1 ms, opaque 66.7 → 62.6 ms |
| **0045-fog-layer-search** | The scattering fog finds its light layers without indexing the layer heights by a variable. Bit-identical. | fog pass 7.4–10.3 → 6.9–8.9 ms at 1920×1080 |
| **0046-occlusion-culling** | Colour views (main, robot cameras) cull instances hidden behind what they have drawn: first the instances seen last frame, then a max-depth pyramid of that depth (half resolution, read back) tests the rest by bounding sphere; drawn ones are retested a quarter per frame. Meshes under 64 triangles (impostors) draw second, culled a 64 m cell at a time. A view that hides under 10 M triangles draws in one pass for 30 frames. Exact: images and LiDAR returns unchanged. `IRONLARK_OCCLUSION=0` turns it off. | camera frame 130 → 62 M triangles, 82 → 59 ms; forest with sensing 0.49× → 0.61× real time; forest view 88 → 70 ms |
| **0047-instance-culling-cost** | Instances are grouped by 16 m sub-cell within their 64 m cells, and each one's bounding sphere is computed once, not every pass; a draw looks up its uniforms once per program, not per draw. Culling a LiDAR face walked thousands of cover instances with a matrix product and square root each. Same instances drawn. | LiDAR scan CPU 22 → 14 ms; forest with sensing 0.61× → 0.62× |
| **0048-mesh-cache** | `WEBOTS_MESH_CACHE=DIR`: a mesh file's parse (assimp's output, as floats) is kept in DIR, keyed by the file's real path, size and time; later loads read it instead of parsing OBJ text, which was half the forest's load. The launcher and tools use `.cache/mesh-cache` (116 MB); the first load after `build-world` fills it. Same arrays: images and LiDAR returns unchanged. | forest load 24 → 13 s; takeoff 68 → 56 s after launch |
| **0049-system-zlib-first** | `webots-bin` links the system zlib ahead of assimp, which exports an old bundled copy that PNG decoding otherwise bound to (where the system's is zlib-ng, several times faster). Same decoded pixels. | forest load 12.8 → 11.0 s |
| **0050-lazy-shadow-volumes** | Stencil shadow volumes (point and spot lights, or a sun without shadow maps) are built from a mesh's uploaded buffers the first time a light needs them, not for every mesh at load. The forest's sun uses the shadow maps. Stencil shadows unchanged. | GPU −16 MB, RAM −65 MB |
| **0051-detail-texture-formats** | The terrain detail roughness and blend mask are stored in the channels the shader reads (R8, RG8), not RGBA8. Same sampling. | GPU −56 MB |
| **0052-shared-texcoord-buffer** | A mesh's pen-painting texture coordinates reuse its texture-coordinate buffer when the bytes are equal (in the forest, every mesh), instead of a copy. | GPU −19 MB |
| **0053-mesh-copies-trimmed** | Webots' CPU copy of a file mesh (for collision, picking, the pen) no longer holds a second, "scaled" set of coordinates, recomputed before use anyway, nor a second set of texture coordinates; its arrays are sized once instead of doubling. Physics bit-identical. | RAM 1750 → 1400 MB; load 10.6 → 9.7 s |
| **0054-texture-images-freed** | Decoded PNG textures are freed once uploaded; infra-red distance sensors, the one later reader, already reload the file. | RAM 1400 → 1065 MB |
| **0055-mesh-attributes-per-vertex** | That CPU copy keeps a file mesh's normals and texture coordinates per vertex, looked up through its triangle indices, not per triangle corner (3.8 corners per vertex in the forest). Same values. | RAM 1065 → 764 MB |
| **0056-instance-memory** | A Shape re-reads its instances file when a renderable lacks them instead of keeping every transform; instance cells use 32-bit indices, trimmed to size. | RAM 764 → 719 MB |
| **0057-vertex-cache-order** | Each triangle mesh's GPU copy is ordered for the post-transform vertex cache ([meshoptimizer](https://github.com/zeux/meshoptimizer), vendored in [`native/meshoptimizer/`](native/meshoptimizer)), vertices in order of first use; orders are kept in the mesh cache (37 MB). Same triangles: only depth ties between intersecting cards resolve differently (≤0.02% of pixels, less than two runs of one build differ). | vertex shader runs per triangle 1.28 → 0.82; forest view 70.0 → 67.1 ms |
| **0058-pen-shader-variant** | PBR vertex shaders read the pen's texture coordinates only in a `#define PEN` twin of their program, which WREN compiles and uses for a material once a `Pen` paints on it. Every vertex fetched them for the fragment shader's pen branch, taken only with a pen texture. Images and pen paint unchanged. | forest view 67.2 → 65.2 ms, overview 49.2 → 47.8 ms |
| **0059-lazy-button-icons** | Toolbar and menu icons (512-pixel images, an enabled and a disabled one each) are decoded the first time they are drawn in that state, not all at start; each state draws the same image through the same Qt code. Window and menus pixel-identical. | RAM 719 → 655 MB |
| **0060-shared-sun-visibility** | The PBR shader looks up the shadowed sun's maps once per pixel, not once per sun: both suns used the same lookup (four shadow taps and a matrix product). Robot cameras and LiDAR unchanged; the main view differs by rounding (≤0.21% of pixels by more than 8 levels, isolated). | forest view 65.2 → 63.4 ms, high 41.5 → 39.3 ms |

To add or amend the newest patch: edit `.cache/webots-source/` (`git add -N` new
files), then `.venv/bin/python tools/save_webots_patch.py [NNNN-name.patch]`,
`./ironlark build-renderer`, `./ironlark check`, `./ironlark check --world forest`,
and note here what it fixes and how it was measured. The builder refuses to
reapply the series over edits not saved as a patch. Keep each patch clean enough to
become an upstream pull request.

`tools/render_lighting_reference.py` renders the built world in Cycles under the
source's own suns, world, fog and Filmic view (4.6 GB; the full source needs more
than ~26 GB); `tools/compare_views.py runs/lighting-reference runs/preview` scores
`tools/render_forest.py` views against it.
`tools/profile_forest.py --out runs/<name>` reports load time, real-time factor,
frame rate, GPU and CPU time per render pass and peak RAM/GPU for the forest as a
viewer sees it (headless: under `xvfb-run -a -s "-screen 0 1920x1080x24"`, which
renders on the NVIDIA GPU; its swap waits for a readback, so frames come slower than
on a display). Any run can log frames: `IRONLARK_FRAME_LOG=/tmp/f.log ./ironlark run`,
then `tools/frame_log.py /tmp/f.log --from 41 --to 60` (simulated seconds).
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
own terms; setup saves ArduPilot's license beside its cache. meshoptimizer (MIT)
is vendored with its license in [`native/meshoptimizer/`](native/meshoptimizer).
