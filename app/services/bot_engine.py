"""Printing WhatsApp bot — natural staff tone, rule flow + optional Grok."""
from __future__ import annotations
import json
import re
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.company import Company, PaymentDetail, CompanyWhatsAppNumber
from app.models.catalog import Service
from app.models.conversation import Conversation, Message, Order, OrderStatus
from app.services.grok_client import grok_chat, grok_vision
from app.services.whatsapp_send import send_text, download_media

def _ctx(conv: Conversation) -> dict:
    try:
        return json.loads(getattr(conv, "context_json", None) or "{}")
    except Exception:
        return {}

def _save_ctx(conv: Conversation, data: dict) -> None:
    if hasattr(conv, "context_json"):
        conv.context_json = json.dumps(data)

def _greet(company: Company) -> str:
    if company.greeting_message:
        return company.greeting_message.strip()
    lang = getattr(company, "bot_language", "both") or "both"
    if lang == "pidgin":
        return f"Good day! Welcome to *{company.name}*. Wetin you wan print today?"
    return f"Hi! Welcome to *{company.name}*. What would you like to print today?"

async def _services(db: AsyncSession, company: Company):
    rows = (await db.execute(
        select(Service).where(Service.company_id == company.id, Service.is_active == True)  # noqa: E712
    )).scalars().all()
    if not rows:
        return [], "Services not listed yet — describe what you need and we will help."
    lines = [f"{i}. *{s.name}* — {company.currency} {s.base_price:,.0f} ({s.unit})" for i, s in enumerate(rows, 1)]
    return rows, "\n".join(lines)

async def _bank(db: AsyncSession, company: Company) -> str:
    rows = (await db.execute(select(PaymentDetail).where(PaymentDetail.company_id == company.id))).scalars().all()
    if not rows:
        return "Please ask staff for bank details."
    parts = []
    for p in rows:
        b = f"*{p.bank_name}*\nName: {p.account_name}\nAcct: {p.account_number}"
        if p.instructions:
            b += f"\n{p.instructions}"
        parts.append(b)
    return "\n\n".join(parts)

