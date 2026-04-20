"""
Model Optimizer - Analyze prediction accuracy and suggest weight refinements
"""
import json
import math
import os
import re
from typing import Dict, List, Tuple
from data_fetcher import fetch_bootstrap, get_current_gameweek
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
    """Calculate MAE, RMSE, and correlation for a specific GW.
    
    Note: FPL API's event_points contains the LAST FINISHED gameweek's points.
    We compare predictions with event_points and validate via non-zero actuals.
    """
    predictions = load_predictions(gw)
    if not predictions:
        return {"error": f"No predictions found for GW{gw}"}
    
    bootstrap = fetch_bootstrap()
    player_map = {p['id']: p for p in bootstrap['elements']}
    current_gw = get_current_gameweek()
    
    # Warn if this isn't the last-completed GW, but still try to analyze
    last_completed = current_gw - 1
    mismatch_warning = None
    if gw != last_completed:
        mismatch_warning = f"Analyzing GW{gw} but event_points data is for GW{last_completed}. Results may be stale."
    
    errors = []
    abs_errors = []
    squared_errors = []
    actual_points = []
    predicted_points = []
    
    # Get predictions list (key is 'predictions' not 'players')
    pred_list = predictions.get('predictions', [])
    if not pred_list:
        return {"error": f"No predictions data in file for GW{gw}"}
    
    for pred in pred_list:
        player_id = pred.get('player_id')  # Key is 'player_id' not 'id'
        xpts = pred.get('predicted_points', 0)  # 'predicted_points' is the actual key
        
        if player_id and player_id in player_map:
            player_data = player_map[player_id]
            actual = player_data.get('event_points', 0)
            
            # Only count players who played or were predicted to play
            if actual > 0 or xpts > 2:
                error = actual - xpts
                errors.append(error)
                abs_errors.append(abs(error))
                squared_errors.append(error ** 2)
                actual_points.append(actual)
                predicted_points.append(xpts)
    
    if not errors:
        return {"error": "No matchable data"}
    
    mae = sum(abs_errors) / len(abs_errors)
    rmse = math.sqrt(sum(squared_errors) / len(squared_errors))
    
    # Calculate correlation coefficient
    n = len(actual_points)
    sum_xy = sum(a * p for a, p in zip(actual_points, predicted_points))
    sum_x = sum(actual_points)
    sum_y = sum(predicted_points)
    sum_x2 = sum(a ** 2 for a in actual_points)
    sum_y2 = sum(p ** 2 for p in predicted_points)
    
    correlation = 0
    denominator = math.sqrt((n * sum_x2 - sum_x ** 2) * (n * sum_y2 - sum_y ** 2))
    if denominator != 0:
        correlation = (n * sum_xy - sum_x * sum_y) / denominator
    
    # Analyze error patterns
    over_predictions = [e for e in errors if e < 0]
    under_predictions = [e for e in errors if e > 0]
    
    result = {
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
    if mismatch_warning:
        result["warning"] = mismatch_warning
    return result


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
    """Analyze accuracy for the most recent completed gameweek with predictions available.
    
    FPL API behavior:
    - current_gw returns the currently ongoing GW (or next if none active)
    - event_points contains the LAST FINISHED GW's actual points
    - We need predictions for that GW to compare
    
    Falls back gracefully if the expected GW's predictions aren't on disk.
    """
    current_gw = get_current_gameweek()
    available = find_available_prediction_gws()
    
    if not available:
        return {
            "error": "No prediction files found on disk",
            "suggestion": "Run the prediction engine first to generate prediction data.",
        }
    
    # Try to find the best GW to analyze
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
    
    metrics = calculate_accuracy_metrics(gw_to_analyze)
    
    if "error" in metrics:
        return {
            **metrics,
            "available_prediction_gws": available,
            "current_gw": current_gw,
            "suggestion": "Model analysis requires prediction data that matches actual GW results.",
        }
    
    results = [metrics]
    
    return {
        "gameweeks_analyzed": [r['gw'] for r in results],
        "individual_results": results,
        "averages": {
            "mae": round(metrics['mae'], 2),
            "rmse": round(metrics['rmse'], 2),
            "correlation": round(metrics['correlation'], 3)
        },
        "current_gw": current_gw,
        "available_prediction_gws": available,
        "note": f"Analysis based on GW{gw_to_analyze} predictions vs actual event_points data."
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


def apply_weight_adjustments(new_weights: Dict) -> bool:
    """Apply new weights to config.py."""
    try:
        with open('config.py', 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Find PREDICTION_WEIGHTS section
        start = content.find('PREDICTION_WEIGHTS = {')
        if start == -1:
            return False
        
        end = content.find('}', start) + 1
        
        # Build new weights string
        weights_str = "PREDICTION_WEIGHTS = {\n"
        for key, value in new_weights.items():
            comment = f"  # {key.replace('_', ' ').title()}"
            weights_str += f'    "{key}": {value},{comment}\n'
        weights_str += "}"
        
        # Replace
        new_content = content[:start] + weights_str + content[end:]
        
        with open('config.py', 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        return True
    except Exception as e:
        print(f"Error applying weights: {e}")
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
