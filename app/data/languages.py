"""Supported bot languages for Client RaQ multi-tenant AI."""
from __future__ import annotations

# code -> (label, reply_style_hint)
LANGUAGE_CATALOG: dict[str, dict[str, str]] = {
    "en": {
        "label": "English",
        "hint": "Clear, warm professional English. Short WhatsApp style.",
    },
    "pidgin": {
        "label": "Pidgin English (Naija)",
        "hint": "Natural Nigerian Pidgin — warm, short, human (haffa, wetin, abeg, dey, oya).",
    },
    "yo": {
        "label": "Yoruba",
        "hint": "Yoruba with light English mix when needed; respectful and clear.",
    },
    "ig": {
        "label": "Igbo",
        "hint": "Igbo with light English mix when needed; warm and clear.",
    },
    "ha": {
        "label": "Hausa",
        "hint": "Hausa with light English mix when needed; respectful and clear.",
    },
    "fr": {
        "label": "French",
        "hint": "Français clair et courtois, style WhatsApp court.",
    },
    "es": {
        "label": "Spanish",
        "hint": "Español claro y amable, mensajes cortos de WhatsApp.",
    },
    "ar": {
        "label": "Arabic",
        "hint": "Arabic (Modern Standard or simple dialect), polite and short.",
    },
    "zh": {
        "label": "Chinese (Mandarin)",
        "hint": "简体中文，简洁友好。",
    },
    "ko": {
        "label": "Korean",
        "hint": "친절하고 짧은 한국어.",
    },
    "bn": {
        "label": "Bangla (Bengali)",
        "hint": "বাংলা — short, clear, friendly.",
    },
    "pt": {
        "label": "Portuguese",
        "hint": "Português claro e amigável.",
    },
    "de": {
        "label": "German",
        "hint": "Klares, freundliches Deutsch.",
    },
    "nl": {
        "label": "Dutch",
        "hint": "Duidelijk en vriendelijk Nederlands.",
    },
    "hi": {
        "label": "Hindi",
        "hint": "सरल हिंदी, छोटा और दोस्ताना।",
    },
}

DEFAULT_LANGS = ["en", "pidgin"]


def parse_enabled_languages(raw: str | None, bot_language: str | None = None) -> list[str]:
    codes: list[str] = []
    if raw:
        try:
            import json
            data = json.loads(raw)
            if isinstance(data, list):
                codes = [str(x).strip().lower() for x in data if str(x).strip()]
        except Exception:
            codes = [x.strip().lower() for x in str(raw).split(",") if x.strip()]
    if not codes:
        bl = (bot_language or "both").lower()
        if bl == "en":
            codes = ["en"]
        elif bl == "pidgin":
            codes = ["pidgin"]
        else:
            codes = list(DEFAULT_LANGS)
    # keep only known
    return [c for c in codes if c in LANGUAGE_CATALOG] or list(DEFAULT_LANGS)


def language_system_block(enabled: list[str]) -> str:
    lines = [
        "LANGUAGE POLICY (critical):",
        "- Detect the customer language from THEIR message (text or voice transcript).",
        "- Reply in the same language/style they used when possible.",
        "- Preferred languages configured by this business:",
    ]
    for c in enabled:
        meta = LANGUAGE_CATALOG.get(c) or {}
        lines.append(f"  • {meta.get('label', c)}: {meta.get('hint', '')}")
    lines += [
        "- If customer uses a language not in the preferred list, still understand them and reply",
        "  in that language if you can, or clear simple English / Pidgin.",
        "- Never dump a robotic form. Greetings get warm human replies first.",
        "- Keep replies short (2–5 sentences), WhatsApp style, a bit witty when appropriate.",
    ]
    return "\n".join(lines)
