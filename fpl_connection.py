"""Per-app-user, encrypted, read-only connections to the private FPL API."""
import hashlib
import json
import os
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path

import requests

API_BASE = "https://fantasy.premierleague.com/api"
CONNECTION_DIR = Path(__file__).parent / "data" / "fpl_connections"
_lock = threading.RLock()
_client = None


class FPLConnectionError(Exception):
    def __init__(self, message, code="fpl_connection_error", status=503):
        super().__init__(message)
        self.code = code
        self.status = status


def _owner_key(user):
    email = str((user or {}).get("email", "")).strip().casefold()
    if not email:
        raise FPLConnectionError("Sign in to connect FPL.", "app_login_required", 401)
    identity = email + ":" + str(user.get("created_at", ""))
    return hashlib.sha256(("fpl-connection-v1:" + identity).encode()).hexdigest()


def _cipher():
    from cryptography.fernet import Fernet
    try:
        return Fernet(os.environ.get("FPL_CONNECTION_KEY", "").encode())
    except (ValueError, TypeError):
        raise FPLConnectionError(
            "FPL connections are not configured on this server.", "fpl_not_configured"
        ) from None


def _db():
    global _client
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_KEY", "").strip()
    if not url and not key:
        return None
    if not url or not key:
        raise FPLConnectionError("FPL connection storage is not configured.")
    if _client is None:
        from supabase import create_client
        _client = create_client(url, key)
    return _client


def _storage_error():
    return FPLConnectionError("FPL connection storage is unavailable. Please try again.")


def _read(owner):
    with _lock:
        try:
            db = _db()
            if db is not None:
                rows = db.table("fpl_connections").select("payload").eq("owner_key", owner).execute().data
                return rows[0]["payload"] if rows else None
            path = CONNECTION_DIR / (owner + ".json")
            return json.loads(path.read_text()) if path.exists() else None
        except FPLConnectionError:
            raise
        except Exception:
            # Do not print storage errors or credential-bearing records.
            raise _storage_error() from None


def _write(owner, payload):
    with _lock:
        try:
            db = _db()
            if db is not None:
                db.table("fpl_connections").upsert(
                    {"owner_key": owner, "payload": payload}, on_conflict="owner_key"
                ).execute()
                return
            CONNECTION_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = CONNECTION_DIR / (owner + ".json")
            temp = path.with_suffix("." + secrets.token_hex(8) + ".tmp")
            try:
                with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
                    json.dump(payload, handle)
                os.replace(temp, path)
            finally:
                temp.unlink(missing_ok=True)
        except FPLConnectionError:
            raise
        except Exception:
            raise _storage_error() from None


def disconnect(user):
    owner = _owner_key(user)
    with _lock:
        try:
            db = _db()
            if db is not None:
                db.table("fpl_connections").delete().eq("owner_key", owner).execute()
            else:
                (CONNECTION_DIR / (owner + ".json")).unlink(missing_ok=True)
        except FPLConnectionError:
            raise
        except Exception:
            raise _storage_error() from None


def _load(user):
    owner = _owner_key(user)
    record = _read(owner)
    if record is None:
        return None
    from cryptography.fernet import InvalidToken
    try:
        payload = json.loads(_cipher().decrypt(record["encrypted"].encode()))
        if payload["owner"] != owner:
            raise ValueError("Owner mismatch")
        return payload
    except FPLConnectionError:
        raise
    except (InvalidToken, KeyError, TypeError, ValueError):
        raise FPLConnectionError(
            "Reconnect your FPL account.", "fpl_reconnect_required", 409
        ) from None


def status(user):
    payload = _load(user)
    return {"connected": bool(payload), **({
        "team_id": payload["team_id"], "connected_at": payload["connected_at"]
    } if payload else {})}


def _token(value):
    if not isinstance(value, str):
        raise FPLConnectionError("Enter your FPL access token.", "invalid_fpl_token", 400)
    value = value.strip()
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    if not value or len(value) > 16384 or any(ch.isspace() for ch in value):
        raise FPLConnectionError("Enter a valid FPL access token.", "invalid_fpl_token", 400)
    return value


def _get(path, token):
    try:
        response = requests.get(
            API_BASE + path,
            headers={"X-Api-Authorization": "Bearer " + token,
                     "Accept": "application/json", "User-Agent": "FPL-GW-Planner/1.0"},
            timeout=(5, 15), allow_redirects=False,
        )
    except requests.RequestException:
        raise FPLConnectionError("FPL is unreachable. Please try again.", "fpl_unavailable", 502) from None
    if response.status_code in (401, 403):
        raise FPLConnectionError(
            "FPL access expired or was rejected. Reconnect using a fresh FPL token.",
            "fpl_reconnect_required", 409,
        )
    if response.status_code == 429:
        raise FPLConnectionError("FPL is busy. Please try again shortly.", "fpl_rate_limited", 429)
    if response.status_code != 200:
        raise FPLConnectionError("FPL could not return your team. Please try again.", "fpl_unavailable", 502)
    try:
        data = response.json()
    except ValueError:
        raise FPLConnectionError("FPL returned an invalid response.", "fpl_invalid_response", 502) from None
    if not isinstance(data, dict):
        raise FPLConnectionError("FPL returned an invalid response.", "fpl_invalid_response", 502)
    return data


