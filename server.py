"""
FPL Predictor - Web Server (v9 — Scalable Flask + Gunicorn)
Designed for Render free tier: 512MB RAM, 1 CPU, ephemeral disk.

Scalability features:
  - API rate limiting (flask-limiter) to prevent abuse
  - In-memory response caching with TTL for heavy endpoints
  - Thread-safe JSON file operations (via auth.py locks)
  - Static asset cache headers for CDN/browser caching
  - Health check endpoint for monitoring
  - Auth-protected heavy computation endpoints
"""
import json
import sys
import os
import time
import threading
import hashlib
from pathlib import Path
from datetime import datetime
from functools import wraps

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)
sys.path.insert(0, str(Path(__file__).parent))

from flask import Flask, request, jsonify, send_from_directory, make_response, redirect

# Rate limiting — graceful fallback if flask-limiter not installed
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    _HAS_LIMITER = True
except ImportError:
    _HAS_LIMITER = False

PORT = int(os.environ.get("PORT", 8888))
BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
SETTINGS_FILE = BASE_DIR / "user_settings.json"
REFRESH_INTERVAL = 2 * 3600
_last_refresh = 0
_refresh_lock = threading.Lock()
_refresh_thread_started = False

app = Flask(__name__, static_folder=None)  # Disable Flask's default static handling

# ── Rate Limiter (degrades gracefully if dependency missing) ──
if _HAS_LIMITER:
    limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=["120 per minute"],  # Global default
        storage_uri="memory://",
    )
else:
    # Stub limiter that does nothing
    class _NoopLimiter:
        def limit(self, *a, **kw):
            def decorator(f): return f
            return decorator
        def exempt(self, f): return f
    limiter = _NoopLimiter()

# ── In-Memory Response Cache ──
_response_cache = {}  # {cache_key: {"data": ..., "expires": timestamp}}
_cache_lock = threading.Lock()


