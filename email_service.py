"""
FPL Predictor - Email Service
Supports two delivery backends (tried in this priority order):

  1. Generic SMTP (recommended for Gmail / personal senders with no custom domain).
     Enabled automatically when SMTP_HOST + SMTP_USER + SMTP_PASS are set.

  2. Resend HTTPS API (better for bulk / deliverability once you own a domain).
     Enabled automatically when RESEND_API_KEY is set.

  3. Dev-mode: neither configured --> prints the link to stdout and returns
     {"ok": False, "dev_mode": True, "link": "..."}. Never raises.

Env vars
--------
  # SMTP backend (Option B - Gmail etc.)
  SMTP_HOST        smtp.gmail.com
  SMTP_PORT        587 (STARTTLS) or 465 (implicit TLS)
  SMTP_USER        your-gmail@gmail.com
  SMTP_PASS        16-char Google App Password (https://myaccount.google.com/apppasswords)
  SMTP_USE_SSL     optional, "1" to force implicit TLS (port 465). Default: autodetect.

  # Resend backend (Option A)
  RESEND_API_KEY   re_xxx... from https://resend.com

  # Common
  EMAIL_FROM       From address, e.g. "FPL Predictor <your-gmail@gmail.com>".
                   Defaults to Resend sandbox "onboarding@resend.dev" (only
                   delivers to the Resend account owner - dev-only).
  PUBLIC_BASE_URL  Public URL of the site (e.g. https://fpl-predictor-e0zz.onrender.com)
                   Used to build verification / reset links.
"""
import os
import json
import urllib.request
import urllib.error


RESEND_ENDPOINT = "https://api.resend.com/emails"


def _env(name, default=""):
    return (os.environ.get(name) or default).strip()


def get_public_base_url():
    """Return canonical public URL of the site (no trailing slash)."""
    url = _env("PUBLIC_BASE_URL") or _env("RENDER_EXTERNAL_URL") or "http://localhost:8888"
    return url.rstrip("/")


def _send_via_resend(to, subject, html, text=""):
    api_key = _env("RESEND_API_KEY")
    sender = _env("EMAIL_FROM", "FPL Predictor <onboarding@resend.dev>")

    if not api_key:
        print(f"  [EMAIL] RESEND_API_KEY not set - dev mode. Would send to {to}:")
        print(f"  [EMAIL] Subject: {subject}")
        return {"ok": False, "dev_mode": True}

    payload = {"from": sender, "to": [to], "subject": subject, "html": html}
    if text:
        payload["text"] = text

    req = urllib.request.Request(
        RESEND_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8") or "{}")
            return {"ok": True, "id": body.get("id")}
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8")
        except Exception:
            pass
        print(f"  [EMAIL] Resend HTTPError {e.code}: {detail}")
        return {"ok": False, "error": f"HTTP {e.code}", "detail": detail}
    except Exception as e:
        print(f"  [EMAIL] Resend send failed: {e}")
        return {"ok": False, "error": str(e)}


def _button(label, url):
    return (
        f'<a href="{url}" style="display:inline-block;padding:12px 24px;'
        f'background:linear-gradient(135deg,#00e676,#00bfa5);color:#000;'
        f'text-decoration:none;border-radius:10px;font-weight:700;'
        f'font-family:sans-serif;font-size:14px;">{label}</a>'
    )


def _wrap(inner):
    return (
        '<div style="font-family:sans-serif;max-width:520px;margin:0 auto;'
        'padding:24px;background:#0f1222;color:#e7e9ef;border-radius:16px;">'
        '<h2 style="color:#00e676;margin-top:0;">\u26bd FPL Predictor</h2>'
        f"{inner}"
        '<hr style="border:none;border-top:1px solid #2a2d3e;margin:24px 0;">'
        '<p style="font-size:11px;color:#8b8f9e;">'
        "If you did not request this email, you can safely ignore it."
        "</p></div>"
    )


def _send_via_smtp(to, subject, html, text=""):
    """Send via generic SMTP (Gmail App Password etc.). Returns {ok,...}."""
    host = _env("SMTP_HOST")
    user = _env("SMTP_USER")
    password = _env("SMTP_PASS")
    if not (host and user and password):
        return {"ok": False, "not_configured": True}

    import smtplib
    import ssl
    from email.message import EmailMessage

    sender = _env("EMAIL_FROM", user)
    try:
        port = int(_env("SMTP_PORT", "587"))
    except ValueError:
        port = 587

    # Implicit-TLS on 465, STARTTLS on anything else (including the common 587).
    use_ssl = _env("SMTP_USE_SSL", "").lower() in ("1", "true", "yes") or port == 465

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    # Plain-text fallback body first, then HTML alternative (RFC-compliant).
    msg.set_content(text or "This email requires an HTML-capable client.")
    msg.add_alternative(html, subtype="html")

    ctx = ssl.create_default_context()
    try:
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=15) as smtp:
                smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=15) as smtp:
                smtp.ehlo()
                smtp.starttls(context=ctx)
                smtp.ehlo()
                smtp.login(user, password)
                smtp.send_message(msg)
        return {"ok": True, "backend": "smtp"}
    except smtplib.SMTPAuthenticationError as e:
        print(f"  [EMAIL] SMTP auth failed ({host}:{port}): {e}")
        return {"ok": False, "error": "SMTP auth failed. For Gmail, use a 16-char App Password, not your login password."}
    except Exception as e:
        print(f"  [EMAIL] SMTP send failed ({host}:{port}): {e}")
        return {"ok": False, "error": str(e)}


