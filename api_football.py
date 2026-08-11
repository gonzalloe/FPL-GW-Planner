import json
import os
import time
from pathlib import Path
import requests

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()

SUPABASE_STORAGE_BUCKET = "api-football-cache"
_supabase = None

# ============================================================
# API-FOOTBALL CONFIG
# ============================================================

APIFOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "")
APIFOOTBALL_BASE = "https://apiv3.apifootball.com/"

API_FOOTBALL_CACHE_DIR = (
    Path(__file__).parent / "cache" / "api_football"
)
API_FOOTBALL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

API_FOOTBALL_CACHE_TTL = 60 * 60 * 24 * 30  # 30 days

ENGLAND_COUNTRY_ID = 44
CHAMPIONSHIP_NAME = "Championship"

# Be polite to the API.
REQUEST_DELAY = 0.3

# ============================================================
# Supabase CONFIG
# ============================================================

def _get_supabase():
    global _supabase
    if _supabase is not None:
        return _supabase
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("[API-FOOTBALL] Supabase Storage not configured")
        return None
    try:
        from supabase import create_client

        _supabase = create_client(
            SUPABASE_URL,
            SUPABASE_KEY,
        )

        return _supabase

    except Exception as e:
        print(
            "[API-FOOTBALL] Supabase init failed:",
            repr(e),
        )
        return None

def _storage_cache_path(cache_key):
    return f"{cache_key}.json"


def _load_storage_cache(cache_key):
    """
    Load cached API-Football data from Supabase Storage.
    Returns:
        (data, updated_at)
    or:
        (None, None)
    """

    sb = _get_supabase()

    if sb is None:
        return None, None

    path = _storage_cache_path(cache_key)

    try:
        data = sb.storage \
            .from_(SUPABASE_STORAGE_BUCKET) \
            .download(path)

        if not data:
            return None, None

        parsed = json.loads(
            data.decode("utf-8")
            if isinstance(data, bytes)
            else data
        )

        print(
            f"[API-FOOTBALL] SUPABASE CACHE HIT: "
            f"{cache_key}"
        )

        return parsed, None

    except Exception as e:
        print(
            f"[API-FOOTBALL] Supabase cache miss: "
            f"{cache_key} ({e})"
        )

        return None, None


def _save_storage_cache(cache_key, data):
    """
    Save API-Football response to Supabase Storage.
    """

    sb = _get_supabase()

    if sb is None:
        return False

    path = _storage_cache_path(cache_key)

    try:
        payload = json.dumps(
            data,
            ensure_ascii=False,
        ).encode("utf-8")

        sb.storage \
            .from_(SUPABASE_STORAGE_BUCKET) \
            .upload(
                path,
                payload,
                {
                    "content-type": "application/json",
                    "cache-control": "31536000",
                    "upsert": "true",
                },
            )

        print(
            f"[API-FOOTBALL] SUPABASE CACHE SAVED: "
            f"{path}"
        )

        return True

    except Exception as e:
        print(
            f"[API-FOOTBALL] Supabase cache save failed: "
            f"{repr(e)}"
        )

        return False

# ============================================================
# RAW API CALL
# ============================================================

