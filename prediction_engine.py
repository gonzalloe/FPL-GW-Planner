"""
FPL Predictor - Prediction Engine v4 (Best-in-Class)

Methodology synthesised from:
  - FPL Review: Probability-weighted EV, xMins as simulation average
  - FPL Vault: Component-based xPts formula (xG, xA, CS, bonus, saves, cards)
  - FPL Optimized: Poisson goal distribution → multi-goal EV
  - XGBoost models (Caidsy, Meharpal Basi): Rolling windows, xG delta, minutes volatility
  - SmartDraftBoard: Poisson CS probability, position-aware FDR
  - FPL Lens: Monte Carlo match simulation approach
  - OpenFPL (arXiv): Feature importance → form > fixture difficulty

Key improvements over v3:
  1. Poisson distribution for goal/assist scoring → proper multi-goal EV
  2. Probabilistic xMins (simulation-style, not binary)
  3. Multi-window rolling form (3/5/8 GW equivalent via weighted decay)
  4. xG delta regression detection (overperformers regress to mean)
  5. DGW-specific starter tiers (rotation risk for 2nd match)
  6. Position-aware fixture difficulty
  7. Proper bonus point model (BPS persistence + position + fixture)
  8. Negative event deductions (cards, own goals, penalty misses)
  9. Minutes volatility as risk signal
  10. Defensive contribution points (clearances/blocks/interceptions)
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


# ══════════════════════════════════════════════════════════════
#  Poisson helpers
# ══════════════════════════════════════════════════════════════

def poisson_pmf(k: int, lam: float) -> float:
    """P(X = k) for Poisson(λ).  Safe for λ=0."""
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def poisson_ev_goals(lam: float, pts_per_goal: int, max_k: int = 6) -> float:
    """
    Expected FPL points from goals using Poisson(λ).
    Sums P(k goals) × k × pts_per_goal for k = 0..max_k.
    This is mathematically equivalent to λ × pts_per_goal for Poisson,
    but we compute explicitly for transparency and to cap at max_k.
    """
    ev = 0.0
    for k in range(max_k + 1):
        ev += poisson_pmf(k, lam) * k * pts_per_goal
    return ev


def poisson_ev_assists(lam: float, max_k: int = 5) -> float:
    """Expected FPL points from assists using Poisson(λ)."""
    ev = 0.0
    for k in range(max_k + 1):
        ev += poisson_pmf(k, lam) * k * SCORING["assist"]
    return ev


def poisson_cs_probability(team_xgc: float) -> float:
    """P(clean sheet) = P(opponent scores 0) = e^(-λ) where λ = team's xGC."""
    if team_xgc <= 0:
        return 0.95  # Near-certain CS
    return math.exp(-team_xgc)


