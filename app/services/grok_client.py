"""Grok (xAI) client — Stage 1 structured understanding + Stage 2 natural reply.
Key never leaves the backend. Failures return None so bot uses safe fallbacks.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from app.config import get_settings

log = logging.getLogger("client_raq.grok")

INTENTS = {
    "greeting", "printing_request", "price_request", "quotation_request",
    "product_availability", "design_request", "design_upload", "file_upload",
    "delivery_question", "turnaround_question", "payment_question", "order_status",
    "modification_request", "cancellation_request", "complaint", "human_agent_request",
    "general_question", "unknown", "clarifying", "affirm", "deny",
}


def _api_key() -> str:
    s = get_settings()
    # Support both names from the integration spec
    return (getattr(s, "GROK_API_KEY", None) or getattr(s, "XAI_API_KEY", None) or "").strip()


def _model() -> str:
    s = get_settings()
    return (getattr(s, "GROK_MODEL", None) or getattr(s, "XAI_MODEL", None) or "grok-2-latest").strip()


def _base_url() -> str:
    s = get_settings()
    return (getattr(s, "GROK_BASE_URL", None) or "https://api.x.ai/v1").rstrip("/")


async def _chat(
    messages: list[dict],
    *,
    temperature: float = 0.3,
    max_tokens: int = 700,
) -> str | None:
    key = _api_key()
    if not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            r = await client.post(
                f"{_base_url()}/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": _model(),
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )
            if r.status_code == 429 or r.status_code >= 500:
                log.warning("grok_http_%s", r.status_code)
                return None
            if r.status_code >= 400:
                log.warning("grok_client_error %s", r.status_code)
                return None
            data = r.json()
            return (data["choices"][0]["message"]["content"] or "").strip()
    except Exception as e:
        log.warning("grok_exception %s", type(e).__name__)
        return None


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    text = text.strip()
    # fenced json
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


async def grok_chat(system: str, user_message: str, history: list[dict] | None = None) -> str | None:
    """Simple text completion (legacy callers)."""
    messages = [{"role": "system", "content": system}]
    if history:
        messages.extend(history[-8:])
    messages.append({"role": "user", "content": user_message})
    return await _chat(messages, temperature=0.4, max_tokens=600)


async def grok_vision(system: str, prompt: str, image_bytes: bytes, mime: str = "image/jpeg") -> str | None:
    import base64

    key = _api_key()
    if not key or not image_bytes:
        return None
    b64 = base64.b64encode(image_bytes).decode("ascii")
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ],
        },
    ]
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                f"{_base_url()}/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": _model(), "messages": messages, "max_tokens": 200},
            )
            if r.status_code != 200:
                return None
            return r.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return None


# ── Stage 1: structured understanding ──────────────────────────────────────

UNDERSTAND_SYSTEM = """You are the understanding layer for a printing-shop WhatsApp bot (Client RaQ).
Return ONLY valid JSON (no markdown, no extra text) with this shape:
{
  "intent": "<one of: greeting|printing_request|price_request|quotation_request|product_availability|design_request|design_upload|file_upload|delivery_question|turnaround_question|payment_question|order_status|modification_request|cancellation_request|complaint|human_agent_request|general_question|clarifying|affirm|deny|unknown>",
  "product": "<service name if any, else null>",
  "quantity": <number or null>,
  "size": "<size text or null>",
  "width": <number or null>,
  "height": <number or null>,
  "size_unit": "<ft|inches|m|null>",
  "design_status": "<has_design|needs_design|received|unknown|null>",
  "fulfillment": "<pickup|delivery|null>",
  "delivery_location": "<text or null>",
  "language": "<en|pidgin>",
  "needs_price": true/false,
  "needs_human": true/false,
  "missing_information": ["field", ...],
  "response_goal": "<ask_missing|calculate_quote|answer_status|answer_faq|handoff|acknowledge|greet>",
  "confidence": 0.0-1.0,
  "notes": "<short internal note>"
}
Rules:
- Understand English, Nigerian English, Pidgin, slang, typos, short replies ("1k", "yes", "that one").
- Use conversation_state and recent_messages; do not ignore prior product/qty/size.
- Never invent prices or claim an order is complete.
- Prefer extracting all facts from the message; only list truly missing fields.
- quantity: treat "1k" as 1000, "2k" as 2000 when context is print qty.
"""


async def understand_message(
    *,
    customer_message: str,
    conversation_state: dict[str, Any],
    recent_messages: list[dict],
    company_name: str,
    services_summary: str,
    currency: str,
) -> dict | None:
    payload = {
        "customer_message": customer_message,
        "conversation_state": conversation_state,
        "recent_messages": recent_messages[-10:],
        "company_name": company_name,
        "currency": currency,
        "available_services": services_summary,
    }
    messages = [
        {"role": "system", "content": UNDERSTAND_SYSTEM},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    raw = await _chat(messages, temperature=0.15, max_tokens=500)
    if not raw:
        return None
    data = _extract_json(raw)
    if not data:
        return None
    intent = str(data.get("intent") or "unknown").lower().strip()
    if intent not in INTENTS:
        intent = "unknown"
    data["intent"] = intent
    conf = data.get("confidence")
    try:
        data["confidence"] = float(conf) if conf is not None else 0.5
    except Exception:
        data["confidence"] = 0.5
    return data


# ── Stage 2: natural customer-facing reply ──────────────────────────────────

REPLY_SYSTEM = """You are a real customer-care staff at a printing/branding company on WhatsApp.
Never say you are an AI or bot. Never invent prices, services, or order statuses.
Only use verified_facts from the backend for money, services, and status.
Match the customer's language (English or Nigerian Pidgin). Keep replies short (1-4 sentences).
Be warm and professional. Ask at most one clear question if something is missing.
Do not use markdown tables. Light *bold* is ok for WhatsApp.
"""


async def generate_reply(
    *,
    company_name: str,
    tone_hint: str,
    conversation_state: dict[str, Any],
    recent_messages: list[dict],
    understanding: dict[str, Any],
    verified_facts: dict[str, Any],
    customer_message: str,
) -> str | None:
    payload = {
        "company_name": company_name,
        "tone": tone_hint,
        "customer_message": customer_message,
        "conversation_state": conversation_state,
        "understanding": understanding,
        "verified_facts": verified_facts,
        "recent_messages": recent_messages[-8:],
    }
    messages = [
        {"role": "system", "content": REPLY_SYSTEM},
        {
            "role": "user",
            "content": (
                "Write the next WhatsApp message to the customer using only verified_facts for prices/status.\n"
                + json.dumps(payload, ensure_ascii=False)
            ),
        },
    ]
    return await _chat(messages, temperature=0.45, max_tokens=400)
