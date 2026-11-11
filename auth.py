"""
FPL Predictor — User Authentication & Subscription System
Pluggable storage: Supabase Postgres (production) or JSON files (local dev).

Backend selection:
  - If SUPABASE_URL and SUPABASE_KEY env vars are set → use Supabase
  - Otherwise → fall back to local JSON files (legacy behavior)

Supports: register, login, session tokens, free/premium tiers.
"""
import json
import os
import hashlib
import secrets
import time
import threading
import copy
import re
from pathlib import Path
from datetime import datetime, timedelta, timezone

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
USERS_FILE = DATA_DIR / "users.json"
SESSIONS_FILE = DATA_DIR / "sessions.json"

# Thread-safety locks for storage access
_users_lock = threading.RLock()
_sessions_lock = threading.RLock()

# ── Storage backend: Supabase (if env configured) or JSON files (fallback) ──
_SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
_SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()
_USE_SUPABASE = bool(_SUPABASE_URL and _SUPABASE_KEY)
_supabase_client = None
_backend_logged = False


def _get_supabase():
    """Lazy-init Supabase client. Returns None if not configured or import fails."""
    global _supabase_client, _USE_SUPABASE, _backend_logged
    if not _USE_SUPABASE:
        if not _backend_logged:
            print(f"  [AUTH] Using JSON file backend ({DATA_DIR})")
            _backend_logged = True
        return None
    if _supabase_client is not None:
        return _supabase_client
    try:
        from supabase import create_client, ClientOptions
        _supabase_client = create_client(_SUPABASE_URL, _SUPABASE_KEY,
                                         options=ClientOptions(postgrest_client_timeout=5))
        if not _backend_logged:
            print(f"  [AUTH] Using Supabase backend ({_SUPABASE_URL[:40]}...)")
            _backend_logged = True
        return _supabase_client
    except Exception as e:
        raise AuthStorageError("Account storage is temporarily unavailable") from e

# Subscription config
PLANS = {
    "free": {
        "name": "Free",
        "price": 0,
        "features": [
            "Import FPL team",
            "View squad & formation",
            "Basic fixture ticker",
            "AI Chat (3 questions/day)",
        ],
        "limits": {
            "hide_xpts": True,
            "hide_detailed_stats": True,
            "chat_limit": 3,
            "no_transfer_sim": True,
            "no_chip_planner": True,
        },
    },
    "premium": {
        "name": "Premium",
        "price_usd": 2.50,
        "price_display": "$2.50/month",
        "features": [
            "All Free features",
            "Full xPts predictions for all players",
            "Transfer Simulator with Optimize XI",
            "Season Chip Planner",
            "GW Planner (multi-GW transfers)",
            "Unlimited AI Chat with what-if scenarios",
            "Per-fixture xG/xMins breakdown",
            "External news integration",
            "Injury-aware predictions",
            "Save & restore transfer plans",
        ],
        "limits": {
            "hide_xpts": False,
            "hide_detailed_stats": False,
            "chat_limit": 999,
            "no_transfer_sim": False,
            "no_chip_planner": False,
        },
    },
    "admin": {
        "name": "Admin",
        "price_usd": 0,
        "price_display": "Free (Owner)",
        "features": ["All Premium features", "Admin dashboard", "User management"],
        "limits": {
            "hide_xpts": False,
            "hide_detailed_stats": False,
            "chat_limit": 99999,
            "no_transfer_sim": False,
            "no_chip_planner": False,
            "is_admin": True,
        },
    },
}

SESSION_TTL = 30 * 24 * 3600  # 30 days
VERIFICATION_TTL = 7 * 24 * 3600  # 7 days for email verification link
RESET_TTL = 60 * 60  # 1 hour for password reset link
REQUIRE_EMAIL_VERIFICATION = os.environ.get("REQUIRE_EMAIL_VERIFICATION", "false").lower() in ("1", "true", "yes")
# _ALITE_PATCH_APPLIED_

# Rate limiting: track failed login attempts per email
_login_attempts = {}  # {email: {"count": int, "lockout_until": float}}
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_SECONDS = 300  # 5 minutes


