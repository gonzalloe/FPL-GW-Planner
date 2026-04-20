# FPL Predictor — Scalability Report

## Current Architecture

- **Stack**: Python Flask + Gunicorn (1 worker, 4 threads)
- **Hosting**: Render free tier (512MB RAM, 1 CPU, ephemeral disk)
- **Storage**: JSON flat files (users, sessions, settings, predictions)
- **Frontend**: Monolithic SPA (`dashboard.html` ~206KB)
- **Auth**: PBKDF2-SHA256, session tokens, 3-tier (free/premium/admin)

---

## Changes Implemented (v9)

### 1. Thread-Safe File I/O (auth.py, server.py)

**Problem**: 4 Gunicorn threads + no file locking = data corruption risk. `get_user_from_token()` wrote to users.json on EVERY authenticated request.

**Fix**:
- `threading.RLock()` on all JSON read/write operations
- Atomic writes: `tmp.write() → tmp.replace(target)` pattern
- Eliminated write-on-every-read in `get_user_from_token()` (only writes when plan expiry actually changes)

### 2. API Rate Limiting (server.py)

**Problem**: No rate limits on any endpoint. A single user could DoS the server by hammering `/api/run` (full prediction regeneration) or `/api/chat` (CPU-intensive NLU).

**Fix** (via `flask-limiter`, graceful fallback if not installed):

| Endpoint | Limit | Reason |
|----------|-------|--------|
| Global default | 120/min | Baseline protection |
| `/api/auth/login` | 10/min | Brute-force protection |
| `/api/auth/register` | 5/min | Spam prevention |
| `/api/run` | 3/min + admin-only | Heavy computation |
| `/api/refresh` | 5/min + admin-only | Heavy computation |
| `/api/chat` | 20/min | CPU-intensive NLU |
| `/api/gw-planner` | 5/min | Multi-GW prediction |
| `/api/chip-analysis` | 10/min | Squad optimization |
| `/api/health` | Exempt | Monitoring |

### 3. In-Memory Response Cache (server.py)

**Problem**: Endpoints like fixture-ticker, top-transfers, and fixture-rankings re-compute on every request despite data only changing every 2 hours.

**Fix**: `@cached_response(ttl_seconds=N)` decorator with thread-safe in-memory cache. Auto-invalidated when predictions regenerate.

| Endpoint | Cache TTL | Reason |
|----------|-----------|--------|
| `/api/fixture-ticker` | 120s | Fixtures rarely change mid-GW |
| `/api/top-transfers` | 300s | Bootstrap data refreshes every 2h |
| `/api/fixture-rankings` | 120s | Same fixture data |

### 4. Smart HTTP Cache Headers (server.py)

**Problem**: All responses had `no-cache` headers, forcing browsers to re-download everything on every page load — including the 206KB HTML file.

**Fix**: Context-aware caching:
- **API responses** (`/api/*`): `no-store, no-cache` (freshness critical)
- **HTML** (`/`, `*.html`): `max-age=300, must-revalidate` (5 min cache, revalidate on deploy)
- **Static assets** (`*.css, *.js, *.png`): `max-age=86400, immutable` (1 day, bust via query param)

### 5. Auth-Protected Heavy Endpoints (server.py)

**Problem**: `/api/run` and `/api/refresh` were unauthenticated and triggered the heaviest computations (full prediction regeneration).

**Fix**: Both now require admin authentication. Non-admin users can't trigger prediction runs.

### 6. Health Check Endpoint (server.py)

**Added**: `GET /api/health` — returns server status, prediction availability, cache stats, last refresh time. Exempt from rate limiting. Useful for:
- Render uptime monitoring
- Future load balancer health probes
- Debugging production issues

---

## Remaining Scalability Considerations (Future)

### When to Upgrade: Signs You've Outgrown JSON Files
- **>100 users**: Migrate to SQLite (WAL mode) — still file-based, zero config
- **>1000 users**: Migrate to PostgreSQL (Render managed DB, $7/mo)
- **Concurrent writes >10/s**: The RLock + atomic write pattern handles this, but a proper DB is better

### When to Upgrade: Signs You Need More Compute
- **Response times >2s**: Add Gunicorn workers: `--workers 2 --threads 4`
- **RAM >400MB**: Move to Render Starter ($7/mo, 1GB RAM)
- **Need background jobs**: Add Redis + Celery/RQ for prediction generation

### Future Architecture (if needed)

```
[CDN (Cloudflare)] → [Load Balancer]
                         ↓
              [Gunicorn (2+ workers)]
                    ↓         ↓
              [Flask App]  [Flask App]
                    ↓         ↓
              [PostgreSQL] [Redis Cache]
                              ↓
                       [Celery Workers]
                       (prediction jobs)
```

### CORS Hardening
Currently `Access-Control-Allow-Origin: *`. When you have a custom domain, restrict to:
```python
resp.headers["Access-Control-Allow-Origin"] = "https://yourdomain.com"
```

### Frontend Optimization
- The 206KB monolithic `dashboard.html` could be split into lazy-loaded modules
- Consider a build step (Vite/esbuild) for minification + code splitting
- Service Worker for offline capability

### Session Cleanup
Sessions accumulate without cleanup. Add a periodic task:
```python
# Clean expired sessions every 24h
def _cleanup_sessions():
    sessions = _load_sessions()
    now = time.time()
    cleaned = {k: v for k, v in sessions.items() if now - v["created_at"] < SESSION_TTL}
    if len(cleaned) < len(sessions):
        _save_sessions(cleaned)
```

---

*Report generated: 2026-04-20*
