
def _rule_reply(company, customer_message: str, ctx: dict) -> str:
    """Natural fallback when Grok is down — match customer tone, never dump a form."""
    name = getattr(company, "name", None) or "us"
    msg = (customer_message or "").strip()
    low = msg.lower()
    words = set(low.replace("?", " ").replace("!", " ").split())
    pidgin_markers = {"abeg", "wan", "dey", "wetin", "oya", "haffa", "omoh", "fit", "nko", "nah", "bros", "brother", "guy", "una", "sef", "sharp", "howfar", "how"}
    is_pidgin = bool(words & pidgin_markers) or any(x in low for x in ("no fit", "how your side", "wetin you", "i wan", "abeg"))
    is_greet = bool(words & {"hi", "hello", "hey", "haffa", "howfar", "sup", "yo", "morning", "evening", "afternoon", "brother", "bros", "boss"}) or low in ("hi", "hello", "hey", "haffa")
    btype = (getattr(company, "business_type", None) or "printing").lower()

    if is_greet and is_pidgin:
        return (
            f"Haa big man! Welcome to *{name}*.\n"
            "How your side nah? Wetin you wan do today — talk am, I dey here."
        )
    if is_greet:
        return (
            f"Hey! Welcome to *{name}*.\n"
            "How can I help you today? Just tell me what you need."
        )
    if is_pidgin:
        return (
            f"I hear you — *{name}* dey online.\n"
            "Abeg tell me wetin you need clearly and I go sort you."
        )
    # business-aware short nudge
    hints = {
        "printing": "banner, sticker, frame, nylon…",
        "exchanger": "PayPal, crypto, gift card, bank transfer…",
        "laundry": "wash, dry-clean, express…",
        "skincare": "product, consultation, booking…",
        "fashion": "tailoring, fabric, ready-to-wear…",
        "restaurant": "menu, order, delivery…",
        "phone_repair": "phone model and the issue…",
        "retail": "the product you want…",
        "logistics": "pickup and delivery details…",
        "real_estate": "rent, sale, or inspection…",
        "education": "the course or service…",
    }
    hint = hints.get(btype, "what you need")
    return (
        f"Thanks for messaging *{name}*.\n"
        f"Tell me {hint} and I will help you right away."
    )




def _company_system_prompt(company) -> str:
    try:
        from app.data.business_templates import build_system_prompt
        return build_system_prompt(company, getattr(company, "business_type", None))
    except Exception as e:
        print("build_system_prompt_fail", e)
        return SYSTEM_PROMPT.replace("a real print shop", getattr(company, "name", "this business") or "this business")


