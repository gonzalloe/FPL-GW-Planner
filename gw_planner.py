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

    def __init__(self, horizon: int = 5, baseline_predictions=None, baseline_gw=None):
        """
        Args:
            horizon: How many GWs ahead to plan (default 5)
        """
        self._projection_planner = None
        self._baseline_gw = baseline_gw
        self._baseline_predictions = baseline_predictions
        if baseline_predictions is None:
            self.engine = PredictionEngine()
        else:
            from types import SimpleNamespace
            from chip_planner import SeasonChipPlanner
            bootstrap = fetch_bootstrap()
            fixtures = fetch_fixtures()
            players = build_player_map(bootstrap)
            teams = build_team_map(bootstrap)
            next_gw = get_next_gameweek(bootstrap)
            self.engine = SimpleNamespace(
                bootstrap=bootstrap, fixtures=fixtures, players=players,
                teams=teams, team_stats={}, current_gw=get_current_gameweek(bootstrap),
                next_gw=next_gw,
            )
            if int(baseline_gw or 0) != int(next_gw):
                raise ValueError(f"Cached predictions are for GW{baseline_gw}; GW{next_gw} predictions are needed.")
            self._projection_planner = SeasonChipPlanner(
                bootstrap=bootstrap, fixtures=fixtures, players=players,
                teams=teams, next_gw=next_gw, baseline_predictions=baseline_predictions,
            )
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
        self._gw_pred_maps = {}
        self._gw_infos = {}

    def _ensure_gw_predictions(self, gw: int):
        """Lazy-load predictions for a gameweek."""
        if gw not in self._gw_predictions:
            if self._projection_planner is None:
                preds = self.engine.predict_all(gw)
                gw_info = self.engine.get_gw_info(gw)
            else:
                preds = (self._baseline_predictions if gw == self._baseline_gw
                         else self._projection_planner._get_target_predictions(gw))
                gw_info = PredictionEngine.get_gw_info(self.engine, gw)
            self._gw_predictions[gw] = preds
            self._gw_infos[gw] = gw_info

    def _get_pred_map(self, gw: int) -> dict:
        """Get {player_id: prediction} map for a GW."""
        self._ensure_gw_predictions(gw)
        if gw not in self._gw_pred_maps:
            self._gw_pred_maps[gw] = {p["player_id"]: p for p in self._gw_predictions[gw]}
        return self._gw_pred_maps[gw]

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
                is_blank = len(opponents) == 0
                is_dgw = tid in dgw_teams and len(opponents) >= 2

                # Avg FDR calculation:
                # - BGW: treated as 5.5 (penalty for no game, slightly above hardest FDR=5)
                # - DGW: sum FDR / num_fixtures (low avg = good two games), but
                #        bonus: subtract 0.5 to reward having double fixture
                # - SGW: plain average
                if is_blank:
                    effective_fdr = 5.5
                elif is_dgw:
                    raw_avg = sum(o["fdr"] for o in opponents) / len(opponents)
                    effective_fdr = round(max(1.0, raw_avg - 0.5), 1)  # DGW bonus
                else:
                    effective_fdr = round(sum(o["fdr"] for o in opponents) / max(len(opponents), 1), 1)

                team_fixtures.append({
                    "gw": gw,
                    "opponents": opponents,
                    "is_dgw": is_dgw,
                    "is_blank": is_blank,
                    "fixture_count": len(opponents),
                    "avg_fdr": effective_fdr,
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

    def plan_transfers(self, current_squad_ids: list[int], bank: float = 0.0,
                       free_transfers: int = 1, chips_available: list[str] | None = None,
                       max_transfers_per_gw: int = 2, selling_prices: dict | None = None,
                       strategy: str = 'balanced') -> dict:
        """Compare bounded beam searches using expected net points over the horizon.

        Chip suggestions are advisory: chip use is not part of the transfer search.
        Selling prices are in £m; absent prices fall back to current market values.
        """
        from transfer_search import TransferSearch

        gameweeks = list(range(self.next_gw, min(self.next_gw + self.horizon, 39)))
        maps = {gw: self._get_pred_map(gw) for gw in gameweeks}
        search = TransferSearch(self.players, maps, gameweeks)
        search.validate(current_squad_ids)
        ft = max(0, min(5, int(free_transfers)))
        prices = selling_prices or {}
        sell = {pid: round(float(prices[pid]) * 10) if prices.get(pid) is not None
                else search.price(pid) for pid in current_squad_ids}
        initial_bank = round(float(bank) * 10)
        strategies = {
            'conservative': (min(1, max_transfers_per_gw), False),
            'balanced': (min(2, max_transfers_per_gw), False),
            'aggressive': (min(2, max_transfers_per_gw), True),
        }
        results = {name: search.run(current_squad_ids, initial_bank, ft, sell, limit, hits)
                   for name, (limit, hits) in strategies.items()}
        # Wider strategy sets can always reuse a narrower strategy's plan.
        if results['conservative'][4] > results['balanced'][4]:
            results['balanced'] = results['conservative']
        if results['balanced'][4] > results['aggressive'][4]:
            results['aggressive'] = results['balanced']
        # The default is balanced; all strategies use the same net-points objective.
        if strategy not in results:
            raise ValueError('Unknown transfer strategy.')
        chosen = results[strategy]
        baseline = sum(search.lineup(tuple(sorted(current_squad_ids)), gw)[0] for gw in gameweeks)
        gw_plans = []
        for gw, record in zip(gameweeks, chosen[5]):
            old, old_bank, old_ft, old_sell, new, new_bank, moves, hits, gross = record
            pred_map = maps[gw]
            def enriched(pid):
                p, pred = self.players[pid], pred_map.get(pid, {})
                return {'player_id': pid, 'name': p.get('web_name', 'Unknown'),
                        'team': p.get('team_short', pred.get('team', '???')),
                        'team_id': p.get('team', 0), 'position': p.get('position_name', pred.get('position', '???')),
                        'position_id': search.position(pid), 'price': search.price(pid) / 10,
                        'predicted_points': search.points(pid, gw), 'raw_xpts': pred.get('raw_xpts', 0),
                        'is_dgw': pred.get('is_dgw', False), 'num_fixtures': pred.get('num_fixtures', 0),
                        'form': float(p.get('form', 0) or 0), 'status': p.get('status', 'a'),
                        'chance_of_playing': p.get('chance_of_playing_next_round'), 'news': p.get('news', ''),
                        'fixtures': pred.get('fixtures', []), 'starter_quality': pred.get('starter_quality', {}),
                        'availability': pred.get('availability', {})}
            squad_before = [enriched(pid) for pid in old]
            transfers = []
            for i, (out_id, in_id) in enumerate(moves):
                out, incoming = enriched(out_id), enriched(in_id)
                out_price = old_sell.get(out_id, search.price(out_id)) / 10
                in_price = search.price(in_id) / 10
                value = self._quick_multi_gw_value(in_id, out_id, gw)
                transfers.append({'out_id': out_id, 'out_name': out['name'], 'out_team': out['team'],
                    'out_position': out['position'], 'out_price': out_price, 'out_xpts': out['predicted_points'],
                    'out_fixtures': out['fixtures'], 'in_id': in_id, 'in_name': incoming['name'],
                    'in_team': incoming['team'], 'in_position': incoming['position'], 'in_price': in_price,
                    'in_xpts': incoming['predicted_points'], 'in_fixtures': incoming['fixtures'],
                    'in_is_dgw': incoming['is_dgw'], 'in_form': incoming['form'],
                    'in_starter_tier': incoming['starter_quality'].get('tier', 'unknown'),
                    'xpts_gain_this_gw': round(incoming['predicted_points'] - out['predicted_points'], 2),
                    'multi_gw_value': round(value, 2), 'cost_delta': round(in_price - out_price, 1),
                    'is_free': i < old_ft, 'is_hit': i >= old_ft})
            before = search.lineup(old, gw)[0]
            _, starters, captain = search.lineup(new, gw)
            chip = self._recommend_chip(squad_before, self._gw_infos.get(gw, {}), gw,
                                        set(chips_available or []), pred_map)
            if chip:
                chip['use_chip'] = False
            gw_plans.append({'gameweek': gw, 'gw_info': self._gw_infos.get(gw, {}),
                'squad_before': squad_before, 'squad_after': list(new), 'starting_xi': list(starters),
                'captain_id': captain, 'baseline_xpts': round(before, 1), 'transfers': transfers,
                'num_transfers': len(moves), 'free_transfers_available': old_ft,
                'free_transfers_used': min(len(moves), old_ft), 'hits': hits, 'hit_cost': 4 * hits,
                'net_spend': (old_bank - new_bank) / 10, 'bank_before': old_bank / 10,
                'bank_after': new_bank / 10, 'post_transfer_xpts': round(gross - 4 * hits, 1),
                'xpts_gain': round(gross - 4 * hits - before, 1),
                'multi_gw_value': self._calc_multi_gw_transfer_value(transfers, gw), 'chip_recommendation': chip})
        hit_cost = sum(g['hit_cost'] for g in gw_plans)
        net_gain = chosen[4] - baseline
        return {'horizon': len(gameweeks), 'from_gw': self.next_gw, 'to_gw': gameweeks[-1],
            'gw_plans': gw_plans, 'selected_strategy': strategy, 'summary': {'total_transfers': sum(g['num_transfers'] for g in gw_plans),
                'total_hits': sum(g['hits'] for g in gw_plans), 'total_hit_cost': hit_cost,
                'total_xpts_gain': round(net_gain + hit_cost, 1), 'net_gain_after_hits': round(net_gain, 1),
                'expected_net_points': round(chosen[4], 1), 'save_transfer_points': round(baseline, 1),
                'chips_used': [], 'final_bank': chosen[1] / 10},
            'strategies': {name: {'expected_net_points': round(state[4], 1),
                 'transfers': sum(len(r[6]) for r in state[5]), 'hits': sum(r[7] for r in state[5]),
                 'roll_gameweeks': sum(not r[6] for r in state[5])}
                 for name, state in results.items()},
            'assumptions': {'chip_use': 'advisory only', 'prices': 'constant',
                'selling_prices_estimated': any(prices.get(pid) is None for pid in current_squad_ids)},
            'fixture_ticker': self._build_squad_ticker(chosen[0])}

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
        lookahead = min(3, 38 - from_gw, self.next_gw + self.horizon - 1 - from_gw)

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
            for gw in range(from_gw, min(self.next_gw + self.horizon, 39)):
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
            low_xpts = sum(
                1
                for p in squad_enriched
                if p["predicted_points"] < 3
            )

            flagged = sum(
                1
                for p in squad_enriched
                if p.get("status") not in ("a", None)
            )

            fixture_swings = self._count_fixture_swings(squad_enriched, gw)

            wc_score = min(
                100,
                low_xpts * 5
                + flagged * 8
                + fixture_swings * 3
            )

            scores["WC"] = {
                "score": wc_score,
                "reason": (
                    f"{low_xpts} low-xPts players, "
                    f"{flagged} flagged. "
                    f"Fixture swing score: {fixture_swings}."
                ),
            }

        if not scores:
            return None

        # Find best chip
        best_chip = max(scores, key=lambda k: scores[k]["score"])
        best_score = scores[best_chip]["score"]

        return {
            "chip_code": best_chip,
            "score": best_score,
            "use_chip": (best_score >= 70 and best_chip in {"BB", "TC", "FH", "WC"}),
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
    def plan_from_team_id(self, team_id: int, horizon: int = None, free_transfers: int | None = None, team_data: dict | None = None, strategy: str = 'balanced') -> dict:
        """Fetch team from FPL API and generate plan."""
        from my_team import fetch_my_team

        if horizon is not None:
            self.horizon = min(horizon, 38 - self.next_gw + 1)

        if team_data is None:
            team_data = fetch_my_team(team_id)
        if team_data.get("error"):
            return {"error": team_data["error"]}

        picks = team_data.get("picks", [])
        if not picks:
            return {
                "preseason": True,
                "score_status": "unavailable",
                "error": None,
                "message": "GW Planner will activate after GW1 starts",
                "from_gw": self.next_gw,
                "to_gw": self.next_gw,
                "gw_plans": [],
                "chips_remaining": ["BB", "TC", "FH", "WC"],
                "chips_used_this_season": [],
            }

        squad_ids = [p["element"] for p in picks]
        bank = team_data.get("gw_summary", {}).get("bank", 0)

        # ------------------------------------------------------------
        # Chip availability
        # ------------------------------------------------------------

        chip_map = {"wildcard": "WC", "freehit": "FH", "bboost": "BB", "3xc": "TC"}
        chip_usage = {1: set(), 2: set()}

        for chip in team_data.get("chips", []):
            name = chip.get("name")
            code = chip_map.get(name)
            if not code:
                continue

            chip_gw = chip.get("event")
            if chip_gw is None:
                continue

            chip_half = 1 if int(chip_gw) <= 19 else 2
            chip_usage[chip_half].add(code)

        current_half = 1 if self.next_gw <= 19 else 2
        chips_available = [
            code for code in ["WC", "FH", "BB", "TC"]
            if code not in chip_usage[current_half]
        ]

        print(
            f"[CHIPS] Current GW={self.current_gw} "
            f"half={current_half} "
            f"usage={chip_usage[current_half]} "
            f"available={chips_available}"
        )

        # ------------------------------------------------------------
        # Free transfers
        # ------------------------------------------------------------
        # Public history gives an entering allowance for a specific GW; it
        # cannot reliably confirm transfers already made before the deadline.
        if free_transfers is not None:
            ft = max(0, min(int(free_transfers), 5))
            ft_source = "explicit_override"
        else:
            from my_team import free_transfers_for_gameweek
            ft, ft_source = free_transfers_for_gameweek(team_data, self.next_gw)
            if ft is None:
                return {"error": f"Free transfers for GW{self.next_gw} are unavailable. "
                                 "Refresh your imported team and try again."}
            ft = max(0, min(int(ft), 5))

        print("[GW PLANNER FT]", {
            "planning_gw": self.next_gw,
            "team_data_free_transfers": team_data.get("free_transfers"),
            "team_data_starting_free_transfers": team_data.get("starting_free_transfers"),
            "planner_ft": ft,
            "ft_source": ft_source,
        })

        # Public picks often omit selling prices. Recover purchase costs from
        # permanent transfer history where available; original purchases remain unknown.
        fh_events = {int(c['event']) for c in team_data.get('chips', [])
                     if c.get('name') == 'freehit' and c.get('event') is not None}
        acquisitions = {}
        for transfer in sorted(team_data.get('transfers', []), key=lambda t: (t.get('event', 0), t.get('time', ''))):
            if transfer.get('event') not in fh_events and transfer.get('element_in_cost') is not None:
                acquisitions[transfer.get('element_in')] = int(transfer['element_in_cost'])
        selling_prices = {}
        for pick in picks:
            pid = pick['element']
            if pick.get('selling_price') is not None:
                selling_prices[pid] = pick['selling_price'] / 10
            elif pid in acquisitions and pid in self.players:
                cost, purchase = self.players[pid]['now_cost'], acquisitions[pid]
                selling_prices[pid] = (cost if cost <= purchase else purchase + (cost - purchase) // 2) / 10
        plan = self.plan_transfers(
            current_squad_ids=squad_ids,
            bank=bank,
            free_transfers=ft,
            chips_available=chips_available,
            selling_prices=selling_prices,
            strategy=strategy,
        )

        chips_used = {code for used in chip_usage.values() for code in used}

        plan["team_info"] = team_data.get("info", {})
        plan["chips_used_this_season"] = sorted(chips_used)
        plan["chips_remaining"] = chips_available
        plan["free_transfers"] = ft
        plan["free_transfers_source"] = ft_source
        plan["prediction_source"] = "cached_fixture_projection" if self._projection_planner else "full_model"

        return plan

