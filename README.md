# ⚽ FPL Predictor — AI-Powered Fantasy Premier League Optimizer

An intelligent prediction and squad optimization system for Fantasy Premier League. Features a **13-factor Poisson model**, real-time injury news, interactive **transfer simulator**, season-wide **chip planner**, **LLM-powered AI chat**, and a modern glassmorphism UI with light/dark theme and a mobile-responsive sidebar.

**Live**: [fpl-predictor-e0zz.onrender.com](https://fpl-predictor-e0zz.onrender.com) — deployed on Render free tier.

---

## ✨ Key Features

| Feature                          | Description                                                                                   |
| --------------------------------- | ----------------------------------------------------------------------------------------------- |
| 🧠 **13-Factor Prediction Model** | Poisson-based xPts: form, FDR, team strength, xG, win probability, ICT, injuries              |
| 🎲 **Win Probability**            | Per-fixture match outcome model using independent Poisson distributions                       |
| 🏥 **Injury Intelligence**        | Real-time news from Fabrizio Romano, David Ornstein, Ben Dinnery — overrides slow FPL updates |
| ⚡ **Transfer Simulator**         | FPL-style pitch, click-to-sell, double-click-to-buy, drag-to-swap, Optimize XI                |
| 🎯 **Season Chip Planner**        | Scans all remaining GWs, scores each chip 0-100, uses your actual squad, DGW/BGW & season-end aware |
| 📅 **GW Planner**                 | Multi-GW transfer planning with rolling budget and FT simulation                              |
| 📊 **Fixture Ticker**             | All 20 teams × 5-15 GW horizon, FDR colors, DGW/BGW indicators (**free for all**)             |
| 🔥 **Top Transfers**              | Most transferred in/out players, price risers/fallers, net movers                             |
| 🤖 **AI Chat**                    | LLM-powered (Dify + Groq/Gemini), grounded in live prediction data — captain picks, comparisons, chip strategy, differentials |
| 📱 **Mobile Navigation**          | Slide-in hamburger sidebar on mobile with full desktop parity (nav, theme toggle, admin panel) |
| 👤 **User Tiers**                 | Free / Premium ($X/mo) / Admin — with Stripe payment integration (Price-ID based)             |
| 🔬 **Model Optimizer**            | Analyze accuracy, auto-suggest weights, one-click apply + hot reload (Admin)                  |
| 🎨 **Modern UI**                  | Glassmorphism, vibrant gradients, light/dark theme toggle                                     |

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

## 🏗️ System Architecture

End-to-end view of every moving part in production — browser, Flask server, prediction / optimization engines, admin tooling, AI chat, and the external services the app talks to.

```
flowchart LR
    subgraph Client["🌐 Client (Browser)"]
        UI["dashboard.html<br/>Single-file SPA<br/>Light/Dark theme<br/>Hamburger sidebar (mobile)"]
        LOGIN["Login / Register<br/>+ Google OAuth<br/>+ Forgot password"]
    end

    subgraph Edge["🚪 Edge (Render.com)"]
        GUNI["Gunicorn<br/>1 worker · 4 threads"]
        RL["flask-limiter<br/>login 10/min · chat 20/min<br/>heavy 3–5/min"]
        CACHE["In-memory TTL cache<br/>fixture-ticker 2m · top-transfers 5m"]
    end

    subgraph App["🧠 Flask App (server.py)"]
        AUTH["auth.py<br/>PBKDF2-SHA256<br/>session tokens (30d)<br/>tiers: free / premium / admin"]
        ROUTES["REST API<br/>/api/predictions · /api/my-team<br/>/api/chat · /api/fixture-ticker<br/>/api/admin/* · /api/stripe/*"]
        CHAT["build_fpl_context() + ask_dify()<br/>Grounds LLM in live predictions,<br/>chips, differentials, season context"]
    end

    subgraph Engines["⚙️ Domain Engines"]
        PRED["prediction_engine.py<br/>13-factor Poisson xPts"]
        SQUAD["squad_optimizer.py<br/>beam + local search"]
        GW["gw_planner.py<br/>multi-GW planner"]
        CHIP["chip_planner.py<br/>season chip optimizer<br/>DGW/BGW & last-GW aware"]
        TEAM["team_analysis.py<br/>win prob · fixture xG"]
        MYTEAM["my_team.py<br/>FPL team import"]
    end

    subgraph Admin["🛡️ Admin Tooling"]
        MOPT["model_optimizer.py<br/>backtest · suggest weights<br/>persisted to storage"]
        RULES["fpl_rules.py<br/>rule reviewer<br/>auto-refine calc"]
        EMAIL["email_service.py<br/>verify · reset"]
    end

    subgraph Data["💾 Data Layer"]
        SUPA[("Supabase Postgres<br/>users · sessions · settings<br/>model_weights")]
        JSON["Local JSON<br/>data/ · cache/ · output/<br/>(atomic writes, RLock)"]
        CONF["config.py<br/>weights · thresholds"]
    end

    subgraph Ext["🌍 External Services"]
        FPL[("FPL Official API<br/>fantasy.premierleague.com")]
        NEWS["news_aggregator.py<br/>multi-source RSS/HTML"]
        GOOG[("Google OAuth")]
        SMTP[("SMTP provider<br/>transactional mail")]
        DIFY[("Dify + Groq/Gemini<br/>LLM chat backend")]
        STRIPE[("Stripe<br/>Checkout · Webhooks · Portal")]
    end

    UI -->|HTTPS| GUNI
    LOGIN -->|HTTPS| GUNI
    LOGIN -. OAuth .-> GOOG
    GOOG -. callback .-> AUTH

    GUNI --> RL --> CACHE --> ROUTES
    ROUTES --> AUTH
    ROUTES --> CHAT
    ROUTES --> PRED
    ROUTES --> SQUAD
    ROUTES --> GW
    ROUTES --> CHIP
    ROUTES --> TEAM
    ROUTES --> MYTEAM
    ROUTES --> MOPT
    ROUTES --> RULES
    ROUTES <--> STRIPE

    CHAT --> DIFY
    CHAT -->|reads| JSON

    AUTH <--> SUPA
    AUTH --> EMAIL --> SMTP

    PRED --> CONF
    SQUAD --> PRED
    GW --> PRED
    CHIP --> PRED
    TEAM --> PRED
    MYTEAM --> FPL
    PRED --> FPL
    NEWS --> ROUTES

    MOPT -->|writes weights| SUPA
    MOPT -. reads history .-> JSON
    RULES -->|confirmed changes| CONF
    CONF <-. hot-reload .- PRED

    classDef ext fill:#fff2cc,stroke:#d6b656,color:#222
    classDef data fill:#e1f5fe,stroke:#0288d1,color:#222
    classDef admin fill:#fde2e2,stroke:#d32f2f,color:#222
    class FPL,GOOG,SMTP,NEWS,DIFY,STRIPE ext
    class SUPA,JSON,CONF data
    class MOPT,RULES,EMAIL admin
```

### AI Chat data flow

```
User question ──► /api/chat ──► build_fpl_context()
                                     │  (top 100 + remaining players,
                                     │   captain pick, chip scores,
                                     │   DGW/BGW, differentials,
                                     │   value picks, season context)
                                     ▼
                              ask_dify(question, context)
                                     │
                                     ▼
                        Dify (LLM: Groq / Gemini, model-agnostic)
                                     │
                                     ▼
                   { answer, conversation_id, suggestions } ──► dashboard.html
```

### Deployment topology

| Layer           | Where it runs               | Persistence                                 |
| ---------------- | ---------------------------- | --------------------------------------------- |
| Static SPA      | Served by Flask from repo   | Stateless                                   |
| Flask + engines | Render.com (Gunicorn)       | Ephemeral FS + `cache/`, `output/`, `data/` |
| Primary DB      | Supabase (Postgres)         | Users, sessions, settings, tuned weights    |
| FPL data        | Local disk cache (`cache/`) | Rehydrated from FPL API on cold start       |
| AI Chat backend | Dify (external)             | Stateless per request; grounded via prompt context |
| Payments        | Stripe (external)           | Subscriptions, webhooks, customer portal    |
| Secrets         | Render env vars             | `SUPABASE_*`, `GOOGLE_OAUTH_*`, `SMTP_*`, `DIFY_*`, `STRIPE_*` |

---

## 📁 Project Structure

```
fpl-predictor/
├── server.py              # Flask web server (v9) — REST API, rate limiting, caching, Dify chat, Stripe
├── dashboard.html         # Single-file SPA (~210KB) — full interactive dashboard + mobile hamburger nav
├── auth.py                # User auth + subscription tiers (free/premium/admin)
├── prediction_engine.py   # 13-factor Poisson prediction model
├── squad_optimizer.py     # Beam search + local search optimizer
├── gw_planner.py          # Multi-GW planner + fixture ticker
├── chip_planner.py        # Season-wide chip deployment optimizer (DGW/BGW/last-GW aware)
├── my_team.py             # FPL team import via Team ID
├── team_analysis.py       # Team-level stats, win probability, fixture xG
├── data_fetcher.py        # FPL API client with local caching
├── news_aggregator.py     # Multi-source news aggregation
├── model_optimizer.py     # Prediction accuracy analysis + weight tuning
├── config.py              # All weights, thresholds, scoring rules
├── requirements.txt       # Python dependencies
├── render.yaml            # Render.com deployment config
├── Procfile                # Gunicorn process config
├── SETUP.md                # Full setup, AI chat, & payment integration guide
├── SCALABILITY.md          # Architecture & growth roadmap
└── data/                   # User accounts & sessions (auto-created, gitignored)
```

> **Note:** `ai_chat.py` (the original 12-intent keyword-matching engine) is retained in the repo as a fallback reference but is no longer wired into `/api/chat` — the chat route now calls `ask_dify()` in `server.py`, which forwards the question plus a live data context to an external LLM via Dify.

---

## 👤 User Tiers

| Feature                    | Free  | Premium | Admin     |
| --------------------------- | ----- | -------- | --------- |
| Import FPL Team            | ✅     | ✅       | ✅         |
| Fixture Ticker (all teams) | ✅     | ✅       | ✅         |
| Top Transfers              | ✅     | ✅       | ✅         |
| Light/Dark Theme           | ✅     | ✅       | ✅         |
| Mobile hamburger nav       | ✅     | ✅       | ✅         |
| AI Chat                    | 3/day | Unlimited | Unlimited |
| xPts Predictions           | 🔒     | ✅       | ✅         |
| Win Probability             | 🔒     | ✅       | ✅         |
| Transfer Simulator         | 🔒     | ✅       | ✅         |
| Chip Strategy              | 🔒     | ✅       | ✅         |
| GW Planner                 | 🔒     | ✅       | ✅         |
| User Management            | ❌     | ❌       | ✅         |
| Model Optimization         | ❌     | ❌       | ✅         |

Premium price is configured in Stripe (see [Stripe setup](#6-stripe-payment-gateway-setup)) — not hardcoded in the app, so it can be changed from the Stripe Dashboard without a code deploy.

---

## 🧠 Prediction Model

13-factor Poisson model with configurable weights:

| Factor               | Weight | Description                                             |
| --------------------- | ------ | ----------------------------------------------------------- |
| Form                 | 20%    | 65% short-term (last 5 GW) + 35% season average         |
| Fixture Difficulty   | 15%    | Position-aware: attackers dampened, defenders amplified |
| Team Form            | 10%    | Last-5 win rate + goals + momentum                       |
| ICT Index            | 10%    | FPL's Influence, Creativity, Threat                       |
| Season Average       | 8%     | Points per game, normalized                               |
| H2H Factor           | 8%     | Head-to-head record + fixture-specific xG                |
| Win Probability      | 8%     | Poisson-based team win probability                       |
| Home/Away            | 7%     | +12% home, -10% away                                       |
| Minutes Consistency  | 7%     | With volatility penalty                                   |
| Team Strength        | 5%     | FPL team ratings                                           |
| Set Pieces           | 5%     | Penalty/corner/FK duties                                   |
| Transfer Momentum    | 3%     | Community transfer trends                                 |
| Bonus Tendency       | 2%     | Historical bonus persistence                               |

### Key Techniques

- **Poisson goal model**: Multi-goal expected value, not linear
- **Poisson CS probability**: `P(CS) = e^(-opponent_xG)` blended with FDR
- **Win probability**: Independent Poisson distributions, clamped [5%, 95%]
- **Realistic injury penalty**: 75% chance → 0.92x, 50% → 0.55x, 25% → 0.22x
- **DGW starter tiers**: Nailed=88%, Regular=60%, Rotation=25%, Fringe=8%
- **Teammate injury boost**: Same-position teammate out → tier promotion

---

## 🤖 AI Chat (Dify + LLM)

The AI Chat feature was migrated from a hardcoded 12-intent keyword-matching engine (`ai_chat.py`) to a **real LLM** grounded in live prediction data via [Dify](https://dify.ai).

### How it works

1. `POST /api/chat` receives `{question, conversation_id}`
2. `build_fpl_context()` assembles a compact text snapshot of the current gameweek: top 100 players (name, team, position, price, xPts, ownership%), remaining players, captain pick, chip scores, DGW/BGW flags, season-end context, differentials, and value picks
3. `ask_dify()` sends `{query: question, inputs: {fpl_data: context}, conversation_id}` to Dify's `chat-messages` endpoint
4. Dify's configured LLM (Groq's Llama models or Gemini, depending on setup) answers using that context
5. The response — `{answer, conversation_id, suggestions}` — is returned to `dashboard.html`, which tracks `conversation_id` across turns for multi-turn memory

### Configuration

| Env var         | Purpose                                              |
| ---------------- | ------------------------------------------------------- |
| `DIFY_API_KEY`   | Dify app API key (`app-xxxx`)                        |
| `DIFY_API_URL`   | Defaults to `https://api.dify.ai/v1`                 |

**Model provider notes:**
- Free-tier Gemini (2.5 Flash/Flash-Lite) is capped at 20 requests/day as of Dec 2025 — not viable for production chat traffic.
- Groq's free tier (e.g. `llama-3.3-70b-versatile`, `deepseek-r1-distill-llama-70b`) offers much higher request volume, but has a **6,000 tokens/minute** cap on some models — keep `build_fpl_context()` output lean (top 100 players, compact pipe-delimited rows) to stay under this limit.
- The system prompt in Dify should explicitly account for **season-end and DGW/BGW context** so chip advice doesn't ignore the fact that, e.g., GW38 is the final gameweek.

### Known constraints

- Context size vs. token-per-minute limits is a live trade-off — reducing player coverage improves reliability on low-TPM providers but risks the LLM not recognizing lower-ranked players by name.
- `ai_chat.py` remains in the repo as an offline/no-LLM fallback reference; it is not currently invoked by `/api/chat`.

---

## ⚙️ Server Architecture (v9)

| Feature                | Details                                                             |
| ------------------------ | ----------------------------------------------------------------------- |
| **Framework**          | Flask + Gunicorn (1 worker, 4 threads)                              |
| **Rate Limiting**      | flask-limiter — login 10/min, chat 20/min, heavy endpoints 3-5/min  |
| **Response Caching**   | In-memory TTL cache for fixture-ticker (2min), top-transfers (5min) |
| **HTTP Cache Headers** | Static assets: 1 day; HTML: 5 min; API: no-cache                    |
| **Thread Safety**      | RLock on all JSON file I/O, atomic writes via tmp→rename            |
| **CORS**               | Full preflight (OPTIONS) handling for cross-browser compatibility   |
| **Auth**               | Session tokens (30-day TTL), PBKDF2-SHA256 password hashing         |
| **AI Chat**            | Dify-backed LLM, grounded via `build_fpl_context()`                 |
| **Health Check**       | `GET /api/health` — status, cache stats, prediction availability    |

---

## 📡 API Reference

### Public Endpoints

| Endpoint                        | Method | Description                         |
| --------------------------------- | -------- | -------------------------------------- |
| `/api/fixture-ticker?horizon=5` | GET    | All 20 teams' fixtures (free)       |
| `/api/fixture-rankings?gws=5`   | GET    | Teams ranked by FDR (free)          |
| `/api/top-transfers`            | GET    | Top transfers in/out this GW (free) |
| `/api/health`                   | GET    | Server health check                 |

### Auth Endpoints

| Endpoint                        | Method | Description                                           |
| --------------------------------- | -------- | --------------------------------------------------------- |
| `/api/auth/register`            | POST   | Create account (honours `REQUIRE_EMAIL_VERIFICATION`) |
| `/api/auth/login`               | POST   | Get session token                                     |
| `/api/auth/me`                  | POST   | Validate token                                        |
| `/api/auth/forgot-password`     | POST   | Send a password-reset email                           |
| `/api/auth/reset-password`      | POST   | Set a new password with a reset token                 |
| `/api/auth/verify-email`        | POST   | Mark an account verified with a token                 |
| `/api/auth/resend-verification` | POST   | Resend the verification email                         |
| `/api/auth/google/login`        | GET    | Start Google OAuth flow (if enabled)                  |
| `/api/auth/google/callback`     | GET    | OAuth return landing                                  |
| `/api/auth/google/exchange`     | POST   | Swap Supabase access_token for a session               |
| `/api/stripe/create-checkout`   | POST   | Start Stripe checkout (uses `STRIPE_PRICE_ID`)         |
| `/api/stripe/webhook`           | POST   | Stripe webhook                                        |

### Data Endpoints (require auth)

| Endpoint                             | Method | Description              |
| --------------------------------------- | -------- | --------------------------- |
| `/api/predictions`                   | GET    | All player predictions   |
| `/api/my-team?id=12345`              | GET    | Fetch & enrich FPL team  |
| `/api/search-players?q=haaland`      | GET    | Search players           |
| `/api/simulate-transfer`             | POST   | Transfer impact analysis |
| `/api/gw-planner?id=12345&horizon=5` | GET    | Multi-GW transfer plan   |
| `/api/season-chips`                  | GET    | Season chip analysis     |
| `/api/chip-analysis`                 | GET    | Current GW chip scoring  |
| `/api/chat`                          | POST   | AI chat (Dify-backed LLM) |

### Admin Endpoints

| Endpoint                    | Method | Description                         |
| ------------------------------ | -------- | ---------------------------------------- |
| `/api/admin/users`          | POST   | List all users                      |
| `/api/admin/set-plan`       | POST   | Change user plan                    |
| `/api/admin/delete-user`    | POST   | Delete user                         |
| `/api/admin/model-analysis` | GET    | Accuracy metrics                    |
| `/api/admin/apply-weights`  | POST   | Apply new weights + regen           |
| `/api/run`                  | GET    | Trigger prediction run (admin only) |
| `/api/refresh`              | GET    | Trigger data refresh (admin only)   |

---

## 📱 Mobile Navigation

The sidebar (`.sidebar`) is desktop-first by default. On viewports ≤768px:

- A fixed hamburger button (`.hamburger-btn`) toggles the sidebar via `transform: translateX(...)`, not `display:none` — this preserves the sidebar's rendered content (avoids the "hamburger opens but menu is empty" issue caused by collapsing width to 0).
- A `.sidebar-overlay` dims the background and closes the menu on tap.
- Nav links auto-close the sidebar on selection (`window.innerWidth <= 768` check).
- The sidebar retains its full desktop content and functionality (Overview, Best Squad, Players, Chip Strategy, Fixtures, Top Transfers, My Team & Planner, AI Chat, Admin, theme toggle, refresh button, account panel) — no separate mobile-only menu is maintained.

---

## 🔐 Authentication Setup

The app ships with a light-weight auth layer ("A1-lite"): PBKDF2 password hashing, Supabase-backed user storage, optional email verification, password reset, and an opt-in Google sign-in button. No heavyweight identity provider required.

### Required env vars (already set for Supabase storage)

| Key            | Description                                |
| ---------------- | ---------------------------------------------- |
| `SUPABASE_URL` | Your Supabase project URL                  |
| `SUPABASE_KEY` | Service role secret key (server-side only) |

### Optional email features (Gmail SMTP or Resend)

Used for "Forgot password" and "Verify email" links. Without either backend configured, the app still runs — the reset/verify links are printed to the server log instead of emailed (dev-mode).

| Key                          | Backend | Description                                                                                                                                                         |
| ------------------------------ | --------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `SMTP_HOST`                  | SMTP    | `smtp.gmail.com` for Gmail                                                                                                                                          |
| `SMTP_PORT`                  | SMTP    | `587` (STARTTLS, default) or `465` (implicit TLS)                                                                                                                   |
| `SMTP_USER`                  | SMTP    | Your Gmail address, e.g. `you@gmail.com`                                                                                                                            |
| `SMTP_PASS`                  | SMTP    | 16-char Google App Password (requires 2FA on the account). **Not** your Google login password.                                                                     |
| `RESEND_API_KEY`             | Resend  | Get one free at resend.com (3000 emails/month)                                                                                                                     |
| `EMAIL_FROM`                 | common  | From address, must match `SMTP_USER` for Gmail SMTP                                                                                                               |
| `PUBLIC_BASE_URL`            | common  | Public site URL, e.g. `https://fpl-predictor-e0zz.onrender.com`                                                                                                     |
| `REQUIRE_EMAIL_VERIFICATION` | common  | `true` to force new signups to verify before first login. Default `false`                                                                                          |

### Google Sign-In (optional, opt-in)

Powered by Supabase's built-in Google OAuth provider. See in-repo comments in `server.py` (`/api/auth/google/*` routes) for the full redirect flow, and enable via `GOOGLE_OAUTH_ENABLED=true`.

---

## 🚀 Deployment

See **[SETUP.md](./SETUP.md)** for the full deployment guide including:

- Render.com deployment (free tier)
- Environment variables
- Dify AI chat setup (Groq/Gemini model provider)
- Stripe payment gateway setup (Price-ID based, configurable without redeploy)
- Account configuration
- Troubleshooting

### Quick Deploy to Render

1. Fork/push to GitHub
2. Create Render Web Service → connect repo → root dir: `fpl-predictor`
3. Add env vars: `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `DIFY_API_KEY`
4. Deploy — accounts auto-created on first request

---

## 6. Stripe Payment Gateway Setup

### Overview

1. Free user clicks **"Upgrade to Premium"**
2. Frontend calls `POST /api/stripe/create-checkout`
3. Backend creates a Stripe Checkout Session using a **Stripe Price object** (`STRIPE_PRICE_ID`) — not a hardcoded amount
4. User is redirected to Stripe's hosted payment page
5. After payment, Stripe sends a webhook to `/api/stripe/webhook`
6. Backend upgrades user to premium
7. User is redirected back with `?upgraded=1` → success toast shown
8. On renewal: `invoice.paid` webhook extends premium
9. On cancellation: `customer.subscription.deleted` webhook downgrades to free
10. Premium users see **"Manage Subscription"** link → Stripe Customer Portal

### Price configuration (Price-ID based)

Pricing is **not hardcoded** in `server.py`. Instead:

1. Stripe Dashboard → **Product catalog** → create a Product + recurring monthly Price
2. Copy the Price ID (`price_xxxxxxxxxxxxx`)
3. Set it on Render:
   ```
   STRIPE_PRICE_ID = price_xxxxxxxxxxxxx
   ```
4. `server.py`'s `/api/stripe/create-checkout` route references `line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}]`

To change the price later: create a **new** Price under the same Product in Stripe (Prices are immutable), then update the `STRIPE_PRICE_ID` env var and redeploy. Existing subscribers keep their original price; new checkouts use the new one.

> **Frontend note:** `dashboard.html` displays the price as static text in a few places (upgrade banner, terms modal, feature-lock overlay). These are cosmetic labels only — they are **not** read from Stripe, so update them manually if the Stripe price changes, to avoid a mismatch between what's advertised and what Stripe actually charges.

### Required env vars

| Variable                | Description                                   |
| -------------------------- | ------------------------------------------------- |
| `STRIPE_SECRET_KEY`     | Stripe API secret key (`sk_test_...` / `sk_live_...`) |
| `STRIPE_WEBHOOK_SECRET` | Stripe webhook signing secret (`whsec_...`)    |
| `STRIPE_PRICE_ID`       | Stripe Price object ID (`price_...`)            |

### Webhook events required

- `checkout.session.completed`
- `invoice.paid`
- `invoice.payment_failed`
- `customer.subscription.deleted`
- `customer.subscription.updated`

Endpoint URL: `https://<your-render-domain>/api/stripe/webhook`

### Without Stripe keys (default behavior)

- "Upgrade" button shows: **"Payment system not configured. Contact admin."**
- Admin can manually upgrade users via the Admin dashboard → Quick Actions
- No payment is processed, no Stripe calls are made
- All other features work normally

---

## 📄 License

Personal use. FPL data belongs to the Premier League.

---

## 🛠️ Recent Fixes

### AI Chat migrated to Dify + external LLM

Replaced the 12-intent keyword-matching `ai_chat.py` engine with `build_fpl_context()` + `ask_dify()` in `server.py`, wired to Dify's `chat-messages` API. The `/api/chat` route now forwards `{question, conversation_id}`, builds a live-data context (players, captain, chips, DGW/BGW, differentials, value picks), and returns the LLM's grounded answer. Frontend (`sendChat()` in `dashboard.html`) tracks `conversation_id` for multi-turn memory.

### Chip strategy — season-end and DGW/BGW awareness

Chip advice previously did not account for how many gameweeks remained in the season, occasionally recommending "save your chip" on the final GW. `build_fpl_context()` / the chip-strategy prompt now includes explicit season context (current GW, GWs remaining, DGW/BGW schedule) so the LLM's chip recommendations correctly account for season-end and known double/blank gameweeks.

### Model Optimizer — weight-cap bug (fixed)

**Symptom:** Clicking **Apply Suggested Weights** in Admin → Model Optimization showed all factor deltas as `→ 0.000`, and the model grade stayed at D even after applying.

**Root cause:** In `model_optimizer.py::suggest_weight_adjustments()`, the `min()` caps used to bound suggested weight increases (e.g. `min(0.12, current + 0.02)` for `ict_index`) were set at or below the already-current weight value, so no real adjustment was possible. Separately, the high-MAE branch's threshold (`> 3.5`) never triggered against typical MAE values (~2.0), so that adjustment path was effectively dead code.

**Fix:** Raised the caps on `fixture_difficulty` and `ict_index` above their current values, added rebalancing (reducing `ownership_momentum`, `bonus_tendency`, `h2h_factor`) so weights continue to sum to 1.0, and lowered the MAE trigger threshold to a realistic value. Applying weights now also requires a data refresh + re-analysis to see the updated grade, since the grade reflects the last-generated predictions, not the just-applied weights.

### Mobile hamburger navigation (added)

Added a `.hamburger-btn` + `.sidebar-overlay` pair with a `@media (max-width: 768px)` block that slides `.sidebar` in via `transform: translateX(...)` rather than toggling `display`, preserving its full desktop content (all nav items, theme toggle, admin panel) on mobile. Initial implementation used `display:none` to hide the sidebar by default, which caused the sidebar to render with zero width when toggled open; fixed by giving the sidebar an explicit `width` and keeping `display:block` while controlling visibility purely via `transform`.

### Stripe pricing — moved from hardcoded amount to Price ID

`/api/stripe/create-checkout` previously created an inline `price_data` object with a hardcoded `unit_amount`, requiring a code change + redeploy to adjust price. Migrated to referencing a Stripe **Price ID** (`STRIPE_PRICE_ID` env var) created in the Stripe Dashboard, so price changes no longer require touching `server.py`. Frontend price labels in `dashboard.html` remain static text and must still be updated manually to stay in sync with whatever price the `STRIPE_PRICE_ID` resolves to.

### Admin / Premium gating — cache poisoning (fixed)

**Symptom:** Users on the `admin` (or `premium`) tier were still seeing `🔒 Unlock` / `Upgrade to see` placeholders on the Overview, Best Squad, Captain pick, Best Chip, xPts columns, etc. — even though the sidebar correctly showed their `ADMIN` badge.

**Root cause:** `/api/predictions` locked premium fields on free/guest requests by **mutating** the dictionaries returned by `_cached_predictions()`. Those dictionaries are the process-wide memo cache (keyed by file mtime), so a single free/guest request would rewrite `predicted_points`, `squad.captain`, `chip_analysis.best_chip`, etc. to `"🔒"` in the shared cache. Every subsequent request — including admin and premium — then received the already-locked data until the predictions file was regenerated.

**Fix:** In `server.py::api_predictions`, deep-copy `data` and `preds` before applying any lock mutations for free/guest users. The shared cache is now read-only from the route's perspective, so admin/premium users always receive the full, unlocked payload.

---

## FPL Rule Reviewer (admin)

The admin dashboard (`/#admin`) includes a **FPL Rule Reviewer** card that keeps the app in sync with the official game when Premier League changes structural rules each season (for example: "no more 2 Free Hits", "budget raised to £100.5m", or new chips).

**How it works**

1. Click **Review FPL Rules** — the backend fetches the live `/api/bootstrap-static/` JSON from `fantasy.premierleague.com`, extracts the structural rules (squad size, budget, per-position limits, transfer cost, captain multiplier, chip counts) and diffs them against the stored baseline.
2. Each changed rule is shown with a checkbox — **SAFE** rows are pre-checked structural JSON rules; **REVIEW** rows (scoring point values) are never auto-applied and must be updated manually in `config.SCORING`.
3. Click **Apply Selected** — re-fetches live, refuses any rule whose value no longer matches the admin's snapshot (stale/tampered protection), writes accepted values to `app_settings.fpl_rules_overrides` in Supabase, and hot-swaps them into `config` in memory. Predictions regenerate in the background.
4. **Rollback** clears every override. Next process start uses `config.py` defaults; next Review captures a fresh baseline.

**Persistence** — all three artefacts (`fpl_rules_baseline`, `fpl_rules_overrides`, `fpl_rules_history`) live under the same Supabase `app_settings` table used for admin-tuned model weights. No table changes needed if you already ran the `app_settings` SQL setup.
