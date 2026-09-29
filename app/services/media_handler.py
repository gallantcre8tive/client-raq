"""Media download, storage, optional voice transcription, payment screenshot notes."""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.conversation import Attachment
from app.services.whatsapp_send import download_media

log = logging.getLogger("client_raq.media")


def _media_root() -> Path:
    s = get_settings()
    root = Path(getattr(s, "MEDIA_ROOT", None) or "uploads")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _ext_from_mime(mime: str | None) -> str:
    m = (mime or "").lower()
    if "png" in m:
        return ".png"
    if "jpeg" in m or "jpg" in m:
        return ".jpg"
    if "webp" in m:
        return ".webp"
    if "pdf" in m:
        return ".pdf"
    if "ogg" in m or "opus" in m:
        return ".ogg"
    if "mpeg" in m or "mp3" in m:
        return ".mp3"
    if "mp4" in m or "video" in m:
        return ".mp4"
    if "wav" in m:
        return ".wav"
    return ".bin"


async def store_whatsapp_media(
    db: AsyncSession,
    *,
    company_id: int,
    customer_wa_id: str,
    conversation_id: int | None,
    access_token: str,
    wa_media_id: str,
    kind: str = "file",
    original_name: str | None = None,
    order_id: int | None = None,
    message_id: int | None = None,
) -> Attachment | None:
    """Download WhatsApp media and persist under uploads/{company_id}/..."""
    blob = await download_media(wa_media_id, access_token)
    if not blob:
        log.warning("media_download_failed id=%s", wa_media_id)
        return None
    content, mime = blob
    company_dir = _media_root() / str(company_id)
    company_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{uuid.uuid4().hex}{_ext_from_mime(mime)}"
    path = company_dir / fname
    path.write_bytes(content)
    rel = f"{company_id}/{fname}"
    att = Attachment(
        company_id=company_id,
        customer_wa_id=customer_wa_id,
        conversation_id=conversation_id,
        order_id=order_id,
        message_id=message_id,
        kind=kind,
        original_name=original_name or fname,
        mime_type=mime,
        storage_path=rel,
        wa_media_id=wa_media_id,
        size_bytes=len(content),
    )
    db.add(att)
    await db.flush()
    return att


async def transcribe_audio(content: bytes, mime: str | None = None) -> str | None:
    """Optional Groq Whisper transcription. Returns None if GROQ_API_KEY missing."""
    key = (get_settings().GROQ_API_KEY or "").strip()
    if not key or not content:
        return None
    try:
        # Groq OpenAI-compatible audio transcriptions
        files = {
            "file": ("audio.ogg", content, mime or "audio/ogg"),
        }
        data = {"model": "whisper-large-v3", "language": "en"}
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}"},
                files=files,
                data=data,
            )
            if r.status_code >= 400:
                log.warning("groq_whisper %s %s", r.status_code, (r.text or "")[:200])
                return None
            return (r.json().get("text") or "").strip() or None
    except Exception as e:
        log.warning("transcribe_fail %s", type(e).__name__)
        return None


async def describe_image_optional(content: bytes, mime: str | None, prompt: str) -> str | None:
    """Best-effort image note via Grok if key present. Fail soft."""
    from app.services.grok_client import grok_vision
    try:
        return await grok_vision(
            "You help a print shop review customer images.",
            prompt,
            content,
            mime or "image/jpeg",
        )
    except Exception:
        return None