def _hash_password(password: str, salt: str = None) -> tuple:
    """Hash password with PBKDF2-SHA256 (600k iterations). Resistant to brute-force."""
    if salt is None:
        salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac(
        'sha256', password.encode(), salt.encode(), iterations=600_000
    ).hex()
    return hashed, salt


class AuthStorageError(RuntimeError):
    """Storage failed; callers must not treat this as an empty account store."""


class ConcurrentAccountUpdate(AuthStorageError):
    """A record changed after it was read; retry from a fresh snapshot."""


class _RecordSnapshot(dict):
    def __init__(self, rows):
        super().__init__(rows)
        self.original = copy.deepcopy(rows)


def _read_records(table, key, path, identity=None):
    sb = _get_supabase()
    try:
        if sb is not None:
            if identity is not None:
                result = sb.table(table).select(f"{key},data").eq(key, identity).execute()
                return _RecordSnapshot({row[key]: row["data"] for row in (result.data or [])})
            rows = {}
            offset = 0
            # Paginate explicitly; PostgREST's default row limit is not a full table.
            while True:
                result = sb.table(table).select(f"{key},data").order(key).range(offset, offset + 499).execute()
                batch = result.data or []
                rows.update({row[key]: row["data"] for row in batch})
                if len(batch) < 500:
                    break
                offset += len(batch)
            return _RecordSnapshot(rows)
        rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(rows, dict):
            raise ValueError("Invalid account storage")
        if identity is not None:
            rows = {identity: rows[identity]} if identity in rows else {}
        return _RecordSnapshot(rows)
    except AuthStorageError:
        raise
    except Exception as exc:
        raise AuthStorageError("Account storage is temporarily unavailable") from exc


def _write_records(table, key, path, rows):
    # A plain dict is not evidence that absent records were intentionally removed.
    if not isinstance(rows, _RecordSnapshot):
        raise AuthStorageError("Account changes require a loaded snapshot")
    original = rows.original
    changed = {identity: value for identity, value in rows.items()
               if identity not in original or value != original[identity]}
    removed = original.keys() - rows.keys()
    if not changed and not removed:
        return
    sb = _get_supabase()
    try:
        if sb is not None:
            for identity in removed | changed.keys():
                revision = original.get(identity, {}).get("_revision")
                if revision is not None and (type(revision) is not int or revision < 0):
                    raise AuthStorageError("Invalid account revision")
                if identity in changed:
                    changed[identity]["_revision"] = (revision or 0) + 1
                if identity not in original:
                    sb.table(table).insert({key: identity, "data": changed[identity]}).execute()
                    continue
                query = (sb.table(table).delete() if identity in removed else
                         sb.table(table).update({"data": changed[identity]}))
                query = query.eq(key, identity)
                # Revision lives inside the existing JSON column: no schema
                # migration, and no large account payload in the query URL.
                query = (query.is_("data->>_revision", "null") if revision is None else
                         query.eq("data->>_revision", str(revision)))
                result = query.execute()
                if not result.data:
                    raise ConcurrentAccountUpdate("Account changed; please retry")
        else:
            current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            for identity in removed | changed.keys():
                if ((identity in current) != (identity in original) or
                        (identity in original and current[identity] != original[identity])):
                    raise ConcurrentAccountUpdate("Account changed; please retry")
            for identity in removed:
                del current[identity]
            current.update(changed)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        rows.original = copy.deepcopy(dict(rows))
    except AuthStorageError:
        raise
    except Exception as exc:
        # Never acknowledge an ephemeral file write when the configured DB failed.
        raise AuthStorageError("Account changes could not be saved; please retry") from exc


def _load_users(email=None):
    with _users_lock:
        return _read_records("users", "email", USERS_FILE, email)


def _save_users(users):
    with _users_lock:
        _write_records("users", "email", USERS_FILE, users)


def _load_sessions(token=None):
    with _sessions_lock:
        return _read_records("sessions", "token", SESSIONS_FILE, token)


def _save_sessions(sessions):
    with _sessions_lock:
        _write_records("sessions", "token", SESSIONS_FILE, sessions)


