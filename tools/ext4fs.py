"""
Minimal read/write ext4 driver, just enough to pull a file out of a Sony
Walkman system image and write it back in place.

Scope / limitations
-------------------
* ext4 only, no ext2/ext3 journal replay.
* Block sizes 1024..4096.
* Extent-mapped regular files only (no indirect-block files, no inline data).
* Writes are strictly in-place: the file length is never changed, so block
  bitmaps / inode bitmaps / the inode table itself are never touched.
  That is deliberate - it keeps the image's filesystem metadata byte-identical
  to the stock one, which is what makes the flash step safe.
* Read-only for everything else (directories are read, never modified).

Nothing here is specific to Sony; it is a generic little ext4 tool.
"""
from __future__ import annotations

import struct
from typing import BinaryIO, Dict, List, Optional, Tuple

EXT4_SUPER_MAGIC = 0xEF53
EXT4_EXTENTS_MAGIC = 0xF30A
S_IFMT = 0xF000
S_IFREG = 0x8000
S_IFDIR = 0x4000

INCOMPAT_64BIT = 0x80


class Ext4Error(RuntimeError):
    pass


class Superblock:
    __slots__ = ("block_size", "inodes_per_group", "inode_size", "incompat",
                 "inode_count", "desc_size", "gdt_start")

    def __init__(self, raw: bytes) -> None:
        if struct.unpack_from("<H", raw, 56)[0] != EXT4_SUPER_MAGIC:
            raise Ext4Error("not an ext4 superblock (bad magic)")
        self.inode_count = struct.unpack_from("<I", raw, 0)[0]
        self.block_size = 1024 << struct.unpack_from("<I", raw, 24)[0]
        self.inodes_per_group = struct.unpack_from("<I", raw, 40)[0]
        self.inode_size = struct.unpack_from("<H", raw, 88)[0] or 128
        self.incompat = struct.unpack_from("<I", raw, 96)[0]
        # s_desc_size is only meaningful when 64BIT is set
        dsz = struct.unpack_from("<H", raw, 254)[0] if self.incompat & INCOMPAT_64BIT else 0
        self.desc_size = max(32, dsz)
        # group descriptors live right after the (possibly 1024-byte) superblock
        self.gdt_start = (2 if self.block_size == 1024 else 1) * self.block_size

    @property
    def is_64bit(self) -> bool:
        return bool(self.incompat & INCOMPAT_64BIT)


