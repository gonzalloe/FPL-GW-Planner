# FPL Predictor - Codebase Audit Report

**Date**: 2026-04-20  
**Audited By**: WorkBuddy AI  

---

## Summary

✅ **Overall Status**: PASS  
✅ **Security**: No critical issues found  
✅ **Code Quality**: Good, with improvements applied  
✅ **Performance**: Optimized for Render free tier  

---

## Cleaned Up

### Removed Files
- ✅ `test_v2.py` - Old test script (unused)
- ✅ `test_v3.py` - Old test script (unused)
- ✅ `debug_gw.py` - Debug script (unused)
- ✅ `debug_player.py` - Debug script (unused)
- ✅ `check_oreilly.py` - Debug script (unused)
- ✅ `time_test.py` - Debug script (unused)

These files are properly excluded in `.gitignore`.

---

## Security Review

### ✅ Credentials Management
- **Passwords**: All handled via environment variables (`ADMIN_PASSWORD`, `CC_PASSWORD`, etc.)
- **API Keys**: Stripe keys loaded from env vars (`STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`)
- **Password Hashing**: PBKDF2-SHA256 with 600k iterations (industry standard)
- **Tokens**: Cryptographically secure (`secrets.token_hex(32)`)

### ✅ Authentication
- **Session tokens**: Secure random generation
- **User roles**: Proper admin/premium/free tier checks
- **API protection**: All sensitive endpoints check auth headers

### ✅ Data Storage
- **User data**: Stored in `data/users.json` (gitignored)
- **Sessions**: Stored in `data/sessions.json` (gitignored)
- **Cache**: Auto-managed in `cache/` directory (gitignored)

### ⚠️ Minor Security Notes
1. **CORS**: Currently set to `*` (allow all origins). Consider restricting in production:
   ```python
   resp.headers["Access-Control-Allow-Origin"] = "https://yourdomain.com"
   ```

2. **Rate Limiting**: No rate limiting on API endpoints. Consider adding for production:
   ```python
   from flask_limiter import Limiter
   limiter = Limiter(app, default_limits=["200 per day", "50 per hour"])
   ```

---

## Code Quality

### ✅ Well-Structured
- Clear separation of concerns (data_fetcher, prediction_engine, squad_optimizer, etc.)
- Single-file SPA dashboard (easy deployment)
- Comprehensive config.py for easy tuning

### ✅ Documentation
- Detailed docstrings in prediction_engine.py
- Comprehensive README.md
- Inline comments for complex logic

### ✅ Error Handling
- Try-catch blocks in critical paths
- Graceful fallbacks (offline cache, missing data)
- User-friendly error messages

---

## New Features Added

### 1. Model Optimizer (`model_optimizer.py`)
- Analyzes prediction accuracy vs actual points
- Calculates MAE, RMSE, correlation metrics
- Suggests weight adjustments
- Can apply changes to config.py

### 2. Admin Dashboard Enhancement
- Added Model Optimization section
- Visual performance metrics display
- One-click weight adjustment application
- Weight comparison table

### 3. UI/UX Improvements
- Added smooth animations (fade-in, pulse-glow, spin)
- Hover effects on cards and buttons
- Improved responsive design
- Loading states with spinners
- Toast notification styles
- Gradient text on stat values
- Mobile-friendly sidebar collapse

---

## Performance

### ✅ Optimized for Render Free Tier
- Memory-efficient caching
- Auto-refresh every 2 hours (not every request)
- Thread-safe refresh mechanism
- Lazy loading of heavy operations

### ✅ Frontend
- Single HTML file (~160KB)
- Inline CSS/JS (no external dependencies)
- Fast initial load
- Client-side rendering for responsiveness

---

## Recommendations for Production

1. **Environment Variables** (already set up):
   ```bash
   PORT=8888
   STRIPE_SECRET_KEY=sk_live_xxx
   STRIPE_WEBHOOK_SECRET=whsec_xxx
   ADMIN_EMAIL=admin@yourdomain.com
   ADMIN_PASSWORD=SecurePassword123!
   CC_EMAIL=you@yourdomain.com
   CC_PASSWORD=YourPassword
   ```

2. **Enable HTTPS** (Render provides this automatically)

3. **Monitor Performance**:
   - Check memory usage: `ps aux | grep python`
   - Check logs: Render Dashboard → Logs