def cached_response(ttl_seconds: int = 60, key_prefix: str = ""):
    """Decorator: cache JSON responses in memory with TTL.
    Greatly reduces CPU load for repeat requests to heavy endpoints."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            # Build cache key from endpoint + query string
            cache_key = f"{key_prefix or f.__name__}:{request.full_path}"
            now = time.time()
            with _cache_lock:
                entry = _response_cache.get(cache_key)
                if entry and entry["expires"] > now:
                    return jsonify(entry["data"])
            # Cache miss — compute
            result = f(*args, **kwargs)
            # Only cache successful JSON responses
            if isinstance(result, tuple):
                resp, code = result
                if code == 200:
                    try:
                        data = resp.get_json()
                        with _cache_lock:
                            _response_cache[cache_key] = {"data": data, "expires": now + ttl_seconds}
                    except Exception:
                        pass
                return result
            else:
                try:
                    data = result.get_json()
                    with _cache_lock:
                        _response_cache[cache_key] = {"data": data, "expires": now + ttl_seconds}
                except Exception:
                    pass
                return result
        return wrapper
    return decorator


def invalidate_cache(prefix: str = ""):
    """Invalidate cached responses (call after data refresh or prediction regeneration)."""
    with _cache_lock:
        if not prefix:
            _response_cache.clear()
        else:
            keys_to_del = [k for k in _response_cache if k.startswith(prefix)]
            for k in keys_to_del:
                del _response_cache[k]


# ── Utilities ──

_settings_lock = threading.Lock()

# _SERVER_PERSIST_PATCH_
# user_settings (e.g. team_id) persisted via app_storage so they survive
# container restarts on Render. Falls back to the old local file when
# Supabase isn't configured. On first read after migration we also pull
# any pre-existing user_settings.json to seed the store.
def _load_settings():
    with _settings_lock:
        try:
            from app_storage import get_setting, set_setting
            data = get_setting("user_settings", None)
            if data is None and SETTINGS_FILE.exists():
                # One-time migration: seed storage with the legacy file contents
                try:
                    data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                    if data:
                        set_setting("user_settings", data)
                        print("  [SETTINGS] Migrated legacy user_settings.json to app_storage.")
                except Exception as e:
                    print(f"  [SETTINGS] Legacy migration read failed: {e}")
            return data or {}
        except Exception as e:
            print(f"  [SETTINGS] _load_settings fallback to file: {e}")
            if SETTINGS_FILE.exists():
                return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            return {}

def _save_settings(data):
    with _settings_lock:
        try:
            from app_storage import set_setting
            set_setting("user_settings", data)
        except Exception as e:
            print(f"  [SETTINGS] _save_settings storage write failed: {e}")
        # Always also mirror to disk as an emergency backup (best-effort)
        try:
            tmp = SETTINGS_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(SETTINGS_FILE)
        except Exception:
            pass

# Load any admin-tuned weights from persistent storage on startup.
# This must happen BEFORE the first _run_predictions so the background
# refresh thread picks them up.
try:
    from model_optimizer import load_saved_weights as _load_saved_weights
    _load_saved_weights()
except Exception as _e:
    print(f"  [STARTUP] load_saved_weights skipped: {_e}")


def _run_predictions(gw=None):
    from prediction_engine import PredictionEngine
    from squad_optimizer import SquadOptimizer, ChipAdvisor
    engine = PredictionEngine()
    target_gw = gw or engine.next_gw
    gw_info = engine.get_gw_info(target_gw)
    predictions = engine.predict_all(target_gw)
    optimizer = SquadOptimizer(predictions)
    squad = optimizer.optimize_squad()
    chip_advisor = ChipAdvisor(predictions, gw_info)
    chip_analysis = chip_advisor.analyze()
    bb_squad = optimizer.optimize_squad(chip="bench_boost")
    output = {
        "generated_at": datetime.now().isoformat(), "gameweek": target_gw,
        "gw_info": gw_info, "predictions": predictions, "squad": squad,
        "bb_squad": bb_squad, "chip_analysis": chip_analysis,
        "top_picks": predictions[:30],
        "differentials": [p for p in predictions if float(p.get("selected_by_percent", 0)) < 10
                          and p.get("starter_quality", {}).get("tier") in ("nailed", "regular")][:15],
        "value_picks": sorted([p for p in predictions if p.get("price", 99) <= 6.5
                               and p.get("starter_quality", {}).get("tier") in ("nailed", "regular")],
                              key=lambda x: x["predicted_points"] / max(x.get("price", 4), 3.5), reverse=True)[:15],
    }
    OUTPUT_DIR.mkdir(exist_ok=True)
    filename = OUTPUT_DIR / f"gw{target_gw}_predictions.json"
    filename.write_text(json.dumps(output, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    invalidate_cache()  # Clear all cached responses when predictions change
    return output

def _refresh_data():
    global _last_refresh
    if not _refresh_lock.acquire(blocking=False):
        print("  [REFRESH] Already running, skipping.")
        return
    try:
        cache_dir = BASE_DIR / "cache"
        if cache_dir.exists():
            for f in cache_dir.glob("*.json"):
                try: f.unlink()
                except: pass
        print(f"  [REFRESH] {datetime.now().strftime('%H:%M:%S')} — Running predictions...")
        _run_predictions()
        # Also ensure last-completed GW predictions exist (needed by model optimizer)
        try:
            from data_fetcher import get_current_gameweek
            current_gw = get_current_gameweek()
            last_completed = current_gw - 1
            if last_completed > 0:
                last_file = OUTPUT_DIR / f"gw{last_completed}_predictions.json"
                if not last_file.exists():
                    print(f"  [REFRESH] Generating GW{last_completed} predictions for model analysis...")
                    _run_predictions(gw=last_completed)
        except Exception as e:
            print(f"  [REFRESH] Could not generate last-completed GW predictions: {e}")
        _last_refresh = time.time()
        print(f"  [REFRESH] {datetime.now().strftime('%H:%M:%S')} — Done.")
    except Exception as e:
        print(f"  [REFRESH] ERROR: {e}")
    finally:
        _refresh_lock.release()

def _auto_refresh_loop():
    global _last_refresh
    # Wait before first refresh to let server stabilize
    time.sleep(90)
    while True:
        try:
            if time.time() - _last_refresh >= REFRESH_INTERVAL:
                _refresh_data()
            time.sleep(60)
        except Exception as e:
            print(f"  [AUTO-REFRESH] Error: {e}")
            time.sleep(300)

def _ensure_refresh_thread():
    """Start refresh thread exactly once per process."""
    global _refresh_thread_started
    if _refresh_thread_started:
        return
    _refresh_thread_started = True
    t = threading.Thread(target=_auto_refresh_loop, daemon=True)
    t.start()
    print(f"  [INFO] Auto-refresh thread started (pid={os.getpid()})")

_setup_done = False

def _auto_setup_accounts():
    global _setup_done
    if _setup_done:
        return
    _setup_done = True

    from auth import register, _load_users, _save_users, _hash_password
    from datetime import timedelta
    admin_email = os.environ.get("ADMIN_EMAIL", "")
    admin_pass = os.environ.get("ADMIN_PASSWORD", "")
    if not admin_email or not admin_pass:
        return
    users = _load_users()
    far = (datetime.now() + timedelta(days=365 * 99)).isoformat()

    accounts = [
        (admin_email, admin_pass, "Admin", "admin"),
        (os.environ.get("CC_EMAIL", ""), os.environ.get("CC_PASSWORD", ""), "CC", "premium"),
        (os.environ.get("CC2_EMAIL", ""), os.environ.get("CC2_PASSWORD", ""), "CC Alt", "free"),
    ]

    for email, password, name, plan in accounts:
        if not email or not password:
            continue
        if email not in users:
            register(email, password, name)
            # register() saves with plan="free" — immediately fix the plan
            users = _load_users()
            users[email]["plan"] = plan
            users[email]["plan_expires"] = far
            _save_users(users)  # Save immediately so next register() doesn't overwrite
            print(f"  [SETUP] Created account: {email} ({plan})")
        else:
            changed = False
            # Verify password matches env var — reset if not
            hashed, _ = _hash_password(password, users[email]["salt"])
            if hashed != users[email]["password_hash"]:
                new_hash, new_salt = _hash_password(password)
                users[email]["password_hash"] = new_hash
                users[email]["salt"] = new_salt
                changed = True
                print(f"  [SETUP] Password synced from env for: {email}")
            # Ensure plan is correct
            if users[email].get("plan") != plan:
                users[email]["plan"] = plan
                users[email]["plan_expires"] = far
                changed = True
                print(f"  [SETUP] Plan fixed: {email} → {plan}")
            if changed:
                _save_users(users)
    print(f"  [SETUP] ✅ Done.")

def _get_auth_user():
    from auth import get_user_from_token
    auth = request.headers.get("Authorization", "")
    token = auth.replace("Bearer ", "") if auth.startswith("Bearer ") else ""
    return get_user_from_token(token)

# In-memory cache of parsed predictions JSON, keyed by (path, mtime).
# Avoids re-parsing a 2+ MB file on every API request.
_PREDICTIONS_MEMO = {"key": None, "preds": [], "data": {}}
_PREDICTIONS_LOCK = threading.Lock()

def _cached_predictions():
    """Load cached predictions. Returns (predictions_list, full_data_dict) or ([], {}).
    Caches parsed JSON in process memory, invalidated by file mtime."""
    files = sorted(OUTPUT_DIR.glob("gw*_predictions.json"), reverse=True)
    if not files:
        return [], {}
    p = files[0]
    try: mtime = p.stat().st_mtime
    except Exception: mtime = 0
    key = (str(p), mtime)
    with _PREDICTIONS_LOCK:
        if _PREDICTIONS_MEMO.get("key") == key:
            return _PREDICTIONS_MEMO["preds"], _PREDICTIONS_MEMO["data"]
    data = json.loads(p.read_text(encoding="utf-8"))
    preds = data.get("predictions", [])
    with _PREDICTIONS_LOCK:
        _PREDICTIONS_MEMO["key"] = key
        _PREDICTIONS_MEMO["preds"] = preds
        _PREDICTIONS_MEMO["data"] = data
    return preds, data


# ── Middleware ──

# Endpoints safe to cache privately in the browser for a short time.
_BROWSER_CACHE_API = {
    "/api/predictions": 60,
    "/api/fixture-ticker": 120,
    "/api/fixture-rankings": 120,
    "/api/top-transfers": 300,
    "/api/season-chips": 60,
}

def _maybe_gzip(resp):
    try:
        if resp.direct_passthrough: return resp
        ae = (request.headers.get("Accept-Encoding") or "").lower()
        if "gzip" not in ae: return resp
        if resp.status_code < 200 or resp.status_code >= 300: return resp
        if resp.headers.get("Content-Encoding"): return resp
        ct = (resp.content_type or "").lower()
        if not ("json" in ct or "javascript" in ct or "text/" in ct): return resp
        data = resp.get_data()
        if len(data) < 1024: return resp
        import gzip as _gz
        gz = _gz.compress(data, compresslevel=5)
        resp.set_data(gz)
        resp.headers["Content-Encoding"] = "gzip"
        resp.headers["Content-Length"] = str(len(gz))
        resp.headers["Vary"] = "Accept-Encoding"
    except Exception:
        pass
    return resp

@app.after_request
def after_request(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    path = request.path
    if path.startswith("/api/"):
        max_age = _BROWSER_CACHE_API.get(path)
        if max_age and request.method == "GET":
            resp.headers["Cache-Control"] = f"private, max-age={max_age}, stale-while-revalidate=120"
            resp.headers.pop("Pragma", None); resp.headers.pop("Expires", None)
        else:
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            resp.headers["Pragma"] = "no-cache"
            resp.headers["Expires"] = "0"
    elif path == "/" or path.endswith(".html"):
        resp.headers["Cache-Control"] = "public, max-age=300, must-revalidate"
    elif any(path.endswith(ext) for ext in (".css", ".js", ".png", ".jpg", ".ico", ".svg", ".woff2")):
        resp.headers["Cache-Control"] = "public, max-age=86400, immutable"
    else:
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return _maybe_gzip(resp)

@app.before_request
def before_request():
    # ── CORS Preflight: handle OPTIONS immediately ──
    # Browsers send OPTIONS before POST with Content-Type: application/json.
    # Without this, Flask returns 405 and the browser blocks the actual request
    # with "fail to fetch" — this was breaking login on ALL browsers except the
    # one that had a cached token in localStorage.
    if request.method == "OPTIONS":
        print(f"  [CORS] OPTIONS preflight: {request.path} from {request.headers.get('Origin', 'no-origin')}")
        resp = app.make_default_options_response()
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        resp.headers["Access-Control-Max-Age"] = "86400"  # Cache preflight for 24h
        return resp

    # Lazy-start refresh thread on first request (safe for gunicorn workers)
    _ensure_refresh_thread()
    # Auto-setup accounts on first request
    try: _auto_setup_accounts()
    except: pass


# ── Static files (explicit, no Flask static_folder magic) ──

@app.route("/")
def index():
    return send_from_directory(str(BASE_DIR), "dashboard.html")

@app.route("/<path:filename>")
def serve_static(filename):
    # Security: block access to sensitive files/directories
    blocked = ("data/", "cache/", ".git", "__pycache__", ".env", "sessions.json", "users.json")
    if any(filename.startswith(b) or filename.endswith(b) for b in blocked):
        return jsonify({"error": "Not found"}), 404
    if filename.endswith(".py") or filename.endswith(".pyc"):
        return jsonify({"error": "Not found"}), 404
    return send_from_directory(str(BASE_DIR), filename)


# ── Auth ──

@app.route("/api/auth/login", methods=["POST"])
@limiter.limit("10 per minute")
def api_auth_login():
    from auth import login
    data = request.get_json(silent=True) or {}
    email = data.get("email", "?")
    print(f"  [AUTH] Login: {email}")
    result = login(data.get("email", ""), data.get("password", ""))
    code = 200 if result.get("ok") else 401
    print(f"  [AUTH] Result: ok={result.get('ok', False)} code={code} error={result.get('error', 'none')}")
    return jsonify(result), code

@app.route("/api/auth/register", methods=["POST"])
@limiter.limit("5 per minute")
def api_auth_register():
    from auth import register
    data = request.get_json(silent=True) or {}
    result = register(data.get("email", ""), data.get("password", ""), data.get("name", ""))
    return jsonify(result), 200 if result.get("ok") else 400

@app.route("/api/auth/me", methods=["POST"])
def api_auth_me():
    user = _get_auth_user()
    if user:
        return jsonify({"ok": True, "user": user})
    return jsonify({"error": "Not authenticated"}), 401


# ── A1-lite auth endpoints (email verification, password reset, OAuth callback) ──
# _ALITE_SERVER_PATCH_

@app.route("/api/auth/forgot-password", methods=["POST"])
@limiter.limit("3 per minute")
def api_auth_forgot_password():
    """Send a password reset email. Always returns ok (no account enumeration)."""
    from auth import request_password_reset
    data = request.get_json(silent=True) or {}
    result = request_password_reset(data.get("email", ""))
    return jsonify(result), 200


@app.route("/api/auth/reset-password", methods=["POST"])
@limiter.limit("5 per minute")
def api_auth_reset_password():
    """Set a new password using a valid reset token."""
    from auth import reset_password_with_token
    data = request.get_json(silent=True) or {}
    result = reset_password_with_token(
        data.get("token", ""),
        data.get("password", ""),
    )
    return jsonify(result), 200 if result.get("ok") else 400


@app.route("/api/auth/verify-email", methods=["POST"])
@limiter.limit("10 per minute")
def api_auth_verify_email():
    """Mark the user's email as verified given a valid token."""
    from auth import verify_email_with_token
    data = request.get_json(silent=True) or {}
    result = verify_email_with_token(data.get("token", ""))
    return jsonify(result), 200 if result.get("ok") else 400


