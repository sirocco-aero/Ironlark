"""The DDS textures the world draws, as Webots' compressed-textures patch reads them: BC7, and BC1 where opaque.

Their own module so a cached result is keyed by the encoder's code alone (tools/stage_cache.py).
"""
import struct

import etcpak
import numpy as np
from PIL import Image


def write_bc7_dds(image, path):
    """DDS (DX10 header, BC7) with a box-filtered mip chain, as Webots would generate."""
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A").getextrema()[0] < 250
    levels, level = [], rgba
    while True:
        w, h = level.size
        block = level if w % 4 == 0 and h % 4 == 0 else level.resize((max(4, w), max(4, h)), Image.Resampling.BOX)
        levels.append(etcpak.compress_bc7(np.ascontiguousarray(np.asarray(block)).tobytes(), *block.size))
        if w == 1 and h == 1:
            break
        level = level.resize((max(1, w // 2), max(1, h // 2)), Image.Resampling.BOX)
    width, height = rgba.size
    header = struct.pack("<4s7I44x", b"DDS ", 124, 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | 0x80000, height, width,
                         len(levels[0]), 0, len(levels))
    header += struct.pack("<2I4s5I", 32, 0x4 | (0x1 if alpha else 0), b"DX10", 0, 0, 0, 0, 0)
    header += struct.pack("<5I", 0x1000 | 0x400000 | 0x8, 0, 0, 0, 0)
    header += struct.pack("<5I", 98, 3, 0, 1, 0)  # BC7_UNORM, 2D texture, one of it
    path.write_bytes(header + b"".join(levels))


def write_bc1_dds(image_path, dds_path):
    """A BC1 (opaque) DDS with a DX10 header and its mip levels."""
    image = Image.open(image_path).convert("RGBA")
    levels = []
    while min(image.size) >= 4:  # BC1 blocks are 4 x 4: the chain stops there
        levels.append(etcpak.compress_bc1(np.ascontiguousarray(np.asarray(image)).tobytes(), *image.size))
        image = image.resize((image.size[0] // 2, image.size[1] // 2), Image.Resampling.BOX)
    w, h = Image.open(image_path).size
    header = bytearray(148)
    header[0:4] = b"DDS "
    fields = {4: 124, 8: 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000, 12: h, 16: w, 20: len(levels[0]), 28: len(levels),
              76: 32, 80: 0x4, 108: 0x1000 | 0x400000 | 0x8, 128: 71, 132: 3, 140: 1}
    for offset, value in fields.items():
        header[offset:offset + 4] = int(value).to_bytes(4, "little")
    header[84:88] = b"DX10"
    dds_path.write_bytes(bytes(header) + b"".join(levels))