def _dispatch(to, subject, html, text=""):
    """Try SMTP first (if configured), then Resend, then dev-mode.
    Guarantees a dict return with at least {"ok": bool}."""
    # 1. SMTP
    if _env("SMTP_HOST") and _env("SMTP_USER") and _env("SMTP_PASS"):
        result = _send_via_smtp(to, subject, html, text)
        # Only fall through on "not configured"; real failures surface as-is
        # so the caller can log / surface them.
        if not result.get("not_configured"):
            return result

    # 2. Resend
    if _env("RESEND_API_KEY"):
        return _send_via_resend(to, subject, html, text)

    # 3. Dev-mode: no backend wired up. Don't raise; log + let caller surface the link.
    print(f"  [EMAIL] No backend configured (SMTP_* or RESEND_API_KEY). Would send to {to}: {subject}")
    return {"ok": False, "dev_mode": True}


def send_verification_email(to_email, token):
    link = f"{get_public_base_url()}/verify-email?token={token}"
    html = _wrap(
        "<p>Welcome! Please confirm your email address to activate your account:</p>"
        f'<p style="margin:24px 0;">{_button("Verify my email", link)}</p>'
        f'<p style="font-size:12px;color:#8b8f9e;">Or copy this link:<br>'
        f'<span style="color:#7fd4ff;">{link}</span></p>'
    )
    text = f"Verify your email: {link}"
    result = _dispatch(to_email, "Verify your FPL Predictor account", html, text)
    if not result.get("ok"):
        result["link"] = link
    return result


def send_password_reset_email(to_email, token):
    link = f"{get_public_base_url()}/reset-password?token={token}"
    html = _wrap(
        "<p>We received a request to reset your password. This link expires in 1 hour.</p>"
        f'<p style="margin:24px 0;">{_button("Reset my password", link)}</p>'
        f'<p style="font-size:12px;color:#8b8f9e;">Or copy this link:<br>'
        f'<span style="color:#7fd4ff;">{link}</span></p>'
    )
    text = f"Reset your password: {link}"
    result = _dispatch(to_email, "Reset your FPL Predictor password", html, text)
    if not result.get("ok"):
        result["link"] = link
    return result
