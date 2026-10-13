"""Client RaQ AI agent — Grok/xAI with tools + natural rule fallbacks."""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.config import get_settings

log = logging.getLogger("client_raq.agent")

def _extract_size_qty(msg: str):
    """Return (w, h, qty, unit) from free text if present."""
    import re
    low = (msg or "").lower()
    w = h = qty = None
    unit = None
    m = re.search(r"(\d+(?:\.\d+)?)\s*[x×*]\s*(\d+(?:\.\d+)?)", low)
    if m:
        w, h = float(m.group(1)), float(m.group(2))
    m = re.search(r"(?:of\s+)?(\d+)\s*(units?|pcs?\.?|pieces?|qty|quantity)\b", low)
    if not m:
        m = re.search(r"(\d+)(pcs|pieces|pc)\b", low)
    if m:
        qty = max(1, int(m.group(1)))
    if "inch" in low:
        unit = "in"
    elif any(x in low for x in ("ft", "feet", "foot")):
        unit = "ft"
    return w, h, qty, unit






def _escalate_human(is_pidgin: bool = True) -> str:
    if is_pidgin:
        return "Hold on small — I go holla my person make e confirm this one for you. I go message you back sharp."
    return "Give me a moment — I will check with the team and get back to you shortly."

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
    # Continue active quote only when message is NOT a new service / clear new request / pure greet
    _new_service = any(
        x in low
        for x in (
            "sticker", "banner", "nylon", "frame", "flex", "jotter", "cloth", "sav",
            "paypal", "cashapp", "giftcard", "crypto", "laundry", "repair",
            "new order", "another", "different", "start over", "start again",
        )
    )
    _pure_greet = (
        len(words) <= 8
        and bool(words & {"hi", "hello", "hey", "haffa", "howfar", "bros", "boss", "guy", "morning", "evening"})
        or any(x in low for x in ("how e be", "how é", "how far", "bad guy", "my guy", "wetin dey"))
    ) and not any(x in low for x in ("sticker", "sav", "banner", "size", "piece", "inch")) and not any(c.isdigit() for c in low)
    if _pure_greet:
        # allow normal greeting path — do not force "still on SAV"
        pass
    elif (ctx.get("service_name") or ctx.get("service_id") or ctx.get("width") or ctx.get("quote_locked")) and not _new_service:
        # ALWAYS parse THIS message for size/qty before asking again
        w, h, qty, unit = _extract_size_qty(msg)
        if w and h and not ctx.get("quote_locked"):
            unit = unit or ("in" if "inch" in low else "ft" if any(x in low for x in ("ft", "feet")) else ctx.get("size_unit") or "in")
            q = qty or ctx.get("qty") or 1
            if is_pidgin:
                return f"Oya I get am — *{w:g}×{h:g} {unit}* × {q} pcs. Make I check the price for you sharp sharp."
            return f"Got it — *{w:g}×{h:g} {unit}* × {q} pcs. Checking the price for you now."

        # --- Answer ANY question while order is open (rate, terms, finishing) ---
        svc = ctx.get("service_name") or "your order"
        rate = ctx.get("unit_rate") or ctx.get("base_price")
        rate_unit = ctx.get("rate_unit") or ctx.get("size_unit") or "sq ft"
        cur = getattr(company, "currency", None) or "NGN"

        # Square foot / rate / how much per
        if any(x in low for x in (
            "square foot", "sq ft", "sqft", "per foot", "per ft", "rate",
            "how much per", "wetin be the price", "wetin be the square",
            "how much for one", "unit price", "base price", "per square",
        )):
            if rate:
                if is_pidgin:
                    return (
                        f"For *{svc}*, the rate na *{cur} {float(rate):,.0f}* per {rate_unit}.\n"
                        f"Your own ({ctx.get('width') or '?'}×{ctx.get('height') or '?'} "
                        f"{ctx.get('size_unit') or ''} × {ctx.get('qty') or 1}) "
                        f"total na *{cur} {float(ctx.get('locked_total') or ctx.get('total') or 0):,.0f}*."
                    )
                return (
                    f"For *{svc}*, the rate is *{cur} {float(rate):,.0f}* per {rate_unit}.\n"
                    f"Your job total is *{cur} {float(ctx.get('locked_total') or ctx.get('total') or 0):,.0f}*."
                )
            if ctx.get("locked_total") and ctx.get("width") and ctx.get("height"):
                area = float(ctx["width"]) * float(ctx["height"]) * float(ctx.get("qty") or 1)
                if area > 0:
                    implied = float(ctx["locked_total"]) / area
                    if is_pidgin:
                        return f"Based on your quote, e work out about *{cur} {implied:,.0f}* per {rate_unit}. Total still *{cur} {float(ctx['locked_total']):,.0f}*."
                    return f"Based on your quote, that is about *{cur} {implied:,.0f}* per {rate_unit}. Total remains *{cur} {float(ctx['locked_total']):,.0f}*."
            if is_pidgin:
                return "Abeg hold on — I go confirm the exact rate from the price list and tell you."
            return "One moment — I will confirm the exact rate from our price list."

        # Eyelets explanation + offer
        if any(x in low for x in ("eyelet", "eyelets", "grommet", "hole for hang", "hang hole")):
            if is_pidgin:
                return (
                    "Eyelets na those small metal rings wey we put for the four corners of the banner "
                    "so you fit hang am with rope or cable tie.\n"
                    "You want eyelets for this job? If yes I go add the finishing fee."
                )
            return (
                "Eyelets are the small metal rings on the corners of a banner so you can hang it with rope or ties.\n"
                "Do you want eyelets on this job? I can add the finishing fee if yes."
            )

        # Whiteout
        if any(x in low for x in ("whiteout", "white out", "blockout", "opaque", "see through")):
            if is_pidgin:
                return (
                    "Whiteout (blockout) na backing wey make the banner no transparent — "
                    "print no go show for the back.\nYou need whiteout for this one?"
                )
            return (
                "Whiteout (blockout) is a backing so the banner is not see-through — "
                "the print does not show from the back.\nDo you need whiteout on this job?"
            )

        # Print and cut / sticker cutting
        if any(x in low for x in ("cut", "cutting", "print and cut", "print & cut", "kiss cut", "die cut")):
            cut_fee = ctx.get("cut_fee") or 1000
            if is_pidgin:
                return (
                    "Print and cut mean we go cut the sticker shape for you (no just print for sheet).\n"
                    f"Cutting fee usually start from about *{cur} {float(cut_fee):,.0f}* depending on quantity.\n"
                    "You want us to cut the stickers for you?"
                )
            return (
                "Print and cut means we cut each sticker shape for you (not only print on a sheet).\n"
                f"Cutting usually starts around *{cur} {float(cut_fee):,.0f}* depending on quantity.\n"
                "Do you want us to cut the stickers?"
            )

        # Total again
        if any(x in low for x in ("total", "how much", "price", "cost", "amount", "ego", "how much be")):
            if ctx.get("locked_total"):
                if is_pidgin:
                    return (
                        f"Your total still stand *{cur} {float(ctx['locked_total']):,.0f}* "
                        f"for *{svc}* ({ctx.get('width') or '?'}×{ctx.get('height') or '?'} "
                        f"{ctx.get('size_unit') or ''} × {ctx.get('qty') or 1}).\n"
                        "You wan *pickup* or *delivery*?"
                    )
                return (
                    f"Your total is *{cur} {float(ctx['locked_total']):,.0f}* "
                    f"for *{svc}*.\nPickup or delivery?"
                )

        if not (ctx.get("width") and ctx.get("height")):
            if is_pidgin:
                return "Abeg send the size like *5x2* and how many pieces (inches or feet)."
            return "Please send the size like *5x2* and how many pieces (inches or feet)."
        if not ctx.get("quote_locked"):
            if is_pidgin:
                return "I don get the size. Confirm quantity or say *quote* make I give price."
            return "I have the size. Confirm quantity or say *quote* for the total."
        # Quote open — stay helpful, never generic greet
        if is_pidgin:
            return (
                f"Your *{svc}* quote still dey: *{cur} {float(ctx.get('locked_total') or 0):,.0f}*.\n"
                "You fit ask about rate, eyelets, whiteout, cutting — or choose *pickup* / *delivery*."
            )
        return (
            f"Your *{svc}* quote is *{cur} {float(ctx.get('locked_total') or 0):,.0f}*.\n"
            "Ask about rate, eyelets, whiteout, cutting — or choose pickup / delivery."
        )

    # "??" / "?" alone is follow-up confusion, NOT a greeting
    is_confused = low in ("?", "??", "???", "hello?", "wetin?", "wetin", "eh?", "abi?")
    is_greet = (not is_confused) and (
        bool(words & {"hi", "hello", "hey", "haffa", "howfar", "sup", "yo", "morning", "evening", "afternoon", "brother", "bros", "boss", "blood"})
        or low in ("hi", "hello", "hey", "haffa", "how far", "haffa blood", "how far my oga", "how e be")
    )
    btype = (getattr(company, "business_type", None) or "printing").strip().lower().replace(" ", "_").replace("-", "_")


    if is_confused:
        if ctx.get("service_name"):
            return (
                "I still dey on that order — abeg drop size like *5x2* and how many pieces."
                if is_pidgin else
                "Still on that order — please send size like *5x2* and quantity."
            )
        return (
            "I dey here. Wetin you wan do today?"
            if is_pidgin else
            "I'm here. What do you need today?"
        )

    if is_greet and is_pidgin:
        return "How far my guy! Wetin you wan do today — I dey here."
    if is_greet:
        return "Hey! How can I help you today? Just tell me what you need."

    # ── Printing ──
    if btype in ("printing", "print"):
        # If they already named the product (sav/sticker/banner), do not ask "what exactly"
        if any(x in low for x in ("print something", "wan print", "i wan print", "print am", "something jare")):
            if any(x in low for x in ("sav", "sticker", "label", "vinyl", "decal")):
                w, h, qty, unit = _extract_size_qty(msg)
                if w and h:
                    unit = unit or "in"
                    qbit = f" × {qty} pieces" if qty else ""
                    return (
                        f"Oya SAV/sticker *{w:g}×{h:g} {unit}*{qbit} — I go check the price now."
                        if is_pidgin else
                        f"SAV/sticker *{w:g}×{h:g} {unit}*{qbit} noted. Checking the total now."
                    )
                return (
                    f"Oya sticker! Abeg tell me size (width x height) and how many pieces. Inches or feet?"
                    if is_pidgin else
                    f"Sticker — please share size (width x height) and quantity. Inches or feet?"
                )
            if any(x in low for x in ("banner", "flex", "nylon", "frame")):
                pass  # fall through to specific branches below
            else:
                return (
                    f"No wahala — we fit print am.\nWetin exactly: sticker, banner, nylon, frame, or another thing?"
                    if is_pidgin or "jare" in low or "wan" in low else
                    f"Sure — we can help.\nWhat would you like to print: sticker, banner, nylon, frame, or something else?"
                )

        if any(x in low for x in ("sticker", "sav", "label")):
            w, h, qty, unit = _extract_size_qty(msg)
            if w and h:
                unit = unit or "in"
                qbit = f" × {qty} pieces" if qty else ""
                return (
                    f"Oya! Sticker *{w:g}×{h:g} {unit}*{qbit} for *{name}*.\n"
                    f"I go confirm the total now — you wan pickup or delivery after the quote?"
                    if is_pidgin else
                    f"Sticker *{w:g}×{h:g} {unit}*{qbit} noted for *{name}*.\n"
                    f"I'll confirm the total — pickup or delivery after the quote?"
                )
            return (
                f"Oya sticker! Abeg tell me size (width x height) and how many pieces. Inches or feet?"
                if is_pidgin else
                f"Sticker — please share size (width x height) and quantity. Inches or feet?"
            )
        if "nylon" in low:
            return (
                f"Nylon — how many pieces you wan, and e go get custom print?"
                if is_pidgin else
                f"Nylon bags — how many pieces, and do you need custom branding?"
            )
        if any(x in low for x in ("banner", "flex", "rollup", "roll-up")):
            w, h, qty, unit = _extract_size_qty(msg)
            if w and h:
                unit = unit or "ft"
                qbit = f" × {qty}" if qty else ""
                return (
                    f"Banner *{w:g}×{h:g} {unit}*{qbit} — I go confirm price for *{name}*."
                    if is_pidgin else
                    f"Banner *{w:g}×{h:g} {unit}*{qbit} noted — confirming price for *{name}*."
                )
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

    # Last resort — short, human, no brand spam
    if is_pidgin:
        return "I hear you. Wetin you wan do — or ask me any question about the job."
    return "Got it. What do you need, or ask me any question about the job."



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
    try:
        s = get_settings()
        return (getattr(s, "GROK_API_KEY", None) or getattr(s, "XAI_API_KEY", None) or "").strip()
    except Exception as e:
        log.warning("get_settings api_key fail %s", e)
        return ""


