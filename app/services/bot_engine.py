"""Human-like printing WhatsApp bot: buttons, Grok NLU, sq-ft pricing, staff notify."""
from __future__ import annotations
import json
import re
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.company import Company, PaymentDetail, CompanyWhatsAppNumber
from app.models.catalog import Service
from app.models.conversation import Conversation, Message, Order, OrderStatus
from app.services.grok_client import grok_chat, grok_vision
from app.services.whatsapp_send import send_text, send_buttons, send_list, download_media


def _ctx(conv: Conversation) -> dict:
    try:
        return json.loads(getattr(conv, "context_json", None) or "{}")
    except Exception:
        return {}


def _save_ctx(conv: Conversation, data: dict) -> None:
    conv.context_json = json.dumps(data)


def _lang(ctx: dict) -> str:
    return (ctx.get("lang") or "en").lower()


def _t(ctx: dict, en: str, pid: str) -> str:
    return pid if _lang(ctx) == "pidgin" else en


def _greet(company: Company) -> str:
    if company.greeting_message and company.greeting_message.strip():
        return company.greeting_message.strip()
    return f"Hi! Welcome to *{company.name}*. We're glad you're here."


def _unit_mode(unit: str | None) -> str:
    u = (unit or "").lower()
    if any(x in u for x in ("piece", "pcs", "each", "frame", "copy", "per pc")):
        return "piece"
    if any(x in u for x in ("sqm", "m2", "metre", "meter")):
        return "sqm"
    if any(x in u for x in ("sq", "square", "ft", "feet", "foot", "sqft")):
        return "sqft"
    if "ft" in u or "feet" in u:
        return "sqft"
    return "piece"


def _size_in_inches(text: str) -> bool:
    low = (text or "").lower()
    return any(x in low for x in ("inch", "inches", " in", "in ", '"'))


def _parse_size(text: str):
    low = (text or "").lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*[x×*]\s*(\d+(?:\.\d+)?)", low)
    if not m:
        m = re.search(r"(\d+(?:\.\d+)?)\s*by\s*(\d+(?:\.\d+)?)", low)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None, None


def _parse_qty(text: str):
    low = (text or "").lower()
    m = re.search(r"(\d+)\s*(unit|units|pcs|pc|piece|pieces|qty|quantity|copy|copies)\b", low)
    if m:
        return max(1, int(m.group(1)))
    if re.fullmatch(r"\d+", (text or "").strip()):
        return max(1, int(text.strip()))
    return None


def _calc_total(service, width, height, qty, size_text: str = "") -> float:
    base = float(service.base_price or 0)
    qty = max(1, int(qty or 1))
    mode = getattr(service, 'pricing_method', None) or _unit_mode(service.unit)
    if mode in ('sqin',):
        mode = 'sqft'  # handled with inches below via size_text
    if mode in ('custom', 'setup_unit', 'tier'):
        mode = _unit_mode(service.unit)
    if mode == "piece" or not width or not height:
        return round(base * qty, 2)
    if mode == "sqft" and _size_in_inches(size_text or ""):
        return round((width * height * qty * base) / 144.0, 2)
    return round(width * height * base * qty, 2)


def _is_clarifying_question(text: str) -> bool:
    low = (text or "").lower().strip()
    if not low:
        return False
    if "?" in low:
        return True
    triggers = (
        "what do you mean", "wetin you mean", "what is unit", "what's unit", "whats unit",
        "mean by unit", "explain", "how much", "i don't understand", "i dont understand",
        "i no understand", "abeg explain", "clarify", "what does", "wetin be",
    )
    return any(x in low for x in triggers)


def _is_sqft_unit(unit: str | None) -> bool:
    return _unit_mode(unit) in ("sqft", "sqm")


async def _services(db: AsyncSession, company: Company) -> list:
    return list((await db.execute(
        select(Service).where(Service.company_id == company.id, Service.is_active == True)  # noqa: E712
    )).scalars().all())


async def _bank(db: AsyncSession, company: Company) -> str:
    rows = (await db.execute(select(PaymentDetail).where(PaymentDetail.company_id == company.id))).scalars().all()
    if not rows:
        return "Please ask our staff for bank details."
    parts = []
    for p in rows:
        b = f"*{p.bank_name}*\nAccount name: {p.account_name}\nAccount number: {p.account_number}"
        if p.instructions:
            b += f"\n{p.instructions}"
        parts.append(b)
    return "\n\n".join(parts)


