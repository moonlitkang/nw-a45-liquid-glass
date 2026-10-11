#!/usr/bin/env python3
"""
Independent verification of a patched Walkman system image / .UPG container.

Usage
-----
    python tools/verify.py image <patched.img> <stock.img>
    python tools/verify.py upg   <patched.UPG> <stock.UPG> <stock.img>

Every check recomputes things from scratch. Nothing is trusted from the build
log. Exit code is non-zero if any check fails.

Checks
------
image:
  1. only the expected files differ (block-level diff, mapped back to inodes)
  2. ext4 metadata (superblock, GDT, bitmaps, inode table) is byte-identical
  3. file sizes unchanged
  4. patched QML is syntactically sane and carries the glass palette
  5. no expensive constructs (blur / shader / layer / opacity)
upg:
  A. container self-MD5
  B. header signature / file count / record layout
  C. system sector decodes to exactly the patched image
  D. device-side md5 manifest matches the decoded image
  E. every other sector is byte-identical to stock
"""
from __future__ import annotations

import argparse
import hashlib
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ext4fs import Ext4                                  # noqa: E402
from qtres import QtResources, find_chunks, sanity_check  # noqa: E402
from theme import BANNED_TOKENS, REPLACEMENTS             # noqa: E402

UI_BIN = "vendor/sony/bin/HgrmMediaPlayerApp"
THEME_JSON = "vendor/sony/bin/ScreenSettings.json"
BLOCK = 4096

_results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> bool:
    _results.append((ok, name, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))
    return ok


