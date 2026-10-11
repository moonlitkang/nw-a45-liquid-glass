"""
Sony Walkman firmware container (.UPG) - unpack / repack.

Container layout (NW-A40 / NW-A45 family, "NW_WM_FW.UPG")
---------------------------------------------------------
    [0x000 : 0x010]  plain MD5 of everything from 0x010 to EOF
    [0x010 : 0x0C0]  AES-128-ECB encrypted header
                       [0x00:0x10]  signature  (ASCII hex of the derived sig)
                       [0x10:0x14]  file_count (u32 LE)
                       [0x14:0x14+16*n]  n records of (offset u32 LE, size u32 LE)
    [0x0C0 : EOF]    AES-128-CBC encrypted payload area, one record per sector

Every sector is encrypted as a *single continuous CBC stream* with a fixed IV,
not per-block. Getting that wrong yields data that looks almost right for the
first 16 bytes and then turns to noise, which is a classic way to waste a day.

Sector 6 of the NW-A45 image is the ext4 `system` partition. Its plaintext is a
tightly packed stream of

    u32 LE compressed_size
    zlib stream -> exactly one 4096-byte ext4 block

repeated 204800 times (204800 * 4096 = 838860800 bytes). There is *no* padding
or alignment between records - also a classic trap, because the natural
assumption is 16-byte alignment to match the AES block size.

Key material
------------
The AES keys are derived from device-family key material and are deliberately
NOT shipped in this repository. Drop them into `keys.local.json` (git-ignored)
or export the environment variables; see `config.py`. Without them the
resource-level tooling still works - only the container pack/unpack step needs
them.
"""
from __future__ import annotations

import hashlib
import struct
import zlib
from typing import Dict, List, Tuple

from Crypto.Cipher import AES

from config import load_keys

SECTOR_ALIGN = 16
SYSTEM_BLOCK = 4096


class UpgError(RuntimeError):
    pass


class Upg:
    """A parsed .UPG container. Keeps the original bytes so repacking is safe."""

    def __init__(self, blob: bytes):
        self.blob = bytes(blob)
        keys = load_keys()
        ks = AES.new(keys["aes_passkey"], AES.MODE_CBC, keys["aes_iv"]).decrypt(keys["kas"])
        self.key = ks[:16]
        self.sig = ks[16:32]
        self.iv = keys["aes_iv"]
        self._parse()

    # ------------------------------------------------------------------ header
    def _parse(self) -> None:
        if len(self.blob) < 0xC0:
            raise UpgError("file too small to be a UPG container")
        self.body_md5 = self.blob[:16]
        hdr = AES.new(self.key, AES.MODE_ECB).decrypt(self.blob[16:16 + 32 + 16 * 64])
        if hdr[:16] != self.sig:
            raise UpgError("header signature mismatch - wrong key material?")
        self.file_count = struct.unpack_from("<I", hdr, 16)[0]
        if not 0 < self.file_count <= 64:
            raise UpgError(f"implausible file_count {self.file_count}")
        self.records: List[Tuple[int, int]] = [
            struct.unpack_from("<II", hdr, 32 + i * 16) for i in range(self.file_count)
        ]

    def check_md5(self) -> bool:
        return hashlib.md5(self.blob[16:]).digest() == self.body_md5

    # ----------------------------------------------------------------- sectors
    def sector_plain(self, index: int) -> bytes:
        off, size = self.records[index]
        return AES.new(self.key, AES.MODE_CBC, self.iv).decrypt(self.blob[off:off + size])

    def sector_encrypt(self, index: int, plaintext: bytes) -> None:
        off, size = self.records[index]
        if len(plaintext) != size:
            raise UpgError(f"sector {index}: plaintext must stay {size} bytes, got {len(plaintext)}")
        enc = AES.new(self.key, AES.MODE_CBC, self.iv).encrypt(plaintext)
        blob = bytearray(self.blob)
        blob[off:off + size] = enc
        self.blob = bytes(blob)

    def finalize_md5(self) -> None:
        self.blob = hashlib.md5(self.blob[16:]).digest() + self.blob[16:]
        self.body_md5 = self.blob[:16]

    # ----------------------------------------------------- ext4 sector codec
    @staticmethod
    def decode_block_stream(plain: bytes, expected_bytes: int | None = None) -> bytes:
        """Decode the packed [u32 LE size][zlib] block stream of the system sector."""
        out = bytearray()
        off = 0
        n = len(plain)
        while off + 4 <= n:
            size = struct.unpack_from("<I", plain, off)[0]
            if size == 0 or off + 4 + size > n:
                break
            try:
                out += zlib.decompress(plain[off + 4:off + 4 + size])
            except zlib.error as e:
                raise UpgError(f"zlib failure at offset {off}: {e}") from e
            off += 4 + size
        if expected_bytes is not None and len(out) != expected_bytes:
            raise UpgError(f"decoded {len(out)} bytes, expected {expected_bytes}")
        return bytes(out)

    @staticmethod
    def encode_block_stream(image: bytes, level: int = 9) -> bytes:
        """Inverse of decode_block_stream: 4 KiB blocks, tightly packed, then AES-padded."""
        parts = [struct.pack("<I", len(c)) + c for c in
                 (zlib.compress(image[o:o + SYSTEM_BLOCK], level)
                  for o in range(0, len(image), SYSTEM_BLOCK))]
        body = b"".join(parts)
        pad = (SECTOR_ALIGN - len(body) % SECTOR_ALIGN) % SECTOR_ALIGN
        return body + b"\x00" * pad

    # ------------------------------------------------------------- md5 manifest
    def md5_manifest(self) -> bytes:
        """Sector 1 is a plain-text manifest the device uses for write-verify."""
        return self.sector_plain(1)

    def set_md5_manifest_entry(self, name: str, digest: str, byte_count: int) -> None:
        """
        Rewrite the manifest line for `name` in place.

        The device runs roughly:
            grep <name> md5.txt && fwpup -z ...
        i.e. when a line is present it writes AND verifies. A stale digest here
        makes the flash abort part-way, so this must be refreshed whenever the
        corresponding image changes.
        """
        plain = bytearray(self.md5_manifest())
        line = f"{byte_count} {digest} {name}\n".encode()
        start = plain.find(name.encode())
        if start < 0:
            raise UpgError(f"{name} not present in md5 manifest")
        bol = plain.rfind(b"\n", 0, start) + 1
        eol = plain.find(b"\n", start) + 1
        old = bytes(plain[bol:eol])
        if len(old) != len(line):
            raise UpgError(f"manifest line length would change: {len(old)} -> {len(line)}")
        plain[bol:eol] = line
        self.sector_encrypt(1, bytes(plain))

    # ------------------------------------------------------------------ output
    def save(self, path: str) -> str:
        self.finalize_md5()
        with open(path, "wb") as f:
            f.write(self.blob)
        return hashlib.sha256(self.blob).hexdigest()

    def summary(self) -> Dict:
        return {"file_count": self.file_count,
                "size": len(self.blob),
                "md5_ok": self.check_md5(),
                "records": [{"offset": o, "size": s} for o, s in self.records]}
