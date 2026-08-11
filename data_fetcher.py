"""
FPL Predictor - Data Fetcher
All data from the official FPL API & APIFOOTBALL.
"""
import json
import time
import requests
import csv
import io
import os
from pathlib import Path
from config import FPL_ENDPOINTS

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

# Rate limiting: be polite to the API
REQUEST_DELAY = 0.3  # seconds between requests

# Get APIFOOTBALL KEY
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "")

""""
Testing for APIFOOTBALL Integration
"""
def test_api_football():
    api_key = os.getenv("API_FOOTBALL_KEY")

    url = "https://apiv3.apifootball.com/"
    params = {
        "action": "get_leagues",
        "country_id": 44,
        "APIkey": api_key,
    }

    response = requests.get(url, params=params, timeout=30)

    print("STATUS:", response.status_code)
    print("RESPONSE:", response.text[:5000])

def get_championship_league_id():
    api_key = os.getenv("API_FOOTBALL_KEY", "")

    if not api_key:
        return None

    url = "https://apiv3.apifootball.com/"

    params = {
        "action": "get_leagues",
        "country_id": 44,
        "APIkey": api_key,
    }

    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print(f"[API-FOOTBALL] get_leagues failed: {e}")
        return None

    for league in data:
        if league.get("league_name", "").lower() == "championship":
            return int(league["league_id"])

    return None

def get_championship_teams():
    api_key = os.getenv("API_FOOTBALL_KEY", "")
    league_id = get_championship_league_id()

    if not api_key or not league_id:
        return []

    url = "https://apiv3.apifootball.com/"

    params = {
        "action": "get_teams",
        "league_id": league_id,
        "APIkey": api_key,
    }

    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"[API-FOOTBALL] get_teams failed: {e}")
        return []

def get_api_football_player(name):
    api_key = os.getenv("API_FOOTBALL_KEY", "")

    url = "https://apiv3.apifootball.com/"

    params = {
        "action": "get_players",
        "player_name": name,
        "APIkey": api_key,
    }
    results = get_api_football_player("Meslier")
    print(results)

    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"[API-FOOTBALL] get_players failed: {e}")
        return []


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


