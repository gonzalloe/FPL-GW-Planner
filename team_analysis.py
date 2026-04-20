"""
FPL Predictor - Team Analysis Module
Computes H2H win rates, recent form (last 5), team xG/xGC per fixture.
All derived from the FPL fixtures data (319 finished matches).
"""


def build_team_stats(fixtures: list, teams: dict) -> dict:
    """
    Build comprehensive team stats from finished fixtures.
    Returns {team_id: {wins, draws, losses, goals_for, goals_against,
                       recent_results, h2h, xg_per_game, xgc_per_game, ...}}
    """
    stats = {}
    for tid in teams:
        stats[tid] = {
            "team_id": tid,
            "name": teams[tid].get("name", "?"),
            "short_name": teams[tid].get("short_name", "?"),
            "wins": 0, "draws": 0, "losses": 0,
            "goals_for": 0, "goals_against": 0,
            "clean_sheets": 0,
            "results": [],       # list of (gw, opponent_id, gf, ga, is_home, result)
            "home_results": [],
            "away_results": [],
        }

    # Process all finished fixtures in order
    finished = sorted(
        [f for f in fixtures if f.get("finished") and f.get("team_h_score") is not None],
        key=lambda f: (f.get("event", 0), f.get("id", 0))
    )

    for f in finished:
        th = f["team_h"]
        ta = f["team_a"]
        sh = f["team_h_score"]
        sa = f["team_a_score"]
        gw = f.get("event", 0)

        if th not in stats or ta not in stats:
            continue

        # Home team
        if sh > sa:
            hr, ar = "W", "L"
        elif sh == sa:
            hr, ar = "D", "D"
        else:
            hr, ar = "L", "W"

        stats[th]["goals_for"] += sh
        stats[th]["goals_against"] += sa
        stats[th]["wins"] += 1 if hr == "W" else 0
        stats[th]["draws"] += 1 if hr == "D" else 0
        stats[th]["losses"] += 1 if hr == "L" else 0
        if sa == 0:
            stats[th]["clean_sheets"] += 1
        stats[th]["results"].append((gw, ta, sh, sa, True, hr))
        stats[th]["home_results"].append((gw, ta, sh, sa, hr))

        # Away team
        stats[ta]["goals_for"] += sa
        stats[ta]["goals_against"] += sh
        stats[ta]["wins"] += 1 if ar == "W" else 0
        stats[ta]["draws"] += 1 if ar == "D" else 0
        stats[ta]["losses"] += 1 if ar == "L" else 0
        if sh == 0:
            stats[ta]["clean_sheets"] += 1
        stats[ta]["results"].append((gw, th, sa, sh, False, ar))
        stats[ta]["away_results"].append((gw, th, sa, sh, ar))

    # Compute derived stats
    for tid, s in stats.items():
        played = s["wins"] + s["draws"] + s["losses"]
        s["played"] = played
        s["win_rate"] = s["wins"] / played if played > 0 else 0
        s["gf_per_game"] = s["goals_for"] / played if played > 0 else 0
        s["ga_per_game"] = s["goals_against"] / played if played > 0 else 0
        s["cs_rate"] = s["clean_sheets"] / played if played > 0 else 0

        # Last 5 results
        last5 = s["results"][-5:] if len(s["results"]) >= 5 else s["results"]
        s["last5_wins"] = sum(1 for r in last5 if r[5] == "W")
        s["last5_draws"] = sum(1 for r in last5 if r[5] == "D")
        s["last5_losses"] = sum(1 for r in last5 if r[5] == "L")
        s["last5_gf"] = sum(r[2] for r in last5)
        s["last5_ga"] = sum(r[3] for r in last5)
        s["last5_cs"] = sum(1 for r in last5 if r[3] == 0)
        n5 = len(last5)
        s["last5_win_rate"] = s["last5_wins"] / n5 if n5 > 0 else 0
        s["last5_gf_pg"] = s["last5_gf"] / n5 if n5 > 0 else 0
        s["last5_ga_pg"] = s["last5_ga"] / n5 if n5 > 0 else 0
        # Form string like "WWDLW"
        s["last5_form_str"] = "".join(r[5] for r in last5)

    return stats


