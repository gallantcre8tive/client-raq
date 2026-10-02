"""Nav / page labels driven by company business_type."""
from __future__ import annotations
from typing import Any

from app.data.business_templates import get_template


def labels_for_company(company: Any = None, business_type: str | None = None) -> dict[str, str]:
    bt = business_type
    if company is not None and not bt:
        bt = getattr(company, "business_type", None)
    tpl = get_template(bt)
    name = ""
    if company is not None:
        name = (getattr(company, "name", None) or "") or ""
    return {
        "company_name": name or "Company",
        "business_type": (bt or "printing"),
        "business_label": tpl.get("label") or "Business",
        "nav_dashboard": "Dashboard",
        "nav_orders": tpl.get("orders_label") or "Orders",
        "nav_services": tpl.get("services_label") or "Services",
        "nav_orders_short": tpl.get("orders_label") or "Orders",
        "dashboard_label": tpl.get("dashboard_label") or "Activity",
        "services_label": tpl.get("services_label") or "Services",
        "orders_label": tpl.get("orders_label") or "Orders",
        "business_summary": tpl.get("summary") or "",
    }
