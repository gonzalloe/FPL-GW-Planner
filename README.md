# ⚽ FPL Predictor — AI-Powered Fantasy Premier League Squad Optimizer

An intelligent prediction and squad optimization system for Fantasy Premier League. Features a **Poisson-based prediction model** with injury-aware team analysis, **real-time news** from Fabrizio Romano/David Ornstein/BBC Sport, interactive **transfer simulator**, season-wide **chip planning**, and **AI chat** with what-if scenarios.

**Live Demo**: Deploy on [Render.com](https://render.com) in 2 minutes (free tier). See [Deployment](#-deployment).

### Highlights
- 🧠 **12-factor Poisson model** — xG, xA, CS probability, bonus points, negative events
- 🔬 **Model Optimization** — Analyze prediction accuracy, auto-suggest weight adjustments (Admin feature)
- 🏥 **Injury-aware** — teammate injury boosts, team penalty, opponent weakness detection
- 📰 **Real-time news** — Google News RSS pulls Fabrizio Romano, Ben Dinnery, David Ornstein updates
- ⚡ **Transfer Simulator** — FPL-style pitch, drag-to-swap, double-click-to-buy, Optimize XI button
- 🎯 **Chip Planner** — Season-wide analysis, 1 chip per GW, dual chip sets (FPL 25/26)
- 🤖 **AI Chat** — 12 intents, what-if scenarios, per-fixture breakdown
- 👤 **User accounts** — Free/Premium ($2.50/mo)/Admin tiers with Stripe payments
- 📊 **All FPL players** — 600+ players including injured/suspended/youngsters
- 🎨 **Enhanced UI** — Smooth animations, responsive design, modern dark theme

---

## Table of Contents

- [Quick Start](#-quick-start)
- [Project Structure](#-project-structure)
- [Setup Guide](#-setup-guide)
- [User Accounts & Subscription](#-user-accounts--subscription)
- [How Predictions Work](#-how-predictions-work)
- [How the Squad Optimizer Works](#-how-the-squad-optimizer-works)
- [Transfer Simulator](#-transfer-simulator)
- [Season Chip Planner](#-season-chip-planner)
- [GW Planner](#-gw-planner)
- [Dashboard Features](#-dashboard-features)
- [API Reference](#-api-reference)
- [AI Chat](#-ai-chat)
- [Auto-Refresh](#-auto-refresh)
- [Deployment](#-deployment)
- [Configuration & Tuning](#-configuration--tuning)

---

## 🚀 Quick Start

```bash
# 1. Install dependencies
pip install requests numpy

# 2. Generate predictions (fetches live data from FPL API)
python main.py           # Predict for next gameweek
python main.py 33        # Predict for specific GW

# 3. Start the web dashboard (auto-refreshes every 2 hours)
python server.py         # Opens at http://localhost:8888
```

That's it. The system fetches all data from the official FPL API automatically and refreshes every 2 hours.

---

## 📁 Project Structure

```
fpl-predictor/
├── config.py              # All scoring rules, weights, constraints, chip thresholds
├── data_fetcher.py        # FPL API client with local caching + offline fallback
├── team_analysis.py       # Team-level stats: win rates, H2H, fixture xG, momentum
├── prediction_engine.py   # Poisson-based prediction model (v4) — the brain
├── model_optimizer.py     # NEW: Analyze accuracy & suggest weight adjustments
├── squad_optimizer.py     # Beam search + local search optimizer
├── gw_planner.py          # Multi-GW transfer planner with fixture ticker
├── chip_planner.py        # Season-wide chip deployment optimizer
├── ai_chat.py             # Semantic NLU chat engine (v2, no external LLM needed)
├── ai_analyst.py          # LLM prompt generator for external AI analysis
├── my_team.py             # FPL team import via Team ID
├── news_aggregator.py     # Multi-source news: FPL + BBC + Sky + Google News (Romano, Ornstein, Dinnery)
├── auth.py                # User authentication + subscription tiers (free/premium/admin)
├── main.py                # CLI runner
├── server.py              # HTTP server + REST API (port 8888, auto-refresh every 2h)
├── dashboard.html         # Full interactive web dashboard (single-file SPA, ~160KB)
├── requirements.txt       # Python dependencies
├── render.yaml            # Render.com deployment config
├── AUDIT_REPORT.md        # NEW: Code security & quality audit report
├── .gitignore             # Excludes cache, output, data, debug files
├── cache/                 # API response cache (auto-managed)
├── output/                # Generated prediction JSON files
└── data/                  # User accounts & sessions (auto-created, gitignored)
```

---

## 🔬 Model Optimization (Admin Feature)

**NEW**: Analyze prediction accuracy and fine-tune the xPts model based on real match results.

### How It Works

1. Go to **Admin Dashboard** → **Model Optimization** section
2. Click **"Analyze Performance"** button
3. System will:
   - Load recent gameweek predictions (GW33, GW34, etc.)
   - Compare predicted xPts vs actual points scored
   - Calculate accuracy metrics: MAE, RMSE, correlation
   - Detect systematic biases (over/under-prediction)
   - Suggest weight adjustments based on performance

### Metrics Explained

| Metric | Description | Good Range |
|--------|-------------|------------|
| **MAE** (Mean Absolute Error) | Average prediction error | < 3.0 pts |
| **RMSE** (Root Mean Squared Error) | Penalizes large errors more | < 4.0 pts |
| **Correlation** | How well predictions rank players | > 0.5 |
| **Grade** | Overall performance rating | A or B |

### Example Output

```
GW33 Accuracy Metrics:
  MAE: 2.8 pts
  RMSE: 3.6 pts
  Correlation: 0.62
  Grade: B (Good)

Recommendations:
  ✓ Model is slightly over-predicting goals
  → Reduce bonus_tendency weight by 0.02
  → Increase form weight by 0.03
```

### Applying Weight Adjustments

1. Review suggested weights in the comparison table
2. Click **"Apply Suggested Weights"**
3. System updates `config.py` with new PREDICTION_WEIGHTS
4. **Restart server** for changes to take effect:
   ```bash
   python server.py
   ```

### When to Use

- **After each gameweek**: Check if predictions matched reality
- **When MAE > 3.5**: Model needs recalibration
- **When correlation < 0.5**: Prediction factors aren't aligned with outcomes
- **Mid-season**: Adjust for changing meta (DGWs, fixture congestion, etc.)

### Weight Tuning Philosophy

The 12 prediction factors have different impacts:

| Factor | Current Weight | Effect |
|--------|----------------|--------|
| **Form** | 0.20 | Most important — recent performance |
| **Fixture Difficulty** | 0.15 | Opponent strength matters |
| **Team Form** | 0.10 | Team-level context |
| **ICT Index** | 0.10 | FPL's own metric |
| **H2H Factor** | 0.08 | Head-to-head + fixture xG |
| Others | 0.37 | Season avg, home/away, minutes, etc. |

**Principle**: If the model consistently over/under-predicts, nudge the dominant weights (form, fixture_difficulty) in the opposite direction.

---

## 📋 Setup Guide

### Prerequisites

- **Python 3.10+** (tested on 3.12)
- **Internet connection** (to fetch FPL API data)
- No API keys needed — the FPL API is public

### Install & Run

```bash
pip install requests numpy
python main.py        # Generate predictions
python server.py      # Start dashboard at http://localhost:8888
```

### Import Your FPL Team

1. Go to `https://fantasy.premierleague.com` → Log in → "My Team"
2. Find your Team ID in the URL: `fantasy.premierleague.com/entry/1234567/event/...`
3. Enter `1234567` in the **My Team & Planner** page

---

## 👤 User Accounts & Subscription

### Authentication
Users must register/login to access the dashboard. Accounts stored in `data/users.json` (server-side, not in repo).

### Tiers

| Feature | Free | Premium ($2.50/mo) | Admin |
|---------|------|---------------------|-------|
| Import FPL Team | ✅ | ✅ | ✅ |
| View Squad & Formation | ✅ | ✅ | ✅ |
| Basic Fixture Ticker | ✅ | ✅ | ✅ |
| AI Chat | 3/day | Unlimited | Unlimited |
| **xPts Predictions** | 🔒 | ✅ | ✅ |
| **Transfer Simulator** | 🔒 | ✅ | ✅ |
| **Optimize XI** | 🔒 | ✅ | ✅ |
| **Season Chip Planner** | 🔒 | ✅ | ✅ |
| **GW Planner** | 🔒 | ✅ | ✅ |
| **Per-fixture Breakdown** | 🔒 | ✅ | ✅ |
| **What-If Scenarios** | 🔒 | ✅ | ✅ |
| **User Management** | ❌ | ❌ | ✅ |
| **Model Optimization** | ❌ | ❌ | ✅ |

### Payment (Stripe)
- Premium subscription: **$2.50/month** via Stripe Checkout
- Set `STRIPE_SECRET_KEY` env var for live payments
- Without Stripe key: test-mode instant upgrade (for development)
- Webhook endpoint: `POST /api/stripe/webhook`

### Admin API
```bash
# List all users
POST /api/admin/users

# Change user plan
POST /api/admin/set-plan
{"email": "user@example.com", "plan": "premium", "months": 12}

# Delete user
POST /api/admin/delete-user
{"email": "user@example.com"}
```

### Initial Setup (after deployment)
Create admin and personal accounts:
```python
python -c "
from auth import register, _load_users, _save_users
from datetime import datetime, timedelta

# Create accounts
register('admin@yourdomain.com', 'YourAdminPassword', 'Admin')
register('you@yourdomain.com', 'YourPassword', 'YourName')

# Set plans
users = _load_users()
far = (datetime.now() + timedelta(days=365*99)).isoformat()
users['admin@yourdomain.com']['plan'] = 'admin'
users['admin@yourdomain.com']['plan_expires'] = far
users['you@yourdomain.com']['plan'] = 'premium'
users['you@yourdomain.com']['plan_expires'] = far
_save_users(users)
print('Done!')
"
```

---

## 🧠 How Predictions Work

The engine uses a **Poisson-based probabilistic model** inspired by FPL Review, FPL Optimized, FPL Vault, SmartDraftBoard, and XGBoost research papers.

### 12 Prediction Factors

| # | Factor | Weight | Description |
|---|--------|--------|-------------|
| 1 | **Form** | 20% | 65% short-term (last 5 GW) + 35% season average |
| 2 | **Fixture Difficulty** | 15% | Position-aware: attackers dampened to 65%, defenders amplified to 120% |
| 3 | **Team Form** | 10% | Last-5 win rate + goals scored + momentum |
| 4 | **ICT Index** | 10% | FPL's Influence, Creativity, Threat |
| 5 | **Season Average** | 8% | Points per game, normalized |
| 6 | **H2H Factor** | 8% | Head-to-head record + fixture-specific xG |
| 7 | **Home/Away** | 7% | +12% home, -10% away |
| 8 | **Minutes Consistency** | 7% | With volatility penalty |
| 9 | **Team Strength** | 5% | FPL team ratings |
| 10 | **Set Pieces** | 5% | Penalty/corner/FK duties |
| 11 | **Transfer Momentum** | 3% | Community transfer trends |
| 12 | **Bonus Tendency** | 2% | Historical bonus persistence |

### Key Techniques

- **Poisson goal model**: `P(k goals) = (λ^k × e^(-λ)) / k!` — multi-goal EV, not linear
- **Poisson CS probability**: `P(CS) = e^(-opponent_xG)` blended with FDR and defensive form
- **xG delta regression**: Overperformers dampened (0.78-0.92x), underperformers boosted (1.05-1.10x)
- **DGW-aware starter tiers**: Probability of starting BOTH matches (nailed=88%, rotation=25%)
- **Availability rules**: ≥75% chance → full xPts (just flagged), <75% → discounted
- **Teammate injury boost**: When same-position teammates are injured, remaining players get tier promotion (fringe→rotation→regular)
- **Team injury penalty**: Teams with many injured starters get dampened form/strength/xG (up to -30%)
- **Opponent injury penalty**: Playing a weakened team → higher scoring context + higher CS probability
- **External news overrides**: Real-time injury info from Fabrizio Romano, David Ornstein, Ben Dinnery, BBC Sport overrides slow FPL updates

### DGW Starter Tiers

| Tier | P(Both DGW Starts) | 2nd Match xMins Discount |
|------|--------------------| -------------------------|
| **Nailed** | 88% | -8% |
| **Regular** | 60% | -25% |
| **Rotation** | 25% | -50% |
| **Fringe** | 8% | -75% |

### Injury Intelligence

The engine dynamically adjusts predictions based on team injury context:

| Feature | Effect |
|---------|--------|
| **Teammate injury boost** | If same-position teammate is out, player gets tier promotion (fringe→rotation→regular) |
| **Team injury penalty** | `max(0.70, 1.0 - injury_fraction × 0.60)` — dampens form/strength for injured teams |
| **Opponent weakness** | Playing against injured team → higher scoring context, higher CS probability |
| **External news override** | If Fabrizio Romano reports injury before FPL updates, engine applies it immediately |
| **Confidence boost** | +10-15% confidence when opportunity created by teammate injuries |

---

## 🏆 How the Squad Optimizer Works

**Beam Search + Local Search** to maximize total squad xPts under FPL constraints (£100m, 2-5-5-3, max 3 per team).

1. **Beam Search** (width=50): Evaluates player combinations per position globally
2. **Local Search**: Iteratively swaps players to improve total xPts
3. **Formation Selection**: Tests all 7 valid formations, picks highest-xPts XI
4. **Captain Selection**: `predicted_points × availability × dgw_bonus`

---

## 🔄 Transfer Simulator

The **FPL-style Transfer Simulator** lets you plan transfers interactively:

### Features
- **Pitch view**: Your squad displayed as a football pitch (FWD/MID/DEF/GKP rows + bench bar)
- **Player info**: Each player box shows opponent(venue), xG, and W/D/L team form below xPts
- **Click to sell**: Click any player → opens replacement search panel
- **Double-click to buy**: Double-click any replacement to instantly confirm the transfer
- **Drag to swap**: Drag any player onto another to swap positions (starter ↔ bench substitution)
- **⚡ Optimize XI**: Auto-picks the best starting 11 + captain + vice-captain based on xPts across all valid formations
- **GW selector**: Choose target GW (current + next 5) — impact calculated per GW
- **Chip toggle**: Select active chip (WC/FH/BB/TC) — chips used in 1st half don't affect 2nd half (FPL 25/26 dual chip sets)
- **Free transfers on chips**: FH/WC active → all transfers show as FREE (0 hits)
- **Budget awareness**: All players shown, unaffordable ones dimmed with "need +£Xm" badge
- **Impact analysis**: This GW gain, XI xPts before/after, multi-GW value (4 GW lookahead), price delta
- **Live squad update**: After confirming a transfer, the pitch instantly updates with the new player (green glow + "NEW" badge)
- **Transfer queue**: Plan multiple transfers, see total gain/cost/hits summary
- **Save & Restore**: Save your transfer plan to localStorage — auto-restores when you come back
- **Captain/VC**: Double-click for captain (C), right-click for vice-captain (V)

### Drag & Drop Substitution
- Drag any player (starter or bench) onto another → instant position swap
- Blue glow = valid swap, red glow = invalid
- Works for: starter ↔ bench, starter ↔ starter, bench ↔ bench

---

## 🎯 Season Chip Planner

Scans **ALL remaining gameweeks** to find the optimal time for each chip.

### How It Works
1. Go to **Chip Strategy** page → Click **"Analyze Season"**
2. System scans every remaining GW (e.g., GW33-GW38) and scores each chip 0-100
3. Considers: DGW size, BGW blanks, bench quality, captain quality, WC-before-DGW strategy
4. **Uses your actual squad**: BB scores your real bench, TC finds your best captain

### FPL 25/26 Dual Chip Sets
- Each half-season (GW1-19, GW20-38) gets its own set of 4 chips (BB, TC, FH, WC)
- Chips used in 1st half don't affect 2nd half availability
- Currently active chip (this GW) shown as "Active", not "Used"

### Output
- **Recommended Chip Schedule**: Timeline showing when to use each chip (1 chip per GW enforced)
- **Per-chip cards**: Best GW + score + top 3 alternatives with reasoning
- **Score Heatmap**: Color-coded grid of every chip × every GW (hover for details)
- **Auto-detects** which chips you've already used this half-season

### Scoring Factors
| Chip | Key Triggers |
|------|-------------|
| **BB** | Large DGW (6+ teams), strong bench (20+ xPts), bench DGW count |
| **TC** | DGW premium captain (15+ xPts), nailed, easy fixtures |
| **FH** | BGW (5+ teams missing), many squad blanks |
| **WC** | Big DGW 1-2 GWs ahead (WC to build → BB next week) |

---

## 📅 GW Planner

Multi-GW transfer planning with rolling state simulation.

- Pre-computes predictions for 3/5/8 GWs ahead
- Simulates transfers week-by-week with rolling budget and FT tracking
- **Multi-GW value**: Each transfer scored 3 GWs ahead (with decay weighting)
- **Hit threshold**: Only suggests -4 hits if net gain > 4 pts
- **Chip timing**: Per-GW chip scores with ≥70/100 recommendation threshold
- **Fixture Ticker**: All 20 teams' fixtures with FDR colors and DGW badges
- **Fixture Rankings**: Teams ranked by average difficulty

---

## 🌐 Dashboard Features

7 pages, accessible via sidebar (premium tabs hidden for free users):

| Tab | What it shows | Free | Premium |
|-----|---------------|------|---------|
| **📊 Overview** | GW hero card with DGW/BGW alerts, pitch view, stats, chip rec | 🔒 | ✅ |
| **⚽ Best Squad** | Starting XI + bench with full stats, fixtures, form | 🔒 | ✅ |
| **🏆 Players** | Top 30, All, DGW, Differentials, Value — 600+ players | 🔒 | ✅ |
| **🎯 Chip Strategy** | Season-wide analysis, heatmap, half-season chip tracking | 🔒 | ✅ |
| **👤 My Team** | My Squad + Fixture Ticker (free) / + Transfer Sim + GW Planner (premium) | ✅* | ✅ |
| **🤖 AI Chat** | 12-intent NLU, what-if scenarios, per-fixture breakdown | 🔒 | ✅ |
| **🛡️ Admin** | User management, plan upgrades, bulk actions (admin only) | ❌ | ❌/✅ |

### Key UI Features
- **GW Hero header**: Gradient card with large GW number + DGW/BGW/Normal alerts
- **FPL-style pitch**: Gradient jerseys with team shortnames, captain/DGW/injury badges, opponent/xG/form info
- **⚡ Optimize XI**: One-click auto-pick best starting 11 + captain + vice-captain
- **Drag & drop**: Swap players by dragging between positions
- **Double-click to buy**: Instantly confirm transfers in replacement list
- **Save & restore**: Transfer plans persist across sessions via localStorage
- **Live transfer updates**: Pitch reflects changes instantly after confirming transfers
- **Refresh status**: Shows "Updated Xh Ym ago" with manual refresh button

---

## 📡 API Reference

### Data Endpoints (require auth token)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/predictions` | GET | All player predictions (xPts masked for free users) |
| `/api/run?gw=33` | GET | Run fresh predictions |
| `/api/my-team?id=12345` | GET | Fetch & enrich your FPL team |
| `/api/search-players?q=haaland&pos=FWD` | GET | Search players for transfer simulator |
| `/api/simulate-transfer` | POST | `{squad_ids, out_id, in_id, gw}` → impact analysis |
| `/api/gw-planner?id=12345&horizon=5` | GET | Multi-GW transfer plan |
| `/api/season-chips` | GET | Season-wide chip analysis (all remaining GWs) |
| `/api/fixture-ticker` | GET | All 20 teams' fixtures |
| `/api/fixture-rankings?gws=5` | GET | Teams ranked by FDR |
| `/api/chip-analysis` | GET | Current GW chip scoring |
| `/api/chat` | POST | `{"question": "Who should I captain?"}` |
| `/api/news` | GET | Aggregated news from all sources |
| `/api/refresh` | GET | Trigger manual data refresh |
| `/api/refresh-status` | GET | Last refresh time + next refresh ETA |
| `/api/settings` | GET/POST | User settings (team_id, etc.) |

### Auth Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/auth/register` | POST | `{email, password, name}` → create account |
| `/api/auth/login` | POST | `{email, password}` → get session token |
| `/api/auth/me` | POST | Validate token → return user info |
| `/api/stripe/create-checkout` | POST | Start Stripe subscription checkout |
| `/api/stripe/webhook` | POST | Stripe webhook for payment events |

### Admin Endpoints (admin role required)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/admin/users` | POST | List all users |
| `/api/admin/set-plan` | POST | `{email, plan, months}` → change user plan |
| `/api/admin/delete-user` | POST | `{email}` → delete user |
| `/api/admin/model-analysis` | GET | Get prediction accuracy metrics & weight suggestions |
| `/api/admin/apply-weights` | POST | `{weights}` → apply new prediction weights |

---

## 🤖 AI Chat

Semantic intent scoring with 100+ weighted regex patterns across 12 intents. No external LLM required.

| Intent | Example |
|--------|---------|
| **Comparison** | "Compare Salah vs Haaland", "I have X but thinking of Y" |
| **Captain** | "Who should I captain?", "Is Haaland a good captain?" |
| **Keep/Sell** | "Should I keep Saka?", "Time to sell Salah?" |
| **Chip strategy** | "Should I Bench Boost?", "When to use TC?" |
| **Transfers** | "Is it worth taking a -4 for Haaland?" |
| **Value picks** | "Budget defenders?", "Best under 6m" |
| **Differentials** | "Hidden gems nobody owns?" |
| **What-If** | "If Darlow plays both DGW games, what's his xPts?" |
| **Player lookup** | "Tell me about Haaland" — shows per-fixture xPts/xMins/xG/CS% breakdown |

---

## 🔄 Auto-Refresh

The server automatically refreshes all data every **2 hours**:

1. Clears cached FPL API data
2. Re-fetches fresh player stats, injuries, news, fixtures from FPL API
3. **Searches Google News RSS** for latest PL injury/team news (Fabrizio Romano, David Ornstein, Ben Dinnery, BBC, Sky, Guardian)
4. **Cross-references** external news with FPL data → overrides slow FPL injury updates
5. **Rebuilds team injury context** → cascading effects on predictions
6. Re-runs the prediction engine for all 600+ players
7. Manual refresh available via sidebar button or `GET /api/refresh`
8. Refresh status shown in sidebar: "Updated Xh Ym ago"

---

## ⚙️ Configuration & Tuning

All tunable parameters in `config.py`:

```python
PREDICTION_WEIGHTS = {
    "form": 0.20, "fixture_difficulty": 0.15, "team_form": 0.10,
    "ict_index": 0.10, "season_avg": 0.08, "h2h_factor": 0.08,
    "home_away": 0.07, "minutes_consistency": 0.07, "team_strength": 0.05,
    "set_pieces": 0.05, "ownership_momentum": 0.03, "bonus_tendency": 0.02,
}

FDR_MULTIPLIER = {1: 1.30, 2: 1.15, 3: 1.00, 4: 0.85, 5: 0.70}

SQUAD_BUDGET = 1000     # £100.0m
MAX_PER_TEAM = 3
```

---

## 📊 Data Sources

### FPL API (no key needed)

| Endpoint | Data |
|----------|------|
| `bootstrap-static` | All players, teams, GW calendar |
| `fixtures` | All matches with scores, FDR |
| `element-summary/{id}` | Individual player history |

### External News Sources

Real-time injury/team news fetched via Google News RSS on every refresh:

| Source | Reliability | Type |
|--------|------------|------|
| **Fabrizio Romano** | 10/10 | Transfer/injury confirmations |
| **David Ornstein** | 10/10 | Exclusive team news |
| **Ben Dinnery** | 9/10 | Injury specialist |
| **BBC Sport** | 9/10 | RSS feed |
| **The Athletic** | 9/10 | In-depth reporting |
| **Sky Sports** | 8/10 | RSS feed |
| **The Guardian** | 8/10 | RSS feed |
| **PremierInjuries.com** | 9/10 | Injury tracker |

External news automatically overrides FPL's slow injury updates when a mismatch is detected.

Data cached locally with offline fallback. Auto-refreshed every 2 hours.

---

## 🚀 Deployment

### Option 1: Local (default)
```bash
pip install requests numpy
python server.py    # http://localhost:8888
```

### Option 2: Render.com (free, cloud)
1. Push to GitHub (public repo)
2. Go to [render.com](https://render.com) → New → Web Service
3. Connect your GitHub repo: `gonzalloe/FPL-GW-Planner`
4. Settings:
   - **Root Directory**: `fpl-predictor`
   - **Build Command**: `pip install requests numpy`
   - **Start Command**: `python server.py --no-browser`
   - **Environment**: Python 3
5. Deploy — your app will be live at `https://fpl-gw-planner.onrender.com`

The included `render.yaml` and `requirements.txt` auto-configure everything.

### Step 3: Configure Stripe (optional, for real payments)
1. Create account at [stripe.com](https://stripe.com)
2. Get API keys from Stripe Dashboard → Developers → API keys
3. In Render, add environment variables:
   - `STRIPE_SECRET_KEY` = `sk_live_xxx` (or `sk_test_xxx` for testing)
   - `STRIPE_WEBHOOK_SECRET` = `whsec_xxx`
4. In Stripe Dashboard → Webhooks → Add endpoint:
   - URL: `https://your-app.onrender.com/api/stripe/webhook`
   - Events: `checkout.session.completed`

Without Stripe keys, the "Upgrade" button uses test-mode (instant free upgrade for development).

### Step 4: Create Admin Account (after first deploy)
Use Render's Shell tab or run locally:
```python
python -c "
from auth import register, _load_users, _save_users
from datetime import datetime, timedelta
register('admin@yourdomain.com', 'SecurePassword123!', 'Admin')
users = _load_users()
users['admin@yourdomain.com']['plan'] = 'admin'
users['admin@yourdomain.com']['plan_expires'] = (datetime.now() + timedelta(days=365*99)).isoformat()
_save_users(users)
print('Admin account created!')
"
```

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `PORT` | No | Server port (default: 8888, Render sets this automatically) |
| `STRIPE_SECRET_KEY` | No | Stripe API key for real payments |
| `STRIPE_WEBHOOK_SECRET` | No | Stripe webhook signature verification |

---

## License

Personal use. FPL data belongs to the Premier League.