def get_promoted_team_priors(bootstrap: dict, teams: dict, real_priors: dict) -> dict:
    """
    Fallback priors for teams absent from real_priors (promoted teams),
    sourced from football-data.co.uk's Championship (E1) results for their
    most recent Championship season, adjusted for the step up to the PL.

    GF and GA are adjusted SEPARATELY (not by one symmetric ratio):
      - GF deflates moving to PL (Championship attacks score more, on
        average, against weaker PL-bound defenses is not guaranteed - PL
        defenses are collectively stronger, so raw scoring rate overstates
        PL output).
      - GA inflates moving to PL (PL attacks are collectively stronger,
        so a team's defense concedes more than their Championship rate
        suggested).
    Both factors are computed from real_priors vs a corresponding PL-wide
    Championship-teams baseline where available, falling back to a
    conservative default only if that comparison can't be computed.
    """
    events = bootstrap.get("events", [])
    if not events:
        return {}
    try:
        year = int(events[0]["deadline_time"][:4])
    except (KeyError, ValueError, TypeError):
        return {}
    season_code = f"{str(year-1)[-2:]}{str(year)[-2:]}"  # e.g. 2025 -> "2425"

    url = f"https://www.football-data.co.uk/mmz4281/{season_code}/E1.csv"
    rows = _fetch_csv(url, f"champ_e1_{season_code}")
    if not rows:
        return {}  # source unavailable -> caller's LEAGUE_AVG_GOALS last resort applies

    def normalize(name: str) -> str:
        n = name.lower().strip()
        # remove punctuation/suffixes
        for suffix in (" fc", " afc", " f.c.", " a.f.c."):
            n = n.replace(suffix, "")
        aliases = {
            # Championship -> FPL naming
            "hull": "hull",
            "hull city": "hull",
            "ipswich": "ipswich",
            "ipswich town": "ipswich",
            "coventry": "coventry",
            "coventry city": "coventry",
            # common names
            "nott'm forest": "nottingham forest",
            "nottingham forest": "nottingham forest",
            "man utd": "manchester united",
            "man united": "manchester united",
            "man city": "manchester city",
            "spurs": "tottenham",
            "wolves": "wolverhampton",
            "sheffield utd": "sheffield united",
            "sheffield united": "sheffield united",
            "newcastle": "newcastle united",
        }
        return aliases.get(n, n)

    agg = {}
    champ_total_gf, champ_total_games = 0, 0
    for row in rows:
        home, away = row.get("HomeTeam", ""), row.get("AwayTeam", "")
        try:
            fthg, ftag = int(row["FTHG"]), int(row["FTAG"])
        except (KeyError, ValueError):
            continue
        champ_total_gf += fthg + ftag
        champ_total_games += 2
        for name, gf, ga in ((home, fthg, ftag), (away, ftag, fthg)):
            if not name:
                continue
            key = normalize(name)
            a = agg.setdefault(key, {"gf": 0, "ga": 0, "played": 0})
            a["gf"] += gf; a["ga"] += ga; a["played"] += 1

    if champ_total_games == 0:
        return {}
    champ_avg_goals = champ_total_gf / champ_total_games

    pl_gf_values = [v["gf_per_game"] for v in real_priors.values()]
    pl_ga_values = [v["ga_per_game"] for v in real_priors.values()]
    pl_avg_gf = sum(pl_gf_values) / len(pl_gf_values) if pl_gf_values else 1.35
    pl_avg_ga = sum(pl_ga_values) / len(pl_ga_values) if pl_ga_values else 1.35

    # Championship -> Premier League scoring environment adjustment
    # GF and GA are treated separately.
    gf_adjustment = 0.85
    ga_adjustment = 1.15

    priors = {}
    for tid, t in teams.items():
        if tid in real_priors:
            continue
        key = normalize(t.get("name", ""))
        a = agg.get(key)
        if not a:
            print(f"[WARN] Promoted team not matched: {t.get('name')} ({key})")
            continue
        champ_gf = a["gf"] / a["played"]
        champ_ga = a["ga"] / a["played"]

        # Adjust Championship performance into PL environment
        adjusted_gf = champ_gf * gf_adjustment
        adjusted_ga = champ_ga * ga_adjustment

        # Regression toward PL average because promotion is a huge difficulty jump.
        PROMOTED_GF_TRUST = 0.20
        PROMOTED_GA_TRUST = 0.35
        final_gf = (adjusted_gf * PROMOTED_GF_TRUST + pl_avg_gf * (1 - PROMOTED_GF_TRUST)) * 0.90
        final_ga = (adjusted_ga * PROMOTED_GA_TRUST + pl_avg_ga * (1 - PROMOTED_GA_TRUST)) * 1.15
        priors[tid] = {
            "gf_per_game": round(final_gf, 3),
            "ga_per_game": round(final_ga, 3),
        }
    return priors
    

def get_last_season_rates(player_id: int) -> dict:
    """
    Per-90 xG/xA/bonus rate from the player's most recent completed
    PL season with a meaningful sample (>=450 mins, ~5 full games).
    Used as a cold-start prior before this season's minutes accumulate.
    Returns {} if no qualifying prior season is found.
    """
    try:
        detail = fetch_player_detail(player_id)
    except Exception:
        return {}

    history_past = detail.get("history_past", []) if isinstance(detail, dict) else []
    if not history_past:
        return {}

    # Walk backwards from most recent season, skip ones with too little data
    for season in reversed(history_past):
        mins = int(season.get("minutes", 0) or 0)
        if mins < 450:
            continue
        starts = int(season.get("starts", 0) or 0)
        if starts <= 0:
            # Older FPL seasons don't always report `starts` - approximate
            starts = max(round(mins / 75), 1)
        games = 38
        xg = float(season.get("expected_goals", 0) or 0)
        xa = float(season.get("expected_assists", 0) or 0)
        bonus = int(season.get("bonus", 0) or 0)
        per90 = mins / 90.0
        return {
            "xg_per90": xg / per90 if per90 > 0 else 0.0,
            "xa_per90": xa / per90 if per90 > 0 else 0.0,
            "bonus_per_start": ( bonus / starts if starts > 0 else 0.0 ),
            "season_name": season.get("season_name", ""),
            "minutes": mins,
            "starts": starts,
            "games": games,
        }
    return {}

