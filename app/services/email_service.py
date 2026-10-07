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


def _brand_wrap(title: str, body_html: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title></head>
<body style="margin:0;padding:0;background:#0B1220;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#0B1220;padding:32px 16px;">
    <tr><td align="center">
      <table role="presentation" width="100%" style="max-width:560px;background:#121a2b;border-radius:16px;border:1px solid #1e2a3f;overflow:hidden;">
        <tr><td style="padding:28px 28px 12px;text-align:center;">
          <div style="font-size:22px;font-weight:700;color:#ffffff;letter-spacing:-0.02em;">Client RaQ</div>
          <div style="font-size:13px;color:#00AEFF;margin-top:4px;">WhatsApp AI for businesses</div>
        </td></tr>
        <tr><td style="padding:8px 28px 28px;color:#e8eef7;font-size:15px;line-height:1.55;">
          {body_html}
        </td></tr>
        <tr><td style="padding:16px 28px 28px;border-top:1px solid #1e2a3f;color:#8b9bb4;font-size:12px;line-height:1.5;">
          Need help? Reply to this email or message support on WhatsApp.<br>
          <a href="https://clientraq.com" style="color:#00AEFF;text-decoration:none;">clientraq.com</a>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""


def send_signup_started_email(email: str) -> dict:
    """Right after signup form — before verification code."""
    subject = "Welcome to Client RaQ — verify your email"
    text = (
        "Welcome to Client RaQ.\n\n"
        "Thanks for signing up. We are sending a verification code to this email next.\n"
        "Enter that code to finish creating your account, then you can log in and set up your AI assistant.\n\n"
        "— Client RaQ\nhttps://clientraq.com"
    )
    body = """
      <p style="margin:0 0 16px;font-size:18px;font-weight:600;color:#fff;">Welcome to Client RaQ</p>
      <p style="margin:0 0 14px;color:#c5d0e0;">
        Thanks for signing up. A <strong style="color:#fff;">verification code</strong> is on its way to this inbox.
      </p>
      <p style="margin:0 0 14px;color:#c5d0e0;">
        Enter the code to finish setup, then log in to connect WhatsApp and configure your assistant.
      </p>
    """
    return send_email(to=email, subject=subject, html=_brand_wrap(subject, body), text=text)


def send_welcome_email(email: str, *, company_name: str | None = None, name: str | None = None) -> dict:
    """Sent after successful signup (verification completed / account ready)."""
    who = (name or company_name or "").strip() or "there"
    biz = (company_name or "").strip()
    subject = "Welcome to Client RaQ"
    text = (
        f"Hi {who},\n\n"
        "Welcome to Client RaQ.\n\n"
        "Your account is ready. You can log in, set up your services, connect WhatsApp, "
        "and let the AI handle customer requests, quotes, and payment proofs — while you stay in control.\n\n"
        + (f"Business: {biz}\n\n" if biz else "")
        + "Log in: https://clientraq.com/company/login\n\n"
        "If you did not create this account, you can ignore this email.\n\n"
        "— Client RaQ"
    )
    body = f"""
      <p style="margin:0 0 16px;font-size:18px;font-weight:600;color:#fff;">Welcome, {who}</p>
      <p style="margin:0 0 14px;color:#c5d0e0;">Your Client RaQ account is ready.</p>
      <p style="margin:0 0 14px;color:#c5d0e0;">
        Log in to set up your services and prices, connect WhatsApp, and start handling customer
        requests, quotes, and payment proofs — while you stay in control of confirmations.
      </p>
      {"<p style='margin:0 0 14px;color:#c5d0e0;'><strong style='color:#fff;'>Business:</strong> " + biz + "</p>" if biz else ""}
      <p style="margin:24px 0;">
        <a href="https://clientraq.com/company/login"
           style="display:inline-block;background:#00AEFF;color:#0B1220;font-weight:700;text-decoration:none;
                  padding:12px 22px;border-radius:999px;">Log in to Client RaQ</a>
      </p>
      <p style="margin:0;color:#8b9bb4;font-size:13px;">If you did not create this account, you can ignore this email.</p>
    """
    return send_email(to=email, subject=subject, html=_brand_wrap(subject, body), text=text)


def send_subscription_expiring_email(
    email: str,
    *,
    company_name: str,
    days_left: int,
    plan_label: str,
    ends_on: str,
) -> dict:
    """Reminder before paid plan or trial ends (e.g. 3 days)."""
    subject = f"Your Client RaQ plan ends in {days_left} day{'s' if days_left != 1 else ''}"
    text = (
        f"Hi {company_name},\n\n"
        f"Your Client RaQ {plan_label} ends on {ends_on} ({days_left} day(s) left).\n\n"
        "When it ends, your WhatsApp assistant will pause for customers until you renew.\n\n"
        "Renew now so you don’t miss orders:\n"
        "https://clientraq.com/company/billing\n\n"
        "— Client RaQ"
    )
    body = f"""
      <p style="margin:0 0 16px;font-size:18px;font-weight:600;color:#fff;">Subscription ending soon</p>
      <p style="margin:0 0 14px;color:#c5d0e0;">Hi <strong style="color:#fff;">{company_name}</strong>,</p>
      <p style="margin:0 0 14px;color:#c5d0e0;">
        Your <strong style="color:#fff;">{plan_label}</strong> ends on
        <strong style="color:#fff;">{ends_on}</strong>
        (<span style="color:#00AEFF;">{days_left} day{'s' if days_left != 1 else ''} left</span>).
      </p>
      <p style="margin:0 0 14px;color:#c5d0e0;">
        After that date, your WhatsApp assistant will pause for customers until you renew.
        Existing data stays safe — you only need an active plan for the bot to reply.
      </p>
      <p style="margin:24px 0;">
        <a href="https://clientraq.com/company/billing"
           style="display:inline-block;background:#00AEFF;color:#0B1220;font-weight:700;text-decoration:none;
                  padding:12px 22px;border-radius:999px;">Renew subscription</a>
      </p>
    """
    return send_email(to=email, subject=subject, html=_brand_wrap(subject, body), text=text)


def send_subscription_expired_email(
    email: str,
    *,
    company_name: str,
    plan_label: str = "subscription",
) -> dict:
    """Sent once when plan/trial has fully ended."""
    subject = "Your Client RaQ assistant is paused"
    text = (
        f"Hi {company_name},\n\n"
        f"Your Client RaQ {plan_label} has ended.\n\n"
        "Your WhatsApp assistant is paused for customers until you renew.\n"
        "Your chats, orders, and settings are still saved.\n\n"
        "Renew here: https://clientraq.com/company/billing\n\n"
        "— Client RaQ"
    )
    body = f"""
      <p style="margin:0 0 16px;font-size:18px;font-weight:600;color:#fff;">Assistant paused</p>
      <p style="margin:0 0 14px;color:#c5d0e0;">Hi <strong style="color:#fff;">{company_name}</strong>,</p>
      <p style="margin:0 0 14px;color:#c5d0e0;">
        Your Client RaQ <strong style="color:#fff;">{plan_label}</strong> has ended.
        The WhatsApp assistant will not reply to customers until you renew.
      </p>
      <p style="margin:0 0 14px;color:#c5d0e0;">
        Your chats, orders, services, and settings remain saved. Renew anytime to go live again.
      </p>
      <p style="margin:24px 0;">
        <a href="https://clientraq.com/company/billing"
           style="display:inline-block;background:#00AEFF;color:#0B1220;font-weight:700;text-decoration:none;
                  padding:12px 22px;border-radius:999px;">Renew now</a>
      </p>
    """
    return send_email(to=email, subject=subject, html=_brand_wrap(subject, body), text=text)
