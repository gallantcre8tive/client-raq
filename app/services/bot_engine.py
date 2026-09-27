"""Human-like printing WhatsApp bot: buttons, Grok NLU, sq-ft pricing, staff notify."""
from __future__ import annotations
import json
import re
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.company import Company, PaymentDetail, CompanyWhatsAppNumber
from app.models.catalog import Service, ServiceVariant
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
    """Only true unit/price-meaning questions — not status or general chat."""
    low = (text or "").lower().strip()
    if not low:
        return False
    unit_q = (
        "what do you mean by unit", "mean by unit", "what is unit", "what's unit",
        "whats unit", "wetin be unit", "wetin unit", "explain unit",
    )
    if any(x in low for x in unit_q):
        return True
    if ("unit" in low) and any(x in low for x in ("mean", "explain", "wetin", "what is", "what's")):
        return True
    return False


def _is_order_status_question(text: str) -> bool:
    low = (text or "").lower()
    keys = (
        "ready", "done", "finish", "finished", "completed", "status", "update",
        "my work", "my order", "my job", "don done", "haffa", "has my", "is my",
        "how far my", "wetin about my", "when will", "when go", "don ready",
        "collect", "pickup time", "ready for",
    )
    return any(k in low for k in keys)


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



async def _variants(db: AsyncSession, service_id: int) -> list:
    return list((await db.execute(
        select(ServiceVariant).where(
            ServiceVariant.service_id == service_id,
            ServiceVariant.is_active == True,  # noqa: E712
        ).order_by(ServiceVariant.sort_order, ServiceVariant.id)
    )).scalars().all())


async def _send_size_options(link, company, service, variants, to, ctx: dict) -> None:
    """WhatsApp list of fixed sizes small → large."""
    if not variants:
        await send_text(
            link.phone_number_id, link.access_token or "", to,
            _t(ctx, f"*{service.name}* — tell me the size and quantity you need.",
               f"*{service.name}* — tell me the size and how many you need."),
        )
        return
    rows = []
    for v in variants[:9]:
        desc = f"{company.currency} {v.price:,.0f}"
        if v.subtitle:
            desc = f"{v.subtitle} · {desc}"
        rows.append((f"var_{v.id}", v.label[:24], desc[:72]))
    if len(variants) > 9:
        rows.append(("var_more", "More sizes", "Ask for other sizes"))
    body = _t(
        ctx,
        f"*{service.name}* — pick a size (or type it):",
        f"*{service.name}* — choose size (or type am):",
    )
    await _reply(
        link, to,
        list_body=body,
        list_btn=_t(ctx, "Choose size", "Choose size"),
        list_rows=rows,
        header=company.name[:60],
    )


