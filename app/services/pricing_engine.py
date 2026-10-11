"""
Client RaQ — deterministic multi-business pricing engine.

Never invents prices. Returns None when required inputs are missing so the bot can ask.
All amounts are rounded to 2 decimal places.
"""
from __future__ import annotations

import re
from typing import Any


def _f(v, default=0.0) -> float:
    try:
        if v is None or v == "":
            return float(default)
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _i(v, default=1) -> int:
    try:
        return max(1, int(float(v)))
    except (TypeError, ValueError):
        return max(1, int(default))


def normalize_method(service: Any) -> str:
    """Map service.pricing_method / unit / flow_type to a calculator mode."""
    method = (getattr(service, "pricing_method", None) or "").strip().lower()
    unit = (getattr(service, "unit", None) or "").strip().lower()
    flow = (getattr(service, "flow_type", None) or "").strip().lower()

    if method in ("sqft", "sq_ft", "square_feet", "per_sqft"):
        return "sqft"
    if method in ("sqin", "sq_in", "square_inch"):
        return "sqft"  # handled via size_unit=in
    if method in ("sqm", "sq_m", "square_meter", "per_sqm"):
        return "sqm"
    if method in ("piece", "per_piece", "each", "unit", "pcs"):
        return "piece"
    if method in ("fixed", "fixed_size", "flat"):
        return "fixed"
    if method in ("rate", "fx", "exchange", "percent", "spread"):
        return "rate"
    if method in ("tier", "tiered", "qty_tier"):
        return "tier"
    if method in ("per_kg", "kg", "weight"):
        return "per_kg"
    if method in ("per_hour", "hourly"):
        return "per_hour"
    if method in ("consultation", "fee", "service_fee"):
        return "fee"

    # Infer from unit text
    if any(x in unit for x in ("sqm", "m2", "metre", "meter")):
        return "sqm"
    if any(x in unit for x in ("sq", "square", "ft", "feet", "foot")):
        return "sqft"
    if any(x in unit for x in ("kg", "kilo")):
        return "per_kg"
    if any(x in unit for x in ("hour", "hr")):
        return "per_hour"
    if any(x in unit for x in ("%","rate", "ngn/$", "usd")):
        return "rate"

    # Infer from flow_type (business-specific defaults)
    if flow in ("exchanger", "fx", "giftcard", "crypto", "paypal", "cashapp"):
        return "rate"
    if flow in ("laundry", "dry_clean", "cleaning"):
        return "piece"  # per garment unless unit says kg
    if flow in ("repair", "phone_repair"):
        return "fixed"
    if flow in ("real_estate", "agency", "inspection"):
        return "fee"
    if flow in ("skincare", "beauty", "booking"):
        return "fixed"
    if flow in ("frame", "frameless", "acrylic"):
        return "fixed"
    if flow in ("banner", "flex", "sticker", "sav", "large_format"):
        return "sqft"

    return "piece"


def parse_size(text: str) -> tuple[float | None, float | None]:
    low = (text or "").lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*[x×*]\s*(\d+(?:\.\d+)?)", low)
    if not m:
        m = re.search(r"(\d+(?:\.\d+)?)\s*by\s*(\d+(?:\.\d+)?)", low)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None, None


def detect_size_unit(text: str, ctx: dict | None = None) -> str:
    if ctx:
        u = (ctx.get("size_unit") or "").lower()
        if u in ("ft", "feet", "foot"):
            return "ft"
        if u in ("in", "inch", "inches"):
            return "in"
        if u in ("m", "metre", "meter", "sqm"):
            return "m"
    low = (text or "").lower()
    if "inch" in low or "inches" in low or re.search(r'\d\s*"', low):
        return "in"
    if re.search(r"\d+(?:\.\d+)?\s*ins?\b", low):
        return "in"
    if any(x in low for x in ("metre", "meter", "sqm", "m2")):
        return "m"
    if any(x in low for x in ("ft", "feet", "foot", "sqft", "sq ft")):
        return "ft"
    return "ft"


def parse_qty(text: str) -> int | None:
    low = (text or "").lower()
    m = re.search(r"(\d+)\s*(unit|units|pcs|pc|piece|pieces|qty|quantity|copy|copies|item|items)\b", low)
    if m:
        return max(1, int(m.group(1)))
    if re.fullmatch(r"\d+", (text or "").strip()):
        return max(1, int(text.strip()))
    return None


def min_qty_ok(service: Any, qty: int) -> tuple[bool, int]:
    mn = int(getattr(service, "min_qty", None) or 1)
    mn = max(1, mn)
    return qty >= mn, mn


