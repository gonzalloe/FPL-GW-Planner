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
    Calculate free transfers available at the start of the next GW.
    Start with 1 FT.
    Unused FT rolls forward, capped at 5.
    Transfers made during a GW consume available FT.
    """
    chips = chips or []

    chip_by_gw = {}
    for chip in chips:
        if not isinstance(chip, dict):
            continue

        event = chip.get("event")
        if event is None:
            continue

        chip_by_gw[int(event)] = str(
            chip.get("name", "")
        ).lower()

    free_transfers = 1

    rows = sorted(
        history or [],
        key=lambda row: int(row.get("event", 0))
    )

    for row in rows:
        gw = int(row.get("event", 0))
        transfers = int(
            row.get("event_transfers", 0) or 0
        )

        chip = chip_by_gw.get(gw)

        # Free Hit / Wildcard do not consume FTs.
        if chip in ("freehit", "wildcard"):
            transfers = 0

        # Consume available FT with transfers made in this GW.
        used_free = min(
            transfers,
            free_transfers
        )

        free_transfers -= used_free

        # One new FT is added for the next GW.
        free_transfers = min(
            5,
            free_transfers + 1
        )

    return max(
        1,
        min(5, free_transfers)
    )


def fetch_my_team(team_id: int) -> dict:
    """
    Fetch a user's FPL team data.

    Handles:
    - Current/completed GW detection
    - Early transfers made for the upcoming GW
    - Correct upcoming-GW squad selection
    - Free Transfer rollover
    - Free Transfers already spent on the upcoming GW
    - Transfer hits
    - Free Hit squad reversion
    - Wildcard / Free Hit FT preservation
    - GW financial summary
    - Transfer history
    - Chips and season history

    Important distinction:
        completed_gw = latest finished GW
        planning_gw  = GW currently being planned

    The UI should use:
        result["free_transfers"]
    as the AVAILABLE FT for the planning GW.
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
        "error": None,
    }

    headers = {
        "User-Agent": "FPL-Predictor/1.0"
    }

    # ================================================================
    # 1. FETCH TEAM ENTRY
    # ================================================================

    try:
        url = f"{FPL_API_BASE}/entry/{team_id}/"

        resp = requests.get(
            url,
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()

        entry = resp.json()

        current_event = int(
            entry.get("current_event") or 0
        )

        result["info"] = {
            "name": entry.get(
                "name",
                "Unknown Team",
            ),
            "player_first_name": entry.get(
                "player_first_name",
                "",
            ),
            "player_last_name": entry.get(
                "player_last_name",
                "",
            ),
            "overall_points": entry.get(
                "summary_overall_points",
                0,
            ),
            "overall_rank": entry.get(
                "summary_overall_rank",
                0,
            ),
            "gameweek_points": entry.get(
                "summary_event_points",
                0,
            ),
            "gameweek_rank": entry.get(
                "summary_event_rank",
                0,
            ),
            "current_event": current_event,
            "total_transfers": entry.get(
                "last_deadline_total_transfers",
                0,
            ),
            "bank": (
                entry.get("last_deadline_bank") or 0
            ) / 10,
            "team_value": (
                entry.get("last_deadline_value") or 0
            ) / 10,
            "started_event": entry.get(
                "started_event",
                1,
            ),
            "favourite_team": entry.get(
                "favourite_team"
            ),
            "transfers_limit": entry.get(
                "transfers_limit"
            ),
        }

    except Exception as e:
        result["error"] = (
            f"Could not fetch team info: {str(e)}"
        )
        return result

    if not current_event:
        result["error"] = "Invalid current FPL gameweek."
        return result

    time.sleep(0.2)

    # ================================================================
    # 2. FETCH BOOTSTRAP TO DETERMINE GW STATE
    # ================================================================

    try:
        bootstrap_url = (
            f"{FPL_API_BASE}/bootstrap-static/"
        )

        bootstrap_resp = requests.get(
            bootstrap_url,
            headers=headers,
            timeout=15,
        )
        bootstrap_resp.raise_for_status()

        bootstrap_data = bootstrap_resp.json()
        events = bootstrap_data.get("events", [])

    except Exception:
        events = []

    # ------------------------------------------------
    # Determine latest finished GW.
    #
    # Do NOT blindly assume entry.current_event is
    # the planning GW.
    # ------------------------------------------------

    completed_gw = current_event

    current_event_data = next(
        (
            e for e in events
            if int(e.get("id", 0)) == current_event
        ),
        None,
    )

    if current_event_data:
        if not current_event_data.get("finished", False):
            # Current GW is still active.
            completed_gw = max(
                0,
                current_event - 1,
            )

    # Find the highest explicitly finished GW.
    finished_events = [
        int(e.get("id", 0))
        for e in events
        if e.get("finished", False)
    ]

    if finished_events:
        completed_gw = max(finished_events)

    planning_gw = completed_gw + 1

    result["info"]["completed_gw"] = completed_gw
    result["info"]["planning_gw"] = planning_gw

    # ================================================================
    # 3. FETCH HISTORY + CHIPS
    # ================================================================

    history_rows = []
    chips_used = []
    past_seasons = []

    try:
        history_url = (
            f"{FPL_API_BASE}/entry/"
            f"{team_id}/history/"
        )

        hist_resp = requests.get(
            history_url,
            headers=headers,
            timeout=15,
        )
        hist_resp.raise_for_status()

        hist_data = hist_resp.json()

        history_rows = hist_data.get(
            "current",
            []
        )

        chips_used = hist_data.get(
            "chips",
            []
        )

        past_seasons = hist_data.get(
            "past",
            []
        )

        result["history"] = history_rows
        result["chips"] = chips_used
        result["past_seasons"] = past_seasons

    except Exception as e:
        result["error"] = (
            f"Could not fetch team history: {str(e)}"
        )

    time.sleep(0.2)

    # ================================================================
    # 4. FETCH TRANSFER HISTORY
    #
    # This MUST happen independently of entry_history.
    #
    # Why?
    #
    # A transfer made early for GW4 is not necessarily represented
    # in GW3's entry_history.
    # ================================================================

    all_transfers = []

    try:
        transfers_url = (
            f"{FPL_API_BASE}/entry/"
            f"{team_id}/transfers/"
        )

        transfers_resp = requests.get(
            transfers_url,
            headers=headers,
            timeout=15,
        )
        transfers_resp.raise_for_status()

        transfer_data = transfers_resp.json()

        if isinstance(transfer_data, list):
            all_transfers = transfer_data

        result["transfers"] = all_transfers[:20]

    except Exception:
        all_transfers = []

    # ================================================================
    # 5. CALCULATE FT ENTERING PLANNING GW
    #
    # Example:
    #
    # GW1: 1 FT, use 0
    # GW2: 2 FT, use 0
    # GW3: 3 FT, use 0
    #
    # Entering GW4 = 4 FT
    #
    # This is the FT bank BEFORE GW4 early transfers.
    # ================================================================

    completed_history = [
        row
        for row in history_rows
        if int(row.get("event", 0)) <= completed_gw
    ]

    starting_free_transfers = calculate_free_transfers(
        completed_history,
        chips_used,
    )

    # ================================================================
    # 6. IDENTIFY TRANSFERS FOR COMPLETED + PLANNING GW
    # ================================================================

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

    planning_transfer_count = len(
        planning_transfers
    )

    # ================================================================
    # 7. CALCULATE REMAINING FT + HITS
    #
    # This is the value the UI should display.
    #
    # Example:
    #   starting = 4
    #   used     = 3
    #   remaining = 1
    #
    # Example:
    #   starting = 2
    #   used     = 3
    #   remaining = 0
    #   hits     = 1
    #
    # NOTE:
    # Wildcard / Free Hit transfers are handled separately by
    # calculate_free_transfers() for completed GWs.
    # Early planning transfers are actual transfers for planning_gw.
    # ================================================================

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

    # ================================================================
    # 8. IMPORTANT:
    # result["free_transfers"] MUST BE REMAINING FT
    #
    # NOT starting_free_transfers.
    #
    # This fixes the UI showing 4 after 3 early transfers.
    # ================================================================

    result["free_transfers"] = (
        free_transfers_remaining
    )

    # Also expose both values explicitly so other code does not
    # have to guess what "free_transfers" means.
    result["starting_free_transfers"] = (
        starting_free_transfers
    )

    result["free_transfers_remaining"] = (
        free_transfers_remaining
    )

    result["transfer_hits"] = transfer_hits

    # ================================================================
    # 9. FETCH COMPLETED GW PICKS
    #
    # This is always useful as a fallback/baseline.
    # ================================================================

    completed_pick_list = []
    completed_eh = {}

    try:
        completed_url = (
            f"{FPL_API_BASE}/entry/"
            f"{team_id}/event/"
            f"{completed_gw}/picks/"
        )

        completed_resp = requests.get(
            completed_url,
            headers=headers,
            timeout=15,
        )
        completed_resp.raise_for_status()

        completed_picks_data = (
            completed_resp.json()
        )

        completed_pick_list = (
            completed_picks_data.get(
                "picks",
                []
            )
        )

        completed_eh = (
            completed_picks_data.get(
                "entry_history",
                {}
            )
        )

    except Exception as e:
        result["error"] = (
            result["error"]
            or
            f"Could not fetch completed GW picks: {str(e)}"
        )

    time.sleep(0.2)

    # ================================================================
    # 10. FETCH PLANNING GW PICKS
    #
    # This is the critical part for early transfers.
    #
    # If GW3 has finished and GW4 transfers have already been made,
    # GW4 picks should be the squad displayed by the planner.
    #
    # We do NOT require planning_transfer_count > 0.
    #
    # Even with zero transfers, GW4 picks are still the correct
    # planning squad.
    # ================================================================

    planning_pick_list = []
    planning_eh = {}
    planning_resp_status = None
    planning_success = False

    try:
        planning_url = (
            f"{FPL_API_BASE}/entry/"
            f"{team_id}/event/{planning_gw}/picks/"
        )

        planning_resp = requests.get(
            planning_url,
            headers=headers,
            timeout=15,
        )

        planning_resp_status = (
            planning_resp.status_code
        )

        if planning_resp.ok:
            planning_picks_data = (
                planning_resp.json()
            )

            planning_pick_list = (
                planning_picks_data.get(
                    "picks",
                    []
                )
            )

            planning_eh = (
                planning_picks_data.get(
                    "entry_history",
                    {}
                )
            )

            # A valid FPL picks response should contain 15 picks.
            # Treat an empty/incomplete future response as unusable.
            if len(planning_pick_list) >= 15:
                planning_success = True

    except Exception:
        planning_success = False

    # ================================================================
    # 11. FREE HIT HANDLING
    #
    # Free Hit means:
    #
    # GW N = temporary FH squad
    # GW N+1 = original squad restored
    #
    # Therefore, if the latest completed GW used Free Hit,
    # the actual squad entering the planning GW is the squad
    # from the GW before the Free Hit.
    #
    # IMPORTANT:
    # Do NOT apply this rule to Wildcard.
    # Wildcard permanently changes the squad.
    # ================================================================

    free_hit_gw = None

    for chip in chips_used:
        if not isinstance(chip, dict):
            continue

        chip_name = str(
            chip.get("name", "")
        ).lower()

        chip_event = chip.get("event")

        try:
            chip_event = int(chip_event)
        except (TypeError, ValueError):
            continue

        if (
            chip_name == "freehit"
            and chip_event == completed_gw
        ):
            free_hit_gw = chip_event
            break

    if free_hit_gw is not None:
        # The actual squad entering the next GW is the squad
        # from before the Free Hit.
        revert_gw = max(
            1,
            completed_gw - 1,
        )

        try:
            revert_url = (
                f"{FPL_API_BASE}/entry/"
                f"{team_id}/event/{revert_gw}/picks/"
            )

            revert_resp = requests.get(
                revert_url,
                headers=headers,
                timeout=15,
            )

            if revert_resp.ok:
                revert_data = revert_resp.json()

                revert_picks = revert_data.get(
                    "picks",
                    []
                )

                if len(revert_picks) >= 15:
                    # Free Hit squad must NOT be displayed.
                    result["picks"] = revert_picks
                    result["active_chip"] = None
                    result["auto_subs"] = []

                    picks_event = revert_gw

                    result["fh_revert_from"] = (
                        completed_gw
                    )
                    result["fh_reverted_to"] = (
                        revert_gw
                    )

                else:
                    # Fall back to normal planning logic.
                    if planning_success:
                        result["picks"] = (
                            planning_pick_list
                        )
                        result["active_chip"] = (
                            planning_picks_data.get(
                                "active_chip"
                            )
                        )
                        result["auto_subs"] = (
                            planning_picks_data.get(
                                "automatic_subs",
                                []
                            )
                        )
                        picks_event = planning_gw
                    else:
                        result["picks"] = (
                            completed_pick_list
                        )
                        result["active_chip"] = (
                            completed_picks_data.get(
                                "active_chip"
                            )
                        )
                        result["auto_subs"] = (
                            completed_picks_data.get(
                                "automatic_subs",
                                []
                            )
                        )
                        picks_event = completed_gw

            else:
                if planning_success:
                    result["picks"] = (
                        planning_pick_list
                    )
                    result["active_chip"] = (
                        planning_picks_data.get(
                            "active_chip"
                        )
                    )
                    result["auto_subs"] = (
                        planning_picks_data.get(
                            "automatic_subs",
                            []
                        )
                    )
                    picks_event = planning_gw
                else:
                    result["picks"] = (
                        completed_pick_list
                    )
                    result["active_chip"] = (
                        completed_picks_data.get(
                            "active_chip"
                        )
                    )
                    result["auto_subs"] = (
                        completed_picks_data.get(
                            "automatic_subs",
                            []
                        )
                    )
                    picks_event = completed_gw

        except Exception:
            # Safe fallback.
            if planning_success:
                result["picks"] = (
                    planning_pick_list
                )
                result["active_chip"] = (
                    planning_picks_data.get(
                        "active_chip"
                    )
                )
                result["auto_subs"] = (
                    planning_picks_data.get(
                        "automatic_subs",
                        []
                    )
                )
                picks_event = planning_gw
            else:
                result["picks"] = (
                    completed_pick_list
                )
                result["active_chip"] = (
                    completed_picks_data.get(
                        "active_chip"
                    )
                )
                result["auto_subs"] = (
                    completed_picks_data.get(
                        "automatic_subs",
                        []
                    )
                )
                picks_event = completed_gw

    else:
        # ============================================================
        # NORMAL CASE
        #
        # Use planning GW picks whenever available.
        # This handles:
        #
        # 0 early transfers
        # 1 early transfer
        # multiple early transfers
        # wildcard
        # normal rollover
        # ============================================================

        if planning_success:
            result["picks"] = (
                planning_pick_list
            )

            result["active_chip"] = (
                planning_picks_data.get(
                    "active_chip"
                )
            )

            result["auto_subs"] = (
                planning_picks_data.get(
                    "automatic_subs",
                    []
                )
            )

            picks_event = planning_gw

        else:
            # Future picks may not exist yet.
            # Use the completed GW as the safe fallback.
            result["picks"] = (
                completed_pick_list
            )

            result["active_chip"] = (
                completed_picks_data.get(
                    "active_chip"
                )
            )

            result["auto_subs"] = (
                completed_picks_data.get(
                    "automatic_subs",
                    []
                )
            )

            picks_event = completed_gw

    # ================================================================
    # 12. FINANCIAL STATE
    #
    # For the planning GW:
    #
    # - Future picks normally don't have a meaningful completed
    #   entry_history.
    # - Therefore, use the latest completed GW financial state.
    #
    # The important early-transfer information comes from the
    # transfer-history endpoint, not entry_history.
    # ================================================================

    financial_eh = completed_eh

    if planning_success and planning_eh:
        # Only use planning entry_history if it actually contains
        # meaningful financial fields.
        if (
            planning_eh.get("bank") is not None
            or
            planning_eh.get("value") is not None
        ):
            financial_eh = planning_eh

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

    # ================================================================
    # 13. BUILD GW SUMMARY
    # ================================================================

    result["gw_summary"] = {
        # GW being planned.
        "event": planning_gw,

        # Last completed GW.
        "completed_event": completed_gw,

        # Explicit planning GW.
        "planning_event": planning_gw,

        # Latest completed GW points.
        "points": financial_eh.get(
            "points",
            completed_eh.get(
                "points",
                0,
            ),
        ),

        "total_points": financial_eh.get(
            "total_points",
            completed_eh.get(
                "total_points",
                0,
            ),
        ),

        "rank": financial_eh.get(
            "rank",
            completed_eh.get(
                "rank",
                0,
            ),
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

        # Transfers made for the planning GW.
        "event_transfers": (
            planning_transfer_count
        ),

        "event_transfers_cost": int(
            financial_eh.get(
                "event_transfers_cost",
                0,
            ) or 0
        ),

        # FT before early planning transfers.
        "starting_free_transfers": (
            starting_free_transfers
        ),

        # FT remaining after early planning transfers.
        "free_transfers_remaining": (
            free_transfers_remaining
        ),

        # Number of transfers beyond available FT.
        "transfer_hits": (
            transfer_hits
        ),

        "points_on_bench": financial_eh.get(
            "points_on_bench",
            0,
        ),
    }

    # ================================================================
    # 14. CONVENIENCE FIELDS
    # ================================================================

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

    # ================================================================
    # 15. DEBUG DATA
    #
    # This is intentionally verbose so you can inspect exactly
    # what FPL returned in Network -> /api/my-team.
    # ================================================================

    result["debug_fpl"] = {
        "current_event_from_entry": current_event,

        "completed_gw": completed_gw,

        "planning_gw": planning_gw,

        "picks_event_returned": picks_event,

        "planning_response_status": (
            planning_resp_status
        ),

        "planning_success": (
            planning_success
        ),

        "completed_pick_count": (
            len(completed_pick_list)
        ),

        "planning_pick_count": (
            len(planning_pick_list)
        ),

        "returned_pick_count": (
            len(result.get("picks", []))
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
            for p in result.get(
                "picks",
                []
            )
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

        "transfer_hits": (
            transfer_hits
        ),

        "free_hit_gw": free_hit_gw,

        "bank_raw": bank_raw,

        "value_raw": value_raw,
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
