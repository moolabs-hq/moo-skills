"""Usage-only seat assignment."""

from __future__ import annotations

from app.services.moolabs_client import emit_usage_event_safe


def assign_seat(customer_id: str, request_id: str) -> None:
    emit_usage_event_safe(
        event_type="seat.assigned",
        customer_id=customer_id,
        entity_id=request_id,
        meter_slug="seat.assigned",
        value=1,
        meta={"workflow_id": "seat.assigned"},
    )
