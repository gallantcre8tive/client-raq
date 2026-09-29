"""Guides for company admins: how to price each service type for the bot."""
from __future__ import annotations

# Guides keyed by pricing_method + common catalog names
GUIDES = {
    "sqft": {
        "title": "Per square foot (banners, flex, SAV, mesh…)",
        "summary": "One service. One rate per sq ft. Customer gives width × height; bot multiplies.",
        "how_to_set": [
            "Go to Services → Add (or enable from catalog).",
            "Name example: Flex Banner or SAV Sticker.",
            "Base price: your naira rate per square foot (e.g. 250).",
            "Unit / pricing method: per sq ft (or sqft).",
            "Do NOT create a separate service for every size (10x5, 12x8…). One rate is enough.",
            "Save. Optional: add a public reference image URL later.",
        ],
        "bot_formula": "width_ft × height_ft × rate × quantity",
        "examples": [
            "10 × 15 ft banner, rate ₦250, qty 2 → 10×15×250×2 = ₦75,000",
            "If customer uses inches for stickers on sq-ft rate: (W×H×qty×rate) ÷ 144",
        ],
        "mistakes": [
            "Leaving base price at 0 → bot shows NGN 0.",
            "Adding 20 size rows for banners — unnecessary; sizes come from chat.",
        ],
    },
    "sqm": {
        "title": "Per square metre",
        "summary": "Same idea as sq ft, but rate is per m².",
        "how_to_set": [
            "Base price = rate per square metre.",
            "Unit: per sq m / sqm.",
            "Customer size in metres (or bot converts from feet if needed).",
        ],
        "bot_formula": "width_m × height_m × rate × quantity",
        "examples": ["2m × 1m × ₦3,000 × 1 = ₦6,000"],
        "mistakes": ["Mixing sq ft rate with sq m unit."],
    },
    "piece": {
        "title": "Per piece (nylon, pens, single products)",
        "summary": "Fixed price for one item. Quantity multiplies the price.",
        "how_to_set": [
            "Base price = price for ONE piece (e.g. nylon ₦500).",
            "Unit: per piece.",
            "Min quantity: set if you don’t take small orders (e.g. 50 or 100).",
            "No size variants needed unless product has size options.",
        ],
        "bot_formula": "base_price × quantity",
        "examples": ["500 nylons × ₦150 = ₦75,000"],
        "mistakes": ["Putting area rates on nylon when you sell only per piece."],
    },
    "variants": {
        "title": "Fixed sizes (frames, acrylic, boxes A2/A3, frameless…)",
        "summary": "Parent service + a list of sizes each with its own price. Bot uses the size label price × qty.",
        "how_to_set": [
            "Create service: Frame (or Acrylic Frame, Box).",
            "Base price can be 0 — real money is on variants.",
            "Unit: per piece.",
            "Open the service → Variants / Sizes.",
            "Add each size as Label + Price, e.g. 5x7 = 2600, 8x10 = 3500, A3 = 4000.",
            "Acrylic: create a SEPARATE service from normal Frame so prices don’t mix.",
            "Box: labels A5, A4, A3, A2, A1 with your prices.",
        ],
        "bot_formula": "variant_price × quantity",
        "examples": [
            "2 × Frame 10x12 @ ₦4,700 = ₦9,400",
            "1 × Box A2 @ your A2 price",
        ],
        "mistakes": [
            "Only setting parent base price and forgetting variants.",
            "Putting frame sizes under Flex Banner (wrong pricing type).",
        ],
    },
    "tier": {
        "title": "Quantity tiers (business cards, flyers, jotters…)",
        "summary": "Price often changes by pack size (100, 500, 1000). Use variants as pack sizes or set base + explain in description.",
        "how_to_set": [
            "Option A: Variants = 100 pcs, 500 pcs, 1000 pcs with pack prices.",
            "Option B: Base = price per piece; bot multiplies by quantity (simple).",
            "Write minimum order in description (e.g. minimum 100).",
        ],
        "bot_formula": "pack price from variant, or base × qty",
        "examples": ["Business cards: variant “500 pcs” = ₦15,000"],
        "mistakes": ["No min qty note — customers order 5 cards unexpectedly."],
    },
    "custom": {
        "title": "Custom / quote-only jobs",
        "summary": "Complex jobs (vehicle full brand, special finishing). Bot collects info and escalates to staff.",
        "how_to_set": [
            "Enable service with base 0 or starting-from price.",
            "Description: what you need from customer (photos, measurements).",
            "Staff confirms final price in Messages / Orders.",
        ],
        "bot_formula": "No auto total until staff confirms",
        "examples": ["Car full branding — bot hands over to human"],
        "mistakes": ["Expecting exact auto price without variants or rate."],
    },
}

# Map catalog service names → guide key
NAME_TO_GUIDE = {
    "flex": "sqft", "banner": "sqft", "mesh": "sqft", "backlit": "sqft",
    "sav": "sqft", "vinyl": "sqft", "vision": "sqft", "window": "sqft",
    "sticker": "sqft", "floor sticker": "sqft", "reflective": "sqft",
    "frame": "variants", "frameless": "variants", "acrylic": "variants",
    "box": "variants", "mount": "variants",
    "nylon": "piece", "poly": "piece", "pen": "piece", "cap": "piece",
    "business card": "tier", "flyer": "tier", "brochure": "tier", "jotter": "tier",
    "exercise": "tier", "dtf": "piece", "dtg": "piece", "screen": "piece",
    "vehicle": "custom", "car branding": "custom", "uv": "sqft",
}


def guide_for_service_name(name: str) -> dict:
    low = (name or "").lower()
    for key, gkey in NAME_TO_GUIDE.items():
        if key in low:
            return GUIDES.get(gkey, GUIDES["custom"])
    return GUIDES["piece"]


def all_guides():
    return GUIDES
