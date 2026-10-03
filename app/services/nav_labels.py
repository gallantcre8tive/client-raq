"""Nav / page labels driven by company business_type."""
from __future__ import annotations
from typing import Any

from app.data.business_templates import get_template


def _safe_get(obj: Any, key: str, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    try:
        return getattr(obj, key, default)
    except Exception:
        return default


def labels_for_company(company: Any = None, business_type: str | None = None) -> dict[str, str]:
    bt = business_type
    if company is not None and not bt:
        bt = _safe_get(company, "business_type", None)
    try:
        tpl = get_template(bt)
    except Exception:
        tpl = {}
    name = ""
    if company is not None:
        name = (_safe_get(company, "name", None) or "") or ""
    return {
        "company_name": name or "Company",
        "business_type": (bt or "printing"),
        "business_label": (tpl.get("label") if isinstance(tpl, dict) else None) or "Business",
        "nav_dashboard": "Dashboard",
        "nav_orders": (tpl.get("orders_label") if isinstance(tpl, dict) else None) or "Orders",
        "nav_services": (tpl.get("services_label") if isinstance(tpl, dict) else None) or "Services",
        "nav_orders_short": (tpl.get("orders_label") if isinstance(tpl, dict) else None) or "Orders",
        "dashboard_label": (tpl.get("dashboard_label") if isinstance(tpl, dict) else None) or "Activity",
        "services_label": (tpl.get("services_label") if isinstance(tpl, dict) else None) or "Services",
        "orders_label": (tpl.get("orders_label") if isinstance(tpl, dict) else None) or "Orders",
        "business_summary": (tpl.get("summary") if isinstance(tpl, dict) else None) or "",
    }
