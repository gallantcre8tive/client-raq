"""Telegram channel adapter — same conversation engine as WhatsApp."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.company import Company

router = APIRouter(tags=["telegram"])


async def _company_by_telegram_token(db: AsyncSession, token: str) -> Company | None:
    if not token:
        return None
    try:
        row = (await db.execute(text(
            "SELECT id FROM companies WHERE telegram_bot_token = :t AND COALESCE(telegram_enabled, false) = true LIMIT 1"
        ), {"t": token})).first()
        if not row:
            return None
        return (await db.execute(select(Company).where(Company.id == int(row[0])))).scalars().first()
    except Exception as e:
        print("telegram_company_lookup", e)
        return None


async def send_telegram_text(bot_token: str, chat_id: str | int, text_body: str) -> dict[str, Any]:
    import httpx
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(url, json={"chat_id": chat_id, "text": (text_body or "")[:4000]})
            return {"ok": r.status_code < 400, "status": r.status_code, "body": r.text[:500]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/webhook/telegram/{company_slug}")
async def telegram_webhook(
    company_slug: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Per-company Telegram webhook. Set in BotFather via setWebhook."""
    payload = await request.json()
    print("telegram_webhook", company_slug, list(payload.keys()))

    company = (await db.execute(select(Company).where(Company.slug == company_slug))).scalars().first()
    if not company:
        return JSONResponse({"ok": False, "error": "company"}, status_code=404)

    # entitlement
    try:
        from app.services.billing_service import get_company_subscription, subscription_is_live
        sub = await get_company_subscription(db, company.id)
        if sub and not getattr(sub, "channel_telegram", False):
            return JSONResponse({"ok": True, "skipped": "telegram_not_entitled"})
        if sub is not None and not subscription_is_live(sub):
            return JSONResponse({"ok": True, "skipped": "subscription_paused"})
    except Exception as e:
        print("tg_sub_check", e)

    token = getattr(company, "telegram_bot_token", None) or ""
    try:
        from app.services.token_crypto import decrypt_secret
        token = decrypt_secret(token) or token
    except Exception:
        pass
    if not token:
        return JSONResponse({"ok": False, "error": "no_token"}, status_code=400)

    message = payload.get("message") or payload.get("edited_message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text_body = (message.get("text") or "").strip()
    if not chat_id:
        return JSONResponse({"ok": True})

    # Voice / photo notes as captions for now
    if not text_body and message.get("caption"):
        text_body = message.get("caption")
    if not text_body and message.get("voice"):
        text_body = "[voice note — transcription pending]"
    if not text_body:
        text_body = "[message received]"

    # Reuse agent path lightly via bot_engine-style handling
    try:
        from app.services.client_raq_agent import run_agent
        from app.models.conversation import Conversation, Message
        from sqlalchemy import select as sel
        import json

        wa_fake = f"tg:{chat_id}"
        conv = (await db.execute(
            sel(Conversation).where(
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
                await notify_new_customer(db, company_id=company.id, customer_label=wa_fake, conversation_id=conv.id)
            except Exception:
                pass

        ctx = {}
        try:
            ctx = json.loads(conv.context_json or "{}")
        except Exception:
            ctx = {}

        db.add(Message(
            conversation_id=conv.id,
            company_id=company.id,
            direction="inbound",
            body=text_body[:4000],
        ))
        await db.commit()

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
            pass

        if needs_human:
            try:
                from app.services.admin_notify import notify_human_needed
                await notify_human_needed(db, company_id=company.id, reason="Telegram customer needs help", conversation_id=conv.id, customer_label=wa_fake)
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
        print("telegram_handle_fail", type(e).__name__, e)
        try:
            await send_telegram_text(token, chat_id, "Thanks — our team will reply shortly.")
        except Exception:
            pass

    return JSONResponse({"ok": True})
