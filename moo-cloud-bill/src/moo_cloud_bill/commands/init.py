"""`init` — capture the Moolabs API key (generated in the Moolabs UI) via a
hidden prompt and persist it 0600. The key is never echoed or logged unmasked.
"""
from __future__ import annotations

import getpass

from ..credentials import default_config_dir, mask_key, persist_key


def run_init(*, config_dir=None, env=None, prompt=getpass.getpass, out=print) -> int:
    config_dir = default_config_dir(env) if config_dir is None else config_dir
    key = (prompt("Paste your Moolabs API key (Moolabs UI → API Keys): ") or "").strip()
    if not key:
        out("No key entered — nothing saved.")
        return 1
    path = persist_key(key, config_dir=config_dir)
    out(f"Saved API key {mask_key(key)} to {path} (chmod 600).")
    return 0
