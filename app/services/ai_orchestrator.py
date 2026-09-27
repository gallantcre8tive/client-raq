"""Two-stage AI pipeline: understand → backend facts → natural reply.
Never lets Grok invent prices; backend calculates from company services/variants.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.catalog import Service, ServiceVariant
from app.models.company import Company, PaymentDetail
from app.models.conversation import Message, Order
from app.services.grok_client import generate_reply, understand_message


async def load_recent_messages(db: AsyncSession, conversation_id: int, limit: int = 12) -> list[dict]:
    rows = list(
        (
            await db.execute(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.id.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    rows.reverse()
    out = []
    for m in rows:
        role = "assistant" if m.direction == "outbound" else "user"
        out.append({"role": role, "content": (m.body or "")[:500]})
    return out


def services_summary(services: list, currency: str) -> str:
    parts = []
    for s in services[:40]:
        parts.append(f"{s.name} ({currency} {s.base_price}/{s.unit}, method={getattr(s, 'pricing_method', 'piece')})")
    return "; ".join(parts) if parts else "none configured"


def apply_understanding_to_state(ctx: dict, u: dict) -> dict:
    """Merge structured AI result into conversation context without wiping unknowns."""
    if not u:
        return ctx
    if u.get("product"):
        ctx["ai_product"] = str(u["product"])
    if u.get("quantity"):
        try:
            ctx["qty"] = max(1, int(u["quantity"]))
        except Exception:
            pass
    if u.get("size"):
        ctx["variant_label"] = str(u["size"])
        ctx["size_text"] = str(u["size"])
    if u.get("width") is not None:
        try:
            ctx["width"] = float(u["width"])
        except Exception:
            pass
    if u.get("height") is not None:
        try:
            ctx["height"] = float(u["height"])
        except Exception:
            pass
    if u.get("size_unit"):
        ctx["size_unit"] = u["size_unit"]
    if u.get("design_status") in ("has_design", "needs_design", "received"):
        ctx["design_status"] = u["design_status"]
    if u.get("fulfillment") in ("pickup", "delivery"):
        ctx["fulfillment"] = u["fulfillment"]
    if u.get("delivery_location"):
        ctx["delivery_location"] = str(u["delivery_location"])
    if u.get("language") in ("en", "pidgin"):
        ctx["lang"] = u["language"]
    ctx["last_intent"] = u.get("intent")
    ctx["ai_confidence"] = u.get("confidence")
    return ctx


def match_service(services: list, product_hint: str | None, text: str) -> Any | None:
    if not services:
        return None
    hay = f"{product_hint or ''} {text or ''}".lower()
    # exact-ish name match
    for s in services:
        n = s.name.lower()
        if n in hay or any(w in hay for w in n.split() if len(w) > 3):
            return s
    # keyword map
    keywords = {
        "nylon": ("nylon", "poly bag", "packaging bag"),
        "banner": ("banner", "flex", "roll"),
        "sticker": ("sticker", "sav", "label", "vinyl"),
        "frame": ("frame", "frameless", "acrylic"),
        "shirt": ("shirt", "tee", "hoodie", "dtf", "embroidery"),
        "flyer": ("flyer", "leaflet", "brochure"),
        "jotter": ("jotter", "notebook", "exercise"),
        "pen": ("pen", "biro"),
    }
    for s in services:
        n = s.name.lower()
        for key, words in keywords.items():
            if key in n or any(w in n for w in words):
                if any(w in hay for w in words) or key in hay:
                    return s
    return None


async def match_variant(db: AsyncSession, service_id: int, size_label: str | None) -> ServiceVariant | None:
    if not size_label or not service_id:
        return None
    variants = list(
        (
            await db.execute(
                select(ServiceVariant).where(
                    ServiceVariant.service_id == service_id,
                    ServiceVariant.is_active == True,  # noqa: E712
                )
            )
        )
        .scalars()
        .all()
    )
    low = size_label.lower().replace(" ", "")
    for v in variants:
        lab = v.label.lower().replace(" ", "")
        if lab == low or lab in low or low in lab:
            return v
        # small/medium/large soft match
        if size_label.lower() in ("small", "medium", "large") and size_label.lower() in v.label.lower():
            return v
    return None


def calc_from_service(service, ctx: dict) -> float | None:
    """Backend-only price. Returns None if not enough data."""
    from app.services.bot_engine import _calc_total, _parse_size

    qty = int(ctx.get("qty") or 1)
    unit_price = ctx.get("unit_price")
    if unit_price is not None and getattr(service, "pricing_method", "") == "fixed_size":
        return round(float(unit_price) * qty, 2)
    w = ctx.get("width")
    h = ctx.get("height")
    size_text = ctx.get("size_text") or ""
    if (w is None or h is None) and size_text:
        w, h = _parse_size(size_text)
    method = getattr(service, "pricing_method", None) or "piece"
    if method in ("sqft", "sqin") and (not w or not h):
        return None
    if method == "fixed_size" and unit_price is None:
        return None
    return _calc_total(service, w, h, qty, size_text)


async def build_verified_facts(
    db: AsyncSession,
    company: Company,
    services: list,
    ctx: dict,
    understanding: dict,
    from_wa: str,
) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "company_name": company.name,
        "currency": company.currency,
        "services_offered": [s.name for s in services if s.is_active],
        "greeting": (company.greeting_message or "")[:200],
        "design_fee_default": float(getattr(company, "design_fee_default", 0) or 0),
    }
    # bank
    pay = (
        await db.execute(
            select(PaymentDetail).where(
                PaymentDetail.company_id == company.id,
                PaymentDetail.is_active == True,  # noqa: E712
            )
        )
    ).scalars().first()
    if pay:
        facts["bank"] = {
            "bank_name": pay.bank_name,
            "account_number": pay.account_number,
            "account_name": pay.account_name,
            "instructions": pay.instructions,
        }
    else:
        facts["bank"] = None

    # latest order status
    order = (
        await db.execute(
            select(Order)
            .where(Order.company_id == company.id, Order.customer_wa_id == from_wa)
            .order_by(Order.id.desc())
            .limit(1)
        )
    ).scalars().first()
    if order:
        st = order.status.value if hasattr(order.status, "value") else str(order.status)
        facts["latest_order"] = {
            "id": order.id,
            "status": st,
            "total": float(order.total or 0),
            "service": order.service_name,
        }
    else:
        facts["latest_order"] = None

    product = understanding.get("product") or ctx.get("ai_product") or ctx.get("service_name")
    service = None
    if ctx.get("service_id"):
        service = next((s for s in services if s.id == ctx["service_id"]), None)
    if not service:
        service = match_service(services, product, understanding.get("product") or "")
    if service:
        facts["matched_service"] = {
            "id": service.id,
            "name": service.name,
            "base_price": service.base_price,
            "unit": service.unit,
            "pricing_method": getattr(service, "pricing_method", "piece"),
        }
        ctx["service_id"] = service.id
        ctx["service_name"] = service.name
        if understanding.get("size") or ctx.get("variant_label"):
            v = await match_variant(db, service.id, understanding.get("size") or ctx.get("variant_label"))
            if v:
                ctx["variant_id"] = v.id
                ctx["variant_label"] = v.label
                ctx["unit_price"] = v.price
                ctx["base_price"] = v.price
                facts["matched_size"] = {"label": v.label, "price": v.price}
        total = calc_from_service(service, ctx)
        if total is not None:
            design = float(ctx.get("design_fee") or 0)
            if understanding.get("design_status") == "needs_design" and not design:
                design = float(getattr(company, "design_fee_default", 0) or 0)
                ctx["design_fee"] = design
            grand = total + design
            ctx["subtotal"] = total
            ctx["total"] = grand
            facts["verified_quote"] = {
                "subtotal": total,
                "design_fee": design,
                "total": grand,
                "qty": ctx.get("qty"),
                "size": ctx.get("variant_label") or ctx.get("size_text"),
                "currency": company.currency,
            }
        else:
            facts["verified_quote"] = None
            facts["quote_blocked_reason"] = "missing_size_or_qty"
    else:
        facts["matched_service"] = None
        facts["verified_quote"] = None

    facts["missing"] = understanding.get("missing_information") or []
    facts["intent"] = understanding.get("intent")
    return facts


async def run_ai_turn(
    db: AsyncSession,
    company: Company,
    services: list,
    ctx: dict,
    conversation_id: int,
    from_wa: str,
    customer_message: str,
) -> tuple[dict | None, dict, str | None]:
    """
    Returns (understanding, verified_facts, reply_text).
    reply_text may be None → caller uses rule-based path.
    """
    recent = await load_recent_messages(db, conversation_id)
    summary = services_summary(services, company.currency)
    understanding = await understand_message(
        customer_message=customer_message,
        conversation_state=ctx,
        recent_messages=recent,
        company_name=company.name,
        services_summary=summary,
        currency=company.currency,
    )
    if not understanding:
        return None, {}, None

    apply_understanding_to_state(ctx, understanding)
    facts = await build_verified_facts(db, company, services, ctx, understanding, from_wa)

    tone = "pidgin" if ctx.get("lang") == "pidgin" else "clear friendly English"
    if understanding.get("language") == "pidgin":
        tone = "natural Nigerian Pidgin, short and warm"
        ctx["lang"] = "pidgin"

    reply = await generate_reply(
        company_name=company.name,
        tone_hint=tone,
        conversation_state=ctx,
        recent_messages=recent,
        understanding=understanding,
        verified_facts=facts,
        customer_message=customer_message,
    )
    return understanding, facts, reply
