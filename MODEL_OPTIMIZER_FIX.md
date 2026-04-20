# Model Optimizer Fix Summary

## Issues Fixed

### 1. UTF-8 Encoding Error ❌→✅
**Problem**: `UnicodeDecodeError: 'charmap' codec can't decode byte 0x90`

**Cause**: JSON files contain UTF-8 characters (player names like "Guéhi") but Python on Windows defaults to cp1252

**Fix**: Added `encoding='utf-8'` to all `open()` calls in `model_optimizer.py`

---

### 2. Wrong Data Structure Keys ❌→✅
**Problem**: `KeyError: 'players'` and player ID always None

**Cause**: Used incorrect key names based on assumptions

**Actual Structure**:
```json
{
  "predictions": [  // NOT 'players'
    {
      "player_id": 260,  // NOT 'id'
      "predicted_points": 15.19,  // NOT 'xPts'
      "position_id": 2,  // NOT 'position' (numeric, not string)
      ...
    }
  ]
}
```

**Fix**: Updated all references to use correct keys

---

### 3. FPL API Timing Logic ❌→✅
**Problem**: "No data available for recent gameweeks"

**Cause**: Misunderstood FPL API behavior
- `get_current_gameweek()` returns 33 (NEXT upcoming GW)
- `event_points` contains GW32 data (PREVIOUS GW's actual points)
- Was trying to analyze GW30-32 but only had GW33 predictions

**Fix**: Analyze `GW(current-1)` predictions vs `event_points` data

---

## Results After Fix ✅

**Test Run**: `python model_optimizer.py analyze`

```json
{
  "gw": 32,
  "total_analyzed": 290,
  "mae": 2.51,      // Mean Absolute Error - Good!
  "rmse": 3.56,     // Root Mean Squared Error
  "correlation": 0.166,  // Needs improvement
  "avg_error": -0.09,
  "performance_grade": "B (Good)"
}
```

**Weight Suggestions Generated**:
- Recommended increasing `fixture_difficulty` weight (+0.021)
- Recommended increasing `ict_index` weight (+0.014)
- Recommended reducing `form` weight (-0.01)

---

## Permission Check ✅

**Question**: Is it normal that admin accounts can see xPts and premium features?

**Answer**: **YES - This is correct by design**

**Code Evidence** (`server.py` line 223):
```python
is_premium = user and user.get("plan") in ("premium", "admin")
```

**Hierarchy**: Admin > Premium > Free

Admin accounts have all premium features, which is the expected behavior for administrative access.

---

## Files Changed

1. `model_optimizer.py` - Fixed encoding and data keys (73 insertions, 43 deletions)

## Git Status

- Commit: `77bf1ec` - "Fix model optimizer: UTF-8 encoding + correct data keys"
- Pushed to: `https://github.com/gonzalloe/FPL-GW-Planner.git`
- Status: ✅ Deployed

---

## How to Use

1. Go to Admin Dashboard
2. Click "Analyze Performance" in Model Optimization panel
3. Review metrics (MAE, RMSE, Correlation)
4. Click "Apply Suggested Weights" if satisfied
5. Restart server for changes to take effect

**Note**: Requires prediction data for GW(current-1) to match event_points availability.
