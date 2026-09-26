# Roadmap

## World

One connected, visually rich world: outdoor travel, forest, rocky terrain,
caves, industrial sites, and a GTA-style minimap. Art style is open; pick what
coheres and build whatever import support it needs. Engine format support is
never the reason to reject an environment.

### Now

[Poly Haven Pine Forest](https://polyhaven.com/collections/pine_forest) (CC0),
built from its Blender scene by `./ironlark build-world`: authored tree variants
and saplings, source placements, cover, river, terrain, sky and baked canopy
light. Goal: match the source renders. Rule: no enshittification. Fix engine
and pipeline bottlenecks; don't cut asset quality.

Rejected: Kenney Nature Kit (look).

Matched to the source: Filmic "Medium High Contrast" view, both suns (strength,
color, direction), the camera-visible sky (HDRI × 0.2 with its rotation), the
lighting sky (Nishita × 0.7), and HD (1K) object textures downsampled from the
source's originals, the source's exposure (+1) and no bloom. Both skies are written
as linear radiance; Blender's own HDR save had stored them display-encoded (2.4×
too bright at mid-grey). Light under the canopy comes from baked visibility
layers traced through the real foliage (`tools/bake_forest_light.py`, patches
0026–0028).

The world has no visible end. The viewer is fenced inside the exported source
terrain (203 × 200 m, patch 0012). Beyond it, the region mirrored across each
edge of the rectangle the terrain fully covers (its edges are ragged by up to 3 m): ground and river truly mirrored (continuous at the seams), trees and cover
at their mirrored places but turned, so none faces its twin
(`tools/forest_backdrop.py`). Trees past 150 m are impostors captured from the
trees as drawn (`tools/bake_impostors.py`, patch 0017), out to 2 km, where fog
seen from the 70 m ceiling hides what lies beyond; past 400 m they thin with
distance and widen to keep the canopy closed, as the source's scatter thins with
camera distance. Ground past the first ring is decimated. The fog is a layer
with a soft top (patches 0014, 0021) whose far haze meets the sky at the
horizon (patch 0020). Rendering: 23 ms per step (19 without the far rings).

Open fidelity gaps against the source (Cycles lighting the same geometry with the
source's lights, `tools/render_lighting_reference.py` and `tools/compare_views.py`:
0.96–1.23× as bright at the pinned views):

- **Bounced light**: Webots has none; under the canopy Cycles' sunlight bounces
  off leaves and ground. Inside the forest the fog adds ×1.67 light to Cycles' ×1.37.
- **Fog**: robot `Camera` devices lack it; calibrate against
  `./ironlark render-reference` (needs more than ~26 GB RAM).
- **Terrain**: four 4K baked tiles (~100 m each). The source tiles its ground
  materials under masks; matching that needs a Webots detail-map patch, and
  would be sharper up close with far less VRAM.
- **River**: done: the source's clear water (patch 0010) with flowing white foam
  (0019), mirroring the banks and trees through screen-space reflections (0030).
  No refraction offset yet: the bed shows straight through.
- **Foliage shimmer**: needles thinner than a pixel flip in and out as the view
  moves (22% of pixels for a 2 cm step). Needs multisampling with
  alpha-to-coverage for masked materials.
- **LiDAR vs foliage**: range sensors ignore `alphaCutoff`, so twig cards
  would read as solid planes. Fix before LiDAR work.

Known bugs: with ambient occlusion off (`ambientOcclusionRadius 0` or the GTAO
preference) most trees do not draw; cause not yet found.

### Candidates for later regions

Not purchased, imported or tested in Webots.

