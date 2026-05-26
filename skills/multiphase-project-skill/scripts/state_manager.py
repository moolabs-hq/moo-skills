#!/usr/bin/env python3
"""State manager for multiphase-project-skill.

Reads, writes, and validates ``.multiphase-project/state.yaml`` — the single
source of truth for a project's position in the brainstorm -> PRD -> ralph
-> review pipeline.

The orchestrator never writes ``state.yaml`` directly; it always shells out
to this script. That keeps validation and transition rules in one place.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore[import-not-found]
except ImportError:
    sys.stderr.write(
        "ERROR: PyYAML is required. Install with: pip install pyyaml\n"
    )
    sys.exit(2)

STATE_DIR = Path(".multiphase-project")
STATE_FILE = STATE_DIR / "state.yaml"

VALID_PROJECT_STATUS = {"new", "in_progress", "complete", "aborted"}

VALID_PHASE_STATUS = {
    "pending",
    "decomposing",
    "decomposed",
    "grooming",
    "groomed",
    "developing",
    "developed",
    "reviewing",
    "complete",
    "blocked",
}

VALID_WORK_STATUS = {"pending", "in_progress", "complete", "skipped"}


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        raise FileNotFoundError(
            f"No state file at {STATE_FILE}. Run 'init' first."
        )
    with STATE_FILE.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    validate_state(data)
    return data


def save_state(state: dict[str, Any]) -> None:
    validate_state(state)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state["project"]["updated_at"] = _now()
    with STATE_FILE.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(state, fh, sort_keys=False)


def validate_state(state: dict[str, Any]) -> None:
    if "project" not in state:
        raise ValueError("state missing 'project' root")
    project = state["project"]
    for required in ("name", "status", "current_phase_index"):
        if required not in project:
            raise ValueError(f"project missing '{required}'")
    if project["status"] not in VALID_PROJECT_STATUS:
        raise ValueError(
            f"invalid project.status: {project['status']!r} "
            f"(must be one of {sorted(VALID_PROJECT_STATUS)})"
        )
    if not isinstance(project["current_phase_index"], int):
        raise ValueError("project.current_phase_index must be an int")
    if project["current_phase_index"] < 0:
        raise ValueError("project.current_phase_index must be >= 0")

    phases = state.get("phases", []) or []
    seen_phase_ids: set[str] = set()
    for index, phase in enumerate(phases):
        for required in ("id", "name", "status"):
            if required not in phase:
                raise ValueError(f"phase {index} missing '{required}'")
        if phase["id"] in seen_phase_ids:
            raise ValueError(f"duplicate phase id: {phase['id']!r}")
        seen_phase_ids.add(phase["id"])
        if phase["status"] not in VALID_PHASE_STATUS:
            raise ValueError(
                f"invalid status on phase {phase['id']!r}: "
                f"{phase['status']!r} (must be one of {sorted(VALID_PHASE_STATUS)})"
            )

        works = phase.get("works", []) or []
        seen_work_ids: set[str] = set()
        for work_index, work in enumerate(works):
            for required in ("id", "status"):
                if required not in work:
                    raise ValueError(
                        f"work {work_index} in phase {phase['id']!r} missing "
                        f"'{required}'"
                    )
            if work["id"] in seen_work_ids:
                raise ValueError(
                    f"duplicate work id in phase {phase['id']!r}: {work['id']!r}"
                )
            seen_work_ids.add(work["id"])
            if work["status"] not in VALID_WORK_STATUS:
                raise ValueError(
                    f"invalid status on work {phase['id']}.{work['id']}: "
                    f"{work['status']!r} (must be one of {sorted(VALID_WORK_STATUS)})"
                )


def _find_phase(state: dict[str, Any], phase_id: str) -> dict[str, Any]:
    for phase in state.get("phases", []) or []:
        if phase["id"] == phase_id:
            return phase
    raise ValueError(f"phase {phase_id!r} not found")


def cmd_init(args: argparse.Namespace) -> int:
    if STATE_FILE.exists() and not args.force:
        sys.stderr.write(
            f"State file already exists at {STATE_FILE}. Use --force to overwrite.\n"
        )
        return 1
    state = {
        "project": {
            "name": args.name,
            "description": args.description,
            "status": "new",
            "current_phase_index": 0,
            "created_at": _now(),
            "updated_at": _now(),
        },
        "phases": [],
    }
    save_state(state)
    print(f"Initialized {STATE_FILE}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    state = load_state()
    if args.json:
        print(json.dumps(state, indent=2, default=str))
        return 0
    project = state["project"]
    phases = state.get("phases", []) or []
    print(f"Project: {project['name']}")
    print(f"Status:  {project['status']}")
    if phases:
        print(
            f"Phase:   {project['current_phase_index'] + 1}/{len(phases)} "
            f"(index {project['current_phase_index']})"
        )
    else:
        print("Phase:   (no phases yet)")
    print("")
    for index, phase in enumerate(phases):
        marker = "->" if index == project["current_phase_index"] else "  "
        works = phase.get("works", []) or []
        done = sum(1 for work in works if work.get("status") == "complete")
        print(
            f"  {marker} [{phase['status']:>11}] {phase['id']:<24} "
            f"({done}/{len(works)} works) {phase['name']}"
        )
    return 0


def cmd_add_phase(args: argparse.Namespace) -> int:
    state = load_state()
    phases = state.setdefault("phases", [])
    if any(phase["id"] == args.id for phase in phases):
        sys.stderr.write(f"phase {args.id!r} already exists\n")
        return 1
    phases.append(
        {
            "id": args.id,
            "name": args.name,
            "description": args.description or "",
            "status": "pending",
            "works": [],
            "artifacts": {},
            "created_at": _now(),
        }
    )
    if state["project"]["status"] == "new":
        state["project"]["status"] = "in_progress"
    save_state(state)
    print(f"Added phase {args.id!r}")
    return 0


def cmd_add_work(args: argparse.Namespace) -> int:
    state = load_state()
    phase = _find_phase(state, args.phase_id)
    works = phase.setdefault("works", [])
    if any(work["id"] == args.id for work in works):
        sys.stderr.write(
            f"work {args.id!r} already exists in phase {args.phase_id!r}\n"
        )
        return 1
    works.append(
        {
            "id": args.id,
            "description": args.description,
            "status": "pending",
            "acceptance_criteria": args.acceptance_criteria or "",
        }
    )
    save_state(state)
    print(f"Added work {args.id!r} to phase {args.phase_id!r}")
    return 0


def cmd_set_phase_status(args: argparse.Namespace) -> int:
    state = load_state()
    phase = _find_phase(state, args.phase_id)
    phase["status"] = args.status
    if args.status == "complete":
        phase["completed_at"] = _now()
    save_state(state)
    print(f"phase {args.phase_id!r} -> {args.status}")
    return 0


def cmd_mark_work_done(args: argparse.Namespace) -> int:
    state = load_state()
    phase = _find_phase(state, args.phase_id)
    for work in phase.get("works", []) or []:
        if work["id"] == args.work_id:
            work["status"] = "complete"
            work["completed_at"] = _now()
            save_state(state)
            print(
                f"work {args.work_id!r} in phase {args.phase_id!r} -> complete"
            )
            return 0
    sys.stderr.write(
        f"work {args.work_id!r} not found in phase {args.phase_id!r}\n"
    )
    return 1


def cmd_advance_phase(args: argparse.Namespace) -> int:
    state = load_state()
    phases = state.get("phases", []) or []
    project = state["project"]
    index = project["current_phase_index"]
    if index >= len(phases):
        sys.stderr.write("already past the last phase\n")
        return 1
    current = phases[index]
    pending = [
        work
        for work in current.get("works", []) or []
        if work.get("status") not in {"complete", "skipped"}
    ]
    if pending and not args.force:
        sys.stderr.write(
            f"refusing to advance: phase {current['id']!r} has "
            f"{len(pending)} incomplete works "
            f"({', '.join(work['id'] for work in pending)}). "
            "Use --force to override.\n"
        )
        return 2
    current["status"] = "complete"
    current["completed_at"] = _now()
    project["current_phase_index"] = index + 1
    if project["current_phase_index"] >= len(phases):
        project["status"] = "complete"
        project["completed_at"] = _now()
    save_state(state)
    print(
        f"advanced past phase {current['id']!r}. "
        f"now at index {project['current_phase_index']}/{len(phases)}"
    )
    return 0


def cmd_set_artifact(args: argparse.Namespace) -> int:
    state = load_state()
    phase = _find_phase(state, args.phase_id)
    phase.setdefault("artifacts", {})[args.key] = args.path
    save_state(state)
    print(f"phase {args.phase_id!r} artifact {args.key!r} -> {args.path}")
    return 0


def cmd_set_error(args: argparse.Namespace) -> int:
    state = load_state()
    phase = _find_phase(state, args.phase_id)
    phase["error"] = args.message
    phase["status"] = "blocked"
    save_state(state)
    print(f"phase {args.phase_id!r} -> blocked: {args.message}")
    return 0


def cmd_clear_error(args: argparse.Namespace) -> int:
    state = load_state()
    phase = _find_phase(state, args.phase_id)
    phase.pop("error", None)
    if phase["status"] == "blocked":
        phase["status"] = "pending"
    save_state(state)
    print(f"phase {args.phase_id!r} error cleared")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="state_manager", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="create a new state file")
    p.add_argument("name")
    p.add_argument("description", nargs="?", default="")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("status", help="show current state")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("add-phase", help="append a phase")
    p.add_argument("id")
    p.add_argument("name")
    p.add_argument("--description", default="")
    p.set_defaults(func=cmd_add_phase)

    p = sub.add_parser("add-work", help="append a work to a phase")
    p.add_argument("phase_id")
    p.add_argument("id")
    p.add_argument("description")
    p.add_argument("--acceptance-criteria", default="")
    p.set_defaults(func=cmd_add_work)

    p = sub.add_parser("set-phase-status", help="set a phase status")
    p.add_argument("phase_id")
    p.add_argument("status", choices=sorted(VALID_PHASE_STATUS))
    p.set_defaults(func=cmd_set_phase_status)

    p = sub.add_parser("mark-work-done", help="mark a work complete")
    p.add_argument("phase_id")
    p.add_argument("work_id")
    p.set_defaults(func=cmd_mark_work_done)

    p = sub.add_parser(
        "advance-phase",
        help="mark current phase complete and advance the index",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="advance even if some works are pending",
    )
    p.set_defaults(func=cmd_advance_phase)

    p = sub.add_parser("set-artifact", help="record an artifact path on a phase")
    p.add_argument("phase_id")
    p.add_argument("key")
    p.add_argument("path")
    p.set_defaults(func=cmd_set_artifact)

    p = sub.add_parser("set-error", help="mark a phase blocked with a message")
    p.add_argument("phase_id")
    p.add_argument("message")
    p.set_defaults(func=cmd_set_error)

    p = sub.add_parser("clear-error", help="clear a blocked phase error")
    p.add_argument("phase_id")
    p.set_defaults(func=cmd_clear_error)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError) as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