def _model() -> str:
    try:
        s = get_settings()
        return (getattr(s, "GROK_MODEL", None) or getattr(s, "XAI_MODEL", None) or "grok-4-fast-non-reasoning").strip()
    except Exception:
        return "grok-4-fast-non-reasoning"


def _base() -> str:
    try:
        s = get_settings()
        return (getattr(s, "GROK_BASE_URL", None) or "https://api.x.ai/v1").rstrip("/")
    except Exception:
        return "https://api.x.ai/v1"


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
    """Returns (reply_text, updated_ctx, needs_human). Never raises."""
    try:
        return await _run_agent_inner(
            db,
            company=company,
            conv=conv,
            ctx=ctx,
            from_wa=from_wa,
            customer_message=customer_message,
            recent=recent,
            attachment_note=attachment_note,
            max_tool_rounds=max_tool_rounds,
        )
    except Exception as e:
        log.warning("run_agent_outer_fail %s", type(e).__name__, exc_info=True)
        print("run_agent_outer_fail", type(e).__name__, e)
        try:
            return _rule_reply(company, customer_message, ctx), ctx, False
        except Exception:
            name = getattr(company, "name", None) or "us"
            return (f"Hey — *{name}* here. Tell me what you need.", ctx, False)


async def _run_agent_inner(
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
        for m in recent[-16:]:
            role = "assistant" if m.get("direction") == "outbound" else "user"
            body = (m.get("body") or "").strip()
            if body:
                messages.append({"role": role, "content": body[:2000]})
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
    user_blob += "\n\nRULES: Never restart with a welcome if conversation already started. Never invent prices. Use tools for price/services. Short WhatsApp replies. If customer said sticker/banner/etc, continue that flow. If message is ?? or short, use history."
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
