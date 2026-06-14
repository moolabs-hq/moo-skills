"""argparse CLI dispatcher for moo-cloud-bill."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import aws
from .acute_client import AcuteClient
from .config import load_config
from .credentials import default_config_dir, resolve_key
from .cur_columns import load_column_map
from .ui import ConsoleUI
from .commands import configure as c_configure
from .commands import detect as c_detect
from .commands import init as c_init
from .commands import push as c_push
from .commands import review as c_review
from .commands import scan as c_scan
from .commands import seed as c_seed

FINDINGS_FILE = "untagged-findings.yaml"
COLUMN_MAP_FILE = "cur-column-map.yaml"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="moo-cloud-bill", description="AWS CUR → Moolabs Acute ingestion.")
    p.add_argument("--config", help="config dir (default ~/.config/moo-cloud-bill)")
    p.add_argument("--profile", help="AWS profile")
    p.add_argument("--acute-base", help="Acute base URL override")
    p.add_argument("--dry-run", action="store_true", help="preview without mutating/sending")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="capture the Moolabs API key (hidden prompt)")
    d = sub.add_parser("detect", help="confirm AWS usage from code imports")
    d.add_argument("repo", nargs="?", default=".")
    sub.add_parser("configure", help="discover/reuse or create the Legacy CUR")
    sub.add_parser("push", help="read CUR → aggregate daily → POST to Acute")
    sub.add_parser("scan", help="write untagged-spend findings for review")
    r = sub.add_parser("review", help="interactive findings review")
    r.add_argument("--seed", action="store_true", help="seed approved rows immediately")
    sub.add_parser("seed", help="POST approved findings to resource_service_map")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    config_dir = Path(args.config) if args.config else default_config_dir()
    overrides = {"aws_profile": args.profile, "acute_base": args.acute_base}

    if args.command == "init":
        return c_init.run_init(config_dir=config_dir)

    if args.command == "detect":
        return c_detect.run_detect(args.repo)

    if args.command == "configure":
        cfg = load_config(config_dir=config_dir, env=os.environ, overrides=overrides)
        clients = aws.make_clients(profile=cfg.aws_profile, region=cfg.region)
        c_configure.run_configure(
            clients, ConsoleUI(), config_dir=config_dir,
            column_map_path=config_dir / COLUMN_MAP_FILE, dry_run=args.dry_run,
        )
        return 0

    if args.command == "push":
        cfg = load_config(config_dir=config_dir, env=os.environ, overrides=overrides)
        key = _require_key(config_dir)
        if key is None:
            return 1
        clients = aws.make_clients(profile=cfg.aws_profile, region=cfg.region)
        column_map = load_column_map(config_dir / COLUMN_MAP_FILE)
        return c_push.run_push(cfg, key, clients=clients, column_map=column_map, dry_run=args.dry_run)

    if args.command == "scan":
        cfg = load_config(config_dir=config_dir, env=os.environ, overrides=overrides)
        clients = aws.make_clients(profile=cfg.aws_profile, region=cfg.region)
        column_map = load_column_map(config_dir / COLUMN_MAP_FILE)
        rows = c_push.read_cur_rows(cfg, clients)
        return c_scan.run_scan(rows, column_map, config_dir / FINDINGS_FILE)

    if args.command == "review":
        cfg = load_config(config_dir=config_dir, env=os.environ, overrides=overrides)
        seed_client = None
        if args.seed:
            key = _require_key(config_dir)
            if key is None:
                return 1
            seed_client = AcuteClient(cfg.acute_base, key)
        return c_review.run_review(config_dir / FINDINGS_FILE, ConsoleUI(), seed_client=seed_client)

    if args.command == "seed":
        cfg = load_config(config_dir=config_dir, env=os.environ, overrides=overrides)
        key = _require_key(config_dir)
        if key is None:
            return 1
        return c_seed.run_seed(config_dir / FINDINGS_FILE, AcuteClient(cfg.acute_base, key))

    return 2


def _require_key(config_dir: Path) -> str | None:
    key = resolve_key(env=os.environ, config_dir=config_dir)
    if not key:
        print("No Moolabs API key found — run `moo-cloud-bill init` first.", file=sys.stderr)
    return key


if __name__ == "__main__":
    sys.exit(main())
