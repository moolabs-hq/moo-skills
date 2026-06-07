"""Cost-bearing checkout — emits a sibling-pair via emit_event_safe."""

from __future__ import annotations


def deliver_recommendation(customer_id: str, request_id: str, value: int) -> None:
    pass  # codemod inserts emit_event_safe(...) here