@app.route("/api/auth/resend-verification", methods=["POST"])
@limiter.limit("3 per minute")
def api_auth_resend_verification():
    from auth import resend_verification_email
    data = request.get_json(silent=True) or {}
    result = resend_verification_email(data.get("email", ""))
    return jsonify(result), 200


@app.route("/verify-email")
def page_verify_email():
    """Landing page the user hits after clicking the verification link in their email.
    The token is in the query string; we hand it to the dashboard which calls
    /api/auth/verify-email via JS to complete the flow."""
    return send_from_directory(str(BASE_DIR), "dashboard.html")


@app.route("/reset-password")
def page_reset_password():
    """Landing page for password reset. Dashboard.html reads ?token=... from URL."""
    return send_from_directory(str(BASE_DIR), "dashboard.html")


# ── Google OAuth (optional — disabled unless GOOGLE_OAUTH_ENABLED=true) ──
# This is a STUB wired to Supabase Auth's Google provider. To enable:
#   1. In Supabase Dashboard → Authentication → Providers → enable Google
#      and fill in your Google Client ID + Secret.
#   2. In Google Cloud Console → OAuth consent screen + credentials.
#   3. Add your site URL to "Authorized redirect URIs":
#        https://<your-domain>/api/auth/google/callback
#   4. Set env var GOOGLE_OAUTH_ENABLED=true on Render.
#   5. The frontend "Sign in with Google" button calls /api/auth/google/login
#      which bounces to Supabase, which bounces to Google, which bounces back here.

@app.route("/api/auth/google/login")
def api_auth_google_login():
    """Kick off Google OAuth flow via Supabase Auth."""
    if os.environ.get("GOOGLE_OAUTH_ENABLED", "").lower() not in ("1", "true", "yes"):
        return jsonify({"error": "Google sign-in is not enabled on this deployment."}), 503
    sb_url = os.environ.get("SUPABASE_URL", "").strip()
    if not sb_url:
        return jsonify({"error": "Supabase not configured"}), 503
    # Supabase hosted authorization endpoint — redirects to Google, then back to our callback
    redirect_to = request.host_url.rstrip("/") + "/api/auth/google/callback"
    return redirect(f"{sb_url}/auth/v1/authorize?provider=google&redirect_to={redirect_to}")


@app.route("/api/auth/google/callback")
def api_auth_google_callback():
    """Receive the authenticated identity from Supabase and mint our own session.
    Supabase returns the user info via URL fragment, so this page runs a tiny JS
    snippet that posts the access_token back to /api/auth/google/exchange."""
    return (
        "<!doctype html><meta charset=utf-8><title>Signing in…</title>"
        "<body style=\"font-family:sans-serif;background:#0f1222;color:#e7e9ef;"
        "display:flex;align-items:center;justify-content:center;height:100vh;\">"
        "<div>Finishing sign-in…</div>"
        "<script>(async()=>{"
        "const h=new URLSearchParams(location.hash.slice(1));"
        "const at=h.get('access_token');"
        "if(!at){document.body.innerText='Missing access_token';return;}"
        "const r=await fetch('/api/auth/google/exchange',{method:'POST',"
        "headers:{'Content-Type':'application/json'},"
        "body:JSON.stringify({access_token:at})});"
        "const d=await r.json();"
        "if(d.ok){localStorage.setItem('fpl_auth_token',d.token);location.href='/';}"
        "else{document.body.innerText=d.error||'Sign-in failed';}"
        "})();</script></body>"
    )


