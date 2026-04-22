"""
FPL Predictor - Rule Reviewer Engine
-------------------------------------
Admin-triggered: fetch FPL `bootstrap-static` and compare structural game
rules against a baseline stored in app_storage.

Diff safety levels:
    safe   -> can be auto-applied (squad size/budget/chip counts/etc.)
    review -> flagged for manual review only, never auto-applied
              (scoring-related fields live on the HTML help page, which
               we refuse to parse.)

When admin confirms, an "overrides" blob is stored in app_storage and
re-applied on every boot AND hot-swapped into the running process, so
values survive Render redeploys without editing source code.

All rule changes are audit-logged. Last N snapshots kept for rollback.

Security posture:
  - Never trust the FPL API response blindly; validate every field.
  - Never load the override blob into config without re-validation.
  - Never auto-apply to scoring values.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Tuple

from app_storage import get_setting, set_setting

# -- Storage keys --
K_BASELINE = "fpl_rules_baseline"
K_OVERRIDES = "fpl_rules_overrides"
K_HISTORY = "fpl_rules_history"
MAX_HISTORY = 20
MAX_SNAPSHOTS = 5


RULE_SPECS: List[Dict[str, Any]] = [
    {
        "id": "squad_budget",
        "config_key": "SQUAD_BUDGET",
        "kind": "primitive",
        "safety": "safe",
        "label": "Starting squad budget (tenths of GBP m)",
        "validator": lambda v: (isinstance(v, int) and 500 <= v <= 2000,
                                "must be int 500..2000 tenths"),
    },
    {
        "id": "squad_size",
        "config_key": "SQUAD_SIZE",
        "kind": "primitive",
        "safety": "safe",
        "label": "Squad size",
        "validator": lambda v: (isinstance(v, int) and 10 <= v <= 20,
                                "must be int 10..20"),
    },
    {
        "id": "starting_xi",
        "config_key": "STARTING_XI",
        "kind": "primitive",
        "safety": "safe",
        "label": "Starting XI size",
        "validator": lambda v: (isinstance(v, int) and 9 <= v <= 13,
                                "must be int 9..13"),
    },
    {
        "id": "max_per_team",
        "config_key": "MAX_PER_TEAM",
        "kind": "primitive",
        "safety": "safe",
        "label": "Max players from a single Premier League team",
        "validator": lambda v: (isinstance(v, int) and 2 <= v <= 5,
                                "must be int 2..5"),
    },
    {
        "id": "chips",
        "config_key": "CHIPS",
        "kind": "dict",
        "safety": "safe",
        "label": "Chips available this season (name + count)",
        "validator": None,
    },
    {
        "id": "position_limits",
        "config_key": "POSITION_LIMITS",
        "kind": "dict",
        "safety": "safe",
        "label": "Squad composition per position (min/max/play)",
        "validator": None,
    },
]


# ---------------- Public API ----------------

def collect_current_rules() -> Dict[str, Any]:
    """Pull current structural rules from FPL bootstrap-static.

    Sanity-checks the payload looks like real FPL data before using it
    (defensive against captive-portal / proxy poisoning).
    """
    from data_fetcher import fetch_bootstrap
    bs = fetch_bootstrap()
    if not isinstance(bs, dict):
        raise ValueError("bootstrap-static did not return a JSON object")
    must_have = {"elements", "teams", "events", "element_types", "game_settings"}
    if len(must_have & set(bs.keys())) < 4:
        raise ValueError(
            "bootstrap-static payload does not look like FPL data "
            "(missing expected keys) - refusing to use it"
        )

    gs = bs.get("game_settings", {}) or {}
    etypes = bs.get("element_types", []) or []

    rules: Dict[str, Any] = {
        "squad_size": gs.get("squad_squadsize"),
        "starting_xi": gs.get("squad_squadplay"),
        "max_per_team": gs.get("squad_team_limit"),
    }

    budget = gs.get("squad_total_spend")
    if not isinstance(budget, int):
        budget = 1000
    rules["squad_budget"] = int(budget)

    pos_limits: Dict[int, Dict[str, Any]] = {}
    for et in etypes:
        etid = et.get("id")
        if not isinstance(etid, int):
            continue
        name = et.get("singular_name_short") or et.get("singular_name") or str(etid)
        sel = et.get("squad_select")
        play_min = et.get("squad_min_play")
        play_max = et.get("squad_max_play")
        pos_limits[etid] = {
            "name": name,
            "squad_min": int(sel) if isinstance(sel, int) else None,
            "squad_max": int(sel) if isinstance(sel, int) else None,
            "play_min": int(play_min) if isinstance(play_min, int) else None,
            "play_max": int(play_max) if isinstance(play_max, int) else None,
        }
    rules["position_limits"] = pos_limits

    chips_raw = bs.get("chips", []) or []
    chips_norm: Dict[str, Dict[str, Any]] = {}
    for c in chips_raw:
        if not isinstance(c, dict):
            continue
        name = (c.get("name") or "").strip().lower()
        if not name:
            continue
        chips_norm[name] = {
            "name": c.get("name"),
            "number": c.get("number"),
            "start_event": c.get("start_event"),
            "stop_event": c.get("stop_event"),
            "chip_type": c.get("chip_type"),
        }
    rules["chips"] = chips_norm

    rules["_meta"] = {"fetched_at": datetime.utcnow().isoformat() + "Z"}
    return rules


def get_baseline() -> Dict[str, Any]:
    return get_setting(K_BASELINE, {}) or {}


def get_overrides() -> Dict[str, Any]:
    return get_setting(K_OVERRIDES, {}) or {}


def diff_rules(current: Dict[str, Any], baseline: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not baseline:
        return [{
            "id": spec["id"],
            "label": spec["label"],
            "safety": spec["safety"],
            "current": current.get(spec["id"]),
            "baseline": None,
            "changed": True,
            "auto_apply": spec["safety"] == "safe",
            "reason": "First-run baseline - accept to establish the reference snapshot.",
        } for spec in RULE_SPECS]

    out = []
    for spec in RULE_SPECS:
        rid = spec["id"]
        cur_v = current.get(rid)
        base_v = baseline.get(rid)
        changed = _normalise(cur_v) != _normalise(base_v)
        ok, reason = _validate_rule(spec, cur_v)
        out.append({
            "id": rid,
            "label": spec["label"],
            "safety": spec["safety"],
            "current": cur_v,
            "baseline": base_v,
            "changed": changed,
            "auto_apply": bool(changed and spec["safety"] == "safe" and ok),
            "reason": (reason if not ok
                       else ("Safe to auto-apply." if spec["safety"] == "safe"
                             else "Flagged for manual review - verify on FPL help page before applying.")),
        })
    return out


def review() -> Dict[str, Any]:
    try:
        cur = collect_current_rules()
    except Exception as e:
        return {"ok": False, "error": f"Could not fetch FPL rules: {e}"}
    base = get_baseline()
    return {
        "ok": True,
        "current": cur,
        "baseline": base or None,
        "first_run": not base,
        "diff": diff_rules(cur, base),
        "fetched_at": cur.get("_meta", {}).get("fetched_at"),
    }


def apply(admin_email: str,
          accepted_ids: List[str],
          current_snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Persist the accepted subset of rule changes.

    Re-validates every accepted rule; refuses `review`-safety rules.
    """
    if not isinstance(current_snapshot, dict):
        return {"ok": False, "error": "Invalid snapshot"}
    if not isinstance(accepted_ids, list) or not all(isinstance(x, str) for x in accepted_ids):
        return {"ok": False, "error": "accepted_ids must be a list of strings"}
    if len(accepted_ids) > 50:
        return {"ok": False, "error": "too many accepted_ids"}

    spec_by_id = {s["id"]: s for s in RULE_SPECS}
    overrides = get_overrides()
    applied: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []

    for rid in accepted_ids:
        spec = spec_by_id.get(rid)
        if not spec:
            rejected.append({"id": rid, "reason": "unknown rule id"})
            continue
        if spec["safety"] != "safe":
            rejected.append({"id": rid, "reason": "not auto-appliable; manual review required"})
            continue
        val = current_snapshot.get(rid)
        ok, reason = _validate_rule(spec, val)
        if not ok:
            rejected.append({"id": rid, "reason": f"validation failed: {reason}"})
            continue
        overrides[rid] = val
        applied.append({"id": rid, "value": val})

    set_setting(K_OVERRIDES, overrides)

    try:
        _reload_into_running_process(overrides)
    except Exception as e:
        rejected.append({"id": "_hot_reload", "reason": f"in-memory refresh failed: {e}"})

    _rotate_baseline(current_snapshot)

    _append_history({
        "action": "apply",
        "admin": admin_email,
        "applied": applied,
        "rejected": rejected,
        "at": datetime.utcnow().isoformat() + "Z",
    })

    return {"ok": True, "applied": applied, "rejected": rejected}


