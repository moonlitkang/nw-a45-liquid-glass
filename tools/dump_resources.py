#!/usr/bin/env python3
"""
Dump every embedded Qt resource out of the Walkman UI binary.

Usage
-----
    python tools/dump_resources.py <system.img> [-o out_dir] [--bin <path>]

By default it reads `/vendor/sony/bin/HgrmMediaPlayerApp` straight out of the
ext4 image. With --bin you can point it at a binary you already extracted.

The output preserves the original qrc paths, e.g.
    out_dir/chunk_8/components/menu/OptionMenu.qml
    out_dir/chunk_0/assets/images/separator_option.png

Only reads - it never writes to the image.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ext4fs import Ext4                                  # noqa: E402
from qtres import QtResources, find_chunks, sanity_check  # noqa: E402

DEFAULT_BIN = "vendor/sony/bin/HgrmMediaPlayerApp"


def safe_name(part: str) -> str:
    bad = '<>:"|?*\\'
    out = "".join(("_" if c in bad or ord(c) < 0x20 else c) for c in part)
    return out.rstrip(" .") or "_"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image", help="ext4 system image")
    ap.add_argument("-o", "--out", default="work/resources")
    ap.add_argument("--bin", default=None, help="path to an already-extracted binary")
    args = ap.parse_args()

    if args.bin:
        blob = Path(args.bin).read_bytes()
    else:
        with Ext4(args.image) as fs:
            ino = fs.lookup(DEFAULT_BIN)
            blob, _ = fs.read_file(ino)
        print(f"read {DEFAULT_BIN}: {len(blob):,} bytes")

    out_root = Path(args.out)
    chunks = find_chunks(blob)
    print(f"candidate resource tables: {len(chunks)}")

    total_files = 0
    for idx, (tree, names, data) in enumerate(chunks):
        if not sanity_check(blob, tree, names, data):
            continue
        res = QtResources(blob, tree, names, data)
        n = 0
        for full, flags, off in res.entries():
            try:
                _raw, dec, _p, _sz = res.read(off, flags)
            except Exception:
                continue
            parts = [safe_name(p) for p in full.split("/") if p not in ("", ".")]
            dest = out_root / f"chunk_{idx}" / Path(*parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(dec)
            n += 1
        total_files += n
        print(f"  chunk_{idx}: tree=0x{tree:X} names=0x{names:X} data=0x{data:X}  {n} file(s)")

    print(f"wrote {total_files} resource(s) under {out_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