def get_recent_gw_stats(player_id: int, window: int = 5) -> dict:
    """
    Current-season per-gameweek minutes/starts from the last `window` GWs,
    via element-summary's `history` array (distinct from `history_past`,
    which covers prior completed seasons). Used to detect a player who has
    fallen out of / into the team recently - something season-long
    cumulative starts/minutes cannot see, since it treats August and this
    week's form identically.
    Returns {} if fewer than 2 games are available (too little to trust).
    """
    try:
        detail = fetch_player_detail(player_id)
    except Exception:
        return {}

    history = detail.get("history", []) if isinstance(detail, dict) else []
    if not history:
        return {}

    recent = history[-window:]
    if len(recent) < 2:
        return {}

    starts = sum(1 for gw in recent if int(gw.get("minutes", 0) or 0) >= 60)
    total_mins = sum(int(gw.get("minutes", 0) or 0) for gw in recent)
    n = len(recent)

    return {
        "recent_start_rate": starts / n,
        "recent_avg_mins": total_mins / n,
        "recent_games": n,
    }


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


def _fetch_csv(url: str, cache_key: str, cache_ttl: int = 86400) -> list[dict]:
    """Tiny CSV GET-with-cache helper (vaastav archive serves CSV, not JSON)."""
    cache_file = CACHE_DIR / f"{cache_key}.json"
    if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < cache_ttl:
        return json.loads(cache_file.read_text(encoding="utf-8"))
    try:
        resp = requests.get(url, headers={"User-Agent": "FPL-Predictor/1.0"}, timeout=15)
        resp.raise_for_status()
        rows = list(csv.DictReader(io.StringIO(resp.text)))
        cache_file.write_text(json.dumps(rows), encoding="utf-8")
        return rows
    except Exception:
        if cache_file.exists():
            return json.loads(cache_file.read_text(encoding="utf-8"))
        return []


def get_previous_season_team_stats(bootstrap: dict, teams: dict) -> dict:
    """
    {team_id: {"gf_per_game", "ga_per_game"}} from the most recently
    completed PL season, sourced from the vaastav/Fantasy-Premier-League
    public archive (FPL's own API doesn't expose prior-season results).
    Matched via short_name (FPL's team_id can shift between seasons).
    Missing/newly-promoted teams are simply absent -> caller's
    LEAGUE_AVG_GOALS fallback in build_team_stats() applies to them.
    """
    events = bootstrap.get("events", [])
    if not events:
        return {}
    try:
        year = int(events[0]["deadline_time"][:4])
    except (KeyError, ValueError, TypeError):
        return {}
    season = f"{year - 1}-{str(year)[-2:]}"
    base = f"https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data/{season}"

    prev_teams = _fetch_csv(f"{base}/teams.csv", f"prev_teams_{season}")
    prev_fixtures = _fetch_csv(f"{base}/fixtures.csv", f"prev_fixtures_{season}")
    if not prev_teams or not prev_fixtures:
        return {}

    id_to_short = {row["id"]: row["short_name"] for row in prev_teams}
    agg = {}
    for f in prev_fixtures:
        if str(f.get("finished", "")).lower() != "true":
            continue
        th, ta = id_to_short.get(f.get("team_h")), id_to_short.get(f.get("team_a"))
        try:
            sh, sa = int(f["team_h_score"]), int(f["team_a_score"])
        except (KeyError, ValueError, TypeError):
            continue
        for name, gf, ga in ((th, sh, sa), (ta, sa, sh)):
            if not name:
                continue
            a = agg.setdefault(name, {"gf": 0, "ga": 0, "played": 0})
            a["gf"] += gf; a["ga"] += ga; a["played"] += 1

    short_to_current_id = {t.get("short_name"): tid for tid, t in teams.items()}
    result = {}
    for short_name, a in agg.items():
        tid = short_to_current_id.get(short_name)
        if tid and a["played"] > 0:
            result[tid] = {
                "gf_per_game": round(a["gf"] / a["played"], 3),
                "ga_per_game": round(a["ga"] / a["played"], 3),
            }
    return result


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
