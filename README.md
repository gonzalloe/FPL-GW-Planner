# ⚽ FPL Predictor — AI-Powered Fantasy Premier League Optimizer

An intelligent prediction and squad optimization system for Fantasy Premier League. Features a **13-factor Poisson model**, real-time injury news, interactive **transfer simulator**, season-wide **chip planner**, **AI chat**, and a modern glassmorphism UI with light/dark theme.

**Live**: [fpl-predictor-e0zz.onrender.com](https://fpl-predictor-e0zz.onrender.com) — deployed on Render free tier.

---

## ✨ Key Features

| Feature | Description |
|---------|-------------|
| 🧠 **13-Factor Prediction Model** | Poisson-based xPts: form, FDR, team strength, xG, win probability, ICT, injuries |
| 🎲 **Win Probability** | Per-fixture match outcome model using independent Poisson distributions |
| 🏥 **Injury Intelligence** | Real-time news from Fabrizio Romano, David Ornstein, Ben Dinnery — overrides slow FPL updates |
| ⚡ **Transfer Simulator** | FPL-style pitch, click-to-sell, double-click-to-buy, drag-to-swap, Optimize XI |
| 🎯 **Season Chip Planner** | Scans all remaining GWs, scores each chip 0-100, uses your actual squad |
| 📅 **GW Planner** | Multi-GW transfer planning with rolling budget and FT simulation |
| 📊 **Fixture Ticker** | All 20 teams × 5-15 GW horizon, FDR colors, DGW/BGW indicators (**free for all**) |
| 🔥 **Top Transfers** | Most transferred in/out players, price risers/fallers, net movers |
| 🤖 **AI Chat** | 12 intents, what-if scenarios, per-fixture breakdown — no external LLM needed |
| 👤 **User Tiers** | Free / Premium ($2.50/mo) / Admin — with Stripe payment integration |
| 🔬 **Model Optimizer** | Analyze accuracy, auto-suggest weights, one-click apply + hot reload (Admin) |
| 🎨 **Modern UI** | Glassmorphism, vibrant gradients, light/dark theme toggle |

---

## 🚀 Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Start the dashboard (auto-generates predictions on first load)
python server.py         # → http://localhost:8888
```

The server fetches all data from the official FPL API and auto-refreshes every 2 hours.

---

## 📁 Project Structure

```
fpl-predictor/
├── server.py              # Flask web server (v9) — REST API, rate limiting, caching
├── dashboard.html         # Single-file SPA (~210KB) — full interactive dashboard
├── auth.py                # User auth + subscription tiers (free/premium/admin)
├── prediction_engine.py   # 13-factor Poisson prediction model
├── squad_optimizer.py     # Beam search + local search optimizer
├── gw_planner.py          # Multi-GW planner + fixture ticker
├── chip_planner.py        # Season-wide chip deployment optimizer
├── ai_chat.py             # Semantic NLU chat engine (12 intents)
├── my_team.py             # FPL team import via Team ID
├── team_analysis.py       # Team-level stats, win probability, fixture xG
├── data_fetcher.py        # FPL API client with local caching
├── news_aggregator.py     # Multi-source news aggregation
├── model_optimizer.py     # Prediction accuracy analysis + weight tuning
├── config.py              # All weights, thresholds, scoring rules
├── requirements.txt       # Python dependencies
├── render.yaml            # Render.com deployment config
├── Procfile               # Gunicorn process config
├── SETUP.md               # Full setup & payment integration guide
├── SCALABILITY.md         # Architecture & growth roadmap
└── data/                  # User accounts & sessions (auto-created, gitignored)
```

---

## 👤 User Tiers

| Feature | Free | Premium ($2.50/mo) | Admin |
|---------|------|---------------------|-------|
| Import FPL Team | ✅ | ✅ | ✅ |
| Fixture Ticker (all teams) | ✅ | ✅ | ✅ |
| Top Transfers | ✅ | ✅ | ✅ |
| Light/Dark Theme | ✅ | ✅ | ✅ |
| AI Chat | 3/day | Unlimited | Unlimited |
| xPts Predictions | 🔒 | ✅ | ✅ |
| Win Probability | 🔒 | ✅ | ✅ |
| Transfer Simulator | 🔒 | ✅ | ✅ |
| Chip Strategy | 🔒 | ✅ | ✅ |
| GW Planner | 🔒 | ✅ | ✅ |
| User Management | ❌ | ❌ | ✅ |
| Model Optimization | ❌ | ❌ | ✅ |

---

## 🧠 Prediction Model

13-factor Poisson model with configurable weights:

| Factor | Weight | Description |
|--------|--------|-------------|
| Form | 20% | 65% short-term (last 5 GW) + 35% season average |
| Fixture Difficulty | 15% | Position-aware: attackers dampened, defenders amplified |
| Team Form | 10% | Last-5 win rate + goals + momentum |
| ICT Index | 10% | FPL's Influence, Creativity, Threat |
| Season Average | 8% | Points per game, normalized |
| H2H Factor | 8% | Head-to-head record + fixture-specific xG |
| Win Probability | 8% | Poisson-based team win probability |
| Home/Away | 7% | +12% home, -10% away |
| Minutes Consistency | 7% | With volatility penalty |
| Team Strength | 5% | FPL team ratings |
| Set Pieces | 5% | Penalty/corner/FK duties |
| Transfer Momentum | 3% | Community transfer trends |
| Bonus Tendency | 2% | Historical bonus persistence |

### Key Techniques
- **Poisson goal model**: Multi-goal expected value, not linear
- **Poisson CS probability**: `P(CS) = e^(-opponent_xG)` blended with FDR
- **Win probability**: Independent Poisson distributions, clamped [5%, 95%]
- **Realistic injury penalty**: 75% chance → 0.92x, 50% → 0.55x, 25% → 0.22x
- **DGW starter tiers**: Nailed=88%, Regular=60%, Rotation=25%, Fringe=8%
- **Teammate injury boost**: Same-position teammate out → tier promotion

---

## ⚙️ Server Architecture (v9)

| Feature | Details |
|---------|---------|
| **Framework** | Flask + Gunicorn (1 worker, 4 threads) |
| **Rate Limiting** | flask-limiter — login 10/min, chat 20/min, heavy endpoints 3-5/min |
| **Response Caching** | In-memory TTL cache for fixture-ticker (2min), top-transfers (5min) |
| **HTTP Cache Headers** | Static assets: 1 day; HTML: 5 min; API: no-cache |
| **Thread Safety** | RLock on all JSON file I/O, atomic writes via tmp→rename |
| **CORS** | Full preflight (OPTIONS) handling for cross-browser compatibility |
| **Auth** | Session tokens (30-day TTL), PBKDF2-SHA256 password hashing |
| **Health Check** | `GET /api/health` — status, cache stats, prediction availability |

---

## 📡 API Reference

### Public Endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/fixture-ticker?horizon=5` | GET | All 20 teams' fixtures (free) |
| `/api/fixture-rankings?gws=5` | GET | Teams ranked by FDR (free) |
| `/api/top-transfers` | GET | Top transfers in/out this GW (free) |
| `/api/health` | GET | Server health check |

### Auth Endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/auth/register` | POST | Create account |
| `/api/auth/login` | POST | Get session token |
| `/api/auth/me` | POST | Validate token |
| `/api/stripe/create-checkout` | POST | Start Stripe checkout |
| `/api/stripe/webhook` | POST | Stripe webhook |

### Data Endpoints (require auth)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/predictions` | GET | All player predictions |
| `/api/my-team?id=12345` | GET | Fetch & enrich FPL team |
| `/api/search-players?q=haaland` | GET | Search players |
| `/api/simulate-transfer` | POST | Transfer impact analysis |
| `/api/gw-planner?id=12345&horizon=5` | GET | Multi-GW transfer plan |
| `/api/season-chips` | GET | Season chip analysis |
| `/api/chip-analysis` | GET | Current GW chip scoring |
| `/api/chat` | POST | AI chat |

### Admin Endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/admin/users` | POST | List all users |
| `/api/admin/set-plan` | POST | Change user plan |
| `/api/admin/delete-user` | POST | Delete user |
| `/api/admin/model-analysis` | GET | Accuracy metrics |
| `/api/admin/apply-weights` | POST | Apply new weights + regen |
| `/api/run` | GET | Trigger prediction run (admin only) |
| `/api/refresh` | GET | Trigger data refresh (admin only) |

---

## 🚀 Deployment

See **[SETUP.md](SETUP.md)** for full deployment guide including:
- Render.com deployment (free tier)
- Environment variables
- Stripe payment gateway setup
- Account configuration
- Troubleshooting

### Quick Deploy to Render
1. Fork/push to GitHub
2. Create Render Web Service → connect repo → root dir: `fpl-predictor`
3. Add env vars: `ADMIN_EMAIL`, `ADMIN_PASSWORD`
4. Deploy — accounts auto-created on first request

---

## 📄 License

Personal use. FPL data belongs to the Premier League.