class Ext4:
    """Open an ext4 image. Pass mode 'rb' for read-only, 'r+b' for in-place writes."""

    def __init__(self, path: str, mode: str = "rb") -> None:
        self.f: BinaryIO = open(path, mode)
        self.path = path
        self.ro = "w" not in mode and "+" not in mode
        self.f.seek(1024)
        self.sb = Superblock(self.f.read(1024))
        self.bs = self.sb.block_size

    # ---------------------------------------------------------------- helpers
    def _rd(self, off: int, n: int) -> bytes:
        self.f.seek(off)
        return self.f.read(n)

    def _block(self, n: int) -> bytes:
        return self._rd(n * self.bs, self.bs)

    def close(self) -> None:
        self.f.close()

    def __enter__(self) -> "Ext4":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------------------------------------------------------------- inodes
    def _inode_offset(self, num: int) -> int:
        g = (num - 1) // self.sb.inodes_per_group
        i = (num - 1) % self.sb.inodes_per_group
        gd = self._rd(self.sb.gdt_start + g * self.sb.desc_size, self.sb.desc_size)
        table = struct.unpack_from("<I", gd, 8)[0]
        if self.sb.is_64bit and self.sb.desc_size >= 64:
            table |= struct.unpack_from("<I", gd, 40)[0] << 32
        return table * self.bs + i * self.sb.inode_size

    def inode_raw(self, num: int) -> bytes:
        return self._rd(self._inode_offset(num), self.sb.inode_size)

    def inode(self, num: int) -> Dict:
        raw = self.inode_raw(num)
        mode = struct.unpack_from("<H", raw, 0)[0]
        size = struct.unpack_from("<I", raw, 4)[0]
        if self.sb.inode_size >= 112:
            size |= struct.unpack_from("<I", raw, 108)[0] << 32
        return {"num": num, "mode": mode, "size": size, "raw": raw,
                "is_dir": (mode & S_IFMT) == S_IFDIR,
                "is_reg": (mode & S_IFMT) == S_IFREG}

    # --------------------------------------------------------------- extents
    def extents(self, inode_raw: bytes) -> List[Tuple[int, int, int]]:
        """Return [(logical_block, physical_block, block_count), ...]."""
        def walk(buf: bytes) -> List[Tuple[int, int, int]]:
            magic, entries, _max, depth, _gen = struct.unpack_from("<HHHHI", buf, 0)
            if magic != EXT4_EXTENTS_MAGIC:
                return []
            out: List[Tuple[int, int, int]] = []
            for i in range(entries):
                p = 12 + i * 12
                if depth == 0:
                    lb, elen, hi, lo = struct.unpack_from("<IHHI", buf, p)
                    out.append((lb, (hi << 32) | lo, elen & 0x7FFF))
                else:
                    _lb, lo, hi, _u = struct.unpack_from("<IIHH", buf, p)
                    out.extend(walk(self._block(((hi << 32) | lo))))
            return out
        # i_block[60] holds the extent tree; the first 4 bytes are the header
        return walk(inode_raw[40:100])

    # ------------------------------------------------------------------ I/O
    def read_file(self, ino: int) -> Tuple[bytes, List[Tuple[int, int, int]]]:
        obj = self.inode(ino)
        if not obj["is_reg"]:
            raise Ext4Error(f"inode {ino} is not a regular file")
        exts = self.extents(obj["raw"])
        data = bytearray(obj["size"])
        for lb, pb, cnt in exts:
            off = lb * self.bs
            n = min(cnt * self.bs, max(0, len(data) - off))
            if n > 0:
                data[off:off + n] = self._rd(pb * self.bs, n)
        return bytes(data), exts

    def write_file(self, exts: List[Tuple[int, int, int]], data: bytes) -> int:
        """In-place write. `data` must not be longer than the allocated space."""
        if self.ro:
            raise Ext4Error("image opened read-only")
        total = sum(c * self.bs for _, _, c in exts)
        if len(data) > total:
            raise Ext4Error(f"payload {len(data)} exceeds allocated {total}")
        padded = data + b"\x00" * (total - len(data))
        off = 0
        for _lb, pb, cnt in exts:
            n = min(cnt * self.bs, len(padded) - off)
            if n > 0:
                self.f.seek(pb * self.bs)
                self.f.write(padded[off:off + n])
                off += n
        self.f.flush()
        return off

    # ------------------------------------------------------------ path lookup
    def _read_dir(self, ino: int) -> bytes:
        obj = self.inode(ino)
        data = bytearray(obj["size"])
        for lb, pb, cnt in self.extents(obj["raw"]):
            off = lb * self.bs
            n = min(cnt * self.bs, max(0, len(data) - off))
            if n > 0:
                data[off:off + n] = self._rd(pb * self.bs, n)
        return bytes(data)

    def listdir(self, ino: int) -> List[Tuple[int, str, int]]:
        """Return [(inode, name, file_type), ...] for a directory inode."""
        data = self._read_dir(ino)
        out = []
        p = 0
        while p + 8 <= len(data):
            ino, rec = struct.unpack_from("<IH", data, p)
            if rec < 8 or p + rec > len(data):
                break
            nl = data[p + 6]
            ftype = data[p + 7]
            name = data[p + 8:p + 8 + nl].decode("utf-8", "replace")
            if ino:
                out.append((ino, name, ftype))
            p += rec
        return out

    def lookup(self, path: str, root: int = 2) -> int:
        """Resolve an absolute-ish path like 'vendor/sony/bin/foo' to an inode."""
        cur = root
        for part in [p for p in path.replace("\\", "/").split("/") if p]:
            hit = None
            for ino, name, _t in self.listdir(cur):
                if name == part:
                    hit = ino
                    break
            if hit is None:
                raise FileNotFoundError(f"{part!r} not found while resolving {path!r}")
            cur = hit
        return cur

    def walk(self, root: int = 2, prefix: str = "", depth: int = 0,
             max_depth: int = 32) -> List[Tuple[int, str]]:
        """Recursively list (inode, full_path) for everything under `root`."""
        if depth > max_depth:
            return []
        out: List[Tuple[int, str]] = []
        try:
            entries = self.listdir(root)
        except Exception:
            return out
        for ino, name, ftype in entries:
            if name in (".", ".."):
                continue
            full = f"{prefix}/{name}"
            out.append((ino, full))
            if ftype == 2:  # directory
                out.extend(self.walk(ino, full, depth + 1, max_depth))
        return out
