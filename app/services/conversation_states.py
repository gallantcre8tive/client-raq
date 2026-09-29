"""Formal conversation stages (spec §19)."""
from __future__ import annotations

FORMAL_STATES = (
    "new_customer",
    "understanding_request",
    "collecting_information",
    "awaiting_file",
    "awaiting_payment",
    "payment_received",  # admin confirmed
    "processing_order",
    "awaiting_delivery_information",
    "ready_for_human",
    "completed",
    "cancelled",
    # legacy aliases still accepted
    "open",
    "await_service",
    "await_details",
    "await_fulfillment",
    "await_schedule",
    "await_payment",
    "await_proof",
    "order_placed",
)

LEGACY_TO_FORMAL = {
    "open": "understanding_request",
    "await_service": "understanding_request",
    "await_details": "collecting_information",
    "await_fulfillment": "awaiting_delivery_information",
    "await_schedule": "awaiting_delivery_information",
    "await_payment": "awaiting_payment",
    "await_proof": "awaiting_payment",
    "awaiting_file": "awaiting_file",
    "order_placed": "processing_order",
}


def normalize_state(state: str | None) -> str:
    s = (state or "new_customer").strip()
    return LEGACY_TO_FORMAL.get(s, s)


def next_state_after_facts(ctx: dict, current: str) -> str:
    """Heuristic stage update from structured context."""
    cur = normalize_state(current)
    if ctx.get("needs_human") or cur == "ready_for_human":
        return "ready_for_human"
    if ctx.get("payment_confirmed"):
        return "processing_order"
    if ctx.get("payment_proof") and not ctx.get("payment_confirmed"):
        return "awaiting_payment"
    if ctx.get("awaiting_file"):
        return "awaiting_file"
    if ctx.get("quote_locked") and not ctx.get("fulfillment"):
        return "awaiting_delivery_information"
    if ctx.get("quote_locked") and ctx.get("fulfillment") and not ctx.get("payment_proof"):
        return "awaiting_payment"
    if ctx.get("service_name") or ctx.get("service_id"):
        if not ctx.get("qty") or (
            ctx.get("pricing_needs_size") and not (ctx.get("width") and ctx.get("height"))
        ):
            return "collecting_information"
        return "collecting_information"
    if cur in ("new_customer", "open"):
        return "understanding_request"
    return cur