4. **Backup Data**:
   - Regularly backup `data/users.json` (not in repo)
   - Consider using a managed database for production scale

5. **Rate Limiting** (future enhancement):
   ```bash
   pip install flask-limiter
   ```

---

## Testing Checklist

- [x] Login/Register flow
- [x] Admin user management
- [x] Model optimization feature
- [x] Prediction loading
- [x] Transfer simulator
- [x] Chip planner
- [x] AI chat
- [x] Auto-refresh mechanism
- [x] Mobile responsive design
- [x] Stripe payment flow (test mode)

---

## Conclusion

The codebase is **production-ready** with:
- ✅ Strong security practices
- ✅ Clean architecture
- ✅ Comprehensive features
- ✅ Good documentation
- ✅ Performance optimized

No critical issues found. All test/debug files removed. Ready for deployment.


---

## Pass 2 — FPL Rule Reviewer + Hardening

**Date**: 2026-04-22  
**Scope**: code-security pass alongside the FPL Rule Reviewer feature rollout.

### Findings & Fixes

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | No request-body size cap — oversized JSON could exhaust worker memory | Medium | **Fixed** — `app.config["MAX_CONTENT_LENGTH"] = 256 KB` added at Flask init |
| 2 | `/api/setup-accounts` & `/api/reset-accounts` used plain string compare on the SETUP_KEY (timing attack) | Medium | **Fixed** — switched to `hmac.compare_digest`, added min-20-char key length guard, kept key-gate closed when unset |
| 3 | Same two endpoints were un-rate-limited | Low | **Fixed** — `@limiter.limit("5/min")` and `"2/min")` respectively |
| 4 | 10 admin routes each copy-paste the same `if user.plan != 'admin': 403` guard — one missed paste = full privilege bypass | Medium | **Mitigated** — added `require_admin` decorator; routes can migrate incrementally. All 10 existing routes were re-verified to currently have the inline guard. |
| 5 | FPL API response was previously consumed raw into `config` | Low → would become Medium once auto-apply shipped | **Fixed** — `fpl_rules.py` validates every field (type + range), refuses apply if the admin's snapshot no longer matches live API, and never auto-applies scoring point values |
| 6 | No audit log for admin-initiated rule/weight changes | Low | **Fixed** — `fpl_rules_history` key (capped 20 entries) records every apply/rollback with admin email + timestamp |
| 7 | Admin-tuned model weights & rule overrides previously lived only on Render's ephemeral disk → wiped on every redeploy | High (data loss) | **Fixed** (earlier commit) — persisted via `app_storage` → Supabase `app_settings` table |

### Posture re-confirmed (no change needed)

- ✅ **Password hashing**: PBKDF2-SHA256, 600 000 iterations (OWASP-recommended minimum), 16-byte per-user salt, single-use reset tokens (`secrets.token_urlsafe(32)`) with expiry.
- ✅ **Auth transport**: bearer-token header (no cookies → no CSRF surface).
- ✅ **Stripe webhook**: signature verified via `stripe.Webhook.construct_event`.
- ✅ **Email canonicalisation**: `email.strip().lower()` in register/login/reset.
- ✅ **Path traversal**: Flask's `send_from_directory` blocks `..`; explicit blocklist for `data/`, `.env`, `*.py`, `users.json`, `sessions.json`.
- ✅ **No `eval` / `exec` / `debug=True`** anywhere in the request path.
- ✅ **Rate-limit** on all auth endpoints: login 10/min, register 5/min, forgot 3/min, reset 5/min, resend-verify 10/min.

### Known, intentional deferrals

- **Per-account lockout** after N failed logins — deferred; current 10/min/IP rate limit is adequate for the deployment size. Add if traffic scales.
- **Content-Security-Policy** / other hardening headers — not yet set. Flask default is unopinionated. Add via a `@app.after_request` hook when we stop using inline JS in `dashboard.html`.
- **Replay protection** on the /rules/apply endpoint — we already re-fetch server-side and require the admin's `snapshot` to match the live value, which is a stronger guarantee than a nonce.

### Conclusion

**Status: PASS.** The new FPL Rule Reviewer adds administrative surface but does so with validation, re-fetch, audit log, and defensive merge semantics. The companion hardening closes the remaining medium-severity findings from the original audit's surface area.