def get_h2h(team_a_id: int, team_b_id: int, team_stats: dict) -> dict:
    """
    Get head-to-head record between two teams from THIS season's fixtures.
    Returns {team_a_wins, team_b_wins, draws, team_a_goals, team_b_goals}
    """
    results_a = team_stats.get(team_a_id, {}).get("results", [])
    h2h = {"a_wins": 0, "b_wins": 0, "draws": 0, "a_goals": 0, "b_goals": 0, "matches": 0}

    for gw, opp, gf, ga, is_home, result in results_a:
        if opp == team_b_id:
            h2h["matches"] += 1
            h2h["a_goals"] += gf
            h2h["b_goals"] += ga
            if result == "W":
                h2h["a_wins"] += 1
            elif result == "L":
                h2h["b_wins"] += 1
            else:
                h2h["draws"] += 1

    return h2h


def calculate_win_probability(team_xg: float, opp_xg: float) -> float:
    """
    Calculate win probability using Poisson distribution.
    Returns probability of team winning (0.0 - 1.0).
    """
    import math
    
    # Poisson probability mass function
    def poisson_pmf(k: int, lam: float) -> float:
        return (lam ** k) * math.exp(-lam) / math.factorial(k)
    
    # Calculate probabilities for score outcomes (0-5 goals each team)
    win_prob = 0.0
    for team_goals in range(6):
        for opp_goals in range(6):
            if team_goals > opp_goals:
                prob = poisson_pmf(team_goals, team_xg) * poisson_pmf(opp_goals, opp_xg)
                win_prob += prob
    
    return min(0.95, max(0.05, win_prob))  # Clamp between 5% and 95%



def get_fixture_xg(team_id: int, opponent_id: int, is_home: bool,
                   team_stats: dict) -> dict:
    """
    Estimate xG and xGC for a specific fixture based on:
    1. Team's scoring rate (recent + season)
    2. Opponent's conceding rate (recent + season)
    3. Home/away split
    4. H2H record
    Returns {team_xg, team_xgc, opponent_xg, opponent_xgc, cs_probability}
    """
    ts = team_stats.get(team_id, {})
    os_ = team_stats.get(opponent_id, {})

    # Season scoring/conceding rates
    team_gf = ts.get("gf_per_game", 1.2)
    team_ga = ts.get("ga_per_game", 1.2)
    opp_gf = os_.get("gf_per_game", 1.2)
    opp_ga = os_.get("ga_per_game", 1.2)

    # Last-5 rates (weighted more heavily)
    team_gf_l5 = ts.get("last5_gf_pg", team_gf)
    team_ga_l5 = ts.get("last5_ga_pg", team_ga)
    opp_gf_l5 = os_.get("last5_gf_pg", opp_gf)
    opp_ga_l5 = os_.get("last5_ga_pg", opp_ga)

    # Blend season + recent (60% recent, 40% season)
    team_attack = 0.6 * team_gf_l5 + 0.4 * team_gf
    team_defence = 0.6 * team_ga_l5 + 0.4 * team_ga
    opp_attack = 0.6 * opp_gf_l5 + 0.4 * opp_gf
    opp_defence = 0.6 * opp_ga_l5 + 0.4 * opp_ga

    # League average goals per game (approx)
    league_avg = 1.35

    # xG = (team_attack * opponent_conceding) / league_avg
    team_xg = (team_attack * opp_ga) / (league_avg + 0.01)
    opp_xg = (opp_attack * team_ga) / (league_avg + 0.01)

    # Home/away adjustment
    if is_home:
        team_xg *= 1.12  # Home scoring boost
        opp_xg *= 0.90   # Away scoring penalty
    else:
        team_xg *= 0.90
        opp_xg *= 1.12

    # H2H adjustment (small)
    h2h = get_h2h(team_id, opponent_id, team_stats)
    if h2h["matches"] > 0:
        h2h_dominance = (h2h["a_wins"] - h2h["b_wins"]) / h2h["matches"]
        team_xg *= (1 + h2h_dominance * 0.05)
        opp_xg *= (1 - h2h_dominance * 0.05)

    # Clamp to reasonable range
    team_xg = max(0.3, min(team_xg, 4.0))
    opp_xg = max(0.2, min(opp_xg, 4.0))

    # Clean sheet probability (Poisson-ish: P(0 goals) ≈ e^(-xG))
    import math
    cs_prob = math.exp(-opp_xg)
    cs_prob = max(0.02, min(cs_prob, 0.65))

    # Win probability using Poisson distribution
    win_prob = calculate_win_probability(team_xg, opp_xg)
    
    return {
        "team_xg": round(team_xg, 2),
        "team_xgc": round(opp_xg, 2),
        "opponent_xg": round(opp_xg, 2),
        "opponent_xgc": round(team_xg, 2),
        "cs_probability": round(cs_prob, 3),
        "win_probability": round(win_prob, 3),
        "h2h": h2h,
    }