async def _reply(
    link: CompanyWhatsAppNumber,
    to: str,
    text: str | None = None,
    buttons: list[tuple[str, str]] | None = None,
    list_rows: list[tuple[str, str, str]] | None = None,
    list_body: str | None = None,
    list_btn: str = "View options",
    header: str | None = None,
) -> None:
    pid, tok = link.phone_number_id, link.access_token or ""
    if buttons and text:
        ok = await send_buttons(pid, tok, to, text, buttons, header=header)
        if not ok and text:
            await send_text(pid, tok, to, text)
        return
    if list_rows and list_body:
        ok = await send_list(pid, tok, to, list_body, list_btn, list_rows, header=header)
        if not ok:
            fallback = list_body + "\n\n" + "\n".join(f"• {t}" for _, t, _ in list_rows)
            await send_text(pid, tok, to, fallback)
        return
    if text:
        await send_text(pid, tok, to, text)


async def _send_lang_prompt(link, company, to, greet: str) -> None:
    body = (
        f"{greet}\n\n"
        f"*{company.name}*\n"
        "Which language do you prefer?"
    )
    await _reply(
        link, to, body,
        buttons=[("lang_en", "English"), ("lang_pidgin", "Pidgin")],
    )


async def _send_services(link, company, services, to, ctx: dict) -> None:
    header = company.name[:60]
    intro = _t(
        ctx,
        f"Here are services from *{company.name}*. Tap one, or ask anything in your own words.",
        f"See the services we dey do for *{company.name}*. Press one, or just type wetin you need.",
    )
    if not services:
        await send_text(
            link.phone_number_id, link.access_token or "", to,
            intro + "\n\n" + _t(ctx, "Catalog is updating — describe what you need.", "Catalog still dey update — just talk wetin you need."),
        )
        return
    rows = []
    for s in services[:9]:
        price = f"{company.currency} {s.base_price:,.0f}/{s.unit}"
        rows.append((f"svc_{s.id}", s.name[:24], price[:72]))
    rows.append(("svc_enquiry", "Enquiry / Other", _t(ctx, "Ask us anything", "Ask any question")[:72]))
    await _reply(
        link, to,
        list_body=intro,
        list_btn=_t(ctx, "Our services", "Our services"),
        list_rows=rows,
        header=header,
    )


def _detect_customer_lang(text: str, ctx: dict) -> str:
    """Match customer vibe: pidgin markers vs formal English."""
    low = (text or "").lower()
    pidgin_markers = (
        "wetin", "abeg", "dey", "wan ", "una", "na ", "how far", "o!", "sha",
        "make i", "i go", "e be", "no wahala", "sharp", "bros", "oga", "pls abeg",
        "i dey", "you dey", "wetin you", "how much be",
    )
    if any(m in low for m in pidgin_markers):
        return "pidgin"
    # if company forced pidgin only
    if ctx.get("lang") == "pidgin" and not text:
        return "pidgin"
    # good English / neutral -> english unless forced pidgin
    if ctx.get("lang") == "pidgin":
        # still allow English if they wrote proper English
        if len(low) > 12 and not any(m in low for m in pidgin_markers):
            return "en"
        return "pidgin"
    return "en"


async def _grok_staff(
    company: Company,
    ctx: dict,
    user_text: str,
    services: list,
    extra: str = "",
) -> str | None:
    lang = _detect_customer_lang(user_text, ctx)
    ctx["lang"] = lang  # keep conversation aligned to latest vibe
    catalog = ", ".join(
        f"{s.name} ({company.currency} {s.base_price}/{s.unit}, method={getattr(s, 'pricing_method', 'piece')})"
        for s in services
    ) or "none listed yet"
    system = (
        f"You are a real customer-care staff at {company.name}, a printing and branding shop. "
        "Never say you are an AI or a bot. "
        f"Reply in {'Nigerian Pidgin English (natural, short)' if lang == 'pidgin' else 'clear, friendly professional English'}. "
        "Match the customer's tone: if they wrote casually, be warm; if formal, be professional. "
        "Keep replies short (1-4 sentences). "
        f"Services and rates we actually offer: {catalog}. "
        "Only quote prices using those rates. For area pricing: feet use W×H×rate×qty; "
        "inches with per-sq-ft rate use (W×H×qty×rate)/144. Per piece = rate×qty. "
        "Unit means how many copies of the same job. "
        "If unsure of size or quantity, ask one clear question. "
        f"Company note: {(company.greeting_message or '')[:180]}. {extra}"
    )
    return await grok_chat(system, user_text)


