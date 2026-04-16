# ⚽ FPL Predictor — AI-Powered Fantasy Premier League Squad Optimizer

An intelligent prediction and squad optimization system for Fantasy Premier League. Pulls live data from the official FPL API, runs a multi-factor prediction model, and generates the mathematically optimal squad for each gameweek.

---

## Table of Contents

- [Quick Start](#-quick-start)
- [Project Structure](#-project-structure)
- [Setup Guide](#-setup-guide)
- [How Predictions Work](#-how-predictions-work)
- [How the Squad Optimizer Works](#-how-the-squad-optimizer-works)
- [Chip Strategy System](#-chip-strategy-system)
- [Dashboard Features](#-dashboard-features)
- [API Reference](#-api-reference)
- [GW Planner](#-gw-planner)
- [AI Chat](#-ai-chat)
- [Configuration & Tuning](#-configuration--tuning)

---

## 🚀 Quick Start

```bash
# 1. Install dependencies
pip install requests numpy

# 2. Generate predictions (fetches live data from FPL API)
python main.py           # Predict for next gameweek
python main.py 33        # Predict for specific GW

# 3. Start the web dashboard
python server.py         # Opens at http://localhost:8888
```

That's it. The system fetches all data from the official FPL API automatically.

---

## 📁 Project Structure

```
fpl-predictor/
├── config.py              # All scoring rules, weights, constraints, chip thresholds
├── data_fetcher.py        # FPL API client with local caching (24hr TTL)
├── team_analysis.py       # Team-level stats: win rates, H2H, fixture xG, momentum
├── prediction_engine.py   # Poisson-based prediction model (v4) — the brain
├── squad_optimizer.py     # Beam search + local search optimizer
├── gw_planner.py          # Multi-GW transfer planner with fixture ticker
├── ai_chat.py             # Semantic NLU chat engine (v2, no external LLM needed)
├── ai_analyst.py          # LLM prompt generator for external AI analysis
├── my_team.py             # FPL team import via Team ID
├── news_aggregator.py     # Multi-source football news scraper
├── main.py                # CLI runner
├── server.py              # HTTP server + REST API (port 8888)
├── dashboard.html         # Full interactive web dashboard (single-file SPA)
├── cache/                 # API response cache (auto-managed, 24hr TTL)
└── output/                # Generated prediction JSON files
```

---

## 📋 Setup Guide

### Prerequisites

- **Python 3.10+** (tested on 3.12)
- **Internet connection** (to fetch FPL API data)
- No API keys needed — the FPL API is public

### Step 1: Install Dependencies

```bash
pip install requests numpy
```

Optional (for advanced features):
```bash
pip install pandas scipy   # Enhanced data processing
```

### Step 2: Run Predictions

```bash
python main.py
```

This will:
1. Fetch the FPL bootstrap data (826 players, 20 teams)
2. Fetch all fixtures (380 matches)
3. Build team analysis (win rates, H2H from 319 finished matches)
4. Run the 12-factor prediction model on all eligible players
5. Optimize the best 15-man squad
6. Analyze chip strategy
7. Save results to `output/gw{N}_predictions.json`

### Step 3: Start the Dashboard

```bash
python server.py
```

Opens `http://localhost:8888` in your browser with the full interactive dashboard.

### Step 4: Import Your Team (Optional)

1. Go to `https://fantasy.premierleague.com`
2. Log in → Go to "My Team" or "Points"
3. Find your Team ID in the URL: `fantasy.premierleague.com/entry/1234567/event/...`
4. Enter `1234567` in the "My Team" tab of the dashboard

---

## 🧠 How Predictions Work

### Overview

Each player receives a **predicted points score** (`xPts`) for the target gameweek. The engine uses a **Poisson-based probabilistic model** inspired by the best FPL prediction tools (FPL Review, FPL Optimized, FPL Vault, SmartDraftBoard, and XGBoost research papers).

The prediction combines:

1. **Poisson goal/assist model** — proper probability distribution for scoring events
2. **Poisson clean sheet model** — `P(CS) = e^(-opponent_xG)` for realistic CS probabilities
3. **12 weighted adjustment factors** — form, fixture, team momentum, H2H, ICT, etc.
4. **DGW-aware starter quality tiers** — probability of starting BOTH matches
5. **xG delta regression** — players overperforming xG are dampened back toward mean
6. **Availability adjustment** — ≥75% is full xPts, below 75% gets discounted
7. **Negative event deductions** — cards, own goals, penalty misses at per-90 rates
8. **Bonus prediction** — persistence + projected goal involvement + position base rate

### Research & Methodology Sources

| Tool/Source | Key Technique Adopted |
|-------------|----------------------|
| **FPL Review** | Probability-weighted EV across simulated matches, xMins model |
| **FPL Optimized** | **Poisson distribution** for multi-goal expected value |
| **FPL Vault** | Component-based xPts formula (xG + xA + CS + bonus + saves - cards) |
| **SmartDraftBoard** | **Poisson CS probability**, position-aware FDR |
| **XGBoost papers** (Caidsy, Meharpal Basi, OpenFPL arXiv) | **xG delta regression**, multi-window form (65/35 split), minutes volatility |
| **FPL Copilot** | Opponent-adjusted projections, rotation risk discount |
| **FPL Solver** | Negative event deductions, confidence tiers |

### The 12 Prediction Factors

| # | Factor | Weight | Description |
|---|--------|--------|-------------|
| 1 | **Form** | 20% | 65% short-term (last 5 GW) + 35% season average — from ML feature importance research |
| 2 | **Fixture Difficulty** | 15% | Position-aware: attackers dampened to 65% effect (form > fixture), defenders amplified to 120% |
| 3 | **Team Form** | 10% | Last-5 win rate + goals scored + momentum. Attackers boosted by scoring form, defenders by CS form |
| 4 | **ICT Index** | 10% | FPL's Influence, Creativity, Threat per game vs position average |
| 5 | **Season Average** | 8% | Points per game this season, normalized around 3.5 |
| 6 | **H2H Factor** | 8% | Head-to-head record vs this specific opponent + fixture-specific xG advantage |
| 7 | **Home/Away** | 7% | +12% for home games, -10% for away games |
| 8 | **Minutes Consistency** | 7% | Minutes ratio with volatility penalty for inconsistent players |
| 9 | **Team Strength** | 5% | FPL's team attack/defence ratings (position-specific) |
| 10 | **Set Pieces** | 5% | Penalty taker (+0.4), corner taker (+0.15), direct FK (+0.1) |
| 11 | **Transfer Momentum** | 3% | Net transfers this GW — community sentiment signal |
| 12 | **Bonus Tendency** | 2% | 50% historical persistence + 30% projected GI + 20% position base rate |

### Base Expected Points — Poisson Model

For **each fixture**, the base xPts use Poisson probability distributions rather than linear approximations:

#### Goal Scoring (Poisson Multi-Goal EV)

```
player_lambda = player_xG_per_90 × (xMins/90) × scoring_context × fdr_mod × home_mod

P(k goals) = (λ^k × e^(-λ)) / k!

goal_EV = Σ[k=1..4] P(k goals) × k × goal_value
```

- `scoring_context = team_fixture_xG / league_avg` — if team expected to score 2.5 vs weak opponent, all players' xG boosted proportionally
- Goal values: GKP=10, DEF=6, MID=5, FWD=4
- This captures the **non-linear value** of high-xG players (a player with 0.8 xG has meaningful probability of a brace)

#### Clean Sheet (Poisson Zero-Goals)

```
base_cs = e^(-team_xGC_for_fixture)
cs_prob = 0.60 × base_cs + 0.30 × fdr_cs + 0.10 × recent_defensive_form
```

- `team_xGC` comes from `team_analysis.py` — opponent's scoring rate adjusted for recent form and H2H
- GKP/DEF: 4 pts × cs_prob, MID: 1 pt × cs_prob × 0.7

#### xG Delta Regression

Players significantly outperforming their xG are expected to regress:

```
if goals / xG > 1.40 → multiplier = 0.78 (strong regression expected)
if goals / xG > 1.25 → multiplier = 0.85
if goals / xG > 1.10 → multiplier = 0.92
if goals / xG < 0.70 → multiplier = 1.10 (bounce-back expected)
if goals / xG < 0.85 → multiplier = 1.05
```

#### Negative Events

Deducted from base xPts at per-90 rates:
- Yellow cards: `-1 × yellows_per_start × 0.5`
- Red cards: `-3 × reds_per_start × 0.5`
- Own goals: estimated `-2 × og_rate` (defenders higher)
- Penalty misses: estimated `-2 × miss_rate`

### Starter Quality Tiers (DGW-Aware)

In Double Gameweeks, the key question is: **will this player start BOTH matches?** The system models this probabilistically:

| Tier | Start Rate | Avg Mins | P(Both DGW Starts) | DGW Effective Matches | 2nd Match xMins Discount |
|------|-----------|----------|--------------------|-----------------------|-------------------------|
| **Nailed** | ≥75% | ≥65 | **88%** | 1.92 | -8% |
| **Regular** | ≥50% | ≥45 | **60%** | 1.55 | -25% |
| **Rotation** | ≥30% | ≥20 | **25%** | 1.10 | -50% |
| **Fringe** | — | ≥8 | **8%** | 0.55 | -75% |
| **Bench Warmer** | — | <8 | **2%** | 0.15 | -75% |

**How it works in DGW:**
- 1st fixture: full xMins prediction
- 2nd fixture: xMins × (1 - discount). A nailed player gets 92% of normal minutes; a rotation player gets only 50%
- Each fixture is predicted independently with its own opponent, FDR, home/away, team xG
- Final xPts = fixture_1_xPts + fixture_2_xPts (weighted by start probability)

### Final Prediction Formula

```
For each fixture:
  base = minutes + poisson_goal_EV + poisson_assist_EV + cs_pts + bonus + saves - negatives
  fixture_xPts = base × (1 + Σ(factor_i × weight_i)) × xG_regression × starter_multiplier

predicted_points = Σ(fixture_xPts) × availability_discount
confidence = f(data_quality, minutes_volatility, fixture_count)
```

### Availability Rules

| Chance of Playing | Treatment |
|-------------------|-----------|
| ≥75% (yellow flag) | **Full xPts** — included normally, flagged in UI |
| 50% | ×0.50 discount |
| 25% | ×0.25 discount |
| <25% | ×0.10 discount |
| Injured/Suspended | Excluded entirely |

**Design decision**: ≥75% chance players get zero discount because FPL auto-substitutes if they don't play. The risk is already managed by having a strong bench.

---

## 🏆 How the Squad Optimizer Works

### Goal

Find the **15-player squad** that **maximizes total predicted points** while respecting all FPL constraints:

- **Budget**: £100.0m total
- **Squad composition**: 2 GKP, 5 DEF, 5 MID, 3 FWD
- **Team limit**: Max 3 players from any single team

### Algorithm: Beam Search + Local Search

The optimizer uses a **3-phase approach**:

#### Phase 1: Beam Search (Global Optimization)

```
Position order: GKP(2) → FWD(3) → MID(5) → DEF(5)
Beam width: 50 (keeps top 50 partial solutions at each step)
```

1. Start with empty squad, full budget
2. For each position group:
   - Generate all valid combinations of N players from top candidates
   - For each existing partial solution × each new combination:
     - Check budget feasibility (including minimum reserve for remaining positions)
     - Check team limit (max 3 per team)
     - Calculate total xPts
   - Keep only the top 50 partial solutions (beam width)
3. Return the highest-xPts complete squad

This avoids the greedy trap where picking the best player first blocks better combinations later.

#### Phase 2: Local Search Improvement

After beam search, iteratively try player swaps:

```
For each squad player (worst first):
  For each non-squad player (best first):
    If same position AND improves xPts AND fits budget AND respects team limits:
      → Swap them
```

Repeat until no improving swap exists (up to 200 iterations).

#### Phase 3: Starting XI Selection

Try **all 7 valid formations** and pick the one with highest total xPts:

| Formation | DEF | MID | FWD |
|-----------|-----|-----|-----|
| 3-4-3 | 3 | 4 | 3 |
| 3-5-2 | 3 | 5 | 2 |
| 4-3-3 | 4 | 3 | 3 |
| 4-4-2 | 4 | 4 | 2 |
| 4-5-1 | 4 | 5 | 1 |
| 5-3-2 | 5 | 3 | 2 |
| 5-4-1 | 5 | 4 | 1 |

For each formation: pick top N players per position → sum xPts → keep best.

### Captain Selection

```
captain_score = predicted_points
                × availability_factor (75%+ → 0.90, 50% → 0.20, 25% → 0.05)
                × dgw_nailed_bonus (DGW + nailed → ×1.10)
```

Captain = highest captain_score. Vice = second highest.

---

## 🎯 Chip Strategy System

The `ChipAdvisor` scores each chip 0-100 based on gameweek conditions:

### Bench Boost (BB)
- +40 pts: Bench xPts ≥ 12.0
- +30 pts: ≥3/4 bench players are DGW
- +20 pts: ≥4 teams have double fixtures
- +10 pts: ≥12 total fixtures this GW

### Triple Captain (TC)
- +30 pts: Top player ≥ 10.0 xPts
- +35 pts: Captain is DGW
- +15 pts: Captain form ≥ 7.0
- +10 pts: Captain is nailed starter
- +10 pts: Captain has ≥1 easy fixture (FDR ≤ 2)

### Free Hit (FH)
- +50 pts: ≥4 of your players blank this GW
- +25 pts: DGW but <4 DGW players in your squad
- +30 pts: BGW with <8 total fixtures

### Wildcard (WC)
- +15 pts: Large DGW (≥5 teams)
- Best used 1 GW before a big DGW to build a BB-ready squad

---

## 🌐 Dashboard Features

| Tab | What it shows |
|-----|---------------|
| **📊 Overview** | Pitch view with optimal XI, stats summary, chip recommendation, DGW banner |
| **⚽ Best Squad** | Starting XI + bench tables with full stats, xPts, fixtures, team form |
| **🏆 Players** | Unified player table with filter tabs: Top 30, All, DGW, Differentials (<10% owned), Value (≤£6.5m). Position filters, search, availability toggles |
| **🎯 Chip Strategy** | All 4 chips scored 0-100 with visual rings and reasoning |
| **👤 My Team** | Import your FPL team → predicted points, weakest links, transfer suggestions |
| **📅 GW Planner** | Multi-GW transfer planner with fixture ticker, rolling FT/budget tracking |
| **🤖 AI Chat** | Ask any FPL question in natural language — semantic NLU engine |

### Data Columns Explained

| Column | Meaning |
|--------|---------|
| **xPts** | Predicted points for this GW (risk-adjusted if flagged) |
| **Raw** | Predicted points if the player definitely plays (shown as strikethrough when different from xPts) |
| **Tier** | Starter quality: nailed / regular / rotation / fringe |
| **FDR** | Fixture Difficulty Rating (1=easy green → 5=hard red) |
| **xG** | Team's expected goals in this specific fixture |
| **Team L5** | Team's last 5 results: W(green) D(orange) L(red) |
| **WR** | Team's season win rate percentage |
| **Conf** | Prediction confidence bar (0-100%) |
| **Status** | ✓ available, ⚠ XX% doubtful (hover for injury details) |

---

## 📡 API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/predictions` | GET | Latest cached prediction JSON |
| `/api/run?gw=33` | GET | Run fresh predictions (takes ~10s) |
| `/api/files` | GET | List available prediction files |
| `/api/my-team?id=12345` | GET | Fetch & enrich your FPL team |
| `/api/news` | GET | Aggregated football news |
| `/api/transfers?id=12345` | GET | Transfer recommendations for your team |
| `/api/chip-analysis` | GET | Detailed chip scoring |
| `/api/settings` | GET/POST | User settings (team_id, etc.) |
| `/api/chat` | POST | AI chat — `{"question": "Who should I captain?"}` |
| `/api/gw-planner?id=12345&horizon=5` | GET | Multi-GW transfer plan |
| `/api/fixture-ticker` | GET | All 20 teams' fixtures across horizon |
| `/api/fixture-rankings?gws=5` | GET | Teams ranked by avg fixture difficulty |

---

## 📅 GW Planner

The GW Planner (`gw_planner.py`) simulates optimal transfers across multiple future gameweeks.

### How It Works

1. **Import your team** (via Team ID)
2. **Choose planning horizon** (3, 5, or 8 GWs ahead)
3. The planner:
   - Pre-computes predictions for every GW in the horizon
   - Simulates transfers week by week with rolling budget and free transfer tracking
   - Evaluates each transfer's **multi-GW value** (3-GW lookahead with decay weighting)
   - Recommends chip timing when conditions score ≥70/100

### Features

| Feature | Description |
|---------|-------------|
| **Fixture Ticker** | All 20 teams' fixtures across the horizon with FDR color coding and DGW badges |
| **Fixture Rankings** | Teams ranked by average FDR — see who has the easiest run |
| **Multi-GW Transfer Value** | Each transfer scored not just for this GW, but 3 GWs ahead (with 0.6x decay per week) |
| **Rolling State** | Tracks squad composition, bank balance, free transfers, and chips across the plan |
| **Hit Analysis** | Only suggests -4 hits if the net gain exceeds 4 pts |
| **Chip Timing** | BB/TC/FH/WC scored per GW, only recommended if score ≥70/100 |
| **FT Accumulation** | Tracks free transfers rolling over (max 5 per FPL rules) |

---

## 🤖 AI Chat

The built-in chat engine (`ai_chat.py` v2) uses **semantic intent scoring** to understand natural language questions. Every question is scored against all 11 intents simultaneously — the highest-scoring intent wins. No keyword matching, no external LLM required.

### How Intent Detection Works

```
"I have Salah but thinking of getting Palmer"
  → comparison: 4.5 (2 player entities + "getting" transfer signal)
  → transfer:   3.5 (getting = buy signal + player entity)
  → player:     3.0 (player entity bonus)
  → Winner: COMPARISON ✅
```

100+ weighted regex patterns across 11 intents, plus entity extraction (fuzzy player matching, team aliases, position aliases, price ranges).

### Supported Question Types

| Intent | Example Questions |
|--------|-------------------|
| **Player comparison** | "Compare Salah vs Haaland", "I have X but thinking of Y", "Why pick X over Y?" |
| **Captain advice** | "Who should I captain?", "Is Haaland a good captain?" |
| **Keep/Sell assessment** | "Should I keep Saka?", "Is it time to sell Salah?" |
| **Chip strategy** | "Should I Bench Boost?", "When to use TC?" |
| **Position query** | "Best midfielders?", "Top 10 forwards" |
| **Value picks** | "Budget defenders?", "Best players under 6m" |
| **Team query** | "Best Arsenal players?", "City assets?" |
| **DGW questions** | "Best DGW players?", "Which teams have double?" |
| **Differentials** | "Hidden gems nobody owns?", "Low ownership picks?" |
| **Transfer advice** | "Is it worth taking a -4 for Haaland?", "Who to replace X?" |
| **Player lookup** | "How is Haaland predicted?", "Saka stats" |

### External LLM Integration

For deeper analysis, `ai_analyst.py` generates structured prompts you can paste into ChatGPT/Claude:

```python
from ai_analyst import AIAnalyst
prompt = AIAnalyst.generate_analysis_prompt(predictions, squad, gw)
# → Copy to ChatGPT for expert-level analysis
```

---

## ⚙️ Configuration & Tuning

All tunable parameters live in `config.py`:

### Prediction Weights

```python
PREDICTION_WEIGHTS = {
    "form": 0.20,               # Increase to favor in-form players
    "fixture_difficulty": 0.15,  # Increase to weight easy fixtures more
    "team_form": 0.10,          # Team-level momentum
    "ict_index": 0.10,          # FPL's underlying stats
    "season_avg": 0.08,         # Consistency over the season
    "h2h_factor": 0.08,         # Head-to-head matchup data
    "home_away": 0.07,          # Home advantage
    "minutes_consistency": 0.07, # How nailed-on
    "team_strength": 0.05,      # Overall team quality
    "set_pieces": 0.05,         # Penalty/corner duties
    "ownership_momentum": 0.03, # Transfer trends
    "bonus_tendency": 0.02,     # Bonus point history
}
```

### FDR Multipliers

```python
FDR_MULTIPLIER = {
    1: 1.30,  # Very easy (e.g., top 6 vs bottom 3)
    2: 1.15,  # Easy
    3: 1.00,  # Average
    4: 0.85,  # Tough
    5: 0.70,  # Very tough (e.g., promoted team vs Man City)
}
```

### Squad Constraints

```python
SQUAD_BUDGET = 1000     # £100.0m (in tenths)
MAX_PER_TEAM = 3        # FPL rule: max 3 from one team
```

---

## 📊 Data Sources

All data comes from the **official FPL API** (no API key needed):

| Endpoint | Data |
|----------|------|
| `bootstrap-static` | All 826 players, 20 teams, GW calendar, player stats |
| `fixtures` | All 380 matches with scores, FDR, team lineups |
| `element-summary/{id}` | Individual player detailed history |

Data is cached locally in `cache/` with 24-hour TTL. Delete the cache folder to force a fresh fetch.

---

## 🔄 Updating for a New Gameweek

Simply re-run:

```bash
python main.py       # Fetches fresh data, generates new predictions
python server.py     # Restart server to load new data
```

Or use the dashboard's API: visit `http://localhost:8888/api/run` to regenerate predictions without restarting.

---

## License

Personal use. FPL data belongs to the Premier League.
