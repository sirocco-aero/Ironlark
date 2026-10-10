"""The world's meshes as the assembly writes them from the export's: in Webots' frame, and without the back copy of a
double-faced card where the material is drawn from both sides. Their own module so a result kept in the stage store is
keyed by this code alone (tools/stage_cache.py).
"""


def enu(v):
    """Legacy export cache (X,Y-up,Z) -> flight/world ENU, a proper rotation."""
    return (v[0], -v[2], v[1])


def copy_mesh(src, dst):
    with open(src) as fi, open(dst, "w") as fo:
        for line in fi:
            if line.startswith(("v ", "vn ")):
                kind, *v = line.split()
                fo.write(kind + " %.6f %.6f %.6f\n" % enu(list(map(float, v[:3]))))
            else:
                fo.write(line)


def drop_back_copies(path):
    """Remove the second of each pair of faces with the same corners and texture
    coordinates (a card and its reversed copy)."""
    lines = path.read_text().splitlines(keepends=True)
    positions = [l.split()[1:4] for l in lines if l.startswith("v ")]
    uvs = [l.split()[1:3] for l in lines if l.startswith("vt ")]
    seen, kept, dropped = set(), [], 0
    for line in lines:
        if line.startswith("f "):
            corners = []
            for vertex in line.split()[1:]:
                index = vertex.split("/")
                uv = tuple(uvs[int(index[1]) - 1]) if len(index) > 1 and index[1] else ()
                corners.append((tuple(positions[int(index[0]) - 1]), uv))
            key = frozenset(corners)
            if len(key) == len(corners) and key in seen:
                dropped += 1
                continue
            seen.add(key)
        kept.append(line)
    if dropped:
        path.write_text("".join(kept))
    return dropped