def _match_service(text: str, services: list) -> Service | None:
    low = text.lower()
    for s in services:
        name = s.name.lower()
        if name in low or any(w in low for w in name.split() if len(w) > 3):
            return s
    keywords = {
        "banner": ["banner", "flex", "signboard"],
        "sticker": ["sticker", "label", "sav"],
        "frame": ["frame", "picture frame"],
        "cloth": ["cloth", "tshirt", "t-shirt", "polo", "hoodie"],
        "jotter": ["jotter", "notebook"],
        "pen": ["pen", "biro"],
        "nylon": ["nylon", "polybag"],
    }
    for s in services:
        key = s.name.lower()
        for k, words in keywords.items():
            if k in key and any(w in low for w in words):
                return s
    return None


async def notify_order_status(
    db: AsyncSession,
    order: Order,
    status: str,
    note: str | None = None,
) -> None:
    """Called when company admin updates order status — message the customer."""
    link = (await db.execute(
        select(CompanyWhatsAppNumber).where(
            CompanyWhatsAppNumber.company_id == order.company_id,
            CompanyWhatsAppNumber.is_active == True,  # noqa: E712
        )
    )).scalars().first()
    if not link or not link.access_token or not order.customer_wa_id:
        return
    company = await db.get(Company, order.company_id)
    name = (company.name if company else "us")
    st = status.lower().replace(" ", "_")
    msg = None
    if st in ("payment_confirmed", "confirmed", "accepted"):
        msg = (
            f"Good news from *{name}* ✅\n"
            f"Your payment for order *#{order.id}* has been received. Thank you!\n"
            f"We'll start working on it shortly."
            + (f"\n\nNote: {note}" if note else "")
        )
    elif st in ("in_production", "production"):
        msg = f"*{name}*: Your order *#{order.id}* is now in production."
    elif st in ("ready", "ready_for_pickup", "completed"):
        msg = (
            f"*{name}*: Your order *#{order.id}* is ready.\n"
            f"Please message us before you leave for pickup so we confirm someone is available."
            + (f"\n\n{note}" if note else "")
        )
    elif st in ("rejected", "cancelled"):
        msg = f"*{name}*: Update on order *#{order.id}*: {note or 'Please contact us for details.'}"
    elif st == "on_hold":
        msg = f"*{name}*: Please hold for a bit on order *#{order.id}*. {note or 'We will message you shortly.'}"
    elif note:
        msg = f"*{name}* (order #{order.id}): {note}"
    if msg:
        await send_text(link.phone_number_id, link.access_token, order.customer_wa_id, msg)


