import pytest

from moo_cloud_bill.config import (
    DEFAULT_ACUTE_BASE,
    Config,
    acute_base_from_domain,
    load_config,
    save_config,
)


@pytest.mark.parametrize("domain,expected", [
    ("dev.moolabs.com", "https://acute.dev.moolabs.com"),
    ("https://dev.moolabs.com/", "https://acute.dev.moolabs.com"),
    ("acute.dev.moolabs.com", "https://acute.dev.moolabs.com"),  # already acute.
    ("moolabs.com", "https://acute.moolabs.com"),
])
def test_acute_base_from_domain(domain, expected):
    assert acute_base_from_domain(domain) == expected


def test_acute_base_from_blank_domain_falls_back():
    assert acute_base_from_domain("  ") == DEFAULT_ACUTE_BASE


def test_defaults_when_no_file(tmp_path):
    cfg = load_config(config_dir=tmp_path, env={})
    assert cfg.acute_base == DEFAULT_ACUTE_BASE
    assert cfg.region == "us-east-1"
    assert cfg.bucket is None


def test_round_trip_save_load(tmp_path):
    cfg = Config(bucket="b", prefix="cost/hourly", report_name="r", reporting_currency="EUR")
    save_config(cfg, config_dir=tmp_path)
    loaded = load_config(config_dir=tmp_path, env={})
    assert loaded.bucket == "b"
    assert loaded.prefix == "cost/hourly"
    assert loaded.reporting_currency == "EUR"


def test_env_overrides_file(tmp_path):
    save_config(Config(acute_base="https://from-file"), config_dir=tmp_path)
    cfg = load_config(config_dir=tmp_path, env={"ACUTE_BASE": "https://from-env"})
    assert cfg.acute_base == "https://from-env"


def test_cli_override_wins(tmp_path):
    cfg = load_config(
        config_dir=tmp_path,
        env={"ACUTE_BASE": "https://from-env"},
        overrides={"acute_base": "https://from-cli"},
    )
    assert cfg.acute_base == "https://from-cli"


def test_save_config_has_no_secret_field(tmp_path):
    path = save_config(Config(bucket="b"), config_dir=tmp_path)
    text = path.read_text()
    assert "MOOLABS_API_KEY" not in text
    assert "api_key" not in text.lower()
