"""Email delivery for signup verification codes.

Primary: Resend HTTPS API (Render free tier often blocks SMTP ports 465/587).
Fallback: SMTP.

Env:
  SMTP_PASSWORD=re_xxx   # Resend API key
  SMTP_FROM=Client-RaQ <noreply@clientraq.com>
  SMTP_HOST=smtp.resend.com
  SMTP_USER=resend
  SMTP_PORT=587
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

import httpx

from app.config import get_settings

log = logging.getLogger("client_raq.email")


def generate_verification_code(length: int = 6) -> str:
    return "".join(random.choices(string.digits, k=length))


def smtp_configured() -> bool:
    s = get_settings()
    return bool((getattr(s, "SMTP_PASSWORD", None) or "").strip())


def _api_key() -> str:
    s = get_settings()
    return (getattr(s, "SMTP_PASSWORD", None) or "").strip()


def _from_parts() -> tuple[str, str]:
    s = get_settings()
    from_raw = (getattr(s, "SMTP_FROM", None) or "Client-RaQ <noreply@clientraq.com>").strip()
    name, addr = parseaddr(from_raw)
    if not addr or "@" not in addr:
        m = re.search(r"<([^>]+@[^>]+)>", from_raw)
        if m:
            addr = m.group(1).strip()
            name = from_raw.split("<")[0].strip().strip('"') or "Client-RaQ"
        else:
            addr = "noreply@clientraq.com"
            name = "Client-RaQ"
    if not name:
        name = "Client-RaQ"
    return formataddr((name, addr)), addr


def _send_resend_api(*, to: str, subject: str, html: str, text: str) -> dict:
    key = _api_key()
    if not key:
        return {"ok": False, "error": "no_api_key"}
    from_header, _ = _from_parts()
    payload = {"from": from_header, "to": [to], "subject": subject, "html": html, "text": text}
    try:
        with httpx.Client(timeout=20.0) as client:
            r = client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=payload,
            )
        body = (r.text or "")[:400]
        if r.status_code in (200, 201):
            print("EMAIL_SENT api", to, r.status_code)
            return {"ok": True, "dev_fallback": False, "error": None, "via": "resend_api"}
        err = f"resend_api HTTP {r.status_code}: {body}"
        print("EMAIL_ATTEMPT_FAIL", err)
        return {"ok": False, "error": err}
    except Exception as e:
        err = f"resend_api {type(e).__name__}: {e}"
        print("EMAIL_ATTEMPT_FAIL", err)
        return {"ok": False, "error": err}


def _send_smtp(*, to: str, subject: str, html: str, text: str) -> dict:
    s = get_settings()
    host = (getattr(s, "SMTP_HOST", None) or "").strip()
    port = int(getattr(s, "SMTP_PORT", 587) or 587)
    user = (getattr(s, "SMTP_USER", None) or "").strip()
    password = _api_key()
    if not host or not user or not password:
        return {"ok": False, "error": "smtp_not_configured"}
    from_header, from_bare = _from_parts()
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_header
    msg["To"] = to
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    raw = msg.as_string()
    errors = []
    for try_port, mode in ((port, "starttls"), (465, "ssl")):
        try:
            if mode == "ssl":
                with smtplib.SMTP_SSL(host, try_port, timeout=10, context=ssl.create_default_context()) as server:
                    server.login(user, password)
                    server.sendmail(from_bare, [to], raw)
            else:
                with smtplib.SMTP(host, try_port, timeout=10) as server:
                    server.ehlo()
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                    server.login(user, password)
                    server.sendmail(from_bare, [to], raw)
            print("EMAIL_SENT smtp", mode, try_port, to)
            return {"ok": True, "dev_fallback": False, "error": None, "via": f"smtp:{mode}:{try_port}"}
        except Exception as e:
            errors.append(f"{mode}:{try_port} {type(e).__name__}: {e}")
            print("EMAIL_ATTEMPT_FAIL", errors[-1])
    return {"ok": False, "error": "; ".join(errors)[:400]}


def send_email(*, to: str, subject: str, html: str, text: Optional[str] = None) -> dict:
    text = text or html
    if not _api_key():
        print("EMAIL_DEV_FALLBACK to=", to, "subject=", subject)
        print("EMAIL_DEV_BODY", (text or html)[:500])
        return {"ok": True, "dev_fallback": True, "error": None}
    api = _send_resend_api(to=to, subject=subject, html=html, text=text)
    if api.get("ok"):
        return api
    smtp = _send_smtp(to=to, subject=subject, html=html, text=text)
    if smtp.get("ok"):
        return smtp
    combined = f"api=[{api.get('error')}]; smtp=[{smtp.get('error')}]"
    print("EMAIL_FAIL", to, combined)
    return {"ok": False, "dev_fallback": False, "error": combined[:500]}


def send_verification_code(email: str, code: str, purpose: str = "signup") -> dict:
    print("EMAIL_CODE", email, code, purpose)
    subject = "Your Client-RaQ verification code"
    text = (
        f"Your Client-RaQ verification code is: {code}\n\n"
        f"This code expires in 5 minutes.\n"
        f"If you did not request this, ignore this email.\n"
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
    <p style="color:#64748b;font-size:12px;margin:0">Expires in 5 minutes.</p>
  </div>
</body></html>"""
    return send_email(to=email, subject=subject, html=html, text=text)
