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
    Calculate free transfers available for the next Gameweek.

    FPL FT logic:
    - Pre-GW1/preseason transfers are unlimited and are NOT part
      of the normal FT bank.
    - A manager starts GW2 with 1 FT.
    - Unused FTs roll forward.
    - Maximum bank is 5 FTs.
    - Transfers consume the available FT bank.
    - Any transfers beyond the available FT bank are hits.
    - Free Hit and Wildcard transfers do not consume FTs.
    - The FT earned after a GW is effectively the unused FT carried
      into that GW plus the normal 1 FT earned for the following GW,
      capped at 5.

    Examples:

        GW1 preseason/unlimited
            -> GW2 starts with 1 FT

        GW2: use 0
            -> GW3 starts with 2 FT

        GW3: use 0
            -> GW4 starts with 3 FT

        GW4: use 3
            -> GW5 starts with 1 FT

        GW2: use 1
            -> GW3 starts with 1 FT

        GW2: use 0
        GW3: use 1
            -> GW4 starts with 1 FT
    """

    chips = chips or []

    # ------------------------------------------------------------
    # Build chip lookup by GW
    # ------------------------------------------------------------

    chip_by_gw = {}

    for chip in chips:
        if not isinstance(chip, dict):
            continue

        event = chip.get("event")
        if event is None:
            continue

        try:
            event = int(event)
        except (TypeError, ValueError):
            continue

        chip_name = str(
            chip.get("name", "")
        ).strip().lower()

        chip_by_gw[event] = chip_name

    # ------------------------------------------------------------
    # History rows
    # ------------------------------------------------------------

    rows = []

    for row in history or []:
        if not isinstance(row, dict):
            continue

        try:
            gw = int(row.get("event", 0))
        except (TypeError, ValueError):
            continue

        if gw <= 0:
            continue

        rows.append(row)

    rows.sort(
        key=lambda row: int(row.get("event", 0))
    )

    # ------------------------------------------------------------
    # IMPORTANT:
    #
    # GW1 is the preseason/unlimited-transfer starting point.
    #
    #     GW1 -> GW2 = 1 FT
    # ------------------------------------------------------------

    free_transfers = 1

    for row in rows:
        gw = int(row.get("event", 0))

        # GW1 establishes the first normal FT for GW2.
        if gw == 1:
            continue

        transfers = int(
            row.get("event_transfers", 0) or 0
        )

        chip = chip_by_gw.get(gw, "")

        # --------------------------------------------------------
        # Free Hit / Wildcard
        #
        # Transfers during these chips do not consume the FT bank.
        # --------------------------------------------------------

        if chip in {
            "freehit",
            "free_hit",
            "wildcard",
        }:
            transfers = 0

        # --------------------------------------------------------
        # Consume available FTs.
        #
        # Anything above this amount is a hit, but this function
        # only calculates the FT bank.
        # --------------------------------------------------------

        used_free_transfers = min(
            transfers,
            free_transfers,
        )

        free_transfers -= used_free_transfers

        # --------------------------------------------------------
        # Earn one FT for the next GW.
        #
        # Example:
        #
        # entering GW3 = 2
        # use 0
        # -> 3 entering GW4
        #
        # entering GW4 = 3
        # use 3
        # -> 1 entering GW5
        # --------------------------------------------------------

        free_transfers = min(
            5,
            free_transfers + 1,
        )

    return max(
        1,
        min(5, free_transfers),
    )


def fetch_my_team(team_id: int) -> dict:
    """
    Fetch a user's FPL team data using the public FPL API.

    FT semantics:
        starting_free_transfers
            FT available when entering the current planning GW,
            before any transfers made for that GW.

        free_transfers
            FT available entering the NEXT GW after accounting for
            known transfers in the current planning GW.

        free_transfers_remaining
            Same as free_transfers.

        early_transfers_known
            True when the public API gives us enough information
            to know the current GW transfers.
    """

    result = {
        "team_id": team_id,
        "info": {},
        "picks": [],
        "transfers": [],
        "history": [],
        "chips": [],
        "past_seasons": [],
        "free_transfers": 1,
        "starting_free_transfers": 1,
        "free_transfers_remaining": 1,
        "transfer_hits": 0,
        "early_transfers_known": False,
        "planning_picks_available": False,
        "picks_are_planning_gw": False,
        "active_chip": None,
        "auto_subs": [],
        "error": None,
    }

    headers = {"User-Agent": "FPL-Predictor/1.0"}

    # ================================================================
    # 1. TEAM ENTRY
    # ================================================================

    try:
        entry_url = f"{FPL_API_BASE}/entry/{team_id}/"
        entry_resp = requests.get(entry_url, headers=headers, timeout=15)
        entry_resp.raise_for_status()
        entry = entry_resp.json()
    except Exception as e:
        result["error"] = f"Could not fetch team info: {str(e)}"
        return result

    current_event = int(entry.get("current_event") or 0)

    if current_event <= 0:
        result["error"] = "Invalid current FPL gameweek."
        return result

    result["info"] = {
        "name": entry.get("name", "Unknown Team"),
        "player_first_name": entry.get("player_first_name", ""),
        "player_last_name": entry.get("player_last_name", ""),
        "overall_points": entry.get("summary_overall_points", 0),
        "overall_rank": entry.get("summary_overall_rank", 0),
        "gameweek_points": entry.get("summary_event_points", 0),
        "gameweek_rank": entry.get("summary_event_rank", 0),
        "current_event": current_event,
        "total_transfers": entry.get("last_deadline_total_transfers", 0),
        "bank": (entry.get("last_deadline_bank") or 0) / 10,
        "team_value": (entry.get("last_deadline_value") or 0) / 10,
        "started_event": entry.get("started_event", 1),
        "favourite_team": entry.get("favourite_team"),
        "transfers_limit": entry.get("transfers_limit"),
    }

    time.sleep(0.2)

    # ================================================================
    # 2. BOOTSTRAP / GAMEWEEK STATE
    # ================================================================

    events = []

    try:
        bootstrap_url = f"{FPL_API_BASE}/bootstrap-static/"
        bootstrap_resp = requests.get(
            bootstrap_url,
            headers=headers,
            timeout=15,
        )
        bootstrap_resp.raise_for_status()
        bootstrap_data = bootstrap_resp.json()
        events = bootstrap_data.get("events", []) or []
    except Exception as e:
        print(f"[GW] Could not fetch bootstrap events: {e}")

    current_event_data = next(
        (
            event for event in events
            if int(event.get("id", 0)) == current_event
        ),
        None,
    )

    if current_event_data and current_event_data.get("finished", False):
        completed_gw = current_event
    else:
        completed_gw = max(0, current_event - 1)

    planning_gw = completed_gw + 1

    result["info"]["completed_gw"] = completed_gw
    result["info"]["planning_gw"] = planning_gw

    # ================================================================
    # 3. HISTORY / CHIPS
    # ================================================================

    history_rows = []
    chips_used = []
    past_seasons = []

    try:
        history_url = f"{FPL_API_BASE}/entry/{team_id}/history/"
        history_resp = requests.get(
            history_url,
            headers=headers,
            timeout=15,
        )
        history_resp.raise_for_status()
        history_data = history_resp.json()

        history_rows = history_data.get("current", []) or []
        chips_used = history_data.get("chips", []) or []
        past_seasons = history_data.get("past", []) or []

        result["history"] = history_rows
        result["chips"] = chips_used
        result["past_seasons"] = past_seasons

    except Exception as e:
        result["error"] = (
            result["error"]
            or f"Could not fetch team history: {str(e)}"
        )

    # ================================================================
    # 4. FT ENTERING CURRENT PLANNING GW
    # ================================================================

    completed_history = [
        row
        for row in history_rows
        if int(row.get("event", 0) or 0) <= completed_gw
    ]

    starting_free_transfers = calculate_free_transfers(
        completed_history,
        chips_used,
    )

    starting_free_transfers = max(
        0,
        min(5, int(starting_free_transfers or 0)),
    )

    result["starting_free_transfers"] = starting_free_transfers

    # ================================================================
    # 5. TRANSFER HISTORY
    # ================================================================

    all_transfers = []
    transfer_endpoint_ok = False
    transfer_endpoint_status = None

    try:
        transfers_url = f"{FPL_API_BASE}/entry/{team_id}/transfers/"
        transfers_resp = requests.get(
            transfers_url,
            headers=headers,
            timeout=15,
        )

        transfer_endpoint_status = transfers_resp.status_code

        if transfers_resp.ok:
            transfer_data = transfers_resp.json()

            if isinstance(transfer_data, list):
                all_transfers = transfer_data
                transfer_endpoint_ok = True
                result["transfers"] = all_transfers[:20]

    except Exception as e:
        print("[FT] Transfer endpoint failed:", repr(e))

    planning_transfers = []
    completed_transfers = []

    if transfer_endpoint_ok:
        planning_transfers = [
            transfer
            for transfer in all_transfers
            if int(transfer.get("event", 0) or 0) == planning_gw
        ]

        completed_transfers = [
            transfer
            for transfer in all_transfers
            if int(transfer.get("event", 0) or 0) == completed_gw
        ]

    planning_transfer_count = len(planning_transfers)

    # ================================================================
    # 6. DETERMINE WHETHER CURRENT GW TRANSFERS ARE KNOWN
    # ================================================================

    if planning_gw < current_event:
        early_transfers_known = True
    elif transfer_endpoint_ok and planning_transfer_count > 0:
        early_transfers_known = True
    else:
        early_transfers_known = False

    # ================================================================
    # 7. CALCULATE FT ENTERING NEXT GW
    # ================================================================
    #
    # IMPORTANT:
    #
    # starting_free_transfers = FT entering current planning GW.
    #
    # If we know transfers made for the current planning GW:
    #
    #   FT entering next GW =
    #       max(0, starting FT - current GW transfers) + 1
    #
    # Example:
    #
    #   GW4 starts with 3 FT
    #   3 transfers made
    #   3 - 3 = 0
    #   +1 rollover/earned FT
    #   GW5 starts with 1 FT
    #
    # If current GW transfers are NOT known, we cannot safely
    # calculate next GW FT, so we preserve the starting FT.
    #

    if early_transfers_known:
        ft_after_current_gw = max(
            0,
            starting_free_transfers - planning_transfer_count,
        )

        free_transfers_remaining = min(
            5,
            ft_after_current_gw + 1,
        )

        transfer_hits = max(
            0,
            planning_transfer_count - starting_free_transfers,
        )

    else:
        free_transfers_remaining = starting_free_transfers
        transfer_hits = 0

    result["free_transfers"] = free_transfers_remaining
    result["free_transfers_remaining"] = free_transfers_remaining
    result["transfer_hits"] = transfer_hits
    result["early_transfers_known"] = early_transfers_known

    print(
        "[FT DEBUG]",
        {
            "current_event": current_event,
            "completed_gw": completed_gw,
            "planning_gw": planning_gw,
            "starting_free_transfers": starting_free_transfers,
            "transfer_endpoint_ok": transfer_endpoint_ok,
            "all_transfers_count": len(all_transfers),
            "planning_transfers": planning_transfers,
            "planning_transfer_count": planning_transfer_count,
            "ft_after_current_gw": (
                max(
                    0,
                    starting_free_transfers - planning_transfer_count,
                )
                if early_transfers_known
                else None
            ),
            "free_transfers": free_transfers_remaining,
            "free_transfers_remaining": free_transfers_remaining,
            "transfer_hits": transfer_hits,
            "early_transfers_known": early_transfers_known,
        },
    )

    # ================================================================
    # 8. COMPLETED GW PICKS
    # ================================================================

    completed_pick_list = []
    completed_entry_history = {}
    completed_picks_status = None

    try:
        completed_url = (
            f"{FPL_API_BASE}/entry/{team_id}/event/{completed_gw}/picks/"
        )

        completed_resp = requests.get(
            completed_url,
            headers=headers,
            timeout=15,
        )

        completed_picks_status = completed_resp.status_code
        completed_resp.raise_for_status()

        completed_data = completed_resp.json()
        completed_pick_list = completed_data.get("picks", []) or []
        completed_entry_history = (
            completed_data.get("entry_history", {}) or {}
        )

    except Exception as e:
        completed_data = {}
        result["error"] = (
            result["error"]
            or f"Could not fetch completed GW picks: {str(e)}"
        )

    time.sleep(0.2)

    # ================================================================
    # 9. PLANNING GW PICKS
    # ================================================================

    planning_pick_list = []
    planning_entry_history = {}
    planning_picks_status = None
    planning_picks_available = False
    planning_picks_data = {}

    try:
        planning_url = (
            f"{FPL_API_BASE}/entry/{team_id}/event/{planning_gw}/picks/"
        )

        planning_resp = requests.get(
            planning_url,
            headers=headers,
            timeout=15,
        )

        planning_picks_status = planning_resp.status_code

        if planning_resp.ok:
            planning_picks_data = planning_resp.json() or {}

            planning_pick_list = (
                planning_picks_data.get("picks", []) or []
            )

            planning_entry_history = (
                planning_picks_data.get("entry_history", {}) or {}
            )

            planning_picks_available = (
                len(planning_pick_list) >= 15
            )

    except Exception as e:
        print("[PICKS] Planning picks unavailable:", repr(e))

    result["planning_picks_available"] = planning_picks_available

    # ================================================================
    # 10. FREE HIT DETECTION
    # ================================================================

    free_hit_gw = None

    for chip in chips_used:
        if not isinstance(chip, dict):
            continue

        chip_name = str(chip.get("name", "")).lower()

        try:
            chip_event = int(chip.get("event"))
        except (TypeError, ValueError):
            continue

        if chip_name == "freehit" and chip_event == completed_gw:
            free_hit_gw = chip_event
            break

    # ================================================================
    # 11. SELECT SQUAD
    # ================================================================

    picks_event = None

    if free_hit_gw is not None and completed_gw > 1:
        revert_gw = completed_gw - 1
        revert_picks = []

        try:
            revert_url = (
                f"{FPL_API_BASE}/entry/{team_id}/event/{revert_gw}/picks/"
            )

            revert_resp = requests.get(
                revert_url,
                headers=headers,
                timeout=15,
            )

            if revert_resp.ok:
                revert_data = revert_resp.json()
                revert_picks = revert_data.get("picks", []) or []

        except Exception:
            revert_picks = []

        if len(revert_picks) >= 15:
            result["picks"] = revert_picks
            result["active_chip"] = None
            result["auto_subs"] = []
            picks_event = revert_gw
            result["fh_revert_from"] = completed_gw
            result["fh_reverted_to"] = revert_gw

        elif planning_picks_available:
            result["picks"] = planning_pick_list
            result["active_chip"] = planning_picks_data.get("active_chip")
            result["auto_subs"] = planning_picks_data.get(
                "automatic_subs",
                [],
            )
            picks_event = planning_gw
            result["picks_are_planning_gw"] = True

        else:
            result["picks"] = completed_pick_list
            result["active_chip"] = None
            result["auto_subs"] = []
            picks_event = completed_gw

    elif planning_picks_available:
        result["picks"] = planning_pick_list
        result["active_chip"] = planning_picks_data.get("active_chip")
        result["auto_subs"] = planning_picks_data.get(
            "automatic_subs",
            [],
        )
        picks_event = planning_gw
        result["picks_are_planning_gw"] = True

    else:
        result["picks"] = completed_pick_list
        result["active_chip"] = completed_data.get("active_chip")
        result["auto_subs"] = completed_data.get(
            "automatic_subs",
            [],
        )
        picks_event = completed_gw
        result["picks_are_planning_gw"] = False

    # ================================================================
    # 12. FINANCIAL STATE
    # ================================================================

    financial_eh = completed_entry_history

    if planning_picks_available and planning_entry_history:
        if (
            planning_entry_history.get("bank") is not None
            or planning_entry_history.get("value") is not None
        ):
            financial_eh = planning_entry_history

    bank_raw = financial_eh.get("bank")
    value_raw = financial_eh.get("value")

    if bank_raw is None:
        bank_raw = entry.get("last_deadline_bank", 0)

    if value_raw is None:
        value_raw = entry.get("last_deadline_value", 0)

    # ================================================================
    # 13. GW SUMMARY
    # ================================================================

    result["gw_summary"] = {
        "event": planning_gw,
        "completed_event": completed_gw,
        "planning_event": planning_gw,
        "points": financial_eh.get(
            "points",
            completed_entry_history.get("points", 0),
        ),
        "total_points": financial_eh.get(
            "total_points",
            completed_entry_history.get("total_points", 0),
        ),
        "rank": financial_eh.get(
            "rank",
            completed_entry_history.get("rank", 0),
        ),
        "overall_rank": financial_eh.get(
            "overall_rank",
            completed_entry_history.get("overall_rank", 0),
        ),
        "bank": float(bank_raw or 0) / 10,
        "value": float(value_raw or 0) / 10,
        "event_transfers": (
            planning_transfer_count
            if early_transfers_known
            else None
        ),
        "event_transfers_cost": int(
            financial_eh.get("event_transfers_cost", 0) or 0
        ),
        "starting_free_transfers": starting_free_transfers,
        "free_transfers_remaining": free_transfers_remaining,
        "transfer_hits": transfer_hits,
        "points_on_bench": financial_eh.get(
            "points_on_bench",
            0,
        ),
        "early_transfers_known": early_transfers_known,
        "planning_picks_available": planning_picks_available,
    }

    # ================================================================
    # 14. CONVENIENCE FIELDS
    # ================================================================

    result["current_bank"] = result["gw_summary"]["bank"]
    result["current_team_value"] = result["gw_summary"]["value"]

    result["current_event_transfers"] = (
        planning_transfer_count
        if early_transfers_known
        else None
    )

    result["current_transfer_cost"] = result["gw_summary"][
        "event_transfers_cost"
    ]

    # ================================================================
    # 15. DEBUG
    # ================================================================

    result["debug_fpl"] = {
        "current_event_from_entry": current_event,
        "completed_gw": completed_gw,
        "planning_gw": planning_gw,
        "completed_picks_status": completed_picks_status,
        "planning_picks_status": planning_picks_status,
        "planning_picks_available": planning_picks_available,
        "planning_pick_count": len(planning_pick_list),
        "completed_pick_count": len(completed_pick_list),
        "picks_event_returned": picks_event,
        "picks_are_planning_gw": result["picks_are_planning_gw"],
        "transfer_endpoint_status": transfer_endpoint_status,
        "all_transfers_count": len(all_transfers),
        "planning_transfer_count": planning_transfer_count,
        "early_transfers_known": early_transfers_known,
        "starting_free_transfers": starting_free_transfers,
        "free_transfers": free_transfers_remaining,
        "free_transfers_remaining": free_transfers_remaining,
        "transfer_hits": transfer_hits,
        "free_hit_gw": free_hit_gw,
    }

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
