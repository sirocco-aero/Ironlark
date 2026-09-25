# Roadmap

## World

One connected, visually rich world: outdoor travel, forest, rocky terrain,
caves, industrial sites, and a GTA-style minimap. Art style is open; pick what
coheres and build whatever import support it needs. Engine format support is
never the reason to reject an environment.

### Now

[Poly Haven Pine Forest](https://polyhaven.com/collections/pine_forest) (CC0),
built from its Blender scene by `./loiter build-world`: authored tree variants
and saplings, source placements, cover, river, terrain, sky and baked canopy
light. Goal: match the source renders. Rule: no enshittification. Fix engine
and pipeline bottlenecks; don't cut asset quality.

Rejected: Kenney Nature Kit (look).

Matched to the source: Filmic "Medium High Contrast" view, both suns (strength,
color, direction), the camera-visible sky (HDRI × 0.2 with its rotation), the
lighting sky (Nishita × 0.7), and HD (1K) object textures downsampled from the
source's originals. Both skies are written as linear radiance; Blender's own HDR
save had stored them display-encoded (2.4× too bright at mid-grey).

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
source's lights: 0.85–1.9× as bright at the pinned views):

- **Sky occlusion**: every surface sees the whole sky; under the canopy the
  source sees little of it. With the second sun (strength 1 of 6) unshadowed,
  shaded ground stays too bright (the drone's view is 1.9×).
- **Fog**: robot `Camera` devices lack it; calibrate against
  `./loiter render-reference` (needs more than ~26 GB RAM).
- **Terrain**: four 4K baked tiles (~100 m each). The source tiles its ground
  materials under masks; matching that needs a Webots detail-map patch, and
  would be sharper up close with far less VRAM.
- **River**: the source's is clear water with flowing white foam. The foam flows
  (patches 0010, 0019); the water stays opaque until reflections exist
  (screen-space reflections): clear water at grazing angles mirrors the banks and
  trees; Webots can only reflect sky.
- **Foliage shimmer**: needles thinner than a pixel flip in and out as the view
  moves (22% of pixels for a 2 cm step). Needs multisampling with
  alpha-to-coverage for masked materials.
- **LiDAR vs foliage**: range sensors ignore `alphaCutoff`, so twig cards
  would read as solid planes. Fix before LiDAR work.

Known bugs: with `ambientOcclusionRadius 0` the forest view can render only sky
(a small test world renders fine; the viewpoint's position is right); cause not
yet found.

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

## Next: perception in a connected world

Goal: a drone that produces useful, time-consistent observations of a small
connected world. The takeoff check stays the regression gate.

### 1. Sensor rig and ROS 2

Start with 3D LiDAR, IMU and RGB camera. LiDAR and IMU drive first
localization; the camera serves viewing now and object understanding later.
First localization must not depend on cave lighting.

- A Loiter vehicle PROTO around the Iris, with sensor poses and settings in
  versioned config.
- ROS 2 Humble with a bridge publishing `/clock`, IMU, point clouds, images,
  camera calibration and transforms. Webots' Python controller and the ROS
  environment stay independently reproducible.
- Webots time throughout. One place defines Webots ↔ ENU/FLU ↔ NED/FRD.
  Pausing must not advance sensor time; slow rendering must not invent it.
- Configurable noise and rates. If the odometry needs per-return LiDAR timing,
  model acquisition; never fake scan timing on an instantaneous range image.
- rosbag2 record and replay. Ground truth never reaches autonomy topics.

**Done when** one recorded flight holds synchronized sensor data; a known wall
reads at the right distance and orientation; camera and LiDAR agree; replay
keeps timestamps and transforms; the flight check, with sensing on, reports its
added cost.

### 2. Outdoor-to-cave region

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

### Then: GPS-free flight

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
