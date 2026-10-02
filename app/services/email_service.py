"""Email delivery for signup verification codes.

Configure on Render (optional — if empty, codes are logged server-side for testing):
  SMTP_HOST=smtp.gmail.com
  SMTP_PORT=587
  SMTP_USER=your@gmail.com
  SMTP_PASSWORD=app-password
  SMTP_FROM=Client-RaQ <noreply@clientraq.com>
"""
from __future__ import annotations

import logging
import random
import smtplib
import string
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from app.config import get_settings

log = logging.getLogger("client_raq.email")


def generate_verification_code(length: int = 6) -> str:
    return "".join(random.choices(string.digits, k=length))


def smtp_configured() -> bool:
    s = get_settings()
    return bool(
        getattr(s, "SMTP_HOST", None)
        and getattr(s, "SMTP_USER", None)
        and getattr(s, "SMTP_PASSWORD", None)
    )


def send_email(*, to: str, subject: str, html: str, text: Optional[str] = None) -> dict:
    s = get_settings()
    host = (getattr(s, "SMTP_HOST", None) or "").strip()
    port = int(getattr(s, "SMTP_PORT", 587) or 587)
    user = (getattr(s, "SMTP_USER", None) or "").strip()
    password = (getattr(s, "SMTP_PASSWORD", None) or "").strip()
    from_addr = (getattr(s, "SMTP_FROM", None) or user or "noreply@clientraq.com").strip()

    if not host or not user or not password:
        log.warning("SMTP not configured — email not sent to %s subject=%s", to, subject)
        print("EMAIL_DEV_FALLBACK to=", to, "subject=", subject)
        print("EMAIL_DEV_BODY", (text or html)[:500])
        return {"ok": True, "dev_fallback": True}

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to
    msg.attach(MIMEText(text or html, "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.ehlo()
            try:
                server.starttls()
                server.ehlo()
            except Exception:
                pass
            server.login(user, password)
            server.sendmail(from_addr, [to], msg.as_string())
        log.info("email_sent to=%s subject=%s", to, subject)
        return {"ok": True, "dev_fallback": False}
    except Exception as e:
        log.exception("email_fail")
        print("EMAIL_FAIL", type(e).__name__, e)
        return {"ok": False, "error": str(e)}


def send_verification_code(email: str, code: str, purpose: str = "signup") -> dict:
    subject = "Your Client-RaQ verification code"
    text = (
        f"Your Client-RaQ verification code is: {code}\n\n"
        f"It expires in 15 minutes. If you did not request this, ignore this email.\n"
        f"Purpose: {purpose}\n"
    )
    html = f"""
    <div style="font-family:system-ui,sans-serif;max-width:480px;margin:0 auto;padding:24px">
      <h2 style="color:#0a0a0a">Client-RaQ</h2>
      <p>Your verification code is:</p>
      <p style="font-size:28px;font-weight:700;letter-spacing:6px;color:#00AEFF">{code}</p>
      <p style="color:#666;font-size:14px">Expires in 15 minutes. Purpose: {purpose}.</p>
    </div>
    """
    return send_email(to=email, subject=subject, html=html, text=text)
