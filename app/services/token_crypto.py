"""Encrypt sensitive tokens at rest (WhatsApp / Telegram access tokens).

Uses SECRET_KEY from env. If encryption fails, falls back to plaintext so
existing installs keep working.
"""
from __future__ import annotations

import base64
import hashlib
from typing import Optional

from app.config import get_settings

PREFIX = "enc:v1:"


def _fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return None
    raw = (get_settings().SECRET_KEY or "client-raq").encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
    return Fernet(key)


def encrypt_secret(value: Optional[str]) -> Optional[str]:
    if not value:
        return value
    if value.startswith(PREFIX):
        return value
    f = _fernet()
    if not f:
        return value
    try:
        token = f.encrypt(value.encode("utf-8")).decode("utf-8")
        return PREFIX + token
    except Exception as e:
        print("encrypt_secret_fail", e)
        return value


def decrypt_secret(value: Optional[str]) -> Optional[str]:
    if not value:
        return value
    if not value.startswith(PREFIX):
        return value  # legacy plaintext
    f = _fernet()
    if not f:
        return value
    try:
        raw = value[len(PREFIX):]
        return f.decrypt(raw.encode("utf-8")).decode("utf-8")
    except Exception as e:
        print("decrypt_secret_fail", e)
        return value
