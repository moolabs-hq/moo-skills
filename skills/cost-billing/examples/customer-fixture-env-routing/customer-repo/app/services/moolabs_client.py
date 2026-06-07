"""STUB — what `app/services/moolabs_client.py` looks like AFTER the codemod
runs. This file is included in the fixture so:
  1. The customer-repo can be imported as a Python package (no ImportError
     in `app.services.checkout` / `app.services.seat_assignment`).
  2. Reviewers see the post-codemod helper shape — `_resolve_api_key()`
     reading via `from app.settings import get_settings` (the Phase B
     "modify" mode contract).

In a REAL customer repo this file does NOT exist before the codemod runs.
Phase B's instrument step writes this file from
`assets/codemod-templates/python-moolabs-client.py.j2` with the per-service
`env_config` resolved by `config_wire.py`."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.settings import get_settings


@lru_cache(maxsize=1)
def _resolve_api_key() -> str:
    return get_settings().moolabs_api_key.get_secret_value()


# Real helpers from the v0.3.0-rc1 SDK live here after the codemod runs.
# This stub provides no-op signatures so the fixture's service files import
# cleanly without pulling in the actual `moolabs` package.

def emit_event_safe(**_kwargs: Any) -> None:
    """Sibling-pair emission (cost + usage in one call). Stubbed."""


def emit_usage_event_safe(**_kwargs: Any) -> None:
    """Usage-only emission. Stubbed."""


def emit_cost_event_safe(**_kwargs: Any) -> None:
    """Cost-only emission. Stubbed."""
