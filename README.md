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
├── prediction_engine.py   # 12-factor prediction model — the brain
├── squad_optimizer.py     # Beam search + local search optimizer
├── ai_chat.py             # Rule-based NLU chat engine (no external LLM needed)
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

Each player receives a **predicted points score** (`xPts`) for the target gameweek. The prediction combines:

1. **Base expected points** — derived from the player's stats (xG, xA, clean sheet probability, saves, bonus, cards)
2. **12 weighted factors** — modifiers that adjust the base score up or down
3. **Starter quality multiplier** — how nailed-on the player is
4. **Availability adjustment** — injury/suspension risk discount
5. **DGW summation** — in Double Gameweeks, predictions are summed across all fixtures

### The 12 Prediction Factors

| # | Factor | Weight | Description |
|---|--------|--------|-------------|
| 1 | **Form** | 20% | Recent performance — 60% last-5-GW form + 40% season PPG, normalized |
| 2 | **Fixture Difficulty** | 15% | FDR rating (1-5) mapped to multiplier. Attackers weighted 1.2x, defenders 0.9x |
| 3 | **Team Form** | 10% | Team's last-5 win rate + goals scored + momentum. Attackers boosted by scoring form, defenders by clean sheet form |
| 4 | **ICT Index** | 10% | FPL's Influence, Creativity, Threat index per game vs position average |
| 5 | **Season Average** | 8% | Points per game this season, normalized around 3.5 |
| 6 | **H2H Factor** | 8% | Head-to-head record vs this specific opponent + fixture-specific xG advantage |
| 7 | **Home/Away** | 7% | +10% for home games, -8% for away games |
| 8 | **Minutes Consistency** | 7% | Minutes ratio: >85% = +0.2, 65-85% = +0.05, 40-65% = -0.1, <40% = -0.3 |
| 9 | **Team Strength** | 5% | FPL's team attack/defence ratings (position-specific) |
| 10 | **Set Pieces** | 5% | Penalty taker (+0.4), corner taker (+0.15), direct FK (+0.1) |
| 11 | **Transfer Momentum** | 3% | Net transfers this GW — community sentiment signal |
| 12 | **Bonus Tendency** | 2% | Historical bonus points per start vs average |

### Base Expected Points Calculation

For **each fixture**, the base xPts are calculated as:

```
base_xPts = minutes_pts + goal_pts + assist_pts + cs_pts + gc_penalty + bonus + saves + card_risk
```

Where:
- **Minutes pts**: 2.0 (avg ≥60 min), 1.5 (≥30), 0.8 (≥1)
- **Goal pts**: `player_xG_per_start × scoring_context × goal_value × FDR_mod × home_mod`
  - `scoring_context = team_fixture_xG / league_avg` — if the team is expected to score 2.5 vs a weak opponent (vs 1.35 avg), each player's xG is proportionally boosted
  - Goal values: GKP=10, DEF=6, MID=5, FWD=4
- **Assist pts**: `player_xA_per_start × scoring_context × 3 × FDR_mod × home_mod`
- **Clean sheet pts**: `fixture_CS_probability × cs_value`
  - CS probability uses Poisson approximation: `P(0 goals) = e^(-opponent_xG)`
  - GKP/DEF: 4 pts, MID: 1 pt (×0.7 modifier)
- **Goals conceded penalty**: `(team_xGC / 2) × -1 × 0.5` (for DEF/GKP only)
- **Bonus**: `bonus_per_start × FDR_mod × 0.8`
- **Saves (GKP)**: `(saves_per_game × conceding_context / 3) × 1`
- **Card risk**: `(yellows × -1 + reds × -3) / starts × 0.5`

### Fixture-Specific Team xG (from `team_analysis.py`)

The team's expected goals against a specific opponent is calculated as:

```
team_xG = (team_attack_rate × opponent_concede_rate) / league_avg
```

Where:
- `team_attack_rate` = 60% recent (last-5) + 40% season goals-per-game
- `opponent_concede_rate` = 60% recent + 40% season goals-conceded-per-game
- Home boost: ×1.12, Away penalty: ×0.90
- H2H adjustment: ±5% based on this season's head-to-head dominance

### Final Prediction Formula

```
fixture_xPts = base_xPts × (1 + Σ(factor_i × weight_i)) × starter_multiplier
predicted_points = Σ(fixture_xPts for each fixture) × availability_discount
```

### Starter Quality Tiers

| Tier | Criteria | Multiplier |
|------|----------|-----------|
| **Nailed** | ≥75% start rate, ≥65 avg mins | ×1.00 |
| **Regular** | ≥50% start rate, ≥45 avg mins | ×0.90 |
| **Rotation** | ≥30% start rate, ≥25 avg mins | ×0.65 |
| **Fringe** | ≥10 avg mins | ×0.35 |
| **Bench Warmer** | <10 avg mins | ×0.10 |

### Availability Discount

| Chance of Playing | Discount |
|-------------------|----------|
| ≥75% (yellow flag) | **None** — full xPts, just flagged in UI |
| 50% | ×0.50 |
| 25% | ×0.25 |
| <25% | ×0.10 |
| Injured/Suspended | Excluded entirely |

**Design decision**: Players with ≥75% chance are included at full predicted points because FPL auto-subs if they don't play. They're flagged in the UI so you know the risk.

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
| **📊 Overview** | Pitch view with optimal XI, stats summary, chip recommendation |
| **⚽ Best Squad** | Starting XI + bench tables with full stats, xPts, fixtures, team form |
| **🎯 Chip Strategy** | All 4 chips scored 0-100 with reasoning and usage guide |
| **🏆 Top Picks** | Top 30 players, filterable by position, DGW, availability |
| **🔥 DGW Focus** | All DGW teams + best DGW players ranked |
| **💎 Differentials** | <10% ownership, nailed starters with high xPts |
| **💰 Value Picks** | ≤£6.5m players with best xPts/price ratio |
| **👤 My Team** | Import your FPL team → see predicted points, weakest links, transfer suggestions |
| **📋 All Players** | Searchable/filterable full prediction table (100 players) |
| **🤖 AI Chat** | Ask any FPL question — player comparisons, captain picks, strategy |

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

---

## 🤖 AI Chat

The built-in chat engine (`ai_chat.py`) handles natural language questions using rule-based intent detection + prediction data lookups. No external LLM or API key required.

### Supported Question Types

| Intent | Example Questions |
|--------|-------------------|
| **Player comparison** | "Compare Salah vs Haaland", "Why pick X over Y?" |
| **Captain advice** | "Who should I captain?", "Best captain this GW?" |
| **Chip strategy** | "Should I Bench Boost?", "When to use TC?" |
| **Position query** | "Best midfielders?", "Top 10 forwards" |
| **Team query** | "Best Arsenal players?", "City assets?" |
| **DGW questions** | "Best DGW players?", "Which teams have double?" |
| **Differentials** | "Best differentials?", "Low ownership picks?" |
| **Transfer advice** | "Should I sell Salah?", "Who to replace X?" |
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