@app.route("/api/auth/google/exchange", methods=["POST"])
@limiter.limit("10 per minute")
def api_auth_google_exchange():
    """Verify the Supabase access_token with Supabase, then create our session."""
    if os.environ.get("GOOGLE_OAUTH_ENABLED", "").lower() not in ("1", "true", "yes"):
        return jsonify({"error": "Google sign-in is not enabled."}), 503
    data = request.get_json(silent=True) or {}
    at = (data.get("access_token") or "").strip()
    if not at:
        return jsonify({"error": "Missing access_token"}), 400
    sb_url = os.environ.get("SUPABASE_URL", "").strip()
    sb_key = os.environ.get("SUPABASE_KEY", "").strip()
    if not (sb_url and sb_key):
        return jsonify({"error": "Supabase not configured"}), 503
    try:
        import urllib.request, json as _json
        req = urllib.request.Request(
            f"{sb_url}/auth/v1/user",
            headers={"apikey": sb_key, "Authorization": f"Bearer {at}"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            profile = _json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"  [AUTH] Google exchange failed: {e}")
        return jsonify({"error": "Could not verify identity with Supabase"}), 401
    email = (profile.get("email") or "").strip().lower()
    name = (profile.get("user_metadata") or {}).get("full_name") or ""
    if not email:
        return jsonify({"error": "Provider did not return an email"}), 400
    from auth import upsert_oauth_user
    return jsonify(upsert_oauth_user(email, name=name, provider="google")), 200



# ── Predictions ──

# --- FILTER_USED_CHIPS_START ---
# Strip chips the user has already used (in the current half of the season) from
# chip_analysis. FPL 25/26: 4 chips per half (GW1-19 / GW20-38).
_HALF_CUTOFF_CHIPS = 20
_CHIP_NAME_MAP = {"bboost": "BB", "3xc": "TC", "freehit": "FH", "wildcard": "WC"}

def _user_used_chips_in_current_half():
    """Returns set of chip codes the user has used in the current half, or empty set."""
    try:
        settings = _load_settings()
        team_id = settings.get("team_id")
        if not team_id: return set()
        from my_team import fetch_my_team
        from data_fetcher import get_current_gameweek
        td = fetch_my_team(int(team_id))
        if td.get("error"): return set()
        try: cgw = get_current_gameweek()
        except Exception: cgw = (td.get("gw_summary", {}).get("event", 1) or 1) + 1
        current_half = 2 if cgw >= _HALF_CUTOFF_CHIPS else 1
        used = set()
        for c in (td.get("chips", []) or []):
            event = c.get("event", 0) or 0
            half = 2 if event >= _HALF_CUTOFF_CHIPS else 1
            if half != current_half: continue
            code = _CHIP_NAME_MAP.get(c.get("name", ""), str(c.get("name", "")).upper())
            used.add(code)
        return used
    except Exception:
        return set()

def _filter_chip_analysis(chip_analysis, used_codes):
    """Return NEW chip_analysis with used chips removed. Does not mutate input."""
    if not used_codes or not isinstance(chip_analysis, dict):
        return chip_analysis
    import copy
    ca = copy.deepcopy(chip_analysis)
    recs = ca.get("recommendations", []) or []
    ca["recommendations"] = [r for r in recs if r.get("code") not in used_codes]
    best = ca.get("best_chip")
    if best and best.get("code") in used_codes:
        ca["best_chip"] = ca["recommendations"][0] if ca["recommendations"] else None
    ca["user_used_chips"] = sorted(list(used_codes))
    return ca
# --- FILTER_USED_CHIPS_END ---

@app.route("/api/predictions")
def api_predictions():
    preds, data = _cached_predictions()
    if not data:
        return jsonify({"error": "No predictions yet. Please wait for data refresh."}), 404

    # Filter chips already used this half so UI never recommends an unusable chip.
    # IMPORTANT: do NOT mutate the memoized dict -- shallow-copy then swap chip_analysis.
    used_codes = _user_used_chips_in_current_half()
    if used_codes and data.get("chip_analysis"):
        data = dict(data)
        data["chip_analysis"] = _filter_chip_analysis(data.get("chip_analysis"), used_codes)

    user = _get_auth_user()
    is_premium = user and user.get("plan") in ("premium", "admin")

    if not is_premium:
        import random
        data["user_plan"] = "free" if user else "guest"
        # Shuffle predictions for free users so the sort order doesn't reveal xPts ranking.
        # (xPts is locked with 🔒 — leaving them sorted would leak the ranking)
        random.shuffle(preds)
        data["predictions"] = preds
        for p in preds:
            # Lock premium numeric fields
            for k in ["predicted_points", "raw_xpts", "confidence",
                      "team_last5_wr", "team_season_wr", "team_momentum", "team_injury_penalty"]:
                p[k] = "🔒"
            # Lock starter tier but keep the key so frontend can render the lock
            if isinstance(p.get("starter_quality"), dict):
                p["starter_quality"] = {"tier": "🔒"}
            # Keep fixtures but lock win_probability within
            for f in (p.get("fixtures") or []):
                if isinstance(f, dict):
                    f["win_probability"] = "🔒"
                    f["xp_single"] = "🔒"
                    f["xp_adjusted"] = "🔒"
            # Drop raw factors (advanced data)
            p.pop("factors", None)
        # Squad data — keep structure but lock xPts & confidence, keep fixtures
        sq = data.get("squad", {})
        for p in sq.get("starting_xi", []) + sq.get("bench", []):
            p["predicted_points"] = "🔒"
            p["raw_xpts"] = "🔒" if "raw_xpts" in p else p.get("raw_xpts")
            p["confidence"] = "🔒"
            if isinstance(p.get("starter_quality"), dict):
                p["starter_quality"] = {"tier": "🔒"}
            for f in (p.get("fixtures") or []):
                if isinstance(f, dict):
                    f["win_probability"] = "🔒"
                    f["xp_single"] = "🔒"
                    f["xp_adjusted"] = "🔒"
        sq["predicted_total_points"] = "🔒"
        sq["squad_total_xpts"] = "🔒"
        # Lock captain info — free users shouldn't see the captain pick
        if sq.get("captain"):
            sq["captain"] = {"name": "🔒 Upgrade to see", "predicted_points": "🔒", "player_id": None}
        if sq.get("vice_captain"):
            sq["vice_captain"] = {"name": "🔒", "predicted_points": "🔒", "player_id": None}
        # Lock chip analysis — free users shouldn't see best chip recommendation
        chip = data.get("chip_analysis", {})
        if chip.get("best_chip"):
            chip["best_chip"] = {"code": "🔒", "name": "Premium only", "score": "🔒"}
        for rec in chip.get("recommendations", []):
            rec["score"] = "🔒"; rec["code"] = "🔒"
        for key in ("top_picks", "differentials", "value_picks"):
            for p in data.get(key, []):
                p["predicted_points"] = "🔒"
                p["raw_xpts"] = "🔒"
    else:
        data["user_plan"] = user.get("plan", "premium")  # 'premium' or 'admin'

    return jsonify(data)

@app.route("/api/run")
@limiter.limit("3 per minute")
def api_run():
    # Protect heavy prediction regeneration — require admin auth
    user = _get_auth_user()
    if not user or user.get("plan") != "admin":
        return jsonify({"error": "Admin access required to trigger prediction run"}), 403
    try:
        gw = request.args.get("gw", 0, type=int) or None
        return jsonify(_run_predictions(gw))
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": "Internal server error"}), 500

@app.route("/api/refresh")
@limiter.limit("5 per minute")
def api_refresh():
    user = _get_auth_user()
    if not user or user.get("plan") != "admin":
        return jsonify({"error": "Admin access required"}), 403
    threading.Thread(target=_refresh_data, daemon=True).start()
    return jsonify({"ok": True, "message": "Refresh started"})

@app.route("/api/refresh-status")
def api_refresh_status():
    return jsonify({
        "last_refresh": datetime.fromtimestamp(_last_refresh).isoformat() if _last_refresh else None,
        "seconds_ago": int(time.time() - _last_refresh) if _last_refresh else None,
        "interval_hours": REFRESH_INTERVAL / 3600,
        "next_refresh_in": max(0, REFRESH_INTERVAL - (time.time() - _last_refresh)) if _last_refresh else 0,
    })


# ── Settings ──

@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "GET":
        return jsonify(_load_settings())
    # POST requires authentication
    user = _get_auth_user()
    if not user:
        return jsonify({"error": "Not authenticated"}), 401
    data = request.get_json(silent=True) or {}
    settings = _load_settings()
    settings.update(data)
    _save_settings(settings)
    return jsonify({"ok": True, "settings": settings})


# ── Chat ──

@app.route("/api/chat", methods=["POST"])
@limiter.limit("20 per minute")
def api_chat():
    try:
        data = request.get_json(silent=True) or {}
        question = data.get("question", "").strip()
        if not question:
            return jsonify({"error": "No question provided"}), 400
        from ai_chat import FPLChatEngine
        _, cached = _cached_predictions()
        if not cached: cached = _run_predictions()
        chat = FPLChatEngine(
            predictions=cached.get("predictions", []), squad=cached.get("squad", {}),
            gw_info=cached.get("gw_info", {}), chip_analysis=cached.get("chip_analysis", {}),
            bb_squad=cached.get("bb_squad", {}),
        )
        return jsonify(chat.answer(question))
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"answer": "Sorry, something went wrong.", "suggestions": ["Who should I captain?"]}), 200


