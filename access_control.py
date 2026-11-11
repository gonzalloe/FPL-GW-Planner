"""Response serialization shared by public prediction and team views."""
import copy

LOCK = "🔒"
PUBLIC_PLAYER_FIELDS = {
    "player_id", "name", "full_name", "team", "team_name", "team_id", "position",
    "position_id", "price", "selected_by_percent", "form", "ppg", "total_points",
    "minutes", "starts", "goals_scored", "assists", "clean_sheets", "status", "news",
    "availability", "chance_of_playing", "num_fixtures", "is_dgw", "team_last5_form",
    "is_captain", "is_vice_captain", "multiplier", "position_in_squad", "is_starter",
}
PUBLIC_FIXTURE_FIELDS = {
    "opponent", "opponent_full", "opponent_id", "is_home", "fdr", "venue", "event",
    "team_form", "opp_form",
}


def public_player(player):
    """Allowlist public fields so new model outputs cannot leak accidentally."""
    result = {key: copy.deepcopy(value) for key, value in player.items() if key in PUBLIC_PLAYER_FIELDS}
    result.update(predicted_points=LOCK, raw_xpts=LOCK, confidence=LOCK,
                  team_last5_wr=LOCK, team_season_wr=LOCK,
                  starter_quality={"tier": LOCK, "p_start": LOCK})
    fixtures = [{key: copy.deepcopy(value) for key, value in fixture.items() if key in PUBLIC_FIXTURE_FIELDS}
                for fixture in player.get("fixtures", []) if isinstance(fixture, dict)]
    for fixture in fixtures:
        fixture.update(win_probability=LOCK, xp_single=LOCK, xp_adjusted=LOCK)
    result["fixtures"] = fixtures
    result["fixture"] = fixtures[0] if fixtures else {}
    return result


def public_prediction_data(data):
    def redact(value):
        if isinstance(value, list):
            return [redact(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "player_id" in value:
            return public_player(value)
        result = {}
        for key, item in value.items():
            if key in {"captain", "vice_captain"}:
                result[key] = {"name": LOCK, "player_id": None, "predicted_points": LOCK} if item else None
            elif key in {"predicted_total_points", "squad_total_xpts", "neutral_xpts", "base_xp"}:
                result[key] = LOCK
            elif key == "chip_analysis":
                result[key] = {"best_chip": {"code": LOCK, "name": "Premium only", "score": LOCK},
                               "recommendations": []}
            elif key in {"top_picks", "differentials", "value_picks"}:
                result[key] = []
            else:
                result[key] = redact(item)
        return result
    return redact(data)
