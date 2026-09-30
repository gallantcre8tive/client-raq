"""Serve attachments: local file or re-download from WhatsApp Cloud API."""
from __future__ import annotations
from pathlib import Path
from typing import Tuple

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import get_settings
from app.models.conversation import Attachment
from app.models.company import CompanyWhatsAppNumber
from app.services.whatsapp_send import download_media


def media_root() -> Path:
    s = get_settings()
    root = Path(getattr(s, "MEDIA_ROOT", None) or "uploads")
    if not root.is_absolute():
        root = (Path(__file__).resolve().parent.parent / root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def local_path_for(att: Attachment) -> Path | None:
    sp = (att.storage_path or "").replace("\\", "/").lstrip("/")
    if not sp:
        return None
    # strip leading media/ or uploads/
    for prefix in ("media/", "uploads/"):
        if sp.startswith(prefix):
            sp = sp[len(prefix):]
    p = media_root() / sp
    if p.is_file():
        return p
    # try basename only under company folder
    if att.company_id:
        alt = media_root() / str(att.company_id) / Path(sp).name
        if alt.is_file():
            return alt
    bare = media_root() / Path(sp).name
    if bare.is_file():
        return bare
    return None


async def resolve_bytes(
    db: AsyncSession,
    att: Attachment,
) -> Tuple[bytes, str, str] | None:
    """Return (content, mime, filename) or None."""
    mime = att.mime_type or "application/octet-stream"
    fname = att.original_name or Path(att.storage_path or "file.bin").name

    lp = local_path_for(att)
    if lp:
        return lp.read_bytes(), mime, fname

    # Re-download from WhatsApp if we still have media id + company token
    mid = (att.wa_media_id or "").strip()
    if not mid:
        return None
    link = (await db.execute(
        select(CompanyWhatsAppNumber).where(
            CompanyWhatsAppNumber.company_id == att.company_id,
            CompanyWhatsAppNumber.is_active == True,  # noqa: E712
        )
    )).scalars().first()
    if not link or not link.access_token:
        return None
    blob = await download_media(mid, link.access_token)
    if not blob:
        return None
    content, got_mime = blob
    if got_mime:
        mime = got_mime
    # Cache to disk for next time
    try:
        company_dir = media_root() / str(att.company_id)
        company_dir.mkdir(parents=True, exist_ok=True)
        cache_name = Path(att.storage_path or fname).name
        if not cache_name or cache_name == ".":
            cache_name = f"{mid}.bin"
        dest = company_dir / cache_name
        dest.write_bytes(content)
        att.storage_path = f"{att.company_id}/{cache_name}"
        if got_mime:
            att.mime_type = got_mime
        await db.commit()
    except Exception as e:
        print("cache_att", type(e).__name__, e)
    return content, mime, fname
