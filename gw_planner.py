"""
FPL Predictor - Gameweek Planner
Multi-GW transfer planning with rolling squad state, fixture ticker,
and optimal transfer recommendations for future gameweeks.
"""
import copy
from config import (
    SQUAD_BUDGET, MAX_PER_TEAM, POSITION_LIMITS,
    FDR_MULTIPLIER, CHIPS
)
from data_fetcher import (
    fetch_bootstrap, fetch_fixtures, build_player_map, build_team_map,
    get_player_fixtures, get_dgw_teams, get_bgw_teams,
    get_next_gameweek, get_current_gameweek, get_fixtures_for_gameweek
)
from prediction_engine import PredictionEngine
from team_analysis import build_team_stats, get_fixture_xg, calc_team_momentum


class GWPlanner:
    """
    Plans transfers across multiple future gameweeks.
    Takes the user's current squad and simulates optimal transfers
    week by week, tracking budget, free transfers, and chip usage.
    """

    def __init__(self, horizon: int = 5):
        """
        Args:
            horizon: How many GWs ahead to plan (default 5)
        """
        self.engine = PredictionEngine()
        self.bootstrap = self.engine.bootstrap
        self.fixtures = self.engine.fixtures
        self.players = self.engine.players
        self.teams = self.engine.teams
        self.team_stats = self.engine.team_stats
        self.current_gw = self.engine.current_gw
        self.next_gw = self.engine.next_gw
        self.horizon = min(horizon, 38 - self.next_gw + 1)

        # Pre-compute predictions for each GW in the planning horizon
        self._gw_predictions = {}
        self._gw_infos = {}

    def _ensure_gw_predictions(self, gw: int):
        """Lazy-load predictions for a gameweek."""
        if gw not in self._gw_predictions:
            preds = self.engine.predict_all(gw)
            self._gw_predictions[gw] = preds
            self._gw_infos[gw] = self.engine.get_gw_info(gw)

    def _get_pred_map(self, gw: int) -> dict:
        """Get {player_id: prediction} map for a GW."""
        self._ensure_gw_predictions(gw)
        return {p["player_id"]: p for p in self._gw_predictions[gw]}

    # ── Fixture Ticker ────────────────────────────────────────

    def build_fixture_ticker(self, team_ids: list[int] | None = None) -> dict:
        """
        Build a fixture ticker showing each team's fixtures for the planning horizon.
        Returns {team_id: [{gw, opponents: [{name, fdr, is_home, is_dgw}]}]}
        """
        if team_ids is None:
            team_ids = list(self.teams.keys())

        ticker = {}
        for tid in team_ids:
            team_fixtures = []
            for gw in range(self.next_gw, self.next_gw + self.horizon):
                if gw > 38:
                    break
                fixes = get_player_fixtures(tid, gw, self.fixtures)
                opponents = []
                for f in fixes:
                    opp = self.teams.get(f["opponent_id"], {})
                    opponents.append({
                        "team_id": f["opponent_id"],
                        "name": opp.get("short_name", "???"),
                        "full_name": opp.get("name", "Unknown"),
                        "fdr": f["fdr"],
                        "is_home": f["is_home"],
                        "venue": "H" if f["is_home"] else "A",
                    })
                dgw_teams = get_dgw_teams(gw, self.fixtures)
                team_fixtures.append({
                    "gw": gw,
                    "opponents": opponents,
                    "is_dgw": tid in dgw_teams,
                    "is_blank": len(opponents) == 0,
                    "fixture_count": len(opponents),
                    "avg_fdr": round(sum(o["fdr"] for o in opponents) / max(len(opponents), 1), 1),
                })
            ticker[tid] = {
                "team_name": self.teams.get(tid, {}).get("name", "Unknown"),
                "short_name": self.teams.get(tid, {}).get("short_name", "???"),
                "fixtures": team_fixtures,
            }
        return ticker

    def build_player_fixture_ticker(self, player_ids: list[int]) -> dict:
        """Build fixture ticker for specific players across the horizon."""
        result = {}
        for pid in player_ids:
            p = self.players.get(pid)
            if not p:
                continue
            tid = p["team"]
            team_ticker = self.build_fixture_ticker([tid]).get(tid, {})
            gw_xpts = {}
            for gw in range(self.next_gw, self.next_gw + self.horizon):
                if gw > 38:
                    break
                pred_map = self._get_pred_map(gw)
                pred = pred_map.get(pid, {})
                gw_xpts[gw] = round(pred.get("predicted_points", 0), 2)

            result[pid] = {
                "name": p.get("web_name", "Unknown"),
                "team": p.get("team_short", "???"),
                "team_id": tid,
                "position": p.get("position_name", "???"),
                "price": p.get("now_cost", 0) / 10,
                "fixtures": team_ticker.get("fixtures", []),
                "gw_xpts": gw_xpts,
                "total_xpts": round(sum(gw_xpts.values()), 2),
            }
        return result

    # ── FDR Difficulty Score for Planning ─────────────────────

    def calc_fixture_run_score(self, team_id: int, from_gw: int = None,
                                num_gws: int = None) -> dict:
        """
        Calculate how good a team's fixture run is over num_gws.
        Lower FDR sum = easier run. Also counts DGWs and blanks.
        """
        if from_gw is None:
            from_gw = self.next_gw
        if num_gws is None:
            num_gws = self.horizon

        total_fdr = 0
        num_fixtures = 0
        dgw_count = 0
        blank_count = 0

        for gw in range(from_gw, min(from_gw + num_gws, 39)):
            fixes = get_player_fixtures(team_id, gw, self.fixtures)
            if not fixes:
                blank_count += 1
                total_fdr += 5  # Blank = worst possible
            else:
                for f in fixes:
                    total_fdr += f["fdr"]
                    num_fixtures += 1
                if len(fixes) >= 2:
                    dgw_count += 1

        avg_fdr = total_fdr / max(num_fixtures + blank_count, 1)

        return {
            "team_id": team_id,
            "team_name": self.teams.get(team_id, {}).get("name", "???"),
            "short_name": self.teams.get(team_id, {}).get("short_name", "???"),
            "total_fdr": total_fdr,
            "avg_fdr": round(avg_fdr, 2),
            "num_fixtures": num_fixtures,
            "dgw_count": dgw_count,
            "blank_count": blank_count,
            "difficulty_rating": self._fdr_to_rating(avg_fdr),
        }

    def _fdr_to_rating(self, avg_fdr: float) -> str:
        if avg_fdr <= 2.2:
            return "excellent"
        elif avg_fdr <= 2.8:
            return "good"
        elif avg_fdr <= 3.2:
            return "average"
        elif avg_fdr <= 3.8:
            return "tough"
        else:
            return "very_tough"

    def rank_teams_by_fixtures(self, from_gw: int = None,
                                num_gws: int = None) -> list[dict]:
        """Rank all 20 teams by how easy their fixture run is."""
        scores = []
        for tid in self.teams:
            score = self.calc_fixture_run_score(tid, from_gw, num_gws)
            scores.append(score)
        scores.sort(key=lambda x: x["avg_fdr"])
        return scores

    # ── Multi-GW Transfer Planner ─────────────────────────────

    def plan_transfers(self, current_squad_ids: list[int],
                       bank: float = 0.0,
                       free_transfers: int = 1,
                       chips_available: list[str] | None = None,
                       max_transfers_per_gw: int = 2) -> dict:
        """
        Plan optimal transfers across the planning horizon.

        Args:
            current_squad_ids: List of 15 player IDs in current squad
            bank: Current money in bank (millions)
            free_transfers: Free transfers available for next GW
            chips_available: List of chip codes still available (WC, FH, BB, TC)
            max_transfers_per_gw: Max transfers to suggest per GW (incl. hits)

        Returns:
            Full plan with GW-by-GW transfer recommendations
        """
        if chips_available is None:
            chips_available = ["WC", "FH", "BB", "TC"]

        # Build rolling state
        squad = list(current_squad_ids)
        rolling_bank = bank
        rolling_ft = free_transfers
        remaining_chips = set(chips_available)

        gw_plans = []

        for gw in range(self.next_gw, self.next_gw + self.horizon):
            if gw > 38:
                break

            pred_map = self._get_pred_map(gw)
            gw_info = self._gw_infos.get(gw, {})

            # Enrich current squad for this GW
            squad_enriched = []
            for pid in squad:
                p = self.players.get(pid, {})
                pred = pred_map.get(pid, {})
                squad_enriched.append({
                    "player_id": pid,
                    "name": p.get("web_name", "Unknown"),
                    "team": p.get("team_short", "???"),
                    "team_id": p.get("team", 0),
                    "position": p.get("position_name", "???"),
                    "position_id": p.get("element_type", 0),
                    "price": p.get("now_cost", 0) / 10,
                    "predicted_points": pred.get("predicted_points", 0),
                    "raw_xpts": pred.get("raw_xpts", 0),
                    "is_dgw": pred.get("is_dgw", False),
                    "num_fixtures": pred.get("num_fixtures", 0),
                    "form": float(p.get("form", 0)),
                    "status": p.get("status", "a"),
                    "chance_of_playing": p.get("chance_of_playing_next_round"),
                    "news": p.get("news", ""),
                    "fixtures": pred.get("fixtures", []),
                    "starter_quality": pred.get("starter_quality", {}),
                    "availability": pred.get("availability", {}),
                })

            # Calculate total squad xPts for this GW (no transfers)
            baseline_xpts = sum(
                p["predicted_points"] for p in
                sorted(squad_enriched, key=lambda x: x["predicted_points"], reverse=True)[:11]
            )

            # Find best transfers for this GW
            transfers = self._find_best_transfers(
                squad_enriched, pred_map, rolling_bank,
                rolling_ft, max_transfers_per_gw, gw
            )

            # Chip recommendation for this GW
            chip_rec = self._recommend_chip(
                squad_enriched, gw_info, gw, remaining_chips, pred_map
            )

            # Calculate post-transfer squad xPts
            post_squad = list(squad)
            transfer_cost = 0
            net_spend = 0.0
            for t in transfers:
                if t["out_id"] in post_squad:
                    post_squad.remove(t["out_id"])
                    post_squad.append(t["in_id"])
                    net_spend += t["in_price"] - t["out_price"]

            # Transfer cost (hits)
            num_transfers = len(transfers)
            free_used = min(num_transfers, rolling_ft)
            hits = max(0, num_transfers - rolling_ft)
            transfer_cost = hits * 4

            # Post-transfer predictions
            post_enriched = []
            for pid in post_squad:
                pred = pred_map.get(pid, {})
                post_enriched.append(pred)
            post_xpts = sum(
                p.get("predicted_points", 0) for p in
                sorted(post_enriched, key=lambda x: x.get("predicted_points", 0), reverse=True)[:11]
            ) - transfer_cost

            # Calculate multi-GW value of transfers
            multi_gw_value = self._calc_multi_gw_transfer_value(
                transfers, gw
            )

            gw_plan = {
                "gameweek": gw,
                "gw_info": gw_info,
                "squad_before": squad_enriched,
                "baseline_xpts": round(baseline_xpts, 1),
                "transfers": transfers,
                "num_transfers": num_transfers,
                "free_transfers_available": rolling_ft,
                "free_transfers_used": free_used,
                "hits": hits,
                "hit_cost": transfer_cost,
                "net_spend": round(net_spend, 1),
                "bank_before": round(rolling_bank, 1),
                "bank_after": round(rolling_bank - net_spend, 1),
                "post_transfer_xpts": round(post_xpts, 1),
                "xpts_gain": round(post_xpts - baseline_xpts, 1),
                "multi_gw_value": multi_gw_value,
                "chip_recommendation": chip_rec,
            }
            gw_plans.append(gw_plan)

            # Update rolling state for next GW
            squad = post_squad
            rolling_bank = rolling_bank - net_spend

            # Free transfer logic: accrue 1 FT per GW, max 5
            # (changed to 5 from 2 in 2023-24 rules, capped at 5)
            ft_used = free_used
            remaining = rolling_ft - ft_used
            rolling_ft = min(remaining + 1, 5)
            if rolling_ft < 1:
                rolling_ft = 1

            # If chip used, update remaining chips
            if chip_rec and chip_rec.get("use_chip"):
                chip_code = chip_rec["chip_code"]
                if chip_code in remaining_chips:
                    remaining_chips.discard(chip_code)
                    if chip_code == "WC":
                        rolling_ft = 1  # WC resets FT to 1
                    elif chip_code == "FH":
                        squad = list(current_squad_ids)  # FH reverts squad
                        rolling_ft = rolling_ft  # FH doesn't affect FT

        # Summary stats
        total_transfers = sum(g["num_transfers"] for g in gw_plans)
        total_hits = sum(g["hits"] for g in gw_plans)
        total_hit_cost = sum(g["hit_cost"] for g in gw_plans)
        total_xpts_gain = sum(g["xpts_gain"] for g in gw_plans)

        return {
            "horizon": self.horizon,
            "from_gw": self.next_gw,
            "to_gw": self.next_gw + self.horizon - 1,
            "gw_plans": gw_plans,
            "summary": {
                "total_transfers": total_transfers,
                "total_hits": total_hits,
                "total_hit_cost": total_hit_cost,
                "total_xpts_gain": round(total_xpts_gain, 1),
                "net_gain_after_hits": round(total_xpts_gain - total_hit_cost, 1),
                "chips_used": [
                    g["chip_recommendation"]["chip_code"]
                    for g in gw_plans
                    if g["chip_recommendation"] and g["chip_recommendation"].get("use_chip")
                ],
                "final_bank": round(gw_plans[-1]["bank_after"], 1) if gw_plans else rolling_bank,
            },
            "fixture_ticker": self._build_squad_ticker(squad),
        }

    def _find_best_transfers(self, squad_enriched: list, pred_map: dict,
                              bank: float, free_transfers: int,
                              max_transfers: int, gw: int) -> list:
        """Find the best transfers for a single GW."""
        current_ids = {p["player_id"] for p in squad_enriched}
        team_counts = {}
        for p in squad_enriched:
            t = p["team_id"]
            team_counts[t] = team_counts.get(t, 0) + 1

        # Rank squad by this GW's xPts (worst first = transfer candidates)
        candidates_out = sorted(squad_enriched, key=lambda x: x["predicted_points"])

        transfers = []
        used_in_ids = set()
        budget = bank

        # Only suggest transfers that are clearly beneficial
        # Limit to max_transfers (including potential hits)
        for out_p in candidates_out:
            if len(transfers) >= max_transfers:
                break

            out_id = out_p["player_id"]
            out_price = out_p["price"]
            out_xpts = out_p["predicted_points"]
            pos_id = out_p["position_id"]
            out_team = out_p["team_id"]

            # Find best replacement
            best_in = None
            best_gain = 0

            for pred in self._gw_predictions.get(gw, []):
                in_id = pred["player_id"]
                if in_id in current_ids or in_id in used_in_ids:
                    continue
                if pred.get("position_id") != pos_id:
                    continue

                in_price = pred.get("price", 99)
                if in_price > out_price + budget:
                    continue

                in_team = pred.get("team_id", 0)
                # Team limit check (ok if replacing from same team)
                team_cnt = team_counts.get(in_team, 0)
                if in_team != out_team and team_cnt >= MAX_PER_TEAM:
                    continue

                in_xpts = pred.get("predicted_points", 0)
                gain = in_xpts - out_xpts

                # Also consider multi-GW value
                multi_gw_gain = self._quick_multi_gw_value(in_id, out_id, gw)
                total_value = gain * 0.5 + multi_gw_gain * 0.5

                if total_value > best_gain:
                    best_gain = total_value
                    best_in = pred

            if best_in and best_gain > 0:
                in_price = best_in.get("price", 0)

                # Check if this is worth a hit
                is_free = len(transfers) < free_transfers
                if not is_free:
                    # Need at least 4 pts gain to justify a hit
                    if best_gain < 4.0:
                        continue

                cost_delta = in_price - out_price

                transfers.append({
                    "out_id": out_id,
                    "out_name": out_p["name"],
                    "out_team": out_p["team"],
                    "out_position": out_p["position"],
                    "out_price": out_price,
                    "out_xpts": round(out_xpts, 2),
                    "out_fixtures": out_p.get("fixtures", []),
                    "in_id": best_in["player_id"],
                    "in_name": best_in["name"],
                    "in_team": best_in["team"],
                    "in_position": best_in["position"],
                    "in_price": in_price,
                    "in_xpts": round(best_in.get("predicted_points", 0), 2),
                    "in_fixtures": best_in.get("fixtures", []),
                    "in_is_dgw": best_in.get("is_dgw", False),
                    "in_form": best_in.get("form", 0),
                    "in_starter_tier": best_in.get("starter_quality", {}).get("tier", "unknown"),
                    "xpts_gain_this_gw": round(best_in.get("predicted_points", 0) - out_xpts, 2),
                    "multi_gw_value": round(best_gain, 2),
                    "cost_delta": round(cost_delta, 1),
                    "is_free": is_free,
                    "is_hit": not is_free,
                })

                used_in_ids.add(best_in["player_id"])
                budget -= cost_delta

                # Update team counts for subsequent transfers
                team_counts[out_team] = team_counts.get(out_team, 1) - 1
                in_team = best_in.get("team_id", 0)
                team_counts[in_team] = team_counts.get(in_team, 0) + 1
                current_ids.discard(out_id)
                current_ids.add(best_in["player_id"])

        return transfers

    def _quick_multi_gw_value(self, in_id: int, out_id: int, from_gw: int) -> float:
        """Quick estimate of multi-GW value of a transfer (looks 3 GWs ahead)."""
        total_gain = 0.0
        lookahead = min(3, 38 - from_gw)

        for gw in range(from_gw, from_gw + lookahead + 1):
            if gw > 38:
                break
            pred_map = self._get_pred_map(gw)
            in_pred = pred_map.get(in_id, {})
            out_pred = pred_map.get(out_id, {})
            in_xpts = in_pred.get("predicted_points", 0)
            out_xpts = out_pred.get("predicted_points", 0)
            # Decay weight for future GWs (less certain)
            weight = 1.0 / (1 + (gw - from_gw) * 0.3)
            total_gain += (in_xpts - out_xpts) * weight

        return total_gain

    def _calc_multi_gw_transfer_value(self, transfers: list, from_gw: int) -> list:
        """Calculate the multi-GW impact of each planned transfer."""
        results = []
        for t in transfers:
            gw_values = []
            for gw in range(from_gw, min(from_gw + self.horizon, 39)):
                pred_map = self._get_pred_map(gw)
                in_pred = pred_map.get(t["in_id"], {})
                out_pred = pred_map.get(t["out_id"], {})
                gw_values.append({
                    "gw": gw,
                    "in_xpts": round(in_pred.get("predicted_points", 0), 2),
                    "out_xpts": round(out_pred.get("predicted_points", 0), 2),
                    "gain": round(
                        in_pred.get("predicted_points", 0) -
                        out_pred.get("predicted_points", 0), 2
                    ),
                })
            total_gain = sum(v["gain"] for v in gw_values)
            results.append({
                "in_name": t["in_name"],
                "out_name": t["out_name"],
                "gw_breakdown": gw_values,
                "total_gain": round(total_gain, 2),
            })
        return results

    # ── Chip Timing Recommendations ───────────────────────────

    def _recommend_chip(self, squad_enriched: list, gw_info: dict,
                        gw: int, remaining_chips: set,
                        pred_map: dict) -> dict | None:
        """Recommend whether to use a chip this GW."""
        if not remaining_chips:
            return None

        dgw_teams = gw_info.get("dgw_teams", {})
        bgw_teams = gw_info.get("bgw_teams", {})
        is_dgw = len(dgw_teams) > 0
        is_bgw = len(bgw_teams) > 0

        scores = {}

        # Bench Boost
        if "BB" in remaining_chips:
            bench = sorted(squad_enriched, key=lambda x: x["predicted_points"])[:4]
            bench_xpts = sum(p["predicted_points"] for p in bench)
            bb_score = 0
            if is_dgw:
                dgw_bench = sum(1 for p in bench if p.get("is_dgw"))
                bb_score = min(100, int(bench_xpts * 3 + dgw_bench * 10))
            else:
                bb_score = min(70, int(bench_xpts * 2.5))
            scores["BB"] = {
                "score": bb_score,
                "reason": f"Bench xPts: {round(bench_xpts, 1)}. "
                          f"{'DGW with ' + str(len(dgw_teams)) + ' teams doubling.' if is_dgw else 'No DGW.'}",
            }

        # Triple Captain
        if "TC" in remaining_chips:
            best_player = max(squad_enriched, key=lambda x: x["predicted_points"])
            tc_extra = best_player["predicted_points"]  # Extra captain pts
            tc_score = 0
            if is_dgw and best_player.get("is_dgw"):
                tc_score = min(100, int(tc_extra * 5))
            else:
                tc_score = min(60, int(tc_extra * 3))
            scores["TC"] = {
                "score": tc_score,
                "reason": f"Best captain: {best_player['name']} ({round(tc_extra, 1)} xPts"
                          f"{', DGW' if best_player.get('is_dgw') else ''}).",
            }

        # Free Hit
        if "FH" in remaining_chips:
            blanks = sum(1 for p in squad_enriched if p.get("num_fixtures", 0) == 0)
            fh_score = 0
            if blanks >= 4:
                fh_score = min(100, blanks * 15)
            elif is_bgw and len(bgw_teams) >= 5:
                fh_score = 60
            else:
                fh_score = max(0, blanks * 8)
            scores["FH"] = {
                "score": fh_score,
                "reason": f"{blanks} players blanking. "
                          f"{'Major BGW with ' + str(len(bgw_teams)) + ' teams missing.' if is_bgw else ''}",
            }

        # Wildcard
        if "WC" in remaining_chips:
            # WC is good when squad needs major overhaul
            low_xpts = sum(1 for p in squad_enriched if p["predicted_points"] < 3)
            flagged = sum(1 for p in squad_enriched if p.get("status") not in ("a", None))
            fixture_swings = self._count_fixture_swings(squad_enriched, gw)
            wc_score = min(100, low_xpts * 8 + flagged * 10 + fixture_swings * 5)
            scores["WC"] = {
                "score": wc_score,
                "reason": f"{low_xpts} low-xPts players, {flagged} flagged. "
                          f"Fixture swing score: {fixture_swings}.",
            }

        if not scores:
            return None

        # Find best chip
        best_chip = max(scores, key=lambda k: scores[k]["score"])
        best_score = scores[best_chip]["score"]

        return {
            "chip_code": best_chip,
            "score": best_score,
            "use_chip": best_score >= 70,  # Only recommend if score >= 70
            "reason": scores[best_chip]["reason"],
            "all_scores": {
                code: {"score": s["score"], "reason": s["reason"]}
                for code, s in scores.items()
            },
        }

    def _count_fixture_swings(self, squad: list, gw: int) -> int:
        """Count how many players have a significant fixture difficulty swing."""
        swings = 0
        for p in squad:
            fixes_now = get_player_fixtures(p["team_id"], gw, self.fixtures)
            fixes_next = get_player_fixtures(p["team_id"], min(gw + 1, 38), self.fixtures)
            if fixes_now and fixes_next:
                avg_now = sum(f["fdr"] for f in fixes_now) / len(fixes_now)
                avg_next = sum(f["fdr"] for f in fixes_next) / len(fixes_next)
                if abs(avg_next - avg_now) >= 2:
                    swings += 1
        return swings

    # ── Helper: Build squad fixture ticker ────────────────────

    def _build_squad_ticker(self, squad_ids: list[int]) -> dict:
        """Build fixture ticker for all teams represented in the squad."""
        team_ids = set()
        for pid in squad_ids:
            p = self.players.get(pid, {})
            team_ids.add(p.get("team", 0))
        team_ids.discard(0)
        return self.build_fixture_ticker(list(team_ids))

    # ── Convenience: Plan from FPL Team ID ────────────────────

    def plan_from_team_id(self, team_id: int, horizon: int = None) -> dict:
        """
        Convenience method: fetch team from FPL API and generate plan.
        """
        from my_team import fetch_my_team

        if horizon is not None:
            self.horizon = min(horizon, 38 - self.next_gw + 1)

        team_data = fetch_my_team(team_id)
        if team_data.get("error"):
            return {"error": team_data["error"]}

        picks = team_data.get("picks", [])
        if not picks:
            return {"error": "No picks found for this team"}

        squad_ids = [p["element"] for p in picks]
        bank = team_data.get("gw_summary", {}).get("bank", 0)

        # Determine chips still available
        chips_used = {c.get("name") for c in team_data.get("chips", [])}
        chip_map = {"wildcard": "WC", "freehit": "FH", "bboost": "BB", "3xc": "TC"}
        chips_available = [
            code for name, code in chip_map.items()
            if name not in chips_used
        ]

        # Estimate free transfers (FPL doesn't expose this directly)
        # If they made 0 transfers last GW, they gained 1 FT (max 5)
        history = team_data.get("history", [])
        if history:
            last_gw = history[-1]
            last_transfers = last_gw.get("event_transfers", 0)
            # Rough estimate: if 0 transfers, probably have 2+ FT
            if last_transfers == 0 and len(history) >= 2:
                ft = min(5, 2)  # Conservative estimate
            else:
                ft = 1
        else:
            ft = 1

        plan = self.plan_transfers(
            current_squad_ids=squad_ids,
            bank=bank,
            free_transfers=ft,
            chips_available=chips_available,
        )

        plan["team_info"] = team_data.get("info", {})
        plan["chips_used_this_season"] = list(chips_used)
        plan["chips_remaining"] = chips_available
        plan["estimated_free_transfers"] = ft

        return plan
