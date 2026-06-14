"""Non-secret config: a flat TOML file written by `configure`, read by `push`.

Secrets (the Moolabs key) NEVER live here — see credentials.py. Precedence for
the values exposed at runtime: CLI flags > env > toml file.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

from .credentials import default_config_dir

# Placeholder default — confirm per deployment (PRD OQ-9 / OQ-2-deploy).
DEFAULT_ACUTE_BASE = "https://api.moolabs.com"
CONFIG_FILENAME = "moo-cloud-bill.toml"


@dataclass(frozen=True)
class Config:
    bucket: str | None = None
    prefix: str | None = None
    report_name: str | None = None
    region: str = "us-east-1"
    acute_base: str = DEFAULT_ACUTE_BASE
    reporting_currency: str = "USD"
    aws_profile: str | None = None


def config_path(config_dir: Path | None = None) -> Path:
    config_dir = default_config_dir() if config_dir is None else Path(config_dir)
    return config_dir / CONFIG_FILENAME


def load_config(
    *,
    config_dir: Path | None = None,
    env: dict | None = None,
    overrides: dict | None = None,
) -> Config:
    """Build a Config from (toml file) then env overrides then CLI overrides."""
    env = {} if env is None else env
    overrides = {} if overrides is None else overrides

    data: dict = {}
    path = config_path(config_dir)
    if path.exists():
        with open(path, "rb") as fh:
            data = tomllib.load(fh)

    cfg = Config(
        bucket=data.get("bucket"),
        prefix=data.get("prefix"),
        report_name=data.get("report_name"),
        region=data.get("region", "us-east-1"),
        acute_base=data.get("acute_base", DEFAULT_ACUTE_BASE),
        reporting_currency=data.get("reporting_currency", "USD"),
        aws_profile=data.get("aws_profile"),
    )

    env_over = {}
    if env.get("ACUTE_BASE"):
        env_over["acute_base"] = env["ACUTE_BASE"]
    if env.get("AWS_PROFILE"):
        env_over["aws_profile"] = env["AWS_PROFILE"]
    cfg = replace(cfg, **env_over)

    clean_overrides = {k: v for k, v in overrides.items() if v is not None}
    if clean_overrides:
        cfg = replace(cfg, **clean_overrides)
    return cfg


def save_config(cfg: Config, *, config_dir: Path | None = None) -> Path:
    """Write a flat TOML config (no secrets). Returns the path."""
    config_dir = default_config_dir() if config_dir is None else Path(config_dir)
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / CONFIG_FILENAME
    lines = ["# moo-cloud-bill config — NO SECRETS HERE (see credentials file)\n"]
    for key in ("bucket", "prefix", "report_name", "region", "acute_base",
                "reporting_currency", "aws_profile"):
        value = getattr(cfg, key)
        if value is not None:
            lines.append(f'{key} = "{_toml_escape(str(value))}"\n')
    path.write_text("".join(lines))
    return path


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
