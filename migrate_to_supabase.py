"""
One-shot migration script: copy users.json + sessions.json from local files to Supabase.

Usage:
    1. Set env vars SUPABASE_URL and SUPABASE_KEY (service_role key)
    2. Make sure the "users" and "sessions" tables exist in Supabase (see SQL below)
    3. Run:  python migrate_to_supabase.py

Required Supabase tables (run this SQL in Supabase SQL Editor first):

    create table if not exists public.users (
        email text primary key,
        data  jsonb not null,
        updated_at timestamptz default now()
    );

    create table if not exists public.sessions (
        token text primary key,
        data  jsonb not null,
        created_at timestamptz default now()
    );

    -- Optional: index for faster session cleanup
    create index if not exists sessions_created_at_idx
        on public.sessions ((data->>'created_at'));
"""
import os
import json
import sys
from pathlib import Path

URL = os.environ.get("SUPABASE_URL", "").strip()
KEY = os.environ.get("SUPABASE_KEY", "").strip()

if not URL or not KEY:
    print("ERROR: please set SUPABASE_URL and SUPABASE_KEY env vars first.")
    print("  Windows PowerShell:")
    print("    $env:SUPABASE_URL = \"https://xxxxx.supabase.co\"")
    print("    $env:SUPABASE_KEY = \"<service_role_key>\"")
    sys.exit(1)

try:
    from supabase import create_client
except ImportError:
    print("ERROR: supabase package not installed. Run:  pip install supabase")
    sys.exit(1)

sb = create_client(URL, KEY)
print(f"Connected to Supabase: {URL[:40]}...")

DATA_DIR = Path(__file__).parent / "data"
USERS_FILE = DATA_DIR / "users.json"
SESSIONS_FILE = DATA_DIR / "sessions.json"

# ── Migrate users ──
if USERS_FILE.exists():
    users = json.loads(USERS_FILE.read_text(encoding="utf-8"))
    print(f"Found {len(users)} user(s) in {USERS_FILE}")
    if users:
        rows = [{"email": e, "data": u} for e, u in users.items()]
        sb.table("users").upsert(rows, on_conflict="email").execute()
        print(f"  [OK] Uploaded {len(rows)} user(s) to Supabase.users")
else:
    print(f"No {USERS_FILE} file to migrate.")

# ── Migrate sessions ──
if SESSIONS_FILE.exists():
    sessions = json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
    print(f"Found {len(sessions)} session(s) in {SESSIONS_FILE}")
    if sessions:
        rows = [{"token": t, "data": d} for t, d in sessions.items()]
        # Batch upsert
        for i in range(0, len(rows), 500):
            sb.table("sessions").upsert(rows[i:i+500], on_conflict="token").execute()
        print(f"  [OK] Uploaded {len(rows)} session(s) to Supabase.sessions")
else:
    print(f"No {SESSIONS_FILE} file to migrate.")

# ── Verify ──
print()
print("=== Verification ===")
u_res = sb.table("users").select("email", count="exact").execute()
s_res = sb.table("sessions").select("token", count="exact").execute()
print(f"Supabase.users     total rows: {u_res.count}")
print(f"Supabase.sessions  total rows: {s_res.count}")
print()
print("Migration complete! You can now deploy with SUPABASE_URL and SUPABASE_KEY set on Render.")
