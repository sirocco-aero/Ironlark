"""Model the Ironlark drone's parts in Blender and export one OBJ per material.

    blender --background --python drone_meshes.py -- --out <folder>

Robot frame FLU (x forward, y left, z up), metres, origin at the Iris flight model's origin. The
shape keeps the Iris's physics: its motors (thrust points at x +-0.13, y +-0.20/0.22, z 0.023) and its
collision box (0.47 x 0.47 x 0.11 about the origin: the feet stand on its bottom, z -0.055).
Propellers are modelled at their hub (the Propeller's slowHelix sits at the thrust point).
"""
import argparse
import math
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

p = argparse.ArgumentParser()
p.add_argument("--out", type=Path, required=True)
args = p.parse_args(sys.argv[sys.argv.index("--") + 1:])
args.out.mkdir(parents=True, exist_ok=True)

bpy.ops.wm.read_factory_settings(use_empty=True)
PARTS = {}  # material -> objects

MOTORS = [(0.13, -0.22), (-0.13, 0.20), (0.13, 0.22), (-0.13, -0.20)]
PROP_RADIUS = 0.127  # 10 inch


def add(material, obj):
    PARTS.setdefault(material, []).append(obj)
    return obj


def finish(obj, bevel=0.0, segments=3, subdivide=0, smooth=True):
    if bevel:
        m = obj.modifiers.new("bevel", "BEVEL")
        m.width, m.segments, m.limit_method = bevel, segments, "ANGLE"
    if subdivide:
        m = obj.modifiers.new("subdivide", "SUBSURF")
        m.levels = m.render_levels = subdivide
    if smooth:
        for poly in obj.data.polygons:
            poly.use_smooth = True
    return obj


def box(size, location, bevel=0.0, subdivide=0, segments=3):
    bpy.ops.mesh.primitive_cube_add(size=1, location=location)
    obj = bpy.context.object
    obj.scale = size
    bpy.ops.object.transform_apply(scale=True)
    return finish(obj, bevel, segments, subdivide)


def cylinder(radius, depth, location, rotation=(0, 0, 0), vertices=48, bevel=0.0):
    bpy.ops.mesh.primitive_cylinder_add(radius=radius, depth=depth, location=location, rotation=rotation,
                                        vertices=vertices)
    return finish(bpy.context.object, bevel, 2)


def between(radius, a, b, vertices=24):
    """A cylinder from point a to point b."""
    a, b = Vector(a), Vector(b)
    d = b - a
    obj = cylinder(radius, d.length, (a + b) / 2, vertices=vertices)
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = Vector((0, 0, 1)).rotation_difference(d.normalized())
    return obj


# Body: a rounded shell, an orange band around it, a carbon top plate; the battery under it.
add("paint", box((0.17, 0.11, 0.045), (0, 0, 0), bevel=0.016, segments=4, subdivide=1))
add("accent", box((0.172, 0.112, 0.006), (0, 0, 0.004), bevel=0.016, segments=4, subdivide=1))
add("carbon", box((0.15, 0.092, 0.004), (0, 0, 0.0245), bevel=0.012, segments=4))
add("battery", box((0.13, 0.058, 0.026), (0, 0, -0.036), bevel=0.004, segments=2))
add("accent", box((0.012, 0.062, 0.03), (0.035, 0, -0.036), bevel=0.002, segments=2))  # strap
add("accent", box((0.012, 0.062, 0.03), (-0.035, 0, -0.036), bevel=0.002, segments=2))

for mx, my in MOTORS:
    sx, sy = math.copysign(1, mx), math.copysign(1, my)
    # Arm from the body's corner to the motor mount, a clamp at each end.
    add("carbon", between(0.0085, (sx * 0.055, sy * 0.03, 0.0), (mx, my, 0.0)))
    add("metal_dark", cylinder(0.021, 0.005, (mx, my, 0.0015), bevel=0.001))
    add("metal_dark", box((0.022, 0.024, 0.014), (sx * 0.062, sy * 0.036, 0.0), bevel=0.002, segments=2))
    # Motor: stator bell, orange ring, shaft.
    add("metal_dark", cylinder(0.0165, 0.018, (mx, my, 0.013), bevel=0.0015))
    add("accent", cylinder(0.0168, 0.0022, (mx, my, 0.0205), bevel=0.0005))
    add("metal", cylinder(0.004, 0.008, (mx, my, 0.025)))
    # Leg: from under the arm down and out to a rubber foot on the collision box's bottom.
    top = Vector((mx * 0.55, my * 0.5, -0.006))
    foot = Vector((mx * 0.62, my * 0.6, -0.049))
    add("carbon", between(0.0045, top, foot, vertices=16))
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.006, location=foot, segments=24, ring_count=12)
    add("rubber", finish(bpy.context.object))
    # Navigation light under the mount: red left, green right, white rear.
    light = "led_white" if mx < 0 else ("led_red" if my > 0 else "led_green")
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.0035, location=(mx, my, -0.0025), segments=16, ring_count=8)
    add(light, finish(bpy.context.object))