def calculate(
    service: Any,
    *,
    width: float | None = None,
    height: float | None = None,
    qty: int | None = 1,
    size_text: str = "",
    size_unit: str | None = None,
    amount: float | None = None,
    extra_fee: float | None = None,
    design_fee: float | None = None,
    unit_price_override: float | None = None,
    weight_kg: float | None = None,
    hours: float | None = None,
    ctx: dict | None = None,
) -> dict[str, Any]:
    """
    Returns:
      {
        ok: bool,
        total: float | None,
        need: list[str],      # missing inputs
        mode: str,
        breakdown: str,
        min_qty: int,
        min_qty_blocked: bool,
      }
    """
    ctx = ctx or {}
    mode = normalize_method(service)
    base = _f(unit_price_override if unit_price_override is not None else getattr(service, "base_price", 0))
    qty = _i(qty if qty is not None else ctx.get("qty") or 1, 1)
    design = _f(design_fee if design_fee is not None else getattr(service, "design_fee", 0) or ctx.get("design_fee") or 0)
    extra = _f(extra_fee if extra_fee is not None else ctx.get("extra_fee") or 0)
    need: list[str] = []
    mn_ok, mn = min_qty_ok(service, qty)
    result: dict[str, Any] = {
        "ok": False,
        "total": None,
        "need": need,
        "mode": mode,
        "breakdown": "",
        "min_qty": mn,
        "min_qty_blocked": not mn_ok,
        "qty": qty,
        "base_price": base,
    }

    if not mn_ok and mode not in ("rate", "fee"):
        need.append(f"min_qty:{mn}")
        result["breakdown"] = f"Minimum order quantity is {mn}."
        return result

    # Resolve size if needed
    w = width if width is not None else ctx.get("width")
    h = height if height is not None else ctx.get("height")
    if (w is None or h is None) and (size_text or ctx.get("size_text")):
        w, h = parse_size(size_text or str(ctx.get("size_text") or ""))
    unit = (size_unit or ctx.get("size_unit") or detect_size_unit(size_text or str(ctx.get("size_text") or ""), ctx)).lower()

    total: float | None = None
    breakdown = ""

    if mode == "piece":
        total = round(base * qty + design + extra, 2)
        breakdown = f"{base} × {qty}" + (f" + design {design}" if design else "") + (f" + extras {extra}" if extra else "")

    elif mode == "fixed":
        # fixed size / fixed service price (frames, repair jobs, packages)
        unit_p = _f(unit_price_override if unit_price_override is not None else ctx.get("unit_price") or base)
        if unit_p <= 0 and base <= 0:
            need.append("price")
            return result
        total = round(unit_p * qty + design + extra, 2)
        breakdown = f"{unit_p} × {qty}"

    elif mode == "sqft":
        if not w or not h:
            need.append("size")
            return result
        w, h = float(w), float(h)
        if unit in ("in", "inch", "inches"):
            area_factor = (w * h) / 144.0
            total = round(area_factor * base * qty + design + extra, 2)
            breakdown = f"({w}×{h} in ÷ 144) × {base} × {qty}"
        elif unit in ("m", "metre", "meter", "sqm"):
            area_factor = w * h * 10.7639
            total = round(area_factor * base * qty + design + extra, 2)
            breakdown = f"({w}×{h} m → sqft) × {base} × {qty}"
        else:
            total = round(w * h * base * qty + design + extra, 2)
            breakdown = f"{w}×{h} ft × {base} × {qty}"

    elif mode == "sqm":
        if not w or not h:
            need.append("size")
            return result
        w, h = float(w), float(h)
        if unit in ("ft", "feet", "foot"):
            total = round((w * h / 10.7639) * base * qty + design + extra, 2)
            breakdown = f"({w}×{h} ft → m²) × {base} × {qty}"
        else:
            total = round(w * h * base * qty + design + extra, 2)
            breakdown = f"{w}×{h} m² × {base} × {qty}"

    elif mode == "rate":
        # Exchanger: amount in foreign currency × rate = local payout
        amt = _f(amount if amount is not None else ctx.get("amount") or ctx.get("fx_amount"))
        rate = base if base > 0 else _f(ctx.get("rate"))
        if amt <= 0:
            need.append("amount")
            return result
        if rate <= 0:
            need.append("rate")
            return result
        total = round(amt * rate + extra, 2)
        breakdown = f"{amt} × rate {rate}"

    elif mode == "per_kg":
        kg = _f(weight_kg if weight_kg is not None else ctx.get("weight_kg") or ctx.get("kg"))
        if kg <= 0:
            need.append("weight_kg")
            return result
        total = round(kg * base + design + extra, 2)
        breakdown = f"{kg} kg × {base}"

    elif mode == "per_hour":
        hrs = _f(hours if hours is not None else ctx.get("hours"))
        if hrs <= 0:
            need.append("hours")
            return result
        total = round(hrs * base + extra, 2)
        breakdown = f"{hrs} hr × {base}"

    elif mode == "fee":
        # Real estate / consultation — flat fee (qty usually 1)
        if base <= 0:
            need.append("price")
            return result
        total = round(base * qty + extra, 2)
        breakdown = f"fee {base}" + (f" × {qty}" if qty > 1 else "")

    elif mode == "tier":
        # Optional JSON tiers on service.config_json — fallback to piece
        total = round(base * qty + design + extra, 2)
        breakdown = f"tier-base {base} × {qty}"

    else:
        total = round(base * qty + design + extra, 2)
        breakdown = f"{base} × {qty}"

    result["ok"] = total is not None and not need
    result["total"] = total
    result["breakdown"] = breakdown
    result["size_unit"] = unit
    result["width"] = w
    result["height"] = h
    return result


def format_money(amount: float, currency: str = "NGN") -> str:
    cur = (currency or "NGN").upper()
    try:
        amt = float(amount)
    except (TypeError, ValueError):
        return f"{cur} 0"
    if cur in ("NGN", "NAIRA", "₦"):
        return f"₦{amt:,.0f}" if amt >= 100 else f"₦{amt:,.2f}"
    if cur in ("USD", "$"):
        return f"${amt:,.2f}"
    return f"{cur} {amt:,.2f}"