def rollback(admin_email: str) -> Dict[str, Any]:
    """Clear applied overrides - config.py defaults take over."""
    set_setting(K_OVERRIDES, {})
    try:
        _reload_into_running_process({})
    except Exception as e:
        _append_history({
            "action": "rollback_reload_failed",
            "admin": admin_email,
            "error": str(e),
            "at": datetime.utcnow().isoformat() + "Z",
        })
    _append_history({
        "action": "rollback",
        "admin": admin_email,
        "at": datetime.utcnow().isoformat() + "Z",
    })
    return {
        "ok": True,
        "message": "Overrides cleared. Running process reverted; restart workers to fully reset.",
    }


def get_history() -> List[Dict[str, Any]]:
    return get_setting(K_HISTORY, []) or []


# ---------------- Runtime integration ----------------

def apply_overrides_to_config() -> int:
    """Read stored override blob and mutate the `config` module in place.
    NEVER raises - a broken override must not crash the server.
    """
    try:
        overrides = get_setting(K_OVERRIDES, {}) or {}
        if not overrides:
            return 0
        import sys
        cfg = sys.modules.get("config")
        if cfg is None:
            try:
                import config as cfg  # noqa: F401
                cfg = sys.modules["config"]
            except Exception:
                return 0
        return _mutate_config_module(cfg, overrides)
    except Exception as e:
        print(f"  [FPL_RULES] apply_overrides_to_config skipped: {e}")
        return 0


