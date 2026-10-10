"""The world's texture work that depends only on the images it reads: source photographs and twig cards made into the
world's textures, the impostor atlases, the texture size limit, and dilating colour into empty texels. Its own module so a result kept in the stage store is keyed by this code alone
(tools/stage_cache.py).
"""
import numpy as np
from PIL import Image, ImageOps

# A view covers the capture sphere's diameter; at the impostor distance (150 m) on a 1080-line screen with the
# viewer's 1 rad field of view, a metre spans about 12 pixels.
IMPOSTOR_PIXELS_PER_METRE = 12.0
IMPOSTOR_FRAMES = 8
# HD textures, matching Poly Haven's official 1K downsamples of the same originals.
TEXTURE_LIMIT = 1024


def dilate(color, alpha, steps=16):
    """Spread colours into empty texels so filtering never pulls in black: each step, a texel next to a filled one
    takes the mean of its filled 3 x 3 neighbours (the image's edge repeated beyond it)."""
    valid = (alpha > 1e-3).astype(np.float32)
    color = color * valid[..., None]
    height, width = valid.shape
    offsets = [(dy, dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1)]

    def around(rows, cols):
        """Each texel's 3 x 3 neighbours, the edge repeated: (rows, columns) per offset."""
        return [(np.clip(rows + dy, 0, height - 1), np.clip(cols + dx, 0, width - 1)) for dy, dx in offsets]

    filled = valid > 0
    pad = np.pad(filled, 1, mode="edge")
    reached = np.zeros_like(filled)
    for dy, dx in offsets:
        reached |= pad[1 + dy:1 + dy + height, 1 + dx:1 + dx + width]
    rows, cols = np.nonzero(reached & ~filled)  # the texels that fill first; only these are ever summed
    for _ in range(steps):
        if not len(rows):
            break
        acc, weight = 0, 0
        neighbours = around(rows, cols)
        for r, c in neighbours:  # the order the whole-image sums added in: equal floats, empty texels being 0
            acc = acc + color[r, c]
            weight = weight + valid[r, c]
        color[rows, cols] = acc / weight[:, None]
        valid[rows, cols] = 1.0
        filled[rows, cols] = True
        # Next, the empty neighbours of the texels just filled: no other texel gained a filled neighbour.
        reached = np.zeros_like(filled)
        for r, c in neighbours:
            reached[r, c] = True
        rows, cols = np.nonzero(reached & ~filled)
    fill = color[valid > 0].mean(0) if valid.any() else np.zeros(color.shape[-1])
    color[valid == 0] = fill
    return color