def transfer_status(snapshot):
    """Read FPL's allowance; derive remaining solely from FPL's own made field.

    Never inspect public/early-transfer history or award next GW's transfer here.
    """
    transfers = snapshot.get("transfers", {})
    if not isinstance(transfers, dict):
        raise FPLConnectionError("FPL returned an invalid transfer status.", "fpl_invalid_response", 502)
    limit, made = transfers.get("limit"), transfers.get("made")
    if not isinstance(made, int) or isinstance(made, bool) or made < 0:
        raise FPLConnectionError("FPL returned an invalid transfer status.", "fpl_invalid_response", 502)
    if limit is None and "limit" in transfers:
        return {"allowance": None, "remaining": None, "unlimited": True, "made": made}
    if not isinstance(limit, int) or isinstance(limit, bool) or not 0 <= limit <= 5:
        raise FPLConnectionError("FPL returned an invalid transfer status.", "fpl_invalid_response", 502)
    return {"allowance": limit, "remaining": max(0, limit - made), "unlimited": False, "made": made}


def _snapshot(team_id, token):
    snapshot = _get(f"/my-team/{team_id}/", token)
    picks = snapshot.get("picks")
    if (not isinstance(picks, list) or len(picks) != 15
            or not all(isinstance(p, dict) and isinstance(p.get("element"), int)
                       and isinstance(p.get("position"), int) for p in picks)
            or {p["position"] for p in picks} != set(range(1, 16))
            or len({p["element"] for p in picks}) != 15):
        raise FPLConnectionError("FPL returned an incomplete squad.", "fpl_invalid_response", 502)
    transfer_status(snapshot)
    return snapshot


def connect(user, token):
    """Discover the team from /me; never trust a caller-supplied owner/team ID."""
    owner = _owner_key(user)
    cipher = _cipher()
    token = _token(token)
    identity = _get("/me/", token)
    player = identity.get("player")
    team_id = player.get("entry") if isinstance(player, dict) else None
    if not isinstance(team_id, int) or isinstance(team_id, bool) or team_id <= 0:
        raise FPLConnectionError("This FPL account has no team for the current season.", "fpl_no_team", 400)
    _snapshot(team_id, token)
    payload = {"owner": owner, "team_id": team_id, "access_token": token,
               "connected_at": datetime.now(timezone.utc).isoformat()}
    _write(owner, {"encrypted": cipher.encrypt(json.dumps(payload).encode()).decode()})
    return {"connected": True, "team_id": team_id, "connected_at": payload["connected_at"]}


def fetch_snapshot(user, team_id):
    payload = _load(user)
    if payload is None:
        return None
    # A connected user's credential is never used to fetch another entry.
    if int(payload["team_id"]) != int(team_id):
        return None
    return _snapshot(int(team_id), payload["access_token"])


def apply_snapshot(team_data, snapshot):
    """Overlay the live squad, prices, bank and transfers on public points/history."""
    balance = transfer_status(snapshot)
    transfers = snapshot["transfers"]
    team_data["picks"] = [dict(p) for p in snapshot["picks"]]
    team_data["free_transfers"] = balance["allowance"]
    team_data["free_transfers_remaining"] = balance["remaining"]
    team_data["transfers_unlimited"] = balance["unlimited"]
    team_data["free_transfers_source"] = "fpl_my_team"
    team_data["fpl_connected"] = True
    team_data["fpl_transfer_status"] = {key: transfers.get(key) for key in ("limit", "made", "cost", "status")}
    team_data["picks_are_planning_gw"] = True
    # FPL chips identify the current activation through status_for_entry.
    active = next((c.get("name") for c in snapshot.get("chips", [])
                   if c.get("status_for_entry") == "active"), None)
    team_data["active_chip"] = active
    summary = team_data.setdefault("gw_summary", {})
    summary["free_transfers"] = balance["allowance"]
    summary["free_transfers_remaining"] = balance["remaining"]
    summary["event_transfers"] = balance["made"]
    summary["event_transfers_cost"] = transfers.get("cost", 0)
    for field in ("bank", "value"):
        value = transfers.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            summary[field] = value / 10
    team_data["current_bank"] = summary.get("bank", 0)
    team_data["current_team_value"] = summary.get("value", 0)
    return team_data
