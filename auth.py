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
from pathlib import Path
from datetime import datetime, timedelta

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
        from supabase import create_client
        _supabase_client = create_client(_SUPABASE_URL, _SUPABASE_KEY)
        if not _backend_logged:
            print(f"  [AUTH] Using Supabase backend ({_SUPABASE_URL[:40]}...)")
            _backend_logged = True
        return _supabase_client
    except Exception as e:
        print(f"  [AUTH] Supabase init failed, falling back to JSON files: {e}")
        _USE_SUPABASE = False
        return None

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


def _load_users() -> dict:
    with _users_lock:
        sb = _get_supabase()
        if sb is not None:
            try:
                res = sb.table("users").select("email,data").execute()
                return {row["email"]: row["data"] for row in (res.data or [])}
            except Exception as e:
                print(f"  [AUTH] Supabase load_users failed: {e}")
                return {}
        if USERS_FILE.exists():
            return json.loads(USERS_FILE.read_text(encoding="utf-8"))
        return {}


def _save_users(users: dict):
    with _users_lock:
        sb = _get_supabase()
        if sb is not None:
            try:
                # Fetch existing emails so we can compute deletions
                existing = sb.table("users").select("email").execute()
                existing_emails = {row["email"] for row in (existing.data or [])}
                new_emails = set(users.keys())

                # Delete users that were removed
                to_delete = existing_emails - new_emails
                for email in to_delete:
                    sb.table("users").delete().eq("email", email).execute()

                # Upsert all current users
                if users:
                    rows = [{"email": e, "data": u} for e, u in users.items()]
                    sb.table("users").upsert(rows, on_conflict="email").execute()
                return
            except Exception as e:
                print(f"  [AUTH] Supabase save_users failed: {e}")
                # Fall through to file save as emergency backup
        tmp = USERS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(users, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(USERS_FILE)


def _load_sessions() -> dict:
    with _sessions_lock:
        sb = _get_supabase()
        if sb is not None:
            try:
                res = sb.table("sessions").select("token,data").execute()
                return {row["token"]: row["data"] for row in (res.data or [])}
            except Exception as e:
                print(f"  [AUTH] Supabase load_sessions failed: {e}")
                return {}
        if SESSIONS_FILE.exists():
            return json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
        return {}


def _save_sessions(sessions: dict):
    with _sessions_lock:
        sb = _get_supabase()
        if sb is not None:
            try:
                existing = sb.table("sessions").select("token").execute()
                existing_tokens = {row["token"] for row in (existing.data or [])}
                new_tokens = set(sessions.keys())

                to_delete = existing_tokens - new_tokens
                if to_delete:
                    # Chunk to avoid URL length limits on large in-lists
                    tok_list = list(to_delete)
                    for i in range(0, len(tok_list), 100):
                        sb.table("sessions").delete().in_("token", tok_list[i:i+100]).execute()

                if sessions:
                    rows = [{"token": t, "data": d} for t, d in sessions.items()]
                    # Batch upsert to avoid huge payloads
                    for i in range(0, len(rows), 500):
                        sb.table("sessions").upsert(rows[i:i+500], on_conflict="token").execute()
                return
            except Exception as e:
                print(f"  [AUTH] Supabase save_sessions failed: {e}")
        tmp = SESSIONS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(sessions, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(SESSIONS_FILE)


def register(email: str, password: str, name: str = "") -> dict:
    """Register a new user. Returns {ok, token, user} or {error}."""
    email = email.strip().lower()
    if not email or "@" not in email:
        return {"error": "Invalid email"}
    if len(password) < 6:
        return {"error": "Password must be at least 6 characters"}

    users = _load_users()
    if email in users:
        return {"error": "Email already registered"}

    hashed, salt = _hash_password(password)
    user = {
        "email": email,
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
    }
    users[email] = user
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

    users = _load_users()
    user = users.get(email)
    if not user:
        return {"error": "Email not found"}

    hashed, _ = _hash_password(password, user["salt"])
    if hashed != user["password_hash"]:
        # Track failed attempt
        attempt["count"] = attempt.get("count", 0) + 1
        if attempt["count"] >= MAX_LOGIN_ATTEMPTS:
            attempt["lockout_until"] = time.time() + LOCKOUT_SECONDS
            attempt["count"] = 0
        _login_attempts[email] = attempt
        return {"error": "Incorrect password"}

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
    sessions = _load_sessions()
    session = sessions.get(token)
    if not session:
        return None
    if time.time() - session.get("created_at", 0) > SESSION_TTL:
        del sessions[token]
        _save_sessions(sessions)
        return None

    email = session.get("email")
    users = _load_users()
    user = users.get(email)
    if not user:
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
    users = _load_users()
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
    users = _load_users()
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
    users = _load_users()
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
    sessions = _load_sessions()
    sessions[token] = {
        "email": email,
        "created_at": time.time(),
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
    users = _load_users()
    user = users.get(email)
    if not user:
        return {"error": "User not found"}
    user["team_id"] = team_id
    users[email] = user
    _save_users(users)
    return {"ok": True}


def check_chat_limit(email: str) -> dict:
    """Check if user has chat messages remaining today."""
    users = _load_users()
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
    users = _load_users()
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
