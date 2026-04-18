# ⚽ FPL Predictor — AI-Powered Fantasy Premier League Squad Optimizer

An intelligent prediction and squad optimization system for Fantasy Premier League. Pulls live data from the official FPL API, runs a Poisson-based prediction model, and generates the mathematically optimal squad for each gameweek — with transfer simulator, season-wide chip planning, and AI chat.

---

## Table of Contents

- [Quick Start](#-quick-start)
- [Project Structure](#-project-structure)
- [Setup Guide](#-setup-guide)
- [How Predictions Work](#-how-predictions-work)
- [How the Squad Optimizer Works](#-how-the-squad-optimizer-works)
- [Transfer Simulator](#-transfer-simulator)
- [Season Chip Planner](#-season-chip-planner)
- [GW Planner](#-gw-planner)
- [Dashboard Features](#-dashboard-features)
- [API Reference](#-api-reference)
- [AI Chat](#-ai-chat)
- [Auto-Refresh](#-auto-refresh)
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
├── squad_optimizer.py     # Beam search + local search optimizer
├── gw_planner.py          # Multi-GW transfer planner with fixture ticker
├── chip_planner.py        # Season-wide chip deployment optimizer
├── ai_chat.py             # Semantic NLU chat engine (v2, no external LLM needed)
├── ai_analyst.py          # LLM prompt generator for external AI analysis
├── my_team.py             # FPL team import via Team ID
├── news_aggregator.py     # Multi-source football news scraper
├── main.py                # CLI runner
├── server.py              # HTTP server + REST API (port 8888, auto-refresh every 2h)
├── dashboard.html         # Full interactive web dashboard (single-file SPA, ~136KB)
├── cache/                 # API response cache (auto-managed)
└── output/                # Generated prediction JSON files
```

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

6 pages, accessible via sidebar:

| Tab | What it shows |
|-----|---------------|
| **📊 Overview** | GW hero card with DGW/BGW alerts, pitch view with optimal XI, stats summary, chip recommendation |
| **⚽ Best Squad** | Starting XI + bench with full stats, xPts, fixtures, team form |
| **🏆 Players** | Unified table: Top 30, All, DGW, Differentials (<10% owned), Value (≤£6.5m) |
| **🎯 Chip Strategy** | Current GW + season-wide analysis with heatmap, half-season chip tracking |
| **👤 My Team & Planner** | 4 sub-tabs: My Squad, Transfer Simulator, GW Planner, Fixture Ticker |
| **🤖 AI Chat** | Natural language Q&A — semantic NLU with what-if scenarios, no external LLM needed |

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

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/predictions` | GET | Latest cached predictions (all 387 players) |
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
| `/api/refresh` | GET | Trigger manual data refresh |
| `/api/refresh-status` | GET | Last refresh time + next refresh ETA |
| `/api/settings` | GET/POST | User settings (team_id, etc.) |

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

- Clears cached FPL API data
- Re-fetches fresh player stats, injuries, news, fixtures
- Re-runs the prediction engine
- Manual refresh available via sidebar button or `GET /api/refresh`
- Refresh status shown in sidebar: "Updated Xh Ym ago"

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

---

## License

Personal use. FPL data belongs to the Premier League.
