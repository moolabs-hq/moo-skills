"""Tests for credential capture, persistence, and resolution.

Security-critical: the Moolabs API key must land on disk 0600, resolve by a
documented precedence (env > file), and never appear unmasked in any output.
"""
import os
import stat

import pytest

from moo_cloud_bill import credentials as cr


# ── masking ───────────────────────────────────────────────────────────────
def test_mask_key_shows_prefix_only():
    assert cr.mask_key("mlk_abcdef123456") == "mlk_…****"


def test_mask_key_short_is_fully_hidden():
    assert cr.mask_key("abc") == "****"
    assert cr.mask_key("") == "****"


def test_mask_key_never_contains_the_secret_body():
    key = "mlk_supersecretvalue"
    masked = cr.mask_key(key)
    assert "supersecret" not in masked
    assert "value" not in masked


# ── persistence ───────────────────────────────────────────────────────────
def test_persist_key_writes_0600_file(tmp_path):
    path = cr.persist_key("mlk_abc123", config_dir=tmp_path)
    assert path.exists()
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


def test_persist_key_dir_is_0700(tmp_path):
    cfg = tmp_path / "moo-cloud-bill"
    cr.persist_key("mlk_abc123", config_dir=cfg)
    mode = stat.S_IMODE(os.stat(cfg).st_mode)
    assert mode == 0o700, f"expected 0700, got {oct(mode)}"


def test_persist_key_file_content_is_env_format(tmp_path):
    path = cr.persist_key("mlk_abc123", config_dir=tmp_path)
    assert path.read_text() == "MOOLABS_API_KEY=mlk_abc123\n"


def test_persist_key_rejects_empty(tmp_path):
    with pytest.raises(ValueError):
        cr.persist_key("   ", config_dir=tmp_path)


# ── resolution precedence ─────────────────────────────────────────────────
def test_resolve_prefers_env_over_file(tmp_path):
    cr.persist_key("mlk_fromfile", config_dir=tmp_path)
    got = cr.resolve_key(env={"MOOLABS_API_KEY": "mlk_fromenv"}, config_dir=tmp_path)
    assert got == "mlk_fromenv"


def test_resolve_falls_back_to_file(tmp_path):
    cr.persist_key("mlk_fromfile", config_dir=tmp_path)
    got = cr.resolve_key(env={}, config_dir=tmp_path)
    assert got == "mlk_fromfile"


def test_resolve_returns_none_when_absent(tmp_path):
    assert cr.resolve_key(env={}, config_dir=tmp_path) is None


def test_resolve_ignores_blank_env(tmp_path):
    cr.persist_key("mlk_fromfile", config_dir=tmp_path)
    got = cr.resolve_key(env={"MOOLABS_API_KEY": "   "}, config_dir=tmp_path)
    assert got == "mlk_fromfile"


def test_read_key_from_missing_file_is_none(tmp_path):
    assert cr.read_key_from_file(tmp_path / "nope" / "credentials") is None
