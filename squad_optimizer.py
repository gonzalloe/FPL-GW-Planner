"""
FPL Predictor - Squad Optimizer v3
Maximizes TOTAL squad xPts using a knapsack-style dynamic programming approach.
Respects all FPL constraints: budget, max 3 per team, position limits.
"""
from config import (
    SQUAD_BUDGET, SQUAD_SIZE, STARTING_XI, MAX_PER_TEAM,
    POSITION_LIMITS, CHIPS, CHIP_THRESHOLDS
)
import itertools


class SquadOptimizer:
    """
    Optimizes squad selection to MAXIMIZE total predicted points.
    Uses branch-and-bound with greedy heuristic + local search improvement.
    """

    def __init__(self, predictions: list[dict], budget: float | None = None):
        self.predictions = [p for p in predictions if p.get("predicted_points", 0) > 0]
        self.budget = budget or (SQUAD_BUDGET / 10)

    def optimize_squad(self, chip: str | None = None) -> dict:
        """
        Find the best 15-man squad maximizing total xPts.
        Then pick the best starting XI from that squad.
        """
        # Step 1: Build optimal 15-man squad
        squad = self._optimize_full_squad()

        if len(squad) < 15:
            squad = self._fill_remaining(squad)

        # Step 2: Local search improvement — try swaps to increase total xPts
        squad = self._local_search_improve(squad)

        # Step 3: Select starting XI (best 11 from 15)
        starting_xi, bench = self._select_best_xi(squad, chip)

        # Step 4: Captain & vice-captain
        captain, vice_captain = self._select_captain(starting_xi)

        # Calculate totals
        total_cost = sum(p.get("price", 0) for p in squad)
        squad_total_xpts = sum(p["predicted_points"] for p in squad)

        if chip == "bench_boost":
            total_predicted = squad_total_xpts
            if captain:
                total_predicted += captain["predicted_points"]
        else:
            total_predicted = sum(p["predicted_points"] for p in starting_xi)
            if captain:
                if chip == "triple_captain":
                    total_predicted += captain["predicted_points"] * 2
                else:
                    total_predicted += captain["predicted_points"]

        dgw_count = sum(1 for p in squad if p.get("is_dgw"))
        dgw_xi = sum(1 for p in starting_xi if p.get("is_dgw"))

        return {
            "squad": sorted(squad, key=lambda x: (x["position_id"], -x["predicted_points"])),
            "starting_xi": sorted(starting_xi, key=lambda x: (x["position_id"], -x["predicted_points"])),
            "bench": bench,
            "captain": captain,
            "vice_captain": vice_captain,
            "total_cost": round(total_cost, 1),
            "budget_remaining": round(self.budget - total_cost, 1),
            "predicted_total_points": round(total_predicted, 1),
            "squad_total_xpts": round(squad_total_xpts, 1),
            "formation": self._get_formation(starting_xi),
            "chip_active": chip,
            "dgw_players": dgw_count,
            "dgw_in_xi": dgw_xi,
        }

    def _optimize_full_squad(self) -> list:
        """
        Build the best 15-man squad maximizing TOTAL xPts.

        Strategy: Position-by-position allocation with budget optimization.
        1. For each position, generate top candidates (sorted by xPts)
        2. Use a multi-pass approach:
           a. First, pick the best combination of players by position
           b. Respect team limits (max 3 per team)
           c. Respect budget
        3. Then do iterative improvement via swap search
        """
        # Pre-filter: only consider eligible players
        eligible = self._get_eligible_players()

        # Group by position
        by_pos = {1: [], 2: [], 3: [], 4: []}
        for p in eligible:
            pos_id = p.get("position_id", 0)
            if pos_id in by_pos:
                by_pos[pos_id].append(p)

        # Sort each position by xPts
        for pos_id in by_pos:
            by_pos[pos_id].sort(key=lambda x: x["predicted_points"], reverse=True)

        # Trim to top N per position to keep search space manageable
        # More candidates for positions with more slots
        top_n = {1: 15, 2: 40, 3: 40, 4: 25}
        for pos_id in by_pos:
            by_pos[pos_id] = by_pos[pos_id][:top_n.get(pos_id, 30)]

        # Strategy: Build squad position by position, using beam search
        # Start with the position that has fewest slots (GKP: 2) to constrain early
        # Then fill DEF(5), MID(5), FWD(3)
        best_squad = self._beam_search_squad(by_pos)

        if best_squad and len(best_squad) == 15:
            return best_squad

        # Fallback: greedy approach
        return self._greedy_squad(by_pos)

    def _beam_search_squad(self, by_pos: dict, beam_width: int = 50) -> list:
        """
        Beam search across positions to find highest-xPts squad.
        Each state = (selected_players, budget_left, team_counts).
        """
        # Order: GKP(2) → FWD(3) → MID(5) → DEF(5)
        # Pick restrictive positions first, then fill flexible ones
        pos_order = [
            (1, 2),  # 2 GKP
            (4, 3),  # 3 FWD
            (3, 5),  # 5 MID
            (2, 5),  # 5 DEF
        ]

        # Initial state: no players, full budget
        states = [{"players": [], "budget": self.budget, "teams": {}, "xpts": 0.0}]

        for pos_id, count in pos_order:
            candidates = by_pos.get(pos_id, [])
            if not candidates:
                continue

            new_states = []

            for state in states:
                # Generate all valid combinations of `count` players from candidates
                # For efficiency, only try top candidates that fit budget
                affordable = [c for c in candidates
                              if c.get("price", 0) <= state["budget"]
                              and c["player_id"] not in {p["player_id"] for p in state["players"]}]

                # For large candidate pools, limit combinations
                max_cands = min(len(affordable), 12 if count <= 2 else 15)
                pool = affordable[:max_cands]

                if len(pool) < count:
                    # Not enough candidates — try with all affordable
                    pool = affordable[:count + 5]
                    if len(pool) < count:
                        continue

                for combo in itertools.combinations(pool, count):
                    # Check team limits
                    new_teams = dict(state["teams"])
                    valid = True
                    combo_cost = 0
                    combo_xpts = 0

                    for p in combo:
                        tid = p.get("team_id", p.get("team", 0))
                        new_teams[tid] = new_teams.get(tid, 0) + 1
                        if new_teams[tid] > MAX_PER_TEAM:
                            valid = False
                            break
                        combo_cost += p.get("price", 0)
                        combo_xpts += p["predicted_points"]

                    if not valid:
                        continue

                    new_budget = state["budget"] - combo_cost
                    if new_budget < 0:
                        continue

                    # Check if remaining budget is enough for remaining positions
                    remaining_slots = sum(c for pid, c in pos_order
                                          if pid not in [pos_id] + [p[0] for p in pos_order[:pos_order.index((pos_id, count))]])
                    # Rough min cost per remaining player: ~4.0
                    # But be more generous to not prune too aggressively
                    if remaining_slots > 0 and new_budget < remaining_slots * 3.8:
                        # Check more carefully
                        filled_pos = set()
                        for p in state["players"]:
                            filled_pos.add(p.get("position_id"))
                        for p in combo:
                            filled_pos.add(p.get("position_id"))
                        unfilled_count = 0
                        for pid2, cnt2 in pos_order:
                            if pid2 not in filled_pos:
                                unfilled_count += cnt2
                        if unfilled_count > 0 and new_budget < unfilled_count * 3.9:
                            continue

                    new_states.append({
                        "players": state["players"] + list(combo),
                        "budget": new_budget,
                        "teams": new_teams,
                        "xpts": state["xpts"] + combo_xpts,
                    })

            if not new_states:
                # If no valid states, keep old states (shouldn't happen)
                continue

            # Keep top beam_width states by xPts
            new_states.sort(key=lambda s: s["xpts"], reverse=True)
            states = new_states[:beam_width]

        if not states:
            return []

        # Return best squad
        best = max(states, key=lambda s: s["xpts"])
        return best["players"]

    def _greedy_squad(self, by_pos: dict) -> list:
        """Fallback greedy approach: pick best available player-by-player."""
        squad = []
        budget_left = self.budget
        team_counts = {}
        squad_ids = set()
        pos_counts = {1: 0, 2: 0, 3: 0, 4: 0}

        # Build global list sorted by xPts
        all_candidates = []
        for pos_id, players in by_pos.items():
            all_candidates.extend(players)
        all_candidates.sort(key=lambda x: x["predicted_points"], reverse=True)

        # Pass 1: Fill minimum positions first to guarantee validity
        for pos_id, limits in POSITION_LIMITS.items():
            needed = limits["squad_min"]
            for p in by_pos.get(pos_id, []):
                if needed <= 0:
                    break
                if p["player_id"] in squad_ids:
                    continue
                if p.get("price", 0) > budget_left:
                    continue
                tid = p.get("team_id", p.get("team", 0))
                if team_counts.get(tid, 0) >= MAX_PER_TEAM:
                    continue
                squad.append(p)
                squad_ids.add(p["player_id"])
                budget_left -= p.get("price", 0)
                team_counts[tid] = team_counts.get(tid, 0) + 1
                pos_counts[pos_id] = pos_counts.get(pos_id, 0) + 1
                needed -= 1

        # Pass 2: Fill remaining to 15 with highest xPts
        for p in all_candidates:
            if len(squad) >= SQUAD_SIZE:
                break
            if p["player_id"] in squad_ids:
                continue
            pos_id = p.get("position_id", 0)
            max_allowed = POSITION_LIMITS.get(pos_id, {}).get("squad_max", 5)
            if pos_counts.get(pos_id, 0) >= max_allowed:
                continue
            if p.get("price", 0) > budget_left:
                continue
            tid = p.get("team_id", p.get("team", 0))
            if team_counts.get(tid, 0) >= MAX_PER_TEAM:
                continue
            squad.append(p)
            squad_ids.add(p["player_id"])
            budget_left -= p.get("price", 0)
            team_counts[tid] = team_counts.get(tid, 0) + 1
            pos_counts[pos_id] = pos_counts.get(pos_id, 0) + 1

        return squad

    def _local_search_improve(self, squad: list, max_iterations: int = 200) -> list:
        """
        Iteratively try to swap squad players with non-squad players
        to increase total xPts while respecting all constraints.
        """
        squad_ids = {p["player_id"] for p in squad}
        eligible = self._get_eligible_players()
        non_squad = [p for p in eligible if p["player_id"] not in squad_ids]
        non_squad.sort(key=lambda x: x["predicted_points"], reverse=True)

        # Only consider top non-squad players (no point swapping for worse)
        min_squad_xpts = min(p["predicted_points"] for p in squad) if squad else 0
        non_squad = [p for p in non_squad if p["predicted_points"] > min_squad_xpts * 0.8]

        improved = True
        iterations = 0
        while improved and iterations < max_iterations:
            improved = False
            iterations += 1

            # Try swapping each squad player with each non-squad player
            squad_sorted = sorted(squad, key=lambda x: x["predicted_points"])

            for i, out_player in enumerate(squad_sorted):
                if improved:
                    break
                for in_player in non_squad:
                    if in_player["player_id"] in squad_ids:
                        continue
                    # Same position required
                    if in_player.get("position_id") != out_player.get("position_id"):
                        continue
                    # Must improve xPts
                    gain = in_player["predicted_points"] - out_player["predicted_points"]
                    if gain <= 0.05:  # Min threshold to swap
                        continue
                    # Budget check
                    cost_diff = in_player.get("price", 0) - out_player.get("price", 0)
                    budget_remaining = self.budget - sum(p.get("price", 0) for p in squad)
                    if cost_diff > budget_remaining + 0.01:
                        continue
                    # Team limit check
                    in_tid = in_player.get("team_id", in_player.get("team", 0))
                    out_tid = out_player.get("team_id", out_player.get("team", 0))
                    team_counts = {}
                    for p in squad:
                        if p["player_id"] != out_player["player_id"]:
                            tid = p.get("team_id", p.get("team", 0))
                            team_counts[tid] = team_counts.get(tid, 0) + 1
                    team_counts[in_tid] = team_counts.get(in_tid, 0) + 1
                    if team_counts[in_tid] > MAX_PER_TEAM:
                        continue

                    # Valid swap — do it
                    squad = [p for p in squad if p["player_id"] != out_player["player_id"]]
                    squad.append(in_player)
                    squad_ids.discard(out_player["player_id"])
                    squad_ids.add(in_player["player_id"])
                    non_squad = [p for p in non_squad if p["player_id"] != in_player["player_id"]]
                    non_squad.append(out_player)
                    improved = True
                    break

        return squad

    def _get_eligible_players(self) -> list:
        """Filter predictions to eligible players only."""
        eligible = []
        for p in self.predictions:
            # Must have positive xPts
            if p.get("predicted_points", 0) <= 0:
                continue
            # Skip bench warmers
            tier = p.get("starter_quality", {}).get("tier", "unknown")
            if tier == "bench_warmer":
                continue
            # Skip very doubtful
            avail = p.get("availability", {})
            if avail.get("status") == "doubtful" and avail.get("chance", 50) < 50:
                continue
            if avail.get("status") == "unavailable":
                continue
            eligible.append(p)
        return eligible

    def _fill_remaining(self, squad: list) -> list:
        """Fill remaining spots if beam search didn't get to 15."""
        budget_left = self.budget - sum(p.get("price", 0) for p in squad)
        team_counts = {}
        for p in squad:
            tid = p.get("team_id", p.get("team", 0))
            team_counts[tid] = team_counts.get(tid, 0) + 1
        squad_ids = {p["player_id"] for p in squad}
        pos_counts = {}
        for p in squad:
            pid = p.get("position_id", 0)
            pos_counts[pid] = pos_counts.get(pid, 0) + 1

        eligible = self._get_eligible_players()
        remaining = [p for p in eligible if p["player_id"] not in squad_ids]
        remaining.sort(key=lambda x: x["predicted_points"], reverse=True)

        for p in remaining:
            if len(squad) >= SQUAD_SIZE:
                break
            pos_id = p.get("position_id", 0)
            max_allowed = POSITION_LIMITS.get(pos_id, {}).get("squad_max", 5)
            if pos_counts.get(pos_id, 0) >= max_allowed:
                continue
            if p.get("price", 0) > budget_left:
                continue
            tid = p.get("team_id", p.get("team", 0))
            if team_counts.get(tid, 0) >= MAX_PER_TEAM:
                continue
            squad.append(p)
            squad_ids.add(p["player_id"])
            budget_left -= p.get("price", 0)
            team_counts[tid] = team_counts.get(tid, 0) + 1
            pos_counts[pos_id] = pos_counts.get(pos_id, 0) + 1

        return squad

    def _select_best_xi(self, squad: list, chip: str | None = None) -> tuple[list, list]:
        """
        Select the BEST starting XI from the 15-man squad.
        Tries all valid formations and picks the one with highest total xPts.
        """
        # Valid formations: DEF-MID-FWD combos (must sum to 10 outfield)
        valid_formations = []
        for d in range(3, 6):  # 3-5 DEF
            for m in range(2, 6):  # 2-5 MID
                f = 10 - d - m  # FWD = remaining
                if 1 <= f <= 3:
                    valid_formations.append((d, m, f))

        # Group squad by position
        by_pos = {1: [], 2: [], 3: [], 4: []}
        for p in squad:
            pos_id = p.get("position_id", 0)
            if pos_id in by_pos:
                by_pos[pos_id].append(p)

        # Sort each position by xPts
        for pos_id in by_pos:
            by_pos[pos_id].sort(key=lambda x: x["predicted_points"], reverse=True)

        best_xi = None
        best_xi_xpts = -1

        for d_count, m_count, f_count in valid_formations:
            # Check if we have enough players
            if len(by_pos[2]) < d_count:
                continue
            if len(by_pos[3]) < m_count:
                continue
            if len(by_pos[4]) < f_count:
                continue

            # Pick top N from each position
            xi = []
            xi.extend(by_pos[1][:1])  # 1 GKP always
            xi.extend(by_pos[2][:d_count])
            xi.extend(by_pos[3][:m_count])
            xi.extend(by_pos[4][:f_count])

            xi_xpts = sum(p["predicted_points"] for p in xi)

            if xi_xpts > best_xi_xpts:
                best_xi_xpts = xi_xpts
                best_xi = xi

        if best_xi is None:
            # Fallback
            best_xi = sorted(squad, key=lambda x: x["predicted_points"], reverse=True)[:11]

        xi_ids = {p["player_id"] for p in best_xi}
        bench = [p for p in squad if p["player_id"] not in xi_ids]
        bench.sort(key=lambda x: x["predicted_points"], reverse=True)

        return best_xi, bench

    def _select_captain(self, starting_xi: list) -> tuple[dict | None, dict | None]:
        """Pick captain = highest xPts in XI. Slight caution for 75% flagged."""
        def captain_score(p):
            xp = p["predicted_points"]
            avail = p.get("availability", {})
            status = avail.get("status", "available")
            chance = avail.get("chance", 100)
            if status == "doubtful":
                if chance >= 75:
                    xp *= 0.90
                elif chance >= 50:
                    xp *= 0.20
                else:
                    xp *= 0.05
            if p.get("is_dgw") and p.get("starter_quality", {}).get("tier") == "nailed":
                xp *= 1.1
            return xp

        sorted_xi = sorted(starting_xi, key=captain_score, reverse=True)
        captain = sorted_xi[0] if sorted_xi else None
        vice = sorted_xi[1] if len(sorted_xi) > 1 else None
        return captain, vice

    def _get_formation(self, starting_xi: list) -> str:
        counts = {2: 0, 3: 0, 4: 0}
        for p in starting_xi:
            pos = p.get("position_id", 0)
            if pos in counts:
                counts[pos] += 1
        return f"{counts[2]}-{counts[3]}-{counts[4]}"


