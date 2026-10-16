"""
Service-aware workflow engine for Client RaQ.

Maps each product/service family to:
- required fields (ask only if missing)
- optional fields
- finishing options (only when relevant — e.g. eyelets for banners, NOT stickers)
- pricing method hints
- next-question templates (en + pidgin)

Business types other than printing use their own families so exchanger
never sees banner questions, real estate never sees sticker sizes, etc.
"""
from __future__ import annotations
import re
from typing import Any

# ---------------------------------------------------------------------------
# Service families (printing)
# ---------------------------------------------------------------------------

PRINT_FAMILIES: dict[str, dict[str, Any]] = {
    "banner": {
        "match": ("banner", "flex", "mesh", "backlit", "billboard", "backdrop", "frontlit"),
        "exclude": ("roll-up", "rollup", "x-banner", "teardrop", "sticker", "sav"),
        "required": ("size", "qty"),
        "optional": ("material", "design", "finishing", "fulfillment"),
        "finishing": ("eyelets", "hemming", "whiteout", "rope", "pole_pocket"),
        "pricing": "sqft",
        "default_unit": "ft",
        "ask_size": {
            "en": "What size do you need (width x height)? Feet or metres?",
            "pidgin": "Which size you wan (width x height)? Feet or metres?",
        },
        "ask_finishing": {
            "en": "Any finishing — eyelets (hang holes), hemming, or whiteout/blockout?",
            "pidgin": "You need finishing — eyelets (hang holes), hemming, or whiteout?",
        },
    },
    "sticker": {
        "match": ("sticker", "sav", "vinyl", "label", "decal", "one-way", "frosted", "window film"),
        "exclude": ("banner", "flex", "frame"),
        "required": ("size", "qty"),
        "optional": ("cut", "design", "surface", "fulfillment"),
        "finishing": ("print_cut", "lamination"),  # NO eyelets
        "pricing": "sqft",
        "default_unit": "in",
        "ask_size": {
            "en": "What size (width x height) and how many pieces? Inches or feet?",
            "pidgin": "Abeg tell me size (width x height) and how many pieces. Inches or feet?",
        },
        "ask_finishing": {
            "en": "Do you need print & cut (we cut each shape), or print only on sheet?",
            "pidgin": "You want print & cut (we cut the shape), or print only for sheet?",
        },
    },
    "rollup": {
        "match": ("roll-up", "rollup", "x-banner", "x banner", "teardrop", "l-banner"),
        "exclude": (),
        "required": ("variant", "qty"),
        "optional": ("design", "fulfillment"),
        "finishing": (),
        "pricing": "piece",
        "default_unit": "piece",
        "ask_size": {
            "en": "Which size — e.g. 85x200 or 100x200 — and how many stands?",
            "pidgin": "Which size — 85x200 or 100x200 — and how many?",
        },
    },
    "frame": {
        "match": ("frame", "frameless", "acrylic", "photo frame"),
        "exclude": (),
        "required": ("variant", "qty"),
        "optional": ("design", "fulfillment"),
        "finishing": (),
        "pricing": "fixed_size",
        "default_unit": "piece",
        "ask_size": {
            "en": "Which frame size do you need? (e.g. 8x10, 12x16, 16x20)",
            "pidgin": "Which frame size you wan? (e.g. 8x10, 12x16, 16x20)",
        },
    },
    "nylon": {
        "match": ("nylon", "poly bag", "carrier bag"),
        "exclude": (),
        "required": ("qty",),
        "optional": ("size", "colours", "design", "fulfillment"),
        "finishing": (),
        "pricing": "tier_qty",
        "default_unit": "piece",
        "ask_size": {
            "en": "How many nylon bags, and do you have a size or we'll use standard?",
            "pidgin": "How many nylon, and you get size or we use standard?",
        },
    },
    "flyer": {
        "match": ("flyer", "flyer", "leaflet", "handbill"),
        "exclude": (),
        "required": ("qty",),
        "optional": ("size", "paper", "sides", "design", "fulfillment"),
        "finishing": ("lamination",),
        "pricing": "piece",
        "default_unit": "piece",
        "ask_size": {
            "en": "How many flyers, and which size (A5, A4, A6)?",
            "pidgin": "How many flyer, which size (A5, A4, A6)?",
        },
    },
    "business_card": {
        "match": ("business card", "name card", "visiting card"),
        "exclude": (),
        "required": ("qty",),
        "optional": ("sides", "finish", "design", "fulfillment"),
        "finishing": ("lamination", "spot_uv"),
        "pricing": "piece",
        "default_unit": "piece",
        "ask_size": {
            "en": "How many cards (e.g. 100, 500, 1000)? Single or double sided?",
            "pidgin": "How many card (100, 500, 1000)? One side or two side?",
        },
    },
    "garment": {
        "match": ("t-shirt", "hoodie", "cloth", "dtf", "dtg", "flock", "screen print", "cap branding"),
        "exclude": (),
        "required": ("qty",),
        "optional": ("garment_type", "size_chart", "method", "design", "fulfillment"),
        "finishing": (),
        "pricing": "piece",
        "default_unit": "piece",
        "ask_size": {
            "en": "Which garment and how many? Do you supply the cloth or we source it?",
            "pidgin": "Which cloth and how many? You go supply the cloth or we buy am?",
        },
    },
    "pen": {
        "match": ("pen", "jotter", "notebook", "exercise book"),
        "exclude": (),
        "required": ("qty",),
        "optional": ("design", "fulfillment"),
        "finishing": (),
        "pricing": "piece",
        "default_unit": "piece",
        "ask_size": {
            "en": "How many pieces do you need?",
            "pidgin": "How many pieces you wan?",
        },
    },
    "generic_print": {
        "match": (),
        "exclude": (),
        "required": ("size", "qty"),
        "optional": ("design", "fulfillment"),
        "finishing": (),
        "pricing": "sqft",
        "default_unit": "ft",
        "ask_size": {
            "en": "Please share size (width x height) and quantity.",
            "pidgin": "Abeg send size (width x height) and quantity.",
        },
    },
}

