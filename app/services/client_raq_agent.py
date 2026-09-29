"""Client RaQ Agent — Grok + tools. Primary intelligence layer.

Flow: message → Grok (with tools) → tool results → Grok → WhatsApp reply.
Prices always from calculate_service_price backend tool. Company isolation enforced.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.company import Company
from app.models.conversation import Conversation, Message
from app.services.agent_tools import TOOL_DEFINITIONS, ToolContext, execute_tool

log = logging.getLogger("client_raq.agent")

SYSTEM_PROMPT = """You are Client RaQ, the WhatsApp customer-service agent for a real print shop.

You sound like a helpful human staff member — warm, concise, professional. Never say you are an AI or Grok.

HARD RULES:
1. NEVER invent services, prices, bank details, or order status. Use tools.
2. Before stating any price, call calculate_service_price (or get_service_details first).
3. If price is not configured or tool returns error, say you will confirm with the team — call notify_human_agent if needed.
4. Remember facts already in order state / conversation — do not re-ask.
5. Match language: Pidgin if customer uses Pidgin; clear English otherwise.
6. WhatsApp style: short (2–5 sentences). One clear next question when needed.
7. Do not claim payment is confirmed unless backend/admin confirmed it.
8. Escalate with notify_human_agent for: human request, complaints, unknown special jobs, price disputes, confusion after two tries.
9. When quote is locked, do not change the total unless customer asks for a new quote.
10. Company data from tools is the only source of truth.
11. When a design/image/file arrives, acknowledge it and continue — do not ask for the same file again.
12. Payment screenshots only mark pending verification; never say payment is confirmed until admin confirms.
13. Voice notes may arrive as transcribed text — treat transcript as the customer message.

Use tools whenever you need services, prices, payment info, or to save order fields.
For returning customers saying 'same as last time', call get_previous_orders then confirm details.
When customer is unsure what a product looks like, call send_reference_sample.
Keep formal stage updated with set_conversation_state when stage clearly changes.

14. SIZE UNITS: If the customer has not said whether size is in inches or feet (or metres), ASK once: "Is that size in inches or in feet?" Do NOT calculate area price until unit is known. Default only if they clearly said ft/feet or inches/" or cm.
15. MINIMUM QUANTITY: Before confirming a quote, check the service min_qty from tools. If customer qty is below min_qty, explain the company minimum and ask them to increase qty OR confirm they will still pay (especially for small inch stickers on large-format machines — waste warning).
16. SMALL INCH / LARGE-FORMAT WASTE: If size is in inches and both sides are under 12 inches AND quantity is under 20 (or under service min_qty), warn: material waste on large format; ask if they will increase quantity or accept paying for the sheet waste. Do not silently under-quote.
17. TIER PRICES: If the service has fixed options / variants labeled with quantities (e.g. "50 pcs", "100 pcs"), prefer those prices over inventing a per-piece scale. Call get_service_details / list variants via tools.
18. Always ask only the next missing fact (unit, size, qty, design, fulfillment) — one clear question.

