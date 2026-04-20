"""
FPL Predictor - Configuration
"""

# ── FPL API Base ──────────────────────────────────────────────
FPL_API_BASE = "https://fantasy.premierleague.com/api"

# ── User Settings (persisted to user_settings.json) ─────────
SETTINGS_FILE = "user_settings.json"

FPL_ENDPOINTS = {
    "bootstrap": f"{FPL_API_BASE}/bootstrap-static/",
    "fixtures": f"{FPL_API_BASE}/fixtures/",
    "player_detail": f"{FPL_API_BASE}/element-summary/{{player_id}}/",
    "gameweek_live": f"{FPL_API_BASE}/event/{{event_id}}/live/",
    "dream_team": f"{FPL_API_BASE}/dream-team/{{event_id}}/",
    "set_pieces": f"{FPL_API_BASE}/team/set-piece-notes/",
    "event_status": f"{FPL_API_BASE}/event-status/",
}

# ── FPL Scoring System ────────────────────────────────────────
SCORING = {
    # Minutes played
    "minutes_1_59": 1,
    "minutes_60_plus": 2,
    # Goals scored by position (1=GKP, 2=DEF, 3=MID, 4=FWD)
    "goals": {1: 10, 2: 6, 3: 5, 4: 4},
    # Assists
    "assist": 3,
    # Clean sheets by position
    "clean_sheet": {1: 4, 2: 4, 3: 1, 4: 0},
    # Saves (GKP only) - per 3 saves
    "saves_per_3": 1,
    # Penalties
    "penalty_save": 5,
    "penalty_miss": -2,
    # Goals conceded (GKP/DEF) - per 2 goals conceded
    "goals_conceded_per_2": -1,
    # Cards
    "yellow_card": -1,
    "red_card": -3,
    # Own goal
    "own_goal": -2,
    # Bonus points (1-3 for top performers)
    "bonus_max": 3,
}

# ── Squad Rules ───────────────────────────────────────────────
SQUAD_BUDGET = 1000  # £100.0m in tenths
SQUAD_SIZE = 15
STARTING_XI = 11
MAX_PER_TEAM = 3

POSITION_LIMITS = {
    1: {"name": "GKP", "squad_min": 2, "squad_max": 2, "play_min": 1, "play_max": 1},
    2: {"name": "DEF", "squad_min": 5, "squad_max": 5, "play_min": 3, "play_max": 5},
    3: {"name": "MID", "squad_min": 5, "squad_max": 5, "play_min": 2, "play_max": 5},
    4: {"name": "FWD", "squad_min": 3, "squad_max": 3, "play_min": 1, "play_max": 3},
}

# ── Prediction Weights ────────────────────────────────────────
# These weights tune how different factors influence the prediction
PREDICTION_WEIGHTS = {
    "form": 0.20,                # Recent form (last 5 GWs)
    "fixture_difficulty": 0.15,  # Opponent difficulty (FDR)
    "season_avg": 0.08,          # Season average ppg
    "home_away": 0.07,           # Home/away advantage
    "ict_index": 0.10,           # Influence, Creativity, Threat
    "minutes_consistency": 0.07, # Consistent starter?
    "team_strength": 0.05,       # FPL team strength ratings
    "set_pieces": 0.05,          # Set piece duties
    "ownership_momentum": 0.03,  # Transfer trends
    "bonus_tendency": 0.02,      # Historical bonus points tendency
    # NEW: Team-level factors derived from actual match results
    "team_form": 0.10,           # Team last-5 win rate + momentum + goals
    "h2h_factor": 0.08,          # H2H record + fixture-specific xG/xGC
    "win_probability": 0.08,     # Team win probability from Poisson(xG)
}

# ── Fixture Difficulty Modifier ───────────────────────────────
# FDR goes 1-5 (1=easiest, 5=hardest)
# This maps FDR to a multiplier for expected points
FDR_MULTIPLIER = {
    1: 1.30,  # Very easy fixture
    2: 1.15,  # Easy fixture
    3: 1.00,  # Average fixture
    4: 0.85,  # Tough fixture
    5: 0.70,  # Very tough fixture
}

# ── Home/Away Bonus ───────────────────────────────────────────
HOME_BONUS = 1.10   # 10% boost for home games
AWAY_PENALTY = 0.92  # 8% reduction for away games

# ── Chip Strategy ─────────────────────────────────────────────
CHIPS = {
    "wildcard": {
        "name": "Wildcard",
        "code": "WC",
        "description": "Unlimited free transfers for one GW. Reset your entire squad.",
        "best_when": [
            "Multiple injuries/suspensions in your squad",
            "Before a big DGW to load up on DGW players",
            "Team value tanking from price drops",
            "Major fixture swing (multiple teams difficulty changes)",
        ],
    },
    "free_hit": {
        "name": "Free Hit",
        "code": "FH",
        "description": "Temporary squad for one GW only, reverts next GW.",
        "best_when": [
            "Blank gameweek (BGW) - many of your players do not play",
            "One-off DGW where you want max DGW exposure for 1 week only",
            "Your current squad has many blanks in a specific GW",
        ],
    },
    "bench_boost": {
        "name": "Bench Boost",
        "code": "BB",
        "description": "All 15 players score points (bench included).",
        "best_when": [
            "DGW with strong bench all having 2 fixtures",
            "All 15 squad members are nailed starters",
            "Combine with Wildcard the week before to build a DGW-heavy 15",
        ],
    },
    "triple_captain": {
        "name": "Triple Captain",
        "code": "TC",
        "description": "Captain scores 3x points instead of 2x.",
        "best_when": [
            "DGW with a premium captain having 2 easy home fixtures",
            "Captain has exceptional form + easy fixture(s)",
            "Premium forward/mid with penalty duties in DGW",
        ],
    },
}

# Chip scoring thresholds for recommendation
CHIP_THRESHOLDS = {
    "bench_boost_min_bench_xp": 12.0,
    "triple_captain_min_xp": 10.0,
    "free_hit_blank_threshold": 4,
    "dgw_player_threshold": 8,
}