- [Mountain Forests and Meadows](https://www.fab.com/listings/5c2e9ea7-ea36-413c-bd03-1dbb48f0c403) ([flythrough](https://youtu.be/dPvsIqHenrk))
- [Mountain Lake Valley](https://assetstore.unity.com/packages/3d/environments/landscapes/mountain-lake-valley-environment-built-in-version-140485)
- [Yos3D Cave Mine](https://yos3d.com/asset/environment-cave-mine/): assembled scene plus FBX sources
- [Scans Factory Industrial Abandoned Buildings](https://www.fab.com/listings/7bf45f1b-9ed7-4e83-b071-4aa46f073ea3) ([flythrough](https://www.youtube.com/watch?v=Li7rrcwHslw))

| Source | Offers | Work needed |
| --- | --- | --- |
| [LTU cave world](https://github.com/LTU-RAI/gazebo_cave_world) (MIT) | ~150 × 102 × 33 m cave: chambers, shafts, stalactites, squeezes | Convert SDF and meshes; keep DARPA notices; validate collision |
| [DARPA SubT](https://github.com/osrf/subt/wiki/World-Creation-Tutorial) | Robotics-grade caves and tunnels | Convert Gazebo placements and meshes; check terms; plugins need reimplementation |
| [SubTGraph](https://github.com/LTU-RAI/SubTGraph) | Configurable multi-level cave, mine and lava-tube layouts | Import OBJ or tiles; keep generator topology away from the drone's map |
| [Kenney Cave](https://kenney.nl/assets/modular-cave-kit) / [Factory](https://kenney.nl/assets/factory-kit) kits (CC0) | Modular interiors | Assemble, check clearances; restyle to match |
| [Forest3D](https://github.com/unitsSpaceLab/Forest3D) (AGPL-3.0) | Procedural terrain and vegetation placement | Adapt exporter; benchmark vegetation cost |
| [Webots OSM importer](https://github.com/cyberbotics/webots/blob/R2025a/docs/automobile/openstreetmap-importer.md) | Real street layouts | Add detail and interiors |

Assets from different sources won't fit together by themselves. Expect work on
scale, materials, lighting, terrain seams and collision.

### Pipeline rules

- Keep originals, their terms and checksums in `.cache/`; generate runtime
  assets and scenes reproducibly from code.
- Rendered surface and collision must agree wherever navigation depends on
  them. Vegetation gets deliberate sensor and collision behaviour.
- World geometry is never an autonomy map; unknown areas stay unknown.
- GPS loss is a simulated rule, not a side effect of a roof.
- Measure rendering, sensing and physics together before promising scale or
  frame rate.
- Presentation is the world itself: fullscreen, configured in files.

## Now: the simulator as a test bench

Goal: a simulator the drone's autonomy can be developed and judged in: the
source's looks, a drone carrying real sensors, and time-consistent data out
through ROS 2. The takeoff check stays the regression gate. New scenes come
later, each when a capability needs it.

### 1. Finish the forest's looks

Approved look: tag `looks-good-2026-09-26`. Check every change against it
(A/B renders of the same views) and against Cycles (`tools/render_lighting_reference.py`;
render it at the preview's aspect, 1920×861 or half, or crop to the same vertical FOV).

Handoff state (2026-09-26), partly unverified:

- Done, verified: light layers baked without cover under 3 m (it shaded itself:
  dark rocks); cover gets the source's normal and roughness maps (the lookup had a
  wrong path); patch 0037 stops black foliage from degenerate normal-map frames.
- Built, not yet verified: 0035 fog in `Camera` devices (color only; medium from
  `WbFog` via a provider hook, so headless cameras get it too); 0036 range sensors
  see instanced shapes and cut alpha-masked texels (they skipped every instance, so
  a LiDAR saw no trees or rocks).
- River rocks black under the sun: fixed by casting no cascade shadow from dense
  small clutter (over 2000 instances and under 0.25 m placed height: the river bed's
  `rock_moss_set_02_rock07`…`13` pebbles, ~78 k instances, and two dry-branch sets;
  `tools/build_forest_world.py`). Root cause still unknown, recorded for later: in
  the shadow maps such a carpet shades itself black. A lone pebble on the bed stays
  dark, the same pebble 0.5 m up is lit and casts a correct shadow; the water, LOD1
  meshes, mesh winding, backdrop terrain, cascade caching (tried as patch 0038,
  reverted) and a 4× normal offset are ruled out. The rocks of `set_01`, as small but
  ~950 per mesh, shade correctly. Repro: `/tmp`-style world with the terrain, lights
  and one `rock07` instance (instance 729 at −21.01, 32.14, −0.92), `--lights main`.
- River close up: done (0038). The water shows its sunlit bed and the banks.
- Next, in order: terrain detail maps (read the source's `main_terrain` material
  first: tiled textures under masks; the bake is 2.5 cm per texel); GTAO-off missing
  trees; shimmer. Then verify 0035 and 0036 while building the sensor rig.

### 2. Sensor rig and ROS 2

Start with 3D LiDAR, IMU and RGB camera. LiDAR and IMU drive first
localization; the camera serves viewing now and object understanding later.
First localization must not depend on cave lighting.

- A Ironlark vehicle PROTO around the Iris flight model, with sensor poses and
  settings in versioned config, and a look to match the forest (the stock Iris
  looks crude).
  Plan: `config/sensors.json` is the one source of mounts, rates, ranges and noise;
  a Blender script generates the PROTO (carbon arms, machined motor bells, two-blade
  props with a fastHelix blur disc, legs ending at the Iris box bottom, z −0.055) and
  keeps the Iris physics, bounding box, motor names and its noiseless
  accelerometer/gyro/inertial unit/GPS for ArduPilot (also ground truth). Sensors: a
  Mid-360-class LiDAR on top (360° × 59°, `verticalFieldOfView` 1.03, `tiltAngle`
  +0.39 for −7°…52°; check the sign on a wall), 0.1–40 m, 10 Hz; a separate noisy IMU
  at 250 Hz (2 ms physics step); a front camera, 640×480 at 20 Hz.
- LiDAR honours `alphaCutoff`: foliage cards must not read as solid planes.
- ROS 2 Humble with a bridge publishing `/clock`, IMU, point clouds, images,
  camera calibration and transforms. Webots' Python controller and the ROS
  environment stay independently reproducible.
- Webots time throughout. One place defines Webots ↔ ENU/FLU ↔ NED/FRD.
  Pausing must not advance sensor time; slow rendering must not invent it.
- Configurable noise and rates. If the odometry needs per-return LiDAR timing,
  model acquisition; never fake scan timing on an instantaneous range image.
- rosbag2 record and replay. Ground truth never reaches autonomy topics.

  Plan: `flight_bridge` steps the robot every 2 ms in lockstep with SITL; hook a
  sampler after each step (Webots time; numpy wraps `wb_lidar_get_point_cloud` and
  camera buffers without copies) streaming framed messages (magic, type, sim time
  ns, length) over localhost TCP from a sender thread with a bounded queue (drop and
  count, never stall physics). A `ros:humble-ros-base` container (image already
  pulled; `--network host`) runs an rclpy node publishing `/clock`, IMU, PointCloud2,
  images (bgra8 → bgr8), camera_info and static TF from `config/sensors.json`, ground
  truth under `/ground_truth/*` only; `./ironlark run --record` adds `ros2 bag record`
  into the run folder, `./ironlark replay RUN` plays it with `--clock`. Webots frames
  are ENU/FLU = REP-103; one module documents Webots ↔ ENU/FLU ↔ NED/FRD.

**Done when** one recorded flight holds synchronized sensor data; a known wall
reads at the right distance and orientation; camera and LiDAR agree; replay
keeps timestamps and transforms; the flight check, with sensing on, reports its
added cost.

## Later: regions

Added one at a time as the drone's capabilities need them; candidates under
[Candidates for later regions](#candidates-for-later-regions).

### Outdoor-to-cave region

From [the world](#world): an outdoor home, rocks,
vegetation and an accessible entrance to a real cave section. The drone departs
outdoors; it never spawns inside.

- Asset manifest: source, revision or checksum, license, units, conversion.
- Convert placements, meshes, textures and collision into reusable Webots
  components, adding format support where needed.
- Generate the scene from config with a fixed seed; keep full geometry and
  generator topology out of navigation.
- Check openings and sensor returns, not just looks.
- Profile physics, sensing and rendering together; fix measured costs.

**Done when** the drone takes off outdoors and correctly senses entrance,
terrain, vegetation and cave walls. Autonomous cave travel is not yet claimed.

## Next: autonomy

### GPS-free flight

Evaluate LiDAR-inertial odometry on the recordings. Feed pose, velocity,
uncertainty and health to ArduPilot's external-navigation input. Hover and make
short moves with navigation GPS off; measure error against the evaluator.

Next: local mapping, collision-aware motion, tracking-loss handling, and
GPS ↔ external-navigation transitions tested in motion, keeping frame and
return route. GPS degradation needs an explicit model.

First full mission: leave home, reach a given entrance, map a bounded unknown
interior, exit, return, land. Its behaviours become reusable tasks under a
manager that handles priority, interruption and resumption while perception,
mapping, planning and control run concurrently. General-purpose behaviour
grows from these tested pieces.