def apifootball_call(
    action,
    cache_key=None,
    cache_ttl=None,
    **params,
):
    """
    Make an API-Football call with persistent
    Supabase Storage caching.

    Cache-first:
        Supabase Storage -> API-Football

    If API-Football fails, the last cached response
    is used when available.
    """

    if not APIFOOTBALL_KEY:
        print(
            "[API-FOOTBALL] ERROR: "
            "API_FOOTBALL_KEY is not set"
        )
        return None

    if cache_ttl is None:
        cache_ttl = API_FOOTBALL_CACHE_TTL

    # -------------------------------------------------
    # SUPABASE CACHE
    # -------------------------------------------------

    cached_data = None

    if cache_key:
        cached_data, _ = _load_storage_cache(
            cache_key
        )

        if cached_data is not None:
            return cached_data

    # -------------------------------------------------
    # API REQUEST
    # -------------------------------------------------

    query = {
        "action": action,
        "APIkey": APIFOOTBALL_KEY,
        **params,
    }

    print("\n" + "=" * 70)
    print(
        f"[API-FOOTBALL] ACTION: {action}"
    )
    print(
        f"[API-FOOTBALL] PARAMS: {params}"
    )
    print("=" * 70)

    try:
        response = requests.get(
            APIFOOTBALL_BASE,
            params=query,
            timeout=30,
        )

        print(
            "[API-FOOTBALL] STATUS:",
            response.status_code,
        )

        response.raise_for_status()

        data = response.json()

        if isinstance(data, list):
            print(
                "[API-FOOTBALL] ITEMS:",
                len(data),
            )
        else:
            print(
                "[API-FOOTBALL] RESPONSE TYPE:",
                type(data).__name__,
            )

        # -------------------------------------------------
        # SAVE TO SUPABASE
        # -------------------------------------------------

        if cache_key:
            _save_storage_cache(
                cache_key,
                data,
            )

        return data

    except Exception as e:

        print(
            "[API-FOOTBALL] ERROR:",
            repr(e),
        )

        # -------------------------------------------------
        # FALLBACK TO EXISTING CACHE
        # -------------------------------------------------

        if cache_key:
            stale_data, _ = _load_storage_cache(
                cache_key
            )

            if stale_data is not None:
                print(
                    "[API-FOOTBALL] "
                    "USING STALE SUPABASE CACHE:",
                    cache_key,
                )
                return stale_data
        return None

def get_championship_league():
    """
    Automatically find the current Championship league.
    We do NOT hard-code the league ID here.
    """
    data = apifootball_call(
        "get_leagues",
        cache_key="england_leagues",
        cache_ttl=60 * 60 * 24 * 7,
        country_id=ENGLAND_COUNTRY_ID,
    )

    if not isinstance(data, list):
        raise RuntimeError(
            "API-Football get_leagues returned no usable data"
        )

    for league in data:
        if (
            str(league.get("league_name", "")).strip().lower()
            == CHAMPIONSHIP_NAME.lower()
        ):
            return league

    raise RuntimeError(
        "England Championship was not found in API-Football"
    )

def get_fpl_season_dates(bootstrap, year_offset=0):
    """
    Determine the FPL season from bootstrap data.
    year_offset:
        0  = current FPL season
       -1  = previous FPL season
    """

    events = bootstrap.get("events", [])
    if not events:
        raise RuntimeError(
            "FPL bootstrap contains no events"
        )
    dates = []

    for event in events:
        deadline = event.get("deadline_time")

        if deadline:
            dates.append(deadline[:10])

    if not dates:
        raise RuntimeError(
            "Could not determine FPL season dates"
        )

    first_date = min(dates)
    first_year = int(first_date[:4]) + year_offset

    return {
        "season": f"{first_year}/{first_year + 1}",
        "from_date": f"{first_year}-08-01",
        "to_date": f"{first_year + 1}-05-31",
    }

def get_championship_config(bootstrap, year_offset=0):
    """
    Automatically determine:
        - Championship league ID
        - relevant season
        - historical date range
    No manual season/year/league-ID changes required.
    """

    league = get_championship_league()
    season_info = get_fpl_season_dates(bootstrap,  year_offset=year_offset)
    config = {
        "league_id": str(
            league["league_id"]
        ),
        "league_name": league.get(
            "league_name",
            CHAMPIONSHIP_NAME,
        ),
        "season": season_info["season"],
        "from_date": season_info["from_date"],
        "to_date": season_info["to_date"],
    }

    print("\n" + "=" * 70)
    print("API-FOOTBALL CHAMPIONSHIP CONFIG")
    print("=" * 70)
    print(
        "League:",
        config["league_name"],
    )
    print(
        "League ID:",
        config["league_id"],
    )
    print(
        "Season:",
        config["season"],
    )
    print(
        "Date range:",
        config["from_date"],
        "to",
        config["to_date"],
    )
    print("=" * 70)

    return config


# ============================================================
# VALUE CONVERSION HELPERS
# ============================================================

def _to_int(value, default=0):
    """
    Safely convert an API value to int.
    """

    if value in (None, ""):
        return default

    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value, default=None):
    """
    Safely convert an API value to float.
    """

    if value in (None, ""):
        return default

    try:
        return float(value)
    except (TypeError, ValueError):
        return default