# Other business families (high-level next steps)
OTHER_FAMILIES: dict[str, dict[str, Any]] = {
    "exchanger": {
        "required": ("method", "amount"),
        "optional": ("currency", "proof", "payout_details"),
        "finishing": (),
        "ask_next": {
            "en": "Which method (PayPal, bank, Cash App…), and how much are you sending/receiving?",
            "pidgin": "Which method (PayPal, bank, Cash App…), and how much?",
        },
    },
    "laundry": {
        "required": ("items",),
        "optional": ("pickup", "express", "stain"),
        "finishing": (),
        "ask_next": {
            "en": "What items (shirts, duvet…), and do you need pickup?",
            "pidgin": "Wetin you get (shirt, duvet…), you need pickup?",
        },
    },
    "fashion": {
        "required": ("garment",),
        "optional": ("measurements", "fabric", "deadline", "design"),
        "finishing": (),
        "ask_next": {
            "en": "What are you sewing or altering? Do you have measurements or a reference photo?",
            "pidgin": "Wetin you wan sew or alter? You get measurement or sample photo?",
        },
    },
    "restaurant": {
        "required": ("items",),
        "optional": ("qty", "delivery", "time"),
        "finishing": (),
        "ask_next": {
            "en": "What would you like from the menu?",
            "pidgin": "Wetin you wan order from menu?",
        },
    },
    "phone_repair": {
        "required": ("device", "issue"),
        "optional": ("model",),
        "finishing": (),
        "ask_next": {
            "en": "Which phone/device, and what is the problem?",
            "pidgin": "Which phone, and wetin spoil?",
        },
    },
    "retail": {
        "required": ("product",),
        "optional": ("variant", "qty"),
        "finishing": (),
        "ask_next": {
            "en": "Which product or model are you looking for?",
            "pidgin": "Which product or model you dey find?",
        },
    },
    "skincare": {
        "required": ("product_or_service",),
        "optional": ("concern", "appointment"),
        "finishing": (),
        "ask_next": {
            "en": "Are you looking for a product or a treatment/appointment?",
            "pidgin": "You wan product or treatment/appointment?",
        },
    },
    "logistics": {
        "required": ("pickup", "destination"),
        "optional": ("weight", "speed"),
        "finishing": (),
        "ask_next": {
            "en": "Pickup location and delivery destination?",
            "pidgin": "Where we go pick am, and where e dey go?",
        },
    },
    "real_estate": {
        "required": ("intent",),  # rent/buy
        "optional": ("area", "budget", "bedrooms", "type"),
        "finishing": (),
        "ask_next": {
            "en": "Are you looking to rent or buy? Preferred area and budget?",
            "pidgin": "You wan rent or buy? Which area and budget?",
        },
    },
    "education": {
        "required": ("course",),
        "optional": ("level", "schedule"),
        "finishing": (),
        "ask_next": {
            "en": "Which course or training are you interested in?",
            "pidgin": "Which course or training you wan?",
        },
    },
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower().strip())


