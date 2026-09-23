"""Source terrain export and shared Blender mesh helpers.

The terrain is evaluated in isolation. Trees are exported separately by
export_forest_visuals.py; source scans and twig layouts are never reconstructed
from crown profiles or passed through the former decimation pipeline.
"""

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path

import bpy
import numpy as np
from forest_view import ORIGIN

REGION = (-68.0, 52.0, -100.0, 20.0)
HOME = ORIGIN
CLEAR_RADIUS = 9.0
PAD_RADIUS = 2.5
PAD_BLEND = 6.0
TERRAIN_MARGIN = 3.0
BAKE_SIZE = 2048


def log(*args):
    print("LOITER_EXPORT", *args, flush=True)


def to_webots(x, y, z):
    """Blender Z-up (home-relative) -> Webots Y-up. Returns (x, y, z)."""
    return (x - HOME[0], z, -(y - HOME[1]))


def mat_to_numpy(m):
    return np.array([[m[i][j] for j in range(4)] for i in range(4)])


def mesh_world_verts(obj, mesh):
    count = len(mesh.vertices)
    flat = np.empty(count * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", flat)
    local = flat.reshape(-1, 3)
    m = mat_to_numpy(obj.matrix_world).astype(np.float32)
    return (local @ m[:3, :3].T + m[:3, 3]).astype(np.float32)


def write_obj(path, verts, faces, uvs=None, normals=None):
    """Minimal OBJ writer. faces: list of (vert_idx, uv_idx, nrm_idx) loops."""
    with open(path, "w") as f:
        f.write("# Loiter forest export\n")
        for v in verts:
            f.write("v %.5f %.5f %.5f\n" % tuple(v))
        if uvs is not None:
            for uv in uvs:
                f.write("vt %.6f %.6f\n" % tuple(uv))
        if normals is not None:
            for n in normals:
                f.write("vn %.5f %.5f %.5f\n" % tuple(n))
        for face in faces:
            parts = []
            for vi, vti, vni in face:
                if uvs is not None and normals is not None:
                    parts.append("%d/%d/%d" % (vi + 1, vti + 1, vni + 1))
                elif uvs is not None:
                    parts.append("%d/%d" % (vi + 1, vti + 1))
                else:
                    parts.append("%d" % (vi + 1))
            f.write("f %s\n" % " ".join(parts))


def loop_vert_indices(mesh):
    loop_vert = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", loop_vert)
    return loop_vert


def loop_uvs(mesh):
    uv_layer = mesh.uv_layers.active if mesh.uv_layers else None
    if uv_layer is not None:
        uv_data = np.empty(len(mesh.loops) * 2, dtype=np.float32)
        uv_layer.data.foreach_get("uv", uv_data)
        return uv_data.reshape(-1, 2)
    # Evaluated geometry-nodes output exposes UVs as a generic attribute.
    attr = mesh.attributes.get("UVMap")
    if attr is None or attr.domain != "CORNER":
        return None
    flat = np.empty(len(mesh.loops) * 3, dtype=np.float32)
    attr.data.foreach_get("vector", flat)
    return flat.reshape(-1, 3)[:, :2].copy()


def loop_normals(mesh):
    normals = np.empty(len(mesh.loops) * 3)
    mesh.loops.foreach_get("normal", normals)
    return normals.reshape(-1, 3)


def build_subset(obj, mesh, keep_tris, uv_data, normals, flatten_home=False):
    """Build converted (verts, faces, uvs, normals) for kept triangulated faces."""
    world = mesh_world_verts(obj, mesh)
    loop_vert = loop_vert_indices(mesh)
    if uv_data is None:
        uv_data = np.zeros((len(mesh.loops), 2))
    # world-space axis conversion happens per-vertex below
    vert_map = {}
    verts, uvs, norms, faces = [], [], [], []
    for tri in keep_tris:
        face = []
        for li in tri:
            vi = int(loop_vert[li])
            if vi not in vert_map:
                x, y, z = world[vi]
                if flatten_home:
                    dx, dy = x - HOME[0], y - HOME[1]
                    dist = math.hypot(dx, dy)
                    if dist < PAD_BLEND:
                        home_z = HOME[2]
                        if dist <= PAD_RADIUS:
                            z = home_z
                        else:
                            t = (dist - PAD_RADIUS) / (PAD_BLEND - PAD_RADIUS)
                            smooth = t * t * (3 - 2 * t)
                            z = home_z + (z - home_z) * smooth
                else:
                    x, y, z = world[vi][0], world[vi][1], world[vi][2]
                vert_map[vi] = len(verts)
                verts.append(to_webots(x, y, z))
            uvs.append((float(uv_data[li][0]), float(uv_data[li][1])))
            n = normals[li]
            # Blender->Webots rotation applied to normals: (x,y,z)->(x,z,-y).
            norms.append((float(n[0]), float(n[2]), float(-n[1])))
            face.append((vert_map[vi], len(uvs) - 1, len(norms) - 1))
        faces.append(face)
    return verts, faces, uvs, norms


def home_ground_z(dg):
    obj = bpy.data.objects["terrain_main"]
    mesh = obj.evaluated_get(dg).to_mesh()
    world = mesh_world_verts(obj, mesh)
    dist = np.hypot(world[:, 0] - HOME[0], world[:, 1] - HOME[1])
    near = world[dist < PAD_RADIUS * 2, 2]
    obj.evaluated_get(dg).to_mesh_clear()
    return float(np.median(near))


def export_terrain(out, dg, home_z, force):
    target = out / "terrain.obj"
    if not force and target.exists() and (out / "terrain_meta.json").exists():
        log("terrain cached")
        return
    obj = bpy.data.objects["terrain_main"]
    mesh = obj.evaluated_get(dg).to_mesh()
    mesh.calc_loop_triangles()
    world = mesh_world_verts(obj, mesh)
    loop_vert = loop_vert_indices(mesh)
    uv_data = loop_uvs(mesh)
    normals = loop_normals(mesh)
    xmin, xmax, ymin, ymax = REGION
    keep = []
    for tri in mesh.loop_triangles:
        c = world[[int(loop_vert[li]) for li in tri.loops]].mean(axis=0)
        if (
            xmin - TERRAIN_MARGIN <= c[0] <= xmax + TERRAIN_MARGIN
            and ymin - TERRAIN_MARGIN <= c[1] <= ymax + TERRAIN_MARGIN
        ):
            keep.append([int(li) for li in tri.loops])
    log("terrain faces kept", len(keep), "of", len(mesh.loop_triangles))
    # Planar world UVs over the region: the source unwrap spans the whole
    # 200 m terrain, so the region would use a fraction of the bake. Planar
    # mapping also lets the world builder composite moss/litter splats (known
    # world positions) into the same texture.
    xmin, xmax, ymin, ymax = REGION
    px0, px1 = xmin - TERRAIN_MARGIN, xmax + TERRAIN_MARGIN
    py0, py1 = ymin - TERRAIN_MARGIN, ymax + TERRAIN_MARGIN
    kept_loops = np.array([li for tri in keep for li in tri], dtype=np.int32)
    uv_planar = uv_data.copy()
    for li in kept_loops:
        x, y = world[int(loop_vert[li])][0], world[int(loop_vert[li])][1]
        uv_planar[li, 0] = (x - px0) / (px1 - px0)
        uv_planar[li, 1] = (y - py0) / (py1 - py0)
    (out / "terrain_meta.json").write_text(json.dumps({"planar": [px0, py0, px1, py1]}))
    global HOME
    HOME = (HOME[0], HOME[1], home_z)
    verts, faces, uvs, norms = build_subset(
        obj, mesh, keep, uv_planar, normals, flatten_home=True
    )
    write_obj(target, verts, faces, uvs, norms)
    log("wrote", target, len(faces), "tris")
    obj.evaluated_get(dg).to_mesh_clear()


def bake_terrain(obj, mesh, keep_tris, loop_vert, world, uv_data, kind="DIFFUSE"):
    """Bake the terrain material in an otherwise empty scene.

    Cycles synchronizes the instanced scatter referenced by the terrain's
    geometry nodes (gigabytes) when baking in the source scene, exhausting
    RAM. Instead, bake an applied copy of the exported subset: same faces,
    UVs, path/river mask attributes and material, no modifiers, alone in a
    throwaway scene.
    """
    sub = bpy.data.meshes.new("loiter_bake_terrain")
    used, faces_idx = {}, []
    for tri in keep_tris:
        face = []
        for li in tri:
            vi = int(loop_vert[li])
            if vi not in used:
                used[vi] = len(used)
            face.append(used[vi])
        faces_idx.append(face)
    order = sorted(used, key=used.get)
    # Same flattening as the exported mesh so the bake matches the OBJ.
    flat = []
    for vi in order:
        x, y, z = (float(v) for v in world[vi])
        dx, dy = x - HOME[0], y - HOME[1]
        dist = math.hypot(dx, dy)
        if dist < PAD_BLEND:
            if dist <= PAD_RADIUS:
                z = HOME[2]
            else:
                t = (dist - PAD_RADIUS) / (PAD_BLEND - PAD_RADIUS)
                z = HOME[2] + (z - HOME[2]) * (t * t * (3 - 2 * t))
        flat.append((x, y, z))
    sub.from_pydata(flat, [], faces_idx)
    sub.update()
    uv_layer = sub.uv_layers.new(name="UVMap")
    for poly, tri in zip(sub.polygons, keep_tris):
        for loop_idx, li in zip(poly.loop_indices, tri):
            uv_layer.data[loop_idx].uv = uv_data[li].tolist()
    for attr_name in ("path", "river"):
        src = mesh.attributes[attr_name].data
        buf = np.empty(len(mesh.vertices) * 4, dtype=np.float32)
        src.foreach_get("color", buf)
        buf = buf.reshape(-1, 4)
        dst = sub.attributes.new(attr_name, "FLOAT_COLOR", "POINT").data
        for new_vi, old_vi in enumerate(order):
            dst[new_vi].color = buf[old_vi].tolist()
    sub.materials.append(bpy.data.materials["main_terrain"])
    for poly in sub.polygons:
        poly.use_smooth = True

    tmp = bpy.data.objects.new("loiter_bake_obj", sub)
    bake_scene = bpy.data.scenes.new("loiter_bake")
    bake_scene.collection.objects.link(tmp)
    prev_scene = bpy.context.window.scene
    bpy.context.window.scene = bake_scene
    bpy.context.view_layer.update()
    try:
        mat = bpy.data.materials["main_terrain"]
        nodes = mat.node_tree.nodes
        img = bpy.data.images.get("loiter_terrain_bake")
        if img is None:
            img = bpy.data.images.new("loiter_terrain_bake", BAKE_SIZE, BAKE_SIZE)
        if kind == "NORMAL":
            img.colorspace_settings.name = "Non-Color"
        tex = nodes.get("LoiterBake")
        if tex is None:
            tex = nodes.new("ShaderNodeTexImage")
            tex.name = "LoiterBake"
        tex.image = img
        for n in nodes:
            n.select = n == tex
        nodes.active = tex
        bpy.context.view_layer.objects.active = tmp
        tmp.select_set(True)
        bake_scene.render.engine = "CYCLES"
        bake_scene.cycles.samples = 1
        bake_scene.cycles.device = "CPU"
        bake_scene.render.bake.use_selected_to_active = False
        bake_scene.render.bake.use_clear = True
        bake_scene.render.bake.margin = 16
        t0 = time.time()
        bpy.ops.object.bake(
            type=kind, **({"pass_filter": {"COLOR"}} if kind == "DIFFUSE" else {})
        )
        log("terrain bake %.1fs" % (time.time() - t0))
        tmp.select_set(False)
    finally:
        bpy.context.window.scene = prev_scene
        bpy.data.scenes.remove(bake_scene)
        bpy.data.objects.remove(tmp, do_unlink=True)
        bpy.data.meshes.remove(sub)


def export_heights(out, dg):
    obj = bpy.data.objects["terrain_main"]
    mesh = obj.evaluated_get(dg).to_mesh()
    world = mesh_world_verts(obj, mesh)
    obj.evaluated_get(dg).to_mesh_clear()
    xmin, xmax, ymin, ymax = REGION
    # Webots frame: X = blender x (home-relative), Z = -(blender y).
    n = 120
    xs = np.linspace(xmin - HOME[0], xmax - HOME[0], n)
    zs = np.linspace(-(ymax - HOME[1]), -(ymin - HOME[1]), n)
    ix = np.clip(((world[:, 0] - xmin) / (xmax - xmin) * n).astype(int), 0, n - 1)
    iy = np.clip(((world[:, 1] - ymin) / (ymax - ymin) * n).astype(int), 0, n - 1)
    S = np.zeros((n, n))
    C = np.zeros((n, n))
    np.add.at(S, (iy, ix), world[:, 2])
    np.add.at(C, (iy, ix), 1)
    H = S / np.maximum(C, 1)
    C = (C > 0).astype(float)
    for _ in range(400):
        if not (C == 0).any():
            break
        filled = np.where(C > 0, H, np.nan)
        layers = [
            np.roll(filled, 1, 0),
            np.roll(filled, -1, 0),
            np.roll(filled, 1, 1),
            np.roll(filled, -1, 1),
        ]
        H = np.where(C > 0, H, np.nanmean(np.stack(layers), axis=0))
        C = np.where(np.isnan(H), 0, 1)
    # rows run south->north in blender y; flip to Webots +Z ordering note.
    grid = H[::-1].tolist()  # row 0 = max Webots z
    (out / "heights.json").write_text(
        json.dumps(
            {
                "n": n,
                "x": [xs[0], xs[-1]],
                "z": [zs[0], zs[-1]],
                "grid": grid,
            }
        )
    )
    log("wrote heights.json")


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    sys.path.insert(0, str(Path(__file__).parent))
    from export_floor_cover import prune

    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    source = out.parent / "pine-forest/source/polyhaven_pine_fir_forest.blend"
    bpy.ops.wm.read_factory_settings(use_empty=True)
    with bpy.data.libraries.load(str(source), link=False) as (_, loaded):
        loaded.objects = ["terrain_main"]
    obj = loaded.objects[0]
    bpy.context.scene.collection.objects.link(obj)
    group = bpy.data.node_groups["terrain"]
    group.links.new(
        group.nodes["landscape_subdivide"].outputs["Geometry"],
        group.nodes["Group Output"].inputs["Geometry"],
    )
    prune(group)
    bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    ground = home_ground_z(dg)
    export_terrain(out, dg, ground, args.force)
    export_heights(out, dg)
    manifest = {
        "source": "Poly Haven Pine Forest (CC0) https://polyhaven.com/collections/pine_forest",
        "blend": source.name,
        "blend_sha256": sha256_of(source),
        "blender": bpy.app.version_string,
        "region_blender": REGION,
        "home_blender": HOME[:2],
        "home_ground_z": ground,
        "clear_radius": CLEAR_RADIUS,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