async def handle_inbound(
    db: AsyncSession,
    *,
    phone_number_id: str,
    from_wa: str,
    text: str | None,
    media_id: str | None = None,
    button_id: str | None = None,
    list_id: str | None = None,
) -> None:
    link = (await db.execute(
        select(CompanyWhatsAppNumber).where(
            CompanyWhatsAppNumber.phone_number_id == phone_number_id,
            CompanyWhatsAppNumber.is_active == True,  # noqa: E712
        )
    )).scalar_one_or_none()
    if not link or not link.access_token:
        return
    company = await db.get(Company, link.company_id)
    if not company:
        return
    st = company.status if isinstance(company.status, str) else getattr(company.status, "value", "active")
    if st == "suspended":
        return

    interactive_id = button_id or list_id
    text = (text or "").strip()
    display_in = text or interactive_id or ("[image]" if media_id else "[media]")

    services = await _services(db, company)

    conv = (await db.execute(
        select(Conversation).where(
            Conversation.company_id == company.id,
            Conversation.customer_wa_id == from_wa,
        )
    )).scalar_one_or_none()
    if not conv:
        conv = Conversation(company_id=company.id, customer_wa_id=from_wa, state="await_lang")
        db.add(conv)
        await db.flush()

    db.add(Message(
        conversation_id=conv.id,
        direction="inbound",
        body=display_in,
        media_url=media_id,
    ))
    await db.flush()

    if getattr(conv, "is_live_takeover", False):
        await db.commit()
        return

    ctx = _ctx(conv)
    low = text.lower()
    state = conv.state or "await_lang"
    company_lang = (getattr(company, "bot_language", None) or "both").lower()
    if company_lang in ("en", "pidgin") and not ctx.get("lang"):
        ctx["lang"] = company_lang
        _save_ctx(conv, ctx)

    # --- Coming for pickup notifications ---
    if any(p in low for p in ("on my way", "i dey come", "coming for", "coming to pick", "i'm coming", "im coming")):
        order = (await db.execute(
            select(Order).where(
                Order.company_id == company.id,
                Order.customer_wa_id == from_wa,
            ).order_by(Order.id.desc())
        )).scalars().first()
        note = f"Customer {from_wa} says they are coming for order #{order.id if order else '?'}."
        if order:
            order.status_note = (order.status_note or "") + f"\n{note}"
        reply = _t(
            ctx,
            "Thanks for letting us know. We'll confirm with the team. If anything changes, we'll message you here.",
            "Thanks as you tell us. We go confirm with the team. If anything change, we go message you.",
        )
        db.add(Message(conversation_id=conv.id, direction="outbound", body=reply))
        await db.commit()
        await send_text(link.phone_number_id, link.access_token, from_wa, reply)
        return

    # --- Payment screenshot ---
    if media_id and state in ("await_payment", "await_proof", "await_service", "open"):
        ctx["payment_proof"] = media_id
        _save_ctx(conv, ctx)
        # vision optional
        vision_note = ""
        try:
            blob = await download_media(media_id, link.access_token)
            if blob:
                content, mime = blob
                vision_note = (await grok_vision(
                    "You check payment receipts for a print shop.",
                    "Briefly confirm if this looks like a payment/transfer receipt. One short sentence.",
                    content, mime,
                )) or ""
        except Exception:
            pass
        total = float(ctx.get("total") or 0)
        order = Order(
            company_id=company.id,
            conversation_id=conv.id,
            customer_wa_id=from_wa,
            service_name=ctx.get("service_name"),
            details=ctx.get("details"),
            total_amount=total,
            currency=company.currency,
            status=OrderStatus.PAYMENT_SUBMITTED if hasattr(OrderStatus, "PAYMENT_SUBMITTED") else OrderStatus.AWAITING_ACCEPTANCE,
            payment_proof_url=media_id,
            status_note=vision_note[:500] if vision_note else None,
        )
        # fill optional columns if exist
        for attr, val in (
            ("fulfillment", ctx.get("fulfillment")),
            ("scheduled_date", ctx.get("date")),
            ("scheduled_time", ctx.get("time")),
            ("delivery_address", ctx.get("address")),
            ("delivery_fee", ctx.get("delivery_fee") or 0),
            ("subtotal", ctx.get("subtotal") or total),
        ):
            if hasattr(order, attr):
                setattr(order, attr, val)
        db.add(order)
        await db.flush()
        conv.state = "order_placed"
        reply = _t(
            ctx,
            f"Payment proof received for *{company.currency} {total:,.0f}*. Our team will confirm shortly. Order ref *#{order.id}*.",
            f"We don collect your payment proof for *{company.currency} {total:,.0f}*. Team go confirm am sharp. Order *#{order.id}*.",
        )
        db.add(Message(conversation_id=conv.id, direction="outbound", body=reply))
        await db.commit()
        await send_text(link.phone_number_id, link.access_token, from_wa, reply)
        return

    # --- Clarifying questions: answer without advancing the order ---
    if text and _is_clarifying_question(text) and state not in ("await_lang",):
        extra = ""
        if "unit" in text.lower():
            extra = (
                "Explain that unit means how many copies of the same print job. "
                "Example: 5 units = 5 of the same size. Do not move to payment."
            )
        grok = await _grok_staff(company, ctx, text, services, extra=extra)
        if not grok:
            if "unit" in text.lower():
                grok = _t(
                    ctx,
                    "Unit means how many of the *same* item you want. "
                    "Example: one banner size, ordered 5 times = *5 units*.",
                    "Unit na how many of that *same* thing you want. "
                    "Example: same banner size, you need 5 = *5 units*.",
                )
            else:
                grok = _t(ctx, "Happy to explain — what should I clarify?", "Which part you want make I explain?")
        db.add(Message(conversation_id=conv.id, direction="outbound", body=grok))
        await db.commit()
        await send_text(link.phone_number_id, link.access_token, from_wa, grok)
        return

    # ========== STATE MACHINE ==========
    outbound = None

    # Language selection
    if interactive_id in ("lang_en", "lang_pidgin") or state == "await_lang":
        if interactive_id == "lang_en" or low in ("english", "en"):
            ctx["lang"] = "en"
            _save_ctx(conv, ctx)
            conv.state = "await_intent"
            await db.commit()
            await _send_services(link, company, services, from_wa, ctx)
            return
        if interactive_id == "lang_pidgin" or low in ("pidgin", "pidgin english", "naija"):
            ctx["lang"] = "pidgin"
            _save_ctx(conv, ctx)
            conv.state = "await_intent"
            await db.commit()
            await _send_services(link, company, services, from_wa, ctx)
            return
        if state == "await_lang" or not ctx.get("lang"):
            company_lang = (getattr(company, "bot_language", None) or "both").lower()
            if company_lang in ("en", "pidgin"):
                ctx["lang"] = company_lang
                _save_ctx(conv, ctx)
                conv.state = "await_intent"
                await db.commit()
                await _send_services(link, company, services, from_wa, ctx)
                return
            conv.state = "await_lang"
            greet = _greet(company)
            db.add(Message(conversation_id=conv.id, direction="outbound", body=greet))
            await db.commit()
            await _send_lang_prompt(link, company, from_wa, greet)
            return

    # Service picked from list
    if interactive_id and interactive_id.startswith("svc_"):
        if interactive_id == "svc_enquiry":
            conv.state = "await_enquiry"
            outbound = _t(
                ctx,
                "Sure — type your question. We'll answer from here or a staff member will follow up.",
                "No problem — type your question. We go answer or staff go message you.",
            )
        else:
            try:
                sid = int(interactive_id.replace("svc_", ""))
            except ValueError:
                sid = None
            chosen = next((s for s in services if s.id == sid), None)
            if chosen:
                ctx.update({
                    "service_id": chosen.id,
                    "service_name": chosen.name,
                    "base_price": chosen.base_price,
                    "unit": chosen.unit,
                })
                _save_ctx(conv, ctx)
                conv.state = "await_details"
                if _is_sqft_unit(chosen.unit):
                    outbound = _t(
                        ctx,
                        f"*{chosen.name}* — {company.currency} {chosen.base_price:,.0f} per square foot.\n"
                        f"Tell me the size (e.g. *3x5*) and how many *units* (how many copies).\n"
                        f"Example: *3x5, 5 units*",
                        f"*{chosen.name}* — {company.currency} {chosen.base_price:,.0f} per square foot.\n"
                        f"Tell me the size (e.g. *3x5*) and how many *units* (how many copies you need).\n"
                        f"Example: *3x5, 5 units*",
                    )
                else:
                    outbound = _t(
                        ctx,
                        f"*{chosen.name}* — {company.currency} {chosen.base_price:,.0f} / {chosen.unit}.\n"
                        f"Send details and how many *units* you need.\n"
                        f"'Unit' means how many of the same item.",
                        f"*{chosen.name}* — {company.currency} {chosen.base_price:,.0f} / {chosen.unit}.\n"
                        f"Send details and how many *units* you need.\n"
                        f"Unit na how many of that same thing you want.",
                    )

    # Free-text intent (Grok + rules) when browsing / after language
    elif state in ("await_intent", "await_service", "await_enquiry", "open", "order_placed") and text:
        if low in ("hi", "hello", "hey", "start", "menu", "services", "service"):
            conv.state = "await_intent"
            await db.commit()
            await _send_services(link, company, services, from_wa, ctx)
            return

        chosen = _match_service(text, services)
        if chosen:
            ctx.update({
                "service_id": chosen.id,
                "service_name": chosen.name,
                "base_price": chosen.base_price,
                "unit": chosen.unit,
                "details": text,
            })
            w, h = _parse_size(text)
            qty = _parse_qty(text) or 1
            if w and h:
                ctx["width"], ctx["height"] = w, h
            ctx["qty"] = qty
            total = _calc_total(chosen, ctx.get("width"), ctx.get("height"), qty, text)
            if w and h and _is_sqft_unit(chosen.unit):
                ctx["total"] = total
                ctx["subtotal"] = total
                _save_ctx(conv, ctx)
                conv.state = "await_fulfillment"
                outbound = _t(
                    ctx,
                    f"Got it — *{chosen.name}* {w:g}×{h:g} ft, {qty} unit(s).\n"
                    f"Amount: *{company.currency} {total:,.0f}* "
                    f"({w:g}×{h:g}×{chosen.base_price:,.0f}×{qty}).\n\n"
                    f"Pickup or delivery?",
                    f"I hear you — *{chosen.name}* {w:g}×{h:g} ft, {qty} unit(s).\n"
                    f"Price: *{company.currency} {total:,.0f}*.\n\n"
                    f"You go pickup or delivery?",
                )
                await _reply(
                    link, from_wa, outbound,
                    buttons=[("ful_pickup", "Pickup"), ("ful_delivery", "Delivery")],
                )
                db.add(Message(conversation_id=conv.id, direction="outbound", body=outbound))
                await db.commit()
                return
            _save_ctx(conv, ctx)
            conv.state = "await_details"
            grok = await _grok_staff(company, ctx, text, services)
            outbound = grok or _t(
                ctx,
                f"Understood — you're looking at *{chosen.name}*. Please send size and how many units.",
                f"I understand — you need *{chosen.name}*. Send size and how many units.",
            )
        else:
            grok = await _grok_staff(company, ctx, text, services)
            if grok:
                outbound = grok
                conv.state = "await_intent"
            else:
                outbound = _t(
                    ctx,
                    "Happy to help. You can tap a service below or just describe what you need.",
                    "I dey here to help. Press a service below or just type wetin you need.",
                )
                await db.commit()
                await send_text(link.phone_number_id, link.access_token, from_wa, outbound)
                await _send_services(link, company, services, from_wa, ctx)
                db.add(Message(conversation_id=conv.id, direction="outbound", body=outbound))
                await db.commit()
                return

    # Details / size / qty
    elif state == "await_details" and text:
        ctx["details"] = (ctx.get("details") or "") + " | " + text
        w, h = _parse_size(text)
        qty = _parse_qty(text)
        if w and h:
            ctx["width"], ctx["height"] = w, h
        if qty:
            ctx["qty"] = qty
        if not ctx.get("qty"):
            ctx["qty"] = 1
        svc = next((s for s in services if s.id == ctx.get("service_id")), None)
        if svc and _is_sqft_unit(svc.unit) and not (ctx.get("width") and ctx.get("height")):
            outbound = _t(
                ctx,
                "Please send the size like *3x5* (width x height in feet), and how many units.",
                "Abeg send size like *3x5* (width x height for feet), and how many units.",
            )
        elif svc:
            total = _calc_total(svc, ctx.get("width"), ctx.get("height"), int(ctx.get("qty") or 1), text)
            ctx["total"] = total
            ctx["subtotal"] = total
            _save_ctx(conv, ctx)
            conv.state = "await_fulfillment"
            size_bit = ""
            if ctx.get("width") and ctx.get("height"):
                size_bit = f"{ctx['width']:g}×{ctx['height']:g} "
            outbound = _t(
                ctx,
                f"*{svc.name}* {size_bit}× {ctx.get('qty')} unit(s)\nTotal: *{company.currency} {total:,.0f}*\n\nPickup or delivery?",
                f"*{svc.name}* {size_bit}× {ctx.get('qty')} unit(s)\nTotal: *{company.currency} {total:,.0f}*\n\nPickup or delivery?",
            )
            await _reply(link, from_wa, outbound, buttons=[("ful_pickup", "Pickup"), ("ful_delivery", "Delivery")])
            db.add(Message(conversation_id=conv.id, direction="outbound", body=outbound))
            await db.commit()
            return
        else:
            outbound = _t(ctx, "Please pick a service first.", "Abeg pick service first.")
            conv.state = "await_intent"

    # Fulfillment
    elif state == "await_fulfillment" or interactive_id in ("ful_pickup", "ful_delivery"):
        is_del = interactive_id == "ful_delivery" or any(w in low for w in ("deliver", "delivery", "send"))
        is_pick = interactive_id == "ful_pickup" or any(w in low for w in ("pickup", "pick up", "collect"))
        if not is_del and not is_pick:
            outbound = _t(ctx, "Please choose *Pickup* or *Delivery*.", "Choose *Pickup* or *Delivery*.")
            await _reply(link, from_wa, outbound, buttons=[("ful_pickup", "Pickup"), ("ful_delivery", "Delivery")])
            db.add(Message(conversation_id=conv.id, direction="outbound", body=outbound))
            await db.commit()
            return
        ctx["fulfillment"] = "delivery" if is_del else "pickup"
        _save_ctx(conv, ctx)
        if is_del:
            conv.state = "await_address"
            outbound = _t(ctx, "Please send the delivery address.", "Send the address we go deliver am.")
        else:
            conv.state = "await_datetime"
            outbound = _t(
                ctx,
                "What date and time for pickup? Example: *Tomorrow 2pm*",
                "Which day and time you go come pick am? Example: *Tomorrow 2pm*",
            )

    elif state == "await_address" and text:
        ctx["address"] = text
        fee = float(getattr(company, "delivery_fee_base", 0) or 0)
        ctx["delivery_fee"] = fee
        if fee:
            ctx["total"] = float(ctx.get("subtotal") or ctx.get("total") or 0) + fee
        _save_ctx(conv, ctx)
        conv.state = "await_datetime"
        outbound = _t(
            ctx,
            f"Address saved." + (f" Delivery fee: *{company.currency} {fee:,.0f}*." if fee else "")
            + "\nWhat date and time for delivery? Example: *25 Sept, 3pm*",
            f"Address don save." + (f" Delivery fee: *{company.currency} {fee:,.0f}*." if fee else "")
            + "\nWhich day and time for delivery? Example: *25 Sept, 3pm*",
        )

    elif state == "await_datetime" and text:
        # Ignore non-schedule messages (questions already handled above)
        if not any(x in low for x in (
            "am", "pm", "monday", "tuesday", "wednesday", "thursday", "friday",
            "saturday", "sunday", "tomorrow", "today", "next", "morning", "evening",
            "afternoon", ":", "/", "jan", "feb", "mar", "apr", "may", "jun",
            "jul", "aug", "sep", "oct", "nov", "dec", "week",
        )) and not re.search(r"\d", text):
            outbound = _t(
                ctx,
                "Please send the *date and time* for pickup/delivery. Example: *Tomorrow 2pm*",
                "Abeg send *day and time*. Example: *Tomorrow 2pm*",
            )
            db.add(Message(conversation_id=conv.id, direction="outbound", body=outbound))
            await db.commit()
            await send_text(link.phone_number_id, link.access_token, from_wa, outbound)
            return
        ctx["datetime"] = text
        parts = re.split(r"[,\-]| at ", text, maxsplit=1)
        ctx["date"] = parts[0].strip() if parts else text
        ctx["time"] = parts[1].strip() if len(parts) > 1 else ""
        _save_ctx(conv, ctx)
        conv.state = "await_payment"
        bank = await _bank(db, company)
        total = float(ctx.get("total") or 0)
        outbound = _t(
            ctx,
            f"Order summary:\n• {ctx.get('service_name')}\n• {ctx.get('details') or '—'}\n"
            f"• {ctx.get('fulfillment')}\n• {text}\n"
            f"• Total: *{company.currency} {total:,.0f}*\n\n"
            f"Pay to:\n{bank}\n\nAfter payment, *send a screenshot* here.",
            f"Order summary:\n• {ctx.get('service_name')}\n• {ctx.get('details') or '—'}\n"
            f"• {ctx.get('fulfillment')}\n• {text}\n"
            f"• Total: *{company.currency} {total:,.0f}*\n\n"
            f"Pay to:\n{bank}\n\nAfter you pay, *send screenshot* here.",
        )

    elif state == "await_payment":
        outbound = _t(
            ctx,
            "Please send the payment screenshot when done. Type *menu* to see services again.",
            "Abeg send payment screenshot when you don pay. Type *menu* if you wan see services again.",
        )

    else:
        # default: greet + language or services
        if not ctx.get("lang"):
            conv.state = "await_lang"
            greet = _greet(company)
            await db.commit()
            await _send_lang_prompt(link, company, from_wa, greet)
            return
        grok = await _grok_staff(company, ctx, text or "hello", services) if text else None
        outbound = grok or _t(ctx, "How can we help you today?", "How we fit help you today?")

    if outbound:
        db.add(Message(conversation_id=conv.id, direction="outbound", body=outbound))
        await db.commit()
        await send_text(link.phone_number_id, link.access_token, from_wa, outbound)
    else:
        await db.commit()
