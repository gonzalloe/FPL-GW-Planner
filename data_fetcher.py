"""
FPL Predictor - Data Fetcher
Pulls all data from the official FPL API.
"""
import json
import time
import requests
from pathlib import Path
from config import FPL_ENDPOINTS

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

# Rate limiting: be polite to the API
REQUEST_DELAY = 0.3  # seconds between requests


def _get(url: str, cache_key: str | None = None, cache_ttl: int = 300) -> dict | list:
    """GET with optional file-based cache (TTL in seconds). Falls back to stale cache on network error."""
    cache_file = None
    if cache_key:
        cache_file = CACHE_DIR / f"{cache_key}.json"
        if cache_file.exists():
            age = time.time() - cache_file.stat().st_mtime
            if age < cache_ttl:
                return json.loads(cache_file.read_text(encoding="utf-8"))

    try:
        time.sleep(REQUEST_DELAY)
        resp = requests.get(url, headers={"User-Agent": "FPL-Predictor/1.0"}, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        if cache_key:
            cache_file = CACHE_DIR / f"{cache_key}.json"
            cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        return data
    except (requests.ConnectionError, requests.Timeout, requests.RequestException) as e:
        # Fallback to stale cache if available
        if cache_file and cache_file.exists():
            print(f"[WARN] API unreachable ({e.__class__.__name__}), using cached {cache_key}")
            return json.loads(cache_file.read_text(encoding="utf-8"))
        raise


def fetch_bootstrap() -> dict:
    """Fetch the main bootstrap-static data (all players, teams, events)."""
    return _get(FPL_ENDPOINTS["bootstrap"], cache_key="bootstrap", cache_ttl=600)


def fetch_fixtures() -> list:
    """Fetch all fixtures for the season."""
    return _get(FPL_ENDPOINTS["fixtures"], cache_key="fixtures", cache_ttl=600)


def fetch_player_detail(player_id: int) -> dict:
    """Fetch detailed history for a single player."""
    url = FPL_ENDPOINTS["player_detail"].format(player_id=player_id)
    return _get(url, cache_key=f"player_{player_id}", cache_ttl=900)


def fetch_gameweek_live(event_id: int) -> dict:
    """Fetch live stats for a specific gameweek."""
    url = FPL_ENDPOINTS["gameweek_live"].format(event_id=event_id)
    return _get(url, cache_key=f"gw_live_{event_id}", cache_ttl=120)


def fetch_set_piece_notes() -> dict:
    """Fetch set piece taker info."""
    try:
        return _get(FPL_ENDPOINTS["set_pieces"], cache_key="set_pieces", cache_ttl=3600)
    except Exception:
        return {}


# ── Derived helpers ───────────────────────────────────────────

def get_current_gameweek(bootstrap: dict | None = None) -> int:
    """Return the current (or next upcoming) gameweek number."""
    if bootstrap is None:
        bootstrap = fetch_bootstrap()
    for event in bootstrap["events"]:
        if event["is_current"]:
            return event["id"]
    # If no current, return next
    for event in bootstrap["events"]:
        if event["is_next"]:
            return event["id"]
    return 1


def get_next_gameweek(bootstrap: dict | None = None) -> int:
    """Return the next gameweek number."""
    if bootstrap is None:
        bootstrap = fetch_bootstrap()
    for event in bootstrap["events"]:
        if event["is_next"]:
            return event["id"]
    current = get_current_gameweek(bootstrap)
    return min(current + 1, 38)


def build_player_map(bootstrap: dict | None = None) -> dict:
    """
    Build a dict of player_id -> enriched player dict.
    Merges team info, position info for easy access.
    """
    if bootstrap is None:
        bootstrap = fetch_bootstrap()

    teams = {t["id"]: t for t in bootstrap["teams"]}
    positions = {p["id"]: p for p in bootstrap["element_types"]}

    players = {}
    for el in bootstrap["elements"]:
        pid = el["id"]
        team = teams.get(el["team"], {})
        pos = positions.get(el["element_type"], {})
        players[pid] = {
            **el,
            "team_name": team.get("name", "Unknown"),
            "team_short": team.get("short_name", "???"),
            "team_strength_overall": team.get("strength_overall_home", 0)
                                     + team.get("strength_overall_away", 0),
            "team_strength_attack_home": team.get("strength_attack_home", 0),
            "team_strength_attack_away": team.get("strength_attack_away", 0),
            "team_strength_defence_home": team.get("strength_defence_home", 0),
            "team_strength_defence_away": team.get("strength_defence_away", 0),
            "position_name": pos.get("singular_name_short", "???"),
            "position_id": el["element_type"],
        }
    return players


def build_team_map(bootstrap: dict | None = None) -> dict:
    """Build dict of team_id -> team dict."""
    if bootstrap is None:
        bootstrap = fetch_bootstrap()
    return {t["id"]: t for t in bootstrap["teams"]}


def get_fixtures_for_gameweek(gw: int, fixtures: list | None = None) -> list:
    """Get fixtures for a specific gameweek."""
    if fixtures is None:
        fixtures = fetch_fixtures()
    return [f for f in fixtures if f.get("event") == gw]


def get_player_fixture(player_team_id: int, gw: int,
                       fixtures: list | None = None) -> dict | None:
    """Get the FIRST fixture for a player's team in a given gameweek.
    For DGW-aware code, use get_player_fixtures() instead."""
    results = get_player_fixtures(player_team_id, gw, fixtures)
    return results[0] if results else None


def get_player_fixtures(player_team_id: int, gw: int,
                        fixtures: list | None = None) -> list[dict]:
    """Get ALL fixtures for a player's team in a given gameweek.
    Returns a list — length 0 (BGW), 1 (normal), or 2+ (DGW)."""
    gw_fixtures = get_fixtures_for_gameweek(gw, fixtures)
    results = []
    for f in gw_fixtures:
        if f["team_h"] == player_team_id or f["team_a"] == player_team_id:
            is_home = f["team_h"] == player_team_id
            opponent_id = f["team_a"] if is_home else f["team_h"]
            fdr = f.get("team_h_difficulty" if is_home else "team_a_difficulty", 3)
            results.append({
                "fixture": f,
                "is_home": is_home,
                "opponent_id": opponent_id,
                "fdr": fdr,
            })
    return results


def get_dgw_teams(gw: int, fixtures: list | None = None) -> dict:
    """Return {team_id: fixture_count} for all teams with 2+ fixtures in a GW."""
    gw_fixtures = get_fixtures_for_gameweek(gw, fixtures)
    counts = {}
    for f in gw_fixtures:
        counts[f["team_h"]] = counts.get(f["team_h"], 0) + 1
        counts[f["team_a"]] = counts.get(f["team_a"], 0) + 1
    return {tid: cnt for tid, cnt in counts.items() if cnt >= 2}


def get_bgw_teams(gw: int, fixtures: list | None = None, bootstrap: dict | None = None) -> set:
    """Return set of team_ids that have NO fixture in a GW (blank gameweek)."""
    if bootstrap is None:
        bootstrap = fetch_bootstrap()
    all_teams = {t["id"] for t in bootstrap["teams"]}
    gw_fixtures = get_fixtures_for_gameweek(gw, fixtures)
    playing = set()
    for f in gw_fixtures:
        playing.add(f["team_h"])
        playing.add(f["team_a"])
    return all_teams - playing