# ---------------- Internals ----------------

def _normalise(v: Any) -> Any:
    if isinstance(v, dict):
        return tuple(sorted((str(k), _normalise(val)) for k, val in v.items()))
    if isinstance(v, list):
        return tuple(_normalise(x) for x in v)
    return v


def _validate_rule(spec: Dict[str, Any], value: Any) -> Tuple[bool, str]:
    if spec["safety"] == "safe" and value is None:
        return False, "value missing in bootstrap - cannot auto-apply"
    if spec["id"] == "chips":
        return _validate_chips(value)
    if spec["id"] == "position_limits":
        return _validate_position_limits(value)
    v = spec.get("validator")
    if v:
        try:
            ok, reason = v(value)
            return bool(ok), (str(reason) if not ok else "ok")
        except Exception as e:
            return False, f"validator crashed: {e}"
    return True, "ok"


def _validate_chips(value: Any) -> Tuple[bool, str]:
    if not isinstance(value, dict):
        return False, "chips must be a dict keyed by chip name"
    if len(value) > 20:
        return False, "unreasonable chip count (>20)"
    for k, v in value.items():
        if not isinstance(k, str) or not isinstance(v, dict):
            return False, "malformed chip entry"
        num = v.get("number")
        if num is not None and not (isinstance(num, int) and 0 <= num <= 10):
            return False, f"chip {k}: implausible number ({num})"
    return True, "ok"


def _validate_position_limits(value: Any) -> Tuple[bool, str]:
    if not isinstance(value, dict) or not value:
        return False, "position_limits must be a non-empty dict"
    for pos_id, row in value.items():
        if not isinstance(row, dict):
            return False, f"position {pos_id}: not a dict"
        for f in ("squad_min", "squad_max", "play_min", "play_max"):
            v = row.get(f)
            if v is not None and not (isinstance(v, int) and 0 <= v <= 15):
                return False, f"position {pos_id}.{f}: implausible value ({v})"
    return True, "ok"


