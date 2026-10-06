"""Multimodal vision layer for Client-RaQ.

Analyzes images/documents with company + conversation context.
Structured JSON only. Never marks payments as verified — admin decides.
Uses Grok/xAI vision when available; fails soft without a key.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any

import httpx

from app.config import get_settings

log = logging.getLogger("client_raq.vision")

# Confidence bands (guidance only — models are not always calibrated)
HIGH = 0.85
MED = 0.60


def _api_key() -> str:
    s = get_settings()
    return (getattr(s, "GROK_API_KEY", None) or getattr(s, "XAI_API_KEY", None) or "").strip()


def _base_url() -> str:
    return (get_settings().GROK_BASE_URL or "https://api.x.ai/v1").rstrip("/")


def _model() -> str:
    s = get_settings()
    # Prefer a vision-capable model name if configured; fall back to chat model
    return (
        (getattr(s, "GROK_VISION_MODEL", None) or "").strip()
        or (getattr(s, "XAI_MODEL", None) or "").strip()
        or (getattr(s, "GROK_MODEL", None) or "grok-2-vision-1212").strip()
        or "grok-2-vision-1212"
    )


def _parse_json(text: str | None) -> dict | None:
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else None
    except Exception:
        m = re.search(r"\{[\s\S]*\}", t)
        if m:
            try:
                obj = json.loads(m.group(0))
                return obj if isinstance(obj, dict) else None
            except Exception:
                return None
    return None


async def _vision_raw(
    system: str,
    user_text: str,
    image_bytes: bytes,
    mime: str = "image/jpeg",
    max_tokens: int = 800,
) -> str | None:
    key = _api_key()
    if not key or not image_bytes:
        return None
    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:{mime or 'image/jpeg'};base64,{b64}"
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]
    # Try primary model, then a couple of known xAI vision-capable names
    models = []
    primary = _model()
    models.append(primary)
    for alt in ("grok-2-vision-1212", "grok-2-vision", "grok-vision-beta"):
        if alt not in models:
            models.append(alt)
    last_err = None
    for model in models:
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                r = await client.post(
                    f"{_base_url()}/chat/completions",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": messages,
                        "max_tokens": max_tokens,
                        "temperature": 0.1,
                    },
                )
                if r.status_code == 200:
                    content = r.json()["choices"][0]["message"]["content"]
                    log.info("vision_ok model=%s len=%s", model, len(content or ""))
                    return (content or "").strip()
                last_err = f"{r.status_code} {(r.text or '')[:180]}"
                log.warning("vision_http model=%s %s", model, last_err)
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            log.warning("vision_exc model=%s %s", model, last_err)
    log.warning("vision_all_failed %s", last_err)
    return None


PAYMENT_SYSTEM = """You analyze payment / transfer receipt screenshots for African and global fintech apps.
Common Nigerian platforms: OPay, PalmPay, Kuda, Moniepoint, GTBank, Access, Zenith, UBA, First Bank, Fidelity, Carbon, Chipper, Flutterwave, Paystack.
Also recognize generic bank transfer slips, PayPal, Cash App, Revolut, Wise, crypto tx screenshots.

Return ONLY valid JSON (no markdown) with this exact shape:
{
  "is_payment_receipt": true or false,
  "detected_type": "payment_receipt" | "not_payment" | "unclear",
  "platform": "OPay" or bank/app name or null,
  "status_shown": "successful" | "pending" | "failed" | "unknown" | null,
  "amount": number or null,
  "currency": "NGN" | "USD" | "EUR" | "GBP" | other ISO or null,
  "sender": string or null,
  "recipient": string or null,
  "recipient_account": string or null,
  "reference": string or null,
  "transaction_date": "YYYY-MM-DD" or free text or null,
  "transaction_type": "transfer" | "payment" | "deposit" | null,
  "narration": string or null,
  "image_quality": "good" | "fair" | "poor",
  "possible_manipulation": "unlikely" | "unknown" | "suspicious",
  "confidence": 0.0 to 1.0,
  "requires_human_verification": true,
  "summary": "one short sentence for the admin"
}