SYSTEM_PROMPT = """You are Client RaQ, the WhatsApp customer-service agent for this business.

You sound like a helpful human staff member — warm, concise, professional. Never say you are an AI or Grok.

HARD RULES:
1. NEVER invent services, prices, bank details, or order status. Use tools.
2. Before stating any price, call calculate_service_price (or get_service_details first).
3. If price is not configured or tool returns error, say you will confirm with the team — call notify_human_agent if needed.
4. Remember facts already in order state / conversation — do not re-ask.
5. LANGUAGE (critical): Reply in the SAME language/style the customer used.
   - Pidgin/Naija (haffa, wetin, abeg, omoh, dey) → reply in natural Pidgin, warm and short.
   - Yoruba, Hausa, Igbo mixed with English → understand and reply in clear Pidgin or simple English with local warmth.
   - French / Spanish / Arabic / German / Dutch / Chinese / Korean / Portuguese → reply in that language if you can; otherwise clear simple English and say you can continue in English.
   - Never force a robotic "service, size, quantity" form on a simple greeting.
   - For "Haffa" / "How far" / "Hi brother" → greet back warmly, then ask what they need — do NOT dump instructions.
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

14. (Printing only) SIZE UNITS: If the customer has not said whether size is in inches or feet (or metres), ASK once: "Is that size in inches or in feet?" Do NOT calculate area price until unit is known. Default only if they clearly said ft/feet or inches/" or cm.
15. MINIMUM QUANTITY: Before confirming a quote, check the service min_qty from tools. If customer qty is below min_qty, explain the company minimum and ask them to increase qty OR confirm they will still pay (especially for small inch stickers on large-format machines — waste warning).
16. SMALL INCH / LARGE-FORMAT WASTE: If size is in inches and both sides are under 12 inches AND quantity is under 20 (or under service min_qty), warn: material waste on large format; ask if they will increase quantity or accept paying for the sheet waste. Do not silently under-quote.
17. TIER PRICES: If the service has fixed options / variants labeled with quantities (e.g. "50 pcs", "100 pcs"), prefer those prices over inventing a per-piece scale. Call get_service_details / list variants via tools.
18. GREETINGS: If the customer only greets (hi, haffa, how far, hello, good morning), reply with a warm short greeting in their style, then ONE soft question about what they need. Do not call tools yet. Do not list every service.
19. Always ask only the next missing fact (unit, size, qty, design, fulfillment) — one clear question.

19. If the customer says wait / I want to ask a question / hold on: answer their question only. Do NOT re-ask pickup/delivery or push the payment step until they are ready.
20. When quote is locked and they ask something else, answer first. At the end you may briefly say the previous quote is still open (service, size, qty, total) — do not send buttons text again.
21. If they want to ADD another item (another banner, frame, etc.), confirm the first quote, ask details for the second, then ask: "Should we add this to the same order with the earlier item?" Sum totals only after they agree.
22. If customer is unsure what a product looks like, say you can show examples if the company has uploaded them (staff manages Example images per service). Stay available after order is placed — answer status, changes, and new jobs in the same chat. Summarize what they ordered/paid when relevant (service, size, qty, total, fulfillment).
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
        async with httpx.AsyncClient(timeout=18.0) as client:
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
    try:
        from app.data.business_templates import build_system_prompt
        base = build_system_prompt(company, getattr(company, "business_type", None))
    except Exception as e:
        print("business_prompt_fail", e)
        base = SYSTEM_PROMPT
    parts = [base]
    if ctx.get("quote_locked") and ctx.get("locked_total"):
        parts.append(
            f"LOCKED QUOTE: total={ctx.get('locked_total')} service={ctx.get('service_name')} "
            f"size={ctx.get('width')}x{ctx.get('height')} {ctx.get('size_unit')} qty={ctx.get('qty')}. Do not change total."
        )
    if ctx.get("lang"):
        parts.append(f"Active reply language for this chat: {ctx.get('lang')}")
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
    max_tool_rounds: int = 2,
) -> tuple[str | None, dict, bool]:
    """Returns (reply_text, updated_ctx, needs_human)."""
    if not _api_key():
        log.warning("grok_no_api_key — rule reply")
        return _rule_reply(company, customer_message, ctx), ctx, False

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

    # Fast path: pure greetings → one natural reply, no tools (swift + human)
    _cm = (customer_message or "").strip().lower()
    _gw = set(_cm.replace("?", " ").replace("!", " ").split())
    _greet_set = {"hi", "hello", "hey", "haffa", "howfar", "sup", "yo", "morning", "evening", "afternoon", "brother", "bros", "boss", "guy", "how", "far", "na"}
    if len(_gw) <= 5 and (_gw & _greet_set) and not any(x in _cm for x in ("sticker", "banner", "nylon", "frame", "price", "how much", "paypal", "order", "qty", "quantity")):
        msg = await _chat_with_tools(messages, None)  # no tools
        if msg and (msg.get("content") or "").strip():
            return msg["content"].strip(), tc.ctx, False
        return _rule_reply(company, customer_message, ctx), ctx, False

    for _round in range(max_tool_rounds):
        msg = await _chat_with_tools(messages, TOOL_DEFINITIONS)
        if not msg:
            return _rule_reply(company, customer_message, tc.ctx), tc.ctx, tc.needs_human

        tool_calls = msg.get("tool_calls") or []
        content = (msg.get("content") or "").strip()

        if not tool_calls:
            # Final natural reply
            if content:
                return content, tc.ctx, tc.needs_human
            return _rule_reply(company, customer_message, tc.ctx), tc.ctx, tc.needs_human

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
    return _rule_reply(company, customer_message, tc.ctx), tc.ctx, tc.needs_human