async def handle_inbound(
    db: AsyncSession,
    *,
    phone_number_id: str,
    from_wa: str,
    text: str | None,
    media_id: str | None = None,
    button_id: str | None = None,
    list_id: str | None = None,
    wa_message_id: str | None = None,
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

    if wa_message_id:
        dup = (await db.execute(
            select(Message).where(
                Message.conversation_id == conv.id,
                Message.media_url == f"wamid:{wa_message_id}",
            ).limit(1)
        )).scalars().first()
        if dup:
            return

    db.add(Message(
        conversation_id=conv.id,
        direction="inbound",
        body=display_in,
        media_url=(f"wamid:{wa_message_id}" if wa_message_id and not media_id else media_id),
    ))
    await db.flush()

    if wa_message_id:
        # count duplicates (same wamid already stored before this insert would need check first)
        pass

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


    # ── Two-stage Grok understanding (free text only; buttons keep rule flow) ──
    structured_states = {
        "await_lang", "await_size", "await_qty_fixed", "await_artwork",
        "await_fulfillment", "await_schedule", "await_payment", "await_proof",
        "await_details",
    }
    if text and not interactive_id and state not in structured_states - {"await_details", "open", "await_intent", "await_service", "order_placed", "await_enquiry"}:
        # prefer AI for exploratory / natural chat states
        pass
    free_ai_states = {"await_intent", "await_service", "open", "order_placed", "await_enquiry", "await_details", "completed", "done", None, ""}
    if text and not interactive_id and (state in free_ai_states or state not in structured_states):
        try:
            from app.services.ai_orchestrator import run_ai_turn
            understanding, facts, ai_reply = await run_ai_turn(
                db, company, services, ctx, conv.id, from_wa, text,
            )
            _save_ctx(conv, ctx)
            if understanding and (
                understanding.get("intent") == "human_agent_request"
                or understanding.get("needs_human")
                or ctx.get("requires_human")
                or float(understanding.get("confidence") or 1) < 0.35
            ):
                from app.models.conversation import AdminNotification
                conv.is_live_takeover = True
                conv.needs_human = True
                conv.handoff_reason = (understanding.get("intent") or "needs_human")[:200]
                db.add(AdminNotification(
                    company_id=company.id,
                    conversation_id=conv.id,
                    title="Customer needs attention",
                    body=f"{from_wa}: {(text or '')[:200]}",
                    priority="high",
                ))
                reply = ai_reply or _t(
                    ctx,
                    "A team member will take over this chat shortly. Thank you for your patience.",
                    "Someone from our team go join this chat soon. Thank you.",
                )
                db.add(Message(conversation_id=conv.id, direction="outbound", body=reply))
                await db.commit()
                await send_text(link.phone_number_id, link.access_token, from_wa, reply)
                return
            if understanding and understanding.get("intent") == "order_status" and facts.get("latest_order"):
                if ai_reply:
                    db.add(Message(conversation_id=conv.id, direction="outbound", body=ai_reply))
                    await db.commit()
                    await send_text(link.phone_number_id, link.access_token, from_wa, ai_reply)
                    return
            # If AI extracted service + quote and confidence high, use natural reply
            conf = float((understanding or {}).get("confidence") or 0)
            intent = (understanding or {}).get("intent") or ""
            if ai_reply and conf >= 0.55 and intent in (
                "printing_request", "price_request", "quotation_request",
                "general_question", "greeting", "product_availability",
                "delivery_question", "turnaround_question", "payment_question",
                "modification_request", "complaint", "clarifying", "affirm", "deny",
            ):
                # Advance state lightly when we have a verified quote
                if facts.get("verified_quote") and intent in ("printing_request", "price_request", "quotation_request"):
                    conv.state = "await_artwork" if not ctx.get("design_status") else "await_fulfillment"
                elif intent == "greeting":
                    conv.state = "await_intent"
                db.add(Message(conversation_id=conv.id, direction="outbound", body=ai_reply))
                await db.commit()
                await send_text(link.phone_number_id, link.access_token, from_wa, ai_reply)
                # After quote, offer design buttons if appropriate
                if facts.get("verified_quote") and conv.state == "await_artwork":
                    await _reply(
                        link, from_wa,
                        _t(ctx, "Do you have a print-ready design?", "You get print-ready design?"),
                        buttons=[("art_have", "I have design"), ("art_need", "Need design")],
                    )
                return
            # Low confidence / no reply → fall through to rule engine with updated ctx
        except Exception:
            pass

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


    # --- Order status questions (ready / done / my work) ---
    if text and _is_order_status_question(text):
        order = (await db.execute(
            select(Order).where(
                Order.company_id == company.id,
                Order.customer_wa_id == from_wa,
            ).order_by(Order.id.desc())
        )).scalars().first()
        if order:
            st = order.status.value if hasattr(order.status, "value") else str(order.status)
            st_label = st.replace("_", " ")
            if st in ("ready", "completed"):
                reply = _t(
                    ctx,
                    f"Yes — order *#{order.id}* is *{st_label}*. You can come for pickup (message us before you leave). If delivery was arranged, we will confirm timing.",
                    f"Yes o — order *#{order.id}* *{st_label}* already. You fit come pick am (message us before you comot). If na delivery, we go confirm time.",
                )
            elif st in ("payment_confirmed", "in_production", "awaiting_acceptance"):
                reply = _t(
                    ctx,
                    f"Order *#{order.id}* is still with us — status: *{st_label}*. We will message you the moment it is ready.",
                    f"Order *#{order.id}* still dey our side — status: *{st_label}*. We go message you once e ready.",
                )
            elif st == "payment_submitted":
                reply = _t(
                    ctx,
                    f"We have your payment proof for order *#{order.id}*. Our team is confirming it, then production starts.",
                    f"We don see your payment for order *#{order.id}*. Team dey confirm am, then we start the work.",
                )
            else:
                reply = _t(
                    ctx,
                    f"Order *#{order.id}* status: *{st_label}*. Need anything else on this job?",
                    f"Order *#{order.id}* status na *{st_label}*. Anything else for the job?",
                )
            if order.status_note:
                reply += f"\n\nNote: {order.status_note[-200:]}"
        else:
            # try Grok with context
            reply = await _grok_staff(
                company, ctx, text, services,
                extra="Customer is asking if their work/order is ready. If no order on file, say we have no open order and offer to start a new one.",
            ) or _t(
                ctx,
                "I do not see an open order on this number yet. Tell me what you ordered or start a new request.",
                "I no see any open order for this number yet. Tell me wetin you order or make we start new one.",
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
                grok = _t(
                    ctx,
                    "Got your message. How can I help with your print job?",
                    "I hear you. How I fit help with your print job?",
                )
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
                    "pricing_method": getattr(chosen, "pricing_method", None) or "piece",
                    "flow_type": getattr(chosen, "flow_type", None) or "generic",
                })
                variants = await _variants(db, chosen.id)
                if variants or (getattr(chosen, "pricing_method", "") == "fixed_size"):
                    _save_ctx(conv, ctx)
                    conv.state = "await_size"
                    await db.commit()
                    await _send_size_options(link, company, chosen, variants, from_wa, ctx)
                    return
                _save_ctx(conv, ctx)
                conv.state = "await_details"
                if _is_sqft_unit(chosen.unit) or getattr(chosen, "pricing_method", "") in ("sqft", "sqin"):
                    outbound = _t(
                        ctx,
                        f"*{chosen.name}* — {company.currency} {chosen.base_price:,.0f} per area.\n"
                        f"Send size (e.g. *3x5 ft* or *2x3 inches*) and how many *units*.\n"
                        f"Example: *3x5 ft, 5 units*",
                        f"*{chosen.name}* — {company.currency} {chosen.base_price:,.0f} per area.\n"
                        f"Send size (e.g. *3x5 ft* or *2x3 inches*) and how many *units*.",
                    )
                else:
                    outbound = _t(
                        ctx,
                        f"*{chosen.name}* — {company.currency} {chosen.base_price:,.0f} / {chosen.unit}.\n"
                        f"How many *units* do you need? (Unit = how many of the same item.)",
                        f"*{chosen.name}* — {company.currency} {chosen.base_price:,.0f} / {chosen.unit}.\n"
                        f"How many *units* you need?",
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
                "pricing_method": getattr(chosen, "pricing_method", None) or "piece",
            })
            variants = await _variants(db, chosen.id)
            if variants or getattr(chosen, "pricing_method", "") == "fixed_size":
                _save_ctx(conv, ctx)
                conv.state = "await_size"
                await db.commit()
                # try match size from same message
                lowl = text.lower().replace(" ", "")
                matched = None
                for v in variants:
                    if v.label.lower().replace(" ", "") in lowl:
                        matched = v
                        break
                if matched:
                    ctx["variant_id"] = matched.id
                    ctx["variant_label"] = matched.label
                    ctx["unit_price"] = matched.price
                    ctx["base_price"] = matched.price
                    qty = _parse_qty(text)
                    if qty:
                        ctx["qty"] = qty
                        ctx["subtotal"] = round(matched.price * qty, 2)
                        ctx["total"] = ctx["subtotal"]
                        ctx["details"] = f"{chosen.name} {matched.label} x{qty}"
                        _save_ctx(conv, ctx)
                        conv.state = "await_artwork"
                        outbound = _t(
                            ctx,
                            f"*{chosen.name}* *{matched.label}* × {qty} = *{company.currency} {ctx['total']:,.0f}*\n"
                            f"Do you have a print-ready design, or need our team to design?",
                            f"*{chosen.name}* *{matched.label}* × {qty} = *{company.currency} {ctx['total']:,.0f}*\n"
                            f"You get design already or make we design am?",
                        )
                        await _reply(link, from_wa, outbound, buttons=[("art_have", "I have design"), ("art_need", "Need design")])
                        db.add(Message(conversation_id=conv.id, direction="outbound", body=outbound))
                        await db.commit()
                        return
                    conv.state = "await_qty_fixed"
                    _save_ctx(conv, ctx)
                    outbound = _t(ctx, f"Size *{matched.label}* — {company.currency} {matched.price:,.0f} each. How many units?",
                                  f"Size *{matched.label}* — {company.currency} {matched.price:,.0f} each. How many units?")
                    db.add(Message(conversation_id=conv.id, direction="outbound", body=outbound))
                    await db.commit()
                    await send_text(link.phone_number_id, link.access_token, from_wa, outbound)
                    return
                await _send_size_options(link, company, chosen, variants, from_wa, ctx)
                return
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
            low2 = text.lower().strip()
            if low2 in ("never mind", "nevermind", "cancel", "forget", "ok", "okay", "thanks", "thank you", "ok thanks"):
                outbound = _t(ctx, "No problem. Message anytime you need a print job.", "No problem. Message anytime you need print.")
                conv.state = "await_intent"
            else:
                grok = await _grok_staff(
                    company, ctx, text, services,
                    extra="Answer helpfully about printing/orders. If they ask about progress, use order context if any. Do not dump a numbered menu.",
                )
                if grok:
                    outbound = grok
                    conv.state = "await_intent"
                else:
                    outbound = _t(
                        ctx,
                        "I can help with that. Tell me the service (e.g. banner, sticker, frame) and size if you know it.",
                        "I fit help. Tell me the service (banner, sticker, frame) and size if you know am.",
                    )
                    conv.state = "await_intent"

    # Details / size / qty

    # Fixed size selection (frames, nylon sizes, etc.)
    elif state == "await_size" or (interactive_id and str(interactive_id).startswith("var_")):
        variants = await _variants(db, int(ctx.get("service_id") or 0)) if ctx.get("service_id") else []
        chosen_v = None
        if interactive_id and str(interactive_id).startswith("var_") and interactive_id != "var_more":
            try:
                vid = int(str(interactive_id).replace("var_", ""))
            except ValueError:
                vid = None
            chosen_v = next((v for v in variants if v.id == vid), None)
        if not chosen_v and text:
            lowl = text.lower().replace(" ", "")
            for v in variants:
                lab = v.label.lower().replace(" ", "")
                if lab in lowl or lowl in lab:
                    chosen_v = v
                    break
        if not chosen_v:
            outbound = _t(ctx, "Please pick a size from the list, or type it (e.g. *8x10*).",
                          "Abeg pick size from the list, or type am (e.g. *8x10*).")
            svc = next((s for s in services if s.id == ctx.get("service_id")), None)
            db.add(Message(conversation_id=conv.id, direction="outbound", body=outbound))
            await db.commit()
            if svc and variants:
                await _send_size_options(link, company, svc, variants, from_wa, ctx)
            else:
                await send_text(link.phone_number_id, link.access_token, from_wa, outbound)
            return
        ctx["variant_id"] = chosen_v.id
        ctx["variant_label"] = chosen_v.label
        ctx["base_price"] = chosen_v.price
        ctx["unit_price"] = chosen_v.price
        _save_ctx(conv, ctx)
        conv.state = "await_qty_fixed"
        outbound = _t(
            ctx,
            f"*{ctx.get('service_name')}* size *{chosen_v.label}* — {company.currency} {chosen_v.price:,.0f} each.\nHow many units do you need?",
            f"*{ctx.get('service_name')}* size *{chosen_v.label}* — {company.currency} {chosen_v.price:,.0f} each.\nHow many units you need?",
        )

    elif state == "await_qty_fixed" and text:
        qty = _parse_qty(text) or (int(text.strip()) if text.strip().isdigit() else None)
        if not qty or qty < 1:
            outbound = _t(ctx, "Please send a number for quantity (e.g. *2*).", "Send number — e.g. *2*.")
        else:
            unit_price = float(ctx.get("unit_price") or ctx.get("base_price") or 0)
            subtotal = round(unit_price * qty, 2)
            ctx["qty"] = qty
            ctx["subtotal"] = subtotal
            ctx["total"] = subtotal
            ctx["details"] = f"{ctx.get('service_name')} {ctx.get('variant_label')} x{qty}"
            _save_ctx(conv, ctx)
            conv.state = "await_artwork"
            outbound = _t(
                ctx,
                f"Subtotal: *{company.currency} {subtotal:,.0f}* ({qty} × {company.currency} {unit_price:,.0f}).\n\n"
                f"Do you have a *print-ready design*, or should our team design it for you?",
                f"Subtotal: *{company.currency} {subtotal:,.0f}* ({qty} × {company.currency} {unit_price:,.0f}).\n\n"
                f"You get print-ready design already, or make we design am for you?",
            )
            await _reply(link, from_wa, outbound, buttons=[("art_have", "I have design"), ("art_need", "Need design")])
            db.add(Message(conversation_id=conv.id, direction="outbound", body=outbound))
            await db.commit()
            return

    elif state == "await_artwork" or interactive_id in ("art_have", "art_need"):
        need = interactive_id == "art_need" or any(w in low for w in ("need design", "design for me", "you design", "una design", "create design"))
        have = interactive_id == "art_have" or any(w in low for w in ("have design", "i have", "print ready", "my file", "artwork ready"))
        if not need and not have:
            outbound = _t(ctx, "Please choose: *I have design* or *Need design*.", "Choose: *I have design* or *Need design*.")
            await _reply(link, from_wa, outbound, buttons=[("art_have", "I have design"), ("art_need", "Need design")])
            db.add(Message(conversation_id=conv.id, direction="outbound", body=outbound))
            await db.commit()
            return
        design_fee = float(getattr(company, "design_fee_default", 0) or 0)
        if need and design_fee > 0:
            ctx["design_fee"] = design_fee
            ctx["total"] = float(ctx.get("subtotal") or 0) + design_fee
            ctx["details"] = (ctx.get("details") or "") + f" | design fee {design_fee}"
        else:
            ctx["design_fee"] = 0
        if have:
            ctx["awaiting_artwork_file"] = True
        _save_ctx(conv, ctx)
        conv.state = "await_fulfillment"
        total = float(ctx.get("total") or ctx.get("subtotal") or 0)
        note = ""
        if need and design_fee:
            note = f"\nDesign fee: *{company.currency} {design_fee:,.0f}*."
        if have:
            note += _t(ctx, "\nYou can send your design file in this chat.", "\nYou fit send design file for this chat.")
        outbound = _t(
            ctx,
            f"Quote so far: *{company.currency} {total:,.0f}*.{note}\n\nPickup or delivery?",
            f"Quote for now: *{company.currency} {total:,.0f}*.{note}\n\nPickup or delivery?",
        )
        await _reply(link, from_wa, outbound, buttons=[("ful_pickup", "Pickup"), ("ful_delivery", "Delivery")])
        db.add(Message(conversation_id=conv.id, direction="outbound", body=outbound))
        await db.commit()
        return

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
