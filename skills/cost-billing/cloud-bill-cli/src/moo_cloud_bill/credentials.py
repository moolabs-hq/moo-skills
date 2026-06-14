"""Moolabs API key capture, persistence, and resolution.

Security contract (PRD FR-6):
  - the key is generated in the Moolabs UI and captured via a hidden prompt
    (see commands/init.py);
  - persisted 0600 to ``~/.config/moo-cloud-bill/credentials`` (or a secrets
    manager in prod);
  - resolved at run time by precedence env > cred file (so cron `push` never
    prompts);
  - never a CLI arg, never written to the toml config, never logged unmasked.
"""
from __future__ import annotations

import os
from pathlib import Path

ENV_VAR = "MOOLABS_API_KEY"
CREDENTIALS_FILENAME = "credentials"


def default_config_dir(env: dict | None = None) -> Path:
    """User config dir; overridable via ``MOO_CLOUD_BILL_CONFIG_DIR`` (tests/prod)."""
    env = os.environ if env is None else env
    override = env.get("MOO_CLOUD_BILL_CONFIG_DIR")
    if override:
        return Path(override)
    return Path("~/.config/moo-cloud-bill").expanduser()


def mask_key(key: str | None) -> str:
    """Mask a key for display: ``mlk_…****``. Never reveals the secret body."""
    k = (key or "").strip()
    if len(k) <= 4:
        return "****"
    return f"{k[:4]}…****"


def persist_key(key: str, *, config_dir: Path) -> Path:
    """Write the key 0600 to ``<config_dir>/credentials`` (dir 0700). Returns the path.

    Uses ``os.open`` with an explicit mode so the file is created restricted
    regardless of the process umask.
    """
    value = (key or "").strip()
    if not value:
        raise ValueError("API key is empty")

    config_dir = Path(config_dir)
    config_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(config_dir, 0o700)

    path = config_dir / CREDENTIALS_FILENAME
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(f"{ENV_VAR}={value}\n")
    os.chmod(path, 0o600)
    return path


def read_key_from_file(path: Path) -> str | None:
    """Parse ``MOOLABS_API_KEY=...`` from a credentials file. None if absent."""
    try:
        text = Path(path).read_text()
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
        return None
    prefix = f"{ENV_VAR}="
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#") or not line:
            continue
        if line.startswith(prefix):
            value = line[len(prefix):].strip()
            return value or None
    return None


def resolve_key(*, env: dict | None = None, config_dir: Path | None = None) -> str | None:
    """Resolve the key by precedence: env > credentials file.

    Secrets Manager / SSM is a prod extension point (resolve before calling this).
    """
    env = os.environ if env is None else env
    from_env = env.get(ENV_VAR)
    if from_env and from_env.strip():
        return from_env.strip()
    if config_dir is not None:
        return read_key_from_file(Path(config_dir) / CREDENTIALS_FILENAME)
    return None
