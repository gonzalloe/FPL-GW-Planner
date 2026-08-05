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
    get_next_gameweek, get_current_gameweek,
    get_last_season_rates, get_previous_season_team_stats, get_recent_gw_stats
)
from team_analysis import (
    build_team_stats, get_h2h, get_fixture_xg,
    calc_team_momentum, get_team_analysis_summary
)


# ══════════════════════════════════════════════════════════════
# Cold-start priors (position averages - used only when a player
# has neither current-season nor previous-season data)
# ══════════════════════════════════════════════════════════════
POSITION_XG_PRIOR = {1: 0.0, 2: 0.05, 3: 0.20, 4: 0.35}      # GKP, DEF, MID, FWD
POSITION_XA_PRIOR = {1: 0.0, 2: 0.05, 3: 0.15, 4: 0.10}
POSITION_BONUS_PRIOR = {1: 0.15, 2: 0.20, 3: 0.25, 4: 0.20}   # bonus per start
# Role
PROMOTED_ROLE_PHASEOUT_GW = 6
ESTABLISHED_ROLE_PHASEOUT_GW = 10
# Attacking ability (xG/xA/bonus)
PROMOTED_ATTACK_SHRINKAGE = 450
ESTABLISHED_ATTACK_SHRINKAGE = 720

PRIOR_FETCH_MINUTES_THRESHOLD = 450  # only fetch last-season history for players still under this many mins
POSITION_START_RATE_PRIOR = {1: 0.55, 2: 0.70, 3: 0.65, 4: 0.65}
POSITION_MINUTES_PRIOR = {1: 0.70, 2: 0.65, 3: 0.60, 4: 0.60}
PLAYER_PRIOR_PHASEOUT_GW = 12

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
        previous_priors = self._build_previous_season_priors()
        self.team_stats = build_team_stats(
            self.fixtures,
            self.teams,
            previous_season_stats=previous_priors
        )
        self.fixture_xg_cache = {}
        self.fixture_cache_hits = 0
        self.fixture_cache_misses = 0

    def _build_previous_season_priors(self) -> dict:
        """
        Real prior-season results (Vaastav) for established teams, backfilled
        with regression-calibrated strength-rating priors only for teams
        absent from that dataset (newly promoted).
        """
        from data_fetcher import get_previous_season_team_stats, get_promoted_team_priors
        real_priors = get_previous_season_team_stats(self.bootstrap, self.teams)
        fallback_priors = get_promoted_team_priors(self.bootstrap, self.teams, real_priors)
        #debug temp
        print("\n=== FINAL TEAM PRIORS ===")
        for tid, p in {**fallback_priors, **real_priors}.items():
            print(
                self.teams[tid]["name"],
                "GF:", p["gf_per_game"],
                "GA:", p["ga_per_game"]
            )
        merged = dict(fallback_priors)  # promoted teams start here
        merged.update(real_priors)      # established teams overwrite with real data
        # Store promoted teams automatically
        self.promoted_team_ids = set(fallback_priors.keys())
        return merged


    def _prepare_player_priors(self):
        """
        Populate p['_prior_xg_per90'] / p['_prior_xa_per90'] / p['_prior_bonus_per_start']
        for every player, per the fallback hierarchy:
            current-season data (applied later, per-fixture, via shrinkage)
            -> previous PL season data (fetched here, only for players with 0 mins)
            -> position-average fallback (never zero for attackers)
        Called once per predict_all() run - not per fixture/per prediction -
        so no API calls happen inside the hot prediction loop.
        """
        for pid, p in self.players.items():
            pos = p.get("position_id", 3)
            mins_played = int(p.get("minutes", 0))

            prior_xg = POSITION_XG_PRIOR.get(pos, 0.15)
            prior_xa = POSITION_XA_PRIOR.get(pos, 0.10)
            prior_bonus = POSITION_BONUS_PRIOR.get(pos, 0.20)
            # Only hit the network for players with zero current-season minutes.
            # Once a player has any minutes, shrinkage already down-weights the
            # prior proportionally, so a network fetch is no longer needed -
            # this avoids one API call per player on every prediction run.
            if mins_played == 0:
                try:
                    rates = get_last_season_rates(pid)
                except Exception:
                    rates = {}
                if rates:
                    prior_xg = rates.get("xg_per90") or prior_xg
                    prior_xa = rates.get("xa_per90") or prior_xa
                    prior_bonus = rates.get("bonus_per_start") or prior_bonus
            p["_prior_xg_per90"] = prior_xg
            p["_prior_xa_per90"] = prior_xa
            p["_prior_bonus_per_start"] = prior_bonus
            try:
                recent = get_recent_gw_stats(pid, window=5)
            except Exception:
                recent = {}
            p["_recent_start_rate"] = recent.get("recent_start_rate")
            p["_recent_avg_mins"] = recent.get("recent_avg_mins")
            p["_recent_games"] = recent.get("recent_games", 0)   

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
        profile = self.calculate_expected_minutes(p, num_fixtures, teammates_out, out_minutes)
        p["starter_quality"] = {**profile, "tier": self._derive_tier_label(profile)}
        # debug temp
        if p.get("web_name") in ("Dowman", "Foden"):  # temp debug filter
            print(f"  [TIER-DEBUG] {p.get('web_name')} predict_player() SET: "
                f"p_start={profile['p_start']} tier={p['starter_quality']['tier']} "
                f"id(p)={id(p)} id(starter_quality)={id(p['starter_quality'])}")

        # ── Per-fixture xPts ──
        total_raw = 0.0
        total_adj = 0.0
        fixture_details = []
        all_factors = {}

        for fix_idx, fix_info in enumerate(all_fixtures):
            cache_key = (
                p["team"],
                fix_info["opponent_id"],
                fix_info["is_home"]
            )
            if cache_key not in self.fixture_xg_cache:
                self.fixture_xg_cache[cache_key] = get_fixture_xg(
                    p["team"],
                    fix_info["opponent_id"],
                    fix_info["is_home"],
                    self.team_stats
                )
            fix_xg_data = self.fixture_xg_cache[cache_key]
            xmins = profile["xmins"]
            # Reduce minutes expectation for second DGW fixture
            if num_fixtures >= 2 and fix_idx > 0:
                xmins *= 0.85
            fix_ev = self._fixture_ev(p, fix_info, fix_xg_data, xmins, profile["p_plays_60"])

            # Contextual factor modifiers
            factors = self._calc_all_factors(p, fix_info, fix_xg_data)
            weighted_mod = sum(factors.get(k, 0) * PREDICTION_WEIGHTS.get(k, 0)
                               for k in PREDICTION_WEIGHTS)
            # Modifiers are bounded to avoid runaway inflation
            weighted_mod = max(-0.35, min(weighted_mod, 0.45))
            fix_xp = fix_ev["appearance"] + fix_ev["other"] * (1.0 + weighted_mod)
            fix_xp = max(0.0, fix_xp)

            total_raw += fix_xp

            # Availability discount
            adj_xp = fix_xp
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

        confidence = self._calc_confidence(p, all_fixtures, p["starter_quality"], availability, teammates_out)

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
            "starter_quality": p["starter_quality"],
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
            
        self._prepare_player_priors()

        results = []
        for pid, p in self.players.items():
            chance = p.get("chance_of_playing_next_round")
            # Only skip players explicitly marked as very unlikely (0% or <25%)
            if chance is not None and chance < min_chance:
                continue

            pred = self.predict_player(pid, target_gw)
            # DEBUG TEMP
            if pred.get("name") in ("Dowman", "Foden"):
                print(f"  [TIER-DEBUG] {pred.get('name')} predict_all() COLLECTED: "
                    f"p_start={pred.get('starter_quality',{}).get('p_start')} "
                    f"tier={pred.get('starter_quality',{}).get('tier')}")
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
                fix_xg_data: dict, xmins: float, p_plays_60=None) -> float:
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
        p_plays = min(xmins / 90.0, 1.0)
        if p_plays_60 is None:  # fallback only if not passed
            p_plays_60 = max(0, (xmins - 30) / 60.0)
            p_plays_60 = min(p_plays_60, 1.0)
        mins_fraction = xmins / 90.0

        # ── 1. Appearance points ──
        appearance_pts = p_plays_60 * 2.0 + (p_plays - p_plays_60) * 1.0
        other_ev = 0.0

        # ── Cold-start blend weight (shared by goals + assists) ──
        if self.is_promoted_player(p):
            shrinkage = PROMOTED_ATTACK_SHRINKAGE
        else:
            shrinkage = ESTABLISHED_ATTACK_SHRINKAGE
        w_current = mins_played / (mins_played + shrinkage)

        # ── 2. Goals (Poisson) ──
        xg_season = float(p.get("expected_goals", 0))
        xg_current_per90 = (xg_season / (mins_played / 90.0)) if mins_played > 0 else 0.0
        prior_xg_per90 = p.get("_prior_xg_per90", POSITION_XG_PRIOR.get(pos, 0.15))
        xg_per90 = w_current * xg_current_per90 + (1 - w_current) * prior_xg_per90
        fdr = fix_info["fdr"]
        fdr_mod = self._position_fdr_modifier(pos, fdr, fix_info["is_home"])
        team_xg = fix_xg_data.get("team_xg", 1.35)
        scoring_context = team_xg / 1.35
        opp_id = fix_info.get("opponent_id", 0)
        opp_injury_pen = getattr(self, '_team_injury_penalty', {}).get(opp_id, 1.0)
        if opp_injury_pen < 1.0:
            opp_weakness = 1.0 + (1.0 - opp_injury_pen) * 0.5
            scoring_context *= opp_weakness
        actual_goals = int(p.get("goals_scored", 0))
        xg_delta = self._calc_xg_delta_regression(actual_goals, xg_season, starts)
        effective_xg = xg_per90 * mins_fraction * fdr_mod * scoring_context * xg_delta
        effective_xg = max(0.0, effective_xg)
        goal_pts = SCORING["goals"].get(pos, 4)
        other_ev += poisson_ev_goals(effective_xg, goal_pts)

        # ── 3. Assists (Poisson) ──
        xa_season = float(p.get("expected_assists", 0))
        xa_current_per90 = (xa_season / (mins_played / 90.0)) if mins_played > 0 else 0.0
        prior_xa_per90 = p.get("_prior_xa_per90", POSITION_XA_PRIOR.get(pos, 0.10))
        xa_per90 = w_current * xa_current_per90 + (1 - w_current) * prior_xa_per90
        effective_xa = xa_per90 * mins_fraction * fdr_mod * scoring_context
        effective_xa = max(0.0, effective_xa)
        other_ev += poisson_ev_assists(effective_xa)

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
            other_ev += blended_cs * cs_pts * p_plays_60

        # ── 5. Goals conceded penalty (DEF/GKP) ──
        if pos in (1, 2):
            gc_ev = poisson_goals_conceded_ev(team_xgc)
            other_ev += gc_ev * p_plays_60 * 0.5

        # ── 6. Bonus points (persistence + position + fixture) ──
        other_ev += self._predict_bonus(p, effective_xg, effective_xa, blended_cs, fdr_mod, mins_fraction)                       

        # ── 7. Saves (GKP) ──
        if pos == 1:
            saves_season = int(p.get("saves", 0))
            saves_per90 = saves_season / max(mins_played / 90.0, 1.0) if mins_played > 0 else 3.0
            # More saves expected vs stronger opponents (higher xGC = more shots)
            conceding_context = min(team_xgc / 1.35, 1.6)
            expected_saves = saves_per90 * mins_fraction * conceding_context
            other_ev += (expected_saves / 3.0) * SCORING["saves_per_3"]
            # Penalty save (small probability based on history)
            pen_saved = int(p.get("penalties_saved", 0))
            if pen_saved > 0:
                pen_save_rate = pen_saved / max(starts, 1)
                other_ev += pen_save_rate * SCORING["penalty_save"] * 0.3

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
        other_ev += yc_rate * mins_fraction * SCORING["yellow_card"]
        other_ev += rc_rate * mins_fraction * SCORING["red_card"]
        other_ev += og_rate * mins_fraction * SCORING["own_goal"]
        other_ev += pm_rate * mins_fraction * SCORING["penalty_miss"]

        # ── 9. Defensive contributions (FPL 25/26 new rule) ──
        # 1 pt per 3 clearances+blocks+interceptions for DEF/GKP
        # NOTE: We keep this conservative — FPL API doesn't provide CBI data yet
        if pos == 2:
            base_dc_rate = 8.0
            dc_fixture_mod = 1.0 + (fdr - 3) * 0.06
            expected_dc = base_dc_rate * mins_fraction * dc_fixture_mod
            other_ev += (expected_dc / 3.0) * 1.0 * 0.35

        return {"appearance": appearance_pts, "other": max(other_ev, 0.0)}

    # ══════════════════════════════════════════════════════════
    #  Starter Quality (DGW-aware)
    # ══════════════════════════════════════════════════════════
    def get_player_role_prior(self, p: dict):
        team_id = p.get("team")
        # Promoted team: Championship role if available
        if team_id in self.promoted_team_ids:
            champ = p.get("championship_role")
            if champ:
                return {
                    "start_rate": champ.get("start_rate", 0.5),
                    "avg_minutes": champ.get("avg_minutes", 60),
                }
        # Previous FPL season (works for ALL players)
        previous_minutes = int(p.get("previous_minutes", 0))
        previous_starts = int(p.get("previous_starts", 0))
        previous_games = max(int(p.get("previous_games", 38)), 1)
        if previous_minutes > 0:
            return {
                "start_rate": min(previous_starts / previous_games, 1.0),
                "avg_minutes": previous_minutes / previous_games,
            }
        # No history
        pos = p.get("element_type", 3)
        return {
            "start_rate": POSITION_START_RATE_PRIOR.get(pos, 0.5),
            "avg_minutes": POSITION_MINUTES_PRIOR.get(pos, 60)
        }
        # debug temp
        print(
            "[PRIOR DEBUG]",
            p.get("web_name"),
            "prev mins",
            p.get("previous_minutes"),
            "prev starts",
            p.get("previous_starts"),
            "prior",
            {
                "start_rate": prior["start_rate"],
                "avg_minutes": prior["avg_minutes"]
            } if prior else None
        )


    def is_promoted_player(self, p: dict) -> bool:
        team_id = p.get("team")
        if not team_id:
            return False
        return team_id in self.promoted_team_ids


    def calculate_expected_minutes(self, p: dict, num_fixtures: int = 1, teammates_out: int = 0, out_minutes: int = 0) -> dict:
        """
        Continuous expected-minutes model. This is the ONLY source of playing-time
        signal used in xPts math (_fixture_ev). Tier labels are derived from this
        output afterward for display and are never read back into this function
        or into any prediction calculation.
        """
        total_minutes = int(p.get("minutes", 0))
        starts = int(p.get("starts", 0))
        gws_played = max(self.current_gw - 1, 1)
        season_avg_mins = total_minutes / gws_played
        season_start_rate = starts / max(total_minutes / 90.0, 1.0)
        season_start_rate = min(season_start_rate, 1.0)

        # Recency blend (fixes stale season-average bug: a player benched the
        # last 8 GWs no longer reads as reliable just because of an August hot streak)
        recent_games = p.get("_recent_games", 0)
        if recent_games >= 4 and total_minutes >= 270:
            recent_start_rate = p.get("_recent_start_rate", season_start_rate)
            recent_avg_mins = p.get("_recent_avg_mins", season_avg_mins)
            w_recent = min(recent_games / 5.0, 1.0) * 0.70
            start_rate = w_recent * recent_start_rate + (1 - w_recent) * season_start_rate
            start_rate = min(max(start_rate, 0.0), 1.0)
            avg_mins = w_recent * recent_avg_mins + (1 - w_recent) * season_avg_mins
        else:
            prior = self.get_player_role_prior(p)
            # No previous role data: New signing / academy player
            if prior is None:
                start_rate = season_start_rate
                avg_mins = season_avg_mins
            else:
                # GW-based trust progression. Same timeline for all players of same category
                if self.is_promoted_player(p):
                    phaseout_gw = PROMOTED_ROLE_PHASEOUT_GW
                else:
                    phaseout_gw = ESTABLISHED_ROLE_PHASEOUT_GW
                # current_gw starts from 1
                weight_current = min(self.current_gw / phaseout_gw,1.0)
                weight_prior = 1.0 - weight_current
                start_rate = (season_start_rate * weight_current + prior["start_rate"] * weight_prior)
                avg_mins = (season_avg_mins * weight_current + prior["avg_minutes"] * weight_prior)
        mins_volatility = self._calc_minutes_volatility(p)
        availability = float(p.get("chance_of_playing_this_round") or 100) / 100.0

        p_start = min(start_rate * availability, 1.0)
        mins_ratio = min(avg_mins / 90.0, 1.0)
        p_plays_60 = min(mins_ratio * availability * (1.0 - mins_volatility * 0.3), 1.0)

        # Rotation risk: ambiguous start rate (mid-range) is the actual risk signal,
        # not "low tier" — a 5%-start benchwarmer isn't a rotation risk, they're just not playing.
        rotation_risk = max(0.0, min(1.0 - abs(start_rate - 0.5) * 2.0, 1.0))
        rotation_risk = max(rotation_risk, mins_volatility * 0.5)

        # Injury boost applied directly to probabilities, not via tier-jump lookup
        if teammates_out >= 1:
            injured_was_starter = out_minutes > gws_played * 30
            boost = 0.0
            if injured_was_starter:
                boost = 0.15 if teammates_out == 1 else 0.25
            p_start = min(p_start + boost, 1.0)
            p_plays_60 = min(p_plays_60 + boost * 0.8, 1.0)

        xmins = p_plays_60 * 90.0 + max(p_start - p_plays_60, 0.0) * 45.0

        # DGW: expected effective matches, scaled continuously by p_start (no tier lookup)
        if num_fixtures >= 2:
            dgw_both_prob = p_start * (0.9 - rotation_risk * 0.5)
            dgw_effective = 1.0 + dgw_both_prob
        else:
            dgw_both_prob = None
            dgw_effective = 1.0

        return {
            "p_start": round(p_start, 3),
            "p_plays_60": round(p_plays_60, 3),
            "xmins": round(xmins, 1),
            "rotation_risk": round(rotation_risk, 3),
            "mins_volatility": round(mins_volatility, 2),
            "dgw_both_start_prob": round(dgw_both_prob, 2) if dgw_both_prob is not None else None,
            "dgw_effective_matches": round(dgw_effective, 2),
        }


    def _derive_tier_label(self, profile: dict) -> str:
        """Display-only squad role label."""
        p_start = profile["p_start"]
        risk = profile["rotation_risk"]
        xmins = profile["xmins"]

        # First-choice starter
        if p_start >= 0.85 and risk < 0.35:
            return "nailed"
        # Usually starts, but not completely secure
        if p_start >= 0.70:
            if risk < 0.65:
                return "regular"
            return "rotation"
        # Genuine rotation player
        if p_start >= 0.40:
            return "rotation"
        # Occasional starter / squad depth
        if p_start >= 0.20 or xmins >= 8:
            return "fringe"
        return "bench_warmer"


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
        pos = p.get("position_id", 3)
        starts = max(int(p.get("starts", 0)), 1)
        mins_played = int(p.get("minutes", 0))

        bonus_season = int(p.get("bonus", 0))
        current_bonus_rate = bonus_season / starts

        prior_bonus_rate = p.get("_prior_bonus_per_start", POSITION_BONUS_PRIOR.get(pos, 0.20))
        if self.is_promoted_player(p):
            shrinkage = PROMOTED_ATTACK_SHRINKAGE
        else:
            shrinkage = ESTABLISHED_ATTACK_SHRINKAGE
        w_current = mins_played / (mins_played + shrinkage)
        historical_rate = w_current * current_bonus_rate + (1 - w_current) * prior_bonus_rate

        gi_boost = (eff_xg * 12.0 + eff_xa * 9.0) / 30.0

        cs_boost = 0.0
        if pos in (1, 2):
            cs_boost = cs_prob * 0.5

        pos_base = {1: 0.25, 2: 0.30, 3: 0.35, 4: 0.28}
        base = pos_base.get(pos, 0.30)

        predicted_bonus = (
            0.50 * historical_rate +
            0.30 * (gi_boost + cs_boost) +
            0.20 * base
        )

        return predicted_bonus * mins_fraction * fdr_mod * 0.85

    # ══════════════════════════════════════════════════════════
    #  Context Factor Calculations
    # ══════════════════════════════════════════════════════════

    def _calc_all_factors(self, p: dict, fixture_info: dict, fix_xg_data: dict) -> dict:
        """Calculate all prediction factors for a single fixture."""
        return {
            "form": self._calc_form(p),
            # "fixture_difficulty": self._calc_fixture_factor(p, fixture_info["fdr"], fixture_info["is_home"]), # remove the fixture_difficulty entry from weighted_mod' 
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
            #"win_probability": self._calc_win_prob_factor(fix_xg_data),
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
        # Early season: no current-season evidence yet. Previous role model should handle trust
        if gw_played <= 5:
            return 0.0
        max_possible = gw_played * 90
        ratio = total_minutes / max_possible
        if ratio > 0.85:
            return 0.10
        elif ratio > 0.65:
            return 0.05
        elif ratio > 0.40:
            return -0.05
        else:
            return -0.15

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
        """Apply availability discount based on chance of playing.
        
        Even 75%-chance players carry real risk (25% chance of blank).
        Discount schedule:
          75%  -> 0.92x  (8% haircut — small but meaningful)
          50%  -> 0.55x  (nearly half)
          25%  -> 0.22x  (heavy discount)
          <25% -> 0.08x  (near-zero)
        """
        if availability["status"] == "doubtful":
            chance = availability.get("chance", 50)
            if chance >= 75:
                return xp * 0.92
            elif chance >= 50:
                return xp * 0.55
            elif chance >= 25:
                return xp * 0.22
            else:
                return xp * 0.08
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
