#!/usr/bin/env python3
"""
Apply the Liquid Glass theme to a Walkman system image.

Usage
-----
    # patch the image only (safe, reversible: keep a copy of the original)
    python tools/patch_ui.py patch  <system.img> [--out patched.img]

    # patch and rebuild the .UPG container (needs key material, see config.py)
    python tools/patch_ui.py pack   <system.img> <original.UPG> <out.UPG>

Design rules this script enforces (learned the hard way - see docs/04):
  1. Every substitution must match. A missed match aborts; it never skips
     silently. A silently-skipped file is how you ship a half-themed UI.
  2. Every replacement must make the file *smaller* than the slot it lives in.
     The QML sits in a fixed-size slot inside the binary; growing it means the
     tail gets truncated, and a truncated QML file is a syntax error that
     stops the menu from opening at all.
  3. CJK comments are stripped before measuring, because they cost 2-3 bytes
     per character and are the cheapest source of headroom.
  4. Banned tokens (blur / shader / layer / opacity) abort the build.
  5. Read-back verification after every write.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ext4fs import Ext4                                    # noqa: E402
from minpng import flat_png                                # noqa: E402
from qtres import QtResources, find_chunks, sanity_check   # noqa: E402
from theme import (BANNED_TOKENS, REPLACEMENTS, THEME_BG_NEW,   # noqa: E402
                   THEME_BG_NEW_V, THEME_BG_OLD, THEME_BG_OLD_V,
                   TRANSPARENT_PNGS)

UI_BIN = "vendor/sony/bin/HgrmMediaPlayerApp"
THEME_JSON = "vendor/sony/bin/ScreenSettings.json"

# Known table offsets for the NW-A45 build. Left as a hint only - the script
# re-discovers them via find_chunks() so it also works on other builds.
KNOWN_TREE, KNOWN_NAMES, KNOWN_DATA = 0x548E88, 0x549820, 0x54B2F8


def strip_cjk_comments(text: str) -> str:
    """Drop trailing `// ...` comments that contain non-ASCII characters."""
    out = []
    for line in text.split("\n"):
        m = re.search(r"//.*", line)
        if m and re.search(r"[^\x00-\x7f]", m.group()):
            line = line[:m.start()].rstrip()
        out.append(line)
    return "\n".join(out)


def chunk_containing(blob: bytes, needle: bytes) -> tuple[int, int, int]:
    """Find the resource table that owns a given basename."""
    for tree, names, data in find_chunks(blob):
        if not sanity_check(blob, tree, names, data):
            continue
        try:
            res = QtResources(blob, tree, names, data)
            for full, _flags, _off in res.entries():
                if full.endswith("/" + needle.decode()) or full == needle.decode():
                    return tree, names, data
        except Exception:
            continue
    raise RuntimeError(f"no resource table contains {needle!r}")


# ---------------------------------------------------------------- QML patching
def patch_qml(blob: bytearray, report: list) -> int:
    """
    Apply every QML substitution, then copy each mutated chunk back into `blob`.

    Important: QtResources works on its own copy, so a chunk has to be written
    back into `blob` *before* anything re-reads it. Forgetting this makes all
    the edits vanish silently, which is exactly the class of bug this script is
    meant to prevent.
    """
    patched = 0
    for basename, subs in REPLACEMENTS.items():
        tree, names, data = chunk_containing(bytes(blob), basename.encode())
        res = QtResources(blob, tree, names, data)

        hit = res.find("/" + basename)
        if hit is None:
            raise RuntimeError(f"{basename}: not found in resource table")
        _full, flags, data_off = hit
        _raw, dec, _p, slot = res.read(data_off, flags)
        text = dec.decode("utf-8")

        for old, new in subs:
            if old not in text:
                raise RuntimeError(f"{basename}: substitution did not match:\n{old[:120]}")
            text = text.replace(old, new)

        before = len(text.encode("utf-8"))
        text = strip_cjk_comments(text)
        new_bytes = text.encode("utf-8")

        # Pad raw text with spaces, binary (compressed) payloads with NULs.
        # Spaces are the safe filler for anything a QML parser might scan past
        # the declared length; NULs would not be.
        pad = 0x00 if (flags & 1) else 0x20
        written = res.write(data_off, flags, new_bytes, pad=pad)
        blob[:] = res.tobytes()          # commit immediately
        report.append(f"  {basename:<22} {len(dec):>6}B -> {len(new_bytes):>6}B "
                      f"(-{before - len(new_bytes)}B comments, slot {slot}B, "
                      f"{'compressed' if flags & 1 else 'raw'}, stored {written}B)")
        patched += 1
    return patched


def verify_qml(blob: bytes) -> None:
    """Re-read every patched file and assert the result is sane."""
    seen_colors = set()
    for basename in REPLACEMENTS:
        tree, names, data = chunk_containing(blob, basename.encode())
        res = QtResources(blob, tree, names, data)
        full, flags, off = res.find("/" + basename)
        _raw, dec, _p, _slot = res.read(off, flags)
        text = dec.decode("utf-8")

        depth = 0
        for ch in text:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            if depth < 0:
                raise RuntimeError(f"{basename}: brace depth went negative")
        if depth != 0:
            raise RuntimeError(f"{basename}: unbalanced braces (depth {depth})")
        if not text.rstrip().endswith("}"):
            raise RuntimeError(f"{basename}: file looks truncated")
        if "BorderImage" in text:
            raise RuntimeError(f"{basename}: BorderImage still present")
        for bad in BANNED_TOKENS:
            if bad in dec:
                raise RuntimeError(f"{basename}: banned token {bad!r}")
        for m in re.finditer(rb"#[0-9a-fA-F]{8}", dec):
            seen_colors.add(m.group().decode())
    print(f"  QML verified: braces balanced, no BorderImage, no banned tokens")
    print(f"  glass colours present: {sorted(seen_colors)}")


# --------------------------------------------------------------- PNG patching
def patch_pngs(blob: bytearray, report: list) -> int:
    from PIL import Image
    import io

    done = 0
    for tree, names, data in find_chunks(bytes(blob)):
        if not sanity_check(bytes(blob), tree, names, data):
            continue
        res = QtResources(blob, tree, names, data)
        for full, flags, off in res.entries():
            base = full.split("/")[-1]
            if base not in TRANSPARENT_PNGS or (flags & 1):
                continue
            try:
                _raw, dec, p, slot = res.read(off, flags)
                w, h = Image.open(io.BytesIO(dec)).size
            except Exception:
                continue
            new_png = flat_png(w, h, TRANSPARENT_PNGS[base])
            if len(new_png) > slot:
                report.append(f"  SKIP {base}: {len(new_png)}B > slot {slot}B")
                continue
            struct.pack_into(">I", blob, p, len(new_png))
            blob[p + 4:p + 4 + len(new_png)] = new_png
            blob[p + 4 + len(new_png):p + 4 + slot] = b"\x00" * (slot - len(new_png))
            report.append(f"  {base:<34} {len(dec):>4}B -> {len(new_png):>4}B "
                          f"(slot {slot}B) {w}x{h}")
            done += 1
    return done


# ------------------------------------------------------------- theme patching
def patch_theme(image_path: Path, report: list) -> None:
    with Ext4(str(image_path), "r+b") as fs:
        ino = fs.lookup(THEME_JSON)
        data, exts = fs.read_file(ino)
        n_d1 = data.count(THEME_BG_OLD.encode())
        n_v1 = data.count(THEME_BG_OLD_V.encode())
        if n_d1 == 0:
            raise RuntimeError("theme: no bgcolor entries matched")
        new = data.replace(THEME_BG_OLD.encode(), THEME_BG_NEW.encode())
        new = new.replace(THEME_BG_OLD_V.encode(), THEME_BG_NEW_V.encode())
        if len(new) != len(data):
            raise RuntimeError("theme: length changed, refusing to write")
        fs.write_file(exts, new)
        back, _ = fs.read_file(ino)
        if back != new:
            raise RuntimeError("theme: read-back mismatch")
        report.append(f"  ScreenSettings.json  bgcolor.D1 x{n_d1}, V1 x{n_v1} "
                      f"#000000 -> #0a1622 ({len(new):,}B unchanged)")


# ---------------------------------------------------------------------- main
def patch_image(src: Path, dst: Path) -> None:
    report: list[str] = []
    print(f"[1/5] copying {src.name} -> {dst.name}")
    shutil.copy2(src, dst)

    print("[2/5] reading UI binary from ext4")
    with Ext4(str(dst), "r+b") as fs:
        ino = fs.lookup(UI_BIN)
        elf, exts = fs.read_file(ino)
        print(f"      {UI_BIN} inode={ino} size={len(elf):,}")

        print("[3/5] patching QML")
        blob = bytearray(elf)
        n = patch_qml(blob, report)
        for line in report:
            print(line)
        report.clear()

        print("[3b/5] patching background PNGs")
        m = patch_pngs(blob, report)
        for line in report:
            print(line)
        report.clear()
        print(f"      {m} PNG(s) replaced")

        print("[3c/5] verifying QML")
        verify_qml(bytes(blob))

        if len(blob) != len(elf):
            raise RuntimeError(f"binary size changed: {len(elf)} -> {len(blob)}")
        fs.write_file(exts, bytes(blob))
        back, _ = fs.read_file(ino)
        if back != bytes(blob):
            raise RuntimeError("binary read-back mismatch")
        print("      read-back OK (size unchanged)")

    print("[4/5] recolouring global theme")
    patch_theme(dst, report)
    for line in report:
        print(line)

    print("[5/5] done")
    print(f"      sha256(patched) = {hashlib.sha256(dst.read_bytes()).hexdigest()}")


def pack_upg(image: Path, original_upg: Path, out_upg: Path) -> None:
    from upg import Upg

    print("[pack 1/4] loading original container")
    upg = Upg(original_upg.read_bytes())
    print(f"      file_count={upg.file_count} md5_ok={upg.check_md5()}")

    print("[pack 2/4] replacing system sector")
    img = image.read_bytes()
    body = Upg.encode_block_stream(img)
    off, slot = upg.records[6]
    if len(body) > slot:
        raise RuntimeError(f"encoded system sector {len(body)}B exceeds slot {slot}B")
    plain = body + b"\x00" * (slot - len(body))
    upg.sector_encrypt(6, plain)
    print(f"      {len(img):,}B image -> {len(body):,}B encoded (slot {slot:,}B)")

    print("[pack 3/4] refreshing device-side md5 manifest (sector 1)")
    upg.set_md5_manifest_entry("system.img", hashlib.md5(img).hexdigest(), len(img))

    print("[pack 4/4] writing")
    digest = upg.save(str(out_upg))
    print(f"      {out_upg}  sha256={digest}")

    print("[verify] round-trip")
    chk = Upg(out_upg.read_bytes())
    decoded = Upg.decode_block_stream(chk.sector_plain(6)[:len(body)], len(img))
    if decoded != img:
        raise RuntimeError("round-trip mismatch")
    manifest = chk.md5_manifest().split(b"\n")[0]
    if hashlib.md5(img).hexdigest().encode() not in manifest:
        raise RuntimeError("manifest digest stale")
    print("      decoded == source image, manifest digest matches")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("patch", help="patch a system image only")
    p1.add_argument("image", type=Path)
    p1.add_argument("--out", type=Path, default=None)

    p2 = sub.add_parser("pack", help="patch and rebuild the .UPG container")
    p2.add_argument("image", type=Path)
    p2.add_argument("upg", type=Path)
    p2.add_argument("--out", type=Path, required=True)

    args = ap.parse_args()
    if args.cmd == "patch":
        dst = args.out or args.image.with_name(args.image.stem + "_glass.img")
        patch_image(args.image, dst)
    else:
        tmp = args.image.with_name(args.image.stem + "_glass.img")
        patch_image(args.image, tmp)
        pack_upg(tmp, args.upg, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