def impostor_atlases(build, out, variant, radius):
    """Albedo+alpha and normal atlases, sized so a view has about the screen's pixels
    at the impostor distance."""
    data = np.load(build / f"impostor_{variant}.npz")
    alpha = data["alpha"]
    safe = np.maximum(alpha, 1e-6)[..., None]
    albedo = np.clip(data["albedo"] / safe, 0, 1)
    albedo = np.where(albedo <= 0.0031308, 12.92 * albedo, 1.055 * albedo ** (1 / 2.4) - 0.055)
    # The mean normal over each texel's needles, kept unnormalized: its length says how much
    # they agree, which the impostor shader's foliage lighting uses.
    normal = np.clip(data["normal"] / safe * 2 - 1, -1, 1)
    depth = np.clip(data["depth"] / safe[..., 0], 0, 1)
    # Power-of-two views: Webots would rescale any other atlas size.
    view = int(np.clip(2 ** np.ceil(np.log2(2 * radius * IMPOSTOR_PIXELS_PER_METRE)), 32, 256))
    size = (IMPOSTOR_FRAMES * view,) * 2
    color = Image.fromarray(np.round(np.dstack([dilate(albedo, alpha), alpha]) * 255).astype(np.uint8), "RGBA")
    color.resize(size, Image.Resampling.LANCZOS).save(out / f"meshes/impostor_{variant}.png")
    # Normals (RGB) and depth (A) shade the crown softly: half the views' resolution is enough.
    filled = dilate(np.dstack([normal * 0.5 + 0.5, depth]), alpha)
    Image.fromarray(np.round(filled * 255).astype(np.uint8), "RGBA").resize(
        (size[0] // 2, size[1] // 2), Image.Resampling.LANCZOS).save(out / f"meshes/impostor_{variant}_normal.png")


def limit_texture(path):
    """Downsample a texture above TEXTURE_LIMIT, in place."""
    with Image.open(path) as image:
        image.load()
    image.thumbnail((TEXTURE_LIMIT, TEXTURE_LIMIT), Image.Resampling.LANCZOS)
    image.save(path)


def bleed_alpha(path):
    """Give masked-out texels their opaque neighbours' colours, in place. Mipmaps average colour over the mask: with
    near-black texels there (78% of a twig atlas), distant foliage darkened to a fifth of its colour. Nothing visible
    changes at full resolution."""
    with Image.open(path) as image:
        rgba = np.asarray(image, np.float32) / 255
    alpha = rgba[..., 3]
    if (alpha > 0.5).all():
        return
    # Dilate from texels that stay opaque at any cutoff, a few texels at a time, then fill
    # the rest (which only deep mipmaps reach) with the mean opaque colour.
    color = dilate(rgba[..., :3], np.where(alpha > 0.5, alpha, 0), steps=8)
    keep = alpha[..., None] > 0.5
    rgba[..., :3] = np.where(keep, rgba[..., :3], color)
    Image.fromarray(np.round(rgba * 255).astype(np.uint8), "RGBA").save(path)


def bleed_normal(base, normal):
    """Same for a normal map, under its base colour's mask: its mipmaps then average the needles' normals only, whose
    disagreement the renderer shades (Webots patch 0018)."""
    with Image.open(base) as image:
        alpha = image.getchannel("A")
    with Image.open(normal) as image:
        rgb = np.asarray(image.convert("RGB"), np.float32) / 255
    alpha = np.asarray(alpha.resize(rgb.shape[1::-1], Image.Resampling.BILINEAR), np.float32) / 255
    if (alpha > 0.5).all():
        return
    keep = alpha[..., None] > 0.5
    rgb = np.where(keep, rgb, dilate(rgb, np.where(alpha > 0.5, alpha, 0), steps=8))
    Image.fromarray(np.round(rgb * 255).astype(np.uint8), "RGB").save(normal)


def photograph(src, dst, mode="RGB", alpha=None, invert=False):
    """A source photograph at 1024 x 1024 (Lanczos) in mode, with alpha's grey levels, inverted with invert, as its
    alpha when given."""
    image = Image.open(src).convert(mode).resize((1024, 1024), Image.Resampling.LANCZOS)
    if alpha:
        mask = Image.open(alpha).convert("L").resize(image.size, Image.Resampling.LANCZOS)
        if invert:
            mask = ImageOps.invert(mask)
        image.putalpha(mask)
    image.save(dst)


def twig_atlas(build, cards, color, normal):
    """A species' twig cards (export_forest_visuals.py) at 512 x 512 in a 4 x 4 grid: colour with its alpha at full
    precision (no hard threshold erases needles), and the normals."""
    img = Image.new("RGBA", (2048, 2048))
    normals = Image.new("RGB", (2048, 2048), (128, 128, 255))
    for card in cards:
        corner = ((card["cell"] % 4) * 512, (card["cell"] // 4) * 512)
        img.paste(Image.open(build / card["file"]).convert("RGBA").resize((512, 512), Image.Resampling.LANCZOS), corner)
        if "normal" in card:
            normals.paste(Image.open(build / card["normal"]).convert("RGB").resize((512, 512), Image.Resampling.LANCZOS),
                          corner)
    img.save(color)
    normals.save(normal)