def _has_term(blob: str, term: str) -> bool:
    """Match whole words/phrases; avoid 'x banner' matching inside 'flex banner'."""
    t = term.lower().strip()
    if not t:
        return False
    # phrase with spaces or hyphens: require non-alnum boundaries
    pattern = r"(?<![a-z0-9])" + re.escape(t).replace(" ", r"[\s\-]+") + r"(?![a-z0-9])"
    return re.search(pattern, blob) is not None


def detect_print_family(service_name: str | None, text: str = "") -> str:
    """Return family key for a printing service name or free text."""
    blob = _norm(f"{service_name or ''} {text or ''}")
    # Prefer more specific families first (rollup before banner so roll-up ≠ flex banner)
    order = ("rollup", "sticker", "frame", "nylon", "flyer", "business_card", "garment", "pen", "banner")
    for key in order:
        fam = PRINT_FAMILIES[key]
        if any(_has_term(blob, m) for m in fam["match"]):
            if fam.get("exclude") and any(_has_term(blob, x) for x in fam["exclude"]):
                # e.g. "sticker banner" rare — prefer sticker if sticker matched
                if key == "banner" and any(_has_term(blob, x) for x in ("sticker", "sav", "label")):
                    continue
            return key
    if any(_has_term(blob, x) for x in ("banner", "flex", "mesh", "billboard")):
        return "banner"
    if any(_has_term(blob, x) for x in ("sticker", "sav", "vinyl", "label")):
        return "sticker"
    if any(x in blob for x in ("print",)):
        return "generic_print"
    return "generic_print"


def family_for_service(service: Any, text: str = "", business_type: str = "printing") -> dict[str, Any]:
    """Resolve workflow family dict for a Service ORM object or name string."""
    bt = (business_type or "printing").lower().replace(" ", "_").replace("-", "_")
    if "print" not in bt and bt not in ("", "print"):
        # non-printing business
        fam = OTHER_FAMILIES.get(bt) or OTHER_FAMILIES.get("retail")
        return {"key": bt, **(fam or {})}

    name = ""
    if service is not None:
        name = getattr(service, "name", None) or str(service)
    key = detect_print_family(name, text)
    base = PRINT_FAMILIES.get(key) or PRINT_FAMILIES["generic_print"]
    return {"key": key, **base}


def allowed_finishing(service: Any, text: str = "", business_type: str = "printing") -> tuple[str, ...]:
    fam = family_for_service(service, text, business_type)
    return tuple(fam.get("finishing") or ())


def is_finishing_allowed(option: str, service: Any, text: str = "", business_type: str = "printing") -> bool:
    opt = (option or "").lower().replace(" ", "_")
    allowed = allowed_finishing(service, text, business_type)
    # map aliases
    aliases = {
        "eyelet": "eyelets",
        "eyelets": "eyelets",
        "grommet": "eyelets",
        "whiteout": "whiteout",
        "white_out": "whiteout",
        "blockout": "whiteout",
        "cut": "print_cut",
        "print_cut": "print_cut",
        "print_and_cut": "print_cut",
        "hem": "hemming",
        "hemming": "hemming",
    }
    key = aliases.get(opt, opt)
    return key in allowed


