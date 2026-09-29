"""Reference sample captions/descriptions for services (no copyrighted stock required).

Companies can override by setting service.reference_image_url on Service if column exists.
These are descriptive prompts + optional public placeholder links the bot can describe
or send when a public URL is configured on the service.
"""
from __future__ import annotations

# Caption templates when sending a reference (WhatsApp caption)
SAMPLE_CAPTIONS = {
    "banner": "Example of a large-format flex banner (sample style). Your print will use your artwork and the size you choose.",
    "flex": "Example flex banner finish. Send your design for an exact match.",
    "nylon": "Example branded nylon / poly bag print. Quantity and size affect price.",
    "sticker": "Example sticker / SAV print. Tell us size in inches or cm and quantity.",
    "frame": "Example photo frame sizes. Pick a size from the list we sent or type one.",
    "business card": "Example business card layout reference. Share your design or text.",
    "jotter": "Example jotter / notepad branding.",
    "signage": "Example signage board style.",
    "default": "Sample reference for this product type. Your final print follows your design and our materials.",
}


def caption_for_service(name: str) -> str:
    low = (name or "").lower()
    for key, cap in SAMPLE_CAPTIONS.items():
        if key != "default" and key in low:
            return cap
    return SAMPLE_CAPTIONS["default"]