class ChipAdvisor:
    """Analyzes the current gameweek and squad to recommend chip usage."""

    def __init__(self, predictions: list[dict], gw_info: dict):
        self.predictions = predictions
        self.gw_info = gw_info

    def analyze(self, current_squad_ids: list[int] | None = None,
                chips_available: list[str] | None = None) -> dict:
        if chips_available is None:
            chips_available = ["wildcard", "free_hit", "bench_boost", "triple_captain"]

        is_dgw = self.gw_info.get("is_dgw", False)
        dgw_teams = self.gw_info.get("dgw_teams", {})
        total_fixtures = self.gw_info.get("total_fixtures", 10)
        gw = self.gw_info.get("gameweek", 0)

        recommendations = []

        # ── Bench Boost ──
        if "bench_boost" in chips_available and is_dgw:
            optimizer = SquadOptimizer(self.predictions)
            bb_squad = optimizer.optimize_squad(chip="bench_boost")
            bench_xp = sum(p["predicted_points"] for p in bb_squad["bench"])
            bench_dgw = sum(1 for p in bb_squad["bench"] if p.get("is_dgw"))

            score = 0
            reasons = []
            if bench_xp >= CHIP_THRESHOLDS["bench_boost_min_bench_xp"]:
                score += 40
                reasons.append(f"Strong bench ({bench_xp:.1f} xPts)")
            if bench_dgw >= 3:
                score += 30
                reasons.append(f"{bench_dgw}/4 bench players have DGW")
            if is_dgw and len(dgw_teams) >= 4:
                score += 20
                reasons.append(f"Big DGW ({len(dgw_teams)} teams with double fixtures)")
            if total_fixtures >= 12:
                score += 10
                reasons.append(f"{total_fixtures} total fixtures this GW")

            recommendations.append({
                "chip": "bench_boost", "name": "Bench Boost", "code": "BB",
                "score": score, "reasons": reasons,
                "bench_xp": round(bench_xp, 1),
                "predicted_total": bb_squad["predicted_total_points"],
            })

        # ── Triple Captain ──
        if "triple_captain" in chips_available:
            top_player = self.predictions[0] if self.predictions else None
            if top_player:
                score = 0
                reasons = []
                xp = top_player["predicted_points"]
                if xp >= CHIP_THRESHOLDS["triple_captain_min_xp"]:
                    score += 30
                    reasons.append(f"{top_player['name']} has {xp:.1f} xPts")
                if top_player.get("is_dgw"):
                    score += 35
                    reasons.append("Captain plays twice (DGW)")
                if top_player.get("form", 0) >= 7:
                    score += 15
                    reasons.append(f"Excellent form ({top_player['form']:.1f})")
                if top_player.get("starter_quality", {}).get("tier") == "nailed":
                    score += 10
                    reasons.append("Nailed starter")
                fixtures = top_player.get("fixtures", [])
                easy = sum(1 for f in fixtures if f.get("fdr", 3) <= 2)
                if easy >= 1:
                    score += 10
                    reasons.append(f"{easy} easy fixture(s)")

                recommendations.append({
                    "chip": "triple_captain", "name": "Triple Captain", "code": "TC",
                    "score": score, "reasons": reasons,
                    "captain": top_player["name"],
                    "captain_xp": round(xp, 1),
                    "extra_points": round(xp, 1),
                })

        # ── Free Hit ──
        if "free_hit" in chips_available:
            score = 0
            reasons = []
            if current_squad_ids:
                pred_map = {p["player_id"]: p for p in self.predictions}
                blanking = sum(1 for sid in current_squad_ids
                               if pred_map.get(sid, {}).get("num_fixtures", 0) == 0)
                dgw_in_squad = sum(1 for sid in current_squad_ids
                                    if pred_map.get(sid, {}).get("is_dgw"))
                if blanking >= CHIP_THRESHOLDS["free_hit_blank_threshold"]:
                    score += 50
                    reasons.append(f"{blanking} players blanking this GW")
                if is_dgw and dgw_in_squad < 4:
                    score += 25
                    reasons.append(f"Only {dgw_in_squad} DGW players in your squad")
            else:
                if is_dgw and len(dgw_teams) >= 4:
                    score += 20
                    reasons.append("Large DGW - FH can maximize DGW exposure")
            if not is_dgw and total_fixtures < 8:
                score += 30
                reasons.append(f"Only {total_fixtures} fixtures (BGW)")

            recommendations.append({
                "chip": "free_hit", "name": "Free Hit", "code": "FH",
                "score": score, "reasons": reasons,
            })

        # ── Wildcard ──
        if "wildcard" in chips_available:
            score = 0
            reasons = []
            if is_dgw and len(dgw_teams) >= 5:
                score += 15
                reasons.append("Large DGW - could WC to build optimal DGW squad")
            reasons.append("Use WC when your squad needs a complete overhaul")
            reasons.append("Best used 1 GW before a big DGW (prep + BB next week)")
            recommendations.append({
                "chip": "wildcard", "name": "Wildcard", "code": "WC",
                "score": score, "reasons": reasons,
            })

        recommendations.sort(key=lambda x: x["score"], reverse=True)
        best = recommendations[0] if recommendations else None

        return {
            "gameweek": gw, "is_dgw": is_dgw,
            "dgw_team_count": len(dgw_teams),
            "total_fixtures": total_fixtures,
            "recommendations": recommendations,
            "best_chip": best,
            "save_chips": not is_dgw and total_fixtures >= 8,
        }