def missing_required(ctx: dict, service: Any = None, text: str = "", business_type: str = "printing") -> list[str]:
    """List required workflow fields still missing from conversation ctx."""
    fam = family_for_service(service or ctx.get("service_name"), text, business_type)
    missing = []
    for field in fam.get("required") or ():
        if field == "size":
            if not (ctx.get("width") and ctx.get("height")):
                missing.append("size")
        elif field == "qty":
            if not ctx.get("qty"):
                missing.append("qty")
        elif field == "variant":
            if not (ctx.get("variant_label") or ctx.get("variant_id")):
                missing.append("variant")
        elif field == "method":
            if not ctx.get("method"):
                missing.append("method")
        elif field == "amount":
            if not ctx.get("amount"):
                missing.append("amount")
        elif field == "items":
            if not ctx.get("items"):
                missing.append("items")
        elif field == "device":
            if not ctx.get("device"):
                missing.append("device")
        elif field == "issue":
            if not ctx.get("issue"):
                missing.append("issue")
        elif field == "product":
            if not ctx.get("product"):
                missing.append("product")
        elif field == "intent":
            if not ctx.get("intent"):
                missing.append("intent")
        elif field == "course":
            if not ctx.get("course"):
                missing.append("course")
        elif field == "garment":
            if not ctx.get("garment"):
                missing.append("garment")
        elif field == "product_or_service":
            if not (ctx.get("product") or ctx.get("service_name")):
                missing.append("product_or_service")
        elif field == "pickup":
            if not ctx.get("pickup_location"):
                missing.append("pickup")
        elif field == "destination":
            if not ctx.get("destination"):
                missing.append("destination")
    return missing


def next_question(ctx: dict, service: Any = None, text: str = "", business_type: str = "printing", pidgin: bool = True) -> str | None:
    """Return the single next relevant question, or None if ready to quote/act."""
    fam = family_for_service(service or ctx.get("service_name"), text, business_type)
    lang = "pidgin" if pidgin else "en"
    missing = missing_required(ctx, service, text, business_type)

    if not missing:
        # optional finishing only if family allows and not yet decided
        finishing = fam.get("finishing") or ()
        if finishing and not ctx.get("finishing_asked") and not ctx.get("quote_locked"):
            # only after size/qty known
            if ctx.get("width") or ctx.get("qty") or ctx.get("variant_label"):
                ask = fam.get("ask_finishing") or {}
                if ask:
                    return ask.get(lang) or ask.get("en")
        if not ctx.get("quote_locked") and (ctx.get("width") or ctx.get("qty") or ctx.get("variant_label")):
            return None  # ready for price
        if not ctx.get("fulfillment") and ctx.get("quote_locked"):
            return (
                "You wan *pickup* or *delivery*?"
                if pidgin
                else "Would you like *pickup* or *delivery*?"
            )
        return None

    field = missing[0]
    if field == "size":
        ask = fam.get("ask_size") or {}
        return ask.get(lang) or ask.get("en") or (
            "Abeg send size (width x height) and quantity." if pidgin else "Please share size and quantity."
        )
    if field == "qty":
        return "How many pieces/units?" if not pidgin else "How many pieces you wan?"
    if field == "variant":
        ask = fam.get("ask_size") or {}
        return ask.get(lang) or ask.get("en")
    if field in ("method", "amount"):
        ask = fam.get("ask_next") or {}
        return ask.get(lang) or ask.get("en")
    ask = fam.get("ask_next") or fam.get("ask_size") or {}
    return ask.get(lang) or ask.get("en")


def workflow_hint_for_prompt(service: Any, ctx: dict, business_type: str = "printing") -> str:
    """Short block injected into AI system/context so Grok follows the right family."""
    fam = family_for_service(service or ctx.get("service_name"), "", business_type)
    key = fam.get("key", "generic")
    required = ", ".join(fam.get("required") or ())
    finishing = ", ".join(fam.get("finishing") or ()) or "none"
    return (
        f"ACTIVE SERVICE FAMILY: {key}. "
        f"Required fields: {required}. "
        f"Allowed finishing ONLY: {finishing}. "
        f"Do NOT ask about finishing options outside this list. "
        f"Do NOT ask banner eyelets for stickers/labels. "
        f"Already collected: width={ctx.get('width')} height={ctx.get('height')} "
        f"qty={ctx.get('qty')} unit={ctx.get('size_unit')} fulfillment={ctx.get('fulfillment')} "
        f"total={ctx.get('locked_total')}."
    )
