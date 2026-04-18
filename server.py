"""
FPL Predictor - Web Server (v7 — Flask)
Production-ready server for Render deployment.
"""
import json
import sys
import os
import time
import threading
from pathlib import Path
from datetime import datetime

# Force unbuffered output for Render/Docker
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

sys.path.insert(0, str(Path(__file__).parent))

from flask import Flask, request, jsonify, send_from_directory

PORT = int(os.environ.get("PORT", 8888))
BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
SETTINGS_FILE = BASE_DIR / "user_settings.json"
REFRESH_INTERVAL = 2 * 3600  # 2 hours
_last_refresh = 0
_refresh_lock = threading.Lock()

app = Flask(__name__, static_folder=str(BASE_DIR), static_url_path="")


# ── Utilities ──

def _load_settings():
    if SETTINGS_FILE.exists():
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    return {}

def _save_settings(data):
    SETTINGS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def _run_predictions(gw=None):
    from prediction_engine import PredictionEngine
    from squad_optimizer import SquadOptimizer, ChipAdvisor

    engine = PredictionEngine()
    target_gw = gw or engine.next_gw
    gw_info = engine.get_gw_info(target_gw)
    predictions = engine.predict_all(target_gw)
    optimizer = SquadOptimizer(predictions)
    squad = optimizer.optimize_squad()
    chip_advisor = ChipAdvisor(predictions, gw_info)
    chip_analysis = chip_advisor.analyze()
    bb_squad = optimizer.optimize_squad(chip="bench_boost")

    output = {
        "generated_at": datetime.now().isoformat(),
        "gameweek": target_gw,
        "gw_info": gw_info,
        "predictions": predictions,
        "squad": squad,
        "bb_squad": bb_squad,
        "chip_analysis": chip_analysis,
        "top_picks": predictions[:30],
        "differentials": [
            p for p in predictions
            if float(p.get("selected_by_percent", 0)) < 10
            and p.get("starter_quality", {}).get("tier") in ("nailed", "regular")
        ][:15],
        "value_picks": sorted(
            [p for p in predictions
             if p.get("price", 99) <= 6.5
             and p.get("starter_quality", {}).get("tier") in ("nailed", "regular")],
            key=lambda x: x["predicted_points"] / max(x.get("price", 4), 3.5),
            reverse=True,
        )[:15],
    }

    OUTPUT_DIR.mkdir(exist_ok=True)
    filename = OUTPUT_DIR / f"gw{target_gw}_predictions.json"
    filename.write_text(json.dumps(output, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return output


def _refresh_data():
    global _last_refresh
    with _refresh_lock:
        try:
            cache_dir = BASE_DIR / "cache"
            if cache_dir.exists():
                for f in cache_dir.glob("*.json"):
                    try: f.unlink()
                    except: pass
            print(f"  [REFRESH] {datetime.now().strftime('%H:%M:%S')} — Clearing cache and re-fetching data...")
            _run_predictions()
            _last_refresh = time.time()
            print(f"  [REFRESH] {datetime.now().strftime('%H:%M:%S')} — Data refreshed successfully.")
        except Exception as e:
            print(f"  [REFRESH] ERROR: {e}")

def _auto_refresh_loop():
    global _last_refresh
    while True:
        try:
            if time.time() - _last_refresh >= REFRESH_INTERVAL:
                _refresh_data()
            time.sleep(60)
        except Exception as e:
            print(f"  [AUTO-REFRESH] Error: {e}")
            time.sleep(300)


# ── Auth helpers ──

def _get_auth_user():
    from auth import get_user_from_token
    auth = request.headers.get("Authorization", "")
    token = auth.replace("Bearer ", "") if auth.startswith("Bearer ") else ""
    return get_user_from_token(token)

def _nocache(resp):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


# ── API Routes ──

@app.after_request
def after_request(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    from auth import login
    data = request.get_json(silent=True) or {}
    print(f"  [AUTH] Login attempt: {data.get('email', '?')}")
    result = login(data.get("email", ""), data.get("password", ""))
    print(f"  [AUTH] Login result: ok={result.get('ok', False)}")
    return jsonify(result), 200 if result.get("ok") else 401


@app.route("/api/auth/register", methods=["POST"])
def api_auth_register():
    from auth import register
    data = request.get_json(silent=True) or {}
    result = register(data.get("email", ""), data.get("password", ""), data.get("name", ""))
    return jsonify(result), 200 if result.get("ok") else 400


@app.route("/api/auth/me", methods=["POST"])
def api_auth_me():
    user = _get_auth_user()
    if user:
        return jsonify({"ok": True, "user": user})
    return jsonify({"error": "Not authenticated"}), 401


@app.route("/api/predictions")
def api_predictions():
    files = sorted(OUTPUT_DIR.glob("gw*_predictions.json"), reverse=True)
    if not files:
        return jsonify({"error": "No predictions. Run first."}), 404
    data = json.loads(files[0].read_text(encoding="utf-8"))

    user = _get_auth_user()
    is_premium = user and user.get("plan") in ("premium", "admin")

    if not is_premium:
        import random
        data["user_plan"] = "free" if user else "guest"
        preds = data.get("predictions", [])
        random.shuffle(preds)
        data["predictions"] = preds
        for p in preds:
            p["predicted_points"] = "🔒"
            p["raw_xpts"] = "🔒"
            p["confidence"] = "🔒"
            p["team_last5_wr"] = "🔒"
            p["team_season_wr"] = "🔒"
            p["team_momentum"] = "🔒"
            p["team_injury_penalty"] = "🔒"
            p.pop("fixtures", None)
            p.pop("factors", None)
            p.pop("starter_quality", None)
        sq = data.get("squad", {})
        xi = sq.get("starting_xi", [])
        random.shuffle(xi)
        for p in xi:
            p["predicted_points"] = "🔒"
            p["confidence"] = "🔒"
            p.pop("fixtures", None)
        for p in sq.get("bench", []):
            p["predicted_points"] = "🔒"
            p.pop("fixtures", None)
        sq["predicted_total_points"] = "🔒"
        chip = data.get("chip_analysis", {})
        if chip.get("best_chip"):
            chip["best_chip"]["score"] = "🔒"
        for rec in chip.get("recommendations", []):
            rec["score"] = "🔒"
        for key in ("top_picks", "differentials", "value_picks"):
            for p in data.get(key, []):
                p["predicted_points"] = "🔒"
                p["raw_xpts"] = "🔒"
            random.shuffle(data.get(key, []))
    else:
        data["user_plan"] = "premium"

    return jsonify(data)


@app.route("/api/run")
def api_run():
    try:
        gw = request.args.get("gw", 0, type=int) or None
        data = _run_predictions(gw)
        return jsonify(data)
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/refresh")
def api_refresh():
    try:
        threading.Thread(target=_refresh_data, daemon=True).start()
        return jsonify({"ok": True, "message": "Refresh started in background"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/refresh-status")
def api_refresh_status():
    return jsonify({
        "last_refresh": datetime.fromtimestamp(_last_refresh).isoformat() if _last_refresh else None,
        "seconds_ago": int(time.time() - _last_refresh) if _last_refresh else None,
        "interval_hours": REFRESH_INTERVAL / 3600,
        "next_refresh_in": max(0, REFRESH_INTERVAL - (time.time() - _last_refresh)) if _last_refresh else 0,
    })


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "GET":
        return jsonify(_load_settings())
    data = request.get_json(silent=True) or {}
    try:
        settings = _load_settings()
        settings.update(data)
        _save_settings(settings)
        return jsonify({"ok": True, "settings": settings})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/chat", methods=["POST"])
def api_chat():
    try:
        data = request.get_json(silent=True) or {}
        question = data.get("question", "").strip()
        if not question:
            return jsonify({"error": "No question provided"}), 400

        from ai_chat import FPLChatEngine
        files = sorted(OUTPUT_DIR.glob("gw*_predictions.json"), reverse=True)
        if files:
            cached = json.loads(files[0].read_text(encoding="utf-8"))
        else:
            cached = _run_predictions()

        chat = FPLChatEngine(
            predictions=cached.get("predictions", []),
            squad=cached.get("squad", {}),
            gw_info=cached.get("gw_info", {}),
            chip_analysis=cached.get("chip_analysis", {}),
            bb_squad=cached.get("bb_squad", {}),
        )
        result = chat.answer(question)
        return jsonify(result)
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"answer": "Sorry, something went wrong.", "suggestions": ["Who should I captain?", "Best DGW players?"]}), 200


@app.route("/api/my-team")
def api_my_team():
    team_id = request.args.get("id") or _load_settings().get("team_id")
    if not team_id:
        return jsonify({"error": "No team ID"}), 400
    try:
        team_id = int(team_id)
        from my_team import fetch_my_team, enrich_my_team, generate_transfer_suggestions
        from prediction_engine import PredictionEngine

        settings = _load_settings()
        settings["team_id"] = team_id
        _save_settings(settings)

        team_data = fetch_my_team(team_id)
        if team_data.get("error"):
            return jsonify(team_data), 400

        engine = PredictionEngine()

        # Use cached predictions if available (much faster than predict_all)
        files = sorted(OUTPUT_DIR.glob("gw*_predictions.json"), reverse=True)
        if files:
            cached = json.loads(files[0].read_text(encoding="utf-8"))
            predictions = cached.get("predictions", [])
        else:
            predictions = engine.predict_all()

        enriched = enrich_my_team(team_data, engine.players, predictions)
        suggestions = generate_transfer_suggestions(enriched, predictions)
        enriched["transfer_suggestions"] = suggestions

        return jsonify({
            "team_id": team_id,
            "info": enriched.get("info", {}),
            "gw_summary": enriched.get("gw_summary", {}),
            "starters": enriched.get("starters", []),
            "bench": enriched.get("bench", []),
            "squad_value": enriched.get("squad_value", 0),
            "predicted_points": enriched.get("predicted_points", 0),
            "weakest_links": enriched.get("weakest_links", []),
            "transfer_suggestions": suggestions,
            "chips_used": enriched.get("chips", []),
            "active_chip": enriched.get("active_chip"),
            "recent_transfers": enriched.get("transfers", [])[:10],
            "history": enriched.get("history", [])[-10:],
        })
    except ValueError:
        return jsonify({"error": "Invalid team ID"}), 400
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/news")
def api_news():
    try:
        from news_aggregator import NewsAggregator
        return jsonify(NewsAggregator().get_news_summary())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/transfers")
def api_transfers():
    team_id = request.args.get("id") or _load_settings().get("team_id")
    if not team_id:
        return jsonify({"error": "No team ID"}), 400
    try:
        team_id = int(team_id)
        from my_team import fetch_my_team, enrich_my_team, generate_transfer_suggestions
        from prediction_engine import PredictionEngine
        team_data = fetch_my_team(team_id)
        engine = PredictionEngine()
        # Use cached predictions if available
        files = sorted(OUTPUT_DIR.glob("gw*_predictions.json"), reverse=True)
        if files:
            cached = json.loads(files[0].read_text(encoding="utf-8"))
            predictions = cached.get("predictions", [])
        else:
            predictions = engine.predict_all()
        enriched = enrich_my_team(team_data, engine.players, predictions)
        suggestions = generate_transfer_suggestions(enriched, predictions, free_transfers=2)
        return jsonify({"team_id": team_id, "suggestions": suggestions,
                        "bank": enriched.get("gw_summary", {}).get("bank", 0),
                        "squad_value": enriched.get("squad_value", 0)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/files")
def api_files():
    files = sorted(OUTPUT_DIR.glob("gw*_predictions.json"), reverse=True)
    return jsonify([{"name": f.name, "path": f"/output/{f.name}"} for f in files])


@app.route("/api/chip-analysis")
def api_chip_analysis():
    try:
        from prediction_engine import PredictionEngine
        from squad_optimizer import SquadOptimizer, ChipAdvisor
        engine = PredictionEngine()
        gw_info = engine.get_gw_info()
        predictions = engine.predict_all()
        settings = _load_settings()
        current_ids = None
        if settings.get("team_id"):
            try:
                from my_team import fetch_my_team
                team_data = fetch_my_team(settings["team_id"])
                if not team_data.get("error"):
                    current_ids = [p.get("element") for p in team_data.get("picks", [])]
            except: pass
        chip_advisor = ChipAdvisor(predictions, gw_info)
        analysis = chip_advisor.analyze(current_squad_ids=current_ids)
        optimizer = SquadOptimizer(predictions)
        normal_squad = optimizer.optimize_squad()
        bb_squad = optimizer.optimize_squad(chip="bench_boost")
        tc_squad = optimizer.optimize_squad(chip="triple_captain")
        return jsonify({
            "gw_info": gw_info, "chip_analysis": analysis,
            "squad_comparison": {
                "normal": {"predicted_total": normal_squad["predicted_total_points"], "formation": normal_squad["formation"],
                           "captain": normal_squad["captain"]["name"] if normal_squad["captain"] else None},
                "bench_boost": {"predicted_total": bb_squad["predicted_total_points"],
                                "bench_xp": sum(p["predicted_points"] for p in bb_squad["bench"]),
                                "extra_points": round(bb_squad["predicted_total_points"] - normal_squad["predicted_total_points"], 1)},
                "triple_captain": {"captain": tc_squad["captain"]["name"] if tc_squad["captain"] else None,
                                   "captain_xp": tc_squad["captain"]["predicted_points"] if tc_squad["captain"] else 0,
                                   "extra_points": round((tc_squad["captain"]["predicted_points"] if tc_squad["captain"] else 0), 1)},
            },
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/gw-planner")
def api_gw_planner():
    team_id = request.args.get("id") or _load_settings().get("team_id")
    if not team_id:
        return jsonify({"error": "No team ID. Import your team first."}), 400
    try:
        horizon = request.args.get("horizon", 5, type=int)
        from gw_planner import GWPlanner
        planner = GWPlanner(horizon=horizon)
        plan = planner.plan_from_team_id(int(team_id), horizon=horizon)
        if plan.get("error"):
            return jsonify(plan), 400
        return jsonify(plan)
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/fixture-ticker")
def api_fixture_ticker():
    try:
        from gw_planner import GWPlanner
        planner = GWPlanner(horizon=6)
        ticker = planner.build_fixture_ticker()
        return jsonify({"from_gw": planner.next_gw, "to_gw": planner.next_gw + planner.horizon - 1, "teams": ticker})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/fixture-rankings")
def api_fixture_rankings():
    try:
        num_gws = request.args.get("gws", 5, type=int)
        from gw_planner import GWPlanner
        planner = GWPlanner(horizon=num_gws)
        rankings = planner.rank_teams_by_fixtures(num_gws=num_gws)
        return jsonify({"from_gw": planner.next_gw, "num_gws": num_gws, "rankings": rankings})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/search-players")
def api_search_players():
    try:
        files = sorted(OUTPUT_DIR.glob("gw*_predictions.json"), reverse=True)
        if not files:
            return jsonify({"players": []})
        data = json.loads(files[0].read_text(encoding="utf-8"))
        predictions = data.get("predictions", [])
        query_lower = (request.args.get("q", "") or "").lower().strip()
        pos_filter = request.args.get("pos")
        max_price = request.args.get("max_price", type=float)
        results = []
        for p in predictions:
            if query_lower and not (query_lower in p.get("name", "").lower() or query_lower in p.get("full_name", "").lower() or
                                    query_lower in p.get("team", "").lower() or query_lower in p.get("team_name", "").lower()):
                continue
            if pos_filter and p.get("position") != pos_filter: continue
            if max_price and p.get("price", 99) > max_price: continue
            if p.get("predicted_points", 0) <= 0 and p.get("minutes", 0) == 0: continue
            results.append({k: p.get(k) for k in ["player_id","name","full_name","team","position","price","predicted_points",
                            "raw_xpts","form","is_dgw","num_fixtures","fixtures","starter_quality","availability",
                            "selected_by_percent","news","team_last5_form","team_season_wr"]})
            if len(results) >= 50: break
        return jsonify({"players": results, "total": len(results)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/squad-predictions")
def api_squad_predictions():
    try:
        gw = request.args.get("gw", 0, type=int)
        ids_str = request.args.get("ids", "")
        if not gw or not ids_str:
            return jsonify({"error": "Need ?gw=X&ids=1,2,3"}), 400
        player_ids = [int(x) for x in ids_str.split(",") if x.strip()]
        from prediction_engine import PredictionEngine
        engine = PredictionEngine()
        all_preds = engine.predict_all(gw)
        pred_map = {p["player_id"]: p for p in all_preds}
        results = []
        for pid in player_ids:
            pred = pred_map.get(pid)
            if pred:
                results.append({k: pred.get(k) for k in ["player_id","name","team","position","position_id","price",
                                "predicted_points","raw_xpts","form","is_dgw","num_fixtures","fixtures",
                                "starter_quality","availability","news","team_last5_form"]})
            else:
                results.append({"player_id": pid, "name": "?", "predicted_points": 0, "fixtures": []})
        return jsonify({"gameweek": gw, "predictions": results})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/simulate-transfer", methods=["POST"])
def api_simulate_transfer():
    try:
        data = request.get_json(silent=True) or {}
        squad_ids = data.get("squad_ids", [])
        out_id = data.get("out_id")
        in_id = data.get("in_id")
        target_gw = data.get("gw")
        if not squad_ids or not out_id or not in_id:
            return jsonify({"error": "Missing squad_ids, out_id, or in_id"}), 400

        files = sorted(OUTPUT_DIR.glob("gw*_predictions.json"), reverse=True)
        if files:
            cached = json.loads(files[0].read_text(encoding="utf-8"))
            pred_map = {p["player_id"]: p for p in cached.get("predictions", [])}
        else:
            from prediction_engine import PredictionEngine
            engine = PredictionEngine()
            pred_map = {p["player_id"]: p for p in engine.predict_all(target_gw)}

        out_pred = pred_map.get(out_id, {})
        in_pred = pred_map.get(in_id, {})
        current_preds = [pred_map.get(pid, {}) for pid in squad_ids]
        current_xi = sorted(current_preds, key=lambda x: x.get("predicted_points", 0), reverse=True)[:11]
        current_xpts = sum(p.get("predicted_points", 0) for p in current_xi)
        new_squad_ids = [pid for pid in squad_ids if pid != out_id] + [in_id]
        new_preds = [pred_map.get(pid, {}) for pid in new_squad_ids]
        new_xi = sorted(new_preds, key=lambda x: x.get("predicted_points", 0), reverse=True)[:11]
        new_xpts = sum(p.get("predicted_points", 0) for p in new_xi)

        from prediction_engine import PredictionEngine
        engine = PredictionEngine()
        gw = target_gw or engine.next_gw
        multi_gw = []
        for future_gw in range(gw, min(gw + 4, 39)):
            if future_gw == gw:
                in_f = in_pred.get("predicted_points", 0)
                out_f = out_pred.get("predicted_points", 0)
            else:
                in_f = engine.predict_player(in_id, future_gw).get("predicted_points", 0)
                out_f = engine.predict_player(out_id, future_gw).get("predicted_points", 0)
            multi_gw.append({"gw": future_gw, "in_xpts": round(in_f, 2), "out_xpts": round(out_f, 2), "gain": round(in_f - out_f, 2)})

        return jsonify({
            "gameweek": gw,
            "out_player": {"player_id": out_id, "name": out_pred.get("name","?"), "team": out_pred.get("team","?"),
                           "position": out_pred.get("position","?"), "price": out_pred.get("price",0),
                           "predicted_points": out_pred.get("predicted_points",0), "fixtures": out_pred.get("fixtures",[])},
            "in_player": {"player_id": in_id, "name": in_pred.get("name","?"), "team": in_pred.get("team","?"),
                          "position": in_pred.get("position","?"), "price": in_pred.get("price",0),
                          "predicted_points": in_pred.get("predicted_points",0), "fixtures": in_pred.get("fixtures",[]),
                          "is_dgw": in_pred.get("is_dgw",False), "starter_quality": in_pred.get("starter_quality",{}),
                          "form": in_pred.get("form",0)},
            "impact": {"this_gw_gain": round(in_pred.get("predicted_points",0) - out_pred.get("predicted_points",0), 2),
                       "xi_xpts_before": round(current_xpts, 1), "xi_xpts_after": round(new_xpts, 1),
                       "xi_gain": round(new_xpts - current_xpts, 1),
                       "price_delta": round(in_pred.get("price",0) - out_pred.get("price",0), 1)},
            "multi_gw": multi_gw, "total_multi_gw_gain": round(sum(g["gain"] for g in multi_gw), 2),
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/season-chips")
def api_season_chips():
    try:
        from chip_planner import SeasonChipPlanner
        settings = _load_settings()
        squad_ids = None
        chips_available = ["BB", "TC", "FH", "WC"]
        chips_used_list = []
        bank = 0.0
        HALF_CUTOFF = 20
        current_half = 2
        chip_name_map = {"bboost": "BB", "3xc": "TC", "freehit": "FH", "wildcard": "WC"}

        if settings.get("team_id"):
            try:
                from my_team import fetch_my_team
                team_data = fetch_my_team(settings["team_id"])
                if not team_data.get("error"):
                    picks = team_data.get("picks", [])
                    squad_ids = [p.get("element") for p in picks]
                    bank = team_data.get("gw_summary", {}).get("bank", 0)
                    raw_chips = team_data.get("chips", [])
                    active_chip = team_data.get("active_chip")
                    chips_used_list = raw_chips
                    active_code = chip_name_map.get(active_chip, "") if active_chip else ""
                    current_gw = team_data.get("gw_summary", {}).get("event", 33)
                    current_half = 2 if current_gw >= HALF_CUTOFF else 1
                    used_set = set()
                    for c in raw_chips:
                        name = c.get("name", "")
                        gw = c.get("event", 0)
                        code = chip_name_map.get(name, name.upper())
                        half = 2 if gw >= HALF_CUTOFF else 1
                        if gw == current_gw: continue
                        if active_chip and name == active_chip: continue
                        if half == current_half: used_set.add(code)
                    chips_available = [code for code in ["BB", "TC", "FH", "WC"] if code not in used_set]
            except: pass

        planner = SeasonChipPlanner()
        result = planner.analyze_season(chips_available=["BB", "TC", "FH", "WC"], current_squad_ids=squad_ids, bank=bank)
        result["user_chips_available"] = chips_available
        result["user_chips_used"] = [
            {"name": c.get("name"), "code": chip_name_map.get(c.get("name",""),"?"), "gw": c.get("event"),
             "half": 2 if c.get("event", 0) >= HALF_CUTOFF else 1}
            for c in chips_used_list
        ]
        result["current_half"] = current_half if settings.get("team_id") else 2
        result["half_cutoff"] = HALF_CUTOFF
        result["all_used"] = len(chips_available) == 0
        return jsonify(result)
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": "Internal server error"}), 500


# ── Stripe ──

@app.route("/api/stripe/create-checkout", methods=["POST"])
def api_stripe_checkout():
    user = _get_auth_user()
    if not user:
        return jsonify({"error": "Not authenticated"}), 401
    try:
        import stripe
        stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
        if not stripe.api_key:
            from auth import upgrade_to_premium
            result = upgrade_to_premium(user["email"])
            return jsonify({"ok": True, "message": "Upgraded (test mode)", "user": result.get("user")})
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price_data": {"currency": "usd", "product_data": {"name": "FPL Predictor Premium"},
                         "unit_amount": 250, "recurring": {"interval": "month"}}, "quantity": 1}],
            mode="subscription",
            success_url=request.headers.get("Origin", "http://localhost:8888") + "/?upgraded=1",
            cancel_url=request.headers.get("Origin", "http://localhost:8888") + "/?cancelled=1",
            client_reference_id=user["email"], customer_email=user["email"],
        )
        return jsonify({"ok": True, "checkout_url": session.url})
    except ImportError:
        from auth import upgrade_to_premium
        result = upgrade_to_premium(user["email"])
        return jsonify({"ok": True, "message": "Upgraded (test mode)", "user": result.get("user")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/stripe/webhook", methods=["POST"])
def api_stripe_webhook():
    try:
        import stripe
        stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
        webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
        if not webhook_secret:
            return jsonify({"error": "Webhook not configured"}), 403
        payload = request.get_data()
        sig = request.headers.get("Stripe-Signature", "")
        event = stripe.Webhook.construct_event(payload, sig, webhook_secret)
        if event.get("type") == "checkout.session.completed":
            session = event["data"]["object"]
            email = session.get("client_reference_id") or session.get("customer_email")
            if email:
                from auth import upgrade_to_premium
                upgrade_to_premium(email, stripe_customer_id=session.get("customer"),
                                   stripe_subscription_id=session.get("subscription"))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ── Admin ──

@app.route("/api/admin/users", methods=["POST"])
def api_admin_users():
    user = _get_auth_user()
    if not user or user.get("plan") != "admin":
        return jsonify({"error": "Admin access required"}), 403
    from auth import list_all_users
    return jsonify(list_all_users(user["email"]))


@app.route("/api/admin/set-plan", methods=["POST"])
def api_admin_set_plan():
    user = _get_auth_user()
    if not user or user.get("plan") != "admin":
        return jsonify({"error": "Admin access required"}), 403
    from auth import admin_set_plan
    data = request.get_json(silent=True) or {}
    return jsonify(admin_set_plan(user["email"], data.get("email",""), data.get("plan","free"), data.get("months",999)))


@app.route("/api/admin/delete-user", methods=["POST"])
def api_admin_delete_user():
    user = _get_auth_user()
    if not user or user.get("plan") != "admin":
        return jsonify({"error": "Admin access required"}), 403
    from auth import admin_delete_user
    data = request.get_json(silent=True) or {}
    return jsonify(admin_delete_user(user["email"], data.get("email","")))


# ── Setup (keep for manual use) ──

@app.route("/api/setup-accounts")
def api_setup_accounts():
    from auth import register, _load_users, _save_users
    from datetime import timedelta
    setup_key = os.environ.get("SETUP_KEY", "")
    provided_key = request.args.get("key", "")
    if not setup_key or provided_key != setup_key:
        return jsonify({"error": "Invalid or missing setup key"}), 403
    users = _load_users()
    if users:
        return jsonify({"error": "Accounts already exist."})
    _auto_setup_accounts()
    return jsonify({"ok": True, "message": "Accounts created"})


@app.route("/api/reset-accounts")
def api_reset_accounts():
    from auth import _save_users, _save_sessions
    setup_key = os.environ.get("SETUP_KEY", "")
    provided_key = request.args.get("key", "")
    if not setup_key or provided_key != setup_key:
        return jsonify({"error": "Invalid or missing setup key"}), 403
    _save_users({})
    _save_sessions({})
    _auto_setup_accounts()
    return jsonify({"ok": True, "message": "Accounts reset and recreated"})


# ── Static files ──

@app.route("/")
def index():
    resp = send_from_directory(str(BASE_DIR), "dashboard.html")
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.route("/<path:filename>")
def static_files(filename):
    resp = send_from_directory(str(BASE_DIR), filename)
    if filename.endswith((".html", ".js")):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
    return resp


# ── Startup ──

def _auto_setup_accounts():
    from auth import register, _load_users, _save_users
    from datetime import timedelta
    admin_email = os.environ.get("ADMIN_EMAIL", "")
    admin_pass = os.environ.get("ADMIN_PASSWORD", "")
    cc_email = os.environ.get("CC_EMAIL", "")
    cc_pass = os.environ.get("CC_PASSWORD", "")
    cc2_email = os.environ.get("CC2_EMAIL", "")
    cc2_pass = os.environ.get("CC2_PASSWORD", "")
    if not admin_email or not admin_pass:
        return
    users = _load_users()
    if admin_email in users:
        return
    print("  [SETUP] Creating initial accounts...")
    far = (datetime.now() + timedelta(days=365 * 99)).isoformat()
    register(admin_email, admin_pass, "Admin")
    if cc_email and cc_pass: register(cc_email, cc_pass, "CC")
    if cc2_email and cc2_pass: register(cc2_email, cc2_pass, "CC Alt")
    users = _load_users()
    if admin_email in users:
        users[admin_email]["plan"] = "admin"
        users[admin_email]["plan_expires"] = far
    if cc_email and cc_email in users:
        users[cc_email]["plan"] = "premium"
        users[cc_email]["plan_expires"] = far
    if cc2_email and cc2_email in users:
        users[cc2_email]["plan"] = "premium"
        users[cc2_email]["plan_expires"] = far
    _save_users(users)
    print(f"  [SETUP] ✅ Accounts created: admin={admin_email}, cc={cc_email}, cc2={cc2_email}")


def _startup():
    """Run startup tasks — called once before first request."""
    global _last_refresh

    print(f"\n{'='*55}")
    print(f"  FPL Predictor Server v7")
    print(f"{'='*55}")

    try:
        _auto_setup_accounts()
    except Exception as e:
        print(f"  [SETUP] ⚠️ Auto-setup failed: {e}")

    files = sorted(OUTPUT_DIR.glob("gw*_predictions.json"), reverse=True)
    if files:
        _last_refresh = files[0].stat().st_mtime
        print(f"  [INFO] Using cached predictions from {datetime.fromtimestamp(_last_refresh).strftime('%Y-%m-%d %H:%M')}")
    else:
        _last_refresh = time.time() - REFRESH_INTERVAL + 60
        print(f"  [INFO] No cached predictions — auto-refresh in ~60s")

    refresh_thread = threading.Thread(target=_auto_refresh_loop, daemon=True)
    refresh_thread.start()
    print(f"  [INFO] Auto-refresh thread started (every {REFRESH_INTERVAL//3600}h)")


# Run startup on import (works with both gunicorn and direct python)
_startup()


def main():
    """Direct execution entry point (local dev or Render without gunicorn)."""
    print(f"  [INFO] Starting Flask dev server on port {PORT}...")
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()
