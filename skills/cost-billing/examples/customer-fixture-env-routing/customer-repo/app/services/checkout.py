"""Cost-bearing recommendation delivery."""

from __future__ import annotations

from app.services.moolabs_client import emit_event_safe


def deliver_recommendation(customer_id: str, request_id: str, value: int) -> None:
    emit_event_safe(
        event_type="checkout.recommendation.delivered",
        customer_id=customer_id,
        entity_id=request_id,
        meter_slug="checkout.recommendation.delivered",
        value=value,
        spans=[{
            "span_id": request_id,
            "cost_micros": 1000,
            "kind": "llm-tokens",
        }],
        meta={"workflow_id": "checkout.recommendation.delivered"},
    )