# LiDAR (Mid-360 class): carbon base, black body, dark glass window, cap.
add("carbon", cylinder(0.036, 0.004, (0, 0, 0.0285), bevel=0.001))
add("metal_black", cylinder(0.0325, 0.030, (0, 0, 0.0455), bevel=0.002))
add("glass", cylinder(0.0312, 0.025, (0, 0, 0.073), vertices=64))
add("metal_black", cylinder(0.0325, 0.009, (0, 0, 0.09), bevel=0.003))
# GPS puck, flat at the rear of the top plate: below the LiDAR's field of view.
add("gps", cylinder(0.02, 0.008, (-0.058, 0, 0.0305), bevel=0.003))
# Front camera: housing, lens barrel, glass.
add("metal_black", box((0.03, 0.036, 0.03), (0.095, 0, -0.012), bevel=0.004, segments=3))
add("metal_dark", cylinder(0.0095, 0.012, (0.114, 0, -0.012), rotation=(0, math.pi / 2, 0), bevel=0.001))
add("glass", cylinder(0.0075, 0.002, (0.1205, 0, -0.012), rotation=(0, math.pi / 2, 0)))


def propeller(direction):
    """Two twisted, tapered blades and a hub, at the hub's centre; direction +1 turns
    counterclockwise seen from above (the blades' leading edges lead that way)."""
    bm = bmesh.new()
    stations = 24
    chord_profile = lambda r: 0.018 + 0.012 * math.sin(math.pi * min(1.0, (r - 0.012) / 0.1)) - 0.012 * max(0.0, (r - 0.1) / 0.027) ** 2
    twist_profile = lambda r: math.radians(28 - 18 * (r - 0.012) / (PROP_RADIUS - 0.012))
    section = 12
    for blade in (0, 1):
        rings = []
        for i in range(stations + 1):
            r = 0.012 + (PROP_RADIUS - 0.012) * i / stations
            chord, twist = chord_profile(r), twist_profile(r)
            thickness = chord * 0.1
            ring = []
            for k in range(section):
                a = 2 * math.pi * k / section
                u, w = 0.5 * chord * math.cos(a), 0.5 * thickness * math.sin(a) * (1.0 if math.sin(a) > 0 else 0.4)
                # Rotate the section by the twist about the blade's axis (x), leading edge up.
                y = u * math.cos(twist) - w * math.sin(twist)
                z = u * math.sin(twist) * direction + w * math.cos(twist)
                ring.append(bm.verts.new((r, -y * direction, z)))
            rings.append(ring)
        for i in range(stations):
            for k in range(section):
                a, b = rings[i][k], rings[i][(k + 1) % section]
                c, d = rings[i + 1][(k + 1) % section], rings[i + 1][k]
                bm.faces.new((a, b, c, d))
        bm.faces.new(list(reversed(rings[0])))
        bm.faces.new(rings[-1])
        if blade:
            bmesh.ops.rotate(bm, verts=[v for ring in rings for v in ring], cent=(0, 0, 0),
                             matrix=Matrix.Rotation(math.pi, 3, "Z"))
    mesh = bpy.data.meshes.new(f"prop_{direction}")
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(mesh.name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    finish(obj)
    hub = cylinder(0.011, 0.008, (0, 0, 0.001), bevel=0.002)
    return obj, hub


for direction, name in ((1, "ccw"), (-1, "cw")):
    blades, hub = propeller(direction)
    add(f"prop_{name}", blades)
    add(f"prop_{name}", hub)
# Spinning propeller: a translucent disc (the Propeller's fastHelix).
bpy.ops.mesh.primitive_circle_add(vertices=64, radius=PROP_RADIUS, fill_type="TRIFAN", location=(0, 0, 0.002))
add("prop_disc", finish(bpy.context.object, smooth=False))

# One OBJ per material: modifiers applied, triangulated, Webots axes (x, y, z as they are).
for material, objects in PARTS.items():
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.wm.obj_export(filepath=str(args.out / f"{material}.obj"), export_selected_objects=True,
                          apply_modifiers=True, export_triangulated_mesh=True, export_materials=False,
                          export_uv=True, export_normals=True, forward_axis="Y", up_axis="Z")
    print("DRONE_PART", material, len(objects), flush=True)
