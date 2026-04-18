"""
FPL Predictor — User Authentication & Subscription System
Lightweight auth with JSON file storage. No database required.
Supports: register, login, session tokens, free/premium tiers.
"""
import json
import hashlib
import secrets
import time
from pathlib import Path
from datetime import datetime, timedelta

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
USERS_FILE = DATA_DIR / "users.json"
SESSIONS_FILE = DATA_DIR / "sessions.json"

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


def _hash_password(password: str, salt: str = None) -> tuple:
    """Hash password with salt using SHA-256."""
    if salt is None:
        salt = secrets.token_hex(16)
    hashed = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return hashed, salt


def _load_users() -> dict:
    if USERS_FILE.exists():
        return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    return {}


def _save_users(users: dict):
    USERS_FILE.write_text(json.dumps(users, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_sessions() -> dict:
    if SESSIONS_FILE.exists():
        return json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
    return {}


def _save_sessions(sessions: dict):
    SESSIONS_FILE.write_text(json.dumps(sessions, indent=2, ensure_ascii=False), encoding="utf-8")


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
    """Login. Returns {ok, token, user} or {error}."""
    email = email.strip().lower()
    users = _load_users()
    user = users.get(email)
    if not user:
        return {"error": "Email not found"}

    hashed, _ = _hash_password(password, user["salt"])
    if hashed != user["password_hash"]:
        return {"error": "Incorrect password"}

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

    _check_plan_expiry(user)
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
