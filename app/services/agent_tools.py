"""Client RaQ agent tools — Grok function-calling. Company-scoped. No invented prices."""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company, PaymentDetail
from app.models.catalog import Service, ServiceVariant
from app.models.conversation import (
    Conversation, Message, Order, OrderStatus, Customer, Attachment, AdminNotification,
)

log = logging.getLogger("client_raq.tools")

TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "list_service_example_images",
            "description": "List example photos the company uploaded for a service (for customers who do not know what they want).",
            "parameters": {
                "type": "object",
                "properties": {
                    "service_name": {"type": "string"},
                    "service_id": {"type": "integer"},
                    "limit": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_service_example_image",
            "description": "Queue one company example image to send to the customer on WhatsApp (by image id from list_service_example_images).",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_id": {"type": "integer"},
                    "caption": {"type": "string"},
                },
                "required": ["image_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_company_information",
            "description": "Get print shop name, hours, location, languages, about, enquiry contacts.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_available_services",
            "description": "List this company's active printing services and configured base prices.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_service_details",
            "description": "Details for one service including fixed-size variants (frames etc).",
            "parameters": {
                "type": "object",
                "properties": {
                    "service_name": {"type": "string"},
                    "service_id": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_service_price",
            "description": "Backend price calculation. size_unit: ft (default banners), in (inches), m. NEVER invent prices.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service_name": {"type": "string"},
                    "service_id": {"type": "integer"},
                    "width": {"type": "number"},
                    "height": {"type": "number"},
                    "quantity": {"type": "integer"},
                    "size_unit": {"type": "string", "enum": ["ft", "in", "m"]},
                    "variant_label": {"type": "string"},
                },
                "required": ["quantity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_payment_information",
            "description": "Bank/payment details for this company.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_payment",
            "description": "Mark conversation as awaiting payment and return payment instructions text using company bank details + locked total.",
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {"type": "number", "description": "Optional override; prefer locked quote total"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_payment_reference",
            "description": "Record that customer submitted payment proof / reference. Does NOT confirm payment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {"type": "string"},
                    "attachment_id": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_order_state",
            "description": "Current structured order/quote context for this chat.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_order_state",
            "description": "Save facts customer provided (service, qty, size, design, fulfillment...).",
            "parameters": {
                "type": "object",
                "properties": {
                    "service_name": {"type": "string"},
                    "service_id": {"type": "integer"},
                    "quantity": {"type": "integer"},
                    "width": {"type": "number"},
                    "height": {"type": "number"},
                    "size_unit": {"type": "string"},
                    "variant_label": {"type": "string"},
                    "design_status": {"type": "string", "enum": ["has_design", "needs_design", "received", "unknown"]},
                    "fulfillment": {"type": "string", "enum": ["pickup", "delivery"]},
                    "address": {"type": "string"},
                    "datetime": {"type": "string"},
                    "notes": {"type": "string"},
                    "lock_quote": {"type": "boolean"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_order",
            "description": "Create order record after quote accepted / payment proof. Uses locked total from context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["awaiting_acceptance", "payment_submitted"],
                        "description": "payment_submitted if proof already received",
                    },
                    "note": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_customer",
            "description": "Load returning customer profile for this WhatsApp number (this company only).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_customer_files",
            "description": "List files/designs/payment proofs this customer already sent.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_customer_file",
            "description": "Mark that we are waiting for a design or document from the customer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_kind": {
                        "type": "string",
                        "enum": ["design", "document", "payment_proof", "reference_image"],
                    },
                    "prompt_hint": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notify_human_agent",
            "description": "Escalate to human staff with reason. Use for complaints, unknown jobs, disputes, human requests.",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_previous_orders",
            "description": "Previous orders for this WhatsApp customer at this company (returning customer).",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max orders to return, default 5"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_reference_sample",
            "description": "Send a reference/sample description or image caption for a service so customer sees what it looks like. Use when customer is unsure about a product.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service_name": {"type": "string"},
                    "service_id": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_conversation_state",
            "description": "Set formal conversation stage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "state": {
                        "type": "string",
                        "enum": [
                            "new_customer", "understanding_request", "collecting_information",
                            "awaiting_file", "awaiting_payment", "payment_received",
                            "processing_order", "awaiting_delivery_information",
                            "ready_for_human", "completed", "cancelled"
                        ],
                    },
                },
                "required": ["state"],
            },
        },
    },

]


def _unit_mode(unit: str | None) -> str:
    u = (unit or "").lower()
    if any(x in u for x in ("piece", "pcs", "each", "frame", "copy", "per pc")):
        return "piece"
    if any(x in u for x in ("sqm", "m2", "metre", "meter")):
        return "sqm"
    if any(x in u for x in ("sq", "square", "ft", "feet", "foot", "sqft")):
        return "sqft"
    return "piece"


def _calc(base: float, mode: str, w, h, qty: int, size_unit: str) -> float:
    qty = max(1, int(qty or 1))
    base = float(base or 0)
    if mode == "piece" or not w or not h:
        return round(base * qty, 2)
    w, h = float(w), float(h)
    unit = (size_unit or "ft").lower()
    if mode == "sqft":
        if unit in ("in", "inch", "inches"):
            return round((w * h * qty * base) / 144.0, 2)
        if unit in ("m", "metre", "meter", "sqm"):
            return round(w * h * 10.7639 * base * qty, 2)
        return round(w * h * base * qty, 2)
    if mode == "sqm":
        if unit in ("ft", "feet", "foot"):
            return round((w * h / 10.7639) * base * qty, 2)
        return round(w * h * base * qty, 2)
    return round(w * h * base * qty, 2)


async def _find_service(db: AsyncSession, company_id: int, name: str | None, sid: int | None) -> Service | None:
    rows = list((await db.execute(
        select(Service).where(Service.company_id == company_id, Service.is_active == True)  # noqa: E712
    )).scalars().all())
    if sid:
        for s in rows:
            if s.id == sid:
                return s
    if name:
        low = name.lower().strip()
        for s in rows:
            if low in (s.name or "").lower() or (s.name or "").lower() in low:
                return s
        for s in rows:
            for part in (s.name or "").lower().replace("/", " ").split():
                if len(part) > 2 and part in low:
                    return s
    return None


class ToolContext:
    def __init__(self, db: AsyncSession, company: Company, conv: Conversation, ctx: dict, from_wa: str):
        self.db = db
        self.company = company
        self.conv = conv
        self.ctx = ctx
        self.from_wa = from_wa
        self.needs_human = False
        self.human_reason = ""
        self.created_order_id: int | None = None
        self.awaiting_file: str | None = None


def _validate_args(name: str, args: dict) -> dict | None:
    """Server-side tool argument validation (spec §31). Returns error dict or None."""
    args = args or {}
    if name == "calculate_service_price":
        q = args.get("quantity")
        if q is not None:
            try:
                q = int(q)
                if q < 1 or q > 1_000_000:
                    return {"error": "invalid_quantity"}
                args["quantity"] = q
            except (TypeError, ValueError):
                return {"error": "quantity_must_be_integer"}
        for dim in ("width", "height"):
            if args.get(dim) is not None:
                try:
                    v = float(args[dim])
                    if v <= 0 or v > 10000:
                        return {"error": f"invalid_{dim}"}
                    args[dim] = v
                except (TypeError, ValueError):
                    return {"error": f"{dim}_must_be_number"}
        unit = args.get("size_unit")
        if unit is not None and str(unit).lower() not in ("ft", "in", "m", "feet", "inch", "inches", "metre", "meter"):
            return {"error": "invalid_size_unit"}
    if name in ("create_order", "update_order_state", "save_payment_reference"):
        # prevent injection of foreign ids
        for k in ("company_id", "customer_id"):
            if k in args:
                args.pop(k, None)
    return None


async def execute_tool(name: str, args: dict, tc: ToolContext) -> dict[str, Any]:

    if name == "list_service_example_images":
        from app.models.service_image import ServiceExampleImage
        from app.models.catalog import Service
        sid = args.get("service_id")
        sname = (args.get("service_name") or "").strip().lower()
        limit = int(args.get("limit") or 5)
        q = select(ServiceExampleImage).where(
            ServiceExampleImage.company_id == tc.company_id,
            ServiceExampleImage.is_active == True,  # noqa: E712
        )
        if sid:
            q = q.where(ServiceExampleImage.service_id == int(sid))
        elif sname:
            svcs = list((await tc.db.execute(
                select(Service).where(Service.company_id == tc.company_id)
            )).scalars().all())
            match_ids = [s.id for s in svcs if sname in (s.name or "").lower()]
            if match_ids:
                q = q.where(ServiceExampleImage.service_id.in_(match_ids))
        rows = list((await tc.db.execute(q.order_by(ServiceExampleImage.id.desc()).limit(limit))).scalars().all())
        return {
            "count": len(rows),
            "images": [
                {
                    "image_id": r.id,
                    "title": r.title or r.original_name,
                    "description": r.description,
                    "tags": r.tags,
                    "service_id": r.service_id,
                }
                for r in rows
            ],
            "hint": "Use send_service_example_image with image_id to show the customer on WhatsApp.",
        }

    if name == "send_service_example_image":
        from app.models.service_image import ServiceExampleImage
        iid = int(args.get("image_id") or 0)
        im = await tc.db.get(ServiceExampleImage, iid)
        if not im or im.company_id != tc.company_id:
            return {"error": "Image not found for this company"}
        # Queue for bot_engine after reply (public URL via authenticated proxy is not public —
        # we will upload from disk in bot layer via pending_reference with local path)
        from app.services.attachment_serve import media_root
        path = media_root() / im.storage_path
        if not path.is_file():
            return {"error": "Image file missing on server. Re-upload in Service → Example images."}
        # Store on conversation context via tool context side channel
        payload = {
            "path": str(path),
            "mime": im.mime_type or "image/jpeg",
            "filename": im.original_name or path.name,
            "caption": (args.get("caption") or im.title or "Example")[:1000],
        }
        tc.pending_images = getattr(tc, "pending_images", []) or []
        tc.pending_images.append(payload)
        # Persist onto conversation context if available
        try:
            conv = getattr(tc, "conversation", None) or getattr(tc, "conv", None)
            if conv is not None:
                import json as _json
                raw = getattr(conv, "context_json", None) or getattr(conv, "context", None) or "{}"
                if isinstance(raw, dict):
                    c = dict(raw)
                else:
                    c = _json.loads(raw) if raw else {}
                arr = list(c.get("pending_example_images") or [])
                arr.append(payload)
                c["pending_example_images"] = arr
                if hasattr(conv, "context_json"):
                    conv.context_json = _json.dumps(c)
                elif hasattr(conv, "context"):
                    conv.context = c
        except Exception as _ce:
            print("queue_example_ctx", _ce)
        return {"queued": True, "image_id": iid, "caption": args.get("caption") or im.title}

    verr = _validate_args(name, args or {})
    if verr:
        return verr
    company = tc.company
    db = tc.db
    cid = company.id
    args = args or {}

    try:
        if name == "get_company_information":
            return {
                "name": company.name,
                "currency": company.currency or "NGN",
                "languages": getattr(company, "bot_language", None) or "both",
                "hours": getattr(company, "business_hours", None) or "",
                "location": getattr(company, "location_text", None) or "",
                "about": getattr(company, "about_text", None) or "",
                "enquiry_whatsapp": getattr(company, "enquiry_whatsapp", None) or "",
                "enquiry_phone": getattr(company, "enquiry_phone", None) or "",
                "enquiry_note": getattr(company, "enquiry_note", None) or "",
            }

        if name == "get_available_services":
            rows = list((await db.execute(
                select(Service).where(Service.company_id == cid, Service.is_active == True)  # noqa: E712
            )).scalars().all())
            out = []
            for s in rows:
                item = {
                    "id": s.id,
                    "name": s.name,
                    "base_price": float(s.base_price or 0),
                    "min_qty": int(getattr(s, "min_qty", 1) or 1),
                "unit": s.unit or "",
                    "pricing_method": getattr(s, "pricing_method", None) or _unit_mode(s.unit),
                    "description": (getattr(s, "description", None) or "")[:200],
                }
                if item["base_price"] <= 0:
                    item["price_note"] = "Price not configured — do not invent"
                out.append(item)
            return {"currency": company.currency or "NGN", "services": out}

        if name == "get_service_details":
            s = await _find_service(db, cid, args.get("service_name"), args.get("service_id"))
            if not s:
                return {"error": "service_not_found"}
            variants = list((await db.execute(
                select(ServiceVariant).where(ServiceVariant.service_id == s.id)
            )).scalars().all())
            return {
                "id": s.id,
                "name": s.name,
                "base_price": float(s.base_price or 0),
                "unit": s.unit or "",
                "pricing_method": getattr(s, "pricing_method", None) or _unit_mode(s.unit),
                "description": getattr(s, "description", None) or "",
                "variants": [{"label": v.label, "price": float(v.price or 0)} for v in variants],
                "price_configured": float(s.base_price or 0) > 0 or any(float(v.price or 0) > 0 for v in variants),
            }

        if name == "calculate_service_price":
            qty = int(args.get("quantity") or 0)
            if qty < 1:
                return {"error": "quantity_required"}
            s = await _find_service(db, cid, args.get("service_name"), args.get("service_id"))
            if not s and tc.ctx.get("service_id"):
                s = await _find_service(db, cid, None, int(tc.ctx["service_id"]))
            if not s:
                return {"error": "service_not_found"}
            label = (args.get("variant_label") or tc.ctx.get("variant_label") or "").strip()
            if label:
                variants = list((await db.execute(
                    select(ServiceVariant).where(ServiceVariant.service_id == s.id)
                )).scalars().all())
                for v in variants:
                    if label.lower().replace(" ", "") in (v.label or "").lower().replace(" ", ""):
                        total = round(float(v.price or 0) * qty, 2)
                        if total <= 0:
                            return {"error": "price_not_configured", "service": s.name, "variant": v.label}
                        return {
                            "service": s.name, "variant": v.label, "quantity": qty,
                            "unit_price": float(v.price or 0), "total": total,
                            "currency": company.currency or "NGN",
                            "formula": f"{v.price} x {qty}",
                        }
            mode = getattr(s, "pricing_method", None) or _unit_mode(s.unit)
            base = float(s.base_price or 0)
            if base <= 0 and mode != "piece":
                return {"error": "price_not_configured", "service": s.name}
            w = args.get("width") if args.get("width") is not None else tc.ctx.get("width")
            h = args.get("height") if args.get("height") is not None else tc.ctx.get("height")
            unit = args.get("size_unit") or tc.ctx.get("size_unit") or "ft"
            if mode in ("sqft", "sqm") and (not w or not h):
                return {
                    "error": "size_required", "service": s.name, "pricing": mode,
                    "message": "Need width and height", "rate": base, "unit": s.unit,
                }
            total = _calc(base, mode if mode in ("sqft", "sqm", "piece") else _unit_mode(s.unit), w, h, qty, unit)
            if total <= 0:
                return {"error": "price_not_configured", "service": s.name}
            formula = f"{base} x {qty}"
            area = None
            if w and h and mode in ("sqft", "sqm"):
                area = float(w) * float(h)
                if unit in ("in", "inch", "inches") and mode == "sqft":
                    formula = f"({w}x{h} in / 144) x {base} x {qty}"
                else:
                    formula = f"{w}x{h} {unit} x {base} x {qty}"
            return {
                "service": s.name, "service_id": s.id, "quantity": qty,
                "width": w, "height": h, "size_unit": unit, "area": area,
                "rate": base, "rate_unit": s.unit, "total": total,
                "currency": company.currency or "NGN", "formula": formula,
            }

        if name == "get_payment_information":
            rows = list((await db.execute(
                select(PaymentDetail).where(PaymentDetail.company_id == cid)
            )).scalars().all())
            if not rows:
                return {"configured": False, "message": "No bank details — notify human"}
            return {
                "configured": True,
                "currency": company.currency or "NGN",
                "banks": [
                    {
                        "bank_name": p.bank_name,
                        "account_name": p.account_name,
                        "account_number": p.account_number,
                        "instructions": p.instructions or "",
                    }
                    for p in rows
                ],
            }

        if name == "request_payment":
            total = float(args.get("amount") or tc.ctx.get("locked_total") or tc.ctx.get("total") or 0)
            rows = list((await db.execute(
                select(PaymentDetail).where(PaymentDetail.company_id == cid)
            )).scalars().all())
            tc.conv.state = "await_payment"
            if not rows:
                return {"ok": False, "error": "no_payment_details", "total": total}
            lines = [f"Please pay *{company.currency or 'NGN'} {total:,.0f}* to:"]
            for p in rows:
                lines.append(f"• {p.bank_name} — {p.account_name} — {p.account_number}")
                if p.instructions:
                    lines.append(f"  ({p.instructions})")
            lines.append("After payment, send the screenshot here.")
            return {"ok": True, "total": total, "instructions": "\n".join(lines)}

        if name == "save_payment_reference":
            tc.ctx["payment_proof"] = args.get("attachment_id") or tc.ctx.get("payment_proof") or "submitted"
            tc.ctx["payment_note"] = (args.get("note") or "")[:500]
            tc.conv.state = "await_payment"
            return {"ok": True, "status": "pending_verification", "message": "Proof recorded; admin must confirm"}

        if name == "get_order_state":
            return {
                "state": tc.conv.state,
                "context": {
                    k: tc.ctx.get(k)
                    for k in (
                        "service_id", "service_name", "qty", "width", "height", "size_unit",
                        "variant_label", "design_status", "fulfillment", "address", "datetime",
                        "total", "locked_total", "quote_locked", "lang", "awaiting_file",
                        "payment_proof", "last_attachment_id",
                    )
                    if tc.ctx.get(k) is not None
                },
            }

        if name == "update_order_state":
            mapping = {
                "service_name": "service_name", "service_id": "service_id", "quantity": "qty",
                "width": "width", "height": "height", "size_unit": "size_unit",
                "variant_label": "variant_label", "design_status": "design_status",
                "fulfillment": "fulfillment", "address": "address", "datetime": "datetime", "notes": "notes",
            }
            for ak, ck in mapping.items():
                if ak in args and args[ak] is not None:
                    tc.ctx[ck] = args[ak]
            if args.get("lock_quote") and tc.ctx.get("total"):
                tc.ctx["locked_total"] = tc.ctx["total"]
                tc.ctx["quote_locked"] = True
            if args.get("service_name") and not tc.ctx.get("service_id"):
                s = await _find_service(db, cid, args["service_name"], None)
                if s:
                    tc.ctx["service_id"] = s.id
                    tc.ctx["service_name"] = s.name
            return {"ok": True, "context": {k: tc.ctx.get(k) for k in mapping.values() if tc.ctx.get(k) is not None}}

        if name == "create_order":
            total = float(tc.ctx.get("locked_total") or tc.ctx.get("total") or 0)
            status_raw = (args.get("status") or "awaiting_acceptance").lower()
            status = OrderStatus.PAYMENT_SUBMITTED if status_raw == "payment_submitted" else OrderStatus.AWAITING_ACCEPTANCE
            details_parts = []
            for k in ("width", "height", "size_unit", "variant_label", "qty", "fulfillment", "datetime", "address"):
                if tc.ctx.get(k) is not None:
                    details_parts.append(f"{k}={tc.ctx.get(k)}")
            order = Order(
                company_id=cid,
                conversation_id=tc.conv.id,
                customer_wa_id=tc.from_wa,
                service_name=tc.ctx.get("service_name"),
                details="; ".join(details_parts) + ((" | " + (args.get("note") or "")) if args.get("note") else ""),
                total=total,
                currency=company.currency or "NGN",
                status=status,
                fulfillment=tc.ctx.get("fulfillment"),
                schedule_note=tc.ctx.get("datetime"),
                payment_proof=str(tc.ctx.get("payment_proof") or "") or None,
            )
            db.add(order)
            await db.flush()
            tc.created_order_id = order.id
            tc.ctx["order_id"] = order.id
            try:
                db.add(AdminNotification(
                    company_id=cid,
                    title="New order",
                    body=f"Order #{order.id} from {tc.from_wa} — {company.currency} {total:,.0f}",
                    link_path="/company/orders",
                ))
            except Exception:
                pass
            return {"ok": True, "order_id": order.id, "total": total, "status": status.value}

        if name == "get_customer":
            row = (await db.execute(
                select(Customer).where(Customer.company_id == cid, Customer.wa_id == tc.from_wa)
            )).scalars().first()
            if not row:
                return {"found": False, "wa_id": tc.from_wa}
            return {
                "found": True,
                "wa_id": row.wa_id,
                "profile_name": row.profile_name,
                "language_preference": row.language_preference,
                "total_conversations": row.total_conversations,
                "last_order_id": row.last_order_id,
            }

        if name == "list_customer_files":
            rows = list((await db.execute(
                select(Attachment).where(
                    Attachment.company_id == cid,
                    Attachment.customer_wa_id == tc.from_wa,
                ).order_by(Attachment.id.desc()).limit(20)
            )).scalars().all())
            return {
                "files": [
                    {
                        "id": a.id,
                        "kind": a.kind,
                        "mime_type": a.mime_type,
                        "created_at": str(a.created_at) if a.created_at else None,
                        "has_transcript": bool(a.transcript),
                    }
                    for a in rows
                ]
            }

        if name == "request_customer_file":
            kind = args.get("file_kind") or "design"
            tc.ctx["awaiting_file"] = kind
            tc.awaiting_file = kind
            tc.conv.state = "awaiting_file"
            return {
                "ok": True,
                "awaiting": kind,
                "hint": args.get("prompt_hint") or f"Please send your {kind} here on WhatsApp.",
            }

        if name == "notify_human_agent":
            reason = (args.get("reason") or "Customer needs attention")[:500]
            tc.needs_human = True
            tc.human_reason = reason
            tc.conv.is_live_takeover = True
            tc.conv.needs_human = True
            tc.conv.handoff_reason = reason
            try:
                db.add(AdminNotification(
                    company_id=cid,
                    title="Customer needs attention",
                    body=f"{tc.from_wa}: {reason}",
                    link_path="/company/messages",
                    priority="high",
                ))
            except Exception as e:
                log.warning("notify_human_db %s", e)
            return {"ok": True, "escalated": True, "reason": reason}


        if name == "get_previous_orders":
            lim = int(args.get("limit") or 5)
            lim = max(1, min(lim, 20))
            rows = list((await db.execute(
                select(Order).where(
                    Order.company_id == cid,
                    Order.customer_wa_id == tc.from_wa,
                ).order_by(Order.id.desc()).limit(lim)
            )).scalars().all())
            return {
                "orders": [
                    {
                        "id": o.id,
                        "service_name": o.service_name,
                        "total": float(o.total or 0),
                        "currency": o.currency,
                        "status": o.status.value if hasattr(o.status, "value") else str(o.status),
                        "details": (o.details or "")[:200],
                        "fulfillment": o.fulfillment,
                    }
                    for o in rows
                ]
            }

        if name == "send_reference_sample":
            from app.data.reference_samples import caption_for_service
            s = await _find_service(db, cid, args.get("service_name"), args.get("service_id"))
            sname = s.name if s else (args.get("service_name") or "print product")
            caption = caption_for_service(sname)
            # Optional public URL on service
            image_url = None
            if s is not None:
                image_url = getattr(s, "reference_image_url", None) or getattr(s, "sample_image_url", None)
            tc.ctx["pending_reference"] = {
                "service": sname,
                "caption": caption,
                "image_url": image_url,
            }
            return {
                "ok": True,
                "service": sname,
                "caption": caption,
                "image_url": image_url,
                "note": "Backend will send caption (and image_url if set on service) to WhatsApp after your reply.",
            }

        if name == "set_conversation_state":
            from app.services.conversation_states import FORMAL_STATES, normalize_state
            st = args.get("state") or ""
            if st not in FORMAL_STATES and normalize_state(st) not in FORMAL_STATES:
                return {"error": "invalid_state", "allowed": list(FORMAL_STATES)[:12]}
            tc.conv.state = st
            return {"ok": True, "state": st}


        return {"error": "unknown_tool", "name": name}
    except Exception as e:
        log.exception("tool_%s", name)
        return {"error": "tool_failed", "detail": type(e).__name__}
