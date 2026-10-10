"""Send WhatsApp Cloud API messages (text, buttons, lists, media)."""
from __future__ import annotations
import asyncio
import httpx

GRAPH = "https://graph.facebook.com/v21.0"


def _digits(wa_id: str) -> str:
    return "".join(c for c in (wa_id or "") if c.isdigit())


async def _post(phone_number_id: str, access_token: str, payload: dict, *, retries: int = 2) -> bool:
    if not phone_number_id or not access_token:
        print("wa_send_skip missing phone_number_id or token")
        return False
    token = (access_token or "").strip()
    url = f"{GRAPH}/{str(phone_number_id).strip()}/messages"
    last_err = ""
    for attempt in range(max(1, retries)):
        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                r = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                if r.status_code < 300:
                    try:
                        data = r.json()
                        mid = ((data.get("messages") or [{}])[0]).get("id")
                    except Exception:
                        mid = None
                    print("wa_send_ok type=%s to=%s mid=%s" % (
                        (payload or {}).get("type"),
                        (payload or {}).get("to"),
                        mid,
                    ))
                    return True
                last_err = (r.text or "")[:400]
                print(
                    "wa_send_fail attempt=%s status=%s type=%s to=%s body=%s"
                    % (attempt + 1, r.status_code, (payload or {}).get("type"), (payload or {}).get("to"), last_err)
                )
                # 401/403 = bad token — do not retry
                if r.status_code in (401, 403):
                    break
                # 429/5xx — brief backoff
                if r.status_code in (429, 500, 502, 503, 504):
                    await asyncio.sleep(0.6 * (attempt + 1))
                    continue
                break
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            print("wa_send_exc attempt=%s %s" % (attempt + 1, last_err))
            await asyncio.sleep(0.4 * (attempt + 1))
    print("wa_send_give_up type=%s err=%s" % ((payload or {}).get("type"), last_err[:200]))
    return False


async def send_text(phone_number_id: str, access_token: str, to_wa_id: str, body: str) -> bool:
    """Send WhatsApp text. Logs Graph status (never the access token)."""
    to = _digits(to_wa_id)
    if not to or not (body or "").strip():
        print("wa_send_text_skip empty to or body")
        return False
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": body[:4090]},
    }
    return await _post(phone_number_id, access_token, payload, retries=3)


async def send_buttons(
    phone_number_id: str,
    access_token: str,
    to_wa_id: str,
    body: str,
    buttons: list[tuple[str, str]],
    header: str | None = None,
) -> bool:
    to = _digits(to_wa_id)
    if not to or not body or not buttons:
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
        "recipient_type": "individual",
        "to": to,
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
    to = _digits(to_wa_id)
    if not to or not body or not rows:
        return False
    sections = [{
        "title": "Options",
        "rows": [
            {"id": str(r[0])[:200], "title": str(r[1])[:24], "description": str(r[2] or "")[:72]}
            for r in rows[:10]
        ],
    }]
    interactive = {
        "type": "list",
        "body": {"text": body[:1024]},
        "action": {"button": (button_label or "View")[:20], "sections": sections},
    }
    if header:
        interactive["header"] = {"type": "text", "text": header[:60]}
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": interactive,
    }
    return await _post(phone_number_id, access_token, payload)


async def send_typing(phone_number_id: str, access_token: str, to_wa_id: str, message_id: str | None = None) -> bool:
    """Mark message as read + typing indicator (best effort)."""
    to = _digits(to_wa_id)
    if not phone_number_id or not access_token or not to:
        return False
    token = (access_token or "").strip()
    url = f"{GRAPH}/{str(phone_number_id).strip()}/messages"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if message_id:
                await client.post(
                    url,
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    json={
                        "messaging_product": "whatsapp",
                        "status": "read",
                        "message_id": message_id,
                        "typing_indicator": {"type": "text"},
                    },
                )
            return True
    except Exception as e:
        print("wa_typing_exc", type(e).__name__, e)
        return False


async def download_media(media_id: str, access_token: str) -> tuple[bytes | None, str | None]:
    if not media_id or not access_token:
        return None, None
    token = access_token.strip()
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            r = await client.get(
                f"{GRAPH}/{media_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if r.status_code >= 300:
                print("wa_media_meta_fail", r.status_code, (r.text or "")[:200])
                return None, None
            meta = r.json()
            media_url = meta.get("url")
            mime = meta.get("mime_type")
            if not media_url:
                return None, None
            r2 = await client.get(media_url, headers={"Authorization": f"Bearer {token}"})
            if r2.status_code >= 300:
                print("wa_media_dl_fail", r2.status_code)
                return None, None
            return r2.content, mime
    except Exception as e:
        print("wa_media_exc", type(e).__name__, e)
        return None, None


async def upload_media_bytes(
    phone_number_id: str,
    access_token: str,
    data: bytes,
    mime: str,
    filename: str,
) -> str | None:
    if not phone_number_id or not access_token or not data:
        return None
    token = access_token.strip()
    url = f"{GRAPH}/{str(phone_number_id).strip()}/media"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                files={"file": (filename or "file.bin", data, mime or "application/octet-stream")},
                data={"messaging_product": "whatsapp", "type": mime or "application/octet-stream"},
            )
            if r.status_code >= 300:
                print("wa_upload_fail", r.status_code, (r.text or "")[:200])
                return None
            return (r.json() or {}).get("id")
    except Exception as e:
        print("wa_upload_exc", type(e).__name__, e)
        return None


async def send_image_id(
    phone_number_id: str,
    access_token: str,
    to_wa_id: str,
    media_id: str,
    caption: str | None = None,
) -> bool:
    to = _digits(to_wa_id)
    if not to or not media_id:
        return False
    image: dict = {"id": media_id}
    if caption:
        image["caption"] = caption[:1024]
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "image",
        "image": image,
    }
    return await _post(phone_number_id, access_token, payload)


async def send_image(
    phone_number_id: str,
    access_token: str,
    to_wa_id: str,
    image_url: str,
    caption: str | None = None,
) -> bool:
    to = _digits(to_wa_id)
    if not to or not image_url:
        return False
    image: dict = {"link": image_url}
    if caption:
        image["caption"] = caption[:1024]
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "image",
        "image": image,
    }
    return await _post(phone_number_id, access_token, payload)
