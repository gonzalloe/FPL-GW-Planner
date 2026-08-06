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
import heapq

DEBUG_OPTIMIZER = True  # set False once diagnosis is complete

class SquadOptimizer:
    """
    Optimizes squad selection to MAXIMIZE total predicted points.
    Uses branch-and-bound with greedy heuristic + local search improvement.
    """

    def __init__(self, predictions: list[dict], budget: float | None = None):
        self.predictions = [p for p in predictions if p.get("predicted_points", 0) > 0]
        self.budget = budget or (SQUAD_BUDGET / 10)

    @staticmethod
    def _squad_score(players):
        if not players:
            return 0

        # highest predicted players are starters approximation
        xi = sorted(
            players,
            key=lambda p: p.get("predicted_points",0),
            reverse=True
        )[:11]
        total = sum(
            p.get("predicted_points",0)
            for p in xi
        )
        captain = max(
            xi,
            key=lambda p:p.get("predicted_points",0)
        )
        return total + captain.get("predicted_points",0)


    def optimize_squad(self, chip: str | None = None) -> dict:
        """
        Find the best 15-man squad maximizing total xPts.
        Then pick the best starting XI from that squad.
        """
        squad = self._solve_ilp_squad()
        if not squad:
            return {
                "squad": [],
                "error": "No feasible squad found within budget/constraints."
            }

        # Select starting XI (best 11 from 15)
        starting_xi, bench = self._select_best_xi(squad, chip)

        # Captain & vice-captain
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

    def _solve_ilp_squad(self) -> list:
        """
        Exact 0/1 ILP replacement for beam search. Guarantees the globally
        optimal 15-man squad (by predicted_points + captain bonus) under
        budget, position-quota, and per-team constraints — no heuristic
        pruning, no candidate-pool truncation, no risk of a top-xPts player
        being excluded by search approximation.
        """
        from pulp import LpProblem, LpMaximize, LpVariable, lpSum, LpBinary, PULP_CBC_CMD, LpStatus

        players = self._get_eligible_players()
        n = len(players)
        if n < 15:
            return []

        prob = LpProblem("fpl_squad", LpMaximize)
        x = [LpVariable(f"x_{i}", cat=LpBinary) for i in range(n)]
        c = [LpVariable(f"c_{i}", cat=LpBinary) for i in range(n)]

        pts = [p["predicted_points"] for p in players]
        price = [p.get("price", 0) for p in players]
        pos = [p.get("position_id", 0) for p in players]
        team = [p.get("team_id", p.get("team", 0)) for p in players]

        prob += lpSum(x[i] * pts[i] for i in range(n)) + lpSum(c[i] * pts[i] for i in range(n))

        prob += lpSum(x) == 15
        for pos_id, quota in ((1, 2), (2, 5), (3, 5), (4, 3)):
            prob += lpSum(x[i] for i in range(n) if pos[i] == pos_id) == quota

        prob += lpSum(x[i] * price[i] for i in range(n)) <= self.budget

        for tid in set(team):
            prob += lpSum(x[i] for i in range(n) if team[i] == tid) <= MAX_PER_TEAM

        for i in range(n):
            prob += c[i] <= x[i]
        prob += lpSum(c) == 1

        status = prob.solve(PULP_CBC_CMD(msg=0))
        if LpStatus[status] != "Optimal":
            return []

        # debug temp
        selected = [players[i] for i in range(n) if x[i].value() == 1]
        watch = {
            "Dowman",
            "B.Fernandes",
            "Saka",
            "Cherki",
            "Foden",
            "Semenyo",
        }
        print("\n=== WATCH PLAYERS ===")
        for p in players:
            if p.get("web_name") in watch:
                print(
                    f"{p['web_name']:12}"
                    f" selected={p in selected}"
                    f" xPts={p['predicted_points']:.2f}"
                    f" price={p['price']:.1f}"
                    f" conf={p.get('confidence',0):.2f}"
                    f" tier={p.get("starter_quality", {}).get("tier", "")}"
                )
        return selected

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
            bb_optimizer.optimize_squad(chip="bench_boost")
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
