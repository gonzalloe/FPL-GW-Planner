"""
FPL Predictor - Email Service
Thin wrapper around Resend's HTTP API.

Env vars:
  RESEND_API_KEY   - Resend API key (https://resend.com)
  EMAIL_FROM       - From address, e.g. "FPL Predictor <noreply@yourdomain.com>"
                     (Defaults to Resend sandbox "onboarding@resend.dev" which
                      only delivers to the account owner - fine for dev.)
  PUBLIC_BASE_URL  - Public URL of the site (e.g. https://fpl-predictor-e0zz.onrender.com)
                     Used to build verification / reset links.

Behavior when RESEND_API_KEY is missing:
  - Does NOT raise. Prints the link to stdout so local dev still works.
  - Returns {"ok": False, "dev_mode": True, "link": "..."} so callers can surface it.
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


def send_verification_email(to_email, token):
    link = f"{get_public_base_url()}/verify-email?token={token}"
    html = _wrap(
        "<p>Welcome! Please confirm your email address to activate your account:</p>"
        f'<p style="margin:24px 0;">{_button("Verify my email", link)}</p>'
        f'<p style="font-size:12px;color:#8b8f9e;">Or copy this link:<br>'
        f'<span style="color:#7fd4ff;">{link}</span></p>'
    )
    text = f"Verify your email: {link}"
    result = _send_via_resend(to_email, "Verify your FPL Predictor account", html, text)
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
    result = _send_via_resend(to_email, "Reset your FPL Predictor password", html, text)
    if not result.get("ok"):
        result["link"] = link
    return result
