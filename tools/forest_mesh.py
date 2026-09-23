"""Write material subsets directly, preserving source vertex/UV/normal data."""

import numpy as np


def write_material_obj(
    path,
    positions,
    loop_vertices,
    uvs,
    normals,
    triangles,
    base_z=0.0,
    uv_scale=(1.0, 1.0),
    two_sided=False,
):
    """Stream OBJ records; never make Python tuples for every triangle corner."""
    loops = np.unique(triangles)
    vertices = np.unique(loop_vertices[loops])
    vertex_index = np.empty(len(positions), dtype=np.int32)
    vertex_index[vertices] = np.arange(1, len(vertices) + 1, dtype=np.int32)
    loop_index = np.empty(len(loop_vertices), dtype=np.int32)
    loop_index[loops] = np.arange(1, len(loops) + 1, dtype=np.int32)
    with path.open("w") as stream:
        stream.write("# Source geometry, UVs and corner normals; legacy cache axes.\n")
        for vi in vertices:
            x, y, z = positions[vi]
            stream.write("v %.5f %.5f %.5f\n" % (x, z - base_z, -y))
        for li in loops:
            u, v = uvs[li]
            stream.write("vt %.6f %.6f\n" % (u * uv_scale[0], v * uv_scale[1]))
        for sign in (1, -1) if two_sided else (1,):
            for li in loops:
                x, y, z = normals[li]
                stream.write("vn %.5f %.5f %.5f\n" % (sign * x, sign * z, -sign * y))
        for tri in triangles:
            face = [
                (vertex_index[loop_vertices[li]], loop_index[li], loop_index[li])
                for li in tri
            ]
            stream.write("f " + " ".join("%d/%d/%d" % f for f in face) + "\n")
            if two_sided:
                stream.write(
                    "f "
                    + " ".join(
                        "%d/%d/%d" % (v, t, n + len(loops))
                        for v, t, n in reversed(face)
                    )
                    + "\n"
                )
    return len(triangles) * (2 if two_sided else 1)
