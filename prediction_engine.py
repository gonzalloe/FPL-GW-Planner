"""
FPL Predictor - Prediction Engine v3
Multi-factor model with DGW/BGW, team H2H, win rates, fixture xG/xGC.
"""
import math
from config import (
    SCORING, PREDICTION_WEIGHTS, FDR_MULTIPLIER,
    HOME_BONUS, AWAY_PENALTY, POSITION_LIMITS
)
from data_fetcher import (
    fetch_bootstrap, fetch_fixtures, fetch_player_detail,
    build_player_map, build_team_map, get_player_fixture,
    get_player_fixtures, get_dgw_teams, get_bgw_teams,
    get_next_gameweek, get_current_gameweek
)
from team_analysis import (
    build_team_stats, get_h2h, get_fixture_xg,
    calc_team_momentum, get_team_analysis_summary
)


class PredictionEngine:
    """
    Multi-factor prediction model for FPL player points.
    v3: Team H2H, win rates, fixture-specific xG/xGC, momentum.
    """

    def __init__(self):
        self.bootstrap = fetch_bootstrap()
        self.fixtures = fetch_fixtures()
        self.players = build_player_map(self.bootstrap)
        self.teams = build_team_map(self.bootstrap)
        self.current_gw = get_current_gameweek(self.bootstrap)
        self.next_gw = get_next_gameweek(self.bootstrap)
        # DGW/BGW data
        self.dgw_teams = {}
        self.bgw_teams = set()
        # NEW: Build team stats from finished fixtures
        self.team_stats = build_team_stats(self.fixtures, self.teams)

    def predict_player(self, player_id: int, target_gw: int | None = None) -> dict:
        """
        Predict expected points for a player in the target gameweek.
        DGW-aware: sums predictions across all fixtures.
        Uses fixture-specific xG/xGC from team analysis.
        """
        if target_gw is None:
            target_gw = self.next_gw

        p = self.players.get(player_id)
        if not p:
            return {"player_id": player_id, "error": "Player not found"}

        # Skip unavailable players
        availability = self._get_availability(p)
        if availability["status"] == "unavailable":
            return self._empty_prediction(p, availability)

        # Get ALL fixtures for this player's team in target GW
        all_fixtures = get_player_fixtures(p["team"], target_gw, self.fixtures)

        if not all_fixtures:
            return self._empty_prediction(p, {"status": "blank_gw"})

        # ── Starter quality check ──
        starter_quality = self._assess_starter_quality(p)

        # ── Predict for EACH fixture, then sum ──
        total_xp_raw = 0.0
        total_xp_adjusted = 0.0
        fixture_details = []
        all_factors = {}

        for fix_info in all_fixtures:
            # NEW: Get fixture-specific xG/xGC from team analysis
            fix_xg_data = get_fixture_xg(
                p["team"], fix_info["opponent_id"],
                fix_info["is_home"], self.team_stats
            )

            factors = self._calc_all_factors(p, fix_info, fix_xg_data)
            base_xp = self._calc_base_expected_points(p, fix_info, starter_quality, fix_xg_data)
            weighted_mod = sum(factors[k] * PREDICTION_WEIGHTS[k] for k in factors)
            fixture_xp = base_xp * (1 + weighted_mod)

            # Apply starter quality multiplier
            fixture_xp *= starter_quality["multiplier"]
            fixture_xp = max(0.0, fixture_xp)

            # Raw xPts = if the player plays
            total_xp_raw += fixture_xp

            # Risk-adjusted
            adjusted_xp = fixture_xp
            if availability["status"] == "doubtful":
                chance = availability.get("chance", 50)
                if chance >= 75:
                    adjusted_xp = fixture_xp  # Full points
                elif chance >= 50:
                    adjusted_xp = fixture_xp * 0.50
                elif chance >= 25:
                    adjusted_xp = fixture_xp * 0.25
                else:
                    adjusted_xp = fixture_xp * 0.10
            adjusted_xp = max(0.0, adjusted_xp)
            total_xp_adjusted += adjusted_xp

            opponent_team = self.teams.get(fix_info["opponent_id"], {})

            # NEW: Get team analysis for this fixture
            team_summary = get_team_analysis_summary(
                p["team"], fix_info["opponent_id"],
                fix_info["is_home"], self.team_stats, self.teams
            )

            fixture_details.append({
                "opponent": opponent_team.get("short_name", "???"),
                "opponent_full": opponent_team.get("name", "Unknown"),
                "is_home": fix_info["is_home"],
                "fdr": fix_info["fdr"],
                "venue": "H" if fix_info["is_home"] else "A",
                "xp_single": round(fixture_xp, 2),
                "xp_adjusted": round(adjusted_xp, 2),
                # NEW: fixture-specific team data
                "fixture_xg": fix_xg_data["team_xg"],
                "fixture_xgc": fix_xg_data["team_xgc"],
                "cs_probability": fix_xg_data["cs_probability"],
                "h2h": fix_xg_data["h2h"],
                "team_form": team_summary["last5_form"],
                "team_last5_wr": team_summary["last5_win_rate"],
                "opp_form": team_summary["opp_last5_form"],
                "opp_last5_wr": team_summary["opp_last5_win_rate"],
                "momentum": team_summary["momentum"],
            })

            # Aggregate factors
            for k, v in factors.items():
                all_factors[k] = all_factors.get(k, 0) + v / len(all_fixtures)

        # Cap at reasonable max
        max_pts = 20.0 * len(all_fixtures)
        total_xp_raw = min(total_xp_raw, max_pts)
        total_xp_adjusted = min(total_xp_adjusted, max_pts)

        # Confidence
        confidence = self._calc_confidence(p, all_fixtures, starter_quality)
        if availability["status"] == "doubtful":
            chance = availability.get("chance", 50)
            if chance >= 75:
                confidence *= 0.95
            elif chance >= 50:
                confidence *= 0.75
            else:
                confidence *= 0.50

        num_fixtures = len(all_fixtures)
        is_dgw = num_fixtures >= 2

        # NEW: Team-level summary for display
        team_id = p.get("team", 0)
        ts = self.team_stats.get(team_id, {})

        return {
            "player_id": player_id,
            "name": p.get("web_name", "Unknown"),
            "full_name": f"{p.get('first_name', '')} {p.get('second_name', '')}".strip(),
            "team": p.get("team_short", "???"),
            "team_name": p.get("team_name", "Unknown"),
            "team_id": p.get("team", 0),
            "position": p.get("position_name", "???"),
            "position_id": p.get("position_id", 0),
            "price": p.get("now_cost", 0) / 10,
            "selected_by_percent": p.get("selected_by_percent", "0"),
            "predicted_points": round(total_xp_adjusted, 2),
            "raw_xpts": round(total_xp_raw, 2),
            "fixtures": fixture_details,
            "fixture": fixture_details[0] if fixture_details else {},
            "num_fixtures": num_fixtures,
            "is_dgw": is_dgw,
            "availability": availability,
            "starter_quality": starter_quality,
            "factors": {k: round(v, 4) for k, v in all_factors.items()},
            "confidence": round(confidence, 2),
            "base_xp": round(total_xp_raw, 2),
            # Player stats
            "minutes": p.get("minutes", 0),
            "starts": p.get("starts", 0),
            "form": float(p.get("form", 0)),
            "ppg": float(p.get("points_per_game", 0)),
            "total_points": p.get("total_points", 0),
            "ict_index": float(p.get("ict_index", 0)),
            # Injury info
            "news": p.get("news", ""),
            "status_code": p.get("status", "a"),
            # NEW: Team context
            "team_last5_form": ts.get("last5_form_str", ""),
            "team_last5_wr": round(ts.get("last5_win_rate", 0), 3),
            "team_season_wr": round(ts.get("win_rate", 0), 3),
            "team_momentum": round(calc_team_momentum(self.team_stats, team_id), 3),
        }

    def predict_all(self, target_gw: int | None = None,
                    min_chance: int = 25) -> list[dict]:
        """Predict points for all eligible players."""
        if target_gw is None:
            target_gw = self.next_gw

        self.dgw_teams = get_dgw_teams(target_gw, self.fixtures)
        self.bgw_teams = get_bgw_teams(target_gw, self.fixtures, self.bootstrap)

        results = []
        for pid, p in self.players.items():
            chance = p.get("chance_of_playing_next_round")
            if chance is not None and chance < min_chance:
                continue

            if self.current_gw > 5:
                min_minutes = p.get("minutes", 0)
                avg_mins_per_gw = min_minutes / max(self.current_gw - 1, 1)
                if avg_mins_per_gw < 5:
                    continue

            pred = self.predict_player(pid, target_gw)
            if pred.get("predicted_points", 0) > 0:
                results.append(pred)

        results.sort(key=lambda x: x["predicted_points"], reverse=True)
        return results

    def get_gw_info(self, target_gw: int | None = None) -> dict:
        """Get gameweek metadata."""
        if target_gw is None:
            target_gw = self.next_gw
        dgw = get_dgw_teams(target_gw, self.fixtures)
        bgw = get_bgw_teams(target_gw, self.fixtures, self.bootstrap)
        from data_fetcher import get_fixtures_for_gameweek
        fixtures = get_fixtures_for_gameweek(target_gw, self.fixtures)
        return {
            "gameweek": target_gw,
            "total_fixtures": len(fixtures),
            "is_dgw": len(dgw) > 0,
            "dgw_teams": {
                tid: {
                    "name": self.teams.get(tid, {}).get("name", "???"),
                    "short_name": self.teams.get(tid, {}).get("short_name", "???"),
                    "fixture_count": cnt,
                }
                for tid, cnt in dgw.items()
            },
            "bgw_teams": {
                tid: self.teams.get(tid, {}).get("name", "???")
                for tid in bgw
            },
        }

    # ── Starter quality assessment ────────────────────────────

    def _assess_starter_quality(self, p: dict) -> dict:
        total_minutes = int(p.get("minutes", 0))
        starts = int(p.get("starts", 0))
        gws_played = max(self.current_gw - 1, 1)
        max_possible_minutes = gws_played * 90
        avg_mins = total_minutes / gws_played
        start_rate = starts / gws_played if gws_played > 0 else 0
        minutes_pct = total_minutes / max_possible_minutes if max_possible_minutes > 0 else 0

        if start_rate >= 0.75 and avg_mins >= 65:
            tier = "nailed"
            multiplier = 1.0
        elif start_rate >= 0.50 and avg_mins >= 45:
            tier = "regular"
            multiplier = 0.90
        elif start_rate >= 0.30 and avg_mins >= 25:
            tier = "rotation"
            multiplier = 0.65
        elif avg_mins >= 10:
            tier = "fringe"
            multiplier = 0.35
        else:
            tier = "bench_warmer"
            multiplier = 0.10

        return {
            "tier": tier, "multiplier": multiplier,
            "avg_mins": round(avg_mins, 1), "start_rate": round(start_rate, 2),
            "minutes_pct": round(minutes_pct, 2), "starts": starts,
            "total_minutes": total_minutes,
        }

    # ── Factor calculations (now 12 factors) ──────────────────

    def _calc_all_factors(self, p: dict, fixture_info: dict,
                          fix_xg_data: dict) -> dict:
        """Calculate all prediction factors for a single fixture."""
        return {
            "form": self._calc_form(p),
            "fixture_difficulty": self._calc_fixture_factor(p, fixture_info["fdr"], fixture_info["is_home"]),
            "season_avg": self._calc_season_avg(p),
            "home_away": self._calc_home_away(fixture_info["is_home"]),
            "ict_index": self._calc_ict(p),
            "minutes_consistency": self._calc_minutes_consistency(p),
            "team_strength": self._calc_team_strength(p, fixture_info["is_home"]),
            "set_pieces": self._calc_set_piece_bonus(p),
            "ownership_momentum": self._calc_transfer_momentum(p),
            "bonus_tendency": self._calc_bonus_tendency(p),
            # NEW factors
            "team_form": self._calc_team_form_factor(p),
            "h2h_factor": self._calc_h2h_factor(p, fixture_info, fix_xg_data),
        }

    def _calc_form(self, p: dict) -> float:
        form = float(p.get("form", 0))
        ppg = float(p.get("points_per_game", 0))
        form_score = (form - 3.5) / 5.0
        ppg_score = (ppg - 3.5) / 5.0
        return 0.6 * form_score + 0.4 * ppg_score

    def _calc_fixture_factor(self, p: dict, fdr: int, is_home: bool) -> float:
        multiplier = FDR_MULTIPLIER.get(fdr, 1.0)
        pos = p.get("position_id", 3)
        if pos in (3, 4):
            return (multiplier - 1.0) * 1.2
        else:
            return (multiplier - 1.0) * 0.9

    def _calc_season_avg(self, p: dict) -> float:
        ppg = float(p.get("points_per_game", 0))
        return (ppg - 3.5) / 6.0

    def _calc_home_away(self, is_home: bool) -> float:
        return 0.10 if is_home else -0.08

    def _calc_ict(self, p: dict) -> float:
        ict = float(p.get("ict_index", 0))
        pos_avg = {1: 50, 2: 80, 3: 120, 4: 100}
        avg = pos_avg.get(p.get("position_id", 3), 100)
        games_played = max(int(p.get("starts", 0)), 1)
        ict_per_game = ict / games_played
        avg_per_game = avg / 20
        return (ict_per_game - avg_per_game) / (avg_per_game + 1)

    def _calc_minutes_consistency(self, p: dict) -> float:
        total_minutes = int(p.get("minutes", 0))
        gw_played = max(self.current_gw - 1, 1)
        max_possible = gw_played * 90
        if max_possible == 0:
            return 0.0
        ratio = total_minutes / max_possible
        if ratio > 0.85:
            return 0.2
        elif ratio > 0.65:
            return 0.05
        elif ratio > 0.40:
            return -0.1
        else:
            return -0.3

    def _calc_team_strength(self, p: dict, is_home: bool) -> float:
        if is_home:
            atk = p.get("team_strength_attack_home", 1200)
            defn = p.get("team_strength_defence_home", 1200)
        else:
            atk = p.get("team_strength_attack_away", 1200)
            defn = p.get("team_strength_defence_away", 1200)
        pos = p.get("position_id", 3)
        if pos in (3, 4):
            return (atk - 1200) / 300
        else:
            return (defn - 1200) / 300

    def _calc_set_piece_bonus(self, p: dict) -> float:
        pen_order = p.get("penalties_order")
        corner_order = p.get("corners_and_indirect_freekicks_order")
        direct_fk = p.get("direct_freekicks_order")
        bonus = 0.0
        if pen_order is not None and pen_order <= 2:
            bonus += 0.4 if pen_order == 1 else 0.15
        if corner_order is not None and corner_order <= 2:
            bonus += 0.15
        if direct_fk is not None and direct_fk <= 2:
            bonus += 0.1
        return bonus

    def _calc_transfer_momentum(self, p: dict) -> float:
        transfers_in = int(p.get("transfers_in_event", 0))
        transfers_out = int(p.get("transfers_out_event", 0))
        net = transfers_in - transfers_out
        if net > 100000:
            return 0.3
        elif net > 50000:
            return 0.2
        elif net > 10000:
            return 0.1
        elif net < -100000:
            return -0.2
        elif net < -50000:
            return -0.1
        else:
            return 0.0

    def _calc_bonus_tendency(self, p: dict) -> float:
        bonus = int(p.get("bonus", 0))
        starts = max(int(p.get("starts", 0)), 1)
        bonus_per_start = bonus / starts
        return (bonus_per_start - 0.4) / 1.0

    # ── NEW FACTORS ──────────────────────────────────────────

    def _calc_team_form_factor(self, p: dict) -> float:
        """
        Team's recent form (last 5 matches win rate + momentum).
        A team on a hot streak → players more likely to score.
        """
        team_id = p.get("team", 0)
        ts = self.team_stats.get(team_id, {})
        momentum = calc_team_momentum(self.team_stats, team_id)

        # Win rate last 5
        l5_wr = ts.get("last5_win_rate", 0.4)
        # Scoring form
        l5_gf = ts.get("last5_gf_pg", 1.3)

        pos = p.get("position_id", 3)
        if pos in (3, 4):
            # Attackers benefit from team scoring form
            score = (l5_wr - 0.4) * 0.5 + (l5_gf - 1.3) * 0.15 + momentum * 0.3
        else:
            # Defenders benefit from team defensive form
            l5_ga = ts.get("last5_ga_pg", 1.3)
            l5_cs = ts.get("last5_cs", 1) / max(len(ts.get("results", [])[-5:]), 1)
            score = (l5_wr - 0.4) * 0.3 + (1.3 - l5_ga) * 0.2 + l5_cs * 0.3 + momentum * 0.2

        return max(-0.4, min(score, 0.4))

    def _calc_h2h_factor(self, p: dict, fixture_info: dict,
                         fix_xg_data: dict) -> float:
        """
        Head-to-head + opponent weakness factor.
        If team dominates this opponent historically → boost.
        If opponent concedes a lot recently → boost for attackers.
        """
        h2h = fix_xg_data.get("h2h", {})
        matches = h2h.get("matches", 0)

        h2h_score = 0.0
        if matches > 0:
            dominance = (h2h["a_wins"] - h2h["b_wins"]) / matches
            gf_advantage = (h2h["a_goals"] - h2h["b_goals"]) / matches
            h2h_score = dominance * 0.15 + gf_advantage * 0.05

        # Opponent weakness (fixture-specific xG tells us how much this
        # team is expected to score)
        fixture_xg = fix_xg_data.get("team_xg", 1.3)
        fixture_xgc = fix_xg_data.get("team_xgc", 1.3)

        pos = p.get("position_id", 3)
        if pos in (3, 4):
            # Attackers: higher team xG against this opponent = good
            xg_bonus = (fixture_xg - 1.3) * 0.15
        else:
            # Defenders: lower team xGC = good (opponent doesn't score much)
            xg_bonus = (1.3 - fixture_xgc) * 0.15

        return max(-0.3, min(h2h_score + xg_bonus, 0.3))

    # ── Base expected points (now uses fixture-specific xG) ──

    def _calc_base_expected_points(self, p: dict, fixture_info: dict,
                                    starter_quality: dict,
                                    fix_xg_data: dict) -> float:
        """
        Calculate base expected points for ONE fixture.
        NOW uses fixture-specific xG/xGC derived from team matchup analysis.
        """
        pos = p.get("position_id", 3)
        starts = max(int(p.get("starts", 0)), 1)

        # Per-game rates from player stats
        xg = float(p.get("expected_goals", 0))
        xa = float(p.get("expected_assists", 0))
        xg_pg = xg / starts
        xa_pg = xa / starts

        # FDR + home/away modifiers
        fdr_mod = FDR_MULTIPLIER.get(fixture_info["fdr"], 1.0)
        home_mod = HOME_BONUS if fixture_info["is_home"] else AWAY_PENALTY

        # NEW: Use fixture-specific team xG to scale player's contribution
        # If team is expected to score 2.5 vs this opponent (above avg 1.35),
        # the player's xG gets a proportional boost
        team_xg = fix_xg_data.get("team_xg", 1.35)
        team_xgc = fix_xg_data.get("team_xgc", 1.35)
        cs_prob = fix_xg_data.get("cs_probability", 0.3)

        # Scoring context multiplier: how much more/less the team
        # is expected to score vs this specific opponent compared to avg
        scoring_context = team_xg / 1.35  # >1 means above-avg scoring expected
        conceding_context = team_xgc / 1.35  # >1 means above-avg goals conceded

        xp = 0.0

        # ── Minutes points ──
        avg_mins = starter_quality["avg_mins"]
        if avg_mins >= 60:
            xp += 2.0
        elif avg_mins >= 30:
            xp += 1.5
        elif avg_mins >= 1:
            xp += 0.8

        # ── Goals (scaled by fixture-specific scoring context) ──
        goal_pts = SCORING["goals"].get(pos, 4)
        # Player xG per start, modified by how this matchup affects team scoring
        effective_xg = xg_pg * scoring_context
        xp += effective_xg * goal_pts * fdr_mod * home_mod

        # ── Assists (also scaled) ──
        effective_xa = xa_pg * scoring_context
        xp += effective_xa * SCORING["assist"] * fdr_mod * home_mod

        # ── Clean sheets (now using fixture-derived CS probability) ──
        cs_pts = SCORING["clean_sheet"].get(pos, 0)
        if cs_pts > 0 and pos in (1, 2):
            # Use fixture-specific CS probability instead of generic xGC
            xp += cs_prob * cs_pts
        elif pos == 3:
            xp += cs_prob * cs_pts * 0.7  # MID CS less impactful

        # ── Goals conceded penalty (for DEF/GKP) ──
        if pos in (1, 2):
            # Expected goals conceded from team analysis
            expected_gc = team_xgc
            gc_penalty = (expected_gc / 2) * SCORING["goals_conceded_per_2"]
            xp += gc_penalty * 0.5  # Temper the impact

        # ── Bonus points ──
        bonus_rate = int(p.get("bonus", 0)) / max(starts, 1)
        xp += bonus_rate * fdr_mod * 0.8

        # ── Saves (GKP) ──
        if pos == 1:
            saves_pg = int(p.get("saves", 0)) / max(starts, 1)
            # More saves expected vs stronger opponents
            saves_adjusted = saves_pg * min(conceding_context, 1.5)
            xp += (saves_adjusted / 3) * SCORING["saves_per_3"]

        # ── Penalty save ──
        if pos == 1 and int(p.get("penalties_saved", 0)) > 0:
            xp += 0.1 * SCORING["penalty_save"]

        # ── Card risk ──
        yellows = int(p.get("yellow_cards", 0))
        reds = int(p.get("red_cards", 0))
        card_risk = (yellows * -1 + reds * -3) / max(starts, 1)
        xp += card_risk * 0.5

        return max(xp, 0.2)

    # ── Availability ──────────────────────────────────────────

    def _get_availability(self, p: dict) -> dict:
        status = p.get("status", "a")
        chance = p.get("chance_of_playing_next_round")
        news = p.get("news", "")

        if status == "u":
            return {"status": "unavailable", "chance": 0, "news": news}
        elif status == "i":
            return {"status": "unavailable", "chance": 0, "news": news or "Injured"}
        elif status == "s":
            return {"status": "unavailable", "chance": 0, "news": news or "Suspended"}
        elif status == "n":
            return {"status": "unavailable", "chance": 0, "news": news or "Not available"}
        elif status == "d":
            return {
                "status": "doubtful",
                "chance": chance if chance is not None else 50,
                "news": news or "Doubtful",
            }
        else:
            return {"status": "available", "chance": 100, "news": news}

    # ── Confidence ────────────────────────────────────────────

    def _calc_confidence(self, p: dict, fixtures: list, starter_quality: dict) -> float:
        score = 0.5
        tier = starter_quality["tier"]
        if tier == "nailed":
            score += 0.25
        elif tier == "regular":
            score += 0.15
        elif tier == "rotation":
            score += 0.0
        elif tier == "fringe":
            score -= 0.15
        else:
            score -= 0.30

        starts = int(p.get("starts", 0))
        if starts > 20:
            score += 0.1
        elif starts > 10:
            score += 0.05
        elif starts < 3:
            score -= 0.15

        status = p.get("status", "a")
        if status == "a":
            score += 0.05
        elif status == "d":
            score -= 0.2

        if len(fixtures) >= 2:
            score -= 0.05

        # NEW: Higher confidence if team form supports prediction
        team_id = p.get("team", 0)
        momentum = calc_team_momentum(self.team_stats, team_id)
        if abs(momentum) > 0.3:
            score += 0.05  # Strong form signal = more confident either way

        return max(0.1, min(score, 0.99))

    # ── Helper ────────────────────────────────────────────────

    def _empty_prediction(self, p: dict, availability: dict) -> dict:
        return {
            "player_id": p.get("id", 0),
            "name": p.get("web_name", "Unknown"),
            "full_name": f"{p.get('first_name', '')} {p.get('second_name', '')}".strip(),
            "team": p.get("team_short", "???"),
            "team_name": p.get("team_name", "Unknown"),
            "team_id": p.get("team", 0),
            "position": p.get("position_name", "???"),
            "position_id": p.get("position_id", 0),
            "price": p.get("now_cost", 0) / 10,
            "selected_by_percent": p.get("selected_by_percent", "0"),
            "predicted_points": 0.0, "raw_xpts": 0.0,
            "fixtures": [], "fixture": {},
            "num_fixtures": 0, "is_dgw": False,
            "availability": availability,
            "starter_quality": {"tier": "unavailable", "multiplier": 0},
            "factors": {}, "confidence": 0.0, "base_xp": 0.0,
            "minutes": p.get("minutes", 0),
            "starts": p.get("starts", 0),
            "form": float(p.get("form", 0)),
            "ppg": float(p.get("points_per_game", 0)),
            "total_points": p.get("total_points", 0),
            "ict_index": float(p.get("ict_index", 0)),
            "news": p.get("news", ""),
            "status_code": p.get("status", "a"),
            "team_last5_form": "",
            "team_last5_wr": 0,
            "team_season_wr": 0,
            "team_momentum": 0,
        }