def poisson_goals_conceded_ev(team_xgc: float, max_k: int = 8) -> float:
    """Expected goals conceded deduction for DEF/GKP: -1 per 2 goals conceded."""
    ev = 0.0
    for k in range(max_k + 1):
        deduction = (k // 2) * SCORING["goals_conceded_per_2"]
        ev += poisson_pmf(k, team_xgc) * deduction
    return ev  # This will be negative


# ══════════════════════════════════════════════════════════════
#  Main Engine
# ══════════════════════════════════════════════════════════════

class PredictionEngine:
    """
    Probabilistic prediction model for FPL player points.
    v4: Poisson-based, multi-window form, xG delta regression,
        DGW-aware starter tiers, position-aware FDR.
    """

    def __init__(self):
        self.bootstrap = fetch_bootstrap()
        self.fixtures = fetch_fixtures()
        self.players = build_player_map(self.bootstrap)
        self.teams = build_team_map(self.bootstrap)
        self.current_gw = get_current_gameweek(self.bootstrap)
        self.next_gw = get_next_gameweek(self.bootstrap)
        self.dgw_teams = {}
        self.bgw_teams = set()
        self.team_stats = build_team_stats(self.fixtures, self.teams)

    # ──────────────────────────────────────────────────────────
    #  Public API
    # ──────────────────────────────────────────────────────────

    def predict_player(self, player_id: int, target_gw: int | None = None) -> dict:
        """Predict xPts for a player.  DGW-aware: sums per-fixture EV."""
        if target_gw is None:
            target_gw = self.next_gw

        p = self.players.get(player_id)
        if not p:
            return {"player_id": player_id, "error": "Player not found"}

        availability = self._get_availability(p)
        if availability["status"] == "unavailable":
            return self._empty_prediction(p, availability)

        all_fixtures = get_player_fixtures(p["team"], target_gw, self.fixtures)
        if not all_fixtures:
            return self._empty_prediction(p, {"status": "blank_gw"})

        num_fixtures = len(all_fixtures)
        is_dgw = num_fixtures >= 2

        # ── Teammate injury boost ──
        team_id = p.get("team", 0)
        pos_id = p.get("position_id", 0)
        injury_ctx = getattr(self, '_team_injury_context', {}).get((team_id, pos_id), {})
        teammates_out = injury_ctx.get("out", 0)
        out_minutes = injury_ctx.get("out_minutes", 0)

        # ── Starter quality (DGW-aware, injury-aware) ──
        starter = self._assess_starter_quality(p, num_fixtures, teammates_out, out_minutes)

        # ── Per-fixture xPts ──
        total_raw = 0.0
        total_adj = 0.0
        fixture_details = []
        all_factors = {}

        for fix_idx, fix_info in enumerate(all_fixtures):
            fix_xg_data = get_fixture_xg(
                p["team"], fix_info["opponent_id"],
                fix_info["is_home"], self.team_stats
            )

            # xMins for THIS fixture (drops for 2nd match in DGW)
            xmins = self._calc_xmins(p, starter, fix_idx, num_fixtures)

            # Compute EV for this fixture
            fix_ev = self._fixture_ev(p, fix_info, fix_xg_data, xmins, starter)

            # Contextual factor modifiers
            factors = self._calc_all_factors(p, fix_info, fix_xg_data)
            weighted_mod = sum(factors.get(k, 0) * PREDICTION_WEIGHTS.get(k, 0)
                               for k in PREDICTION_WEIGHTS)
            # Modifiers are bounded to avoid runaway inflation
            weighted_mod = max(-0.35, min(weighted_mod, 0.45))
            fix_xp = fix_ev * (1.0 + weighted_mod)
            fix_xp = max(0.0, fix_xp)

            total_raw += fix_xp

            # Availability discount
            adj_xp = self._apply_availability_discount(fix_xp, availability)
            total_adj += adj_xp

            opp_team = self.teams.get(fix_info["opponent_id"], {})
            team_summary = get_team_analysis_summary(
                p["team"], fix_info["opponent_id"],
                fix_info["is_home"], self.team_stats, self.teams
            )

            fixture_details.append({
                "opponent": opp_team.get("short_name", "???"),
                "opponent_full": opp_team.get("name", "Unknown"),
                "is_home": fix_info["is_home"],
                "fdr": fix_info["fdr"],
                "venue": "H" if fix_info["is_home"] else "A",
                "xp_single": round(fix_xp, 2),
                "xp_adjusted": round(adj_xp, 2),
                "xmins": round(xmins, 1),
                "fixture_xg": fix_xg_data["team_xg"],
                "fixture_xgc": fix_xg_data["team_xgc"],
                "cs_probability": fix_xg_data["cs_probability"],
                "win_probability": fix_xg_data.get("win_probability", 0),
                "h2h": fix_xg_data["h2h"],
                "team_form": team_summary["last5_form"],
                "team_last5_wr": team_summary["last5_win_rate"],
                "opp_form": team_summary["opp_last5_form"],
                "opp_last5_wr": team_summary["opp_last5_win_rate"],
                "momentum": team_summary["momentum"],
            })

            for k, v in factors.items():
                all_factors[k] = all_factors.get(k, 0) + v / num_fixtures

        # Reasonable ceiling
        max_pts = 22.0 * num_fixtures
        total_raw = min(total_raw, max_pts)
        total_adj = min(total_adj, max_pts)

        confidence = self._calc_confidence(p, all_fixtures, starter, availability, teammates_out)

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
            "predicted_points": round(total_adj, 2),
            "raw_xpts": round(total_raw, 2),
            "fixtures": fixture_details,
            "fixture": fixture_details[0] if fixture_details else {},
            "num_fixtures": num_fixtures,
            "is_dgw": is_dgw,
            "availability": availability,
            "starter_quality": starter,
            "factors": {k: round(v, 4) for k, v in all_factors.items()},
            "confidence": round(confidence, 2),
            "base_xp": round(total_raw, 2),
            # Player stats
            "minutes": p.get("minutes", 0),
            "starts": p.get("starts", 0),
            "form": float(p.get("form", 0)),
            "ppg": float(p.get("points_per_game", 0)),
            "total_points": p.get("total_points", 0),
            "ict_index": float(p.get("ict_index", 0)),
            "news": p.get("news", ""),
            "status_code": p.get("status", "a"),
            "team_last5_form": ts.get("last5_form_str", ""),
            "team_last5_wr": round(ts.get("last5_win_rate", 0), 3),
            "team_season_wr": round(ts.get("win_rate", 0), 3),
            "team_momentum": round(calc_team_momentum(self.team_stats, team_id), 3),
            "team_injury_penalty": getattr(self, '_team_injury_penalty', {}).get(team_id, 1.0),
        }

    def predict_all(self, target_gw: int | None = None,
                    min_chance: int = 0) -> list[dict]:
        """Predict points for ALL FPL players including injured/suspended."""
        if target_gw is None:
            target_gw = self.next_gw

        self.dgw_teams = get_dgw_teams(target_gw, self.fixtures)
        self.bgw_teams = get_bgw_teams(target_gw, self.fixtures, self.bootstrap)

        # ── Fetch external news overrides ──
        # Cross-reference BBC/Sky/PremierInjuries with FPL data to catch
        # injuries/returns that FPL hasn't updated yet
        self._news_overrides = {}
        try:
            from news_aggregator import NewsAggregator
            aggregator = NewsAggregator()
            self._news_overrides = aggregator.get_injury_overrides(self.players)
            if self._news_overrides:
                # Apply overrides to player data so injury context picks them up
                for pid, override in self._news_overrides.items():
                    if pid in self.players:
                        self.players[pid]["_news_override"] = override
                        # Override status if external source says they're out
                        if override["status"] in ("i", "u", "s"):
                            self.players[pid]["status"] = override["status"]
                            self.players[pid]["chance_of_playing_next_round"] = override["chance"]
                            if override.get("news"):
                                self.players[pid]["news"] = override["news"]
        except Exception:
            pass

        # ── Build team injury context ──
        # Count unavailable players per team+position to boost replacements
        self._team_injury_context = {}  # {(team_id, pos_id): {"out": count, "out_names": [...]}}
        for pid, p in self.players.items():
            status = p.get("status", "a")
            chance = p.get("chance_of_playing_next_round")
            is_out = status in ("i", "u", "s", "n") or (chance is not None and chance == 0)
            is_doubtful_low = status == "d" and chance is not None and chance <= 25
            if is_out or is_doubtful_low:
                team_id = p.get("team", 0)
                pos_id = p.get("position_id", 0)
                key = (team_id, pos_id)
                if key not in self._team_injury_context:
                    self._team_injury_context[key] = {"out": 0, "out_names": [], "out_minutes": 0}
                self._team_injury_context[key]["out"] += 1
                self._team_injury_context[key]["out_names"].append(p.get("web_name", "?"))
                self._team_injury_context[key]["out_minutes"] += int(p.get("minutes", 0))

        # ── Build team-level injury penalty ──
        # Teams with many injured starters should have dampened form/xG/strength
        # Penalty based on total missing minutes as fraction of team's total
        self._team_injury_penalty = {}  # {team_id: penalty_multiplier 0.70-1.00}
        team_total_mins = {}  # total minutes for all players per team
        team_out_mins = {}    # total minutes for OUT players per team
        team_out_count = {}   # count of OUT players per team

        for pid, p in self.players.items():
            tid = p.get("team", 0)
            mins = int(p.get("minutes", 0))
            team_total_mins[tid] = team_total_mins.get(tid, 0) + mins

        for (tid, pos_id), ctx in self._team_injury_context.items():
            team_out_mins[tid] = team_out_mins.get(tid, 0) + ctx["out_minutes"]
            team_out_count[tid] = team_out_count.get(tid, 0) + ctx["out"]

        for tid in team_total_mins:
            total = team_total_mins.get(tid, 1)
            out = team_out_mins.get(tid, 0)
            n_out = team_out_count.get(tid, 0)
            if total > 0 and out > 0:
                # Fraction of team's minutes that are injured
                injury_fraction = out / total
                # Penalty: lose 0.30 at most (if half the team's minutes are out)
                penalty = max(0.70, 1.0 - injury_fraction * 0.60)
                self._team_injury_penalty[tid] = round(penalty, 3)
            else:
                self._team_injury_penalty[tid] = 1.0

        results = []
        for pid, p in self.players.items():
            chance = p.get("chance_of_playing_next_round")
            # Only skip players explicitly marked as very unlikely (0% or <25%)
            if chance is not None and chance < min_chance:
                continue

            pred = self.predict_player(pid, target_gw)
            # Include ALL players — even 0 xPts (youngsters, bench warmers)
            if not pred.get("error"):
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
                tid: {
                    "name": self.teams.get(tid, {}).get("name", "???"),
                    "short_name": self.teams.get(tid, {}).get("short_name", "???"),
                }
                for tid in bgw
            },
        }

    # ══════════════════════════════════════════════════════════
    #  Core: Per-Fixture Expected Value (Probabilistic)
    # ══════════════════════════════════════════════════════════

    def _fixture_ev(self, p: dict, fix_info: dict,
                    fix_xg_data: dict, xmins: float,
                    starter: dict) -> float:
        """
        Calculate EV for ONE fixture using Poisson distributions.

        Components:
          1. Appearance points (from xMins)
          2. Goal EV (Poisson on effective xG)
          3. Assist EV (Poisson on effective xA)
          4. Clean sheet EV (Poisson on team xGC)
          5. Goals conceded penalty (Poisson, DEF/GKP)
          6. Bonus points (persistence model)
          7. Saves EV (GKP)
          8. Negative events (cards, OG, pen miss)
          9. Defensive contributions (new FPL 25/26)
        """
        pos = p.get("position_id", 3)
        starts = max(int(p.get("starts", 0)), 1)
        mins_played = int(p.get("minutes", 0))

        # ── xMins → playing probability & minutes fraction ──
        # xMins is a probability-weighted average (like FPL Review)
        # We derive P(plays) and expected fraction of 90 from it
        p_plays = min(xmins / 90.0, 1.0)  # Crude but effective
        p_plays_60 = max(0, (xmins - 30) / 60.0)  # P(plays >= 60 mins)
        p_plays_60 = min(p_plays_60, 1.0)
        mins_fraction = xmins / 90.0

        ev = 0.0

        # ── 1. Appearance points ──
        # 2 pts if plays 60+, 1 pt if plays 1-59
        ev += p_plays_60 * 2.0 + (p_plays - p_plays_60) * 1.0

        # ── 2. Goals (Poisson) ──
        # Player's per-90 xG, scaled by fixture context
        xg_season = float(p.get("expected_goals", 0))
        xg_per90 = xg_season / max(mins_played / 90.0, 1.0) if mins_played > 0 else 0.0

        # Position-aware fixture difficulty adjustment
        fdr = fix_info["fdr"]
        fdr_mod = self._position_fdr_modifier(pos, fdr, fix_info["is_home"])

        # Team scoring context from opponent matchup
        team_xg = fix_xg_data.get("team_xg", 1.35)
        scoring_context = team_xg / 1.35  # >1 = team expected to score more than avg

        # Opponent injury penalty → easier to score against weakened opponents
        opp_id = fix_info.get("opponent_id", 0)
        opp_injury_pen = getattr(self, '_team_injury_penalty', {}).get(opp_id, 1.0)
        if opp_injury_pen < 1.0:
            # Opponent is weakened → boost our scoring context, reduce their xG
            opp_weakness = 1.0 + (1.0 - opp_injury_pen) * 0.5  # Up to 15% boost
            scoring_context *= opp_weakness

        # xG delta regression: if player massively overperforming xG, regress
        actual_goals = int(p.get("goals_scored", 0))
        xg_delta = self._calc_xg_delta_regression(actual_goals, xg_season, starts)

        # Effective xG for this fixture
        effective_xg = xg_per90 * mins_fraction * fdr_mod * scoring_context * xg_delta
        effective_xg = max(0.0, effective_xg)

        goal_pts = SCORING["goals"].get(pos, 4)
        ev += poisson_ev_goals(effective_xg, goal_pts)

        # ── 3. Assists (Poisson) ──
        xa_season = float(p.get("expected_assists", 0))
        xa_per90 = xa_season / max(mins_played / 90.0, 1.0) if mins_played > 0 else 0.0

        # Assists also benefit from higher team xG (more goals = more assists)
        effective_xa = xa_per90 * mins_fraction * fdr_mod * scoring_context
        effective_xa = max(0.0, effective_xa)

        ev += poisson_ev_assists(effective_xa)

        # ── 4. Clean sheet (Poisson) ──
        team_xgc = fix_xg_data.get("team_xgc", 1.35)
        # If opponent is weakened by injuries, they score fewer goals → lower xGC for us
        if opp_injury_pen < 1.0:
            team_xgc *= opp_injury_pen  # Reduce expected goals conceded
        cs_prob = poisson_cs_probability(team_xgc)

        # Blend Poisson CS with FDR-derived CS for robustness
        fdr_cs_prob = self._fdr_cs_probability(fdr, fix_info["is_home"])
        # Blended: 60% Poisson (data-driven), 40% FDR (structural)
        blended_cs = 0.60 * cs_prob + 0.40 * fdr_cs_prob

        # Recent defensive form adjustment
        team_id = p.get("team", 0)
        ts = self.team_stats.get(team_id, {})
        recent_cs_rate = ts.get("last5_cs", 0) / max(min(len(ts.get("results", [])), 5), 1) if ts else 0
        # Blend in recent form: 70% model, 30% recent CS rate
        blended_cs = 0.70 * blended_cs + 0.30 * recent_cs_rate

        cs_pts = SCORING["clean_sheet"].get(pos, 0)
        if cs_pts > 0:
            # Only count CS if player plays 60+ mins (FPL rule)
            ev += blended_cs * cs_pts * p_plays_60

        # ── 5. Goals conceded penalty (DEF/GKP) ──
        if pos in (1, 2):
            gc_ev = poisson_goals_conceded_ev(team_xgc)
            ev += gc_ev * p_plays_60 * 0.5  # Dampened: CS already captures defensive value

        # ── 6. Bonus points (persistence + position + fixture) ──
        ev += self._predict_bonus(p, effective_xg, effective_xa, blended_cs,
                                   fdr_mod, mins_fraction)

        # ── 7. Saves (GKP) ──
        if pos == 1:
            saves_season = int(p.get("saves", 0))
            saves_per90 = saves_season / max(mins_played / 90.0, 1.0) if mins_played > 0 else 3.0
            # More saves expected vs stronger opponents (higher xGC = more shots)
            conceding_context = min(team_xgc / 1.35, 1.6)
            expected_saves = saves_per90 * mins_fraction * conceding_context
            ev += (expected_saves / 3.0) * SCORING["saves_per_3"]

            # Penalty save (small probability based on history)
            pen_saved = int(p.get("penalties_saved", 0))
            if pen_saved > 0:
                pen_save_rate = pen_saved / max(starts, 1)
                ev += pen_save_rate * SCORING["penalty_save"] * 0.3

        # ── 8. Negative events ──
        yellows = int(p.get("yellow_cards", 0))
        reds = int(p.get("red_cards", 0))
        own_goals = int(p.get("own_goals", 0))
        pen_missed = int(p.get("penalties_missed", 0))

        # Per-90 rates scaled by expected minutes
        yc_rate = yellows / max(mins_played / 90.0, 1.0) if mins_played > 0 else 0.1
        rc_rate = reds / max(mins_played / 90.0, 1.0) if mins_played > 0 else 0.005
        og_rate = own_goals / max(mins_played / 90.0, 1.0) if mins_played > 0 else 0.01
        pm_rate = pen_missed / max(mins_played / 90.0, 1.0) if mins_played > 0 else 0.0

        ev += yc_rate * mins_fraction * SCORING["yellow_card"]
        ev += rc_rate * mins_fraction * SCORING["red_card"]
        ev += og_rate * mins_fraction * SCORING["own_goal"]
        ev += pm_rate * mins_fraction * SCORING["penalty_miss"]

        # ── 9. Defensive contributions (FPL 25/26 new rule) ──
        # 1 pt per 3 clearances+blocks+interceptions for DEF/GKP
        # NOTE: We keep this conservative — FPL API doesn't provide CBI data yet
        if pos == 2:
            base_dc_rate = 8.0
            dc_fixture_mod = 1.0 + (fdr - 3) * 0.06
            expected_dc = base_dc_rate * mins_fraction * dc_fixture_mod
            ev += (expected_dc / 3.0) * 1.0 * 0.35  # Dampened: uncertain data

        return max(ev, 0.0)

    # ══════════════════════════════════════════════════════════
    #  xMins (Expected Minutes)
    # ══════════════════════════════════════════════════════════

    def _calc_xmins(self, p: dict, starter: dict, fix_idx: int,
                    num_fixtures: int) -> float:
        """
        Calculate expected minutes for a specific fixture.

        Inspired by FPL Review's xMins: a probability-weighted average
        across scenarios (starts, cameos, benched).

        For DGW: second fixture has rotation risk factored in.
        """
        tier = starter["tier"]
        avg_mins = starter["avg_mins"]
        start_rate = starter["start_rate"]

        # Base xMins from historical pattern
        if tier == "nailed":
            base_xmins = min(avg_mins * 1.0, 90.0)
        elif tier == "regular":
            # Blend: P(start)×85 + P(sub)×20 + P(bench)×0
            p_start = start_rate
            p_sub = min(0.20, 1.0 - start_rate)
            base_xmins = p_start * 85.0 + p_sub * 20.0
        elif tier == "rotation":
            p_start = start_rate
            p_sub = min(0.25, 1.0 - start_rate)
            base_xmins = p_start * 80.0 + p_sub * 18.0
        elif tier == "fringe":
            p_start = start_rate
            p_sub = min(0.30, 1.0 - start_rate)
            base_xmins = p_start * 75.0 + p_sub * 15.0
        else:
            base_xmins = max(avg_mins * 0.5, 1.0)

        # ── DGW rotation discount for 2nd match ──
        if num_fixtures >= 2 and fix_idx >= 1:
            if tier == "nailed":
                # Nailed players almost always start both
                base_xmins *= 0.92  # Slight rest risk
            elif tier == "regular":
                # Regular starters: meaningful rotation risk in 2nd match
                base_xmins *= 0.75
            elif tier == "rotation":
                # Rotation players: high chance of being rested for 2nd
                base_xmins *= 0.50
            else:
                # Fringe/bench: very unlikely to feature in 2nd
                base_xmins *= 0.25

        # Cap and floor
        return max(0.0, min(base_xmins, 90.0))

    # ══════════════════════════════════════════════════════════
    #  Starter Quality (DGW-aware)
    # ══════════════════════════════════════════════════════════

    def _assess_starter_quality(self, p: dict, num_fixtures: int = 1,
                                teammates_out: int = 0, out_minutes: int = 0) -> dict:
        """
        Assess how nailed a player is, with DGW-specific and injury-aware adjustments.

        Tiers:
          nailed   : Start rate ≥75%, avg mins ≥65 → plays both DGW matches
          regular  : Start rate ≥50%, avg mins ≥45 → likely starts both, maybe rested 1
          rotation : Start rate ≥30%, avg mins ≥20 → starts 1, cameo/bench 1
          fringe   : avg mins ≥8 → occasional cameo
          bench_warmer: rarely plays

        Injury boost: if teammates in same position are out, lower-tier players
        get promoted (e.g., fringe → rotation, rotation → regular).
        """
        total_minutes = int(p.get("minutes", 0))
        starts = int(p.get("starts", 0))
        gws_played = max(self.current_gw - 1, 1)
        max_possible = gws_played * 90
        avg_mins = total_minutes / gws_played
        start_rate = starts / gws_played if gws_played > 0 else 0
        minutes_pct = total_minutes / max_possible if max_possible > 0 else 0

        # Minutes volatility (from XGBoost model research)
        # High volatility = unreliable, even if per-appearance stats look good
        mins_volatility = self._calc_minutes_volatility(p)

        # Determine tier
        if start_rate >= 0.75 and avg_mins >= 65:
            tier = "nailed"
            multiplier = 1.0
        elif start_rate >= 0.50 and avg_mins >= 45:
            tier = "regular"
            multiplier = 0.92
        elif start_rate >= 0.30 and avg_mins >= 20:
            tier = "rotation"
            multiplier = 0.70
        elif avg_mins >= 8:
            tier = "fringe"
            multiplier = 0.40
        else:
            tier = "bench_warmer"
            multiplier = 0.10

        # Volatility penalty (from XGBoost research: minutes volatility is key risk signal)
        if mins_volatility > 0.6 and tier in ("regular", "rotation"):
            multiplier *= 0.90  # Volatile minutes = less reliable

        # ── Teammate injury boost ──
        # If teammates in the same position are injured/out, this player is more
        # likely to start. Promote their tier and boost xMins accordingly.
        injury_boost = False
        if teammates_out >= 1:
            # Significant boost: out_minutes means the injured player was a starter
            injured_was_starter = out_minutes > gws_played * 30  # avg >30 mins/gw
            if tier == "bench_warmer" and (teammates_out >= 2 or injured_was_starter):
                tier = "fringe"
                multiplier = max(multiplier, 0.50)
                injury_boost = True
            elif tier == "fringe" and injured_was_starter:
                tier = "rotation"
                multiplier = max(multiplier, 0.75)
                injury_boost = True
            elif tier == "rotation" and injured_was_starter:
                tier = "regular"
                multiplier = max(multiplier, 0.92)
                injury_boost = True
            elif tier == "regular" and teammates_out >= 2:
                tier = "nailed"
                multiplier = max(multiplier, 1.0)
                injury_boost = True

        # ── DGW-specific: probability of starting both matches ──
        if num_fixtures >= 2:
            if tier == "nailed":
                dgw_both_prob = 0.88  # Even nailed players occasionally rest 1
                dgw_effective = 1.92
            elif tier == "regular":
                dgw_both_prob = 0.60
                dgw_effective = 1.55
            elif tier == "rotation":
                dgw_both_prob = 0.25
                dgw_effective = 1.10
            elif tier == "fringe":
                dgw_both_prob = 0.08
                dgw_effective = 0.55
            else:
                dgw_both_prob = 0.02
                dgw_effective = 0.15
        else:
            dgw_both_prob = None
            dgw_effective = 1.0 if tier != "bench_warmer" else 0.2

        return {
            "tier": tier,
            "multiplier": multiplier,
            "avg_mins": round(avg_mins, 1),
            "start_rate": round(start_rate, 2),
            "minutes_pct": round(minutes_pct, 2),
            "starts": starts,
            "total_minutes": total_minutes,
            "mins_volatility": round(mins_volatility, 2),
            "dgw_both_start_prob": round(dgw_both_prob, 2) if dgw_both_prob is not None else None,
            "dgw_effective_matches": round(dgw_effective, 2),
            "injury_boost": injury_boost,
            "teammates_out": teammates_out,
        }

    def _calc_minutes_volatility(self, p: dict) -> float:
        """
        Minutes volatility score (0-1). High = unreliable playing time.
        Based on XGBoost model research: inconsistent minutes is a key risk signal.

        We approximate from aggregate stats since we don't have per-GW data here.
        """
        total_minutes = int(p.get("minutes", 0))
        starts = int(p.get("starts", 0))
        gws_played = max(self.current_gw - 1, 1)

        if gws_played < 3:
            return 0.5  # Not enough data

        avg_mins = total_minutes / gws_played
        appearances = starts + max(0, gws_played - starts)  # Rough sub count

        # If player starts a lot but avg_mins is low → gets subbed off early → moderate
        if starts > 0 and avg_mins > 0:
            mins_per_start = total_minutes / starts
            if mins_per_start < 70 and starts > 5:
                return 0.4  # Gets subbed regularly
        else:
            return 0.8

        # If start rate is far from 100% or 0% → rotation → high volatility
        start_rate = starts / gws_played
        if 0.35 < start_rate < 0.65:
            return 0.7  # True rotation
        elif 0.65 <= start_rate < 0.80:
            return 0.35
        elif start_rate >= 0.80:
            return 0.15  # Very consistent
        else:
            return 0.6  # Mostly bench, sometimes plays

    # ══════════════════════════════════════════════════════════
    #  xG Delta Regression
    # ══════════════════════════════════════════════════════════

    def _calc_xg_delta_regression(self, actual_goals: int, xg: float,
                                   starts: int) -> float:
        """
        Detect overperformance vs xG and regress toward the mean.

        From XGBoost research (Meharpal Basi): players massively overperforming
        xG tend to regress. We apply a dampening factor.

        Returns a multiplier (0.7 - 1.1) applied to projected xG.
        """
        if starts < 5 or xg < 0.5:
            return 1.0  # Not enough data for regression

        xg_per_start = xg / starts
        goals_per_start = actual_goals / starts

        if xg_per_start > 0:
            ratio = goals_per_start / xg_per_start
        else:
            return 1.0

        # Overperforming: ratio > 1.3 → expect regression
        if ratio > 1.8:
            return 0.78  # Heavy regression expected
        elif ratio > 1.4:
            return 0.85  # Moderate regression
        elif ratio > 1.2:
            return 0.92  # Slight regression

        # Underperforming: ratio < 0.7 → expect bounce-back
        elif ratio < 0.5:
            return 1.10  # Strong bounce-back expected
        elif ratio < 0.7:
            return 1.05  # Moderate bounce-back

        return 1.0  # Performing in line with xG

    # ══════════════════════════════════════════════════════════
    #  Position-Aware Fixture Difficulty
    # ══════════════════════════════════════════════════════════

    def _position_fdr_modifier(self, pos: int, fdr: int, is_home: bool) -> float:
        """
        Position-aware FDR modifier (from SmartDraftBoard approach).

        A tough fixture for a defender (facing high-xG attack) is not
        equally tough for an attacker (who can still score against any team).
        """
        base_mod = FDR_MULTIPLIER.get(fdr, 1.0)
        home_mod = HOME_BONUS if is_home else AWAY_PENALTY

        if pos in (3, 4):  # MID/FWD: fixture difficulty affects them LESS
            # Attackers transcend fixture difficulty more often
            # (from OpenFPL research: form > fixture for attackers)
            dampened = 1.0 + (base_mod - 1.0) * 0.65
            return dampened * home_mod
        elif pos == 2:  # DEF: fixture difficulty affects them MORE
            amplified = 1.0 + (base_mod - 1.0) * 1.20
            return amplified * home_mod
        elif pos == 1:  # GKP: similar to DEF
            amplified = 1.0 + (base_mod - 1.0) * 1.10
            return amplified * home_mod
        return base_mod * home_mod

    def _fdr_cs_probability(self, fdr: int, is_home: bool) -> float:
        """
        FDR-derived clean sheet probability (from FPL Vault formula).
        cs_prob = (5 - fdr) / 4 × home_factor
        """
        base = max(0, (5 - fdr)) / 4.0
        if is_home:
            return min(base * 1.15, 0.60)  # Home teams keep CS more often
        else:
            return min(base * 0.85, 0.45)

    # ══════════════════════════════════════════════════════════
    #  Bonus Points Model
    # ══════════════════════════════════════════════════════════

    def _predict_bonus(self, p: dict, eff_xg: float, eff_xa: float,
                       cs_prob: float, fdr_mod: float,
                       mins_fraction: float) -> float:
        """
        Predict expected bonus points using a persistence + situational model.

        Components:
          1. Historical bonus rate (persistence)
          2. Projected goal involvement (goals/assists boost BPS)
          3. Clean sheet bonus (defenders get BPS for CS)
          4. Position-specific base rate
        """
        pos = p.get("position_id", 3)
        starts = max(int(p.get("starts", 0)), 1)
        bonus_season = int(p.get("bonus", 0))

        # Historical rate (persistence — from FPL Vault)
        historical_rate = bonus_season / starts  # bonus per start

        # Goal involvement boost (goals and assists heavily influence BPS)
        gi_boost = (eff_xg * 12.0 + eff_xa * 9.0) / 30.0  # BPS weights for goals/assists

        # CS boost for defenders
        cs_boost = 0.0
        if pos in (1, 2):
            cs_boost = cs_prob * 0.5  # CS gives significant BPS

        # Position base rates (from observed averages)
        pos_base = {1: 0.25, 2: 0.30, 3: 0.35, 4: 0.28}
        base = pos_base.get(pos, 0.30)

        # Blend: 50% historical persistence, 30% projected, 20% base
        predicted_bonus = (
            0.50 * historical_rate +
            0.30 * (gi_boost + cs_boost) +
            0.20 * base
        )

        return predicted_bonus * mins_fraction * fdr_mod * 0.85

    # ══════════════════════════════════════════════════════════
    #  Context Factor Calculations
    # ══════════════════════════════════════════════════════════

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
            "team_form": self._calc_team_form_factor(p),
            "h2h_factor": self._calc_h2h_factor(p, fixture_info, fix_xg_data),
            "win_probability": self._calc_win_prob_factor(fix_xg_data),
        }

    def _calc_form(self, p: dict) -> float:
        """
        Multi-window form calculation (inspired by XGBoost models).
        FPL's "form" is last-5-GW average. We blend with PPG for stability.
        Research shows form > fixture difficulty for prediction accuracy.
        """
        form = float(p.get("form", 0))
        ppg = float(p.get("points_per_game", 0))
        # Short-term (form = last 5) gets higher weight than season avg
        # This aligns with XGBoost research: short-window features dominate
        form_score = (form - 3.5) / 5.0
        ppg_score = (ppg - 3.5) / 5.0
        return 0.65 * form_score + 0.35 * ppg_score

    def _calc_fixture_factor(self, p: dict, fdr: int, is_home: bool) -> float:
        pos = p.get("position_id", 3)
        mod = self._position_fdr_modifier(pos, fdr, is_home)
        return (mod - 1.0) * 0.8  # Already includes position awareness

    def _calc_win_prob_factor(self, fix_xg_data: dict) -> float:
        """
        Win probability as a prediction factor.
        Normalized to roughly the same scale as fixture_difficulty (~[-0.4, 0.4]).
        Baseline: 0.35 (league average win prob).
        Returns positive value when team is favored, negative when underdog.
        """
        win_prob = fix_xg_data.get("win_probability", 0.35)
        # Scale: (win_prob - 0.35) / 0.35 gives [-1.0, 1.7]
        # Compress to [-0.4, 0.4] range similar to other factors
        return max(-0.4, min((win_prob - 0.35) * 1.1, 0.4))

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
            return 0.15
        elif ratio > 0.65:
            return 0.05
        elif ratio > 0.40:
            return -0.08
        else:
            return -0.25

    def _calc_team_strength(self, p: dict, is_home: bool) -> float:
        if is_home:
            atk = p.get("team_strength_attack_home", 1200)
            defn = p.get("team_strength_defence_home", 1200)
        else:
            atk = p.get("team_strength_attack_away", 1200)
            defn = p.get("team_strength_defence_away", 1200)
        pos = p.get("position_id", 3)
        # Apply injury penalty — injured teams are weaker than ratings suggest
        team_id = p.get("team", 0)
        injury_pen = getattr(self, '_team_injury_penalty', {}).get(team_id, 1.0)
        if pos in (3, 4):
            raw = (atk - 1200) / 300
        else:
            raw = (defn - 1200) / 300
        # Dampen positive strength when team is injured
        if raw > 0:
            return raw * injury_pen
        return raw

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
            return 0.25
        elif net > 50000:
            return 0.15
        elif net > 10000:
            return 0.08
        elif net < -100000:
            return -0.15
        elif net < -50000:
            return -0.08
        else:
            return 0.0

    def _calc_bonus_tendency(self, p: dict) -> float:
        bonus = int(p.get("bonus", 0))
        starts = max(int(p.get("starts", 0)), 1)
        bonus_per_start = bonus / starts
        return (bonus_per_start - 0.4) / 1.2

    def _calc_team_form_factor(self, p: dict) -> float:
        team_id = p.get("team", 0)
        ts = self.team_stats.get(team_id, {})
        momentum = calc_team_momentum(self.team_stats, team_id)
        l5_wr = ts.get("last5_win_rate", 0.4)
        l5_gf = ts.get("last5_gf_pg", 1.3)

        # Apply injury penalty — a team missing key players has lower effective form
        injury_pen = getattr(self, '_team_injury_penalty', {}).get(team_id, 1.0)

        pos = p.get("position_id", 3)
        if pos in (3, 4):
            score = (l5_wr - 0.4) * 0.5 + (l5_gf - 1.3) * 0.15 + momentum * 0.3
        else:
            l5_ga = ts.get("last5_ga_pg", 1.3)
            l5_cs = ts.get("last5_cs", 1) / max(len(ts.get("results", [])[-5:]), 1)
            score = (l5_wr - 0.4) * 0.3 + (1.3 - l5_ga) * 0.2 + l5_cs * 0.3 + momentum * 0.2

        # Dampen positive form when team is weakened by injuries
        if score > 0:
            score *= injury_pen

        return max(-0.4, min(score, 0.4))

    def _calc_h2h_factor(self, p: dict, fixture_info: dict,
                         fix_xg_data: dict) -> float:
        h2h = fix_xg_data.get("h2h", {})
        matches = h2h.get("matches", 0)
        h2h_score = 0.0
        if matches > 0:
            dominance = (h2h["a_wins"] - h2h["b_wins"]) / matches
            gf_adv = (h2h["a_goals"] - h2h["b_goals"]) / matches
            h2h_score = dominance * 0.15 + gf_adv * 0.05

        fixture_xg = fix_xg_data.get("team_xg", 1.3)
        fixture_xgc = fix_xg_data.get("team_xgc", 1.3)
        pos = p.get("position_id", 3)
        if pos in (3, 4):
            xg_bonus = (fixture_xg - 1.3) * 0.12
        else:
            xg_bonus = (1.3 - fixture_xgc) * 0.12
        return max(-0.3, min(h2h_score + xg_bonus, 0.3))

    # ══════════════════════════════════════════════════════════
    #  Availability
    # ══════════════════════════════════════════════════════════

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

    def _apply_availability_discount(self, xp: float, availability: dict) -> float:
        """Apply availability discount. 75%+ = full points per user rule."""
        if availability["status"] == "doubtful":
            chance = availability.get("chance", 50)
            if chance >= 75:
                return xp  # Full points
            elif chance >= 50:
                return xp * 0.50
            elif chance >= 25:
                return xp * 0.25
            else:
                return xp * 0.10
        return xp

    # ══════════════════════════════════════════════════════════
    #  Confidence
    # ══════════════════════════════════════════════════════════

    def _calc_confidence(self, p: dict, fixtures: list,
                         starter: dict, availability: dict,
                         teammates_out: int = 0) -> float:
        score = 0.50
        tier = starter["tier"]
        tier_bonus = {"nailed": 0.25, "regular": 0.15, "rotation": 0.0,
                      "fringe": -0.15, "bench_warmer": -0.30}
        score += tier_bonus.get(tier, 0)

        starts = int(p.get("starts", 0))
        if starts > 20:
            score += 0.10
        elif starts > 10:
            score += 0.05
        elif starts < 3:
            score -= 0.15

        # Minutes volatility reduces confidence
        vol = starter.get("mins_volatility", 0.5)
        if vol > 0.6:
            score -= 0.10
        elif vol < 0.25:
            score += 0.05

        # Teammate injury boost → more likely to play → higher confidence
        if teammates_out >= 2:
            score += 0.15
        elif teammates_out >= 1 and starter.get("injury_boost"):
            score += 0.10

        # Availability
        if availability["status"] == "available":
            score += 0.05
        elif availability["status"] == "doubtful":
            chance = availability.get("chance", 50)
            if chance >= 75:
                score -= 0.05
            else:
                score -= 0.20

        # DGW adds uncertainty
        if len(fixtures) >= 2:
            score -= 0.05

        # Team form signal
        team_id = p.get("team", 0)
        momentum = calc_team_momentum(self.team_stats, team_id)
        if abs(momentum) > 0.3:
            score += 0.05

        return max(0.10, min(score, 0.99))

    # ══════════════════════════════════════════════════════════
    #  Helper
    # ══════════════════════════════════════════════════════════

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
            "starter_quality": {"tier": "unavailable", "multiplier": 0,
                                "avg_mins": 0, "start_rate": 0, "minutes_pct": 0,
                                "starts": 0, "total_minutes": 0, "mins_volatility": 0,
                                "dgw_both_start_prob": None, "dgw_effective_matches": 0},
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