# ============================================================
# EVENT FIELD HELPERS
# ============================================================

def _get_event_id(event):
    """
    Extract match/event ID from an API-Football event.
    """

    return (
        event.get("match_id")
        or event.get("event_key")
        or event.get("match_key")
    )


def _get_event_date(event):
    """
    Extract match date from an API-Football event.
    """

    return (
        event.get("match_date")
        or event.get("event_date")
    )


def _get_home_team(event):
    """
    Extract home team name.
    """

    return (
        event.get("match_hometeam_name")
        or event.get("event_home_team")
        or event.get("home_team")
    )


def _get_away_team(event):
    """
    Extract away team name.
    """

    return (
        event.get("match_awayteam_name")
        or event.get("event_away_team")
        or event.get("away_team")
    )


# ============================================================
# RECURSIVE PLAYER RECORD SEARCH
# ============================================================

def _find_player_records(obj):
    """
    Recursively search an API-Football event for player-stat records.

    API-Football can place player information inside nested
    structures depending on the response.

    A dictionary is considered a player-stat record when it
    contains player_key and at least one recognised player
    statistic field.
    """

    found = []

    if isinstance(obj, dict):

        if (
            "player_key" in obj
            and (
                "player_minutes_played" in obj
                or "player_goals" in obj
                or "player_rating" in obj
                or "player_assists" in obj
            )
        ):
            found.append(obj)

        for value in obj.values():
            found.extend(
                _find_player_records(value)
            )

    elif isinstance(obj, list):

        for item in obj:
            found.extend(
                _find_player_records(item)
            )

    return found


# ============================================================
# PLAYER MATCHING
# ============================================================

def _player_matches(
    player,
    player_key=None,
    player_name=None,
):
    """
    Determine whether an API-Football player record matches
    the requested player.

    player_key is preferred because it is much safer than
    name matching.
    """

    current_player_key = str(
        player.get("player_key", "")
    )

    current_name = str(
        player.get("player_name", "")
    )

    # --------------------------------------------------------
    # Exact player-key match
    # --------------------------------------------------------

    if player_key is not None:

        if current_player_key == str(player_key):
            return True

        # If a key was supplied and does not match,
        # don't fall back to name matching.
        return False

    # --------------------------------------------------------
    # Name matching
    # --------------------------------------------------------

    if player_name:

        requested = player_name.strip().lower()
        current = current_name.strip().lower()

        if requested and requested in current:
            return True

    return False


# ============================================================
# HISTORICAL PLAYER MATCH STATISTICS
# ============================================================

