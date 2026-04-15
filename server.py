"""
FPL Predictor - Web Server (v4)
Serves dashboard with DGW-aware predictions, chip strategy, AI chat, and API endpoints.
"""
import json
import http.server
import socketserver
import webbrowser
import sys
import os
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).parent))

PORT = 8888
BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
SETTINGS_FILE = BASE_DIR / "user_settings.json"


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
        "predictions": predictions[:100],
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
    print(f"\n{'='*50}")
    print(f"  FPL Predictor Server v4 (AI Chat)")
    print(f"{'='*50}")
    print(f"\n  Dashboard:      http://localhost:{port}")
    print(f"  API:            http://localhost:{port}/api/predictions")
    print(f"  Run:            http://localhost:{port}/api/run")
    print(f"  My Team:        http://localhost:{port}/api/my-team?id=YOUR_ID")
    print(f"  News:           http://localhost:{port}/api/news")
    print(f"  Chip Analysis:  http://localhost:{port}/api/chip-analysis")
    print(f"  AI Chat:        POST http://localhost:{port}/api/chat")
    print(f"\n  Press Ctrl+C to stop\n")

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", port), FPLHandler) as httpd:
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
