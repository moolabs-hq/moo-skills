#!/usr/bin/env python3
"""Framework-capability tree loader.

Assembles per-framework node files (shared/assets/frameworks/<lang>/<fw>.yaml)
into {language: {framework: Node}} and schema-validates each.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path


class RegistryError(ValueError):
    """A framework node file is malformed."""


@dataclass
class Node:
    id: str
    language: str
    framework: str
    detection: dict
    wiring: dict
    emit: dict
    scripts: list[str]


_LANGUAGES = {"python", "typescript", "go"}
_KINDS = {"regex", "code"}
_MODES = {"modify", "stub"}
_ANCHORS = {"detected_config_dir", "service_root"}
_REQUIRED_TOP = ("id", "language", "framework", "detection", "wiring", "emit", "scripts")


def _read_yaml(path: Path) -> dict:
    try:
        import yaml
        return yaml.safe_load(path.read_text()) or {}
    except ImportError as exc:  # pragma: no cover
        raise RegistryError(
            f"PyYAML required to load framework registry ({path})"
        ) from exc


def _validate(d: dict, path: Path) -> Node:
    if not isinstance(d, dict):
        raise RegistryError(f"{path}: node must be a mapping")
    for k in _REQUIRED_TOP:
        if k not in d:
            raise RegistryError(f"{path}: missing required key {k!r}")
    if d["language"] not in _LANGUAGES:
        raise RegistryError(f"{path}: language {d['language']!r} not in {_LANGUAGES}")
    det = d["detection"] or {}
    if det.get("kind") not in _KINDS:
        raise RegistryError(f"{path}: detection.kind must be one of {_KINDS}")
    wir = d["wiring"] or {}
    if wir.get("mode") not in _MODES:
        raise RegistryError(f"{path}: wiring.mode must be one of {_MODES}")
    emit = d["emit"] or {}
    if emit.get("anchor") not in _ANCHORS:
        raise RegistryError(f"{path}: emit.anchor must be one of {_ANCHORS}")
    if not isinstance(d["scripts"], list) or not d["scripts"]:
        raise RegistryError(f"{path}: scripts must be a non-empty list")
    return Node(
        id=d["id"], language=d["language"], framework=d["framework"],
        detection=det, wiring=wir, emit=emit, scripts=list(d["scripts"]),
    )


def load_registry(frameworks_dir: Path) -> dict[str, dict[str, Node]]:
    """Load + validate all nodes. Returns {language: {framework: Node}}."""
    tree: dict[str, dict[str, Node]] = {}
    if not frameworks_dir.is_dir():
        return tree
    for path in sorted(frameworks_dir.glob("*/*.yaml")):
        node = _validate(_read_yaml(path), path)
        tree.setdefault(node.language, {})[node.framework] = node
    return tree
