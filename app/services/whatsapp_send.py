"""Send WhatsApp Cloud API messages (text, buttons, lists, media)."""
from __future__ import annotations
import httpx

GRAPH = "https://graph.facebook.com/v21.0"


async def _post(phone_number_id: str, access_token: str, payload: dict) -> bool:
    if not phone_number_id or not access_token:
        return False
    url = f"{GRAPH}/{phone_number_id}/messages"
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


async def send_text(phone_number_id: str, access_token: str, to_wa_id: str, body: str) -> bool:
    """Send WhatsApp text. Logs status only (never the access token)."""
    if not to_wa_id or not body:
        return False
    payload = {
        "messaging_product": "whatsapp",
        "to": to_wa_id.replace("+", "").replace(" ", ""),
        "type": "text",
        "text": {"body": body[:4090]},
    }
    return await _post(phone_number_id, access_token, payload)


async def send_buttons(
    phone_number_id: str,
    access_token: str,
    to_wa_id: str,
    body: str,
    buttons: list[tuple[str, str]],
    header: str | None = None,
) -> bool:
    """buttons: list of (id, title) max 3. title max 20 chars."""
    if not to_wa_id or not body or not buttons:
        return False
    btns = []
    for bid, title in buttons[:3]:
        btns.append({
            "type": "reply",
            "reply": {"id": str(bid)[:256], "title": str(title)[:20]},
        })
    interactive = {
        "type": "button",
        "body": {"text": body[:1024]},
        "action": {"buttons": btns},
    }
    if header:
        interactive["header"] = {"type": "text", "text": header[:60]}
    payload = {
        "messaging_product": "whatsapp",
        "to": to_wa_id.replace("+", "").replace(" ", ""),
        "type": "interactive",
        "interactive": interactive,
    }
    return await _post(phone_number_id, access_token, payload)


async def send_list(
    phone_number_id: str,
    access_token: str,
    to_wa_id: str,
    body: str,
    button_label: str,
    rows: list[tuple[str, str, str]],
    header: str | None = None,
) -> bool:
    """rows: list of (id, title, description) max 10. title max 24, desc max 72."""
    if not to_wa_id or not body or not rows:
        return False
    section_rows = []
    for rid, title, desc in rows[:10]:
        row = {"id": str(rid)[:200], "title": str(title)[:24]}
        if desc:
            row["description"] = str(desc)[:72]
        section_rows.append(row)
    interactive = {
        "type": "list",
        "body": {"text": body[:1024]},
        "action": {
            "button": (button_label or "View options")[:20],
            "sections": [{"title": "Options", "rows": section_rows}],
        },
    }
    if header:
        interactive["header"] = {"type": "text", "text": header[:60]}
    payload = {
        "messaging_product": "whatsapp",
        "to": to_wa_id.replace("+", "").replace(" ", ""),
        "type": "interactive",
        "interactive": interactive,
    }
    return await _post(phone_number_id, access_token, payload)


async def download_media(media_id: str, access_token: str):
    if not media_id or not access_token:
        return None
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            meta = await client.get(
                f"{GRAPH}/{media_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if meta.status_code != 200:
                return None
            data = meta.json()
            url = data.get("url")
            mime = data.get("mime_type") or "image/jpeg"
            if not url:
                return None
            file_r = await client.get(url, headers={"Authorization": f"Bearer {access_token}"})
            if file_r.status_code != 200:
                return None
            return file_r.content, mime
    except Exception:
        return None


async def send_typing(phone_number_id: str, access_token: str, to_wa_id: str, message_id: str | None = None) -> bool:
    """Show WhatsApp typing indicator (Cloud API)."""
    if not phone_number_id or not access_token or not to_wa_id:
        return False
    to = to_wa_id.replace("+", "").replace(" ", "")
    # Mark as read + typing when we have message id
    if message_id:
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
            "typing_indicator": {"type": "text"},
        }
    else:
        return False
    return await _post(phone_number_id, access_token, payload)



async def send_image(
    phone_number_id: str,
    access_token: str,
    to_wa_id: str,
    *,
    image_url: str | None = None,
    media_id: str | None = None,
    caption: str | None = None,
) -> bool:
    """Send image by public URL or uploaded media id."""
    if not to_wa_id or not (image_url or media_id):
        return False
    image: dict = {}
    if media_id:
        image["id"] = media_id
    else:
        image["link"] = image_url
    if caption:
        image["caption"] = caption[:1024]
    payload = {
        "messaging_product": "whatsapp",
        "to": to_wa_id.replace("+", "").replace(" ", ""),
        "type": "image",
        "image": image,
    }
    return await _post(phone_number_id, access_token, payload)
