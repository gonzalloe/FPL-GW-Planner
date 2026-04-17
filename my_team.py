"""
FPL Predictor - My Team Module
Fetches and analyzes a user's current FPL team via their Team ID.
"""
import json
import requests
import time
from pathlib import Path
from config import FPL_API_BASE

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)


def fetch_my_team(team_id: int) -> dict:
    """
    Fetch a user's current FPL team data.
    Uses the public API endpoint (no auth needed for basic info).
    Returns team info, picks, transfers, and chips.
    """
    result = {
        "team_id": team_id,
        "info": {},
        "picks": [],
        "transfers": [],
        "history": [],
        "chips": [],
        "error": None,
    }

    # 1. Team basic info + overall history
    try:
        url = f"{FPL_API_BASE}/entry/{team_id}/"
        resp = requests.get(url, headers={"User-Agent": "FPL-Predictor/1.0"}, timeout=15)
        resp.raise_for_status()
        entry = resp.json()
        result["info"] = {
            "name": entry.get("name", "Unknown Team"),
            "player_first_name": entry.get("player_first_name", ""),
            "player_last_name": entry.get("player_last_name", ""),
            "overall_points": entry.get("summary_overall_points", 0),
            "overall_rank": entry.get("summary_overall_rank", 0),
            "gameweek_points": entry.get("summary_event_points", 0),
            "gameweek_rank": entry.get("summary_event_rank", 0),
            "current_event": entry.get("current_event", 0),
            "total_transfers": entry.get("last_deadline_total_transfers", 0),
            "bank": entry.get("last_deadline_bank", 0) / 10,  # Convert to millions
            "team_value": entry.get("last_deadline_value", 0) / 10,
            "started_event": entry.get("started_event", 1),
            "favourite_team": entry.get("favourite_team"),
        }
    except Exception as e:
        result["error"] = f"Could not fetch team info: {str(e)}"
        return result

    time.sleep(0.3)

    # 2. Current GW picks (squad selection)
    current_event = result["info"]["current_event"]
    if current_event:
        try:
            url = f"{FPL_API_BASE}/entry/{team_id}/event/{current_event}/picks/"
            resp = requests.get(url, headers={"User-Agent": "FPL-Predictor/1.0"}, timeout=15)
            resp.raise_for_status()
            picks_data = resp.json()
            result["picks"] = picks_data.get("picks", [])
            result["active_chip"] = picks_data.get("active_chip")
            result["auto_subs"] = picks_data.get("automatic_subs", [])
            # entry_history has GW-level summary
            eh = picks_data.get("entry_history", {})
            result["gw_summary"] = {
                "points": eh.get("points", 0),
                "total_points": eh.get("total_points", 0),
                "rank": eh.get("rank", 0),
                "overall_rank": eh.get("overall_rank", 0),
                "bank": eh.get("bank", 0) / 10,
                "value": eh.get("value", 0) / 10,
                "event_transfers": eh.get("event_transfers", 0),
                "event_transfers_cost": eh.get("event_transfers_cost", 0),
                "points_on_bench": eh.get("points_on_bench", 0),
            }
        except Exception as e:
            result["error"] = f"Could not fetch GW picks: {str(e)}"

    time.sleep(0.3)

    # 3. Transfer history
    try:
        url = f"{FPL_API_BASE}/entry/{team_id}/transfers/"
        resp = requests.get(url, headers={"User-Agent": "FPL-Predictor/1.0"}, timeout=15)
        resp.raise_for_status()
        result["transfers"] = resp.json()[:20]  # Last 20 transfers
    except Exception:
        pass

    time.sleep(0.3)

    # 4. Season history (GW-by-GW)
    try:
        url = f"{FPL_API_BASE}/entry/{team_id}/history/"
        resp = requests.get(url, headers={"User-Agent": "FPL-Predictor/1.0"}, timeout=15)
        resp.raise_for_status()
        hist = resp.json()
        result["history"] = hist.get("current", [])
        result["chips"] = hist.get("chips", [])
        result["past_seasons"] = hist.get("past", [])
    except Exception:
        pass

    return result


