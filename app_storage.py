"""
FPL Predictor - Persistent Key-Value Storage
─────────────────────────────────────────────
Generic key-value store used for things that must survive server restarts
but don't fit in the users/sessions tables (e.g. admin-tuned model weights,
team_id setting, feature flags).

Backend selection mirrors auth.py:
  - If SUPABASE_URL and SUPABASE_KEY are set → use Supabase `app_settings` table
  - Otherwise fall back to local JSON file (data/app_settings.json)

Table schema (create once in Supabase SQL editor — see README.md):
    create table app_settings (
        key   text primary key,
        value jsonb not null,
        updated_at timestamptz default now()
    );
"""
import json
import os
import threading
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
SETTINGS_FILE = DATA_DIR / "app_settings.json"

_lock = threading.RLock()

_SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
_SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()
_USE_SUPABASE = bool(_SUPABASE_URL and _SUPABASE_KEY)
_sb_client = None
_backend_logged = False


def _get_sb():
    global _sb_client, _USE_SUPABASE, _backend_logged
    if not _USE_SUPABASE:
        if not _backend_logged:
            print(f"  [APP_STORAGE] Using JSON file backend ({SETTINGS_FILE})")
            _backend_logged = True
        return None
    if _sb_client is not None:
        return _sb_client
    try:
        from supabase import create_client
        _sb_client = create_client(_SUPABASE_URL, _SUPABASE_KEY)
        if not _backend_logged:
            print(f"  [APP_STORAGE] Using Supabase backend")
            _backend_logged = True
        return _sb_client
    except Exception as e:
        print(f"  [APP_STORAGE] Supabase init failed, using file fallback: {e}")
        _USE_SUPABASE = False
        return None


def _load_all_file():
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  [APP_STORAGE] Could not parse {SETTINGS_FILE}: {e}")
    return {}


def _save_all_file(data):
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(SETTINGS_FILE)


def get_setting(key, default=None):
    """Fetch a value by key. Returns `default` if not found."""
    with _lock:
        sb = _get_sb()
        if sb is not None:
            try:
                res = sb.table("app_settings").select("value").eq("key", key).execute()
                if res.data:
                    return res.data[0].get("value", default)
                return default
            except Exception as e:
                print(f"  [APP_STORAGE] get({key}) Supabase failed, falling back to file: {e}")
        return _load_all_file().get(key, default)


def set_setting(key, value):
    """Store a value by key. Returns True on success."""
    with _lock:
        sb = _get_sb()
        if sb is not None:
            try:
                sb.table("app_settings").upsert(
                    {"key": key, "value": value},
                    on_conflict="key",
                ).execute()
                return True
            except Exception as e:
                print(f"  [APP_STORAGE] set({key}) Supabase failed, writing to file: {e}")
        # File fallback (or emergency backup if Supabase failed)
        data = _load_all_file()
        data[key] = value
        try:
            _save_all_file(data)
            return True
        except Exception as e:
            print(f"  [APP_STORAGE] set({key}) file write failed: {e}")
            return False


def delete_setting(key):
    """Remove a key. Idempotent."""
    with _lock:
        sb = _get_sb()
        if sb is not None:
            try:
                sb.table("app_settings").delete().eq("key", key).execute()
                return True
            except Exception as e:
                print(f"  [APP_STORAGE] delete({key}) Supabase failed: {e}")
        data = _load_all_file()
        if key in data:
            del data[key]
            _save_all_file(data)
        return True