def reset_all_accounts():
    """Explicit destructive operation; never inferred from an empty snapshot."""
    with _users_lock, _sessions_lock:
        users, sessions = _load_users(), _load_sessions()
        users.clear()
        sessions.clear()
        _save_users(users)
        _save_sessions(sessions)


def register(email: str, password: str, name: str = "") -> dict:
    """Register a new user. Returns {ok, token, user} or {error}."""
    email = email.strip().lower()
    if (len(email) > 254 or not re.fullmatch(
            r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", email)):
        return {"error": "Invalid email"}
    if len(password) < 6:
        return {"error": "Password must be at least 6 characters"}

    users = _load_users(email)
    if email in users:
        return {"error": "Email already registered"}

    hashed, salt = _hash_password(password)
    needs_verify = REQUIRE_EMAIL_VERIFICATION
    user = {
        "email": email,
        "auth_generation": secrets.token_hex(16),
        "name": name or email.split("@")[0],
        "password_hash": hashed,
        "salt": salt,
        "plan": "free",
        "plan_expires": None,
        "created_at": datetime.now().isoformat(),
        "team_id": None,
        "chat_count_today": 0,
        "chat_date": None,
        "stripe_customer_id": None,
        "stripe_subscription_id": None,
        "email_verified": (not needs_verify),
        "verification_token": None,
        "verification_token_expires": None,
        "reset_token": None,
        "reset_token_expires": None,
        "oauth_provider": None,
    }
    users[email] = user

    # If verification required, generate token and send email. Do NOT auto-login.
    if needs_verify:
        vtoken = secrets.token_urlsafe(32)
        user["verification_token"] = vtoken
        user["verification_token_expires"] = time.time() + VERIFICATION_TTL
        users[email] = user
        _save_users(users)
        try:
            from email_service import send_verification_email
            send_verification_email(email, vtoken)
        except Exception as e:
            print(f"  [AUTH] send_verification_email failed: {e}")
        return {
            "ok": True,
            "needs_verification": True,
            "message": "Check your email to verify your account before logging in.",
            "user": _public_user(user),
        }

    _save_users(users)
    token = _create_session(email)
    return {
        "ok": True,
        "token": token,
        "user": _public_user(user),
    }


def login(email: str, password: str) -> dict:
    """Login with rate limiting. Returns {ok, token, user} or {error}."""
    email = email.strip().lower()

    # Rate limiting check
    attempt = _login_attempts.get(email, {"count": 0, "lockout_until": 0})
    if time.time() < attempt.get("lockout_until", 0):
        remaining = int(attempt["lockout_until"] - time.time())
        return {"error": f"Too many failed attempts. Try again in {remaining}s."}

    users = _load_users(email)
    user = users.get(email)
    if not user:
        return {"error": "Email not found"}

    # OAuth-only accounts (e.g. Google) have no password_hash
    if not user.get("password_hash"):
        return {"error": "This account uses social sign-in. Please use the Google button."}

    hashed, _ = _hash_password(password, user["salt"])
    if hashed != user["password_hash"]:
        # Track failed attempt
        attempt["count"] = attempt.get("count", 0) + 1
        if attempt["count"] >= MAX_LOGIN_ATTEMPTS:
            attempt["lockout_until"] = time.time() + LOCKOUT_SECONDS
            attempt["count"] = 0
        _login_attempts[email] = attempt
        return {"error": "Incorrect password"}

    # Block login if verification is still pending (explicitly False)
    if user.get("email_verified") is False:
        return {
            "error": "Please verify your email first. Check your inbox for the verification link.",
            "needs_verification": True,
        }

    # Login successful — reset rate limit
    _login_attempts.pop(email, None)

    # Check subscription expiry
    _check_plan_expiry(user)
    users[email] = user
    _save_users(users)

    token = _create_session(email)
    return {
        "ok": True,
        "token": token,
        "user": _public_user(user),
    }


def get_user_from_token(token: str) -> dict:
    """Validate session token and return user. Returns None if invalid."""
    if not token:
        return None
    sessions = _load_sessions(token)
    session = sessions.get(token)
    if not session:
        return None
    if time.time() - session.get("created_at", 0) > SESSION_TTL:
        del sessions[token]
        _save_sessions(sessions)
        return None

    email = session.get("email")
    users = _load_users(email)
    user = users.get(email)
    if (not user or session.get("auth_generation") != user.get("auth_generation")
            or session.get("session_version", 0) != user.get("session_version", 0)):
        return None

    # Only write back if plan expiry actually changed (avoid write-on-every-read)
    old_plan = user.get("plan")
    _check_plan_expiry(user)
    if user.get("plan") != old_plan:
        users[email] = user
        _save_users(users)

    return _public_user(user)


def upgrade_to_premium(email: str, months: int = 1,
                       stripe_customer_id: str = None,
                       stripe_subscription_id: str = None) -> dict:
    """Upgrade user to premium plan."""
    users = _load_users(email)
    user = users.get(email)
    if not user:
        return {"error": "User not found"}

    now = datetime.now()
    current_expiry = user.get("plan_expires")
    if current_expiry:
        try:
            base = datetime.fromisoformat(current_expiry)
            if base > now:
                # Extend from current expiry
                new_expiry = base + timedelta(days=30 * months)
            else:
                new_expiry = now + timedelta(days=30 * months)
        except (ValueError, TypeError):
            new_expiry = now + timedelta(days=30 * months)
    else:
        new_expiry = now + timedelta(days=30 * months)

    user["plan"] = "premium"
    user["plan_expires"] = new_expiry.isoformat()
    if stripe_customer_id:
        user["stripe_customer_id"] = stripe_customer_id
    if stripe_subscription_id:
        user["stripe_subscription_id"] = stripe_subscription_id

    users[email] = user
    _save_users(users)

    return {"ok": True, "user": _public_user(user)}


def downgrade_to_free(email: str) -> dict:
    """Downgrade user to free plan (called by Stripe webhooks on cancellation/failure)."""
    email = email.strip().lower()
    users = _load_users(email)
    user = users.get(email)
    if not user:
        return {"error": "User not found"}

    user["plan"] = "free"
    user["plan_expires"] = None
    user["stripe_subscription_id"] = None  # Clear subscription (it's cancelled)
    # Keep stripe_customer_id for potential re-subscription

    users[email] = user
    _save_users(users)
    return {"ok": True, "user": _public_user(user)}


def extend_premium(email: str, days: int = 35) -> dict:
    """Extend premium plan expiry (called by Stripe webhooks on successful renewal).
    Uses 35 days for monthly to provide buffer for payment processing."""
    email = email.strip().lower()
    users = _load_users(email)
    user = users.get(email)
    if not user:
        return {"error": "User not found"}

    now = datetime.now()
    # If already premium, extend from current expiry (or from now if expired)
    current_expiry = user.get("plan_expires")
    if current_expiry:
        try:
            base = datetime.fromisoformat(current_expiry)
            if base > now:
                new_expiry = base + timedelta(days=days)
            else:
                new_expiry = now + timedelta(days=days)
        except (ValueError, TypeError):
            new_expiry = now + timedelta(days=days)
    else:
        new_expiry = now + timedelta(days=days)

    user["plan"] = "premium"
    user["plan_expires"] = new_expiry.isoformat()

    users[email] = user
    _save_users(users)
    return {"ok": True, "user": _public_user(user)}


def _create_session(email: str) -> str:
    """Create a new session token."""
    token = secrets.token_hex(32)
    user = _load_users(email).get(email)
    if user is None:
        raise AuthStorageError("Account no longer exists")
    sessions = _load_sessions(token)
    sessions[token] = {
        "email": email,
        "created_at": time.time(),
        "auth_generation": user.get("auth_generation"),
        "session_version": user.get("session_version", 0),
    }
    _save_sessions(sessions)
    return token


def _check_plan_expiry(user: dict):
    """Check if premium plan has expired."""
    if user.get("plan") == "premium" and user.get("plan_expires"):
        try:
            expiry = datetime.fromisoformat(user["plan_expires"])
            if datetime.now() > expiry:
                user["plan"] = "free"
                user["plan_expires"] = None
        except (ValueError, TypeError):
            pass


def _public_user(user: dict) -> dict:
    """Return user data safe for sending to frontend."""
    plan = user.get("plan", "free")
    plan_info = PLANS.get(plan, PLANS["free"])
    return {
        "email": user.get("email"),
        "name": user.get("name"),
        "plan": plan,
        "plan_name": plan_info["name"],
        "plan_expires": user.get("plan_expires"),
        "features": plan_info["features"],
        "limits": plan_info["limits"],
        "team_id": user.get("team_id"),
        "created_at": user.get("created_at"),
    }


def update_team_id(email: str, team_id: int) -> dict:
    """Save user's FPL team ID."""
    users = _load_users(email)
    user = users.get(email)
    if not user:
        return {"error": "User not found"}
    user["team_id"] = team_id
    users[email] = user
    _save_users(users)
    return {"ok": True}


def check_chat_limit(email: str) -> dict:
    """Check if user has chat messages remaining today."""
    users = _load_users(email)
    user = users.get(email)
    if not user:
        return {"allowed": False, "remaining": 0}

    plan = user.get("plan", "free")
    limit = PLANS.get(plan, PLANS["free"])["limits"]["chat_limit"]

    today = datetime.now().strftime("%Y-%m-%d")
    if user.get("chat_date") != today:
        user["chat_count_today"] = 0
        user["chat_date"] = today

    remaining = max(0, limit - user.get("chat_count_today", 0))
    return {"allowed": remaining > 0, "remaining": remaining, "limit": limit}


def increment_chat_count(email: str):
    """Increment daily chat count."""
    users = _load_users(email)
    user = users.get(email)
    if not user:
        return
    today = datetime.now().strftime("%Y-%m-%d")
    if user.get("chat_date") != today:
        user["chat_count_today"] = 0
        user["chat_date"] = today
    user["chat_count_today"] = user.get("chat_count_today", 0) + 1
    users[email] = user
    _save_users(users)


# ── Admin Functions ──

def is_admin(user: dict) -> bool:
    """Check if user is an admin."""
    return user.get("plan") == "admin" or user.get("limits", {}).get("is_admin", False)


def list_all_users(admin_email: str) -> dict:
    """List all users (admin only)."""
    users = _load_users()
    admin = users.get(admin_email)
    if not admin or admin.get("plan") != "admin":
        return {"error": "Unauthorized"}

    user_list = []
    for email, u in users.items():
        user_list.append({
            "email": email,
            "name": u.get("name", ""),
            "plan": u.get("plan", "free"),
            "plan_expires": u.get("plan_expires"),
            "created_at": u.get("created_at"),
            "team_id": u.get("team_id"),
            "chat_count_today": u.get("chat_count_today", 0),
        })

    return {"ok": True, "users": user_list, "total": len(user_list)}


def admin_set_plan(admin_email: str, target_email: str, plan: str, months: int = 999) -> dict:
    """Admin: set a user's plan."""
    users = _load_users()
    admin = users.get(admin_email)
    if not admin or admin.get("plan") != "admin":
        return {"error": "Unauthorized"}

    target = users.get(target_email)
    if not target:
        return {"error": f"User {target_email} not found"}

    target["plan"] = plan
    if plan in ("premium", "admin"):
        target["plan_expires"] = (datetime.now() + timedelta(days=30 * months)).isoformat()
    else:
        target["plan_expires"] = None

    users[target_email] = target
    _save_users(users)
    return {"ok": True, "user": _public_user(target)}


def admin_delete_user(admin_email: str, target_email: str) -> dict:
    """Admin: delete a user."""
    users = _load_users()
    admin = users.get(admin_email)
    if not admin or admin.get("plan") != "admin":
        return {"error": "Unauthorized"}

    if target_email not in users:
        return {"error": f"User {target_email} not found"}

    if target_email == admin_email:
        return {"error": "Cannot delete yourself"}

    del users[target_email]
    _save_users(users)
    return {"ok": True, "message": f"Deleted {target_email}"}



# ──────────────────────────────────────────────────────────
# Email verification
# ──────────────────────────────────────────────────────────

def verify_email_with_token(token: str) -> dict:
    """Mark a user as verified given a valid, unexpired verification token."""
    if not token:
        return {"error": "Missing token"}
    users = _load_users()
    now = time.time()
    for email, user in users.items():
        if user.get("verification_token") == token:
            exp = user.get("verification_token_expires") or 0
            if now > exp:
                return {"error": "Verification link expired. Please request a new one."}
            user["email_verified"] = True
            user["verification_token"] = None
            user["verification_token_expires"] = None
            users[email] = user
            _save_users(users)
            # Auto-login after verification for convenience
            sess_token = _create_session(email)
            return {
                "ok": True,
                "token": sess_token,
                "user": _public_user(user),
                "message": "Email verified. You are now logged in.",
            }
    return {"error": "Invalid or already-used verification token"}


def resend_verification_email(email: str) -> dict:
    """Generate a fresh verification token and email it to the user."""
    email = (email or "").strip().lower()
    users = _load_users(email)
    user = users.get(email)
    if not user:
        # Do not leak which emails exist
        return {"ok": True, "message": "If that account exists, a verification email was sent."}
    if user.get("email_verified"):
        return {"ok": True, "message": "Account is already verified. You can log in."}

    vtoken = secrets.token_urlsafe(32)
    user["verification_token"] = vtoken
    user["verification_token_expires"] = time.time() + VERIFICATION_TTL
    users[email] = user
    _save_users(users)
    try:
        from email_service import send_verification_email
        send_verification_email(email, vtoken)
    except Exception as e:
        print(f"  [AUTH] resend verification failed: {e}")
        return {"error": "Failed to send email. Please try again later."}
    return {"ok": True, "message": "Verification email sent. Please check your inbox."}


# ──────────────────────────────────────────────────────────
# Password reset
# ──────────────────────────────────────────────────────────

def request_password_reset(email: str) -> dict:
    """Generate a reset token and email it. Always returns ok (no enumeration)."""
    email = (email or "").strip().lower()
    users = _load_users(email)
    user = users.get(email)
    # Silently succeed even if user does not exist — do not leak account presence
    if user and user.get("password_hash"):
        rtoken = secrets.token_urlsafe(32)
        user["reset_token"] = rtoken
        user["reset_token_expires"] = time.time() + RESET_TTL
        users[email] = user
        _save_users(users)
        try:
            from email_service import send_password_reset_email
            send_password_reset_email(email, rtoken)
        except Exception as e:
            print(f"  [AUTH] send_password_reset_email failed: {e}")
    return {"ok": True, "message": "If that account exists, a reset email was sent."}


def reset_password_with_token(token: str, new_password: str) -> dict:
    """Set a new password given a valid, unexpired reset token."""
    if not token:
        return {"error": "Missing token"}
    if not new_password or len(new_password) < 6:
        return {"error": "Password must be at least 6 characters"}
    users = _load_users()
    now = time.time()
    for email, user in users.items():
        if user.get("reset_token") == token:
            exp = user.get("reset_token_expires") or 0
            if now > exp:
                return {"error": "Reset link expired. Please request a new one."}
            hashed, salt = _hash_password(new_password)
            user["session_version"] = user.get("session_version", 0) + 1
            user["password_hash"] = hashed
            user["salt"] = salt
            user["reset_token"] = None
            user["reset_token_expires"] = None
            # A successful reset also implies the email is reachable → mark verified
            user["email_verified"] = True
            users[email] = user
            _save_users(users)
            # Invalidate all existing sessions for safety
            sessions = _load_sessions()
            dirty = False
            for tk, sess in list(sessions.items()):
                if sess.get("email") == email:
                    del sessions[tk]
                    dirty = True
            if dirty:
                _save_sessions(sessions)
            # Issue a fresh session so the user is logged in immediately
            sess_token = _create_session(email)
            return {
                "ok": True,
                "token": sess_token,
                "user": _public_user(user),
                "message": "Password reset successfully.",
            }
    return {"error": "Invalid or expired reset token"}


# ──────────────────────────────────────────────────────────
# OAuth (Google) — integration point for future Supabase OAuth
# ──────────────────────────────────────────────────────────

def upsert_oauth_user(email: str, name: str = "", provider: str = "google") -> dict:
    """Create or update a user based on a verified OAuth identity.
    Called by the Google OAuth callback once Supabase has verified the identity.
    Returns {ok, token, user}."""
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return {"error": "Invalid email from provider"}

    users = _load_users(email)
    user = users.get(email)
    if user is None:
        user = {
            "email": email,
            "auth_generation": secrets.token_hex(16),
            "name": name or email.split("@")[0],
            "password_hash": None,  # OAuth-only account
            "salt": None,
            "plan": "free",
            "plan_expires": None,
            "created_at": datetime.now().isoformat(),
            "team_id": None,
            "chat_count_today": 0,
            "chat_date": None,
            "stripe_customer_id": None,
            "stripe_subscription_id": None,
            "email_verified": True,  # Provider has already verified
            "verification_token": None,
            "verification_token_expires": None,
            "reset_token": None,
            "reset_token_expires": None,
            "oauth_provider": provider,
        }
    else:
        # Existing local account → link OAuth provider (do NOT wipe password)
        user["email_verified"] = True
        user["oauth_provider"] = user.get("oauth_provider") or provider
        if name and not user.get("name"):
            user["name"] = name
    users[email] = user
    _save_users(users)

    token = _create_session(email)
    return {"ok": True, "token": token, "user": _public_user(user)}


def reserve_chat_message(email):
    """Consume one daily request atomically, before paid provider work begins."""
    with _users_lock:
        for attempt in range(3):
            users = _load_users(email)
            user = users.get(email)
            if not user:
                return {"allowed": False, "remaining": 0}
            _check_plan_expiry(user)
            limit = PLANS.get(user.get("plan"), PLANS["free"])["limits"]["chat_limit"]
            today = datetime.now().strftime("%Y-%m-%d")
            count = user.get("chat_count_today", 0) if user.get("chat_date") == today else 0
            if count >= limit:
                return {"allowed": False, "remaining": 0}
            user.update(chat_date=today, chat_count_today=count + 1)
            try:
                _save_users(users)
                return {"allowed": True, "remaining": limit - count - 1}
            except ConcurrentAccountUpdate:
                if attempt == 2:
                    raise


def apply_stripe_subscription(email, event_id, event_created, subscription, customer_id,
                              allow_subscription_change=False):
    """Persist an absolute Stripe entitlement and its replay marker in one row."""
    email = email.strip().lower()
    with _users_lock:
        users = _load_users(email)
        user = users.get(email)
        if not user:
            raise AuthStorageError("Billing account is not available yet")
        if event_id in user.get("stripe_processed_events", []):
            return {"ok": True, "duplicate": True}
        if event_created < user.get("stripe_event_created", 0):
            return {"ok": True, "ignored": True}
        subscription_id = subscription.get("id")
        if (user.get("stripe_subscription_id") not in (None, subscription_id)
                and not allow_subscription_change):
            return {"ok": True, "ignored": True}
        status = subscription.get("status")
        if not subscription_id or not status:
            raise ValueError("Incomplete subscription state")
        active = status in ("active", "trialing")
        period_end = subscription.get("current_period_end")
        if not period_end:
            items = (subscription.get("items") or {}).get("data") or []
            ends = [item.get("current_period_end") for item in items if item.get("current_period_end")]
            period_end = max(ends) if ends else None
        if active and not period_end:
            raise ValueError("Subscription period is unavailable")
        if user.get("stripe_customer_id") not in (None, customer_id):
            raise ValueError("Billing customer does not match account")
        if user.get("plan") != "admin":
            user["plan"] = "premium" if active else "free"
            # Existing auth expiry handling uses naive UTC timestamps on Render.
            user["plan_expires"] = (datetime.fromtimestamp(period_end, timezone.utc)
                                    .replace(tzinfo=None).isoformat() if active else None)
        user.update(stripe_customer_id=customer_id, stripe_subscription_id=subscription_id,
                    stripe_event_created=event_created,
                    stripe_processed_events=(user.get("stripe_processed_events", []) + [event_id])[-64:])
        _save_users(users)
        return {"ok": True}