def enrich_my_team(team_data: dict, player_map: dict, predictions: list) -> dict:
    """
    Enrich the team data with player details and predictions.
    Merges FPL player data + our prediction data for each pick.
    """
    pred_map = {p.get("player_id"): p for p in predictions}

    enriched_picks = []
    for pick in team_data.get("picks", []):
        pid = pick.get("element")
        player = player_map.get(pid, {})
        pred = pred_map.get(pid, {})

        enriched = {
            "player_id": pid,
            "name": player.get("web_name", "Unknown"),
            "full_name": f"{player.get('first_name', '')} {player.get('second_name', '')}".strip(),
            "team": player.get("team_short", "???"),
            "team_name": player.get("team_name", "Unknown"),
            "position": player.get("position_name", "???"),
            "position_id": player.get("position_id", 0),
            "price": player.get("now_cost", 0) / 10,
            "selected_by_percent": player.get("selected_by_percent", "0"),
            "form": float(player.get("form", 0)),
            "points_per_game": float(player.get("points_per_game", 0)),
            "total_points": int(player.get("total_points", 0)),
            "minutes": int(player.get("minutes", 0)),
            "goals_scored": int(player.get("goals_scored", 0)),
            "assists": int(player.get("assists", 0)),
            "clean_sheets": int(player.get("clean_sheets", 0)),
            "status": player.get("status", "a"),
            "news": player.get("news", ""),
            "chance_of_playing": player.get("chance_of_playing_next_round"),
            # From our predictions
            "predicted_points": pred.get("predicted_points", 0),
            "confidence": pred.get("confidence", 0),
            "fixture": pred.get("fixture", {}),
            "fixtures": pred.get("fixtures", []),
            "is_dgw": pred.get("is_dgw", False),
            "num_fixtures": pred.get("num_fixtures", 0),
            "team_last5_form": pred.get("team_last5_form", ""),
            "factors": pred.get("factors", {}),
            "availability": pred.get("availability", {}),
            # From picks
            "is_captain": pick.get("is_captain", False),
            "is_vice_captain": pick.get("is_vice_captain", False),
            "multiplier": pick.get("multiplier", 1),
            "position_in_squad": pick.get("position", 0),
            "is_starter": pick.get("position", 99) <= 11,
        }
        enriched_picks.append(enriched)

    team_data["enriched_picks"] = enriched_picks

    # Split into starters and bench
    team_data["starters"] = [p for p in enriched_picks if p["is_starter"]]
    team_data["bench"] = [p for p in enriched_picks if not p["is_starter"]]

    # Squad value and stats
    total_value = sum(p["price"] for p in enriched_picks)
    total_predicted = sum(
        p["predicted_points"] * (2 if p["is_captain"] else 1)
        for p in enriched_picks if p["is_starter"]
    )
    team_data["squad_value"] = round(total_value, 1)
    team_data["predicted_points"] = round(total_predicted, 1)

    # Identify weak spots (lowest predicted starters)
    starters_ranked = sorted(team_data["starters"], key=lambda x: x["predicted_points"])
    team_data["weakest_links"] = starters_ranked[:3]

    return team_data


def generate_transfer_suggestions(team_data: dict, predictions: list,
                                   free_transfers: int = 1) -> list:
    """
    Generate smart transfer suggestions based on current team vs predictions.
    """
    if not team_data.get("enriched_picks"):
        return []

    current_ids = {p["player_id"] for p in team_data["enriched_picks"]}
    current_teams = {}
    for p in team_data["enriched_picks"]:
        t = p.get("team", "???")
        current_teams[t] = current_teams.get(t, 0) + 1

    bank = team_data.get("gw_summary", {}).get("bank", 0)

    suggestions = []

    # Rank current starters by predicted points (worst first)
    starters = sorted(
        [p for p in team_data["enriched_picks"] if p["is_starter"]],
        key=lambda x: x["predicted_points"]
    )

    pred_map = {p.get("player_id"): p for p in predictions}

    for out_player in starters[:free_transfers * 3]:
        pos_id = out_player["position_id"]
        out_price = out_player["price"]
        budget = out_price + bank

        # Find better replacements
        candidates = []
        for pred in predictions:
            pid = pred.get("player_id")
            if pid in current_ids:
                continue
            if pred.get("position_id") != pos_id:
                continue
            if pred.get("price", 99) > budget:
                continue
            if pred.get("predicted_points", 0) <= out_player["predicted_points"]:
                continue
            # Check max 3 per team constraint
            team_short = pred.get("team", "???")
            if current_teams.get(team_short, 0) >= 3:
                # Only ok if we're selling from same team
                if out_player["team"] != team_short:
                    continue

            candidates.append(pred)

        candidates.sort(key=lambda x: x["predicted_points"], reverse=True)

        for best in candidates[:3]:
            suggestions.append({
                "out": {
                    "id": out_player["player_id"],
                    "name": out_player["name"],
                    "team": out_player["team"],
                    "position": out_player["position"],
                    "price": out_player["price"],
                    "predicted_points": out_player["predicted_points"],
                    "form": out_player["form"],
                    "total_points": out_player["total_points"],
                },
                "in": {
                    "id": best.get("player_id"),
                    "name": best.get("name"),
                    "team": best.get("team"),
                    "position": best.get("position"),
                    "price": best.get("price", 0),
                    "predicted_points": best.get("predicted_points", 0),
                    "form": float(best.get("factors", {}).get("form", 0)),
                    "fixture": best.get("fixture", {}),
                    "confidence": best.get("confidence", 0),
                    "selected_by_percent": best.get("selected_by_percent", "0"),
                },
                "points_gain": round(
                    best.get("predicted_points", 0) - out_player["predicted_points"], 2
                ),
                "cost_change": round(best.get("price", 0) - out_price, 1),
                "budget_after": round(budget - best.get("price", 0), 1),
            })

    # Sort by points gain and deduplicate
    suggestions.sort(key=lambda x: x["points_gain"], reverse=True)

    # Remove duplicate "in" players
    seen_in = set()
    unique = []
    for s in suggestions:
        in_id = s["in"]["id"]
        if in_id not in seen_in:
            seen_in.add(in_id)
            unique.append(s)

    return unique[:10]
