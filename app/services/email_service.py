"""Email delivery for signup verification codes (Resend / Gmail SMTP).

Render env:
  SMTP_HOST=smtp.resend.com
  SMTP_PORT=587
  SMTP_USER=resend
  SMTP_PASSWORD=re_xxxxxxxx
  SMTP_FROM=Client-RaQ <noreply@clientraq.com>
"""
from __future__ import annotations

import logging
import random
import re
import smtplib
import ssl
import string
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, parseaddr
from typing import Optional

from app.config import get_settings

log = logging.getLogger("client_raq.email")


def generate_verification_code(length: int = 6) -> str:
    return "".join(random.choices(string.digits, k=length))


def smtp_configured() -> bool:
    s = get_settings()
    return bool(
        (getattr(s, "SMTP_HOST", None) or "").strip()
        and (getattr(s, "SMTP_USER", None) or "").strip()
        and (getattr(s, "SMTP_PASSWORD", None) or "").strip()
    )


def _parse_from(from_addr: str, fallback_user: str) -> tuple[str, str]:
    """Return (display_header, bare_email) for SMTP envelope."""
    name, addr = parseaddr(from_addr or "")
    if not addr or "@" not in addr:
        # try angle brackets manually
        m = re.search(r"<([^>]+@[^>]+)>", from_addr or "")
        if m:
            addr = m.group(1).strip()
            name = (from_addr or "").split("<")[0].strip().strip('"') or "Client-RaQ"
        else:
            addr = (fallback_user or "noreply@clientraq.com").strip()
            if "@" not in addr:
                addr = "noreply@clientraq.com"
            name = "Client-RaQ"
    if not name:
        name = "Client-RaQ"
    header = formataddr((name, addr))
    return header, addr


def send_email(*, to: str, subject: str, html: str, text: Optional[str] = None) -> dict:
    s = get_settings()
    host = (getattr(s, "SMTP_HOST", None) or "").strip()
    port = int(getattr(s, "SMTP_PORT", 587) or 587)
    user = (getattr(s, "SMTP_USER", None) or "").strip()
    password = (getattr(s, "SMTP_PASSWORD", None) or "").strip()
    from_raw = (getattr(s, "SMTP_FROM", None) or user or "Client-RaQ <noreply@clientraq.com>").strip()

    if not host or not user or not password:
        log.warning("SMTP not configured — email not sent to %s subject=%s", to, subject)
        print("EMAIL_DEV_FALLBACK to=", to, "subject=", subject)
        print("EMAIL_DEV_BODY", (text or html)[:800])
        return {"ok": True, "dev_fallback": True, "error": None}

    from_header, from_bare = _parse_from(from_raw, user)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_header
    msg["To"] = to
    msg["Reply-To"] = from_bare
    msg.attach(MIMEText(text or html, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    raw = msg.as_string()

    errors: list[str] = []
    # Try STARTTLS on configured port (usually 587), then SSL on 465
    attempts = [(port, "starttls")]
    if port != 465:
        attempts.append((465, "ssl"))
    if port != 587:
        attempts.append((587, "starttls"))

    for try_port, mode in attempts:
        try:
            if mode == "ssl":
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(host, try_port, timeout=25, context=context) as server:
                    server.login(user, password)
                    server.sendmail(from_bare, [to], raw)
            else:
                with smtplib.SMTP(host, try_port, timeout=25) as server:
                    server.ehlo()
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                    server.login(user, password)
                    server.sendmail(from_bare, [to], raw)
            log.info("email_sent to=%s subject=%s via=%s:%s", to, subject, mode, try_port)
            print("EMAIL_SENT", to, subject, mode, try_port)
            return {"ok": True, "dev_fallback": False, "error": None, "via": f"{mode}:{try_port}"}
        except Exception as e:
            err = f"{mode}:{try_port} {type(e).__name__}: {e}"
            errors.append(err)
            log.warning("email_attempt_fail %s", err)
            print("EMAIL_ATTEMPT_FAIL", err)

    log.error("email_fail to=%s errors=%s", to, errors)
    print("EMAIL_FAIL", to, errors)
    return {"ok": False, "dev_fallback": False, "error": "; ".join(errors)[:500]}


def send_verification_code(email: str, code: str, purpose: str = "signup") -> dict:
    # Always print code to Render logs so you can recover if inbox fails
    print("EMAIL_CODE", email, code, purpose)

    subject = "Your Client-RaQ verification code"
    text = (
        f"Your Client-RaQ verification code is: {code}\n\n"
        f"This code expires in 5 minutes.\n"
        f"If you did not request this, you can ignore this email.\n"
    )
    html = f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#0b0f14;font-family:Inter,system-ui,sans-serif">
  <div style="max-width:480px;margin:32px auto;padding:28px 24px;background:#121820;border-radius:16px;border:1px solid #1e2936">
    <div style="font-size:18px;font-weight:700;color:#f8fafc;margin-bottom:8px">Client-RaQ</div>
    <p style="color:#94a3b8;font-size:14px;line-height:1.5;margin:0 0 20px">
      Use this code to verify your email and finish creating your account.
    </p>
    <div style="text-align:center;padding:18px 12px;background:#0b0f14;border-radius:12px;border:1px solid #1e2936;margin-bottom:20px">
      <div style="font-size:32px;font-weight:700;letter-spacing:10px;color:#00AEFF">{code}</div>
    </div>
    <p style="color:#64748b;font-size:12px;margin:0">Expires in 5 minutes. Purpose: {purpose}.</p>
  </div>
</body></html>"""
    return send_email(to=email, subject=subject, html=html, text=text)