# ── My Team ──

@app.route("/api/my-team")
def api_my_team():
    team_id = request.args.get("id") or _load_settings().get("team_id")
    if not team_id:
        return jsonify({"error": "No team ID"}), 400
    try:
        team_id = int(team_id)
        from my_team import fetch_my_team, enrich_my_team, generate_transfer_suggestions
        settings = _load_settings()
        settings["team_id"] = team_id
        _save_settings(settings)
        team_data = fetch_my_team(team_id)
        if team_data.get("error"):
            return jsonify(team_data), 400
        preds, _ = _cached_predictions()
        if not preds:
            return jsonify({"error": "Predictions not ready yet. Please wait for data refresh."}), 503
        player_map = {p["player_id"]: p for p in preds if "player_id" in p}
        enriched = enrich_my_team(team_data, player_map, preds)
        suggestions = generate_transfer_suggestions(enriched, preds)
        return jsonify({
            "team_id": team_id, "info": enriched.get("info", {}),
            "gw_summary": enriched.get("gw_summary", {}),
            "starters": enriched.get("starters", []), "bench": enriched.get("bench", []),
            "squad_value": enriched.get("squad_value", 0),
            "predicted_points": enriched.get("predicted_points", 0),
            "weakest_links": enriched.get("weakest_links", []),
            "transfer_suggestions": suggestions,
            "chips_used": enriched.get("chips", []),
            "active_chip": enriched.get("active_chip"),
            "recent_transfers": enriched.get("transfers", [])[:10],
            "history": enriched.get("history", [])[-10:],
        })
    except ValueError:
        return jsonify({"error": "Invalid team ID"}), 400
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": "Internal server error"}), 500

@app.route("/api/news")
def api_news():
    try:
        from news_aggregator import NewsAggregator
        return jsonify(NewsAggregator().get_news_summary())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/transfers")
