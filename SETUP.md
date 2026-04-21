# 🛠️ FPL Predictor — Full Setup Guide

Complete setup instructions for running FPL Predictor locally or deploying to production with Stripe payment integration.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Local Development](#2-local-development)
3. [Deploy to Render.com](#3-deploy-to-rendercom)
4. [Environment Variables](#4-environment-variables)
5. [Account System](#5-account-system)
6. [Stripe Payment Gateway Setup](#6-stripe-payment-gateway-setup)
7. [Post-Deployment Checklist](#7-post-deployment-checklist)
8. [Troubleshooting](#8-troubleshooting)

---

## 1. Prerequisites

- **Python 3.10+** (tested on 3.12)
- **Git** for version control
- **Internet connection** for FPL API data
- No FPL API key needed — the API is public

### Python Dependencies

```
requests>=2.28.0    # HTTP client for FPL API
numpy>=1.24.0       # Numerical computations
flask>=3.0.0        # Web framework
gunicorn>=21.2.0    # Production WSGI server
flask-limiter>=3.5.0 # API rate limiting
stripe              # (optional) Payment processing
```

---

## 2. Local Development

```bash
# Clone the repo
git clone https://github.com/gonzalloe/FPL-GW-Planner.git
cd FPL-GW-Planner/fpl-predictor

# Install dependencies
pip install -r requirements.txt

# Start the server
python server.py    # → http://localhost:8888
```

On first start, the server:
1. Creates `data/` directory for user accounts
2. Waits 90 seconds, then auto-generates predictions
3. Refreshes data every 2 hours

### Local Environment Variables (optional)

Create a `.env` file or set in your shell:

```bash
export ADMIN_EMAIL=admin@yourdomain.com
export ADMIN_PASSWORD=YourSecurePassword
export CC_EMAIL=you@email.com
export CC_PASSWORD=YourPassword
```

The server auto-creates these accounts on first request.

---

## 3. Deploy to Render.com

### Step 1: Push to GitHub

```bash
git push origin main
```

### Step 2: Create Render Web Service

1. Go to [render.com](https://render.com) → Dashboard → **New** → **Web Service**
2. Connect your GitHub repo: `gonzalloe/FPL-GW-Planner`
3. Configure:

| Setting | Value |
|---------|-------|
| **Name** | `fpl-predictor` |
| **Root Directory** | `fpl-predictor` |
| **Runtime** | Python 3 |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `gunicorn server:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120` |
| **Plan** | Free |

> The included `render.yaml` auto-configures most of these settings.

### Step 3: Set Environment Variables

In Render Dashboard → Your Service → **Environment**:

| Variable | Required | Value |
|----------|----------|-------|
| `ADMIN_EMAIL` | ✅ | `admin@yourdomain.com` |
| `ADMIN_PASSWORD` | ✅ | Strong password (min 6 chars) |
| `CC_EMAIL` | Optional | Your personal test account email |
| `CC_PASSWORD` | Optional | Your personal test account password |
| `CC2_EMAIL` | Optional | Second test account email |
| `CC2_PASSWORD` | Optional | Second test account password |
| `SETUP_KEY` | Optional | Secret key for `/api/setup-accounts` and `/api/reset-accounts` |

### Step 4: Deploy

Click **Deploy** or push to GitHub (auto-deploy is enabled).

The first deploy takes ~2-3 minutes. After "Your service is live 🎉":
- Accounts are auto-created on first request
- Predictions generate within ~90 seconds
- Auto-refreshes every 2 hours

---

## 4. Environment Variables

### Required for Production

| Variable | Description |
|----------|-------------|
| `ADMIN_EMAIL` | Admin account email (gets `admin` role) |
| `ADMIN_PASSWORD` | Admin account password |

### Optional — Additional Accounts

| Variable | Description |
|----------|-------------|
| `CC_EMAIL` | Premium test account email |
| `CC_PASSWORD` | Premium test account password |
| `CC2_EMAIL` | Free test account email |
| `CC2_PASSWORD` | Free test account password |

### Optional — Payment

| Variable | Description |
|----------|-------------|
| `STRIPE_SECRET_KEY` | Stripe API secret key (see [Section 6](#6-stripe-payment-gateway-setup)) |
| `STRIPE_WEBHOOK_SECRET` | Stripe webhook signing secret |

### Optional — Utilities

| Variable | Description |
|----------|-------------|
| `PORT` | Server port (default: 8888, Render sets this automatically) |
| `SETUP_KEY` | Secret key for account reset endpoints |

---

## 5. Account System

### How It Works

- Accounts stored in `data/users.json` (server-side, gitignored)
- Sessions stored in `data/sessions.json` (30-day TTL)
- Passwords hashed with PBKDF2-SHA256 (600k iterations)
- On Render free tier: data persists until the next deploy (ephemeral disk)

### Auto-Setup on Startup

When the server starts, it automatically:
1. Creates accounts from `ADMIN_EMAIL`, `CC_EMAIL`, `CC2_EMAIL` env vars
2. Sets correct plans: admin → `admin`, CC → `premium`, CC2 → `free`
3. Syncs passwords if env vars changed since last startup
4. Runs once per process (not on every request)

### Account Roles

| Role | Capabilities |
|------|-------------|
| **free** | Fixture ticker, top transfers, basic AI chat (3/day) |
| **premium** | All features: xPts, transfer sim, chip planner, unlimited chat |
| **admin** | All premium features + user management + model optimization |

### Manual Account Management (Admin)

Via the Admin dashboard or API:

```bash
# Upgrade user to premium (999 months ≈ forever)
POST /api/admin/set-plan
{"email": "user@example.com", "plan": "premium", "months": 999}

# Downgrade to free
POST /api/admin/set-plan
{"email": "user@example.com", "plan": "free"}

# Delete user
POST /api/admin/delete-user
{"email": "user@example.com"}
```

---

## 6. Stripe Payment Gateway Setup

### Overview

The complete payment flow (all code is implemented — just add Stripe keys):

1. Free user clicks **"Upgrade to Premium"**
2. Frontend calls `POST /api/stripe/create-checkout`
3. Backend creates a Stripe Checkout Session ($2.50/month subscription)
4. User is redirected to Stripe's hosted payment page
5. After payment, Stripe sends a webhook to `/api/stripe/webhook`
6. Backend upgrades user to premium (30-day subscription)
7. User is redirected back with `?upgraded=1` → success toast shown
8. On renewal: `invoice.paid` webhook extends premium by 35 days
9. On cancellation: `customer.subscription.deleted` webhook downgrades to free
10. Premium users see **"Manage Subscription"** link → Stripe Customer Portal

### Implementation Status

All code is complete. Just add Stripe keys to activate:

| Feature | Status | Details |
|---------|--------|---------|
| Stripe Checkout Session | ✅ Done | `POST /api/stripe/create-checkout` |
| Webhook: `checkout.session.completed` | ✅ Done | Upgrades user to premium |
| Webhook: `invoice.paid` | ✅ Done | Extends premium on renewal |
| Webhook: `invoice.payment_failed` | ✅ Done | Logs warning, Stripe auto-retries |
| Webhook: `customer.subscription.deleted` | ✅ Done | Downgrades to free |
| Webhook: `customer.subscription.updated` | ✅ Done | Handles status changes (past_due, unpaid) |
| Webhook signature verification | ✅ Done | `STRIPE_WEBHOOK_SECRET` env var |
| Stripe Customer Portal | ✅ Done | `POST /api/stripe/customer-portal` |
| Success/cancel redirect handling | ✅ Done | `?upgraded=1` / `?cancelled=1` URL params |
| "Manage Subscription" button | ✅ Done | Shown for premium users in sidebar |
| Duplicate checkout prevention | ✅ Done | Already-premium users blocked |
| `stripe` in requirements.txt | ✅ Done | `stripe>=5.0.0` |
| Graceful fallback without keys | ✅ Done | Shows "Contact admin" alert |
| `downgrade_to_free()` in auth.py | ✅ Done | Clears plan + subscription ID |
| `extend_premium()` in auth.py | ✅ Done | Extends expiry on renewal |

### Setup Steps (Only Stripe Dashboard Config Needed)

#### Step 1: Create Stripe Account

1. Go to [stripe.com](https://stripe.com) and create an account
2. Complete business verification (required for live payments)
3. You can use **Test Mode** while setting up

#### Step 2: Get API Keys

1. Stripe Dashboard → **Developers** → **API keys**
2. Copy the **Secret key**: `sk_test_xxx` (for testing) or `sk_live_xxx` (production)

#### Step 3: Add Secret Key to Render

Render Dashboard → Your Service → **Environment** → Add:
```
STRIPE_SECRET_KEY = sk_test_xxxxxxxxxxxxxxxxxxxx
```

#### Step 4: Set Up Webhook

1. Stripe Dashboard → **Developers** → **Webhooks** → **Add endpoint**
2. Configure:

| Setting | Value |
|---------|-------|
| **Endpoint URL** | `https://fpl-predictor-e0zz.onrender.com/api/stripe/webhook` |
| **Events** | Select ALL of the following: |

**Required webhook events (select all 4):**
- ✅ `checkout.session.completed`
- ✅ `invoice.paid`
- ✅ `invoice.payment_failed`
- ✅ `customer.subscription.deleted`
- ✅ `customer.subscription.updated`

3. After creating, copy the **Signing secret**: `whsec_xxx`

#### Step 5: Add Webhook Secret to Render

```
STRIPE_WEBHOOK_SECRET = whsec_xxxxxxxxxxxxxxxxxxxx
```

#### Step 6: Enable Customer Portal

1. Stripe Dashboard → **Settings** → **Billing** → **Customer portal**
2. Enable the portal and configure:
   - Allow customers to cancel subscriptions: ✅
   - Allow customers to update payment methods: ✅
   - Allow customers to view invoice history: ✅
3. Save

#### Step 7: Redeploy

Push to GitHub or trigger manual deploy on Render. Done.

#### Step 8: Test the Flow

1. Ensure you're using **Test Mode** keys (`sk_test_xxx`)
2. Log in as a free user (e.g., cc2@fplpredictor.com)
3. Click **"Upgrade to Premium"** or **"Upgrade Now"** in the banner
4. Use Stripe test card: `4242 4242 4242 4242`, any future expiry, any CVC
5. Verify:
   - ✅ Redirected back to app with success message
   - ✅ User plan changed to `premium` (check admin dashboard)
   - ✅ Premium features unlocked (xPts, Transfer Sim, etc.)
   - ✅ "Manage Subscription" link appears in sidebar
   - ✅ Stripe Dashboard → Payments shows the charge
   - ✅ Stripe Dashboard → Webhooks shows `checkout.session.completed` delivered
6. Test "Manage Subscription" link → opens Stripe Customer Portal
7. Cancel subscription in portal → verify user downgrades to free

#### Step 9: Go Live

1. Switch to **Live Mode** in Stripe Dashboard
2. Get live keys: `sk_live_xxx`
3. Create a **new webhook endpoint** (live mode has separate webhooks) with the same events
4. Update Render env vars with live keys
5. Redeploy

### Quick Reference

| What | Where | Value |
|------|-------|-------|
| Secret Key | Render env `STRIPE_SECRET_KEY` | `sk_test_xxx` or `sk_live_xxx` |
| Webhook Secret | Render env `STRIPE_WEBHOOK_SECRET` | `whsec_xxx` |
| Webhook URL | Stripe Dashboard | `https://your-app.onrender.com/api/stripe/webhook` |
| Webhook Events | Stripe Dashboard | 5 events (see Step 4) |
| Price | server.py (line ~880) | $2.50/month (unit_amount: 250 cents) |
| Currency | server.py | USD |
| Mode | server.py | Subscription (recurring monthly) |

### Without Stripe Keys (Default Behavior)

When `STRIPE_SECRET_KEY` is not set:
- "Upgrade" button shows: **"Payment system not configured. Contact admin."**
- Admin can manually upgrade users via the Admin dashboard → Quick Actions
- No payment is processed, no Stripe calls are made
- All other features work normally

---

## 7. Post-Deployment Checklist

After deploying, verify:

- [ ] Site loads at your Render URL
- [ ] Admin can log in with `ADMIN_EMAIL` / `ADMIN_PASSWORD`
- [ ] Admin sees ADMIN badge (not FREE)
- [ ] CC account has PREMIUM badge
- [ ] Predictions generate within ~2 minutes of first visit
- [ ] Fixture Ticker loads for free users
- [ ] Theme toggle works (light/dark)
- [ ] Login works on multiple browsers/devices
- [ ] Health check: `GET /api/health` returns `{"status": "healthy"}`

### If Using Stripe:
- [ ] `STRIPE_SECRET_KEY` set in Render environment
- [ ] `STRIPE_WEBHOOK_SECRET` set in Render environment
- [ ] Webhook endpoint created in Stripe Dashboard with all 5 events
- [ ] Customer Portal enabled in Stripe Dashboard → Settings → Billing
- [ ] Test payment works with card `4242 4242 4242 4242`
- [ ] User plan upgrades to premium after test payment
- [ ] Success message shown after redirect (`?upgraded=1`)
- [ ] "Manage Subscription" link works for premium users
- [ ] Webhook logs show events delivered in Stripe Dashboard

---

## 8. Troubleshooting

### "Connection error: fail to fetch" on login
- **Cause**: Server is restarting (Render deploys take ~30s)
- **Fix**: Wait for "Your service is live 🎉" in Render logs, then hard refresh (Ctrl+Shift+R)
- The login now has 3x auto-retry with 2-second delays

### Account shows FREE instead of ADMIN/PREMIUM
- **Cause**: Race condition in account setup (fixed in v9)
- **Fix**: Redeploy — the server now syncs plans from env vars on every startup
- Check Render logs for `[SETUP] Plan fixed: email → plan`

### Predictions not loading (stuck on "Generating...")
- **Cause**: Auto-refresh hasn't completed yet (runs ~90s after startup)
- **Fix**: Wait 2 minutes after deploy. Admin users can also trigger `/api/run`

### Data lost after Render redeploy
- **Cause**: Render free tier uses ephemeral disk — all files are wiped on deploy
- **Impact**: User accounts, sessions, and predictions are regenerated on startup
- **Accounts**: Auto-recreated from env vars
- **Sessions**: Users need to log in again after each deploy
- **Predictions**: Auto-generated ~90s after startup

### Stripe webhook not working
1. Check Stripe Dashboard → Webhooks → Recent events
2. Verify the endpoint URL matches your Render URL exactly
3. Check the signing secret matches `STRIPE_WEBHOOK_SECRET`
4. Render logs should show the webhook request arriving

### Rate limit errors (429)
- Global: 120 requests/minute per IP
- Login: 10/minute, Register: 5/minute
- Heavy endpoints (run, planner): 3-5/minute
- Health check is exempt from rate limiting
