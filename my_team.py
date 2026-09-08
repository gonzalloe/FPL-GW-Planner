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

def calculate_free_transfers(history: list, chips: list | None = None) -> int:
    """
    Calculate free transfers available at the start of the current GW.

    History should contain completed gameweeks only.
    """
    chips = chips or []

    chip_by_gw = {}

    for chip in chips:
        if not isinstance(chip, dict):
            continue

        event = chip.get("event")
        name = str(chip.get("name", "")).lower()

        if event is not None:
            chip_by_gw[int(event)] = name

    free_transfers = 1

    rows = sorted(
        history or [],
        key=lambda row: int(row.get("event", 0))
    )

    for row in rows:
        transfers = int(
            row.get("event_transfers", 0) or 0
        )

        gw = int(row.get("event", 0))
        chip = chip_by_gw.get(gw)

        if chip in ("wildcard", "freehit"):
            transfers = 0

        used_free = min(
            transfers,
            free_transfers
        )

        free_transfers -= used_free

        free_transfers = min(
            5,
            free_transfers + 1
        )

    return max(0, min(5, free_transfers))



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
        "free_transfers": 1,
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
            "bank": (entry.get("last_deadline_bank") or 0) / 10,
            "team_value": (entry.get("last_deadline_value") or 0) / 10,
            "started_event": entry.get("started_event", 1),
            "favourite_team": entry.get("favourite_team"),
            "transfers_limit": entry.get("transfers_limit"),
        }
        # Keep the actual FPL value at the top level too.
        result["free_transfers"] = entry.get("free_transfers")

    except Exception as e:
        result["error"] = f"Could not fetch team info: {str(e)}"
        return result

    time.sleep(0.3)

    # 2. Squad selection
    # IMPORTANT:
    # current_event = the latest/current FPL GW
    # planning_event = the GW the manager is currently planning for.
    #
    # Example:
    #   GW3 just finished
    #   current_event = 3
    #   planning_event = 4
    #
    # Early transfers made after GW3 belong to GW4, so the squad
    # displayed by the planner must come from GW4 picks, not GW3.

    current_event = result["info"]["current_event"]

    if current_event:
        try:
            # =========================================================
            # 2A. Determine completed GW and planning GW
            # =========================================================

            completed_gw = current_event
            planning_gw = current_event + 1

            result["info"]["completed_gw"] = completed_gw
            result["info"]["planning_gw"] = planning_gw

            # =========================================================
            # 2B. Fetch season history + chips
            # =========================================================

            history_url = (
                f"{FPL_API_BASE}/entry/{team_id}/history/"
            )

            hist_resp = requests.get(
                history_url,
                headers={"User-Agent": "FPL-Predictor/1.0"},
                timeout=15,
            )

            hist_resp.raise_for_status()
            hist_data = hist_resp.json()
            chips_used = hist_data.get("chips", [])
            history_rows = hist_data.get("current", [])

            result["history"] = history_rows
            result["chips"] = chips_used
            result["past_seasons"] = hist_data.get("past", [])

            # =========================================================
            # 2C. Calculate FREE TRANSFERS AVAILABLE AFTER GW3
            # =========================================================

            completed_history = [
                row
                for row in history_rows
                if int(row.get("event", 0)) <= completed_gw
            ]

            starting_free_transfers = calculate_free_transfers(completed_history, chips_used,)
            result["free_transfers"] = starting_free_transfers

            # =========================================================
            # 2D. Fetch previous picks
            #
            # This is historical/current completed GW data.
            # We use it as the baseline, NOT as the displayed squad.
            # =========================================================

            completed_url = (
                f"{FPL_API_BASE}/entry/"
                f"{team_id}/event/"
                f"{completed_gw}/picks/"
            )

            completed_resp = requests.get(
                completed_url,
                headers={"User-Agent": "FPL-Predictor/1.0"},
                timeout=15,
            )

            completed_resp.raise_for_status()
            completed_picks_data = completed_resp.json()
            completed_pick_list = completed_picks_data.get("picks",[])
            completed_eh = completed_picks_data.get("entry_history",{})

            # =========================================================
            # 2E. Fetch PLANNING GW picks
            # If previous GW is finished and the manager has already made
            # a new GW transfer, the FPL GW4 picks endpoint is the
            # squad we want to display.
            # =========================================================

            planning_url = (
                f"{FPL_API_BASE}/entry/"
                f"{team_id}/event/"
                f"{planning_gw}/picks/"
            )

            planning_resp = requests.get(
                planning_url,
                headers={"User-Agent": "FPL-Predictor/1.0"},
                timeout=15,
            )

            if planning_resp.ok:
                planning_picks_data = planning_resp.json()

                planning_pick_list = planning_picks_data.get(
                    "picks",
                    []
                )

                planning_eh = planning_picks_data.get(
                    "entry_history",
                    {}
                )

                # =====================================================
                # NORMAL CASE:
                # new GW exists -> use new GW squad.
                # =====================================================

                result["picks"] = planning_pick_list
                result["active_chip"] = (planning_picks_data.get("active_chip"))
                result["auto_subs"] = (
                    planning_picks_data.get(
                        "automatic_subs",
                        []
                    )
                )

                picks_event = planning_gw

            else:
                # =====================================================
                # FALLBACK:
                # If FPL does not expose the next GW picks yet,
                # use completed GW picks.
                #
                # This prevents the whole My Team request from failing.
                # =====================================================

                planning_picks_data = {}
                planning_pick_list = []
                planning_eh = {}

                result["picks"] = completed_pick_list
                result["active_chip"] = (completed_picks_data.get("active_chip"))
                result["auto_subs"] = (
                    completed_picks_data.get(
                        "automatic_subs",
                        []
                    )
                )

                picks_event = completed_gw

            # =========================================================
            # 2F. Fetch TRANSFER HISTORY
            #
            # This is essential for early transfers.
            # entry_history for GW3 cannot tell us about a transfer
            # made for GW4.
            # =========================================================

            transfers_url = (
                f"{FPL_API_BASE}/entry/"
                f"{team_id}/transfers/"
            )

            transfers_resp = requests.get(
                transfers_url,
                headers={"User-Agent": "FPL-Predictor/1.0"},
                timeout=15,
            )

            transfers_resp.raise_for_status()

            all_transfers = transfers_resp.json()

            result["transfers"] = all_transfers[:20]

            planning_transfers = [
                t
                for t in all_transfers
                if int(t.get("event", 0)) == planning_gw
            ]

            completed_transfers = [
                t
                for t in all_transfers
                if int(t.get("event", 0)) == completed_gw
            ]

            # =========================================================
            # 2G. Count early transfers for the PLANNING GW
            # =========================================================

            planning_transfer_count = len(
                planning_transfers
            )

            free_transfers_remaining = max(
                0,
                starting_free_transfers
                - planning_transfer_count,
            )

            transfer_hits = max(
                0,
                planning_transfer_count
                - starting_free_transfers,
            )

            # =========================================================
            # 2H. Determine financial state
            #
            # Prefer the planning GW entry_history when available.
            # Otherwise use completed GW.
            # =========================================================

            if planning_eh:
                financial_eh = planning_eh
            else:
                financial_eh = completed_eh

            bank_raw = financial_eh.get("bank")

            value_raw = financial_eh.get("value")

            if bank_raw is None:
                bank_raw = entry.get(
                    "last_deadline_bank",
                    0,
                )

            if value_raw is None:
                value_raw = entry.get(
                    "last_deadline_value",
                    0,
                )

            # =========================================================
            # 2I. GW SUMMARY
            # =========================================================

            result["gw_summary"] = {
                # Important: this is the GW being planned.
                "event": planning_gw,

                # GW3 completed information.
                "completed_event": completed_gw,

                # GW4 planning information.
                "planning_event": planning_gw,

                "points": financial_eh.get(
                    "points",
                    completed_eh.get("points", 0),
                ),

                "total_points": financial_eh.get(
                    "total_points",
                    completed_eh.get("total_points", 0),
                ),

                "rank": financial_eh.get(
                    "rank",
                    completed_eh.get("rank", 0),
                ),

                "overall_rank": financial_eh.get(
                    "overall_rank",
                    completed_eh.get(
                        "overall_rank",
                        0,
                    ),
                ),

                "bank": float(
                    bank_raw or 0
                ) / 10,

                "value": float(
                    value_raw or 0
                ) / 10,

                # IMPORTANT:
                # These are transfers made for GW4.
                "event_transfers": planning_transfer_count,

                "event_transfers_cost": int(
                    financial_eh.get(
                        "event_transfers_cost",
                        0,
                    ) or 0
                ),

                "starting_free_transfers": (
                    starting_free_transfers
                ),

                "free_transfers_remaining": (
                    free_transfers_remaining
                ),

                "transfer_hits": transfer_hits,

                "points_on_bench": financial_eh.get(
                    "points_on_bench",
                    0,
                ),
            }

            # =========================================================
            # 2J. Convenience fields
            # =========================================================

            result["current_bank"] = (
                result["gw_summary"]["bank"]
            )

            result["current_team_value"] = (
                result["gw_summary"]["value"]
            )

            result["current_event_transfers"] = (
                planning_transfer_count
            )

            result["current_transfer_cost"] = (
                result["gw_summary"][
                    "event_transfers_cost"
                ]
            )

            # =========================================================
            # 2K. DEBUG
            # =========================================================

            result["debug_fpl"] = {
                "completed_gw": completed_gw,

                "planning_gw": planning_gw,

                "picks_event_returned": picks_event,

                "planning_response_status": (
                    planning_resp.status_code
                ),

                "completed_pick_ids": [
                    p.get("element")
                    for p in completed_pick_list
                ],

                "planning_pick_ids": [
                    p.get("element")
                    for p in planning_pick_list
                ],

                "returned_pick_ids": [
                    p.get("element")
                    for p in result["picks"]
                ],

                "completed_transfers": (
                    completed_transfers
                ),

                "planning_transfers": (
                    planning_transfers
                ),

                "planning_transfer_count": (
                    planning_transfer_count
                ),

                "starting_free_transfers": (
                    starting_free_transfers
                ),

                "free_transfers_remaining": (
                    free_transfers_remaining
                ),

                "bank_raw": bank_raw,

                "value_raw": value_raw,
            }

        except Exception as e:
            result["error"] = (
                f"Could not fetch squad data: {str(e)}"
            )

    return result