def calc_team_momentum(team_stats: dict, team_id: int) -> float:
    """
    Calculate team momentum score from -1 (terrible) to +1 (excellent).
    Based on last 5 results with recency weighting.
    """
    ts = team_stats.get(team_id, {})
    results = ts.get("results", [])
    last5 = results[-5:] if len(results) >= 5 else results
    if not last5:
        return 0.0

    # Recency weights: most recent match = 5x, oldest = 1x
    weights = list(range(1, len(last5) + 1))
    total_weight = sum(weights)

    score = 0.0
    for i, (gw, opp, gf, ga, is_home, result) in enumerate(last5):
        w = weights[i] / total_weight
        if result == "W":
            # Bigger wins = more momentum
            margin = min(gf - ga, 3) / 3  # Cap at 3-goal margin
            score += w * (0.6 + 0.4 * margin)
        elif result == "D":
            score += w * 0.1
        else:
            margin = min(ga - gf, 3) / 3
            score -= w * (0.5 + 0.3 * margin)

    return max(-1.0, min(score, 1.0))


def get_team_analysis_summary(team_id: int, opponent_id: int, is_home: bool,
                               team_stats: dict, teams: dict) -> dict:
    """
    Comprehensive summary for display in dashboard/AI chat.
    """
    ts = team_stats.get(team_id, {})
    os_ = team_stats.get(opponent_id, {})
    fixture_xg = get_fixture_xg(team_id, opponent_id, is_home, team_stats)
    h2h = fixture_xg["h2h"]
    momentum = calc_team_momentum(team_stats, team_id)
    opp_momentum = calc_team_momentum(team_stats, opponent_id)

    return {
        "team_id": team_id,
        "team_name": teams.get(team_id, {}).get("name", "?"),
        "team_short": teams.get(team_id, {}).get("short_name", "?"),
        "opponent_id": opponent_id,
        "opponent_name": teams.get(opponent_id, {}).get("name", "?"),
        "opponent_short": teams.get(opponent_id, {}).get("short_name", "?"),
        "is_home": is_home,
        # Season record
        "season_win_rate": round(ts.get("win_rate", 0), 3),
        "season_gf_pg": round(ts.get("gf_per_game", 0), 2),
        "season_ga_pg": round(ts.get("ga_per_game", 0), 2),
        "season_cs_rate": round(ts.get("cs_rate", 0), 3),
        # Last 5
        "last5_form": ts.get("last5_form_str", ""),
        "last5_win_rate": round(ts.get("last5_win_rate", 0), 3),
        "last5_gf_pg": round(ts.get("last5_gf_pg", 0), 2),
        "last5_ga_pg": round(ts.get("last5_ga_pg", 0), 2),
        # Opponent last 5
        "opp_last5_form": os_.get("last5_form_str", ""),
        "opp_last5_win_rate": round(os_.get("last5_win_rate", 0), 3),
        "opp_last5_gf_pg": round(os_.get("last5_gf_pg", 0), 2),
        "opp_last5_ga_pg": round(os_.get("last5_ga_pg", 0), 2),
        # H2H this season
        "h2h_wins": h2h["a_wins"],
        "h2h_draws": h2h["draws"],
        "h2h_losses": h2h["b_wins"],
        "h2h_gf": h2h["a_goals"],
        "h2h_ga": h2h["b_goals"],
        "h2h_matches": h2h["matches"],
        # Fixture xG
        "fixture_xg": fixture_xg["team_xg"],
        "fixture_xgc": fixture_xg["team_xgc"],
        "cs_probability": fixture_xg["cs_probability"],
        # Momentum
        "momentum": round(momentum, 3),
        "opp_momentum": round(opp_momentum, 3),
    }
