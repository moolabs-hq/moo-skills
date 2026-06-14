from moo_cloud_bill.cli import build_parser, main


def test_parser_accepts_all_subcommands():
    parser = build_parser()
    for cmd in ["init", "detect", "configure", "push", "scan", "review", "seed"]:
        args = parser.parse_args([cmd] if cmd != "detect" else ["detect", "."])
        assert args.command == cmd


def test_detect_via_cli_returns_zero(tmp_path):
    (tmp_path / "app.py").write_text("import boto3\n")
    assert main(["detect", str(tmp_path)]) == 0


def test_push_without_key_returns_one(tmp_path, monkeypatch):
    monkeypatch.delenv("MOOLABS_API_KEY", raising=False)
    # empty config dir → no key on disk → must fail before any AWS call
    assert main(["--config", str(tmp_path), "push"]) == 1


def test_seed_without_key_returns_one(tmp_path, monkeypatch):
    monkeypatch.delenv("MOOLABS_API_KEY", raising=False)
    assert main(["--config", str(tmp_path), "seed"]) == 1
