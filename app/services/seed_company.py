"""Seed default services from business template on signup."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.business_templates import get_template


async def seed_company_from_template(
    db: AsyncSession,
    *,
    company_id: int,
    business_type: str,
    company_name: str,
) -> None:
    tpl = get_template(business_type)
    examples = tpl.get("example_services") or []
    if not examples:
        return

    for ex in examples:
        name = str(ex.get("name") or "Service")[:200]
        flow = str(ex.get("flow_type") or "generic")[:40]
        unit = str(ex.get("unit") or "per piece")[:40]
        method = "piece"
        if flow in ("sqft", "large_format"):
            method = "sqft"
        elif flow in ("fixed_size",):
            method = "fixed_size"
        elif flow in ("tier_qty", "tier"):
            method = "tier"
        elif flow in ("rate_based",):
            method = "custom"
        elif flow in ("appointment",):
            method = "piece"
        try:
            await db.execute(text("""
                INSERT INTO services (
                    company_id, category, name, description, base_price, unit,
                    pricing_method, flow_type, design_fee, min_qty, is_active, created_at
                )
                SELECT
                    :cid, :cat, :name, :desc, 0, :unit,
                    :method, :flow, 0, 1, true, NOW()
                WHERE NOT EXISTS (
                    SELECT 1 FROM services WHERE company_id = :cid AND name = :name
                )
            """), {
                "cid": int(company_id),
                "cat": str(tpl.get("label") or "General")[:120],
                "name": name,
                "desc": f"Starter service for {tpl.get('label')} — set your real price in Services.",
                "unit": unit,
                "method": method,
                "flow": flow,
            })
        except Exception as e:
            print("seed_service", name, type(e).__name__, e)
            try:
                await db.rollback()
            except Exception:
                pass
            try:
                await db.execute(text("""
                    INSERT INTO services (company_id, category, name, base_price, unit, pricing_method, is_active)
                    SELECT :cid, :cat, :name, 0, :unit, :method, true
                    WHERE NOT EXISTS (
                        SELECT 1 FROM services WHERE company_id = :cid AND name = :name
                    )
                """), {
                    "cid": int(company_id),
                    "cat": str(tpl.get("label") or "General")[:120],
                    "name": name,
                    "unit": unit,
                    "method": method,
                })
            except Exception as e2:
                print("seed_service_fallback", e2)
                try:
                    await db.rollback()
                except Exception:
                    pass
    try:
        await db.commit()
    except Exception as e:
        print("seed_commit", e)
        try:
            await db.rollback()
        except Exception:
            pass
