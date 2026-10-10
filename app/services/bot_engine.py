"""Human-like printing WhatsApp bot: buttons, Grok NLU, sq-ft pricing, staff notify."""
from __future__ import annotations
import logging
import json
import re
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.company import Company, PaymentDetail, CompanyWhatsAppNumber
from app.models.catalog import Service, ServiceVariant
from app.models.conversation import Conversation, Message, Order, OrderStatus
from app.services.grok_client import grok_chat, grok_vision
from app.services.whatsapp_send import send_text, send_buttons, send_list, send_typing, download_media

log = logging.getLogger("client_raq.bot")


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
    """True only for explicit inch sizes — not the English word 'in'."""
    low = (text or "").lower()
    if "inch" in low or "inches" in low:
        return True
    if re.search(r'\d\s*"', low):
        return True
    # e.g. 12in or 12 in at end / before punctuation — not "in landscape"
    if re.search(r'\d+(?:\.\d+)?\s*ins?\b', low):
        # exclude if followed by landscape/portrait/the/a etc.
        if re.search(r'\d+(?:\.\d+)?\s*ins?\s+(?:landscape|portrait|the|a|my|our|total)', low):
            return False
        return True
    return False


def _detect_size_unit(text: str, ctx: dict | None = None) -> str:
    """Prefer saved ctx unit; else explicit ft/in/m; default feet for large format."""
    if ctx:
        u = (ctx.get("size_unit") or "").lower()
        if u in ("ft", "feet", "foot"):
            return "ft"
        if u in ("in", "inch", "inches"):
            return "in"
        if u in ("m", "metre", "meter", "sqm"):
            return "m"
    low = (text or "").lower()
    if _size_in_inches(text):
        return "in"
    if any(x in low for x in ("metre", "meter", "sqm", "m2")):
        return "m"
    if any(x in low for x in ("ft", "feet", "foot", "sqft", "sq ft")):
        return "ft"
    return "ft"


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


def _calc_total(service, width, height, qty, size_text: str = "", size_unit: str | None = None) -> float:
    """Deterministic pricing. Never treat English 'in' as inches."""
    base = float(service.base_price or 0)
    qty = max(1, int(qty or 1))
    mode = getattr(service, "pricing_method", None) or _unit_mode(service.unit)
    if mode in ("sqin",):
        mode = "sqft"
    if mode in ("custom", "setup_unit", "tier"):
        mode = _unit_mode(service.unit)
    if mode == "piece" or not width or not height:
        return round(base * qty, 2)
    unit = (size_unit or "").lower().strip() or _detect_size_unit(size_text or "", None)
    w, h = float(width), float(height)
    if mode == "sqft":
        if unit in ("in", "inch", "inches"):
            return round((w * h * qty * base) / 144.0, 2)
        if unit in ("m", "metre", "meter", "sqm"):
            return round(w * h * 10.7639 * base * qty, 2)
        return round(w * h * base * qty, 2)  # feet
    if mode == "sqm":
        if unit in ("ft", "feet", "foot"):
            return round((w * h / 10.7639) * base * qty, 2)
        return round(w * h * base * qty, 2)
    return round(w * h * base * qty, 2)


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
        "abeg", "wetin", "how far", "no vex", "i wan", "i dey", "una", "naf", "oya",
        "make i", "e be like", "wahala", "sharp sharp", "i go", "you fit", "wetin be",
        "how much be", "abeg help", "bros", "ogas", "na so",
    )
    yoruba_markers = (
        "bawo", "pele", "e se", "ese", "mo fe", "mo n", "se o", "nko", "jowo", "e jowo",
        "kilode", "kilo de", "wa nibi", "omo", "egbon", "iya", "baba", "odu", "naira",
        "mo need", "se e", "eelo", "elo ni", "meloo", "meelo",
    )
    if any(m in low for m in pidgin_markers):
        return "pidgin"
    if any(m in low for m in yoruba_markers):
        return "pidgin"  # respond pidgin + light Yoruba when customer uses Yoruba
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
        f"Reply in {'natural Nigerian Pidgin (short, friendly). If the customer used Yoruba words, you may add light Yoruba (e.g. e se, jowo, bawo) mixed into the Pidgin — never formal textbook Yoruba essays' if lang == 'pidgin' else 'clear, friendly professional English'}. "
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



async def _ensure_order_for_payment(db, company, conv, ctx, from_wa, att_id=None):
    """Create or update order when payment screenshot arrives so it always appears in Orders."""
    from app.models.conversation import Order, OrderStatus, Attachment
    from app.services.admin_notify import notify_company
    from sqlalchemy import select

    total = float(ctx.get("locked_total") or ctx.get("total") or ctx.get("quote_total") or 0)
    service_name = (
        ctx.get("service_name")
        or ctx.get("selected_service")
        or "Print order"
    )
    details_parts = []
    for k in ("width", "height", "size_unit", "variant_label", "qty", "size", "fulfillment", "datetime", "address", "design_fee"):
        if ctx.get(k) is not None and ctx.get(k) != "":
            details_parts.append(f"{k}={ctx.get(k)}")
    details = "; ".join(details_parts) if details_parts else (ctx.get("details") or "")

    order = None
    oid = ctx.get("order_id")
    if oid:
        try:
            order = await db.get(Order, int(oid))
            if order and order.company_id != company.id:
                order = None
        except Exception:
            order = None
    if order is None:
        # latest open order for this customer
        order = (await db.execute(
            select(Order).where(
                Order.company_id == company.id,
                Order.customer_wa_id == from_wa,
            ).order_by(Order.id.desc()).limit(1)
        )).scalars().first()
        # only reuse if not completed/rejected
        if order and str(getattr(order.status, "value", order.status)) in (
            "completed", "rejected", "cancelled"
        ):
            order = None

    proof_ref = f"/company/attachments/{att_id}" if att_id else (str(ctx.get("payment_proof") or "") or None)

    if order is None:
        order = Order(
            company_id=company.id,
            conversation_id=conv.id if conv else None,
            customer_wa_id=from_wa,
            service_name=str(service_name)[:200],
            details=details[:2000] if details else None,
            total=total,
            currency=getattr(company, "currency", None) or "NGN",
            status=OrderStatus.PAYMENT_SUBMITTED,
            fulfillment=ctx.get("fulfillment"),
            schedule_note=ctx.get("datetime") or ctx.get("schedule"),
            payment_proof=proof_ref,
        )
        db.add(order)
        await db.flush()
        ctx["order_id"] = order.id
    else:
        order.status = OrderStatus.PAYMENT_SUBMITTED
        if total:
            order.total = total
        if service_name:
            order.service_name = str(service_name)[:200]
        if details:
            order.details = details[:2000]
        if proof_ref:
            order.payment_proof = proof_ref
        if ctx.get("fulfillment"):
            order.fulfillment = ctx.get("fulfillment")
        order.conversation_id = conv.id if conv else order.conversation_id
        await db.flush()
        ctx["order_id"] = order.id

    # Link attachment to order
    if att_id:
        try:
            att = await db.get(Attachment, int(att_id))
            if att and att.company_id == company.id:
                att.order_id = order.id
                att.kind = "payment_proof"
        except Exception:
            pass

    await db.commit()

    body = (
        f"Customer: {from_wa}\n"
        f"Service: {order.service_name or service_name}\n"
        f"Details: {order.details or details or '—'}\n"
        f"Amount: {order.currency} {float(order.total or 0):,.0f}\n"
        f"Fulfillment: {order.fulfillment or ctx.get('fulfillment') or '—'}\n"
        f"Status: Payment screenshot received — verify in Orders\n"
        f"Order #{order.id}"
    )
    await notify_company(
        db,
        company_id=int(company.id),
        title="NEW PAYMENT SCREENSHOT",
        body=body,
        priority="high",
        conversation_id=int(conv.id) if conv else None,
        link_path=f"/company/orders/{order.id}",
    )
    return order