def get_historical_player_stats(
    league_id,
    from_date,
    to_date,
    player_key=None,
    player_name=None,
):
    """
    Retrieve historical match-by-match player statistics
    from API-Football.

    Args:
        league_id:
            API-Football league ID.

        from_date:
            Start date in YYYY-MM-DD format.

        to_date:
            End date in YYYY-MM-DD format.

        player_key:
            API-Football player key.

            Preferred over player_name.

        player_name:
            Optional fallback for player matching.

    Returns:
        list[dict]
    """

    # --------------------------------------------------------
    # Validate player selector
    # --------------------------------------------------------

    if player_key is None and not player_name:
        print(
            "[API-FOOTBALL] ERROR: "
            "Provide player_key or player_name"
        )
        return []

    # --------------------------------------------------------
    # Request historical events
    # --------------------------------------------------------

    events = apifootball_call(
        "get_events",
        league_id=league_id,
        **{
            "from": from_date,
            "to": to_date,
            "withPlayerStats": 1,
        },
    )

    if not isinstance(events, list):

        print(
            "[API-FOOTBALL] ERROR: "
            "get_events did not return a list"
        )

        return []

    # --------------------------------------------------------
    # Extract player records
    # --------------------------------------------------------

    records = []

    # Used to protect against duplicate nested records.
    seen = set()

    for event in events:

        if not isinstance(event, dict):
            continue

        event_id = _get_event_id(event)

        event_date = _get_event_date(event)

        home_team = _get_home_team(event)

        away_team = _get_away_team(event)

        player_records = _find_player_records(event)

        for player in player_records:

            if not _player_matches(
                player,
                player_key=player_key,
                player_name=player_name,
            ):
                continue

            # ------------------------------------------------
            # API-Football can expose lineup-only records
            # where player_minutes_played is missing.
            #
            # We do NOT count those as actual appearances.
            # ------------------------------------------------

            minutes_raw = player.get(
                "player_minutes_played"
            )

            if minutes_raw in (None, ""):
                continue

            try:
                minutes = int(minutes_raw)

            except (TypeError, ValueError):
                continue

            # ------------------------------------------------
            # Prevent duplicate records
            # ------------------------------------------------

            current_player_key = str(
                player.get("player_key", "")
            )

            dedupe_key = (
                str(event_id),
                current_player_key,
            )

            if dedupe_key in seen:
                continue

            seen.add(dedupe_key)

            # ------------------------------------------------
            # Build normalized record
            # ------------------------------------------------

            records.append(
                {
                    "match_id": event_id,
                    "date": event_date,
                    "home": home_team,
                    "away": away_team,
                    "player_name": player.get(
                        "player_name"
                    ),
                    "player_key": current_player_key,
                    "minutes": minutes,
                    "goals": _to_int(
                        player.get("player_goals")
                    ),
                    "assists": _to_int(
                        player.get("player_assists")
                    ),
                    "yellow_cards": _to_int(
                        player.get(
                            "player_yellow_cards"
                        )
                    ),
                    "red_cards": _to_int(
                        player.get(
                            "player_red_cards"
                        )
                    ),
                    "rating": _to_float(
                        player.get("player_rating")
                    ),
                }
            )

    # --------------------------------------------------------
    # Sort chronologically
    # --------------------------------------------------------

    records.sort(
        key=lambda x: (
            x.get("date") or "",
            str(x.get("match_id") or ""),
        )
    )

    # --------------------------------------------------------
    # Debug summary
    # --------------------------------------------------------

    total_minutes = sum(
        record["minutes"]
        for record in records
    )

    print("\n")
    print("=" * 70)
    print("API-FOOTBALL HISTORICAL PLAYER SUMMARY")
    print("=" * 70)

    print(
        "League ID:",
        league_id,
    )

    print(
        "Date range:",
        from_date,
        "to",
        to_date,
    )

    print(
        "Player:",
        player_name
        if player_name
        else player_key,
    )

    print(
        "Records:",
        len(records),
    )

    print(
        "Total minutes:",
        total_minutes,
    )
    return records

def get_player_championship_history(
    bootstrap,
    player_key=None,
    player_name=None,
):
    """
    Automatically fetch the relevant Championship history
    for the current FPL season.

    No hard-coded:
        league ID
        season
        dates
    """

    config = get_championship_config(bootstrap)

    return get_historical_player_stats(
        league_id=config["league_id"],
        from_date=config["from_date"],
        to_date=config["to_date"],
        player_key=player_key,
        player_name=player_name,
    )