async def handle_inbound(
    db: AsyncSession,
    *,
    phone_number_id: str,
    from_wa: str,
    text: str | None,
    media_id: str | None = None,
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

    text = (text or "").strip()
    services, svc_text = await _services(db, company)

    conv = (await db.execute(
        select(Conversation).where(
            Conversation.company_id == company.id,
            Conversation.customer_wa_id == from_wa,
        )
    )).scalar_one_or_none()
    if not conv:
        conv = Conversation(company_id=company.id, customer_wa_id=from_wa, state="await_service")
        db.add(conv)
        await db.flush()

    db.add(Message(
        conversation_id=conv.id,
        direction="inbound",
        body=text or ("[image]" if media_id else "[media]"),
        media_url=media_id,
    ))
    await db.flush()

    if getattr(conv, "is_live_takeover", False):
        await db.commit()
        return

    low = text.lower()
    reply = None
    ctx = _ctx(conv)

    if low in ("hi", "hello", "hey", "start", "menu", "good morning", "good evening", "good afternoon"):
        conv.state = "await_service"
        reply = _greet(company) + "\n\n*Services:*\n" + svc_text + "\n\nReply with the *number* or *name*."
        _save_ctx(conv, {})

    elif conv.state in ("open", "menu", "await_service"):
        chosen = None
        if low.isdigit():
            i = int(low) - 1
            if 0 <= i < len(services):
                chosen = services[i]
        if not chosen:
            for s in services:
                if s.name.lower() in low or low in s.name.lower():
                    chosen = s
                    break
        if chosen:
            ctx = {"service_id": chosen.id, "service_name": chosen.name, "base_price": chosen.base_price, "unit": chosen.unit}
            _save_ctx(conv, ctx)
            conv.state = "await_details"
            desc = (chosen.description or "").strip()
            reply = (
                f"*{chosen.name}* — {company.currency} {chosen.base_price:,.0f} / {chosen.unit}\n"
                f"{desc}\n\n"
                "Send size/details and quantity.\nExample: *3x2 ft, 2 pieces*"
            )
        else:
            conv.state = "await_service"
            reply = "Please pick a service by number or name:\n\n" + svc_text

    elif conv.state == "await_details":
        ctx["details"] = text
        _save_ctx(conv, ctx)
        conv.state = "await_fulfillment"
        reply = "How do you want it?\n1. *Pickup*\n2. *Delivery*"

    elif conv.state == "await_fulfillment":
        is_del = None
        if any(w in low for w in ("deliver", "delivery", "send", "home", "address")) or low == "2":
            is_del = True
        elif any(w in low for w in ("pickup", "pick up", "collect", "come")) or low == "1":
            is_del = False
        if is_del is None:
            reply = "Please reply *Pickup* or *Delivery*."
        else:
            ctx["fulfillment"] = "delivery" if is_del else "pickup"
            _save_ctx(conv, ctx)
            conv.state = "await_datetime"
            reply = (
                "What *date* and *time* for delivery? Example: *25 Sept, 2pm*"
                if is_del else
                "What *date* and *time* for pickup? Example: *Tomorrow 11am*"
            )

    elif conv.state == "await_datetime":
        parts = re.split(r"[,\-]| at ", text, maxsplit=1)
        ctx["scheduled_date"] = parts[0].strip()
        ctx["scheduled_time"] = parts[1].strip() if len(parts) > 1 else text.strip()
        ctx["when_raw"] = text
        _save_ctx(conv, ctx)
        if ctx.get("fulfillment") == "delivery":
            conv.state = "await_address"
            reply = "Please send the *delivery address* (area + landmark)."
        else:
            conv.state = "await_proof"
            total = float(ctx.get("base_price") or 0)
            ctx["total"] = total
            ctx["delivery_fee"] = 0
            _save_ctx(conv, ctx)
            bank = await _bank(db, company)
            reply = (
                f"Summary:\n• {ctx.get('service_name')}\n• {ctx.get('details')}\n"
                f"• Pickup: {ctx.get('scheduled_date')} {ctx.get('scheduled_time')}\n"
                f"• Total: *{company.currency} {total:,.0f}*\n\n"
                f"Pay to:\n{bank}\n\nAfter transfer, send your *payment screenshot* here."
            )

    elif conv.state == "await_address":
        ctx["address"] = text
        fee = float(getattr(company, "delivery_fee_base", 0) or 0)
        total = float(ctx.get("base_price") or 0) + fee
        ctx["delivery_fee"] = fee
        ctx["total"] = total
        _save_ctx(conv, ctx)
        conv.state = "await_proof"
        bank = await _bank(db, company)
        fee_line = f"• Delivery fee: {company.currency} {fee:,.0f}\n" if fee else ""
        reply = (
            f"Summary:\n• {ctx.get('service_name')}\n• {ctx.get('details')}\n"
            f"• Delivery: {ctx.get('scheduled_date')} {ctx.get('scheduled_time')}\n"
            f"• Address: {ctx.get('address')}\n{fee_line}"
            f"• Total: *{company.currency} {total:,.0f}*\n\n"
            f"Pay to:\n{bank}\n\nAfter transfer, send your *payment screenshot* here."
        )

    elif conv.state in ("await_payment", "await_proof"):
        if media_id or any(w in low for w in ("paid", "sent", "transfer", "i have paid", "payment")):
            staff_note = None
            if media_id and link.access_token:
                media = await download_media(media_id, link.access_token)
                if media:
                    raw, mime = media
                    insight = await grok_vision(
                        "You assist a print shop. Be brief.",
                        "Is this a payment/transfer receipt? Extract amount/ref if visible. One short sentence.",
                        raw, mime,
                    )
                    if insight:
                        staff_note = insight
            order_kw = dict(
                company_id=company.id,
                conversation_id=conv.id,
                customer_wa_id=from_wa,
                service_name=ctx.get("service_name") or "Custom",
                details=ctx.get("details"),
                total_amount=float(ctx.get("total") or ctx.get("base_price") or 0),
                currency=company.currency or "NGN",
                status=OrderStatus.PAYMENT_SUBMITTED,
                payment_proof_url=media_id,
            )
            # optional fields if columns exist
            for k, v in {
                "fulfillment": ctx.get("fulfillment"),
                "scheduled_date": ctx.get("scheduled_date"),
                "scheduled_time": ctx.get("scheduled_time"),
                "delivery_address": ctx.get("address"),
                "delivery_fee": float(ctx.get("delivery_fee") or 0),
                "subtotal": float(ctx.get("base_price") or 0),
                "status_note": staff_note,
            }.items():
                if hasattr(Order, k):
                    order_kw[k] = v
            order = Order(**order_kw)
            db.add(order)
            await db.flush()
            conv.state = "await_service"
            _save_ctx(conv, {})
            reply = (
                f"Got it — payment proof received.\n"
                f"Order *#{order.id}* is with our team for confirmation.\n"
                f"We will update you once verified. Thank you."
            )
        else:
            reply = "Please send the *payment screenshot*, or type *menu* to start again."

    else:
        system = (
            f"You are a real staff member at {company.name} (printing). "
            f"Reply in 1-3 short natural lines. Not robotic. Currency {company.currency}. "
            f"Services:\n{svc_text}\nIf ordering, ask them to pick a service number. Never invent prices."
        )
        ai = await grok_chat(system, text or "hello")
        reply = ai or (_greet(company) + "\n\n*Services:*\n" + svc_text + "\n\nReply with a number.")
        if not ai:
            conv.state = "await_service"

    if reply:
        db.add(Message(conversation_id=conv.id, direction="outbound", body=reply))
        await db.commit()
        await send_text(link.phone_number_id, link.access_token, from_wa, reply)
    else:
        await db.commit()
