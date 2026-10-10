"""Background WhatsApp message processor — webhook returns 200 immediately."""
from __future__ import annotations

import logging
from typing import Any

from app.database import AsyncSessionLocal
from app.services.bot_engine import handle_inbound

log = logging.getLogger("client_raq.webhook_bg")

# Serialize processing per customer so concurrent webhooks don't corrupt state
import asyncio
_customer_locks: dict[str, asyncio.Lock] = {}

def _lock_for(key: str) -> asyncio.Lock:
    if key not in _customer_locks:
        _customer_locks[key] = asyncio.Lock()
    return _customer_locks[key]


async def process_whatsapp_payload(body: dict[str, Any]) -> None:
    """Run outside the webhook request lifecycle with its own DB session."""
    if not body:
        log.info("webhook_empty_body")
        return
    try:
        async with AsyncSessionLocal() as db:
            for entry in body.get("entry", []) or []:
                for change in entry.get("changes", []) or []:
                    value = change.get("value", {}) or {}
                    metadata = value.get("metadata", {}) or {}
                    phone_number_id = metadata.get("phone_number_id") or ""
                    msgs = value.get("messages", []) or []
                    log.info(
                        "webhook_batch phone_number_id=%s messages=%s",
                        phone_number_id,
                        len(msgs),
                    )
                    for msg in msgs:
                        await _one_message(db, phone_number_id, msg)
    except Exception as e:
        log.exception("bg_webhook %s", e)


async def _one_message(db, phone_number_id: str, msg: dict) -> None:
    from_wa = msg.get("from") or ""
    wa_message_id = msg.get("id")
    log.info(
        "webhook_msg id=%s from=%s type=%s phone_number_id=%s",
        wa_message_id,
        from_wa,
        msg.get("type"),
        phone_number_id,
    )
    if not phone_number_id or not from_wa:
        log.warning("webhook_skip missing phone_number_id or from")
        return

    text = None
    media_id = None
    media_kind = None
    button_id = None
    list_id = None
    msg_type = msg.get("type") or ""

    if msg_type == "text":
        text = (msg.get("text") or {}).get("body")
    elif msg_type == "image":
        media_id = (msg.get("image") or {}).get("id")
        text = (msg.get("image") or {}).get("caption") or ""
        media_kind = "image"
    elif msg_type == "document":
        media_id = (msg.get("document") or {}).get("id")
        text = (msg.get("document") or {}).get("caption") or (msg.get("document") or {}).get("filename") or ""
        media_kind = "document"
    elif msg_type in ("audio", "voice"):
        media_id = (msg.get(msg_type) or {}).get("id")
        media_kind = "audio"
    elif msg_type == "video":
        media_id = (msg.get("video") or {}).get("id")
        text = (msg.get("video") or {}).get("caption") or ""
        media_kind = "video"
    elif msg_type == "sticker":
        media_id = (msg.get("sticker") or {}).get("id")
        media_kind = "sticker"
    elif msg_type == "interactive":
        inter = msg.get("interactive") or {}
        if inter.get("type") == "button_reply":
            button_id = (inter.get("button_reply") or {}).get("id")
            text = (inter.get("button_reply") or {}).get("title") or ""
        elif inter.get("type") == "list_reply":
            list_id = (inter.get("list_reply") or {}).get("id")
            text = (inter.get("list_reply") or {}).get("title") or ""
    else:
        log.info("webhook_unsupported_type %s", msg_type)
        # Still try handle with empty text so we can send a fallback

    lock = _lock_for(f"{phone_number_id}:{from_wa}")
    try:
        async with lock:
            await handle_inbound(
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
        try:
            await db.commit()
        except Exception:
            pass
        log.info("webhook_done id=%s", wa_message_id)
    except Exception as e:
        log.exception("handle_inbound failed id=%s err=%s", wa_message_id, e)
        try:
            await db.rollback()
        except Exception:
            pass
        # Last-resort: still try to text the customer so typing is never silent
        try:
            from app.services.whatsapp_send import send_text
            from app.models.company import CompanyWhatsAppNumber
            from sqlalchemy import select
            link = (await db.execute(
                select(CompanyWhatsAppNumber).where(
                    CompanyWhatsAppNumber.phone_number_id == phone_number_id,
                    CompanyWhatsAppNumber.is_active == True,  # noqa: E712
                )
            )).scalar_one_or_none()
            if link and link.access_token and from_wa:
                # Prefer natural short reply — never a robotic form dump
                import traceback
                traceback.print_exc()
                print("bot_trace CRASH", type(e).__name__, e)
                try:
                    from app.services.client_raq_agent import _rule_reply
                    from app.models.company import Company
                    from sqlalchemy import select as _s
                    from app.models.company import CompanyWhatsAppNumber
                    _link = (await db.execute(
                        _s(CompanyWhatsAppNumber).where(
                            CompanyWhatsAppNumber.phone_number_id == phone_number_id
                        )
                    )).scalar_one_or_none()
                    _co = await db.get(Company, _link.company_id) if _link else None
                    em = _rule_reply(_co, text or "", {}) if _co else "I got your message — how can we help?"
                except Exception:
                    em = "I got your message — tell me what you need and I will help."

                await send_text(
                    link.phone_number_id,
                    link.access_token,
                    from_wa,
                    em,
                )
                print("bot_trace emergency_reply sent err=%r" % (e,))
        except Exception as e3:
            print("bot_trace emergency_reply_fail", type(e3).__name__, e3)