19. If the customer says wait / I want to ask a question / hold on: answer their question only. Do NOT re-ask pickup/delivery or push the payment step until they are ready.
20. When quote is locked and they ask something else, answer first. At the end you may briefly say the previous quote is still open (service, size, qty, total) — do not send buttons text again.
21. If they want to ADD another item (another banner, frame, etc.), confirm the first quote, ask details for the second, then ask: "Should we add this to the same order with the earlier item?" Sum totals only after they agree.
22. Stay available after order is placed — answer status, changes, and new jobs in the same chat. Summarize what they ordered/paid when relevant (service, size, qty, total, fulfillment).
"""


def _api_key() -> str:
    s = get_settings()
    return (getattr(s, "GROK_API_KEY", None) or getattr(s, "XAI_API_KEY", None) or "").strip()


def _model() -> str:
    s = get_settings()
    return (getattr(s, "GROK_MODEL", None) or getattr(s, "XAI_MODEL", None) or "grok-4-fast-non-reasoning").strip()


def _base() -> str:
    s = get_settings()
    return (getattr(s, "GROK_BASE_URL", None) or "https://api.x.ai/v1").rstrip("/")


async def _chat_with_tools(messages: list[dict], tools: list[dict] | None = None) -> dict | None:
    key = _api_key()
    if not key:
        return None
    body: dict[str, Any] = {
        "model": _model(),
        "messages": messages,
        "temperature": 0.4,
        "max_tokens": 500,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            log.info("grok_request model=%s msgs=%s tools=%s", _model(), len(messages), bool(tools))
            r = await client.post(
                f"{_base()}/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=body,
            )
            if r.status_code >= 400:
                log.warning("agent_http %s %s", r.status_code, (r.text or "")[:400])
                return None
            data = r.json()
            choice = (data.get("choices") or [{}])[0]
            return choice.get("message") or {}
    except Exception as e:
        log.warning("agent_exception %s", type(e).__name__)
        return None


def _build_system(company: Company, ctx: dict) -> str:
    lang = ctx.get("lang") or getattr(company, "bot_language", None) or "both"
    personality = getattr(company, "bot_personality", None) or "friendly"
    custom = (getattr(company, "custom_ai_instructions", None) or "").strip()
    parts = [
        SYSTEM_PROMPT,
        f"Company: {company.name}",
        f"Currency: {company.currency or 'NGN'}",
        f"Language preference setting: {lang}",
        f"Tone: {personality}",
    ]
    if custom:
        parts.append(f"Shop rules: {custom[:800]}")
    if getattr(company, "business_hours", None):
        parts.append(f"Hours: {company.business_hours}")
    if getattr(company, "location_text", None):
        parts.append(f"Location: {company.location_text}")
    if ctx.get("quote_locked") and ctx.get("locked_total"):
        parts.append(
            f"LOCKED QUOTE: total={ctx.get('locked_total')} service={ctx.get('service_name')} "
            f"size={ctx.get('width')}x{ctx.get('height')} {ctx.get('size_unit')} qty={ctx.get('qty')}. Do not change total."
        )
    return "\n".join(parts)


async def run_agent(
    db: AsyncSession,
    *,
    company: Company,
    conv: Conversation,
    ctx: dict,
    from_wa: str,
    customer_message: str,
    recent: list[dict] | None = None,
    attachment_note: str | None = None,
    max_tool_rounds: int = 5,
) -> tuple[str | None, dict, bool]:
    """Returns (reply_text, updated_ctx, needs_human)."""
    if not _api_key():
        return None, ctx, False

    tc = ToolContext(db, company, conv, ctx, from_wa)
    system = _build_system(company, ctx)

    messages: list[dict] = [{"role": "system", "content": system}]
    if recent:
        for m in recent[-8:]:
            role = "assistant" if m.get("direction") == "outbound" else "user"
            body = (m.get("body") or "").strip()
            if body:
                messages.append({"role": role, "content": body[:800]})
    # structured state hint
    state_hint = {
        "conversation_state": conv.state,
        "order_context": {
            k: ctx.get(k)
            for k in (
                "service_name", "qty", "width", "height", "size_unit", "variant_label",
                "design_status", "fulfillment", "total", "locked_total", "quote_locked", "lang",
            )
            if ctx.get(k) is not None
        },
    }
    user_blob = f"Customer message: {customer_message}"
    if attachment_note:
        user_blob += f"\n\nAttachment event: {attachment_note}"
    user_blob += f"\n\nInternal state: {json.dumps(state_hint, ensure_ascii=False)}"
    messages.append({"role": "user", "content": user_blob})

    for _round in range(max_tool_rounds):
        msg = await _chat_with_tools(messages, TOOL_DEFINITIONS)
        if not msg:
            return None, tc.ctx, tc.needs_human

        tool_calls = msg.get("tool_calls") or []
        content = (msg.get("content") or "").strip()

        if not tool_calls:
            # Final natural reply
            if content:
                return content, tc.ctx, tc.needs_human
            return None, tc.ctx, tc.needs_human

        # Append assistant tool_calls message
        messages.append({
            "role": "assistant",
            "content": content or None,
            "tool_calls": tool_calls,
        })

        for tc_item in tool_calls:
            fn = (tc_item.get("function") or {})
            fname = fn.get("name") or ""
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
            except Exception:
                args = {}
            result = await execute_tool(fname, args, tc)
            # If calculate returned total, mirror into ctx
            if fname == "calculate_service_price" and isinstance(result, dict) and result.get("total"):
                tc.ctx["total"] = result["total"]
                tc.ctx["subtotal"] = result["total"]
                tc.ctx["locked_total"] = result["total"]
                tc.ctx["quote_locked"] = True
                if result.get("service"):
                    tc.ctx["service_name"] = result["service"]
                if result.get("service_id"):
                    tc.ctx["service_id"] = result["service_id"]
                if result.get("quantity"):
                    tc.ctx["qty"] = result["quantity"]
                if result.get("width") is not None:
                    tc.ctx["width"] = result["width"]
                if result.get("height") is not None:
                    tc.ctx["height"] = result["height"]
                if result.get("size_unit"):
                    tc.ctx["size_unit"] = result["size_unit"]
            messages.append({
                "role": "tool",
                "tool_call_id": tc_item.get("id") or fname,
                "content": json.dumps(result, ensure_ascii=False)[:4000],
            })

        if tc.needs_human:
            # one more turn optional — still return escalation text if any later
            pass

    # Exhausted rounds — last try without tools
    messages.append({
        "role": "user",
        "content": "Give the customer a short final WhatsApp reply now. No more tools.",
    })
    msg = await _chat_with_tools(messages, None)
    if msg and (msg.get("content") or "").strip():
        return msg["content"].strip(), tc.ctx, tc.needs_human
    return None, tc.ctx, tc.needs_human