def get_historical_player_stats_bulk(
    league_id,
    from_date,
    to_date,
):
    """
    Fetch historical player match statistics once and return them
    grouped by normalized player name.

    This is much more efficient than calling get_historical_player_stats()
    separately for every player.
    """

    events = apifootball_call(
        "get_events",
        league_id=league_id,
        **{
            "from": from_date,
            "to": to_date,
            "withPlayerStats": 1,
        },
    )

    if not isinstance(events, list):
        print("ERROR: get_events did not return a list")
        return {}

    def find_player_records(obj):
        found = []

        if isinstance(obj, dict):
            if (
                "player_key" in obj
                and (
                    "player_minutes_played" in obj
                    or "player_goals" in obj
                    or "player_rating" in obj
                )
            ):
                found.append(obj)

            for value in obj.values():
                found.extend(find_player_records(value))

        elif isinstance(obj, list):
            for item in obj:
                found.extend(find_player_records(item))

        return found

    def to_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def to_float(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    grouped = {}

    for event in events:
        event_id = (
            event.get("match_id")
            or event.get("event_key")
            or event.get("match_key")
        )

        event_date = (
            event.get("match_date")
            or event.get("event_date")
        )

        home_team = (
            event.get("match_hometeam_name")
            or event.get("event_home_team")
            or event.get("home_team")
        )

        away_team = (
            event.get("match_awayteam_name")
            or event.get("event_away_team")
            or event.get("away_team")
        )

        player_records = find_player_records(event)

        for player in player_records:
            minutes_raw = player.get("player_minutes_played")

            # Ignore lineup-only records.
            if minutes_raw in (None, ""):
                continue

            try:
                minutes = int(minutes_raw)
            except (TypeError, ValueError):
                continue

            player_name = str(
                player.get("player_name", "")
            ).strip()

            if not player_name:
                continue

            key = player_name.lower()

            grouped.setdefault(key, []).append({
                "match_id": event_id,
                "date": event_date,
                "home": home_team,
                "away": away_team,
                "player_name": player_name,
                "player_key": str(
                    player.get("player_key", "")
                ),
                "minutes": minutes,
                "goals": to_int(
                    player.get("player_goals")
                ),
                "assists": to_int(
                    player.get("player_assists")
                ),
                "yellow_cards": to_int(
                    player.get("player_yellow_cards")
                ),
                "red_cards": to_int(
                    player.get("player_red_cards")
                ),
                "rating": to_float(
                    player.get("player_rating")
                ),
            })

    print(
        f"[API-FOOTBALL] Historical players indexed: "
        f"{len(grouped)}"
    )
    return grouped

def get_championship_player_history(
    bootstrap,
    force_refresh=False,
    year_offset=-1,
):
    """
    Fetch and cache Championship history for the
    requested season.
    year_offset=-1 means the Championship season
    immediately preceding the current FPL season.
    """

    config = get_championship_config(bootstrap, year_offset=year_offset)
    season_key = (config["season"].replace("/", "_"))
    cache_key = (f"championship_events_"f"{season_key}")
    if force_refresh:
        cache_file = (
            API_FOOTBALL_CACHE_DIR
            / f"{cache_key}.json"
        )
        if cache_file.exists():
            cache_file.unlink()
    events = apifootball_call(
        "get_events",
        cache_key=cache_key,
        cache_ttl=60 * 60 * 24 * 30,
        league_id=config["league_id"],
        **{
            "from": config["from_date"],
            "to": config["to_date"],
            "withPlayerStats": 1,
        },
    )
    if not isinstance(events, list):
        print(
            "[API-FOOTBALL] ERROR: "
            "Championship events unavailable"
        )
        return []

    return events

# ============================================================
# PLAYER SEASON SUMMARY
# ============================================================

def summarize_player_stats(records):
    """
    Convert match-by-match historical records into a
    season-level summary.

    Returns:

        {
            "matches": ...,
            "starts_or_appearances": ...,
            "minutes": ...,
            "goals": ...,
            "assists": ...,
            "yellow_cards": ...,
            "red_cards": ...,
            "average_rating": ...,
        }
    """

    if not records:
        return {
            "matches": 0,
            "starts_or_appearances": 0,
            "minutes": 0,
            "goals": 0,
            "assists": 0,
            "yellow_cards": 0,
            "red_cards": 0,
            "average_rating": None,
        }

    total_minutes = sum(
        _to_int(r.get("minutes"))
        for r in records
    )

    total_goals = sum(
        _to_int(r.get("goals"))
        for r in records
    )

    total_assists = sum(
        _to_int(r.get("assists"))
        for r in records
    )

    total_yellows = sum(
        _to_int(r.get("yellow_cards"))
        for r in records
    )

    total_reds = sum(
        _to_int(r.get("red_cards"))
        for r in records
    )

    ratings = [
        r.get("rating")
        for r in records
        if r.get("rating") is not None
    ]

    average_rating = (
        sum(ratings) / len(ratings)
        if ratings
        else None
    )

    return {
        "matches": len(records),
        "appearances": len(records),
        "minutes": total_minutes,
        "goals": total_goals,
        "assists": total_assists,
        "yellow_cards": total_yellows,
        "red_cards": total_reds,
        "average_rating": (
            round(average_rating, 2)
            if average_rating is not None
            else None
        ),
    }