"""
FPL Predictor - Web Server (v6)
Auto-refresh, transfer simulator, merged My Team + GW Planner.
"""
import json
import http.server
import socketserver
import webbrowser
import sys
import os
import time
import threading
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

PORT = 8888
BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
SETTINGS_FILE = BASE_DIR / "user_settings.json"
REFRESH_INTERVAL = 2 * 3600  # 2 hours in seconds
_last_refresh = 0
_refresh_lock = threading.Lock()


def _load_settings():
    if SETTINGS_FILE.exists():
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    return {}


def _save_settings(data):
    SETTINGS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _run_predictions(gw=None):
    """Run prediction engine and return full output dict."""
    from prediction_engine import PredictionEngine
    from squad_optimizer import SquadOptimizer, ChipAdvisor
    from datetime import datetime

    engine = PredictionEngine()
    target_gw = gw or engine.next_gw

    gw_info = engine.get_gw_info(target_gw)
    predictions = engine.predict_all(target_gw)

    optimizer = SquadOptimizer(predictions)
    squad = optimizer.optimize_squad()

    # Chip analysis
    chip_advisor = ChipAdvisor(predictions, gw_info)
    chip_analysis = chip_advisor.analyze()

    # BB squad analysis
    bb_squad = optimizer.optimize_squad(chip="bench_boost")

    output = {
        "generated_at": datetime.now().isoformat(),
        "gameweek": target_gw,
        "gw_info": gw_info,
        "predictions": predictions,  # ALL players — needed for full team search & transfer sim
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

    # Save to file
    OUTPUT_DIR.mkdir(exist_ok=True)
    filename = OUTPUT_DIR / f"gw{target_gw}_predictions.json"
    filename.write_text(json.dumps(output, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    return output


def _refresh_data():
    """Refresh all cached data: clear cache, re-fetch from FPL API, re-run predictions."""
    global _last_refresh
    with _refresh_lock:
        try:
            import shutil
            cache_dir = BASE_DIR / "cache"
            if cache_dir.exists():
                for f in cache_dir.glob("*.json"):
                    try:
                        f.unlink()
                    except Exception:
                        pass
            print(f"  [REFRESH] {datetime.now().strftime('%H:%M:%S')} — Clearing cache and re-fetching data...")
            _run_predictions()
            _last_refresh = time.time()
            print(f"  [REFRESH] {datetime.now().strftime('%H:%M:%S')} — Data refreshed successfully.")
        except Exception as e:
            print(f"  [REFRESH] ERROR: {e}")


def _auto_refresh_loop():
    """Background thread: refreshes data every REFRESH_INTERVAL seconds."""
    global _last_refresh
    while True:
        try:
            elapsed = time.time() - _last_refresh
            if elapsed >= REFRESH_INTERVAL:
                _refresh_data()
            time.sleep(60)  # Check every minute
        except Exception as e:
            print(f"  [AUTO-REFRESH] Error: {e}")
            time.sleep(300)


class FPLHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/api/predictions":
            self._serve_latest_predictions()
            return
        if path == "/api/run":
            gw = int(params.get("gw", [0])[0]) or None
            self._run_and_serve(gw)
            return
        if path == "/api/files":
            self._list_files()
            return
        if path == "/api/my-team":
            team_id = params.get("id", [None])[0]
            self._serve_my_team(team_id)
            return
        if path == "/api/news":
            self._serve_news()
            return
        if path == "/api/transfers":
            team_id = params.get("id", [None])[0]
            self._serve_transfers(team_id)
            return
        if path == "/api/settings":
            self._serve_settings()
            return
        if path == "/api/chip-analysis":
            self._serve_chip_analysis()
            return
        if path == "/api/gw-planner":
            team_id = params.get("id", [None])[0]
            horizon = int(params.get("horizon", [5])[0])
            self._serve_gw_planner(team_id, horizon)
            return
        if path == "/api/fixture-ticker":
            self._serve_fixture_ticker()
            return
        if path == "/api/fixture-rankings":
            num_gws = int(params.get("gws", [5])[0])
            self._serve_fixture_rankings(num_gws)
            return
        if path == "/api/refresh":
            self._manual_refresh()
            return
        if path == "/api/refresh-status":
            self._json_response({
                "last_refresh": datetime.fromtimestamp(_last_refresh).isoformat() if _last_refresh else None,
                "seconds_ago": int(time.time() - _last_refresh) if _last_refresh else None,
                "interval_hours": REFRESH_INTERVAL / 3600,
                "next_refresh_in": max(0, REFRESH_INTERVAL - (time.time() - _last_refresh)) if _last_refresh else 0,
            })
            return
        if path == "/api/search-players":
            query = params.get("q", [""])[0]
            pos = params.get("pos", [None])[0]
            max_price = params.get("max_price", [None])[0]
            self._search_players(query, pos, float(max_price) if max_price else None)
            return
        if path == "/api/season-chips":
            self._serve_season_chips()
            return

        if path == "/" or path == "":
            self.path = "/dashboard.html"
        super().do_GET()

    def _handle_chat_post(self):
        """Handle AI chat question."""
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body)
            question = data.get("question", "").strip()
            if not question:
                self._json_response({"error": "No question provided"}, 400)
                return

            from prediction_engine import PredictionEngine
            from squad_optimizer import SquadOptimizer, ChipAdvisor
            from ai_chat import FPLChatEngine

            # Load latest predictions
            files = sorted(OUTPUT_DIR.glob("gw*_predictions.json"), reverse=True)
            if files:
                cached = json.loads(files[0].read_text(encoding="utf-8"))
            else:
                cached = _run_predictions()

            engine_data = {
                "predictions": cached.get("predictions", []),
                "squad": cached.get("squad", {}),
                "gw_info": cached.get("gw_info", {}),
                "chip_analysis": cached.get("chip_analysis", {}),
                "bb_squad": cached.get("bb_squad", {}),
            }

            chat = FPLChatEngine(
                predictions=engine_data["predictions"],
                squad=engine_data["squad"],
                gw_info=engine_data["gw_info"],
                chip_analysis=engine_data["chip_analysis"],
                bb_squad=engine_data["bb_squad"],
            )

            result = chat.answer(question)
            self._json_response(result)

        except Exception as e:
            import traceback
            self._json_response({
                "error": str(e),
                "trace": traceback.format_exc(),
                "answer": f"Sorry, something went wrong: {str(e)}",
                "suggestions": ["Who should I captain?", "Best DGW players?"]
            }, 200)  # Still 200 so the chat UI can display it

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/settings":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
                settings = _load_settings()
                settings.update(data)
                _save_settings(settings)
                self._json_response({"ok": True, "settings": settings})
            except Exception as e:
                self._json_response({"error": str(e)}, 400)
            return

        if path == "/api/chat":
            self._handle_chat_post()
            return

        if path == "/api/simulate-transfer":
            self._handle_simulate_transfer()
            return

        self._json_response({"error": "Not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _serve_latest_predictions(self):
        files = sorted(OUTPUT_DIR.glob("gw*_predictions.json"), reverse=True)
        if not files:
            self._json_response({"error": "No predictions. Run first."}, 404)
            return
        data = json.loads(files[0].read_text(encoding="utf-8"))
        self._json_response(data)

    def _run_and_serve(self, gw):
        try:
            data = _run_predictions(gw)
            self._json_response(data)
        except Exception as e:
            import traceback
            self._json_response({"error": str(e), "trace": traceback.format_exc()}, 500)

    def _list_files(self):
        files = sorted(OUTPUT_DIR.glob("gw*_predictions.json"), reverse=True)
        result = [{"name": f.name, "path": f"/output/{f.name}"} for f in files]
        self._json_response(result)

    def _serve_my_team(self, team_id):
        if not team_id:
            settings = _load_settings()
            team_id = settings.get("team_id")
        if not team_id:
            self._json_response({"error": "No team ID"}, 400)
            return
        try:
            team_id = int(team_id)
            from my_team import fetch_my_team, enrich_my_team, generate_transfer_suggestions
            from prediction_engine import PredictionEngine

            settings = _load_settings()
            settings["team_id"] = team_id
            _save_settings(settings)

            team_data = fetch_my_team(team_id)
            if team_data.get("error"):
                self._json_response(team_data, 400)
                return

            engine = PredictionEngine()
            predictions = engine.predict_all()
            enriched = enrich_my_team(team_data, engine.players, predictions)
            suggestions = generate_transfer_suggestions(enriched, predictions)
            enriched["transfer_suggestions"] = suggestions

            response = {
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
            }
            self._json_response(response)
        except ValueError:
            self._json_response({"error": "Invalid team ID"}, 400)
        except Exception as e:
            import traceback
            self._json_response({"error": str(e), "trace": traceback.format_exc()}, 500)

    def _serve_news(self):
        try:
            from news_aggregator import NewsAggregator
            aggregator = NewsAggregator()
            summary = aggregator.get_news_summary()
            self._json_response(summary)
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _serve_transfers(self, team_id):
        if not team_id:
            settings = _load_settings()
            team_id = settings.get("team_id")
        if not team_id:
            self._json_response({"error": "No team ID"}, 400)
            return
        try:
            team_id = int(team_id)
            from my_team import fetch_my_team, enrich_my_team, generate_transfer_suggestions
            from prediction_engine import PredictionEngine

            team_data = fetch_my_team(team_id)
            engine = PredictionEngine()
            predictions = engine.predict_all()
            enriched = enrich_my_team(team_data, engine.players, predictions)
            suggestions = generate_transfer_suggestions(enriched, predictions, free_transfers=2)

            self._json_response({
                "team_id": team_id,
                "suggestions": suggestions,
                "bank": enriched.get("gw_summary", {}).get("bank", 0),
                "squad_value": enriched.get("squad_value", 0),
            })
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _serve_chip_analysis(self):
        """Dedicated chip analysis endpoint."""
        try:
            from prediction_engine import PredictionEngine
            from squad_optimizer import SquadOptimizer, ChipAdvisor

            engine = PredictionEngine()
            gw_info = engine.get_gw_info()
            predictions = engine.predict_all()

            # Get current squad if available
            settings = _load_settings()
            current_ids = None
            if settings.get("team_id"):
                try:
                    from my_team import fetch_my_team
                    team_data = fetch_my_team(settings["team_id"])
                    if not team_data.get("error"):
                        picks = team_data.get("picks", [])
                        current_ids = [p.get("element") for p in picks]
                except Exception:
                    pass

            chip_advisor = ChipAdvisor(predictions, gw_info)
            analysis = chip_advisor.analyze(current_squad_ids=current_ids)

            # Also generate BB and TC squads for comparison
            optimizer = SquadOptimizer(predictions)
            normal_squad = optimizer.optimize_squad()
            bb_squad = optimizer.optimize_squad(chip="bench_boost")
            tc_squad = optimizer.optimize_squad(chip="triple_captain")

            self._json_response({
                "gw_info": gw_info,
                "chip_analysis": analysis,
                "squad_comparison": {
                    "normal": {
                        "predicted_total": normal_squad["predicted_total_points"],
                        "formation": normal_squad["formation"],
                        "captain": normal_squad["captain"]["name"] if normal_squad["captain"] else None,
                    },
                    "bench_boost": {
                        "predicted_total": bb_squad["predicted_total_points"],
                        "bench_xp": sum(p["predicted_points"] for p in bb_squad["bench"]),
                        "extra_points": round(bb_squad["predicted_total_points"] - normal_squad["predicted_total_points"], 1),
                    },
                    "triple_captain": {
                        "captain": tc_squad["captain"]["name"] if tc_squad["captain"] else None,
                        "captain_xp": tc_squad["captain"]["predicted_points"] if tc_squad["captain"] else 0,
                        "extra_points": round(
                            (tc_squad["captain"]["predicted_points"] if tc_squad["captain"] else 0), 1
                        ),
                    },
                },
            })
        except Exception as e:
            import traceback
            self._json_response({"error": str(e), "trace": traceback.format_exc()}, 500)

    def _serve_settings(self):
        self._json_response(_load_settings())

    def _serve_gw_planner(self, team_id, horizon):
        """Generate multi-GW transfer plan for a team."""
        if not team_id:
            settings = _load_settings()
            team_id = settings.get("team_id")
        if not team_id:
            self._json_response({"error": "No team ID. Import your team first."}, 400)
            return
        try:
            team_id = int(team_id)
            from gw_planner import GWPlanner
            planner = GWPlanner(horizon=horizon)
            plan = planner.plan_from_team_id(team_id, horizon=horizon)
            if plan.get("error"):
                self._json_response(plan, 400)
                return
            self._json_response(plan)
        except Exception as e:
            import traceback
            self._json_response({"error": str(e), "trace": traceback.format_exc()}, 500)

    def _serve_fixture_ticker(self):
        """Return fixture ticker for all 20 teams."""
        try:
            from gw_planner import GWPlanner
            planner = GWPlanner(horizon=6)
            ticker = planner.build_fixture_ticker()
            self._json_response({
                "from_gw": planner.next_gw,
                "to_gw": planner.next_gw + planner.horizon - 1,
                "teams": ticker,
            })
        except Exception as e:
            import traceback
            self._json_response({"error": str(e), "trace": traceback.format_exc()}, 500)

    def _serve_fixture_rankings(self, num_gws):
        """Rank teams by fixture difficulty over next N GWs."""
        try:
            from gw_planner import GWPlanner
            planner = GWPlanner(horizon=num_gws)
            rankings = planner.rank_teams_by_fixtures(num_gws=num_gws)
            self._json_response({
                "from_gw": planner.next_gw,
                "num_gws": num_gws,
                "rankings": rankings,
            })
        except Exception as e:
            import traceback
            self._json_response({"error": str(e), "trace": traceback.format_exc()}, 500)

    def _manual_refresh(self):
        """Trigger manual data refresh."""
        try:
            threading.Thread(target=_refresh_data, daemon=True).start()
            self._json_response({"ok": True, "message": "Refresh started in background"})
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _serve_season_chips(self):
        """Season-wide chip analysis — scan all remaining GWs."""
        try:
            from chip_planner import SeasonChipPlanner

            # Get user's chips and squad if available
            settings = _load_settings()
            squad_ids = None
            chips_available = ["BB", "TC", "FH", "WC"]
            bank = 0.0

            if settings.get("team_id"):
                try:
                    from my_team import fetch_my_team
                    team_data = fetch_my_team(settings["team_id"])
                    if not team_data.get("error"):
                        picks = team_data.get("picks", [])
                        squad_ids = [p.get("element") for p in picks]
                        bank = team_data.get("gw_summary", {}).get("bank", 0)
                        # Determine used chips
                        chips_used = {c.get("name") for c in team_data.get("chips", [])}
                        chip_map = {"bboost": "BB", "3xc": "TC", "freehit": "FH", "wildcard": "WC"}
                        chips_available = [code for name, code in chip_map.items() if name not in chips_used]
                except Exception:
                    pass

            planner = SeasonChipPlanner()
            result = planner.analyze_season(
                chips_available=chips_available,
                current_squad_ids=squad_ids,
                bank=bank,
            )
            self._json_response(result)
        except Exception as e:
            import traceback
            self._json_response({"error": str(e), "trace": traceback.format_exc()}, 500)

    def _search_players(self, query, pos_filter=None, max_price=None):
        """Search players for transfer simulator — returns matching players with predictions."""
        try:
            files = sorted(OUTPUT_DIR.glob("gw*_predictions.json"), reverse=True)
            if not files:
                self._json_response({"players": []})
                return
            data = json.loads(files[0].read_text(encoding="utf-8"))
            predictions = data.get("predictions", [])

            query_lower = query.lower().strip() if query else ""
            results = []
            for p in predictions:
                # Filter by search query (if provided)
                if query_lower and not (
                    query_lower in p.get("name", "").lower() or
                    query_lower in p.get("full_name", "").lower() or
                    query_lower in p.get("team", "").lower() or
                    query_lower in p.get("team_name", "").lower()
                ):
                    continue
                # Filter by position
                if pos_filter and p.get("position") != pos_filter:
                    continue
                # Filter by price
                if max_price and p.get("price", 99) > max_price:
                    continue
                # Skip players with 0 xPts (completely inactive)
                if p.get("predicted_points", 0) <= 0 and p.get("minutes", 0) == 0:
                    continue
                results.append({
                    "player_id": p["player_id"],
                    "name": p["name"],
                    "full_name": p.get("full_name", ""),
                    "team": p["team"],
                    "position": p["position"],
                    "price": p.get("price", 0),
                    "predicted_points": p.get("predicted_points", 0),
                    "raw_xpts": p.get("raw_xpts", 0),
                    "form": p.get("form", 0),
                    "is_dgw": p.get("is_dgw", False),
                    "num_fixtures": p.get("num_fixtures", 0),
                    "fixtures": p.get("fixtures", []),
                    "starter_quality": p.get("starter_quality", {}),
                    "availability": p.get("availability", {}),
                    "selected_by_percent": p.get("selected_by_percent", "0"),
                    "news": p.get("news", ""),
                    "team_last5_form": p.get("team_last5_form", ""),
                    "team_season_wr": p.get("team_season_wr", 0),
                })
                if len(results) >= 50:
                    break

            self._json_response({"players": results, "total": len(results)})
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_simulate_transfer(self):
        """Simulate a transfer: given current squad + proposed in/out, return impact analysis."""
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body)
            squad_ids = data.get("squad_ids", [])
            out_id = data.get("out_id")
            in_id = data.get("in_id")
            target_gw = data.get("gw")

            if not squad_ids or not out_id or not in_id:
                self._json_response({"error": "Missing squad_ids, out_id, or in_id"}, 400)
                return

            from prediction_engine import PredictionEngine
            engine = PredictionEngine()
            gw = target_gw or engine.next_gw

            # Get predictions for both players
            pred_map = {p["player_id"]: p for p in engine.predict_all(gw)}
            out_pred = pred_map.get(out_id, {})
            in_pred = pred_map.get(in_id, {})

            # Calculate current squad XI xPts
            current_preds = [pred_map.get(pid, {}) for pid in squad_ids]
            current_xi = sorted(current_preds, key=lambda x: x.get("predicted_points", 0), reverse=True)[:11]
            current_xpts = sum(p.get("predicted_points", 0) for p in current_xi)

            # Calculate post-transfer squad XI xPts
            new_squad_ids = [pid for pid in squad_ids if pid != out_id] + [in_id]
            new_preds = [pred_map.get(pid, {}) for pid in new_squad_ids]
            new_xi = sorted(new_preds, key=lambda x: x.get("predicted_points", 0), reverse=True)[:11]
            new_xpts = sum(p.get("predicted_points", 0) for p in new_xi)

            # Multi-GW impact (look 3 GWs ahead)
            multi_gw = []
            for future_gw in range(gw, min(gw + 4, 39)):
                future_preds = {p["player_id"]: p for p in engine.predict_all(future_gw)}
                in_future = future_preds.get(in_id, {}).get("predicted_points", 0)
                out_future = future_preds.get(out_id, {}).get("predicted_points", 0)
                multi_gw.append({
                    "gw": future_gw,
                    "in_xpts": round(in_future, 2),
                    "out_xpts": round(out_future, 2),
                    "gain": round(in_future - out_future, 2),
                })

            total_multi_gw_gain = sum(g["gain"] for g in multi_gw)

            self._json_response({
                "gameweek": gw,
                "out_player": {
                    "player_id": out_id,
                    "name": out_pred.get("name", "?"),
                    "team": out_pred.get("team", "?"),
                    "position": out_pred.get("position", "?"),
                    "price": out_pred.get("price", 0),
                    "predicted_points": out_pred.get("predicted_points", 0),
                    "fixtures": out_pred.get("fixtures", []),
                },
                "in_player": {
                    "player_id": in_id,
                    "name": in_pred.get("name", "?"),
                    "team": in_pred.get("team", "?"),
                    "position": in_pred.get("position", "?"),
                    "price": in_pred.get("price", 0),
                    "predicted_points": in_pred.get("predicted_points", 0),
                    "fixtures": in_pred.get("fixtures", []),
                    "is_dgw": in_pred.get("is_dgw", False),
                    "starter_quality": in_pred.get("starter_quality", {}),
                    "form": in_pred.get("form", 0),
                },
                "impact": {
                    "this_gw_gain": round(in_pred.get("predicted_points", 0) - out_pred.get("predicted_points", 0), 2),
                    "xi_xpts_before": round(current_xpts, 1),
                    "xi_xpts_after": round(new_xpts, 1),
                    "xi_gain": round(new_xpts - current_xpts, 1),
                    "price_delta": round(in_pred.get("price", 0) - out_pred.get("price", 0), 1),
                },
                "multi_gw": multi_gw,
                "total_multi_gw_gain": round(total_multi_gw_gain, 2),
            })
        except Exception as e:
            import traceback
            self._json_response({"error": str(e), "trace": traceback.format_exc()}, 500)

    def _json_response(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        if "/api/" in str(args[0]) if args else False:
            print(f"  API: {args[0]}")


def serve(port: int = PORT, open_browser: bool = True):
    global _last_refresh

    print(f"\n{'='*55}")
    print(f"  FPL Predictor Server v6 (Auto-Refresh + Simulator)")
    print(f"{'='*55}")
    print(f"\n  Dashboard:        http://localhost:{port}")
    print(f"  API:              http://localhost:{port}/api/predictions")
    print(f"  Simulate:         POST http://localhost:{port}/api/simulate-transfer")
    print(f"  Search Players:   http://localhost:{port}/api/search-players?q=haaland")
    print(f"  Refresh:          http://localhost:{port}/api/refresh")
    print(f"  Refresh Status:   http://localhost:{port}/api/refresh-status")
    print(f"  Auto-refresh:     Every {REFRESH_INTERVAL//3600} hours")
    print(f"  AI Chat:          POST http://localhost:{port}/api/chat")
    print(f"\n  Press Ctrl+C to stop\n")

    # Mark initial data as "fresh" if predictions exist
    files = sorted(OUTPUT_DIR.glob("gw*_predictions.json"), reverse=True)
    if files:
        _last_refresh = files[0].stat().st_mtime
        print(f"  [INFO] Using cached predictions from {datetime.fromtimestamp(_last_refresh).strftime('%Y-%m-%d %H:%M')}")
    else:
        print(f"  [INFO] No cached predictions — will generate on first request")

    # Start auto-refresh background thread
    refresh_thread = threading.Thread(target=_auto_refresh_loop, daemon=True)
    refresh_thread.start()
    print(f"  [INFO] Auto-refresh thread started (every {REFRESH_INTERVAL//3600}h)")

    socketserver.TCPServer.allow_reuse_address = True

    class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
        daemon_threads = True

    with ThreadedTCPServer(("", port), FPLHandler) as httpd:
        if open_browser:
            webbrowser.open(f"http://localhost:{port}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  Server stopped.")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    port = int(args[0]) if args else PORT
    serve(port, open_browser="--no-browser" not in sys.argv)
