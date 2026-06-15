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
from .errors import MooCloudBillError
from .cur_columns import load_column_map
from .ui import ConsoleUI
from .commands import configure as c_configure
from .commands import detect as c_detect
from .commands import init as c_init
from .commands import push as c_push
from .commands import review as c_review
from .commands import scan as c_scan
from .commands import seed as c_seed
from .commands import verify as c_verify

FINDINGS_FILE = "untagged-findings.yaml"
COLUMN_MAP_FILE = "cur-column-map.yaml"


def build_parser() -> argparse.ArgumentParser:
    # Global flags live on a parent parser added to BOTH the top level and every
    # subcommand, so `--dry-run push` AND `push --dry-run` both work. SUPPRESS
    # defaults mean a subparser never clobbers a value set at the top level.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS,
                        help="config dir (default ~/.config/moo-cloud-bill)")
    common.add_argument("--profile", default=argparse.SUPPRESS, help="AWS profile")
    common.add_argument("--acute-base", default=argparse.SUPPRESS, help="Acute base URL override")
    common.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS,
                        help="preview without mutating/sending")

    p = argparse.ArgumentParser(
        prog="moo-cloud-bill", description="AWS CUR → Moolabs Acute ingestion.", parents=[common]
    )
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", parents=[common], help="capture the Moolabs API key (hidden prompt)")
    d = sub.add_parser("detect", parents=[common], help="confirm AWS usage from code imports")
    d.add_argument("repo", nargs="?", default=".")
    sub.add_parser("configure", parents=[common], help="discover/reuse or create the CUR 2.0 export")
    sub.add_parser("push", parents=[common], help="read CUR → aggregate daily → POST to Acute")
    sub.add_parser("scan", parents=[common], help="write untagged-spend findings for review")
    r = sub.add_parser("review", parents=[common], help="interactive findings review")
    r.add_argument("--seed", action="store_true", help="seed approved rows immediately")
    sub.add_parser("seed", parents=[common], help="POST approved findings to resource_service_map")
    sub.add_parser("verify", parents=[common], help="check Acute connectivity/auth + CUR data readiness")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return _dispatch(args)
    except MooCloudBillError as exc:
        # ONLY intentional, operator-actionable domain errors (e.g. column-map
        # mismatch) get a clean message + non-zero exit. Programming bugs
        # (KeyError, AttributeError, …) still surface as tracebacks.
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # Common AWS auth/connection failures (expired SSO token, no creds) are
        # operator-actionable, not bugs — show a clean message, not a traceback.
        friendly = aws.as_friendly_error(exc)
        if friendly is None:
            raise
        print(f"error: {friendly}", file=sys.stderr)
        return 1


def _dispatch(args) -> int:
    # Global flags use SUPPRESS defaults (so subparsers don't clobber top-level
    # values), so they may be absent from the namespace — read them defensively.
    config_arg = getattr(args, "config", None)
    dry_run = getattr(args, "dry_run", False)
    config_dir = Path(config_arg) if config_arg else default_config_dir()
    overrides = {
        "aws_profile": getattr(args, "profile", None),
        "acute_base": getattr(args, "acute_base", None),
    }

    if args.command == "init":
        return c_init.run_init(config_dir=config_dir)

    if args.command == "detect":
        return c_detect.run_detect(args.repo)

    if args.command == "configure":
        cfg = load_config(config_dir=config_dir, env=os.environ, overrides=overrides)
        clients = aws.make_clients(profile=cfg.aws_profile, region=cfg.region)
        c_configure.run_configure(
            clients, ConsoleUI(), config_dir=config_dir,
            column_map_path=config_dir / COLUMN_MAP_FILE, aws_profile=cfg.aws_profile,
            dry_run=dry_run,
        )
        return 0

    if args.command == "push":
        cfg = load_config(config_dir=config_dir, env=os.environ, overrides=overrides)
        key = _require_key(config_dir)
        if key is None:
            return 1
        clients = aws.make_clients(profile=cfg.aws_profile, region=cfg.region)
        column_map = load_column_map(config_dir / COLUMN_MAP_FILE)
        return c_push.run_push(cfg, key, clients=clients, column_map=column_map, dry_run=dry_run)

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

    if args.command == "verify":
        cfg = load_config(config_dir=config_dir, env=os.environ, overrides=overrides)
        key = _require_key(config_dir)
        if key is None:
            return 1
        return c_verify.run_verify(cfg, key)

    return 2


def _require_key(config_dir: Path) -> str | None:
    key = resolve_key(env=os.environ, config_dir=config_dir)
    if not key:
        print("No Moolabs API key found — run `moo-cloud-bill init` first.", file=sys.stderr)
    return key


if __name__ == "__main__":
    sys.exit(main())
