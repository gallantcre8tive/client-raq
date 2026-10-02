"""Seed default services from large per-business catalogues on signup."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.business_templates import get_template
from app.data.seed_catalog import services_for


async def seed_company_from_template(
    db: AsyncSession,
    *,
    company_id: int,
    business_type: str,
    company_name: str,
) -> None:
    tpl = get_template(business_type)
    examples = services_for(business_type)
    if not examples:
        examples = tpl.get("example_services") or []
    if not examples:
        return

    inserted = 0
    for ex in examples:
        name = str(ex.get("name") or "Service")[:200]
        flow = str(ex.get("flow_type") or "generic")[:40]
        unit = str(ex.get("unit") or "piece")[:40]
        method = str(ex.get("pricing_method") or "piece")[:40]
        if not ex.get("pricing_method"):
            method = "piece"
            if flow in ("sqft", "large_format"):
                method = "sqft"
            elif flow in ("fixed_size",):
                method = "fixed_size"
            elif flow in ("tier_qty", "tier"):
                method = "tier"
            elif flow in ("rate_based",):
                method = "rate_based"
            elif flow in ("location_fee",):
                method = "location_fee"
            elif flow in ("appointment",):
                method = "piece"
        category = str(ex.get("category") or tpl.get("label") or "General")[:120]
        desc = str(ex.get("description") or f"Starter for {tpl.get('label')} — edit price in Services.")[:500]
        base_price = float(ex.get("base_price") or 0)
        min_qty = int(ex.get("min_qty") or 1)
        if min_qty < 1:
            min_qty = 1

        params = {
            "cid": int(company_id),
            "cat": category,
            "name": name,
            "desc": desc,
            "price": base_price,
            "unit": unit,
            "method": method,
            "flow": flow,
            "min_qty": min_qty,
        }
        try:
            await db.execute(text("""
                INSERT INTO services (
                    company_id, category, name, description, base_price, unit,
                    pricing_method, flow_type, design_fee, min_qty, is_active, created_at
                )
                SELECT
                    :cid, :cat, :name, :desc, :price, :unit,
                    :method, :flow, 0, :min_qty, true, NOW()
                WHERE NOT EXISTS (
                    SELECT 1 FROM services WHERE company_id = :cid AND name = :name
                )
            """), params)
            inserted += 1
        except Exception as e:
            print("seed_service", name, type(e).__name__, e)
            try:
                await db.rollback()
            except Exception:
                pass
            try:
                await db.execute(text("""
                    INSERT INTO services (
                        company_id, category, name, description, base_price, unit,
                        pricing_method, is_active, min_qty
                    )
                    SELECT :cid, :cat, :name, :desc, :price, :unit, :method, true, :min_qty
                    WHERE NOT EXISTS (
                        SELECT 1 FROM services WHERE company_id = :cid AND name = :name
                    )
                """), {
                    "cid": params["cid"],
                    "cat": category,
                    "name": name,
                    "desc": desc,
                    "price": base_price,
                    "unit": unit,
                    "method": method,
                    "min_qty": min_qty,
                })
                inserted += 1
            except Exception as e2:
                print("seed_service_fallback", name, e2)
                try:
                    await db.rollback()
                except Exception:
                    pass
    try:
        await db.commit()
        print(f"seed_ok company={company_id} type={business_type} services~={inserted}")
    except Exception as e:
        print("seed_commit", e)
        try:
            await db.rollback()
        except Exception:
            pass
