"""
Model Optimizer - Analyze prediction accuracy and suggest weight refinements
"""
import json
import math
import os
import re
from typing import Dict, List, Tuple
from data_fetcher import fetch_bootstrap, fetch_gameweek_live, get_current_gameweek
from config import PREDICTION_WEIGHTS


def load_predictions(gw: int) -> Dict:
    """Load predictions for a specific GW."""
    try:
        with open(f'output/gw{gw}_predictions.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Warning: Could not load GW{gw} predictions: {e}")
        return None


def find_available_prediction_gws() -> List[int]:
    """Find all GW prediction files available on disk."""
    if not os.path.isdir('output'):
        return []
    gws = []
    for fname in os.listdir('output'):
        m = re.match(r'gw(\d+)_predictions\.json$', fname)
        if m:
            gws.append(int(m.group(1)))
    return sorted(gws)


def find_analyzable_gw() -> int:
    """Find the most recent GW that has BOTH predictions on disk AND completed results.
    
    FPL event_points contains the LAST FINISHED GW's points.
    If current_gw is ongoing (33), event_points has GW32 data.
    We need predictions for GW32 to compare.
    """
    current_gw = get_current_gameweek()
    available = find_available_prediction_gws()
    
    # Most recent completed GW = current_gw - 1 (if current is ongoing)
    # Try current_gw - 1 first, then older ones
    for candidate in range(current_gw - 1, 0, -1):
        if candidate in available:
            return candidate
    return None


def calculate_accuracy_metrics(gw: int) -> Dict:
    """Calculate MAE, RMSE and correlation for a specific GW.

    Pulls the actual per-player points for *that* GW from the FPL live
    endpoint (/api/event/{gw}/live/), not from bootstrap.event_points
    which only reflects the single most-recently finished GW. This lets
    us score historical GWs and do true rolling evaluation.
    """
    predictions = load_predictions(gw)
    if not predictions:
        return {"error": f"No predictions found for GW{gw}"}

    pred_list = predictions.get('predictions', [])
    if not pred_list:
        return {"error": f"No predictions data in file for GW{gw}"}

    # 1) Pull the historical actuals for this GW.
    try:
        live = fetch_gameweek_live(gw) or {}
    except Exception as e:
        return {"error": f"Failed to fetch live data for GW{gw}: {e}"}

    elements = live.get('elements') or []
    # id -> total_points; minutes used to filter zero-padded entries
    live_map = {}
    for el in elements:
        pid = el.get('id')
        stats = el.get('stats') or {}
        if pid is not None:
            live_map[pid] = {
                'total_points': stats.get('total_points', 0) or 0,
                'minutes': stats.get('minutes', 0) or 0,
            }

    if not live_map:
        return {"error": f"Live endpoint returned no elements for GW{gw}"}

    errors, abs_errors, squared_errors = [], [], []
    actual_points, predicted_points = [], []

    for pred in pred_list:
        player_id = pred.get('player_id')
        xpts = pred.get('predicted_points', 0) or 0
        if player_id is None or player_id not in live_map:
            continue
        live_row = live_map[player_id]
        actual = live_row['total_points']
        played = live_row['minutes'] > 0
        # Only count players who actually played OR were strongly predicted.
        # Same policy as before, just sourced from live_map.
        if not (played or xpts > 2):
            continue
        error = actual - xpts
        errors.append(error)
        abs_errors.append(abs(error))
        squared_errors.append(error ** 2)
        actual_points.append(actual)
        predicted_points.append(xpts)

    if not errors:
        return {"error": f"No matchable players for GW{gw}"}

    import math as _math
    mae = sum(abs_errors) / len(abs_errors)
    rmse = _math.sqrt(sum(squared_errors) / len(squared_errors))

    n = len(actual_points)
    sum_xy = sum(a * p for a, p in zip(actual_points, predicted_points))
    sum_x, sum_y = sum(actual_points), sum(predicted_points)
    sum_x2 = sum(a * a for a in actual_points)
    sum_y2 = sum(p * p for p in predicted_points)
    correlation = 0.0
    denom = _math.sqrt((n * sum_x2 - sum_x ** 2) * (n * sum_y2 - sum_y ** 2))
    if denom:
        correlation = (n * sum_xy - sum_x * sum_y) / denom

    over_predictions = [e for e in errors if e < 0]
    under_predictions = [e for e in errors if e > 0]

    return {
        "gw": gw,
        "total_analyzed": len(errors),
        "mae": round(mae, 2),
        "rmse": round(rmse, 2),
        "correlation": round(correlation, 3),
        "avg_error": round(sum(errors) / len(errors), 2),
        "over_predictions": {
            "count": len(over_predictions),
            "avg": round(sum(over_predictions) / len(over_predictions), 2) if over_predictions else 0
        },
        "under_predictions": {
            "count": len(under_predictions),
            "avg": round(sum(under_predictions) / len(under_predictions), 2) if under_predictions else 0
        }
    }


def analyze_position_accuracy(gw: int) -> Dict:
    """Analyze accuracy by position."""
    predictions = load_predictions(gw)
    if not predictions:
        return {"error": f"No predictions found for GW{gw}"}
    
    bootstrap = fetch_bootstrap()
    player_map = {p['id']: p for p in bootstrap['elements']}
    
    position_stats = {1: [], 2: [], 3: [], 4: []}  # GKP, DEF, MID, FWD
    
    pred_list = predictions.get('predictions', [])
    if not pred_list:
        return {"error": f"No predictions data in file"}
    
    for pred in pred_list:
        player_id = pred.get('player_id')  # Key is 'player_id'
        xpts = pred.get('predicted_points', 0)
        pos = pred.get('position_id')  # Position is numeric ID
        
        if player_id and player_id in player_map and pos in position_stats:
            player_data = player_map[player_id]
            actual = player_data.get('event_points', 0)
            
            if actual > 0 or xpts > 2:
                error = abs(actual - xpts)
                position_stats[pos].append(error)
    
    pos_names = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
    result = {}
    
    for pos, errors in position_stats.items():
        if errors:
            result[pos_names[pos]] = {
                "mae": round(sum(errors) / len(errors), 2),
                "count": len(errors)
            }
    
    return result


def analyze_recent_gameweeks(num_gws: int = 3) -> Dict:
    """Analyze accuracy for the most recent N completed GWs with predictions.

    Iterates backwards from the last-completed GW, computing per-GW metrics
    and a linear-decay weighted average (most recent GW has the largest
    weight). Gracefully handles missing prediction files or GWs where the
    live endpoint returns nothing.
    """
    try:
        num_gws = max(1, int(num_gws))
    except (TypeError, ValueError):
        num_gws = 3

    current_gw = get_current_gameweek()
    available = find_available_prediction_gws()

    if not available:
        return {
            "error": "No prediction files found on disk",
            "suggestion": "Run the prediction engine first to generate prediction data.",
        }

    last_completed = max(0, current_gw - 1)
    # Candidate GWs: last_completed, last_completed-1, ... (only those with a prediction file)
    candidate_gws = [gw for gw in range(last_completed, max(0, last_completed - num_gws - 2), -1)
                     if gw in available]
    # Keep at most num_gws, ordered most-recent-first for weighting logic.
    candidate_gws = candidate_gws[:num_gws]

    if not candidate_gws:
        # Original fallback behaviour if nothing lines up cleanly.
        gw_to_analyze = find_analyzable_gw()
        if gw_to_analyze is None:
            return {
                "error": f"No analyzable GW found. Current GW is {current_gw}, "
                         f"available prediction files: {available}",
                "suggestion": (
                    f"Generate predictions for GW{current_gw - 1} (the last completed GW) "
                    f"to enable model analysis."
                ),
                "available_prediction_gws": available,
                "current_gw": current_gw,
            }
        candidate_gws = [gw_to_analyze]

    results = []
    for gw in candidate_gws:
        metrics = calculate_accuracy_metrics(gw)
        if 'error' in metrics:
            # Skip individual failures but keep going
            continue
        results.append(metrics)

    if not results:
        return {
            "error": "All candidate GWs failed to score (live endpoint returned no matchable data).",
            "gameweeks_attempted": candidate_gws,
            "available_prediction_gws": available,
            "current_gw": current_gw,
            "suggestion": "Make sure prediction files match historical GWs that have finished.",
        }

    # Linear-decay weights: oldest=1 ... newest=len(results)
    # results is currently newest-first, so reverse for weighting
    ordered_oldest_first = list(reversed(results))
    weights = list(range(1, len(ordered_oldest_first) + 1))
    total_w = sum(weights)

    def _wavg(key):
        return sum((r.get(key) or 0) * w for r, w in zip(ordered_oldest_first, weights)) / total_w

    averages = {
        'mae': round(_wavg('mae'), 2),
        'rmse': round(_wavg('rmse'), 2),
        'correlation': round(_wavg('correlation'), 3),
    }

    return {
        'gameweeks_analyzed': [r['gw'] for r in results],
        'individual_results': results,
        'averages': averages,
        'current_gw': current_gw,
        'available_prediction_gws': available,
        'weighting': 'linear_decay (most recent GW weighted highest)',
        'note': (
            f"Rolling analysis over {len(results)} GW(s): "
            f"{[r['gw'] for r in results]}. Averages are linear-decay weighted."
        ),
    }


def suggest_weight_adjustments() -> Dict:
    """Suggest weight adjustments based on recent accuracy analysis."""
    analysis = analyze_recent_gameweeks(3)
    
    if "error" in analysis:
        return analysis
    
    suggestions = []
    current_weights = PREDICTION_WEIGHTS.copy()
    
    avg_mae = analysis['averages']['mae']
    avg_correlation = analysis['averages']['correlation']
    
    # Suggest adjustments based on performance
    if avg_mae > 3.5:
        suggestions.append({
            "issue": "High MAE (>3.5) indicates predictions are too optimistic or pessimistic",
            "recommendation": "Consider increasing form weight and decreasing season_avg weight"
        })
        current_weights['form'] = min(0.25, current_weights['form'] + 0.03)
        current_weights['season_avg'] = max(0.05, current_weights['season_avg'] - 0.02)
    
    if avg_correlation < 0.5:
        suggestions.append({
            "issue": "Low correlation (<0.5) suggests model factors don't align with reality",
            "recommendation": "Boost fixture_difficulty and ict_index weights"
        })
        current_weights['fixture_difficulty'] = min(0.20, current_weights['fixture_difficulty'] + 0.03)
        current_weights['ict_index'] = min(0.12, current_weights['ict_index'] + 0.02)
    
    # Analyze error bias
    for result in analysis['individual_results']:
        if result['avg_error'] < -1.5:
            suggestions.append({
                "issue": f"GW{result['gw']}: Systematic over-prediction (avg error: {result['avg_error']})",
                "recommendation": "Model is too bullish - consider dampening bonus and CS probabilities"
            })
        elif result['avg_error'] > 1.5:
            suggestions.append({
                "issue": f"GW{result['gw']}: Systematic under-prediction (avg error: {result['avg_error']})",
                "recommendation": "Model is too conservative - consider increasing goal/assist expectations"
            })
    
    # Normalize weights
    total = sum(current_weights.values())
    adjusted_weights = {k: round(v / total, 3) for k, v in current_weights.items()}
    
    return {
        "analysis": analysis,
        "averages": analysis.get("averages", {}),
        "gameweeks_analyzed": analysis.get("gameweeks_analyzed", []),
        "current_weights": PREDICTION_WEIGHTS,
        "suggested_weights": adjusted_weights,
        "suggestions": suggestions,
        "performance_grade": _grade_performance(avg_mae, avg_correlation)
    }


def _grade_performance(mae: float, correlation: float) -> str:
    """Grade model performance."""
    if mae < 2.5 and correlation > 0.6:
        return "A (Excellent)"
    elif mae < 3.0 and correlation > 0.5:
        return "B (Good)"
    elif mae < 3.5 and correlation > 0.4:
        return "C (Fair)"
    else:
        return "D (Needs Improvement)"


# _PERSIST_PATCH_APPLIED_
def apply_weight_adjustments(new_weights: Dict) -> bool:
    """Persist new weights to Supabase (app_settings table) AND apply them to
    the in-memory config so predictions immediately use them. Survives restart.

    We deliberately do NOT rewrite config.py anymore — that was wiped on every
    redeploy and was unsafe under concurrent admin edits.
    """
    try:
        import config as _config
        from app_storage import set_setting

        # 1) Sanitise: keep only keys the engine actually uses; coerce to float.
        valid_keys = set(_config.PREDICTION_WEIGHTS.keys())
        cleaned = {}
        for k, v in (new_weights or {}).items():
            if k in valid_keys:
                try:
                    cleaned[k] = float(v)
                except (TypeError, ValueError):
                    continue
        if not cleaned:
            print("[OPT] No valid weight keys in payload; nothing to apply.")
            return False

        # Merge so any missing key keeps its current value
        merged = dict(_config.PREDICTION_WEIGHTS)
        merged.update(cleaned)

        # 2) Persist first — if DB write fails we must NOT silently drift.
        ok = set_setting("prediction_weights", merged)
        if not ok:
            print("[OPT] set_setting returned False; aborting in-memory update.")
            return False

        # 3) Mutate the live dict IN PLACE so all existing imports
        #    (prediction_engine did `from config import PREDICTION_WEIGHTS`)
        #    see the new values without needing importlib.reload.
        _config.PREDICTION_WEIGHTS.clear()
        _config.PREDICTION_WEIGHTS.update(merged)

        # Keep prediction_engine module-level binding in sync too
        try:
            import prediction_engine as _pe
            _pe.PREDICTION_WEIGHTS = _config.PREDICTION_WEIGHTS
        except Exception:
            pass

        print(f"[OPT] Saved {len(merged)} weights to Supabase and hot-swapped in memory.")
        return True
    except Exception as e:
        print(f"[OPT] apply_weight_adjustments failed: {e}")
        return False


def load_saved_weights() -> bool:
    """Call once at app startup. Loads admin-tuned weights from Supabase
    (if present) and overlays them on config.PREDICTION_WEIGHTS in memory.
    Returns True if any saved weights were applied."""
    try:
        import config as _config
        from app_storage import get_setting
        saved = get_setting("prediction_weights", None)
        if not isinstance(saved, dict) or not saved:
            return False
        valid_keys = set(_config.PREDICTION_WEIGHTS.keys())
        applied = {k: float(v) for k, v in saved.items()
                   if k in valid_keys and isinstance(v, (int, float))}
        if not applied:
            return False
        _config.PREDICTION_WEIGHTS.update(applied)
        try:
            import prediction_engine as _pe
            _pe.PREDICTION_WEIGHTS = _config.PREDICTION_WEIGHTS
        except Exception:
            pass
        print(f"[OPT] Restored {len(applied)} admin-tuned weights from storage.")
        return True
    except Exception as e:
        print(f"[OPT] load_saved_weights failed: {e}")
        return False


def reset_weights_to_defaults() -> bool:
    """Admin action: wipe the saved override so the app reverts to config.py defaults
    on next restart. Does NOT mutate RAM — restart clears it."""
    try:
        from app_storage import delete_setting
        delete_setting("prediction_weights")
        return True
    except Exception as e:
        print(f"[OPT] reset_weights_to_defaults failed: {e}")
        return False


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == 'analyze':
        result = suggest_weight_adjustments()
        print(json.dumps(result, indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == 'gw':
        gw = int(sys.argv[2]) if len(sys.argv) > 2 else get_current_gameweek()
        metrics = calculate_accuracy_metrics(gw)
        pos_acc = analyze_position_accuracy(gw)
        print(f"\nGW{gw} Accuracy Metrics:")
        print(json.dumps(metrics, indent=2))
        print(f"\nPosition Accuracy:")
        print(json.dumps(pos_acc, indent=2))
    else:
        print("Usage:")
        print("  python model_optimizer.py analyze    # Full analysis with suggestions")
        print("  python model_optimizer.py gw [num]   # Analyze specific GW")