def _mutate_config_module(cfg, overrides: Dict[str, Any]) -> int:
    """In-place update of config module attributes.

    Dict-typed attrs are mutated so `from config import CHIPS` still works.
    Primitive attrs are rebound on `config` AND propagated to consumer
    modules that did `from config import SQUAD_BUDGET` (otherwise rebinding
    is invisible to them).
    """
    applied = 0
    import sys

    primitive_consumers = [
        sys.modules.get("squad_optimizer"),
        sys.modules.get("chip_planner"),
    ]

    def _set_primitive(name: str, val: Any):
        setattr(cfg, name, val)
        for mod in primitive_consumers:
            if mod is not None and hasattr(mod, name):
                setattr(mod, name, val)

    spec_by_id = {s["id"]: s for s in RULE_SPECS}
    for rid, val in overrides.items():
        spec = spec_by_id.get(rid)
        if not spec:
            continue
        ok, _reason = _validate_rule(spec, val)
        if not ok:
            continue
        key = spec["config_key"]

        if spec["kind"] == "primitive":
            _set_primitive(key, val)
            applied += 1
        elif spec["kind"] == "dict":
            existing = getattr(cfg, key, None)
            if isinstance(existing, dict):
                if rid == "chips":
                    _merge_chips(existing, val)
                elif rid == "position_limits":
                    _merge_position_limits(existing, val)
                else:
                    existing.clear()
                    existing.update(val)
            else:
                setattr(cfg, key, val)
            applied += 1
    if applied:
        print(f"  [FPL_RULES] Applied {applied} rule override(s) to config module")
    return applied


def _merge_chips(existing: Dict[str, Any], new: Dict[str, Any]):
    """Merge FPL chip metadata into our hand-curated CHIPS dict."""
    name_map = {
        "wildcard": "wildcard",
        "freehit": "free_hit",
        "free_hit": "free_hit",
        "bboost": "bench_boost",
        "bench_boost": "bench_boost",
        "3xc": "triple_captain",
        "triple_captain": "triple_captain",
        "tc": "triple_captain",
    }
    for key, meta in new.items():
        norm = (key or "").lower().replace(" ", "_")
        cfg_key = name_map.get(norm, norm)
        entry = existing.setdefault(cfg_key, {
            "name": meta.get("name", cfg_key),
            "description": "",
            "best_when": [],
        })
        entry["fpl_number"] = meta.get("number")
        entry["fpl_start_event"] = meta.get("start_event")
        entry["fpl_stop_event"] = meta.get("stop_event")


def _merge_position_limits(existing: Dict[Any, Any], new: Dict[Any, Any]):
    for k, row in new.items():
        try:
            pos_id = int(k)
        except (TypeError, ValueError):
            continue
        cur = existing.setdefault(pos_id, {})
        for f in ("name", "squad_min", "squad_max", "play_min", "play_max"):
            if row.get(f) is not None:
                cur[f] = row[f]


def _reload_into_running_process(overrides: Dict[str, Any]):
    import sys
    cfg = sys.modules.get("config")
    if cfg is None:
        import config as cfg  # noqa: F401
        cfg = sys.modules["config"]
    _mutate_config_module(cfg, overrides)


def _rotate_baseline(new_baseline: Dict[str, Any]):
    old = get_setting(K_BASELINE, None)
    if old:
        hist_key = "fpl_rules_baseline_history"
        hist = get_setting(hist_key, []) or []
        hist.append({"snapshot": old, "rotated_at": datetime.utcnow().isoformat() + "Z"})
        hist = hist[-MAX_SNAPSHOTS:]
        set_setting(hist_key, hist)
    set_setting(K_BASELINE, new_baseline)


def _append_history(entry: Dict[str, Any]):
    hist = get_setting(K_HISTORY, []) or []
    hist.append(entry)
    hist = hist[-MAX_HISTORY:]
    set_setting(K_HISTORY, hist)
