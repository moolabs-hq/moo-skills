#!/usr/bin/env python3
"""Deterministic render driver for the instrument execution step (Phase 2d).

Reads `codemod/tasks.yaml` and renders EVERY shipped template referenced by
`env_wire_tasks` / `slugs_emit_tasks` into the customer repo — the stub Settings
module, the per-product slugs modules, and the deployment-surface stubs —
honoring each deployment stub's `mode` (new_file / append / checklist_only).

This replaces the execution agent HAND-AUTHORING these files (the dogfood
"template-bypass" process issue): emission is now deterministic and auditable,
driven by the shipped Jinja templates rather than equivalent-by-luck prose.

Out of scope (rendered elsewhere / no template):
  - the helper module `moolabs_client.*` — rendered in Phase 2.
  - `docker-compose.yml` / `Dockerfile` — no shipped template; recorded as a
    checklist item for the agent/developer to wire by hand.

Usage:
    python render_artifacts.py \\
        --tasks .moolabs/codemod/tasks.yaml \\
        --repo-root . \\
        [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path


# ──────────────────────────────────────────────────────────────────────
# Template registries (language- / kind-keyed)
# ──────────────────────────────────────────────────────────────────────

_STUB_TEMPLATES = {
    "python": "python-moolabs-settings.py.j2",
    "typescript": "typescript-moolabs-settings.ts.j2",
    "go": "go-moolabs-settings.go.j2",
}
_SLUGS_TEMPLATES = {
    "python": "slugs-python.j2",
    "typescript": "slugs-typescript.j2",
    "go": "slugs-go.j2",
}
# Deployment kinds that have a shipped template. Kinds NOT listed here
# (docker-compose, dockerfile) are recorded as checklist items — there is no
# template, so hand-authoring is the correct path for them.
_DEPLOY_TEMPLATES = {
    "terraform": "terraform-moolabs.tf.j2",
    "dotenv_example": "dotenv-moolabs.env.j2",
    "k8s": "k8s-secret-moolabs.yaml.j2",
    "k8s-secret": "k8s-secret-moolabs.yaml.j2",
}

# Per-language slugs module path convention (matches the Phase C spec +
# task_planner._slugs_import_path_for).
_SLUGS_DIR = {
    "python": "app/services/moolabs",
    "typescript": "src/services/moolabs",
    "go": "internal/moolabsclient",
}
_LANG_EXT = {"python": "py", "typescript": "ts", "go": "go"}

# Sentinel present in every deployment template — used to make `append`
# idempotent (don't re-append if the file is already wired).
_APPEND_SENTINEL = "MOOLABS_API_KEY"

# Marker present in every rendered template's header. Used by new_file to tell
# OUR previously-generated artifact (safe to overwrite/regenerate) apart from a
# hand-written customer file at the same path (must NOT be clobbered).
_GENERATED_MARKER = "/cost-billing-instrument"


@dataclass
class RenderJob:
    kind: str          # "stub" | "slugs" | "deployment"
    template: str | None  # template filename, or None for checklist-only
    dest: str          # repo-relative destination path ("" for checklist)
    mode: str          # "new_file" | "append" | "checklist_only"
    context: dict = field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────
# I/O + inference helpers
# ──────────────────────────────────────────────────────────────────────

def load_yaml(path: Path) -> dict:
    """Read a YAML file via PyYAML. Raises a clear error if PyYAML is absent —
    the driver cannot operate without it."""
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "render_artifacts requires PyYAML to parse tasks.yaml "
            "(pip install pyyaml)"
        ) from exc
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def infer_language(tasks: dict) -> str:
    """Infer the service's primary language. Prefers the per-file insert tasks'
    explicit `language` field (always present, even in modify mode), then falls
    back to the stub_emit_path extension, then python.

    The stub_emit_path-only inference was unreliable: a service in MODIFY mode
    has no stub_emit_path, so an all-modify TS/Go repo would default to python
    and render the wrong slugs template."""
    for t in tasks.get("tasks") or []:
        lang = t.get("language")
        if lang in ("python", "typescript", "go"):
            return lang
    for t in tasks.get("env_wire_tasks") or []:
        stub = t.get("stub_emit_path") or ""
        suffix = Path(stub).suffix.lower()
        if suffix == ".py":
            return "python"
        if suffix in (".ts", ".tsx"):
            return "typescript"
        if suffix == ".go":
            return "go"
    return "python"


def slugs_file_path(language: str, product_slug: str) -> str:
    """Per-product slugs module path (repo-relative). Hyphens in the product
    slug are underscored so the filename is a valid module identifier."""
    safe = (product_slug or "default").replace("-", "_")
    directory = _SLUGS_DIR.get(language, _SLUGS_DIR["python"])
    ext = _LANG_EXT.get(language, "py")
    return f"{directory}/slugs_{safe}.{ext}"


# ──────────────────────────────────────────────────────────────────────
# Planning
# ──────────────────────────────────────────────────────────────────────

def plan_render_jobs(tasks: dict, templates_dir: Path, repo_root: Path) -> list[RenderJob]:
    """Enumerate every render job referenced by the tasks. Pure planning — no
    rendering or I/O (other than `templates_dir` is informational here)."""
    language = infer_language(tasks)
    jobs: list[RenderJob] = []

    for t in tasks.get("env_wire_tasks") or []:
        service_slug = t.get("service_slug", "")
        # Stub Settings module (only when this service is in stub mode).
        if t.get("mode") == "stub" and t.get("stub_emit_path"):
            jobs.append(RenderJob(
                kind="stub",
                template=_STUB_TEMPLATES.get(language, _STUB_TEMPLATES["python"]),
                dest=t["stub_emit_path"],
                mode="new_file",
                context={
                    "service_slug": service_slug,
                    "settings_import_path": t.get("settings_import_path", ""),
                    "api_key_accessor": t.get("api_key_accessor", ""),
                    "kind": "stub",
                },
            ))
        # Deployment surfaces — honor each stub's declared mode.
        for ds in t.get("deployment_stubs") or []:
            kind = ds.get("kind", "")
            mode = ds.get("mode", "checklist_only")
            template = _DEPLOY_TEMPLATES.get(kind)
            # checklist_only, or a kind with no shipped template → record as a
            # checklist item (no render, no write).
            if mode == "checklist_only" or template is None:
                jobs.append(RenderJob(
                    kind="deployment", template=None,
                    dest=ds.get("emit_path", "") or ds.get("source_path", ""),
                    mode="checklist_only",
                    context={"kind": kind, "service_slug": service_slug,
                             "source_path": ds.get("source_path", "")},
                ))
                continue
            jobs.append(RenderJob(
                kind="deployment", template=template,
                dest=ds.get("emit_path", ""),
                mode=mode,
                context={"kind": kind, "service_slug": service_slug},
            ))

    for st in tasks.get("slugs_emit_tasks") or []:
        product = st.get("product_slug", "default")
        jobs.append(RenderJob(
            kind="slugs",
            template=_SLUGS_TEMPLATES.get(language, _SLUGS_TEMPLATES["python"]),
            dest=slugs_file_path(language, product),
            mode="new_file",
            context={
                "product_slug": product,
                "generated_at": st.get("generated_at", ""),
                "constants": st.get("constants") or {},
                "kind": "slugs",
            },
        ))

    return jobs


# ──────────────────────────────────────────────────────────────────────
# Rendering + writing
# ──────────────────────────────────────────────────────────────────────

def _jinja_env(templates_dir: Path):
    from jinja2 import Environment, FileSystemLoader
    return Environment(
        loader=FileSystemLoader(str(templates_dir)),
        keep_trailing_newline=True,
    )


def render_and_write(
    jobs: list[RenderJob], repo_root: Path, templates_dir: Path,
    dry_run: bool = False,
) -> list[dict]:
    """Render each job and write to the repo, honoring its mode. Returns a
    manifest (one dict per job) recording the action taken.

    Modes:
      - new_file: write the rendered template (overwrites — these are generated
        DO-NOT-EDIT artifacts).
      - append: append the rendered snippet to the existing file, but ONLY if
        the file does not already contain the MOOLABS_API_KEY sentinel
        (idempotent — re-runs do not duplicate). Creates the file if absent.
      - checklist_only: write nothing; record the surface for the PR checklist.
    """
    manifest: list[dict] = []
    env = None if dry_run else _jinja_env(templates_dir)

    for job in jobs:
        rec = {"kind": job.kind, "dest": job.dest,
               "template": job.template, "mode": job.mode}

        if job.template is None or job.mode == "checklist_only":
            rec["action"] = "would_checklist" if dry_run else "checklist"
            manifest.append(rec)
            continue

        if dry_run:
            rec["action"] = "would_append" if job.mode == "append" else "would_write"
            manifest.append(rec)
            continue

        rendered = env.get_template(job.template).render(**job.context)
        dest_path = repo_root / job.dest

        if job.mode == "append":
            existing = dest_path.read_text() if dest_path.is_file() else ""
            if _APPEND_SENTINEL in existing:
                rec["action"] = "append_skipped_present"
                manifest.append(rec)
                continue
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            sep = "" if (not existing or existing.endswith("\n")) else "\n"
            dest_path.write_text(existing + sep + rendered)
            rec["action"] = "appended"
            manifest.append(rec)
            continue

        # new_file — write, but NEVER clobber a hand-written customer file.
        # A pre-existing file is safe to overwrite ONLY if it carries our
        # generated marker (i.e. it's our own prior output being regenerated).
        # Otherwise the customer authored it → skip + record for the checklist.
        if dest_path.is_file():
            existing = dest_path.read_text()
            if _GENERATED_MARKER not in existing:
                sys.stderr.write(
                    f"render_artifacts: REFUSING to overwrite customer-authored "
                    f"{job.dest} (no generated marker) — wire MOOLABS_API_KEY by "
                    f"hand or remove the file to regenerate\n"
                )
                rec["action"] = "skipped_customer_file"
                manifest.append(rec)
                continue
            rec["action"] = "regenerated"
        else:
            rec["action"] = "wrote"
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_text(rendered)
        manifest.append(rec)

    return manifest


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tasks", default=".moolabs/codemod/tasks.yaml")
    ap.add_argument("--repo-root", default=".")
    ap.add_argument(
        "--templates-dir",
        default=str(Path(__file__).resolve().parent.parent
                    / "assets" / "codemod-templates"),
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    tasks = load_yaml(Path(args.tasks))
    if not tasks:
        print(f"render_artifacts: no tasks found at {args.tasks}", file=sys.stderr)
        return 1

    repo_root = Path(args.repo_root)
    templates_dir = Path(args.templates_dir)
    jobs = plan_render_jobs(tasks, templates_dir, repo_root)
    manifest = render_and_write(jobs, repo_root, templates_dir, dry_run=args.dry_run)

    for m in manifest:
        dest = m["dest"] or "(checklist)"
        print(f"  {m['action']:24} {m['kind']:11} {dest}")
    n_written = sum(1 for m in manifest if m["action"] in ("wrote", "appended"))
    n_checklist = sum(1 for m in manifest if m["action"] in ("checklist", "would_checklist"))
    print(f"render_artifacts: {len(manifest)} jobs, {n_written} written, "
          f"{n_checklist} checklist", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
