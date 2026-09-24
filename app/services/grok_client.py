"""Grok (xAI) client with graceful fallback when key missing or exhausted."""
from __future__ import annotations
import httpx
from app.config import get_settings

settings = get_settings()

async def grok_chat(system: str, user_message: str, history: list[dict] | None = None) -> str | None:
    """Return model text or None if unavailable (caller uses rule-based fallback)."""
    key = (settings.GROK_API_KEY or "").strip()
    if not key:
        return None
    messages = [{"role": "system", "content": system}]
    if history:
        messages.extend(history[-8:])
    messages.append({"role": "user", "content": user_message})
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            r = await client.post(
                f"{settings.GROK_BASE_URL.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": settings.GROK_MODEL,
                    "messages": messages,
                    "temperature": 0.4,
                    "max_tokens": 600,
                },
            )
            if r.status_code == 429 or r.status_code >= 500:
                return None
            if r.status_code >= 400:
                return None
            data = r.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception:
        return None


async def grok_vision(system: str, prompt: str, image_bytes: bytes, mime: str = "image/jpeg") -> str | None:
    import base64
    key = (settings.GROK_API_KEY or "").strip()
    if not key or not image_bytes:
        return None
    b64 = base64.b64encode(image_bytes).decode("ascii")
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ]},
    ]
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                f"{settings.GROK_BASE_URL.rstrip("/")}/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": settings.GROK_MODEL, "messages": messages, "max_tokens": 180},
            )
            if r.status_code != 200:
                return None
            return (r.json()["choices"][0]["message"]["content"] or "").strip() or None
    except Exception:
        return None
