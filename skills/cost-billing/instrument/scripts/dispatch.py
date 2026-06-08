#!/usr/bin/env python3
"""Thin per-node dispatcher: runs ONLY the scripts a framework node declares.

The instrument phase no longer unconditionally runs config_wire + render_artifacts
for every service. Instead, the winning framework node (from the env-routing
inventory) declares an ordered `scripts` list; this dispatcher runs exactly
those, in order, via the supplied handler map. "Pick the specific context, run
only its scripts."
"""
from __future__ import annotations


class DispatchError(ValueError):
    """A node declared a script with no registered handler."""


def dispatch_node(node: dict, ctx: dict, handlers: dict) -> list[str]:
    """Run node['scripts'] in order via handlers[name](ctx). Returns the ordered
    list of executed script names. An unknown script name → DispatchError (fail
    loud rather than silently skip work)."""
    executed: list[str] = []
    for name in node.get("scripts") or []:
        fn = handlers.get(name)
        if fn is None:
            raise DispatchError(f"node declares unknown script {name!r}")
        fn(ctx)
        executed.append(name)
    return executed