class TransferAdvisor:
    """Recommends transfers based on current squad vs optimal."""

    def __init__(self, predictions: list[dict]):
        self.predictions = predictions
        self.pred_map = {p["player_id"]: p for p in predictions}

    def recommend_transfers(self, current_squad_ids: list[int],
                            free_transfers: int = 1,
                            budget: float = 0.0) -> list[dict]:
        current = [self.pred_map.get(pid) for pid in current_squad_ids
                    if self.pred_map.get(pid)]
        current.sort(key=lambda x: x["predicted_points"])

        recommendations = []

        for out_player in current[:free_transfers * 3]:
            pos_id = out_player.get("position_id")
            out_price = out_player.get("price", 0)
            available_budget = out_price + budget

            candidates = [
                p for p in self.predictions
                if p.get("position_id") == pos_id
                and p["player_id"] not in current_squad_ids
                and p.get("price", 99) <= available_budget
                and p["predicted_points"] > out_player["predicted_points"]
                and p.get("starter_quality", {}).get("tier", "") not in ("bench_warmer", "fringe")
            ]
            candidates.sort(key=lambda x: x["predicted_points"], reverse=True)

            if candidates:
                best = candidates[0]
                recommendations.append({
                    "out": {
                        "name": out_player["name"], "team": out_player["team"],
                        "position": out_player["position"],
                        "price": out_player.get("price", 0),
                        "predicted_points": out_player["predicted_points"],
                        "is_dgw": out_player.get("is_dgw", False),
                        "starter_tier": out_player.get("starter_quality", {}).get("tier", "?"),
                    },
                    "in": {
                        "name": best["name"], "team": best["team"],
                        "position": best["position"],
                        "price": best.get("price", 0),
                        "predicted_points": best["predicted_points"],
                        "is_dgw": best.get("is_dgw", False),
                        "starter_tier": best.get("starter_quality", {}).get("tier", "?"),
                    },
                    "points_gain": round(best["predicted_points"] - out_player["predicted_points"], 2),
                    "cost_change": round(best.get("price", 0) - out_price, 1),
                })

        recommendations.sort(key=lambda x: x["points_gain"], reverse=True)
        return recommendations[:free_transfers * 3]