def api_transfers():
    team_id = request.args.get("id") or _load_settings().get("team_id")
    if not team_id:
        return jsonify({"error": "No team ID"}), 400
    try:
        from my_team import fetch_my_team, enrich_my_team, generate_transfer_suggestions
        team_data = fetch_my_team(int(team_id))
        preds, _ = _cached_predictions()
        if not preds: return jsonify({"error": "Predictions not ready"}), 503
        player_map = {p["player_id"]: p for p in preds if "player_id" in p}
        enriched = enrich_my_team(team_data, player_map, preds)
        suggestions = generate_transfer_suggestions(enriched, preds, free_transfers=2)
        return jsonify({"team_id": int(team_id), "suggestions": suggestions,
                        "bank": enriched.get("gw_summary", {}).get("bank", 0),
                        "squad_value": enriched.get("squad_value", 0)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/files")
def api_files():
    files = sorted(OUTPUT_DIR.glob("gw*_predictions.json"), reverse=True)
    return jsonify([{"name": f.name, "path": f"/output/{f.name}"} for f in files])


# ── Heavy endpoints (use cached data where possible) ──

@app.route("/api/chip-analysis")
@limiter.limit("10 per minute")
def api_chip_analysis():
    try:
        preds, cached = _cached_predictions()
        if not cached:
            return jsonify({"error": "Predictions not ready"}), 503
        from squad_optimizer import SquadOptimizer, ChipAdvisor
        gw_info = cached.get("gw_info", {})
        chip_advisor = ChipAdvisor(preds, gw_info)
        analysis = chip_advisor.analyze()
        # FILTER_CHIP_ANALYSIS_ENDPOINT_DONE: strip user-exhausted chips
        used_codes = _user_used_chips_in_current_half()
        if used_codes:
            analysis = _filter_chip_analysis(analysis, used_codes)
        optimizer = SquadOptimizer(preds)
        normal = optimizer.optimize_squad()
        bb = optimizer.optimize_squad(chip="bench_boost")
        tc = optimizer.optimize_squad(chip="triple_captain")
        return jsonify({
            "gw_info": gw_info, "chip_analysis": analysis,
            "squad_comparison": {
                "normal": {"predicted_total": normal["predicted_total_points"], "formation": normal["formation"],
                           "captain": normal["captain"]["name"] if normal["captain"] else None},
                "bench_boost": {"predicted_total": bb["predicted_total_points"],
                                "bench_xp": sum(p["predicted_points"] for p in bb["bench"]),
                                "extra_points": round(bb["predicted_total_points"] - normal["predicted_total_points"], 1)},
                "triple_captain": {"captain": tc["captain"]["name"] if tc["captain"] else None,
                                   "captain_xp": tc["captain"]["predicted_points"] if tc["captain"] else 0,
                                   "extra_points": round((tc["captain"]["predicted_points"] if tc["captain"] else 0), 1)},
            },
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": "Internal server error"}), 500

@app.route("/api/gw-planner")
@limiter.limit("5 per minute")
def api_gw_planner():
    team_id = request.args.get("id") or _load_settings().get("team_id")
    if not team_id:
        return jsonify({"error": "No team ID"}), 400
    try:
        from gw_planner import GWPlanner
        planner = GWPlanner(horizon=request.args.get("horizon", 5, type=int))
        plan = planner.plan_from_team_id(int(team_id), horizon=planner.horizon)
        return jsonify(plan) if not plan.get("error") else (jsonify(plan), 400)
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": "Internal server error"}), 500

@app.route("/api/fixture-ticker")
@cached_response(ttl_seconds=120, key_prefix="fixture-ticker")
def api_fixture_ticker():
    try:
        horizon = request.args.get("horizon", 6, type=int)
        horizon = max(3, min(horizon, 15))  # Clamp between 3 and 15
        from gw_planner import GWPlanner
        p = GWPlanner(horizon=horizon)
        return jsonify({"from_gw": p.next_gw, "to_gw": p.next_gw + p.horizon - 1, "teams": p.build_fixture_ticker()})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": "Internal server error"}), 500

@app.route("/api/top-transfers")
@cached_response(ttl_seconds=300, key_prefix="top-transfers")
def api_top_transfers():
    """Top 15 transfers in/out this GW from FPL API bootstrap (free for all users)."""
    try:
        from data_fetcher import fetch_bootstrap, get_current_gameweek
        bootstrap = fetch_bootstrap()
        teams = {t["id"]: t for t in bootstrap.get("teams", [])}
        pos_map = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
        current_gw = get_current_gameweek(bootstrap)

        def build_row(p):
            return {
                "id": p["id"],
                "name": p.get("web_name", "Unknown"),
                "team": teams.get(p.get("team"), {}).get("short_name", "???"),
                "team_name": teams.get(p.get("team"), {}).get("name", "Unknown"),
                "position": pos_map.get(p.get("element_type"), "?"),
                "price": round(p.get("now_cost", 0) / 10, 1),
                "price_change": p.get("cost_change_event", 0),  # × 0.1
                "selected_by_percent": float(p.get("selected_by_percent", 0)),
                "transfers_in_event": int(p.get("transfers_in_event", 0)),
                "transfers_out_event": int(p.get("transfers_out_event", 0)),
                "net_transfers": int(p.get("transfers_in_event", 0)) - int(p.get("transfers_out_event", 0)),
                "form": float(p.get("form", 0)),
                "total_points": int(p.get("total_points", 0)),
                "news": p.get("news", ""),
                "status": p.get("status", "a"),
            }

        rows = [build_row(p) for p in bootstrap.get("elements", [])]

        top_in = sorted(rows, key=lambda x: x["transfers_in_event"], reverse=True)[:15]
        top_out = sorted(rows, key=lambda x: x["transfers_out_event"], reverse=True)[:15]
        top_net_in = sorted(rows, key=lambda x: x["net_transfers"], reverse=True)[:15]
        top_net_out = sorted(rows, key=lambda x: x["net_transfers"])[:15]
        price_risers = sorted([r for r in rows if r["price_change"] > 0],
                              key=lambda x: x["price_change"], reverse=True)[:10]
        price_fallers = sorted([r for r in rows if r["price_change"] < 0],
                               key=lambda x: x["price_change"])[:10]

        return jsonify({
            "current_gw": current_gw,
            "top_transfers_in": top_in,
            "top_transfers_out": top_out,
            "top_net_in": top_net_in,
            "top_net_out": top_net_out,
            "price_risers": price_risers,
            "price_fallers": price_fallers,
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": "Internal server error"}), 500

@app.route("/api/fixture-rankings")
@cached_response(ttl_seconds=120, key_prefix="fixture-rankings")
def api_fixture_rankings():
    try:
        n = request.args.get("gws", 5, type=int)
        from gw_planner import GWPlanner
        p = GWPlanner(horizon=n)
        return jsonify({"from_gw": p.next_gw, "num_gws": n, "rankings": p.rank_teams_by_fixtures(num_gws=n)})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": "Internal server error"}), 500

@app.route("/api/search-players")
def api_search_players():
    try:
        preds, _ = _cached_predictions()
        if not preds: return jsonify({"players": []})
        q = (request.args.get("q", "") or "").lower().strip()
        pos = request.args.get("pos")
        mp = request.args.get("max_price", type=float)
        results = []
        for p in preds:
            if q and not (q in p.get("name","").lower() or q in p.get("full_name","").lower() or
                          q in p.get("team","").lower() or q in p.get("team_name","").lower()): continue
            if pos and p.get("position") != pos: continue
            if mp and p.get("price", 99) > mp: continue
            if p.get("predicted_points", 0) <= 0 and p.get("minutes", 0) == 0: continue
            results.append({k: p.get(k) for k in ["player_id","name","full_name","team","position","price",
                            "predicted_points","raw_xpts","form","is_dgw","num_fixtures","fixtures",
                            "starter_quality","availability","selected_by_percent","news","team_last5_form","team_season_wr"]})
            if len(results) >= 50: break
        return jsonify({"players": results, "total": len(results)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/squad-predictions")
def api_squad_predictions():
    try:
        gw = request.args.get("gw", 0, type=int)
        ids_str = request.args.get("ids", "")
        if not gw or not ids_str:
            return jsonify({"error": "Need ?gw=X&ids=1,2,3"}), 400
        player_ids = [int(x) for x in ids_str.split(",") if x.strip()]
        # Use cached if same GW, otherwise run fresh
        preds, cached = _cached_predictions()
        if cached and cached.get("gameweek") == gw:
            pred_map = {p["player_id"]: p for p in preds}
        else:
            from prediction_engine import PredictionEngine
            pred_map = {p["player_id"]: p for p in PredictionEngine().predict_all(gw)}
        results = []
        for pid in player_ids:
            pred = pred_map.get(pid)
            if pred:
                results.append({k: pred.get(k) for k in ["player_id","name","team","position","position_id","price",
                                "predicted_points","raw_xpts","form","is_dgw","num_fixtures","fixtures",
                                "starter_quality","availability","news","team_last5_form"]})
            else:
                results.append({"player_id": pid, "name": "?", "predicted_points": 0, "fixtures": []})
        return jsonify({"gameweek": gw, "predictions": results})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": "Internal server error"}), 500

@app.route("/api/simulate-transfer", methods=["POST"])
def api_simulate_transfer():
    try:
        data = request.get_json(silent=True) or {}
        squad_ids = data.get("squad_ids", [])
        out_id, in_id, target_gw = data.get("out_id"), data.get("in_id"), data.get("gw")
        if not squad_ids or not out_id or not in_id:
            return jsonify({"error": "Missing squad_ids, out_id, or in_id"}), 400
        preds, _ = _cached_predictions()
        if preds:
            pred_map = {p["player_id"]: p for p in preds}
        else:
            from prediction_engine import PredictionEngine
            pred_map = {p["player_id"]: p for p in PredictionEngine().predict_all(target_gw)}
        out_p, in_p = pred_map.get(out_id, {}), pred_map.get(in_id, {})
        cur = sorted([pred_map.get(pid, {}) for pid in squad_ids], key=lambda x: x.get("predicted_points",0), reverse=True)[:11]
        cur_xpts = sum(p.get("predicted_points",0) for p in cur)
        new_ids = [pid for pid in squad_ids if pid != out_id] + [in_id]
        new = sorted([pred_map.get(pid, {}) for pid in new_ids], key=lambda x: x.get("predicted_points",0), reverse=True)[:11]
        new_xpts = sum(p.get("predicted_points",0) for p in new)
        from prediction_engine import PredictionEngine
        engine = PredictionEngine()
        gw = target_gw or engine.next_gw
        multi_gw = []
        for fgw in range(gw, min(gw+4, 39)):
            if fgw == gw:
                inf, outf = in_p.get("predicted_points",0), out_p.get("predicted_points",0)
            else:
                inf = engine.predict_player(in_id, fgw).get("predicted_points",0)
                outf = engine.predict_player(out_id, fgw).get("predicted_points",0)
            multi_gw.append({"gw":fgw,"in_xpts":round(inf,2),"out_xpts":round(outf,2),"gain":round(inf-outf,2)})
        return jsonify({
            "gameweek": gw,
            "out_player": {"player_id":out_id,"name":out_p.get("name","?"),"team":out_p.get("team","?"),
                           "position":out_p.get("position","?"),"price":out_p.get("price",0),
                           "predicted_points":out_p.get("predicted_points",0),"fixtures":out_p.get("fixtures",[])},
            "in_player": {"player_id":in_id,"name":in_p.get("name","?"),"team":in_p.get("team","?"),
                          "position":in_p.get("position","?"),"price":in_p.get("price",0),
                          "predicted_points":in_p.get("predicted_points",0),"fixtures":in_p.get("fixtures",[]),
                          "is_dgw":in_p.get("is_dgw",False),"starter_quality":in_p.get("starter_quality",{}),"form":in_p.get("form",0)},
            "impact": {"this_gw_gain":round(in_p.get("predicted_points",0)-out_p.get("predicted_points",0),2),
                       "xi_xpts_before":round(cur_xpts,1),"xi_xpts_after":round(new_xpts,1),
                       "xi_gain":round(new_xpts-cur_xpts,1),
                       "price_delta":round(in_p.get("price",0)-out_p.get("price",0),1)},
            "multi_gw": multi_gw, "total_multi_gw_gain": round(sum(g["gain"] for g in multi_gw), 2),
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": "Internal server error"}), 500

@app.route("/api/season-chips")
def api_season_chips():
    try:
        from chip_planner import SeasonChipPlanner
        settings = _load_settings()
        squad_ids = None; chips_available = ["BB","TC","FH","WC"]; chips_used_list = []; bank = 0.0
        HALF_CUTOFF = 20; current_half = 2
        cmap = {"bboost":"BB","3xc":"TC","freehit":"FH","wildcard":"WC"}
        if settings.get("team_id"):
            try:
                from my_team import fetch_my_team
                from data_fetcher import get_current_gameweek
                td = fetch_my_team(settings["team_id"])
                if not td.get("error"):
                    squad_ids = [p.get("element") for p in td.get("picks",[])]
                    bank = td.get("gw_summary",{}).get("bank",0)
                    chips_used_list = td.get("chips",[])
                    # Use bootstrap authoritative current GW, NOT gw_summary.event
                    # (which is the last-completed GW and caused FH-at-GW33 to be skipped).
                    try: cgw = get_current_gameweek()
                    except: cgw = td.get("gw_summary",{}).get("event",1) + 1
                    current_half = 2 if cgw >= HALF_CUTOFF else 1
                    used = set()
                    # Any chip already activated in this half is permanently consumed.
                    # Do NOT skip chips just because they match cgw or active_chip.
                    for c in chips_used_list:
                        code = cmap.get(c.get("name",""), c.get("name","").upper())
                        h = 2 if c.get("event",0) >= HALF_CUTOFF else 1
                        if h == current_half: used.add(code)
                    chips_available = [c for c in ["BB","TC","FH","WC"] if c not in used]
            except: pass
        result = SeasonChipPlanner().analyze_season(chips_available=chips_available, current_squad_ids=squad_ids, bank=bank)
        result["user_chips_available"] = chips_available
        result["user_chips_used"] = [{"name":c.get("name"),"code":cmap.get(c.get("name",""),"?"),"gw":c.get("event"),
                                      "half":2 if c.get("event",0)>=HALF_CUTOFF else 1} for c in chips_used_list]
        result["current_half"] = current_half if settings.get("team_id") else 2
        result["half_cutoff"] = HALF_CUTOFF
        result["all_used"] = len(chips_available) == 0
        return jsonify(result)
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": "Internal server error"}), 500


# ── Stripe ──

@app.route("/api/stripe/create-checkout", methods=["POST"])
def api_stripe_checkout():
    user = _get_auth_user()
    if not user: return jsonify({"error": "Not authenticated"}), 401
    try:
        import stripe
        stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
        if not stripe.api_key:
            return jsonify({
                "ok": False,
                "error": "Payment system not configured. Please contact the admin to manually upgrade your account.",
                "contact_admin": True,
            })
        # Already premium? Don't create another checkout
        if user.get("plan") in ("premium", "admin"):
            return jsonify({"ok": False, "error": "You already have an active subscription."})
        s = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price_data":{"currency":"usd","product_data":{"name":"FPL Predictor Premium"},
                         "unit_amount":250,"recurring":{"interval":"month"}},"quantity":1}],
            mode="subscription",
            success_url=request.headers.get("Origin","")+"/?upgraded=1",
            cancel_url=request.headers.get("Origin","")+"/?cancelled=1",
            client_reference_id=user["email"], customer_email=user["email"],
        )
        return jsonify({"ok": True, "checkout_url": s.url})
    except Exception as e:
        print(f"  [STRIPE] Checkout error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/stripe/customer-portal", methods=["POST"])
def api_stripe_portal():
    """Redirect premium user to Stripe Customer Portal to manage/cancel subscription."""
    user = _get_auth_user()
    if not user: return jsonify({"error": "Not authenticated"}), 401
    try:
        import stripe
        stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
        if not stripe.api_key:
            return jsonify({"error": "Payment system not configured."}), 400
        # Need stripe_customer_id to create portal session
        from auth import _load_users
        users = _load_users()
        full_user = users.get(user["email"], {})
        customer_id = full_user.get("stripe_customer_id")
        if not customer_id:
            return jsonify({"error": "No billing account found. If you were upgraded by an admin, billing is managed manually."}), 400
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=request.headers.get("Origin", "") + "/",
        )
        return jsonify({"ok": True, "portal_url": session.url})
    except Exception as e:
        print(f"  [STRIPE] Portal error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/stripe/webhook", methods=["POST"])
@limiter.exempt
def api_stripe_webhook():
    try:
        import stripe
        stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
        ws = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
        if not ws: return jsonify({"error": "Not configured"}), 403
        event = stripe.Webhook.construct_event(
            request.get_data(), request.headers.get("Stripe-Signature", ""), ws
        )
        etype = event.get("type", "")
        obj = event["data"]["object"]
        print(f"  [STRIPE] Webhook: {etype}")

        if etype == "checkout.session.completed":
            # New subscription created — upgrade user
            email = obj.get("client_reference_id") or obj.get("customer_email")
            if email:
                from auth import upgrade_to_premium
                upgrade_to_premium(
                    email,
                    stripe_customer_id=obj.get("customer"),
                    stripe_subscription_id=obj.get("subscription"),
                )
                print(f"  [STRIPE] Upgraded: {email}")

        elif etype == "invoice.paid":
            # Subscription renewed successfully — extend premium
            customer_email = obj.get("customer_email")
            if customer_email:
                from auth import extend_premium
                extend_premium(customer_email, days=35)  # 35 days buffer for monthly
                print(f"  [STRIPE] Renewal extended: {customer_email}")

        elif etype == "invoice.payment_failed":
            # Payment failed — log warning (Stripe retries automatically)
            customer_email = obj.get("customer_email")
            attempt = obj.get("attempt_count", 0)
            print(f"  [STRIPE] Payment failed for {customer_email} (attempt {attempt})")
            # After 3+ failed attempts, Stripe will cancel the subscription
            # which triggers customer.subscription.deleted below

        elif etype == "customer.subscription.deleted":
            # Subscription cancelled or expired — downgrade to free
            customer_id = obj.get("customer")
            if customer_id:
                from auth import downgrade_to_free
                email = _find_email_by_stripe_customer(customer_id)
                if email:
                    downgrade_to_free(email)
                    print(f"  [STRIPE] Downgraded: {email}")

        elif etype == "customer.subscription.updated":
            # Subscription status changed (e.g., paused, past_due)
            customer_id = obj.get("customer")
            status = obj.get("status")
            print(f"  [STRIPE] Subscription updated: customer={customer_id} status={status}")
            if status in ("canceled", "unpaid", "past_due"):
                from auth import downgrade_to_free
                email = _find_email_by_stripe_customer(customer_id)
                if email:
                    downgrade_to_free(email)
                    print(f"  [STRIPE] Downgraded due to status={status}: {email}")

        return jsonify({"ok": True})
    except Exception as e:
        print(f"  [STRIPE] Webhook error: {e}")
        return jsonify({"error": str(e)}), 400


def _find_email_by_stripe_customer(customer_id: str) -> str:
    """Look up user email by Stripe customer ID."""
    from auth import _load_users
    users = _load_users()
    for email, u in users.items():
        if u.get("stripe_customer_id") == customer_id:
            return email
    return ""


# ── Admin ──

@app.route("/api/admin/users", methods=["POST"])
def api_admin_users():
    user = _get_auth_user()
    if not user or user.get("plan") != "admin": return jsonify({"error": "Admin access required"}), 403
    from auth import list_all_users
    return jsonify(list_all_users(user["email"]))

@app.route("/api/admin/set-plan", methods=["POST"])
def api_admin_set_plan():
    user = _get_auth_user()
    if not user or user.get("plan") != "admin": return jsonify({"error": "Admin access required"}), 403
    from auth import admin_set_plan
    d = request.get_json(silent=True) or {}
    return jsonify(admin_set_plan(user["email"], d.get("email",""), d.get("plan","free"), d.get("months",999)))

@app.route("/api/admin/delete-user", methods=["POST"])
def api_admin_delete_user():
    user = _get_auth_user()
    if not user or user.get("plan") != "admin": return jsonify({"error": "Admin access required"}), 403
    from auth import admin_delete_user
    d = request.get_json(silent=True) or {}
    return jsonify(admin_delete_user(user["email"], d.get("email","")))

@app.route("/api/admin/model-analysis", methods=["GET"])
def api_admin_model_analysis():
    """Admin: Get model performance analysis and weight suggestions."""
    user = _get_auth_user()
    if not user or user.get("plan") != "admin": return jsonify({"error": "Admin access required"}), 403
    from model_optimizer import suggest_weight_adjustments, find_available_prediction_gws
    from data_fetcher import get_current_gameweek
    
    # Auto-generate last-completed GW predictions if missing
    try:
        current_gw = get_current_gameweek()
        last_completed = current_gw - 1
        available = find_available_prediction_gws()
        if last_completed > 0 and last_completed not in available:
            print(f"  [ADMIN] Generating missing GW{last_completed} predictions for analysis...")
            _run_predictions(gw=last_completed)
    except Exception as e:
        print(f"  [ADMIN] Could not auto-generate predictions: {e}")
    
    return jsonify(suggest_weight_adjustments())

@app.route("/api/admin/apply-weights", methods=["POST"])
def api_admin_apply_weights():
    """Admin: Apply new weight configuration and regenerate predictions."""
    user = _get_auth_user()
    if not user or user.get("plan") != "admin": return jsonify({"error": "Admin access required"}), 403
    from model_optimizer import apply_weight_adjustments
    d = request.get_json(silent=True) or {}
    weights = d.get("weights", {})
    if not weights:
        return jsonify({"error": "No weights provided"}), 400
    success = apply_weight_adjustments(weights)
    if not success:
        return jsonify({"ok": False, "message": "Failed to persist weights (storage error)"})

    # Weights are already hot-swapped in memory by apply_weight_adjustments.
    # Kick off a threaded prediction regen so the new xPts appear ASAP.
    try:
        def _regen():
            try:
                _run_predictions()
                print("  [ADMIN] Predictions regenerated with new weights")
            except Exception as e:
                print(f"  [ADMIN] Regen failed: {e}")
        threading.Thread(target=_regen, daemon=True).start()
        return jsonify({
            "ok": True,
            "message": "Weights updated. Regenerating predictions in background — refresh the page in ~10 seconds to see new xPts.",
            "regenerating": True,
        })
    except Exception as e:
        return jsonify({
            "ok": True,
            "message": f"Weights saved but auto-regen failed: {e}. Restart server manually.",
            "regenerating": False,
        })

@app.route("/api/setup-accounts")
def api_setup_accounts():
    sk = os.environ.get("SETUP_KEY", "")
    if not sk or request.args.get("key","") != sk: return jsonify({"error": "Invalid key"}), 403
    _auto_setup_accounts()
    return jsonify({"ok": True})

@app.route("/api/reset-accounts")
def api_reset_accounts():
    sk = os.environ.get("SETUP_KEY", "")
    if not sk or request.args.get("key","") != sk: return jsonify({"error": "Invalid key"}), 403
    from auth import _save_users, _save_sessions
    _save_users({}); _save_sessions({})
    _auto_setup_accounts()
    return jsonify({"ok": True, "message": "Reset done"})


@app.route("/api/admin/reset-weights", methods=["POST"])
def api_admin_reset_weights():
    """Admin: Remove persisted weight override so the app uses config.py defaults
    again on next restart. The currently-running process keeps its in-memory
    weights until restart (safer - avoids surprise xPts jumps mid-session)."""
    user = _get_auth_user()
    if not user or user.get("plan") != "admin":
        return jsonify({"error": "Admin access required"}), 403
    from model_optimizer import reset_weights_to_defaults
    ok = reset_weights_to_defaults()
    return jsonify({
        "ok": ok,
        "message": "Saved weight override cleared. Defaults will apply after the next restart."
            if ok else "Failed to clear weight override."
    })


# ── Health & Monitoring ──

@app.route("/api/health")
@limiter.exempt
def api_health():
    """Health check for monitoring / load balancer. Fast, no auth required."""
    predictions_exist = any(OUTPUT_DIR.glob("gw*_predictions.json")) if OUTPUT_DIR.exists() else False
    cache_size = len(_response_cache)
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "predictions_available": predictions_exist,
        "last_refresh_seconds_ago": int(time.time() - _last_refresh) if _last_refresh else None,
        "response_cache_entries": cache_size,
        "pid": os.getpid(),
    })


# ── Entry point ──

if __name__ == "__main__":
    print(f"  [DEV] Starting Flask dev server on port {PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
