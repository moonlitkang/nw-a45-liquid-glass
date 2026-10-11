"""
Hand-rolled minimal PNG writer.

Pillow's encoder cannot get a fully transparent 480x158 image below ~226 bytes,
which does not fit the stock slot (226 bytes for `panel_controller_background.png`).
A 1-bit palette PNG with a single palette entry plus a tRNS chunk is ~112-150
bytes and always fits.

Only used for flat-colour backgrounds, which is all this theme needs.
"""
from __future__ import annotations

import struct
import zlib


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))


def flat_png(width: int, height: int, rgba: tuple[int, int, int, int],
             compress_level: int = 9) -> bytes:
    """
    1-bit palette PNG filled with a single colour.

    All pixels are index 0; the palette holds the RGB and tRNS holds the alpha,
    so one entry describes the whole image.
    """
    r, g, b, a = rgba
    ihdr = struct.pack(">IIBBBBB", width, height, 1, 3, 0, 0, 0)  # 1-bit, palette
    row_bytes = (width + 7) // 8
    raw = (b"\x00" + b"\x00" * row_bytes) * height                # filter byte 0
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", ihdr)
            + _chunk(b"PLTE", bytes((r, g, b)))
            + _chunk(b"tRNS", bytes((a,)))
            + _chunk(b"IDAT", zlib.compress(raw, compress_level))
            + _chunk(b"IEND", b""))
