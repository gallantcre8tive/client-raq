"""Send WhatsApp Cloud API messages."""
from __future__ import annotations
import httpx

GRAPH = "https://graph.facebook.com/v21.0"

async def send_text(phone_number_id: str, access_token: str, to_wa_id: str, body: str) -> bool:
    if not phone_number_id or not access_token or not to_wa_id or not body:
        return False
    url = f"{GRAPH}/{phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": to_wa_id.replace("+", "").replace(" ", ""),
        "type": "text",
        "text": {"body": body[:4090]},
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                url,
                headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
                json=payload,
            )
            return r.status_code < 300
    except Exception:
        return False


async def download_media(media_id: str, access_token: str):
    import httpx
    GRAPH = "https://graph.facebook.com/v21.0"
    if not media_id or not access_token:
        return None
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            meta = await client.get(f"{GRAPH}/{media_id}", headers={"Authorization": f"Bearer {access_token}"})
            if meta.status_code != 200:
                return None
            url = meta.json().get("url")
            mime = meta.json().get("mime_type") or "image/jpeg"
            if not url:
                return None
            data = await client.get(url, headers={"Authorization": f"Bearer {access_token}"})
            if data.status_code != 200:
                return None
            return data.content, mime
    except Exception:
        return None
