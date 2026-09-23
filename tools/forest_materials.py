"""Read photographic material inputs from the source Blender node graphs."""

from pathlib import Path


def asset_textures(obj):
    """Follow shader inputs, never guess albedo from a filename containing diff."""

    def images(socket, seen=None):
        seen = set() if seen is None else seen
        found = []
        for link in socket.links:
            node = link.from_node
            if node in seen:
                continue
            seen.add(node)
            if node.type == "TEX_IMAGE" and node.image:
                found.append(Path(node.image.filepath).name)
            else:
                for inp in node.inputs:
                    found.extend(images(inp, seen))
        return found

    result = {}
    for mat in obj.data.materials:
        if not mat or not mat.use_nodes:
            continue
        slot = {}
        for node in mat.node_tree.nodes:
            if node.type == "BSDF_PRINCIPLED":
                for key, socket in [
                    ("diff", "Base Color"),
                    ("normal", "Normal"),
                    ("roughness", "Roughness"),
                    ("alpha", "Alpha"),
                ]:
                    found = images(node.inputs[socket])
                    if found:
                        slot[key] = found[0]
            elif node.type == "MIX_SHADER":
                # factor=0 selects shader 1; factor=1 selects shader 2.
                transparent = [
                    i
                    for i in (1, 2)
                    if any(
                        l.from_node.type == "BSDF_TRANSPARENT"
                        for l in node.inputs[i].links
                    )
                ]
                found = images(node.inputs[0])
                if transparent and found:
                    slot["alpha"] = found[0]
                    slot["invert_alpha"] = transparent[0] == 2
        if not slot.get("diff"):
            candidates = [
                Path(n.image.filepath).name
                for n in mat.node_tree.nodes
                if n.type == "TEX_IMAGE" and n.image
            ]
            valid = [
                n
                for n in candidates
                if not any(
                    h in n.lower()
                    for h in ("normal", "_nor", "rough", "disp", "mask", "alpha")
                )
            ]
            if valid:
                slot["diff"] = next((n for n in valid if "diff" in n), valid[0])
            else:
                raise RuntimeError("Cannot resolve source base color for " + mat.name)
        if not slot.get("alpha"):
            candidates = [
                Path(n.image.filepath).name
                for n in mat.node_tree.nodes
                if n.type == "TEX_IMAGE" and n.image
            ]
            alpha = next((n for n in candidates if "alpha" in n), None)
            if alpha:
                slot["alpha"] = alpha
        result[mat.name] = slot
    return result
