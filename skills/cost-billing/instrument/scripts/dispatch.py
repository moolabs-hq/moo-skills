#!/usr/bin/env python3
"""Thin per-node dispatcher: runs ONLY the scripts a framework node declares.

The winning framework node (from the env-routing inventory) declares an ordered
`scripts` list; this dispatcher runs exactly those, in order, via the supplied
handler map. "Pick the specific context, run only its scripts."

STATUS (PR #11 review F4): this is the MECHANISM for per-framework script
selectivity, invoked by the agent-driven instrument flow (SKILL.md Phase
2c-render) rather than a deterministic Python `main()`. Today every config-axis
node declares the same `scripts: ["config_wire", "render_artifacts"]`, so the
per-node selectivity is not yet exercised — it becomes meaningful when a future
framework needs a different script set (e.g. a deployment-framework node running
a terraform-only emitter). The function is fully tested so that divergence is a
data change (edit a node's `scripts`), not a code change.
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