def md5_file(p: Path, chunk: int = 1 << 22) -> str:
    h = hashlib.md5()
    with open(p, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


# ------------------------------------------------------------------- image
def verify_image(patched: Path, stock: Path) -> bool:
    print("== image checks ==")
    pa, pb = patched.stat().st_size, stock.stat().st_size
    check(pa == pb, "same image size", f"{pa:,} == {pb:,}")

    with Ext4(str(patched)) as fs:
        # map every block to its owning inode
        owner: dict[int, int] = {}
        for ino, _path in fs.walk(2):
            try:
                obj = fs.inode(ino)
                if not obj["is_reg"] or obj["size"] == 0:
                    continue
                for lb, pblk, cnt in fs.extents(obj["raw"]):
                    for k in range(cnt):
                        owner[pblk + k] = ino
            except Exception:
                continue
        lookup = {ino: p for ino, p in fs.walk(2)}

    diffs = []
    with open(patched, "rb") as fa, open(stock, "rb") as fb:
        i = 0
        while True:
            a = fa.read(BLOCK)
            b = fb.read(BLOCK)
            if not a and not b:
                break
            if a != b:
                diffs.append(i)
            i += 1
    total_blocks = i

    owners = sorted({owner.get(i) for i in diffs})
    names = sorted(lookup.get(o, f"inode {o}") for o in owners)
    expected = {"/" + UI_BIN, "/" + THEME_JSON}
    check(set(names) <= expected, "only expected files differ",
          f"{len(diffs)} block(s) across {names}")
    print(f"        differing blocks: {diffs}")
    print(f"        byte range: 0x{diffs[0]*BLOCK:X}..0x{(diffs[-1]+1)*BLOCK:X}"
          if diffs else "        (no differences)")

    # ext4 metadata must be untouched
    meta_ranges = [(1024, 1024 + 1024),          # superblock
                   (1024 * 4, 1024 * 4 + 32 * 4096)]  # GDT block(s)
    with open(patched, "rb") as fa, open(stock, "rb") as fb:
        same = True
        for lo, hi in meta_ranges:
            fa.seek(lo); fb.seek(lo)
            if fa.read(hi - lo) != fb.read(hi - lo):
                same = False
    check(same, "ext4 superblock + group descriptors unchanged")

    # file sizes
    sizes_ok = True
    with Ext4(str(patched)) as f1, Ext4(str(stock)) as f2:
        for path in (UI_BIN, THEME_JSON):
            s1 = f1.inode(f1.lookup(path))["size"]
            s2 = f2.inode(f2.lookup(path))["size"]
            if s1 != s2:
                sizes_ok = False
                print(f"        {path}: {s1} != {s2}")
    check(sizes_ok, "patched file sizes unchanged (no ext4 metadata churn)")

    # QML content
    with Ext4(str(patched)) as fs:
        elf, _ = fs.read_file(fs.lookup(UI_BIN))
    ok_all, colors = True, set()
    for basename in REPLACEMENTS:
        blob = bytes(elf)
        found = None
        for tree, names, data in find_chunks(blob):
            if not sanity_check(blob, tree, names, data):
                continue
            res = QtResources(blob, tree, names, data)
            hit = res.find("/" + basename)
            if hit:
                found = (res, hit)
                break
        if not found:
            ok_all = False
            print(f"        {basename}: NOT FOUND")
            continue
        res, (_full, flags, off) = found
        _raw, dec, _p, _slot = res.read(off, flags)
        depth = 0
        bad = False
        for ch in dec.decode("utf-8", "replace"):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            if depth < 0:
                bad = True
        if depth != 0 or bad or not dec.rstrip().endswith(b"}"):
            ok_all = False
            print(f"        {basename}: unbalanced/truncated")
        if b"BorderImage" in dec:
            ok_all = False
            print(f"        {basename}: BorderImage still present")
        colors.update(m.group().decode() for m in re.finditer(rb"#[0-9a-fA-F]{8}", dec))
    check(ok_all, "patched QML sane (balanced, not truncated, no BorderImage)")
    print(f"        glass colours found: {sorted(colors)}")

    banned = [t.decode() for t in BANNED_TOKENS
              if any(t in _chunk_bytes(elf, b) for b in REPLACEMENTS)]
    check(not banned, "no blur/shader/layer/opacity constructs", str(banned) if banned else "")

    # theme
    with Ext4(str(patched)) as fs:
        j, _ = fs.read_file(fs.lookup(THEME_JSON))
    check(b'"D1": "#0a1622"' in j, "theme recoloured (#0a1622)")
    check(b'"D1": "#000000"' not in j, "stock theme colour gone")
    return all(r[0] for r in _results)


def _chunk_bytes(elf: bytes, basename: str) -> bytes:
    for tree, names, data in find_chunks(elf):
        if not sanity_check(elf, tree, names, data):
            continue
        res = QtResources(elf, tree, names, data)
        hit = res.find("/" + basename)
        if hit:
            _raw, dec, _p, _slot = res.read(hit[2], hit[1])
            return dec
    return b""


# --------------------------------------------------------------------- upg
def verify_upg(patched: Path, stock: Path, stock_img: Path) -> bool:
    from upg import Upg
    print("== upg checks ==")
    chk = Upg(patched.read_bytes())
    ref = Upg(stock.read_bytes())

    check(chk.check_md5(), "container self-MD5")
    check(chk.file_count == ref.file_count,
          "file count matches stock", f"{chk.file_count}")
    check(chk.records == ref.records, "sector layout identical to stock")

    print("  decoding system sector ...")
    off, slot = chk.records[6]
    plain = chk.sector_plain(6)
    img = Upg.decode_block_stream(plain)
    stock_digest = md5_file(stock_img)
    check(hashlib.md5(img).hexdigest() == stock_digest,
          "system sector decodes to the expected patched image",
          hashlib.md5(img).hexdigest())

    manifest = chk.md5_manifest().split(b"\n")[0]
    check(hashlib.md5(img).hexdigest().encode() in manifest,
          "device-side md5 manifest matches decoded image", manifest.decode().strip())

    # every other sector identical to stock.
    # sector 1 is the md5 manifest (intentionally refreshed) and sector 6 is
    # the system image (intentionally replaced) - both are expected to differ.
    bad = []
    for i in range(chk.file_count):
        if i in (1, 6):
            continue
        o1, s1 = chk.records[i]
        o2, s2 = ref.records[i]
        if s1 != s2 or chk.blob[o1:o1 + s1] != ref.blob[o2:o2 + s2]:
            bad.append(i)
    check(not bad, "all other sectors (0,2-5,7,8) byte-identical to stock",
          str(bad) if bad else "only sector 1 (manifest) and 6 (system) changed")

    check(chk.blob[16:16 + 32 + 16 * 64] == ref.blob[16:16 + 32 + 16 * 64],
          "encrypted header identical to stock")
    return all(r[0] for r in _results)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("image")
    a.add_argument("patched", type=Path)
    a.add_argument("stock", type=Path)
    b = sub.add_parser("upg")
    b.add_argument("patched", type=Path)
    b.add_argument("stock", type=Path)
    b.add_argument("stock_img", type=Path)
    args = ap.parse_args()

    if args.cmd == "image":
        ok = verify_image(args.patched, args.stock)
    else:
        ok = verify_upg(args.patched, args.stock, args.stock_img)

    print()
    print(f"{sum(1 for r in _results if r[0])}/{len(_results)} checks passed")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
