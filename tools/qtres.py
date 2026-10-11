"""
Qt 5 binary resource (RCC) reader / in-place writer.

The Walkman UI ships as one big ARM binary with its QML and images compiled in
via the Qt resource system. This module knows how to walk that structure so you
can read a resource, patch its bytes, and write it back *without changing the
file size* (which is what makes in-place patching of a fixed firmware slot safe).

Format notes (Qt 5.x, rcc format version 1)
-------------------------------------------
tree table   : array of 14-byte nodes, big-endian
    u32 name_offset      offset into the name table (0 for the root node)
    u16 flags            bit0 = compressed, bit1 = directory
    if directory:
        u32 child_count
        u32 child_offset (first child node index; children are contiguous)
    if file:
        u32 locale
        u32 data_offset  offset into the data table

name table   : entries are
    u16 length           number of UTF-16 code units
    u32 hash             (Qt's own hash; not needed to read)
    length * 2 bytes     UTF-16BE text

data table   : for each file
    u32 stored_size      length of the payload blob that follows
    payload              raw bytes, or a qCompress blob when flags bit0 is set

The compressed case is the trap. rcc uses Qt's `qCompress()`, and `qCompress`
prepends the *uncompressed* length as a 4-byte big-endian header:

    stored_size = 4 + len(zlib stream)
    payload     = u32 BE uncompressed_size | zlib stream

So a compressed payload reads as

    00 00 08 7e | 00 00 21 fe | 78 9c ...
    ^ stored    ^ uncompressed ^ zlib

Feeding the whole payload to zlib.decompress() fails with
"unknown compression method", because the first two bytes are `00 00` instead
of `78 9c`. You have to skip 4 bytes first. This single detail is the most
common reason a home-grown parser produces garbage.

`stored_size` doubles as the in-place slot size: the next resource starts
immediately after `data_offset + 4 + stored_size`, so a replacement must be no
larger than the original `stored_size`.
"""
from __future__ import annotations

import re
import struct
import zlib
from typing import Dict, List, Optional, Tuple

FLAG_COMPRESSED = 0x01
FLAG_DIRECTORY = 0x02
NODE_SIZE = 14

# ARM Thumb prologue of the compiler-generated qRegisterResourceData() wrapper.
# The wrapper loads three table pointers (tree/names/data) and calls the real
# function, which is how you find every embedded resource chunk in a stripped
# binary without any symbols.
_THUMB_WRAPPER = bytes.fromhex("0a4901200a4a0b4b80b500af")


class QtResourceError(RuntimeError):
    pass