Rules:
- Screenshots can be fake, edited, old, or for another business. ALWAYS set requires_human_verification true.
- Never claim the money was actually received in the company's bank.
- If unclear whether it is a receipt, set is_payment_receipt false and confidence low.
- Extract numbers carefully (Nigerian format may use commas or Naira symbol).
"""


GENERAL_SYSTEM = """You help a multi-business WhatsApp AI (Client-RaQ) understand customer images.
Business types include: printing, payment exchanger, skincare/beauty, phone sales/repair, real estate, logistics, retail, and others.

Return ONLY valid JSON (no markdown):
{
  "media_type": "image",
  "detected_type": "payment_receipt" | "printing_design" | "product" | "reference_photo" | "document_scan" | "id_document" | "screenshot_other" | "unclear" | "other",
  "confidence": 0.0 to 1.0,
  "description": "short factual description of what is visible",
  "extracted_data": {},
  "recommended_action": "human_verification" | "continue_service_flow" | "ask_clarification" | "acknowledge_design" | "match_catalogue" | "escalate_human",
  "requires_admin_notification": true or false,
  "customer_facing_hint": "one short hint the bot may use (no prices invented, no false claims)"
}

Rules:
- Use conversation context and business type when provided.
- Do not invent product names, prices, or catalogue items.
- If the image looks like a payment receipt, set detected_type to payment_receipt and recommended_action human_verification.
- For printing designs / artwork, set detected_type printing_design.
- For product packaging or bottles (skincare, phones, etc.), set product.
- Low confidence → ask_clarification or escalate_human.
"""


async def analyze_payment_receipt(
    image_bytes: bytes,
    mime: str = "image/jpeg",
    *,
    expected_amount: float | None = None,
    expected_currency: str | None = None,
    company_name: str | None = None,
) -> dict[str, Any]:
    """Structured payment-screenshot analysis. Always requires human verification."""
    extra = []
    if expected_amount is not None:
        extra.append(f"Expected order amount (for comparison only): {expected_currency or ''} {expected_amount}")
    if company_name:
        extra.append(f"Business name (possible recipient): {company_name}")
    prompt = "Analyze this image as a possible payment receipt.\n" + "\n".join(extra)
    raw = await _vision_raw(PAYMENT_SYSTEM, prompt, image_bytes, mime)
    data = _parse_json(raw) or {}
    if not data:
        data = {
            "is_payment_receipt": True,  # conservative: still route to admin
            "detected_type": "unclear",
            "confidence": 0.3,
            "summary": "Could not fully read the screenshot. Admin must review the original image.",
            "requires_human_verification": True,
        }
    data["requires_human_verification"] = True
    data.setdefault("detected_type", "payment_receipt" if data.get("is_payment_receipt") else "unclear")
    data.setdefault("confidence", 0.5)
    data["raw_model_text"] = (raw or "")[:1500]
    return data


async def analyze_business_image(
    image_bytes: bytes,
    mime: str = "image/jpeg",
    *,
    business_type: str | None = None,
    company_name: str | None = None,
    conversation_summary: str | None = None,
    customer_caption: str | None = None,
    awaiting: str | None = None,
) -> dict[str, Any]:
    """General image understanding with company/conversation context."""
    ctx_lines = []
    if business_type:
        ctx_lines.append(f"Business type: {business_type}")
    if company_name:
        ctx_lines.append(f"Company: {company_name}")
    if awaiting:
        ctx_lines.append(f"Bot is currently awaiting from customer: {awaiting}")
    if conversation_summary:
        ctx_lines.append(f"Recent conversation context:\n{conversation_summary[:1200]}")
    if customer_caption:
        ctx_lines.append(f"Customer caption/message with this image: {customer_caption}")
    prompt = "Analyze this customer image for the business assistant.\n" + "\n".join(ctx_lines)
    raw = await _vision_raw(GENERAL_SYSTEM, prompt, image_bytes, mime)
    data = _parse_json(raw) or {}
    if not data:
        data = {
            "media_type": "image",
            "detected_type": "unclear",
            "confidence": 0.25,
            "description": "Image received but could not be analyzed automatically.",
            "recommended_action": "ask_clarification",
            "requires_admin_notification": False,
            "customer_facing_hint": "Thanks, I received your image. Could you tell me briefly what you need?",
        }
    data.setdefault("media_type", "image")
    data.setdefault("confidence", 0.5)
    data["raw_model_text"] = (raw or "")[:1500]
    return data


def analysis_to_attachment_notes(analysis: dict[str, Any]) -> str:
    """Serialize analysis for Attachment.notes (JSON string)."""
    try:
        clean = {k: v for k, v in analysis.items() if k != "raw_model_text"}
        return json.dumps(clean, ensure_ascii=False)[:8000]
    except Exception:
        return json.dumps({"summary": str(analysis)[:500]})


def analysis_to_admin_body(analysis: dict[str, Any], *, customer_label: str = "", order_hint: str = "") -> str:
    """Human-readable body for admin notification."""
    lines = []
    if customer_label:
        lines.append(f"Customer: {customer_label}")
    if order_hint:
        lines.append(order_hint)
    if analysis.get("is_payment_receipt") or analysis.get("detected_type") == "payment_receipt":
        amt = analysis.get("amount")
        cur = analysis.get("currency") or "NGN"
        if amt is not None:
            try:
                lines.append(f"Amount shown: {cur} {float(amt):,.0f}")
            except Exception:
                lines.append(f"Amount shown: {amt}")
        if analysis.get("platform"):
            lines.append(f"Platform: {analysis['platform']}")
        if analysis.get("status_shown"):
            lines.append(f"Status shown: {analysis['status_shown']}")
        if analysis.get("recipient"):
            lines.append(f"Recipient: {analysis['recipient']}")
        if analysis.get("reference"):
            lines.append(f"Reference: {analysis['reference']}")
        if analysis.get("transaction_date"):
            lines.append(f"Date: {analysis['transaction_date']}")
        lines.append("Human verification required — do not treat AI as final proof.")
    else:
        if analysis.get("description"):
            lines.append(analysis["description"])
        if analysis.get("detected_type"):
            lines.append(f"Type: {analysis['detected_type']}")
        if analysis.get("summary"):
            lines.append(str(analysis["summary"]))
    conf = analysis.get("confidence")
    if conf is not None:
        try:
            lines.append(f"AI confidence: {float(conf):.0%}")
        except Exception:
            pass
    return "\n".join(lines)[:2000]


def customer_hint_from_analysis(
    analysis: dict[str, Any],
    *,
    is_payment: bool = False,
    business_type: str | None = None,
) -> str:
    """Short safe note to inject into the agent as attachment_note."""
    if is_payment or analysis.get("is_payment_receipt") or analysis.get("detected_type") == "payment_receipt":
        parts = ["Customer sent a payment screenshot."]
        amt = analysis.get("amount")
        cur = analysis.get("currency") or ""
        if amt is not None:
            try:
                parts.append(f"AI read about {cur} {float(amt):,.0f} on the image (not verified).")
            except Exception:
                pass
        if analysis.get("platform"):
            parts.append(f"Platform shown: {analysis['platform']}.")
        parts.append("Tell them staff will verify the payment. Do NOT confirm payment received. Do NOT invent amounts.")
        return " ".join(parts)
    hint = analysis.get("customer_facing_hint") or analysis.get("description") or ""
    dtype = analysis.get("detected_type") or "image"
    conf = float(analysis.get("confidence") or 0)
    if conf < MED:
        return (
            f"Customer sent an image ({dtype}) but AI is unsure. "
            "Acknowledge receipt and ask what they need, or offer to connect them with staff. "
            f"Vision note: {hint}"
        ).strip()
    if dtype == "printing_design":
        return (
            "Customer sent what looks like a print design/artwork. "
            "Acknowledge the design and continue collecting quantity, size, material if still needed. "
            f"Vision: {hint}"
        ).strip()
    if dtype == "product":
        return (
            "Customer sent a product photo. Use company catalogue if relevant; do not invent prices. "
            f"Vision: {hint}"
        ).strip()
    return f"Customer sent an image ({dtype}). {hint}".strip()
