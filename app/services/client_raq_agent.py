
def _rule_reply(company, customer_message: str, ctx: dict) -> str:
    """Natural fallback when Grok is down — match customer tone + business type."""
    name = getattr(company, "name", None) or "us"
    msg = (customer_message or "").strip()
    low = msg.lower()
    words = set(low.replace("?", " ").replace("!", " ").split())
    pidgin_markers = {
        "abeg", "wan", "dey", "wetin", "oya", "haffa", "omoh", "fit", "nko", "nah",
        "bros", "brother", "guy", "una", "sef", "sharp", "howfar", "how", "blood",
    }
    is_pidgin = bool(words & pidgin_markers) or any(
        x in low for x in ("no fit", "how your side", "wetin you", "i wan", "abeg")
    )
    is_greet = bool(
        words & {"hi", "hello", "hey", "haffa", "howfar", "sup", "yo", "morning", "evening", "afternoon", "brother", "bros", "boss", "blood"}
    ) or low in ("hi", "hello", "hey", "haffa", "how far", "haffa blood")
    btype = (getattr(company, "business_type", None) or "printing").strip().lower().replace(" ", "_").replace("-", "_")

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

    # ── Printing ──
    if btype in ("printing", "print"):
        if any(x in low for x in ("sticker", "sav", "label")):
            return (
                f"Oya sticker for *{name}*!\nAbeg tell me size (width x height) and how many pieces. Inches or feet?"
                if is_pidgin else
                f"Sticker printing — please share size (width x height) and quantity. Inches or feet?"
            )
        if "nylon" in low:
            return (
                f"Nylon — how many pieces you wan, and e go get custom print?"
                if is_pidgin else
                f"Nylon bags — how many pieces, and do you need custom branding?"
            )
        if any(x in low for x in ("banner", "flex", "rollup", "roll-up")):
            return (
                f"Banner/flex — tell me size in feet (e.g. 5x2) and quantity."
                if is_pidgin else
                f"Banner — share size in feet (e.g. 5x2 ft) and quantity."
            )
        if "frame" in low or "acrylic" in low:
            return (
                f"Frame — which size (e.g. 8x10, 12x16)? How many pieces?"
                if not is_pidgin else
                f"Frame — which size (8x10, 12x16…)? How many pieces?"
            )

    # ── Payment exchanger ──
    if btype == "exchanger":
        if any(x in low for x in ("paypal", "pay pal")):
            return (
                f"PayPal for *{name}* — you wan *receive* or *send*? Abeg drop the amount and currency (e.g. $100)."
                if is_pidgin else
                f"PayPal via *{name}* — do you want to *receive* or *send*? Share the amount and currency (e.g. $100)."
            )
        if any(x in low for x in ("cashapp", "cash app", "cashtag")):
            return (
                f"Cash App — amount and currency? I go tell you the tag / next step once confirmed."
                if is_pidgin else
                f"Cash App — share the amount and currency, and I will give the next steps."
            )
        if any(x in low for x in ("crypto", "usdt", "btc", "bitcoin", "eth")):
            return (
                f"Crypto — which coin/network and how much? I go check if that corridor dey available."
                if is_pidgin else
                f"Crypto — which coin/network and amount? I will check if that method is available."
            )
        if any(x in low for x in ("giftcard", "gift card", "itunes", "steam", "amazon card")):
            return (
                f"Gift card — which type and face value? You fit send photo of the card when ready."
                if is_pidgin else
                f"Gift card — which brand and face value? You can send a clear photo of the card when ready."
            )
        if any(x in low for x in ("bank", "transfer", "wire", "ach")):
            return (
                f"Bank transfer — amount, currency, and country? I go share the right details if enabled."
                if is_pidgin else
                f"Bank transfer — amount, currency, and country? I will share the correct details if that method is enabled."
            )
        return (
            f"I dey for *{name}*. You fit use PayPal, bank, Cash App, crypto, gift card… wetin you wan do, and how much?"
            if is_pidgin else
            f"I can help at *{name}* with PayPal, bank, Cash App, crypto, gift cards, and more. What do you need and how much?"
        )

    # ── Laundry ──
    if btype == "laundry":
        if any(x in low for x in ("wash", "laundry", "dry clean", "dry-clean", "iron")):
            return (
                f"Laundry for *{name}* — wetin you get (clothes, duvet…)? Express or normal, and when you wan pickup?"
                if is_pidgin else
                f"Laundry at *{name}* — what items, normal or express, and preferred pickup/delivery time?"
            )
        return (
            f"Welcome to *{name}* laundry. Tell me your items and if you need pickup."
            if not is_pidgin else
            f"*{name}* laundry dey available. Tell me wetin you get and if you need pickup."
        )

    # ── Phone repair ──
    if btype in ("phone_repair", "phone"):
        return (
            f"Phone repair for *{name}* — which phone model, and wetin spoil (screen, battery, charging…)?"
            if is_pidgin else
            f"Phone repair at *{name}* — which model, and what is the issue (screen, battery, charging…)?"
        )

    # ── Real estate ──
    if btype in ("real_estate", "realestate"):
        return (
            f"*{name}* real estate — you dey look rent, buy, or inspection? Area and budget?"
            if is_pidgin else
            f"*{name}* — are you looking to rent, buy, or book an inspection? Share area and budget."
        )

    # ── Skincare ──
    if btype == "skincare":
        return (
            f"*{name}* skincare — you need product advice, consultation, or to book appointment?"
            if not is_pidgin else
            f"*{name}* skincare — product advice, consultation, or appointment?"
        )

    # ── Fashion ──
    if btype == "fashion":
        return (
            f"*{name}* — tailoring, fabric, or ready-to-wear? Tell me what you need."
            if not is_pidgin else
            f"*{name}* fashion — tailoring, fabric, or ready-to-wear? Wetin you wan?"
        )

    # ── Restaurant ──
    if btype == "restaurant":
        return (
            f"*{name}* — you wan see menu, place order, or ask delivery?"
            if not is_pidgin else
            f"*{name}* — menu, order, or delivery? Talk am."
        )

    # ── Retail / logistics / education ──
    if btype == "retail":
        return f"*{name}* — which product are you looking for?"
    if btype == "logistics":
        return f"*{name}* logistics — pickup location, drop-off, and package size?"
    if btype == "education":
        return f"*{name}* — which course or service do you need info on?"

    if is_pidgin:
        return (
            f"I hear you — *{name}* dey online.\n"
            "Abeg tell me wetin you need clearly and I go sort you."
        )
    return (
        f"Thanks for messaging *{name}*.\n"
        "Tell me what you need and I will help you right away."
    )



def _company_system_prompt(company) -> str:
    try:
        from app.data.business_templates import build_system_prompt
        return build_system_prompt(company, getattr(company, "business_type", None))
    except Exception as e:
        print("build_system_prompt_fail", e)
        return SYSTEM_PROMPT.replace("a real print shop", getattr(company, "name", "this business") or "this business")


SYSTEM_PROMPT = """You are Client RaQ, the WhatsApp customer-service agent for this business.

You sound like a helpful human staff member — warm, concise, professional, a bit funny when it fits.
Never say you are an AI or Grok.

CONVERSATION MEMORY:
- Read the full recent chat history before replying.
- Do not re-ask facts already given (size, qty, service, pickup, payment).
- If the customer corrects something, update and confirm.
- Stay on the current order until it is done or they clearly start a new one.

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
    try:
        from app.data.languages import parse_enabled_languages, language_system_block
        enabled = parse_enabled_languages(
            getattr(company, "enabled_languages", None),
            getattr(company, "bot_language", None),
        )
        parts.append(language_system_block(enabled))
    except Exception as e:
        print("lang_block_fail", e)
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
                messages.append({"role": role, "content": body[:1200]})
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
