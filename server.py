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

# Force unbuffered output for Render/Docker (prints show immediately in logs)
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

sys.path.insert(0, str(Path(__file__).parent))

PORT = int(os.environ.get("PORT", 8888))
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
        if path == "/api/squad-predictions":
            self._serve_squad_predictions(params)
            return
        if path == "/api/setup-accounts":
            self._setup_initial_accounts()
            return
        if path == "/api/reset-accounts":
            self._reset_accounts()
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
            traceback.print_exc()
            self._json_response({
                "answer": "Sorry, something went wrong. Please try again.",
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

        if path == "/api/auth/register":
            self._handle_auth_register()
            return

        if path == "/api/auth/login":
            self._handle_auth_login()
            return

        if path == "/api/auth/me":
            self._handle_auth_me()
            return

        if path == "/api/stripe/create-checkout":
            self._handle_stripe_checkout()
            return

        if path == "/api/stripe/webhook":
            self._handle_stripe_webhook()
            return

        if path == "/api/admin/users":
            self._handle_admin_users()
            return

        if path == "/api/admin/set-plan":
            self._handle_admin_set_plan()
            return

        if path == "/api/admin/delete-user":
            self._handle_admin_delete_user()
            return

        self._json_response({"error": "Not found"}, 404)

    def _read_post_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def _get_auth_user(self):
        """Extract user from Authorization header."""
        from auth import get_user_from_token
        auth = self.headers.get("Authorization", "")
        token = auth.replace("Bearer ", "") if auth.startswith("Bearer ") else ""
        return get_user_from_token(token)

    def _handle_auth_register(self):
        try:
            from auth import register
            data = self._read_post_body()
            result = register(data.get("email", ""), data.get("password", ""), data.get("name", ""))
            self._json_response(result, 200 if result.get("ok") else 400)
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_auth_login(self):
        try:
            from auth import login
            data = self._read_post_body()
            result = login(data.get("email", ""), data.get("password", ""))
            self._json_response(result, 200 if result.get("ok") else 401)
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_auth_me(self):
        user = self._get_auth_user()
        if user:
            self._json_response({"ok": True, "user": user})
        else:
            self._json_response({"error": "Not authenticated"}, 401)

    def _handle_stripe_checkout(self):
        """Create a Stripe Checkout session for premium subscription."""
        user = self._get_auth_user()
        if not user:
            self._json_response({"error": "Not authenticated"}, 401)
            return
        try:
            import stripe
            stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
            if not stripe.api_key:
                # No Stripe key — use manual upgrade for testing
                from auth import upgrade_to_premium
                result = upgrade_to_premium(user["email"])
                self._json_response({"ok": True, "message": "Upgraded (test mode)", "user": result.get("user")})
                return

            session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                line_items=[{
                    "price_data": {
                        "currency": "usd",
                        "product_data": {"name": "FPL Predictor Premium"},
                        "unit_amount": 250,  # $2.50 in cents
                        "recurring": {"interval": "month"},
                    },
                    "quantity": 1,
                }],
                mode="subscription",
                success_url=self.headers.get("Origin", "http://localhost:8888") + "/?upgraded=1",
                cancel_url=self.headers.get("Origin", "http://localhost:8888") + "/?cancelled=1",
                client_reference_id=user["email"],
                customer_email=user["email"],
            )
            self._json_response({"ok": True, "checkout_url": session.url})
        except ImportError:
            # Stripe not installed — manual upgrade for testing
            from auth import upgrade_to_premium
            result = upgrade_to_premium(user["email"])
            self._json_response({"ok": True, "message": "Upgraded (test mode — install stripe package for real payments)", "user": result.get("user")})
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_stripe_webhook(self):
        """Handle Stripe webhook for subscription events."""
        try:
            import stripe
            stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
            webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

            if not webhook_secret:
                # No webhook secret configured — reject all webhooks for security
                self._json_response({"error": "Webhook not configured"}, 403)
                return

            length = int(self.headers.get("Content-Length", 0))
            payload = self.rfile.read(length)
            sig = self.headers.get("Stripe-Signature", "")

            # Always verify signature when webhook secret is set
            event = stripe.Webhook.construct_event(payload, sig, webhook_secret)

            if event.get("type") == "checkout.session.completed":
                session = event["data"]["object"]
                email = session.get("client_reference_id") or session.get("customer_email")
                if email:
                    from auth import upgrade_to_premium
                    upgrade_to_premium(
                        email,
                        stripe_customer_id=session.get("customer"),
                        stripe_subscription_id=session.get("subscription"),
                    )

            self._json_response({"ok": True})
        except Exception as e:
            self._json_response({"error": str(e)}, 400)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def _handle_admin_users(self):
        user = self._get_auth_user()
        if not user or user.get("plan") != "admin":
            self._json_response({"error": "Admin access required"}, 403)
            return
        from auth import list_all_users
        result = list_all_users(user["email"])
        self._json_response(result)

    def _handle_admin_set_plan(self):
        user = self._get_auth_user()
        if not user or user.get("plan") != "admin":
            self._json_response({"error": "Admin access required"}, 403)
            return
        from auth import admin_set_plan
        data = self._read_post_body()
        result = admin_set_plan(user["email"], data.get("email", ""), data.get("plan", "free"), data.get("months", 999))
        self._json_response(result)

    def _handle_admin_delete_user(self):
        user = self._get_auth_user()
        if not user or user.get("plan") != "admin":
            self._json_response({"error": "Admin access required"}, 403)
            return
        from auth import admin_delete_user
        data = self._read_post_body()
        result = admin_delete_user(user["email"], data.get("email", ""))
        self._json_response(result)

    def _setup_initial_accounts(self):
        """One-time setup: create initial accounts. Protected by SETUP_KEY env var."""
        from auth import register, _load_users, _save_users
        from datetime import datetime, timedelta

        # Security: require a setup key from env var (prevents unauthorized access)
        setup_key = os.environ.get("SETUP_KEY", "")
        provided_key = parse_qs(urlparse(self.path).query).get("key", [""])[0]
        if not setup_key or provided_key != setup_key:
            self._json_response({"error": "Invalid or missing setup key. Set SETUP_KEY env var and pass ?key=YOUR_KEY"}, 403)
            return

        users = _load_users()
        if users:
            self._json_response({"error": "Accounts already exist."})
            return

        # Read credentials from env vars (never hardcoded)
        admin_email = os.environ.get("ADMIN_EMAIL", "admin@fplpredictor.com")
        admin_pass = os.environ.get("ADMIN_PASSWORD", "")
        cc_email = os.environ.get("CC_EMAIL", "")
        cc_pass = os.environ.get("CC_PASSWORD", "")
        cc2_email = os.environ.get("CC2_EMAIL", "")
        cc2_pass = os.environ.get("CC2_PASSWORD", "")

        if not admin_pass:
            self._json_response({"error": "Set ADMIN_PASSWORD env var"}, 400)
            return

        far = (datetime.now() + timedelta(days=365 * 99)).isoformat()
        created = []

        # Step 1: Register all accounts (each saves as free plan)
        register(admin_email, admin_pass, "Admin")
        created.append({"email": admin_email, "plan": "admin"})

        if cc_email and cc_pass:
            register(cc_email, cc_pass, "CC")
            created.append({"email": cc_email, "plan": "premium"})

        if cc2_email and cc2_pass:
            register(cc2_email, cc2_pass, "CC Alt")
            created.append({"email": cc2_email, "plan": "premium"})

        # Step 2: Load once, upgrade plans, save once
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

        self._json_response({"ok": True, "message": f"{len(created)} accounts created", "accounts": created})

    def _reset_accounts(self):
        """Reset all accounts and re-run setup. Protected by SETUP_KEY."""
        from auth import _save_users, _save_sessions

        setup_key = os.environ.get("SETUP_KEY", "")
        provided_key = parse_qs(urlparse(self.path).query).get("key", [""])[0]
        if not setup_key or provided_key != setup_key:
            self._json_response({"error": "Invalid or missing setup key"}, 403)
            return

        # Wipe users.json AND sessions.json
        _save_users({})
        _save_sessions({})

        # Re-run setup (which now creates accounts with correct plans)
        self._setup_initial_accounts()

    def _serve_latest_predictions(self):
        files = sorted(OUTPUT_DIR.glob("gw*_predictions.json"), reverse=True)
        if not files:
            self._json_response({"error": "No predictions. Run first."}, 404)
            return
        data = json.loads(files[0].read_text(encoding="utf-8"))

        # Apply tier gating — free users can't see xPts
        user = self._get_auth_user()
        is_premium = user and user.get("plan") in ("premium", "admin")

        if not is_premium:
            data["user_plan"] = "free" if user else "guest"

            # Shuffle predictions so free users can't infer xPts from sort order
            import random
            preds = data.get("predictions", [])
            random.shuffle(preds)
            data["predictions"] = preds

            # Mask prediction details for free users
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

            # Mask squad — shuffle XI so ranking is hidden
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

            # Mask chip analysis scores
            chip = data.get("chip_analysis", {})
            if chip.get("best_chip"):
                chip["best_chip"]["score"] = "🔒"
            for rec in chip.get("recommendations", []):
                rec["score"] = "🔒"

            # Mask top picks and differentials
            for key in ("top_picks", "differentials", "value_picks"):
                for p in data.get(key, []):
                    p["predicted_points"] = "🔒"
                    p["raw_xpts"] = "🔒"
                random.shuffle(data.get(key, []))
        else:
            data["user_plan"] = "premium"

        self._json_response(data)

    def _run_and_serve(self, gw):
        try:
            data = _run_predictions(gw)
            self._json_response(data)
        except Exception as e:
            import traceback
            traceback.print_exc(); self._json_response({"error": "Internal server error"}, 500)

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
            traceback.print_exc(); self._json_response({"error": "Internal server error"}, 500)

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
            traceback.print_exc(); self._json_response({"error": "Internal server error"}, 500)

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
            traceback.print_exc(); self._json_response({"error": "Internal server error"}, 500)

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
            traceback.print_exc(); self._json_response({"error": "Internal server error"}, 500)

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
            traceback.print_exc(); self._json_response({"error": "Internal server error"}, 500)

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
            settings = _load_settings()
            squad_ids = None
            chips_available = ["BB", "TC", "FH", "WC"]
            chips_used_list = []
            bank = 0.0
            HALF_CUTOFF = 20  # GW20+ = second half
            current_half = 2  # default to 2nd half
            chip_name_map = {"bboost": "BB", "3xc": "TC", "freehit": "FH", "wildcard": "WC"}

            if settings.get("team_id"):
                try:
                    from my_team import fetch_my_team
                    team_data = fetch_my_team(settings["team_id"])
                    if not team_data.get("error"):
                        picks = team_data.get("picks", [])
                        squad_ids = [p.get("element") for p in picks]
                        bank = team_data.get("gw_summary", {}).get("bank", 0)

                        # Parse chips used — handle FPL's chip naming
                        # FPL 25/26: TWO sets of chips — 1st half (GW1-19) & 2nd half (GW20-38)
                        # Each half gets its own BB, TC, FH, WC
                        raw_chips = team_data.get("chips", [])
                        active_chip = team_data.get("active_chip")  # e.g. "freehit"
                        chips_used_list = raw_chips

                        active_code = chip_name_map.get(active_chip, "") if active_chip else ""

                        # Determine which half we're in
                        current_gw = team_data.get("gw_summary", {}).get("event", 33)
                        current_half = 2 if current_gw >= HALF_CUTOFF else 1

                        # Only count chips used in the CURRENT half
                        # Chips used in the CURRENT GW are "active" (not locked yet)
                        used_set = set()
                        first_half_used = []
                        second_half_used = []
                        for c in raw_chips:
                            name = c.get("name", "")
                            gw = c.get("event", 0)
                            code = chip_name_map.get(name, name.upper())
                            half = 2 if gw >= HALF_CUTOFF else 1
                            entry = {"name": name, "code": code, "gw": gw, "half": half}
                            if half == 1:
                                first_half_used.append(entry)
                            else:
                                second_half_used.append(entry)

                            # Skip chips used in the current GW (they're active, not consumed)
                            if gw == current_gw:
                                continue
                            # Skip the currently active chip (belt and suspenders)
                            if active_chip and name == active_chip:
                                continue
                            # Only mark as "used" if it's in the current half
                            if half == current_half:
                                used_set.add(code)

                        # All 4 chips available per half; check what's used in current half
                        chips_available = []
                        for code in ["BB", "TC", "FH", "WC"]:
                            if code not in used_set:
                                chips_available.append(code)
                except Exception:
                    pass

            planner = SeasonChipPlanner()

            # Always analyze all 4 chips for educational value
            # even if user has none left — show what WOULD be optimal
            all_chips = ["BB", "TC", "FH", "WC"]
            result = planner.analyze_season(
                chips_available=all_chips,
                current_squad_ids=squad_ids,
                bank=bank,
            )

            # Add chip status info — only show current half
            result["user_chips_available"] = chips_available
            result["user_chips_used"] = [
                {"name": c.get("name"), "code": chip_name_map.get(c.get("name",""),"?"), "gw": c.get("event"),
                 "half": 2 if c.get("event", 0) >= HALF_CUTOFF else 1}
                for c in chips_used_list
            ]
            result["current_half"] = current_half if settings.get("team_id") else 2
            result["half_cutoff"] = HALF_CUTOFF
            result["all_used"] = len(chips_available) == 0
            self._json_response(result)
        except Exception as e:
            import traceback
            traceback.print_exc(); self._json_response({"error": "Internal server error"}, 500)

    def _serve_squad_predictions(self, params):
        """Return predictions for specific player IDs at a target GW."""
        try:
            gw = int(params.get("gw", [0])[0])
            ids_str = params.get("ids", [""])[0]
            if not gw or not ids_str:
                self._json_response({"error": "Need ?gw=X&ids=1,2,3"}, 400)
                return
            player_ids = [int(x) for x in ids_str.split(",") if x.strip()]
            from prediction_engine import PredictionEngine
            engine = PredictionEngine()
            all_preds = engine.predict_all(gw)
            pred_map = {p["player_id"]: p for p in all_preds}
            results = []
            for pid in player_ids:
                pred = pred_map.get(pid)
                if pred:
                    results.append({
                        "player_id": pid,
                        "name": pred.get("name", "?"),
                        "team": pred.get("team", "?"),
                        "position": pred.get("position", "?"),
                        "position_id": pred.get("position_id", 0),
                        "price": pred.get("price", 0),
                        "predicted_points": pred.get("predicted_points", 0),
                        "raw_xpts": pred.get("raw_xpts", 0),
                        "form": pred.get("form", 0),
                        "is_dgw": pred.get("is_dgw", False),
                        "num_fixtures": pred.get("num_fixtures", 0),
                        "fixtures": pred.get("fixtures", []),
                        "starter_quality": pred.get("starter_quality", {}),
                        "availability": pred.get("availability", {}),
                        "news": pred.get("news", ""),
                        "team_last5_form": pred.get("team_last5_form", ""),
                    })
                else:
                    results.append({"player_id": pid, "name": "?", "predicted_points": 0, "fixtures": []})
            self._json_response({"gameweek": gw, "predictions": results})
        except Exception as e:
            import traceback
            traceback.print_exc(); self._json_response({"error": "Internal server error"}, 500)

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

            # Use cached predictions for current GW (fast) instead of running predict_all
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

            # Calculate current squad XI xPts
            current_preds = [pred_map.get(pid, {}) for pid in squad_ids]
            current_xi = sorted(current_preds, key=lambda x: x.get("predicted_points", 0), reverse=True)[:11]
            current_xpts = sum(p.get("predicted_points", 0) for p in current_xi)

            # Calculate post-transfer squad XI xPts
            new_squad_ids = [pid for pid in squad_ids if pid != out_id] + [in_id]
            new_preds = [pred_map.get(pid, {}) for pid in new_squad_ids]
            new_xi = sorted(new_preds, key=lambda x: x.get("predicted_points", 0), reverse=True)[:11]
            new_xpts = sum(p.get("predicted_points", 0) for p in new_xi)

            # Multi-GW impact — only predict the 2 relevant players for future GWs (fast)
            multi_gw = []
            from prediction_engine import PredictionEngine
            engine = PredictionEngine()
            gw = target_gw or engine.next_gw

            for future_gw in range(gw, min(gw + 4, 39)):
                if future_gw == gw:
                    # Current GW — use cached data
                    in_future = in_pred.get("predicted_points", 0)
                    out_future = out_pred.get("predicted_points", 0)
                else:
                    # Future GWs — only predict the 2 players, not all 600+
                    in_future_pred = engine.predict_player(in_id, future_gw)
                    out_future_pred = engine.predict_player(out_id, future_gw)
                    in_future = in_future_pred.get("predicted_points", 0)
                    out_future = out_future_pred.get("predicted_points", 0)

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
            traceback.print_exc(); self._json_response({"error": "Internal server error"}, 500)

    def _json_response(self, data, code=200):
        try:
            body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except BrokenPipeError:
            pass  # Client disconnected, safe to ignore

    def log_message(self, format, *args):
        if "/api/" in str(args[0]) if args else False:
            print(f"  API: {args[0]}")


def _auto_setup_accounts():
    """Auto-create initial accounts on startup if they don't exist (for ephemeral hosting)."""
    from auth import register, _load_users, _save_users
    from datetime import timedelta

    admin_email = os.environ.get("ADMIN_EMAIL", "")
    admin_pass = os.environ.get("ADMIN_PASSWORD", "")
    cc_email = os.environ.get("CC_EMAIL", "")
    cc_pass = os.environ.get("CC_PASSWORD", "")
    cc2_email = os.environ.get("CC2_EMAIL", "")
    cc2_pass = os.environ.get("CC2_PASSWORD", "")

    if not admin_email or not admin_pass:
        return  # No credentials configured, skip

    users = _load_users()
    if admin_email in users:
        return  # Already set up

    print("  [SETUP] Creating initial accounts...")
    far = (datetime.now() + timedelta(days=365 * 99)).isoformat()

    # Register all accounts
    if admin_pass:
        register(admin_email, admin_pass, "Admin")
    if cc_email and cc_pass:
        register(cc_email, cc_pass, "CC")
    if cc2_email and cc2_pass:
        register(cc2_email, cc2_pass, "CC Alt")

    # Set correct plans
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


def serve(port: int = PORT, open_browser: bool = True):
    global _last_refresh

    print(f"\n{'='*55}")
    print(f"  FPL Predictor Server v6 (Auto-Refresh + Simulator)")
    print(f"{'='*55}")

    # Auto-create initial accounts if not present (survives ephemeral deploys)
    try:
        _auto_setup_accounts()
    except Exception as e:
        print(f"  [SETUP] ⚠️ Auto-setup failed (non-fatal): {e}")
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

    with ThreadedTCPServer(("0.0.0.0", port), FPLHandler) as httpd:
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
