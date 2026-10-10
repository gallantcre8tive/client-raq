"""Telegram channel — same conversation engine as WhatsApp."""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.company import Company
from app.models.conversation import Conversation, Message

router = APIRouter(tags=["telegram"])
log = logging.getLogger("client_raq.telegram")


async def send_telegram_text(bot_token: str, chat_id: str | int, text_body: str) -> dict[str, Any]:
    if not bot_token or not text_body:
        return {"ok": False}
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(url, json={
                "chat_id": chat_id,
                "text": (text_body or "")[:4000],
            })
            return r.json() if r.content else {"ok": r.status_code < 400}
    except Exception as e:
        log.warning("tg_send %s", e)
        return {"ok": False, "error": str(e)}


async def send_telegram_chat_action(bot_token: str, chat_id: str | int, action: str = "typing") -> None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"https://api.telegram.org/bot{bot_token}/sendChatAction",
                json={"chat_id": chat_id, "action": action},
            )
    except Exception:
        pass


@router.post("/webhook/telegram/{company_slug}")
async def telegram_webhook(
    company_slug: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Per-company Telegram webhook. Registered via setWebhook when company saves token."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": True, "skipped": "bad_json"})

    log.info("telegram_webhook slug=%s keys=%s", company_slug, list(payload.keys()))

    # Load company by slug
    company = None
    try:
        company = (await db.execute(
            select(Company).where(Company.slug == company_slug)
        )).scalars().first()
    except Exception as e:
        log.warning("tg_company_orm %s", e)
        try:
            await db.rollback()
        except Exception:
            pass
    if not company:
        try:
            row = (await db.execute(text(
                "SELECT id, name, slug, telegram_bot_token, COALESCE(telegram_enabled,false) AS telegram_enabled, "
                "business_type, currency, greeting_message FROM companies WHERE slug = :s LIMIT 1"
            ), {"s": company_slug})).mappings().first()
            if row:
                company = (await db.execute(select(Company).where(Company.id == int(row["id"])))).scalars().first()
        except Exception as e:
            log.warning("tg_company_sql %s", e)
            try:
                await db.rollback()
            except Exception:
                pass
    if not company:
        return JSONResponse({"ok": True, "skipped": "unknown_company"})

    token = (getattr(company, "telegram_bot_token", None) or "").strip()
    enabled = bool(getattr(company, "telegram_enabled", False))
    if not token or not enabled:
        return JSONResponse({"ok": True, "skipped": "telegram_disabled"})

    # Subscription / channel entitlement
    try:
        from app.services.subscription_access import assert_bot_may_reply, assert_channel_allowed
        ok_ch, msg_ch = await assert_channel_allowed(db, int(company.id), "telegram")
        if not ok_ch:
            # Optionally notify customer once
            msg = payload.get("message") or payload.get("edited_message") or {}
            chat = (msg.get("chat") or {})
            chat_id = chat.get("id")
            if chat_id:
                await send_telegram_text(token, chat_id, msg_ch or "Telegram is not active on this plan.")
            return JSONResponse({"ok": True, "skipped": "not_entitled"})
        ok_bot, pause_msg = await assert_bot_may_reply(db, int(company.id))
        if not ok_bot:
            msg = payload.get("message") or payload.get("edited_message") or {}
            chat_id = (msg.get("chat") or {}).get("id")
            if chat_id:
                await send_telegram_text(token, chat_id, pause_msg)
            return JSONResponse({"ok": True, "skipped": "subscription"})
    except Exception as e:
        log.warning("tg_sub_gate %s", e)

    msg = payload.get("message") or payload.get("edited_message")
    if not msg:
        return JSONResponse({"ok": True, "skipped": "no_message"})

    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return JSONResponse({"ok": True, "skipped": "no_chat"})

    # Text body (also accept captions on photos)
    text_body = (msg.get("text") or msg.get("caption") or "").strip()
    if not text_body:
        # non-text: acknowledge
        await send_telegram_text(
            token, chat_id,
            "I received your file. Please also send a short text describing what you need.",
        )
        return JSONResponse({"ok": True, "skipped": "non_text"})

    # Stable customer id for multi-tenant isolation
    wa_fake = f"tg:{chat_id}"
    from_user = msg.get("from") or {}
    display = (
        (from_user.get("first_name") or "")
        + (" " + (from_user.get("last_name") or "") if from_user.get("last_name") else "")
    ).strip() or wa_fake

    try:
        await send_telegram_chat_action(token, chat_id, "typing")

        conv = (await db.execute(
            select(Conversation).where(
                Conversation.company_id == company.id,
                Conversation.customer_wa_id == wa_fake,
            )
        )).scalars().first()
        if not conv:
            conv = Conversation(
                company_id=company.id,
                customer_wa_id=wa_fake,
                state="new",
                context_json="{}",
            )
            db.add(conv)
            await db.flush()
            try:
                from app.services.admin_notify import notify_new_customer
                await notify_new_customer(
                    db,
                    company_id=int(company.id),
                    customer_label=display,
                    conversation_id=int(conv.id),
                )
            except Exception:
                pass

        ctx: dict[str, Any] = {}
        try:
            ctx = json.loads(conv.context_json or "{}")
        except Exception:
            ctx = {}
        if display and display != wa_fake:
            ctx["customer_name"] = display

        db.add(Message(
            conversation_id=conv.id,
            company_id=company.id,
            direction="inbound",
            body=text_body[:4000],
        ))
        await db.commit()

        # Same agent as WhatsApp
        from app.services.client_raq_agent import run_agent
        recent = []
        try:
            rows = (await db.execute(
                select(Message)
                .where(Message.conversation_id == conv.id)
                .order_by(Message.id.desc())
                .limit(16)
            )).scalars().all()
            for m in reversed(list(rows)):
                recent.append({
                    "direction": m.direction,
                    "body": (m.body or "")[:1000],
                })
        except Exception:
            recent = []

        reply, ctx, needs_human = await run_agent(
            db,
            company=company,
            conv=conv,
            ctx=ctx,
            from_wa=wa_fake,
            customer_message=text_body,
            recent=recent,
        )
        try:
            conv.context_json = json.dumps(ctx)[:8000]
            await db.commit()
        except Exception:
            try:
                await db.rollback()
            except Exception:
                pass

        if needs_human:
            try:
                from app.services.admin_notify import notify_human_needed
                await notify_human_needed(
                    db,
                    company_id=int(company.id),
                    reason="Telegram customer needs help",
                    conversation_id=int(conv.id),
                    customer_label=display,
                )
            except Exception:
                pass

        if reply:
            await send_telegram_text(token, chat_id, reply)
            try:
                db.add(Message(
                    conversation_id=conv.id,
                    company_id=company.id,
                    direction="outbound",
                    body=reply[:4000],
                ))
                await db.commit()
            except Exception:
                pass
    except Exception as e:
        log.exception("telegram_handle_fail %s", e)
        try:
            await send_telegram_text(token, chat_id, "Thanks — our team will reply shortly.")
        except Exception:
            pass

    return JSONResponse({"ok": True})