class QtResources:
    """One resource chunk: a tree table, a name table and a data table."""

    def __init__(self, blob: bytes | bytearray, tree: int, names: int, data: int):
        self.d = bytearray(blob)
        self.tree = tree
        self.names = names
        self.data = data

    # ------------------------------------------------------------- primitives
    def name(self, offset: int) -> str:
        ln = struct.unpack_from(">H", self.d, self.names + offset)[0]
        raw = self.d[self.names + offset + 6:self.names + offset + 6 + ln * 2]
        return raw.decode("utf-16-be", "replace")

    def node(self, idx: int) -> Dict:
        pos = self.tree + idx * NODE_SIZE
        name_off = struct.unpack_from(">I", self.d, pos)[0]
        flags = struct.unpack_from(">H", self.d, pos + 4)[0]
        out = {"index": idx, "pos": pos, "name_offset": name_off, "flags": flags,
               "is_dir": bool(flags & FLAG_DIRECTORY),
               "compressed": bool(flags & FLAG_COMPRESSED)}
        if out["is_dir"]:
            out["child_count"] = struct.unpack_from(">I", self.d, pos + 6)[0]
            out["child_offset"] = struct.unpack_from(">I", self.d, pos + 10)[0]
        else:
            out["locale"] = struct.unpack_from(">I", self.d, pos + 6)[0]
            out["data_offset"] = struct.unpack_from(">I", self.d, pos + 10)[0]
        return out

    # ----------------------------------------------------------------- walking
    def walk(self, idx: int = 0, path: str = "") -> List[Tuple[str, int, int]]:
        """Depth-first walk. Yields (full_path, flags, data_offset) for files."""
        n = self.node(idx)
        name = "" if idx == 0 else self.name(n["name_offset"])
        full = (path + "/" + name).strip("/")
        if n["is_dir"]:
            out: List[Tuple[str, int, int]] = []
            for j in range(n["child_count"]):
                out.extend(self.walk(n["child_offset"] + j, full))
            return out
        return [(full, n["flags"], n["data_offset"])]

    def entries(self) -> List[Tuple[str, int, int]]:
        return self.walk(0)

    # -------------------------------------------------------------------- read
    def read(self, data_offset: int, flags: int) -> Tuple[bytes, bytes, int, int]:
        """Return (stored_payload, decoded_payload, payload_file_offset, slot_size)."""
        p = self.data + data_offset
        slot = struct.unpack_from(">I", self.d, p)[0]
        raw = bytes(self.d[p + 4:p + 4 + slot])
        if flags & FLAG_COMPRESSED:
            # qCompress blob: skip the 4-byte big-endian uncompressed-size header
            dec = zlib.decompress(raw[4:])
        else:
            dec = raw
        return raw, dec, p, slot

    def find(self, suffix: str) -> Optional[Tuple[str, int, int]]:
        for full, flags, off in self.entries():
            if full.endswith(suffix):
                return full, flags, off
        return None

    # ------------------------------------------------------------------- write
    def write(self, data_offset: int, flags: int, payload: bytes,
              pad: int = 0x00, compress_level: int = 9) -> int:
        """
        Replace a resource in place. The new payload must fit in the old slot.

        Returns the number of bytes written. Raises QtResourceError if it does
        not fit - it never silently truncates, because a truncated QML file is
        a syntax error that bricks the menu at runtime.
        """
        p = self.data + data_offset
        slot = struct.unpack_from(">I", self.d, p)[0]
        if flags & FLAG_COMPRESSED:
            # mirror qCompress(): u32 BE uncompressed size, then the zlib stream
            stored = struct.pack(">I", len(payload)) + zlib.compress(payload, compress_level)
        else:
            stored = payload
        if len(stored) > slot:
            raise QtResourceError(
                f"payload {len(stored)}B does not fit slot {slot}B "
                f"(over by {len(stored) - slot}B)")
        struct.pack_into(">I", self.d, p, len(stored))
        self.d[p + 4:p + 4 + len(stored)] = stored
        # zero (or space) fill the remainder so no stale bytes leak through
        fill = bytes([pad]) * (slot - len(stored))
        self.d[p + 4 + len(stored):p + 4 + slot] = fill
        return len(stored)

    def tobytes(self) -> bytes:
        return bytes(self.d)


def find_chunks(blob: bytes) -> List[Tuple[int, int, int]]:
    """
    Locate every embedded Qt resource table in a stripped ARM binary.

    Strategy: find the Thumb prologue of the compiler-generated
    qRegisterResourceData wrapper, then decode the three PC-relative literal
    loads that follow it. Each `add rX, pc, #imm` uses a different PC value, so
    the literal-to-add pairing has to be explicit rather than assumed.

    Returns a list of (tree, names, data) file offsets.
    """
    chunks: List[Tuple[int, int, int]] = []
    for m in re.finditer(re.escape(_THUMB_WRAPPER), blob):
        a = m.start()
        ptr: List[int] = []
        for ins_off, pc_delta in ((0, 0x10), (4, 0x12), (6, 0x14)):
            ins = struct.unpack_from("<H", blob, a + ins_off)[0]
            if (ins >> 12) != 4:        # not an ADD (imm)
                break
            # Thumb LDR (literal): PC is aligned to 4, base = insn_addr + 4
            literal = ((a + ins_off + 4) & ~3) + ((ins & 0xFF) * 4)
            rel = struct.unpack_from("<I", blob, literal)[0]
            ptr.append(a + pc_delta + rel)
        if len(ptr) == 3 and all(0 < p < len(blob) for p in ptr):
            if tuple(ptr) not in chunks:
                chunks.append(tuple(ptr))
    return chunks


def sanity_check(blob: bytes, tree: int, names: int, data: int,
                 max_nodes: int = 100000) -> bool:
    """Cheap plausibility test so random byte patterns do not look like chunks."""
    try:
        res = QtResources(blob, tree, names, data)
        root = res.node(0)
        if not root["is_dir"] or root["child_count"] > max_nodes:
            return False
        for j in range(min(root["child_count"], 64)):
            res.node(root["child_offset"] + j)
        return True
    except Exception:
        return False
