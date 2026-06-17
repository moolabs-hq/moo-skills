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


def test_env_supplies_full_config_for_containers(tmp_path):
    # A Fargate/Lambda container has no config file — every field comes from env
    # (MCB_*). The secret is NOT here (Secrets Manager → MOOLABS_API_KEY).
    cfg = load_config(config_dir=tmp_path, env={
        "MCB_BUCKET": "cur-bucket",
        "MCB_PREFIX": "cur2",
        "MCB_REPORT_NAME": "moolabs-cur2",
        "MCB_REGION": "us-west-2",
        "MCB_ACUTE_BASE": "https://acute.dev.moolabs.com",
        "MCB_REPORTING_CURRENCY": "EUR",
    })
    assert cfg.bucket == "cur-bucket"
    assert cfg.prefix == "cur2"
    assert cfg.report_name == "moolabs-cur2"
    assert cfg.region == "us-west-2"
    assert cfg.acute_base == "https://acute.dev.moolabs.com"
    assert cfg.reporting_currency == "EUR"


def test_mcb_env_overrides_file_and_cli_still_wins(tmp_path):
    save_config(Config(bucket="from-file", acute_base="https://from-file"), config_dir=tmp_path)
    cfg = load_config(
        config_dir=tmp_path,
        env={"MCB_BUCKET": "from-env", "MCB_ACUTE_BASE": "https://env"},
        overrides={"acute_base": "https://from-cli"},
    )
    assert cfg.bucket == "from-env"            # env beats file
    assert cfg.acute_base == "https://from-cli"  # cli beats env


def test_legacy_acute_base_env_still_honored(tmp_path):
    # Back-compat: the original bare ACUTE_BASE env var still works.
    cfg = load_config(config_dir=tmp_path, env={"ACUTE_BASE": "https://legacy"})
    assert cfg.acute_base == "https://legacy"


def test_save_config_has_no_secret_field(tmp_path):
    path = save_config(Config(bucket="b"), config_dir=tmp_path)
    text = path.read_text()
    assert "MOOLABS_API_KEY" not in text
    assert "api_key" not in text.lower()
