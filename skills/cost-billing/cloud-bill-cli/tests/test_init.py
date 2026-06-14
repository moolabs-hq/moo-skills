from moo_cloud_bill import credentials as cr
from moo_cloud_bill.commands.init import run_init


def test_run_init_persists_and_masks(tmp_path):
    out = []
    rc = run_init(config_dir=tmp_path, prompt=lambda _: "mlk_secretvalue123", out=out.append)
    assert rc == 0
    assert cr.resolve_key(env={}, config_dir=tmp_path) == "mlk_secretvalue123"
    printed = "\n".join(out)
    assert "secretvalue123" not in printed  # never echoed unmasked
    assert "mlk_…****" in printed


def test_run_init_empty_key_aborts(tmp_path):
    out = []
    rc = run_init(config_dir=tmp_path, prompt=lambda _: "   ", out=out.append)
    assert rc == 1
    assert not (tmp_path / "credentials").exists()
