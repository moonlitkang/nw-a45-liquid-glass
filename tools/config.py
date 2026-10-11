"""
Configuration + key loading.

Paths are resolved relative to the repository root so nothing here is tied to
any particular machine. Crypto material is loaded from, in order:

  1. environment variables  WALKMAN_UPG_KAS / WALKMAN_UPG_PASSKEY / WALKMAN_UPG_IV
  2. keys.local.json in the repository root (git-ignored)
  3. keys.json in the repository root

If the keys are absent, everything except the .UPG container step still works
(see docs/01-upg-container.md for why they are not redistributed here).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict

REPO_ROOT = Path(__file__).resolve().parent.parent
KEYS_LOCAL = REPO_ROOT / "keys.local.json"
KEYS_SHIPPED = REPO_ROOT / "keys.json"

DEFAULT_WORK = REPO_ROOT / "work"


class ConfigError(RuntimeError):
    pass


def _from_file(path: Path) -> Dict[str, bytes] | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    try:
        return {
            "kas": bytes.fromhex(data["kas"]),
            "aes_passkey": data["aes_passkey"].encode("ascii"),
            "aes_iv": data["aes_iv"].encode("ascii"),
        }
    except (KeyError, ValueError) as e:
        raise ConfigError(f"{path.name}: {e}") from e


def load_keys() -> Dict[str, bytes]:
    env_kas = os.environ.get("WALKMAN_UPG_KAS")
    env_pass = os.environ.get("WALKMAN_UPG_PASSKEY")
    env_iv = os.environ.get("WALKMAN_UPG_IV")
    if env_kas and env_pass and env_iv:
        return {"kas": bytes.fromhex(env_kas),
                "aes_passkey": env_pass.encode("ascii"),
                "aes_iv": env_iv.encode("ascii")}
    for path in (KEYS_LOCAL, KEYS_SHIPPED):
        got = _from_file(path)
        if got:
            return got
    raise ConfigError(
        "UPG key material not found.\n"
        f"Create {KEYS_LOCAL.name} (or set WALKMAN_UPG_KAS / "
        "WALKMAN_UPG_PASSKEY / WALKMAN_UPG_IV).\n"
        "See keys.example.json and docs/01-upg-container.md."
    )


def work_dir() -> Path:
    d = Path(os.environ.get("WALKMAN_WORK", DEFAULT_WORK))
    d.mkdir(parents=True, exist_ok=True)
    return d
