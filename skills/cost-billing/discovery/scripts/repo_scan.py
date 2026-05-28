#!/usr/bin/env python3
"""Phase 1 — repo-shape + language detection for /cost-billing-discovery.

Produces .moolabs/discovery/repo-profile.yaml with detected services, languages,
frameworks, existing instrumentation, and existing Moolabs SDK presence.

Manifest-first detection per v1-decisions-log.md §6.4b #19c — falls back to
file-extension dominance only when no manifest is found.

Usage:
    python repo_scan.py <repo_path> [--output .moolabs/discovery/repo-profile.yaml]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


# ──────────────────────────────────────────────────────────────────────
# Manifest signatures
# ──────────────────────────────────────────────────────────────────────

MANIFESTS = {
    "python": ["pyproject.toml", "requirements.txt", "setup.py", "setup.cfg", "Pipfile"],
    "typescript": ["package.json", "tsconfig.json"],
    "javascript": ["package.json"],
    "go": ["go.mod", "go.sum"],
    "rust": ["Cargo.toml"],
    "java": ["pom.xml", "build.gradle", "build.gradle.kts"],
}

EXTENSION_MAP = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "java",
}

FRAMEWORK_SIGNATURES_PYTHON = {
    "fastapi": ["fastapi", "uvicorn", "starlette"],
    "django": ["django", "django-rest-framework", "djangorestframework"],
    "flask": ["flask", "flask-restful"],
    "tornado": ["tornado"],
    "litestar": ["litestar"],
    "aiohttp": ["aiohttp"],
}

FRAMEWORK_SIGNATURES_TS = {
    "express": ["express"],
    "nestjs": ["@nestjs/core", "@nestjs/common"],
    "nextjs": ["next"],
    "fastify": ["fastify"],
    "koa": ["koa"],
    "hono": ["hono"],
}

FRAMEWORK_SIGNATURES_GO = {
    "net-http-stdlib": [],
    "gin": ["github.com/gin-gonic/gin"],
    "echo": ["github.com/labstack/echo"],
    "fiber": ["github.com/gofiber/fiber"],
    "chi": ["github.com/go-chi/chi"],
}

# Execution-runtime signatures — non-HTTP entry-point libraries, keyed by the
# execution_context they imply (canonical vocabulary lives in
# shared/assets/execution-context.schema.yaml). These are a SERVICE-LEVEL HINT,
# not the gate: a service carrying one of these but no HTTP framework is still
# instrumentable. Per-call-site classification (W2 context classifier) is
# authoritative. argparse / threading / asyncio are stdlib (no dep to match) and
# are detected at the call-site layer, not here.
EXECUTION_RUNTIME_SIGNATURES_PYTHON = {
    "queue_worker":    ["celery", "rq", "dramatiq", "huey", "arq", "kombu"],
    "stream_consumer": ["confluent-kafka", "aiokafka", "kafka-python", "google-cloud-pubsub", "nats-py"],
    "scheduled_job":   ["apscheduler", "schedule"],
    "cli_batch":       ["click", "typer"],
}

EXECUTION_RUNTIME_SIGNATURES_TS = {
    "queue_worker":    ["bullmq", "bee-queue", "agenda", "@nestjs/microservices"],
    "stream_consumer": ["kafkajs", "@confluentinc/kafka-javascript", "node-rdkafka", "@google-cloud/pubsub"],
    "scheduled_job":   ["node-cron", "node-schedule", "@nestjs/schedule"],
    "cli_batch":       ["commander", "yargs", "oclif"],
}

EXECUTION_RUNTIME_SIGNATURES_GO = {
    "queue_worker":    ["github.com/hibiken/asynq", "github.com/RichardKnop/machinery"],
    "stream_consumer": ["github.com/segmentio/kafka-go", "github.com/IBM/sarama", "github.com/confluentinc/confluent-kafka-go"],
    "scheduled_job":   ["github.com/robfig/cron"],
}

EXISTING_INSTRUMENTATION_PACKAGES = {
    "opentelemetry-api": ["opentelemetry-api", "@opentelemetry/api"],
    "opentelemetry-sdk": ["opentelemetry-sdk", "@opentelemetry/sdk-node"],
    "openllmetry-sdk": ["openllmetry-sdk", "@traceloop/node-server-sdk"],
    "helicone": ["helicone", "@helicone/helicone"],
    "langfuse": ["langfuse"],
    "datadog": ["ddtrace", "dd-trace"],
    "sentry": ["sentry-sdk", "@sentry/node"],
}

MOOLABS_SDK_PACKAGES = ["moolabs", "@moolabs/sdk"]

IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    ".tox", "dist", "build", ".next", ".nuxt", "target", "vendor",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "coverage",
}


# ──────────────────────────────────────────────────────────────────────
# Data shapes
# ──────────────────────────────────────────────────────────────────────


@dataclass
class ServiceProfile:
    path: str
    languages: list[str] = field(default_factory=list)
    manifests: list[str] = field(default_factory=list)
    frameworks_detected: list[str] = field(default_factory=list)
    # Non-HTTP execution contexts detected at service level via dependency
    # signatures (subset of the execution_context enum). A HINT — the W2
    # call-site classifier is authoritative. Empty list = no worker/consumer/
    # scheduler/CLI library found (does NOT mean "not instrumentable").
    execution_runtimes: list[str] = field(default_factory=list)
    existing_instrumentation: list[str] = field(default_factory=list)
    existing_moolabs_sdk: Optional[str] = None


@dataclass
class RepoProfile:
    repo_type: str  # polyrepo | monorepo | microservices
    services: list[ServiceProfile] = field(default_factory=list)
    repo_root_languages: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────
# Detection
# ──────────────────────────────────────────────────────────────────────


def find_service_roots(repo: Path) -> list[Path]:
    """Locate every directory containing a manifest file.

    Strategy:
      - Walk the repo, skip ignored dirs.
      - A directory with any MANIFESTS file is a candidate service root.
      - Deduplicate parent/child collisions: prefer the deeper manifest only
        if the parent isn't also a manifest root.
    """
    candidates: list[Path] = []
    all_manifest_names = {name for names in MANIFESTS.values() for name in names}

    for dirpath, dirnames, filenames in _walk(repo):
        # Mutate dirnames in place to prune the walk
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".")]
        if any(name in all_manifest_names for name in filenames):
            candidates.append(dirpath)

    if not candidates:
        return [repo]
    return candidates


def _walk(root: Path):
    """Generator yielding (Path, dirnames, filenames) — like os.walk but Path-based."""
    import os
    for dp, dn, fn in os.walk(root):
        yield Path(dp), dn, fn


def detect_languages_for(service: Path) -> tuple[list[str], list[str]]:
    """Return (languages, manifest_basenames) detected at this service root.

    Manifest-first; falls back to extension dominance if no manifest matches.
    """
    languages: set[str] = set()
    manifests_found: list[str] = []

    for lang, manifest_names in MANIFESTS.items():
        for manifest in manifest_names:
            if (service / manifest).is_file():
                languages.add(lang)
                manifests_found.append(manifest)

    if not languages:
        # Extension fallback
        ext_counts: dict[str, int] = {}
        for path in service.rglob("*"):
            if path.is_file() and not _path_in_ignored(path, service):
                lang = EXTENSION_MAP.get(path.suffix)
                if lang:
                    ext_counts[lang] = ext_counts.get(lang, 0) + 1
        if ext_counts:
            # Take the dominant language (>= 50% of files)
            total = sum(ext_counts.values())
            for lang, count in sorted(ext_counts.items(), key=lambda kv: -kv[1]):
                if count / total >= 0.5:
                    languages.add(lang)
                    break

    return sorted(languages), sorted(manifests_found)


def _path_in_ignored(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True
    return any(part in IGNORE_DIRS or part.startswith(".") for part in rel.parts)


def detect_frameworks_python(service: Path) -> list[str]:
    deps = _read_python_deps(service)
    return sorted(
        fw for fw, pkgs in FRAMEWORK_SIGNATURES_PYTHON.items() if _any_match(deps, pkgs)
    )


def detect_frameworks_ts(service: Path) -> list[str]:
    deps = _read_node_deps(service)
    return sorted(
        fw for fw, pkgs in FRAMEWORK_SIGNATURES_TS.items() if _any_match(deps, pkgs)
    )


def detect_frameworks_go(service: Path) -> list[str]:
    deps = _read_go_deps(service)
    matched: list[str] = []
    for fw, pkgs in FRAMEWORK_SIGNATURES_GO.items():
        if pkgs and _any_match(deps, pkgs):
            matched.append(fw)
    if not matched and deps:
        # If go.mod exists with deps but no framework match, assume net/http stdlib
        matched.append("net-http-stdlib")
    return sorted(matched)


def _detect_runtimes(deps: set[str], signatures: dict[str, list[str]]) -> list[str]:
    """Return the execution contexts whose signature packages appear in deps."""
    return sorted(ctx for ctx, pkgs in signatures.items() if _any_match(deps, pkgs))


def detect_execution_runtimes_python(service: Path) -> list[str]:
    return _detect_runtimes(_read_python_deps(service), EXECUTION_RUNTIME_SIGNATURES_PYTHON)


def detect_execution_runtimes_ts(service: Path) -> list[str]:
    return _detect_runtimes(_read_node_deps(service), EXECUTION_RUNTIME_SIGNATURES_TS)


def detect_execution_runtimes_go(service: Path) -> list[str]:
    return _detect_runtimes(_read_go_deps(service), EXECUTION_RUNTIME_SIGNATURES_GO)


def detect_existing_instrumentation(service: Path, languages: list[str]) -> list[str]:
    deps: set[str] = set()
    if "python" in languages:
        deps |= _read_python_deps(service)
    if "typescript" in languages or "javascript" in languages:
        deps |= _read_node_deps(service)
    if "go" in languages:
        deps |= _read_go_deps(service)

    detected = []
    for name, pkgs in EXISTING_INSTRUMENTATION_PACKAGES.items():
        if _any_match(deps, pkgs):
            detected.append(name)
    return sorted(detected)


def detect_existing_moolabs_sdk(service: Path, languages: list[str]) -> Optional[str]:
    deps: set[str] = set()
    if "python" in languages:
        deps |= _read_python_deps(service)
    if "typescript" in languages or "javascript" in languages:
        deps |= _read_node_deps(service)
    if "go" in languages:
        deps |= _read_go_deps(service)

    for pkg in MOOLABS_SDK_PACKAGES:
        if pkg in deps:
            return pkg
    return None


def _any_match(deps: set[str], needles: list[str]) -> bool:
    return any(n in deps for n in needles)


# ──────────────────────────────────────────────────────────────────────
# Manifest readers
# ──────────────────────────────────────────────────────────────────────


def _read_python_deps(service: Path) -> set[str]:
    deps: set[str] = set()
    pyproject = service / "pyproject.toml"
    if pyproject.is_file():
        text = pyproject.read_text(encoding="utf-8", errors="replace")
        # Simple parse — pull anything that looks like a dep name
        for match in re.finditer(r'^\s*"?([a-zA-Z0-9_.-]+)"?\s*[=<>~]', text, flags=re.MULTILINE):
            deps.add(match.group(1).lower())
        # PEP 631 [project.dependencies] / [tool.poetry.dependencies]
        for match in re.finditer(r'"([a-zA-Z0-9_.-]+)\s*[<>=~]', text):
            deps.add(match.group(1).lower())
    requirements = service / "requirements.txt"
    if requirements.is_file():
        for line in requirements.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name = re.split(r"[<>=!~\[]", line, maxsplit=1)[0].strip().lower()
            if name:
                deps.add(name)
    return deps


def _read_node_deps(service: Path) -> set[str]:
    deps: set[str] = set()
    package_json = service / "package.json"
    if package_json.is_file():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return deps
        for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            section = data.get(key) or {}
            if isinstance(section, dict):
                deps |= set(section.keys())
    return deps


def _read_go_deps(service: Path) -> set[str]:
    deps: set[str] = set()
    go_mod = service / "go.mod"
    if go_mod.is_file():
        text = go_mod.read_text(encoding="utf-8", errors="replace")
        # Pull both the block form (indented inside `require (...)`) and the
        # single-line form (`require github.com/x/y v1.2.3`). The optional
        # `require ` prefix covers the latter; the `(` of `require (` won't match
        # the module capture, so the opening line is skipped.
        for match in re.finditer(
            r"^\s*(?:require\s+)?([a-zA-Z0-9_./-]+)\s+v[\d.]+", text, flags=re.MULTILINE
        ):
            module = match.group(1)
            if "/" in module:  # actual modules, not directives
                deps.add(module)
    return deps


# ──────────────────────────────────────────────────────────────────────
# Top-level
# ──────────────────────────────────────────────────────────────────────


def scan(repo_path: Path) -> RepoProfile:
    if not repo_path.is_dir():
        raise FileNotFoundError(f"Repo path not found: {repo_path}")

    service_roots = find_service_roots(repo_path)

    services: list[ServiceProfile] = []
    for sr in service_roots:
        languages, manifests_found = detect_languages_for(sr)
        if not languages:
            continue

        frameworks: list[str] = []
        runtimes: list[str] = []
        if "python" in languages:
            frameworks.extend(detect_frameworks_python(sr))
            runtimes.extend(detect_execution_runtimes_python(sr))
        if "typescript" in languages or "javascript" in languages:
            frameworks.extend(detect_frameworks_ts(sr))
            runtimes.extend(detect_execution_runtimes_ts(sr))
        if "go" in languages:
            frameworks.extend(detect_frameworks_go(sr))
            runtimes.extend(detect_execution_runtimes_go(sr))

        existing_instr = detect_existing_instrumentation(sr, languages)
        existing_sdk = detect_existing_moolabs_sdk(sr, languages)

        try:
            rel_path = str(sr.relative_to(repo_path))
        except ValueError:
            rel_path = str(sr)
        if rel_path == ".":
            rel_path = ""

        services.append(
            ServiceProfile(
                path=rel_path,
                languages=languages,
                manifests=manifests_found,
                frameworks_detected=sorted(set(frameworks)),
                execution_runtimes=sorted(set(runtimes)),
                existing_instrumentation=existing_instr,
                existing_moolabs_sdk=existing_sdk,
            )
        )

    if len(services) == 0:
        repo_type = "unknown"
    elif len(services) == 1 and services[0].path == "":
        repo_type = "polyrepo"
    elif len(services) > 3:
        repo_type = "microservices"
    else:
        repo_type = "monorepo"

    notes: list[str] = []
    if any(s.existing_moolabs_sdk for s in services):
        notes.append(
            "Existing Moolabs SDK detected in at least one service. "
            "Confirm with the integrator: 'upgrade in place' vs 'fresh re-instrument'."
        )
    if any("opentelemetry-api" in s.existing_instrumentation for s in services):
        notes.append(
            "OpenTelemetry detected. Codemod will use BROWNFIELD branch — "
            "extends existing spans with moolabs.* attributes rather than wrapping."
        )
    worker_only = [
        s.path or "<root>"
        for s in services
        if s.execution_runtimes and not s.frameworks_detected
    ]
    if worker_only:
        notes.append(
            "Worker/consumer/scheduler service(s) with NO HTTP framework detected: "
            f"{', '.join(worker_only)}. These ARE instrumentable — their cost/usage "
            "emission sites run outside HTTP handlers (execution_runtimes lists the "
            "context). Do not skip. Per-call-site classification is authoritative."
        )

    return RepoProfile(
        repo_type=repo_type,
        services=services,
        repo_root_languages=sorted({lang for s in services for lang in s.languages}),
        notes=notes,
    )


def to_yaml(profile: RepoProfile) -> str:
    """Lightweight YAML emitter — avoids a yaml dependency for the script."""
    def emit(value, indent: int = 0) -> list[str]:
        pad = "  " * indent
        if value is None:
            return ["null"]
        if isinstance(value, bool):
            return ["true" if value else "false"]
        if isinstance(value, (int, float)):
            return [str(value)]
        if isinstance(value, str):
            if any(c in value for c in ":#\n\"'") or value.startswith(("- ", "[", "{", " ")):
                return [json.dumps(value)]
            return [value]
        if isinstance(value, list):
            if not value:
                return ["[]"]
            lines: list[str] = []
            for item in value:
                sub = emit(item, indent + 1)
                if isinstance(item, (dict, list)) and item:
                    lines.append(f"{pad}-")
                    for line in sub:
                        lines.append(f"{pad}  {line}")
                else:
                    lines.append(f"{pad}- {sub[0]}")
            return lines
        if isinstance(value, dict):
            if not value:
                return ["{}"]
            lines = []
            for k, v in value.items():
                sub = emit(v, indent + 1)
                if isinstance(v, (dict, list)) and v:
                    lines.append(f"{pad}{k}:")
                    for line in sub:
                        lines.append(f"{pad}  {line}")
                else:
                    lines.append(f"{pad}{k}: {sub[0]}")
            return lines
        return [str(value)]

    return "\n".join(emit(asdict(profile)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo_path", help="Path to customer repository")
    parser.add_argument(
        "--output",
        default=None,
        help="Output path (default: <repo>/.moolabs/discovery/repo-profile.yaml)",
    )
    args = parser.parse_args()

    repo_path = Path(args.repo_path).resolve()
    output = (
        Path(args.output).resolve()
        if args.output
        else repo_path / ".moolabs" / "discovery" / "repo-profile.yaml"
    )

    try:
        profile = scan(repo_path)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(to_yaml(profile) + "\n", encoding="utf-8")
    print(f"Wrote {output}")
    print(f"  repo_type: {profile.repo_type}")
    print(f"  services:  {len(profile.services)}")
    print(f"  languages: {profile.repo_root_languages}")
    for note in profile.notes:
        print(f"  NOTE: {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