async def _handle_inbound_core(
    db: AsyncSession,
    *,
    phone_number_id: str,
    from_wa: str,
    text: str | None,
    media_id: str | None = None,
    media_kind: str | None = None,
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
        print("bot_trace no_whatsapp_link phone_number_id=%r" % (phone_number_id,))
        return
    company = await db.get(Company, link.company_id)
    if not company:
        return
    st = company.status if isinstance(company.status, str) else getattr(company.status, "value", "active")
    if st == "suspended":
        try:
            await send_text(
                link.phone_number_id, link.access_token, from_wa,
                "This business WhatsApp is temporarily unavailable. Please try again later.",
            )
        except Exception:
            pass
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
        # attachment_id set below after media_handler if available
    ))
    await db.flush()

    if wa_message_id:
        # count duplicates (same wamid already stored before this insert would need check first)
        pass

    # Live takeover is UI-only. Bot ALWAYS continues to reply unless admin
    # explicitly paused via bot settings. Customer must never see typing forever.
    if getattr(conv, "is_live_takeover", False):
        print("bot_trace live_takeover_flag_ignored_keep_replying conv=%s" % getattr(conv, "id", None))


    ctx = _ctx(conv)
    low = text.lower()
    # LANG_DETECT_BLOCK — auto language for multi-lang agent
    try:
        words = set(low.replace("?", " ").replace("!", " ").split())
        if words & {"abeg", "wan", "dey", "wetin", "oya", "haffa", "omoh", "sef", "nah", "bros"}:
            ctx["lang"] = "pidgin"
        elif any(x in low for x in ("bonjour", "merci", "s'il vous", "svp")):
            ctx["lang"] = "fr"
        elif any(x in low for x in ("hola", "gracias", "buenos", "por favor")):
            ctx["lang"] = "es"
        elif any(x in low for x in ("e kaaro", "e ku", "bawo", "pele", "o se")):
            ctx["lang"] = "yo"
        _save_ctx(conv, ctx)
    except Exception:
        pass

    state = conv.state or "await_lang"
    company_lang = (getattr(company, "bot_language", None) or "both").lower()
    if company_lang in ("en", "pidgin") and not ctx.get("lang"):
        ctx["lang"] = company_lang
        _save_ctx(conv, ctx)
    if text and any(w in text.lower().split() for w in ("abeg", "wan", "dey", "wetin", "oya", "naf", "una")):
        ctx["lang"] = "pidgin"
        _save_ctx(conv, ctx)

    if text and not interactive_id and wa_message_id:
        try:
            await send_typing(link.phone_number_id, link.access_token, from_wa, wa_message_id)
        except Exception:
            pass

    # Menu / greetings — ONLY for new/early chats (never re-welcome mid-conversation)
    _greet_words = {
        "menu", "services", "start", "hi", "hello", "hey", "haffa", "howfar", "sup", "yo",
        "morning", "evening", "afternoon", "bros", "brother", "boss", "guy", "blood",
    }
    _tl = (text or "").lower().strip()
    _tw = set(_tl.replace("?", " ").replace("!", " ").replace(",", " ").split())
    _is_pure_greet = bool(text) and not interactive_id and (
        _tl in _greet_words
        or _tl in ("how far", "howfar", "haffa", "haffa blood", "haffa brother", "haffa bro", "how far na", "hi bro", "hello bro")
        or (_tw <= _greet_words and len(_tw) <= 4 and not any(
            x in _tl for x in ("sticker", "banner", "nylon", "frame", "print", "price", "how much", "order", "pay", "list", "service")
        ))
        or _tl.startswith(("good morning", "good evening", "good afternoon", "how far", "haffa "))
    )
    # Count prior outbound bot messages — if we already welcomed, treat greet as soft ping
    _prior_out = 0
    try:
        _prev = (await db.execute(
            select(Message.id).where(
                Message.conversation_id == conv.id,
                Message.direction == "outbound",
            ).limit(1)
        )).scalars().first()
        _prior_out = 1 if _prev else 0
    except Exception as _pe:
        print("prior_out_check", type(_pe).__name__, _pe)
        _prior_out = 1 if (conv.state or "") not in ("await_lang", "open", "", None) else 0

    if _is_pure_greet and _prior_out == 0:
        for k in ("fulfillment", "address", "datetime", "date", "time", "payment_proof"):
            ctx.pop(k, None)
        if "haffa" in _tw or "far" in _tw or "blood" in _tw or "bro" in _tw:
            ctx["lang"] = "pidgin"
        _save_ctx(conv, ctx)
        conv.state = "await_service"
        name = company.name or "us"
        # Prefer company greeting if set
        custom_g = (getattr(company, "greeting_message", None) or "").strip()
        if custom_g:
            body = custom_g.replace("{name}", name)
        elif ctx.get("lang") == "pidgin":
            body = (
                f"Haa blood! Welcome to *{name}*.\n"
                "How your side? Wetin you wan do today?"
            )
        else:
            body = (
                f"Hey! Welcome to *{name}*.\n"
                "How can I help you today?"
            )
        try:
            db.add(Message(conversation_id=conv.id, direction="outbound", body=body))
            await db.commit()
        except Exception as _gc:
            print("greet_commit", _gc)
            try:
                await db.rollback()
            except Exception:
                pass
        ok = await send_text(link.phone_number_id, link.access_token, from_wa, body)
        print("bot_trace greet_first ok=%s" % ok)
        return

    if _is_pure_greet and _prior_out > 0:
        # Returning soft greeting — short, do NOT re-welcome with service pitch
        if "haffa" in _tw or "bro" in _tw or "far" in _tw:
            ctx["lang"] = "pidgin"
            _save_ctx(conv, ctx)
            body = "Haa! I dey here — wetin you need?"
        else:
            body = "Hey — I'm here. What do you need?"
        try:
            db.add(Message(conversation_id=conv.id, direction="outbound", body=body))
            await db.commit()
        except Exception:
            try:
                await db.rollback()
            except Exception:
                pass
        await send_text(link.phone_number_id, link.access_token, from_wa, body)
        print("bot_trace greet_soft ok=1")
        return

    # ── Service list / menu (no AI needed — always answers) ──
    if text and not interactive_id:
        _tlm = text.lower().strip()
        _menu_ask = any(
            p in _tlm for p in (
                "list of", "list una", "una service", "your service", "your services",
                "wetin una", "wetin you dey sell", "wetin you offer", "show service",
                "show services", "all service", "services", "service list", "menu",
                "what do you offer", "what can you do", "wetin you fit do",
            )
        ) or _tlm in ("services", "service", "menu", "list", "price list", "pricelist")
        if _menu_ask:
            rows = []
            names = []
            for s in services:
                if getattr(s, "is_active", True) is False:
                    continue
                nm = (s.name or "Service").strip()
                names.append(nm)
                rows.append((f"svc_{s.id}", nm[:24], (getattr(s, "description", None) or "")[:72]))
                if len(rows) >= 10:
                    break
            name = company.name or "us"
            if not names:
                body = (
                    f"*{name}* — services list not set yet. Tell me what you need and I will help."
                    if (ctx.get("lang") != "pidgin") else
                    f"*{name}* — service list never set. Abeg tell me wetin you need."
                )
            else:
                bullet = "\n".join(f"• {n}" for n in names[:15])
                if ctx.get("lang") == "pidgin" or any(w in _tlm for w in ("una", "wetin", "dey", "abeg")):
                    body = f"*{name}* services wey we get:\n{bullet}\n\nWhich one you wan do? Just type the name."
                else:
                    body = f"Here are *{name}* services:\n{bullet}\n\nWhich one do you want? Type the name to continue."
            try:
                db.add(Message(conversation_id=conv.id, direction="outbound", body=body))
                await db.commit()
            except Exception:
                try:
                    await db.rollback()
                except Exception:
                    pass
            ok = await send_text(link.phone_number_id, link.access_token, from_wa, body)
            print("bot_trace menu_list ok=%s count=%s" % (ok, len(names)))
            if rows and ok:
                try:
                    await send_list(
                        link.phone_number_id, link.access_token, from_wa,
                        "Pick a service:", "View services", rows, header=(name or "")[:60],
                    )
                except Exception as _ml:
                    print("menu_list_interactive", _ml)
            return


    # ── Fast service intent for PRINTING only (works even if Grok is down) ──
    _btype = (getattr(company, "business_type", None) or "printing").strip().lower().replace(" ", "_").replace("-", "_")
    if text and not interactive_id and _btype in ("printing", "print"):
        _intent_map = [
            (("sticker", "sav", "label"), "sticker"),
            (("banner", "flex", "rollup", "roll-up", "roll up"), "banner"),
            (("nylon", "nylon bag", "polybag"), "nylon"),
            (("frame", "frameless", "acrylic"), "frame"),
            (("jotter", "notebook"), "jotter"),
            (("cloth", "tshirt", "t-shirt", "tee"), "cloth"),
        ]
        _low = text.lower()
        _hit = None
        for keys, label in _intent_map:
            if any(k in _low for k in keys):
                _hit = label
                break
        if _hit and not ctx.get("quote_locked"):
            # Match to company service if possible
            matched = None
            for s in services:
                if not getattr(s, "is_active", True):
                    continue
                sn = (s.name or "").lower()
                if _hit in sn or any(k in sn for k in next(ks for ks, lb in _intent_map if lb == _hit)):
                    matched = s
                    break
            if matched:
                ctx["service_id"] = matched.id
                ctx["service_name"] = matched.name
            else:
                ctx["service_name"] = _hit
            ctx["lang"] = ctx.get("lang") or ("pidgin" if any(w in _low.split() for w in ("wan", "abeg", "dey", "wetin")) else "en")
            _save_ctx(conv, ctx)
            conv.state = "await_details"
            # Only short-circuit to rule reply if message is pure intent (no size/qty yet)
            has_size = bool(__import__("re").search(r"\d+\s*[x×]\s*\d+", _low)) or bool(__import__("re").search(r"\d+\s*(inch|inches|ft|feet|pcs|pieces)", _low))
            if not has_size:
                name = company.name or "us"
                if ctx.get("lang") == "pidgin":
                    body = (
                        f"Oya! *{ctx.get('service_name') or _hit}* — no problem.\n"
                        "Abeg tell me the *size* (width x height) and *how many pieces*.\n"
                        "Size dey inches or feet?"
                    )
                else:
                    body = (
                        f"Great — *{ctx.get('service_name') or _hit}*.\n"
                        "Please share the *size* (width x height) and *quantity*.\n"
                        "Is the size in inches or feet?"
                    )
                try:
                    db.add(Message(conversation_id=conv.id, direction="outbound", body=body))
                    await db.commit()
                except Exception:
                    try:
                        await db.rollback()
                    except Exception:
                        pass
                ok = await send_text(link.phone_number_id, link.access_token, from_wa, body)
                print("bot_trace intent_reply service=%s ok=%s" % (_hit, ok))
                return


    # Returning customer: ensure Customer row + previous order hint in ctx
    try:
        from app.models.conversation import Customer, Order as OrderModel
        cust = (await db.execute(
            select(Customer).where(Customer.company_id == company.id, Customer.wa_id == from_wa)
        )).scalars().first()
        if not cust:
            cust = Customer(company_id=company.id, wa_id=from_wa, profile_name=None)
            db.add(cust)
            await db.flush()
        else:
            cust.total_conversations = int(cust.total_conversations or 0) + 1
        prev = (await db.execute(
            select(OrderModel).where(
                OrderModel.company_id == company.id,
                OrderModel.customer_wa_id == from_wa,
            ).order_by(OrderModel.id.desc()).limit(1)
        )).scalars().first()
        if prev and not ctx.get("last_order_hint"):
            ctx["last_order_hint"] = {
                "id": prev.id,
                "service_name": prev.service_name,
                "total": float(prev.total or 0),
                "details": (prev.details or "")[:180],
            }
            _save_ctx(conv, ctx)
    except Exception as e:
        print("returning_customer", type(e).__name__, e)


    # ── Subscription / trial gate (platform billing) ──
    try:
        from app.services.subscription_access import assert_bot_may_reply
        from app.services.billing_service import get_company_subscription, get_pricing
        ok_bot, pause_msg = await assert_bot_may_reply(db, company.id)
        if not ok_bot:
            try:
                await send_text(link.phone_number_id, link.access_token, from_wa, pause_msg)
            except Exception:
                pass
            return
        # count trial messages
        sub = await get_company_subscription(db, company.id)
        if sub and sub.status == "trial":
            sub.message_count_trial = int(sub.message_count_trial or 0) + 1
            try:
                await db.commit()
            except Exception:
                pass
    except Exception as _sub_e:
        print("subscription_gate", type(_sub_e).__name__, _sub_e)

    # trial message counting
    try:
        from app.services.billing_service import get_company_subscription, get_pricing
        from sqlalchemy import text as _t
        sub = await get_company_subscription(db, company.id)
        if sub and sub.status == "trial":
            sub.message_count_trial = int(sub.message_count_trial or 0) + 1
            pricing = await get_pricing(db)
            limit = int(pricing.get("trial_message_limit") or 50)
            await db.commit()
            if sub.message_count_trial > limit:
                try:
                    from app.services.whatsapp_send import send_text
                    await send_text(link.phone_number_id, link.access_token, from_wa,
                        "This free trial has reached its message limit. Please subscribe on Client-RaQ to continue.")
                except Exception:
                    pass
                return
    except Exception as _tm:
        print("trial_message_count", _tm)


    # ── Media: store file, voice transcript, payment/design context ──
    attachment_note = None
    if media_id:
        try:
            from app.services.media_handler import store_whatsapp_media, transcribe_audio, describe_image_optional
            from app.services.whatsapp_send import download_media as _dl
            kind = media_kind or "file"
            if kind == "audio":
                kind = "audio"
            elif (
                state in ("await_payment", "await_proof", "await_fulfillment", "order_placed")
                or ctx.get("awaiting_file") == "payment_proof"
                or ctx.get("payment_details_sent")
                or ctx.get("quote_locked")
                or float(ctx.get("locked_total") or ctx.get("total") or 0) > 0
            ) and kind in ("image", "document", "file", None, "sticker"):
                # Any image/doc after a quote / payment request = payment proof
                kind = "payment_proof"
            elif ctx.get("awaiting_file") in ("design", "document", "reference_image") or state in ("awaiting_file", "await_design"):
                kind = ctx.get("awaiting_file") or "design"
            elif kind == "image":
                kind = "image"
            elif kind == "document":
                kind = "document"
            att = await store_whatsapp_media(
                db,
                company_id=company.id,
                customer_wa_id=from_wa,
                conversation_id=conv.id,
                access_token=link.access_token,
                wa_media_id=media_id,
                kind=kind,
            )
            if att:
                ctx["last_attachment_id"] = att.id
                ctx["last_attachment_kind"] = att.kind
                # Link attachment to the inbound message we just stored
                try:
                    last_in = (await db.execute(
                        select(Message).where(
                            Message.conversation_id == conv.id,
                            Message.direction == "inbound",
                        ).order_by(Message.id.desc()).limit(1)
                    )).scalars().first()
                    if last_in:
                        last_in.attachment_id = att.id
                        if getattr(att, "storage_path", None):
                            last_in.media_url = att.storage_path
                        if last_in.body in ("[media]", "[image]", "", None):
                            last_in.body = att.original_name or att.kind or "Attachment"
                except Exception as _le:
                    print("link_inbound_att", _le)
                if att.kind == "payment_proof" or (
                    att.kind in ("image", "document") and (
                        ctx.get("quote_locked") or float(ctx.get("locked_total") or 0) > 0
                    )
                ):
                    ctx["payment_proof"] = str(att.id)
                    att.kind = "payment_proof"
                    try:
                        from app.services.order_from_payment import create_or_update_payment_order
                        oid = await create_or_update_payment_order(
                            db,
                            company_id=int(company.id),
                            company_currency=str(getattr(company, "currency", None) or "NGN"),
                            from_wa=from_wa,
                            conversation_id=int(conv.id) if conv else None,
                            ctx=ctx,
                            att_id=int(att.id),
                        )
                        print("payment_order_result", oid)
                        _save_ctx(conv, ctx)
                    except Exception as _ne:
                        print("pay_order_notify", type(_ne).__name__, _ne)
                        import traceback
                        traceback.print_exc()
                if att.kind in ("design", "document", "reference_image"):
                    ctx["design_status"] = "received"
                    ctx["awaiting_file"] = None
                # Voice → transcript becomes text
                if media_kind in ("audio", "voice") or att.kind == "audio":
                    blob = await _dl(media_id, link.access_token)
                    if blob:
                        content, mime = blob
                        transcript = await transcribe_audio(content, mime)
                        if transcript:
                            att.transcript = transcript
                            text = (text or "") + (" " if text else "") + transcript
                            attachment_note = f"Customer sent a voice note. Transcript: {transcript}"
                            # language hint for agent
                            tl = transcript.lower()
                            if any(w in tl.split() for w in ("abeg", "wan", "dey", "wetin", "oya", "haffa", "omoh", "sef", "nah")):
                                ctx["lang"] = "pidgin"
                            _save_ctx(conv, ctx)
                        else:
                            attachment_note = "Customer sent a voice note (transcription unavailable). Ask them to type if unclear."
                elif att.kind == "payment_proof":
                    blob = await _dl(media_id, link.access_token)
                    analysis = {}
                    if blob:
                        content, mime = blob
                        try:
                            from app.services.media_handler import analyze_media_structured
                            from app.services.vision_service import customer_hint_from_analysis, analysis_to_admin_body
                            expected = None
                            try:
                                expected = float(ctx.get("locked_total") or ctx.get("total") or 0) or None
                            except Exception:
                                expected = None
                            analysis = await analyze_media_structured(
                                content, mime,
                                kind="payment_proof",
                                business_type=str(getattr(company, "business_type", None) or ""),
                                company_name=str(getattr(company, "name", None) or ""),
                                customer_caption=text or "",
                                awaiting="payment_proof",
                                expected_amount=expected,
                                expected_currency=str(getattr(company, "currency", None) or "NGN"),
                            )
                            if analysis.get("_notes_json"):
                                att.notes = analysis["_notes_json"]
                            attachment_note = customer_hint_from_analysis(analysis, is_payment=True)
                            # Enrich admin notification body in context for order_from_payment
                            ctx["payment_ai_summary"] = analysis_to_admin_body(
                                analysis,
                                customer_label=from_wa,
                                order_hint=f"Quoted total context: {expected or '—'} {getattr(company, 'currency', 'NGN')}",
                            )
                            ctx["payment_ai_amount"] = analysis.get("amount")
                            ctx["payment_ai_platform"] = analysis.get("platform")
                        except Exception as _ve:
                            print("payment_vision", type(_ve).__name__, _ve)
                            vision = (await describe_image_optional(
                                content, mime,
                                "Does this look like a bank transfer or payment receipt? One short sentence.",
                            )) or ""
                            attachment_note = f"Customer sent a payment screenshot. {vision} Staff will verify. Do NOT confirm payment. Do NOT invent amounts.".strip()
                    else:
                        attachment_note = "Customer sent a payment screenshot. Staff will verify. Do NOT confirm payment received."
                    conv.state = "await_payment"
                elif att.kind in ("design", "image", "document", "reference_image"):
                    blob = await _dl(media_id, link.access_token)
                    analysis = {}
                    if blob and att.kind != "document":
                        content, mime = blob
                        try:
                            from app.services.media_handler import analyze_media_structured
                            from app.services.vision_service import customer_hint_from_analysis
                            # short conversation context
                            summary = ""
                            try:
                                recent_bits = []
                                for m in (recent or [])[-6:]:
                                    role = m.get("role") or m.get("direction") or ""
                                    body = (m.get("body") or m.get("text") or "")[:120]
                                    if body:
                                        recent_bits.append(f"{role}: {body}")
                                summary = "\n".join(recent_bits)
                            except Exception:
                                summary = ""
                            analysis = await analyze_media_structured(
                                content, mime,
                                kind=att.kind,
                                business_type=str(getattr(company, "business_type", None) or ""),
                                company_name=str(getattr(company, "name", None) or ""),
                                conversation_summary=summary,
                                customer_caption=text or "",
                                awaiting=str(ctx.get("awaiting_file") or ""),
                            )
                            if analysis.get("_notes_json"):
                                att.notes = analysis["_notes_json"]
                            # If vision detects payment even outside await_payment, reclassify
                            if analysis.get("detected_type") == "payment_receipt" or analysis.get("is_payment_receipt"):
                                att.kind = "payment_proof"
                                ctx["payment_proof"] = str(att.id)
                                attachment_note = customer_hint_from_analysis(analysis, is_payment=True)
                                conv.state = "await_payment"
                            else:
                                attachment_note = customer_hint_from_analysis(
                                    analysis,
                                    business_type=str(getattr(company, "business_type", None) or ""),
                                )
                            if float(analysis.get("confidence") or 1) < 0.55:
                                ctx["needs_human_vision"] = True
                        except Exception as _ve:
                            print("image_vision", type(_ve).__name__, _ve)
                            attachment_note = f"Customer sent a {att.kind} file. Acknowledge and continue. Do NOT invent details."
                    else:
                        attachment_note = f"Customer sent a {att.kind} file. Acknowledge and continue the conversation. Do NOT invent details or prices."
                _save_ctx(conv, ctx)
                await db.flush()
        except Exception as e:
            print("media_process", type(e).__name__, e)




    # Low-confidence vision → flag for human (does not stop bot reply)
    try:
        if ctx.get("needs_human_vision"):
            from app.services.admin_notify import notify_human_needed
            await notify_human_needed(
                db,
                company_id=int(company.id),
                reason="Customer sent an image the AI could not confidently understand. Please review.",
                conversation_id=int(conv.id) if conv else None,
                customer_label=str(from_wa or ""),
            )
            ctx.pop("needs_human_vision", None)
    except Exception as _hv:
        print("vision_human_notify", _hv)

    # ── SAFETY NET: payment media always creates order + notify (even if earlier path missed) ──
    try:
        if media_id and (
            ctx.get("payment_proof")
            or ctx.get("payment_details_sent")
            or ctx.get("quote_locked")
            or float(ctx.get("locked_total") or ctx.get("total") or 0) > 0
            or (conv.state or "") in ("await_payment", "await_proof", "await_fulfillment", "order_placed")
        ):
            from app.services.order_from_payment import create_or_update_payment_order
            att_id = None
            try:
                att_id = int(ctx.get("payment_proof") or ctx.get("last_attachment_id") or 0) or None
            except Exception:
                att_id = None
            # Only when we actually received media this turn
            oid = await create_or_update_payment_order(
                db,
                company_id=int(company.id),
                company_currency=str(getattr(company, "currency", None) or "NGN"),
                from_wa=from_wa,
                conversation_id=int(conv.id) if conv else None,
                ctx=ctx,
                att_id=att_id,
            )
            print("safety_net_payment_order", oid)
            _save_ctx(conv, ctx)
            try:
                await db.commit()
            except Exception:
                pass
    except Exception as _sn:
        print("safety_net_FAIL", type(_sn).__name__, _sn)
        import traceback
        traceback.print_exc()


    # Formal conversation stage
    try:
        from app.services.conversation_states import next_state_after_facts, normalize_state
        if conv.needs_human:
            conv.state = "ready_for_human"
        else:
            conv.state = next_state_after_facts(ctx, conv.state)
    except Exception:
        pass

    # ── Client RaQ Agent (Grok + tools) — primary path for free text ──
    if (text or attachment_note) and not interactive_id:
        # Skip agent only for pure button ids already handled
        try:
            from app.services.client_raq_agent import run_agent
            recent_rows = list((await db.execute(
                select(Message).where(Message.conversation_id == conv.id)
                .order_by(Message.id.desc()).limit(16)
            )).scalars().all())
            recent = [
                {"direction": m.direction, "body": m.body or ""}
                for m in reversed(recent_rows)
            ]
            print("bot_trace agent_start company=%s conv=%s wa=%s text=%r" % (
                company.id, conv.id, from_wa, (text or "")[:80],
            ))
            import asyncio as _aio
            try:
                reply, ctx, needs_human = await _aio.wait_for(
                    run_agent(
                        db,
                        company=company,
                        conv=conv,
                        ctx=ctx,
                        from_wa=from_wa,
                        customer_message=text or (attachment_note or "[media]"),
                        recent=recent,
                        attachment_note=attachment_note,
                    ),
                    timeout=18.0,
                )
            except _aio.TimeoutError:
                print("bot_trace agent_timeout")
                reply = (
                    f"Hi! Thanks for messaging *{company.name}*. "
                    "I am a bit slow right now — please resend what you need "
                    "(service, size, quantity) and I will quote you."
                )
                needs_human = False
            print("bot_trace agent_done reply_len=%s needs_human=%s" % (
                len(reply or ""), needs_human,
            ))
            _save_ctx(conv, ctx)
            if needs_human:
                # Notify admin but KEEP bot replying (do not freeze chat)
                try:
                    from app.services.admin_notify import notify_human_needed
                    await notify_human_needed(
                        db,
                        company_id=int(company.id),
                        reason=(text or attachment_note or "Customer needs attention")[:300],
                        conversation_id=int(conv.id),
                        customer_label=from_wa,
                    )
                except Exception as ne:
                    print("needs_human_notify", ne)
                    try:
                        from app.models.conversation import AdminNotification
                        db.add(AdminNotification(
                            company_id=company.id,
                            conversation_id=conv.id,
                            title="Customer needs your attention",
                            body=(text or attachment_note or "Customer message needs a human reply")[:500],
                            priority="high",
                        ))
                        await db.flush()
                    except Exception as ne2:
                        print("needs_human_notify2", ne2)
            if not reply:
                # Grok returned empty — never leave customer silent (business-aware)
                _bt = (getattr(company, "business_type", None) or "printing").lower()
                if _bt in ("printing", "print"):
                    reply = (
                        f"Hi! Welcome to *{company.name}*. "
                        "Tell me what you need (sticker, banner, nylon, frame…) "
                        "plus size and quantity, and I will quote you."
                    )
                elif _bt == "exchanger":
                    reply = (
                        f"Hi! *{company.name}* here. "
                        "Tell me the method (PayPal, bank, Cash App, crypto…) and amount."
                    )
                else:
                    reply = (
                        f"Hi! Welcome to *{company.name}*. "
                        "Tell me what you need and I will help you right away."
                    )
            if reply:
                # Detect pidgin from customer message
                if text and any(w in text.lower().split() for w in ("abeg", "wan", "dey", "wetin", "oya", "naf")):
                    ctx["lang"] = "pidgin"
                    _save_ctx(conv, ctx)
                # Do not force await_fulfillment on every reply — only when quote just locked
                if ctx.get("quote_locked") and ctx.get("locked_total") and not ctx.get("fulfillment"):
                    if not ctx.get("fulfillment_prompted"):
                        conv.state = "await_fulfillment"
                db.add(Message(conversation_id=conv.id, direction="outbound", body=reply))
                await db.commit()
                ok_send = await send_text(link.phone_number_id, link.access_token, from_wa, reply)
                print("bot_trace send_reply ok=%s len=%s" % (ok_send, len(reply or "")))
                if not ok_send:
                    # one retry without formatting
                    plain = (reply or "").replace("*", "")[:1000]
                    await send_text(link.phone_number_id, link.access_token, from_wa, plain or "Thanks — we got your message.")

                # Service example images queued by tools
                for ex in list(ctx.pop("pending_example_images", None) or []):
                    try:
                        from app.services.whatsapp_send import upload_media_bytes, send_image_id
                        from pathlib import Path as _P
                        p = _P(ex.get("path") or "")
                        if p.is_file():
                            mid = await upload_media_bytes(
                                link.phone_number_id, link.access_token,
                                p.read_bytes(), ex.get("mime") or "image/jpeg",
                                ex.get("filename") or p.name,
                            )
                            if mid:
                                await send_image_id(
                                    link.phone_number_id, link.access_token, from_wa, mid,
                                    caption=ex.get("caption"),
                                )
                    except Exception as _ee:
                        print("example_send", _ee)
                # Catalog / reference sample to WhatsApp
                pref = ctx.pop("pending_reference", None)
                if pref:
                    _save_ctx(conv, ctx)
                    try:
                        from app.services.whatsapp_send import send_image
                        cap = (pref.get("caption") or "")[:1000]
                        url = pref.get("image_url")
                        if url:
                            await send_image(link.phone_number_id, link.access_token, from_wa, image_url=url, caption=cap)
                        elif cap:
                            await send_text(link.phone_number_id, link.access_token, from_wa, "📎 Reference:\n" + cap)
                    except Exception as e:
                        print("ref_send", type(e).__name__, e)

                # Pickup/delivery interactive: at most ONCE per quote, never during side questions
                low_msg = (text or "").lower()
                side_chat = any(w in low_msg for w in (
                    "wait", "hold", "question", "ask", "abeg", "another", "also",
                    "wetin", "how much", "price", "pidgin", "english", "speak",
                    "shey", "abi", "please", "bro", "sir", "ma",
                )) and not any(w in low_msg for w in ("pickup", "delivery", "deliver", "i go come", "collect"))
                if side_chat:
                    ctx["flow_paused"] = True
                    ctx["fulfillment_prompted"] = True  # suppress auto buttons while chatting
                    _save_ctx(conv, ctx)
                elif (
                    ctx.get("quote_locked")
                    and float(ctx.get("locked_total") or 0) > 0
                    and not ctx.get("fulfillment")
                    and not ctx.get("flow_paused")
                    and not ctx.get("fulfillment_prompted")
                ):
                    ctx["fulfillment_prompted"] = True
                    _save_ctx(conv, ctx)
                    try:
                        await send_buttons(
                            link.phone_number_id,
                            link.access_token,
                            from_wa,
                            _t(ctx, "Pickup or delivery?", "Pickup or delivery?"),
                            [("ful_pickup", "Pickup"), ("ful_delivery", "Delivery")],
                        )
                    except Exception:
                        pass
                return
        except BaseException as e:
            print("agent_path", type(e).__name__, e)
            import traceback; traceback.print_exc()
            try:
                low = (text or "").lower()
                pidgin = any(w in low.split() for w in ("abeg", "wan", "dey", "wetin", "oya", "haffa", "omoh", "fit", "nko", "nah"))
                if pidgin:
                    fb = (
                        f"Sorry for the delay — *{company.name}* still dey here.\n"
                        "Abeg tell me wetin you need (sticker, banner, nylon, frame…), "
                        "size and quantity. I go reply sharp."
                    )
                else:
                    fb = (
                        f"Sorry for the delay — *{company.name}* is here.\n"
                        "Tell me what you need (sticker, banner, nylon, frame…), "
                        "size and quantity, and I will quote you right away."
                    )
                db.add(Message(conversation_id=conv.id, direction="outbound", body=fb))
                await db.commit()
                ok = await send_text(link.phone_number_id, link.access_token, from_wa, fb)
                print("bot_trace agent_fallback_send ok=%s" % ok)
                return
            except Exception as e2:
                print("agent_fallback_send", type(e2).__name__, e2)
                try:
                    await send_text(
                        link.phone_number_id, link.access_token, from_wa,
                        f"Hey! *{company.name}* here — tell me wetin you need and I go help you.",
                    )
                except Exception:
                    pass
                return

    # ── Fast path: price / product asks get an instant rule reply (never silent) ──
    low_fast = (text or "").lower()
    price_like = bool(text) and not interactive_id and any(
        w in low_fast for w in (
            "how much", "price", "cost", "quote", "nylon", "sticker", "banner",
            "frame", "flex", "jotter", "cloth", "pen", "sav", "roll-up", "rollup",
        )
    )
    if price_like:
        # Prefer Grok natural answer first
        try:
            from app.services.ai_orchestrator import run_ai_turn
            understanding, facts, ai_reply = await run_ai_turn(
                db, company, services, ctx, conv.id, from_wa, text,
            )
            if ai_reply and len(ai_reply.strip()) > 8:
                # Drop old fulfillment lock when new quote starts
                for k in ("fulfillment", "address", "datetime", "date", "time", "payment_proof"):
                    ctx.pop(k, None)
                if understanding and understanding.get("language") == "pidgin":
                    ctx["lang"] = "pidgin"
                _save_ctx(conv, ctx)
                if facts.get("verified_quote") and float(facts.get("total") or 0) > 0:
                    conv.state = "await_artwork" if not ctx.get("design_status") else "await_fulfillment"
                else:
                    conv.state = "await_details"
                db.add(Message(conversation_id=conv.id, direction="outbound", body=ai_reply.strip()))
                await db.commit()
                await send_text(link.phone_number_id, link.access_token, from_wa, ai_reply.strip())
                return
        except Exception as e:
            print("price_ai_first", type(e).__name__, e)

        matched = None
        for svc in services:
            name = (svc.name or "").lower()
            keys = ("nylon", "sticker", "banner", "frame", "flex", "jotter", "cloth", "pen", "sav", "roll")
            if any(k in low_fast and k in name for k in keys):
                matched = svc
                break
        if matched is None:
            # fuzzy: any service name word in message
            for svc in services:
                for part in (svc.name or "").lower().replace("-", " ").split():
                    if len(part) > 3 and part in low_fast:
                        matched = svc
                        break
                if matched:
                    break
        if matched:
            price = float(getattr(matched, "base_price", 0) or 0)
            unit = getattr(matched, "pricing_unit", None) or getattr(matched, "unit", None) or "per unit"
            for k in ("fulfillment", "address", "datetime", "date", "time", "payment_proof"):
                ctx.pop(k, None)
            ctx["service_id"] = matched.id
            ctx["service_name"] = matched.name
            _save_ctx(conv, ctx)
            conv.state = "await_details"
            if price > 0:
                reply = _t(
                    ctx,
                    f"For *{matched.name}* — from *{company.currency} {price:,.0f}* ({unit}).\n"
                    f"Tell me the *size* and *quantity* so I can give your exact total.",
                    f"For *{matched.name}* — from *{company.currency} {price:,.0f}* ({unit}).\n"
                    f"Tell me *size* and *quantity* make I give exact total.",
                )
            else:
                reply = _t(
                    ctx,
                    f"Yes we do *{matched.name}*. Tell me *size* and *quantity* and I will calculate your price.\n"
                    f"(Or type *menu* for other services.)",
                    f"Yes we dey do *{matched.name}*. Tell me *size* and *quantity* make I calculate the price.\n"
                    f"(Or type *menu* for other services.)",
                )
            db.add(Message(conversation_id=conv.id, direction="outbound", body=reply))
            await db.commit()
            await send_text(link.phone_number_id, link.access_token, from_wa, reply)
            return
        else:
            # No matching service — list what they offer instead of silence
            names = [s.name for s in services[:8] if getattr(s, "is_active", True)]
            list_txt = ", ".join(names) if names else "our print services"
            reply = _t(
                ctx,
                f"I can quote that. We offer: {list_txt}.\nWhich one do you want, and what size/quantity?",
                f"I fit quote am. We get: {list_txt}.\nWhich one you wan, size and quantity?",
            )
            for k in ("fulfillment", "address", "datetime", "date", "time"):
                ctx.pop(k, None)
            _save_ctx(conv, ctx)
            conv.state = "await_service"
            db.add(Message(conversation_id=conv.id, direction="outbound", body=reply))
            await db.commit()
            await send_text(link.phone_number_id, link.access_token, from_wa, reply)
            return

    # ── Two-stage Grok understanding ──
    # Buttons stay rule-based. Free text ALWAYS goes through Grok first so the
    # customer can change topic mid-flow (e.g. ask nylon price while on Pickup).
    structured_states = {
        "await_lang", "await_size", "await_qty_fixed", "await_artwork",
        "await_fulfillment", "await_schedule", "await_payment", "await_proof",
        "await_details", "await_datetime", "await_address",
    }
    # Short answers that clearly complete the current step — let rules handle them
    step_answers = {
        "await_fulfillment": ("pickup", "delivery", "deliver", "come collect", "i go come", "i will come"),
        "await_lang": ("english", "pidgin", "en", "normal english"),
        "await_artwork": ("need design", "print ready", "i have design", "no design", "design ready"),
    }
    text_is_step_answer = False
    if text and state in step_answers:
        tl = text.lower().strip()
        text_is_step_answer = any(k in tl for k in step_answers[state]) and len(tl) < 40

    if text and not interactive_id and not text_is_step_answer:
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
                eq = []
                if getattr(company, "enquiry_whatsapp", None):
                    eq.append(f"WhatsApp: {company.enquiry_whatsapp}")
                if getattr(company, "enquiry_phone", None):
                    eq.append(f"Call: {company.enquiry_phone}")
                eq_line = ("\n" + " | ".join(eq)) if eq else ""
                note = (getattr(company, "enquiry_note", None) or "").strip()
                note_line = f"\n{note}" if note else ""
                reply = ai_reply or _t(
                    ctx,
                    f"A team member will take over this chat shortly. Thank you for your patience.{eq_line}{note_line}",
                    f"Someone from our team go join this chat soon. Thank you.{eq_line}{note_line}",
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
            # If AI understood the free text, prefer natural reply over rigid step prompts
            conf = float((understanding or {}).get("confidence") or 0)
            intent = (understanding or {}).get("intent") or ""
            topic_change_intents = (
                "printing_request", "price_request", "quotation_request",
                "product_availability", "general_question", "greeting",
                "delivery_question", "turnaround_question", "payment_question",
                "modification_request", "complaint", "clarifying",
            )
            # Prefer any solid AI reply for free text (especially price / new product asks)
            price_like = any(w in (text or "").lower() for w in (
                "how much", "price", "cost", "quote", "nylon", "sticker", "banner",
                "frame", "flex", "jotter", "pen", "cloth", "tshirt", "t-shirt",
            ))
            if ai_reply and (conf >= 0.4 or price_like) and (
                intent in topic_change_intents + ("affirm", "deny", "unknown", "") or price_like
            ):
                # Customer asked a NEW product/price while mid-flow → soft reset old cart fields
                if intent in ("printing_request", "price_request", "quotation_request", "product_availability"):
                    for k in (
                        "fulfillment", "address", "datetime", "date", "time",
                        "payment_proof", "subtotal", "delivery_fee",
                    ):
                        ctx.pop(k, None)
                    # If AI/facts point at a different service name, clear size/qty from previous job
                    new_svc = (facts or {}).get("service_name") or (understanding or {}).get("service_name")
                    if new_svc and ctx.get("service_name") and str(new_svc).lower() not in str(ctx.get("service_name")).lower():
                        for k in ("size", "qty", "width", "height", "unit", "design_status", "design_fee", "total", "details"):
                            ctx.pop(k, None)
                    _save_ctx(conv, ctx)
                    if facts.get("verified_quote"):
                        conv.state = "await_artwork" if not ctx.get("design_status") else "await_fulfillment"
                    else:
                        conv.state = "await_service"
                elif intent == "greeting":
                    conv.state = "await_intent"
                db.add(Message(conversation_id=conv.id, direction="outbound", body=ai_reply))
                await db.commit()
                await send_text(link.phone_number_id, link.access_token, from_wa, ai_reply)
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


    # ── Last-chance: free-text price/product ask must not die on Pickup buttons ──
    if text and not interactive_id:
        low_all = text.lower()
        if any(w in low_all for w in ("how much", "price", "cost", "quote", "nylon", "sticker", "banner", "frame")):
            # Soft-reset fulfillment lock so customer can start a new quote
            if state in structured_states or state == "await_fulfillment":
                try:
                    from app.services.ai_orchestrator import run_ai_turn
                    understanding, facts, ai_reply = await run_ai_turn(
                        db, company, services, ctx, conv.id, from_wa, text,
                    )
                    if ai_reply:
                        for k in ("fulfillment", "address", "datetime", "date", "time"):
                            ctx.pop(k, None)
                        _save_ctx(conv, ctx)
                        conv.state = "await_service"
                        db.add(Message(conversation_id=conv.id, direction="outbound", body=ai_reply))
                        await db.commit()
                        await send_text(link.phone_number_id, link.access_token, from_wa, ai_reply)
                        return
                except Exception as e:
                    print("last_chance_ai", type(e).__name__, e)
                # Rule-based mini quote if AI still empty
                for svc in services:
                    name = (svc.name or "").lower()
                    if any(k in low_all and k in name for k in ("nylon", "sticker", "banner", "frame", "flex", "jotter")):
                        price = float(getattr(svc, "base_price", 0) or 0)
                        unit = getattr(svc, "pricing_unit", None) or getattr(svc, "unit", "") or ""
                        reply = _t(
                            ctx,
                            f"For *{svc.name}*: from *{company.currency} {price:,.0f}* {unit}. "
                            f"Tell me size and quantity for exact total.",
                            f"For *{svc.name}*: from *{company.currency} {price:,.0f}* {unit}. "
                            f"Tell me size and quantity make I give exact total.",
                        )
                        for k in ("fulfillment", "address", "datetime"):
                            ctx.pop(k, None)
                        ctx["service_id"] = svc.id
                        ctx["service_name"] = svc.name
                        _save_ctx(conv, ctx)
                        conv.state = "await_details"
                        db.add(Message(conversation_id=conv.id, direction="outbound", body=reply))
                        await db.commit()
                        await send_text(link.phone_number_id, link.access_token, from_wa, reply)
                        return


    # ── Locked quote: do not recompute from chat noise ──
    if ctx.get("quote_locked") and ctx.get("locked_total") is not None and state in (
        "await_fulfillment", "await_datetime", "await_payment", "await_address", "order_placed",
    ):
        low_lock = (text or "").lower()
        changing = any(x in low_lock for x in (
            "change size", "change qty", "change quantity", "wrong size", "new quote",
            "start over", "cancel order", "different size", "recalculate",
        ))
        if text and not interactive_id and not changing:
            # Keep locked total stable
            ctx["total"] = ctx.get("locked_total")
            eq = []
            if getattr(company, "enquiry_whatsapp", None):
                eq.append(f"WhatsApp: {company.enquiry_whatsapp}")
            if getattr(company, "enquiry_phone", None):
                eq.append(f"Call: {company.enquiry_phone}")
            eq_line = ("\n" + " | ".join(eq)) if eq else ""
            # Try natural AI answer without touching math
            try:
                from app.services.ai_orchestrator import run_ai_turn
                understanding, facts, ai_reply = await run_ai_turn(
                    db, company, services, ctx, conv.id, from_wa, text,
                )
                if ai_reply and len(ai_reply.strip()) > 5:
                    ctx["total"] = ctx.get("locked_total")
                    _save_ctx(conv, ctx)
                    db.add(Message(conversation_id=conv.id, direction="outbound", body=ai_reply.strip()))
                    await db.commit()
                    await send_text(link.phone_number_id, link.access_token, from_wa, ai_reply.strip())
                    return
            except Exception as e:
                print("locked_ai", type(e).__name__, e)
            reply = _t(
                ctx,
                f"Your confirmed quote is still *{company.currency} {float(ctx.get('locked_total') or 0):,.0f}* "
                f"for {ctx.get('service_name') or 'your order'} "
                f"({ctx.get('width')}x{ctx.get('height')} {ctx.get('size_unit') or 'ft'} × {ctx.get('qty') or 1}).\n"
                f"Please choose *Pickup* or *Delivery* to continue, or type *new quote* to start over.{eq_line}",
                f"Your quote still *{company.currency} {float(ctx.get('locked_total') or 0):,.0f}* "
                f"for {ctx.get('service_name') or 'your order'}.\n"
                f"Choose *Pickup* or *Delivery*, or type *new quote* start again.{eq_line}",
            )
            db.add(Message(conversation_id=conv.id, direction="outbound", body=reply))
            await db.commit()
            await send_text(link.phone_number_id, link.access_token, from_wa, reply)
            return
        if changing:
            ctx["quote_locked"] = False
            ctx.pop("locked_total", None)
            for k in ("width", "height", "qty", "size", "total", "subtotal", "fulfillment"):
                ctx.pop(k, None)
            conv.state = "await_service"
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
                ctx["size_unit"] = _detect_size_unit(text or "", ctx)
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
            unit = ctx.get("size_unit") or _detect_size_unit(text or "", ctx)
            ctx["size_unit"] = unit
            total = _calc_total(svc, ctx.get("width"), ctx.get("height"), int(ctx.get("qty") or 1), text, size_unit=unit)
            ctx["locked_total"] = total
            ctx["quote_locked"] = True
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
            # Free text that is not pickup/delivery (AI path should have handled topic changes).
            # Acknowledge and re-offer buttons so the chat never feels stuck.
            outbound = _t(
                ctx,
                "I can help with that. For this current order, please choose *Pickup* or *Delivery* below — "
                "or tell me clearly if you want to start a *new* quote (e.g. nylon, sticker, banner).",
                "I fit help you. For this order now, choose *Pickup* or *Delivery* below — "
                "or tell me if you wan start *new* quote (e.g. nylon, sticker, banner).",
            )
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

    if not outbound:
        outbound = _t(
            ctx,
            "How can we help? Type *menu* for services, or tell me what you want to print (e.g. nylon, banner, sticker).",
            "How we fit help? Type *menu* for services, or tell me wetin you wan print (e.g. nylon, banner, sticker).",
        )
    try:
        db.add(Message(conversation_id=conv.id, direction="outbound", body=outbound))
        await db.commit()
        await send_text(link.phone_number_id, link.access_token, from_wa, outbound)
    except Exception as e:
        print("outbound_send_fail", type(e).__name__, e)
        try:
            await db.rollback()
        except Exception:
            pass



    # ── Absolute never-silent for free text (if nothing above replied) ──
    if (text or "").strip() and not interactive_id:
        try:
            from app.services.client_raq_agent import _rule_reply as _rr
            body = _rr(company, text, ctx)
            db.add(Message(conversation_id=conv.id, direction="outbound", body=body))
            await db.commit()
            ok = await send_text(link.phone_number_id, link.access_token, from_wa, body)
            print("bot_trace absolute_safety ok=%s" % ok)
            return
        except Exception as _abs:
            print("absolute_safety", type(_abs).__name__, _abs)
            try:
                await send_text(
                    link.phone_number_id, link.access_token, from_wa,
                    f"Hey — *{company.name or 'we'}* got your message. Tell me clearly what you need.",
                )
            except Exception:
                pass
            return


async def handle_inbound(
    db: AsyncSession,
    *,
    phone_number_id: str,
    from_wa: str,
    text: str | None,
    media_id: str | None = None,
    media_kind: str | None = None,
    button_id: str | None = None,
    list_id: str | None = None,
    wa_message_id: str | None = None,
) -> None:
    """Public entry — never raises to webhook. Always attempts a customer reply on failure."""
    try:
        await _handle_inbound_core(
            db,
            phone_number_id=phone_number_id,
            from_wa=from_wa,
            text=text,
            media_id=media_id,
            media_kind=media_kind,
            button_id=button_id,
            list_id=list_id,
            wa_message_id=wa_message_id,
        )
    except BaseException as e:
        import traceback
        traceback.print_exc()
        print("bot_trace handle_inbound_FATAL", type(e).__name__, e, "text=", (text or "")[:80])
        try:
            await db.rollback()
        except Exception:
            pass
        # Recovery: ALWAYS use intelligent rule reply — never sticky welcome
        try:
            from app.models.company import CompanyWhatsAppNumber
            from sqlalchemy import select as _sel
            from app.services.client_raq_agent import _rule_reply
            link = (await db.execute(
                _sel(CompanyWhatsAppNumber).where(
                    CompanyWhatsAppNumber.phone_number_id == phone_number_id,
                    CompanyWhatsAppNumber.is_active == True,  # noqa: E712
                )
            )).scalar_one_or_none()
            if not link or not link.access_token or not from_wa:
                return
            company = await db.get(Company, link.company_id)
            msg = _rule_reply(company, text or "", {})
            await send_text(link.phone_number_id, link.access_token, from_wa, msg)
            print("bot_trace recovery_rule_reply_sent len=%s" % len(msg or ""))
        except Exception as e2:
            print("bot_trace recovery_failed", type(e2).__name__, e2)
            try:
                from app.models.company import CompanyWhatsAppNumber
                from sqlalchemy import select as _sel
                link = (await db.execute(
                    _sel(CompanyWhatsAppNumber).where(
                        CompanyWhatsAppNumber.phone_number_id == phone_number_id,
                    )
                )).scalar_one_or_none()
                if link and link.access_token and from_wa:
                    await send_text(
                        link.phone_number_id, link.access_token, from_wa,
                        "I got your message — tell me clearly what you need and I will help.",
                    )
            except Exception:
                pass
