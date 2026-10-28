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
        try:
            squad = self._solve_ilp_squad()
        except (ImportError, OSError) as exc:
            print(f"[OPTIMIZER] ILP solver unavailable; using heuristic: {exc}")
            squad = self._optimize_full_squad()
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
        """Bounded fallback search; enforce budget, quotas and team limits.

        Unlike the ILP solver this heuristic does not guarantee optimality.
        Retain cheap candidates and a low-cost branch so expensive early
        selections do not exhaust the budget for the remaining positions.
        """
        quotas = {1: 2, 2: 5, 3: 5, 4: 3}
        by_pos = {pos: [] for pos in quotas}
        for player in self._get_eligible_players():
            pos = player.get("position_id")
            if pos in by_pos:
                by_pos[pos].append(player)
        for pos, quota in quotas.items():
            pool = by_pos[pos]
            if len(pool) < quota:
                return []
            top = sorted(pool, key=lambda p: p["predicted_points"], reverse=True)[:40]
            cheap = sorted(pool, key=lambda p: p.get("price", 0))[:15]
            by_pos[pos] = list({p["player_id"]: p for p in top + cheap}.values())

        slots = [pos for pos, count in quotas.items() for _ in range(count)]
        min_price = {pos: min(p.get("price", 0) for p in pool) for pos, pool in by_pos.items()}
        # score, cost, selected players, team counts, last candidate index
        states = [(0.0, 0.0, (), {}, -1)]
        for depth, pos in enumerate(slots):
            following = slots[depth + 1:]
            lower_bound = sum(min_price[remaining_pos] for remaining_pos in following)
            next_states = []
            same_position = depth > 0 and slots[depth - 1] == pos
            for score, cost, selected, counts, last_index in states:
                start = last_index + 1 if same_position else 0
                for index in range(start, len(by_pos[pos])):
                    player = by_pos[pos][index]
                    team = player.get("team_id", player.get("team", 0))
                    if counts.get(team, 0) >= MAX_PER_TEAM:
                        continue
                    new_cost = cost + player.get("price", 0)
                    if new_cost + lower_bound > self.budget + 1e-6:
                        continue
                    new_counts = dict(counts)
                    new_counts[team] = new_counts.get(team, 0) + 1
                    next_states.append((score + player["predicted_points"], new_cost,
                                        selected + (player,), new_counts, index))
            if not next_states:
                return []
            states = heapq.nlargest(80, next_states, key=lambda state: (state[0], -state[1]))
            cheapest = min(next_states, key=lambda state: (state[1], -state[0]))
            if all(state is not cheapest for state in states):
                states[-1] = cheapest
        return list(max(states, key=lambda state: self._squad_score(state[2]))[2])


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

        from pulp import PulpSolverError
        try:
            status = prob.solve(PULP_CBC_CMD(msg=0))
        except PulpSolverError as exc:
            print(f"[OPTIMIZER] ILP solver failed; using heuristic: {exc}")
            return self._optimize_full_squad()
        if LpStatus[status] != "Optimal":
            return []

        # debug temp
        return [players[i] for i in range(n) if x[i].value() == 1]

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
    """Thin adapter around SeasonChipPlanner for current-GW scoring."""

    def __init__(self, predictions: list[dict], gw_info: dict, engine=None,):
        self.predictions = predictions
        self.gw_info = gw_info
        self.engine = engine

    def analyze(self, current_squad_ids: list[int] | None = None,chips_available: list[str] | None = None) -> dict:
        """Score the current GW using SeasonChipPlanner."""

        from chip_planner import SeasonChipPlanner

        if chips_available is None:
            chips_available = [
                "wildcard",
                "free_hit",
                "bench_boost",
                "triple_captain",
            ]

        if self.engine is None:
            raise RuntimeError(
                "ChipAdvisor requires an existing PredictionEngine."
            )

        planner = SeasonChipPlanner(
            engine=self.engine
        )

        gw = self.gw_info.get(
            "gameweek",
            0,
        )

        meta = {
            "is_dgw": self.gw_info.get(
                "is_dgw",
                False,
            ),
            "dgw_team_count": len(
                self.gw_info.get(
                    "dgw_teams",
                    {},
                )
            ),
            "is_bgw": self.gw_info.get(
                "is_bgw",
                False,
            ),
            "bgw_team_count": len(
                self.gw_info.get(
                    "bgw_teams",
                    {},
                )
            ),
            "total_fixtures": self.gw_info.get(
                "total_fixtures",
                10,
            ),
        }

        # Make sure the planner's engine sees the same
        # current-GW predictions.
        self.engine._baseline_predictions = self.predictions

        # Keep planner-level copy synchronized too.
        planner._baseline_predictions = self.predictions

        chip_definitions = [
            (
                "bench_boost",
                "BB",
                "Bench Boost",
                "_score_bb",
            ),
            (
                "triple_captain",
                "TC",
                "Triple Captain",
                "_score_tc",
            ),
            (
                "free_hit",
                "FH",
                "Free Hit",
                "_score_fh",
            ),
            (
                "wildcard",
                "WC",
                "Wildcard",
                "_score_wc",
            ),
        ]

        recommendations = []

        for (
            chip_key,
            code,
            name,
            method_name,
        ) in chip_definitions:

            if chip_key not in chips_available:
                continue

            score_fn = getattr(
                planner,
                method_name,
            )

            if method_name == "_score_bb":

                score, reason, details = score_fn(
                    gw,
                    meta,
                    self.predictions,
                    self.gw_info,
                    current_squad_ids,
                )

            else:

                score, reason, details = score_fn(
                    gw,
                    meta,
                    self.predictions,
                    current_squad_ids,
                )

            recommendation = {
                "chip": chip_key,
                "name": name,
                "code": code,
                "score": (
                    score
                    if score is not None
                    else 0
                ),
                "reasons": (
                    [reason]
                    if reason
                    else []
                ),
                "score_available": (
                    score is not None
                ),
            }

            if details:
                recommendation.update(
                    details
                )

            recommendations.append(
                recommendation
            )

        recommendations.sort(
            key=lambda x: x["score"],
            reverse=True,
        )

        best = next(
            (
                r
                for r in recommendations
                if r.get(
                    "score_available",
                    True,
                )
            ),
            None,
        )

        return {
            "gameweek": gw,
            "is_dgw": meta["is_dgw"],
            "dgw_team_count": meta["dgw_team_count"],
            "total_fixtures": meta["total_fixtures"],
            "recommendations": recommendations,
            "best_chip": best,
            "save_chips": (
                not meta["is_dgw"]
                and meta["total_fixtures"] >= 8
            ),
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