def enrich_my_team(team_data: dict, player_map: dict, predictions: list) -> dict:
    """
    Enrich the team data with player details and predictions.
    Merges FPL player data + our prediction data for each pick.
    player_map: {player_id: prediction_dict} from our predictions
    predictions: list of prediction dicts
    """
    # player_map is already keyed by player_id from predictions
    # Each prediction already contains: name, team, position, price, etc.

    enriched_picks = []
    for pick in team_data.get("picks", []):
        pid = pick.get("element")
        # player_map contains our prediction data which has all the fields we need
        pred = player_map.get(pid, {})

        enriched = {
            "player_id": pid,
            "purchase_price": (
                float(pick["purchase_price"]) / 10
                if pick.get("purchase_price") is not None
                else None
            ),
            "selling_price": (
                float(pick["selling_price"]) / 10
                if pick.get("selling_price") is not None
                else None
            ),            
            # From our predictions (which already have the right field names)
            "name": pred.get("name", "Unknown"),
            "full_name": pred.get("full_name", "Unknown"),
            "team": pred.get("team", "???"),
            "team_name": pred.get("team_name", "Unknown"),
            "position": pred.get("position", "???"),
            "position_id": pred.get("position_id", 0),
            "price": pred.get("price", 0),
            "selected_by_percent": pred.get("selected_by_percent", "0"),
            "form": pred.get("form", 0),
            "ppg": pred.get("ppg", 0),
            "total_points": pred.get("total_points", 0),
            "minutes": pred.get("minutes", 0),
            "goals_scored": pred.get("goals_scored", 0),
            "assists": pred.get("assists", 0),
            "clean_sheets": pred.get("clean_sheets", 0),
            "status": pred.get("status", "a"),
            "news": pred.get("news", ""),
            "chance_of_playing": pred.get("chance_of_playing"),
            # Prediction-specific fields
            "predicted_points": pred.get("predicted_points", 0),
            "raw_xpts": pred.get("raw_xpts", 0),
            "confidence": pred.get("confidence", 0),
            "fixture": pred.get("fixture", {}),
            "fixtures": pred.get("fixtures", []),
            "is_dgw": pred.get("is_dgw", False),
            "num_fixtures": pred.get("num_fixtures", 0),
            "team_last5_form": pred.get("team_last5_form", ""),
            "team_season_wr": pred.get("team_season_wr", 0),
            "factors": pred.get("factors", {}),
            "availability": pred.get("availability", {}),
            "starter_quality": pred.get("starter_quality", {}),
            "weighted_rotation": pred.get("weighted_rotation", 0),
            "ict_index": pred.get("ict_index", 0),
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
    fpl_value = team_data.get("gw_summary", {}).get("value")
    if fpl_value is not None:
        team_data["squad_value"] = round(float(fpl_value), 1)
    else:
        team_data["squad_value"] = round(
            sum(
                float(p.get("price", 0) or 0)
                for p in enriched_picks
            ),
            1
        )
        team_data["squad_value"] = round(total_value, 1)
    total_predicted = sum(
        p["predicted_points"] * (2 if p["is_captain"] else 1)
        for p in enriched_picks
        if p["is_starter"]
    )
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
        out_sell_price = out_player.get("selling_price")
        if out_sell_price is None:
            out_sell_price = out_player.get("price", 0)
        budget = float(out_sell_price) + float(bank)

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
                    "selling_price": out_sell_price,
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
                "cost_change": round(best.get("price", 0) - out_sell_price, 1),
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
