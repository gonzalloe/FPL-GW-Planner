# Per-user FPL account connections

Each signed-in app user can connect an FPL account from **My Team → Connect your FPL account**. This implementation imports the manager's current FPL access token. It does not implement an OAuth redirect or an automated FPL password login. The user signs in on FPL and copies the `X-Api-Authorization` header from a successful `my-team` request in browser Developer Tools. The UI includes these steps.

The app verifies the token with FPL `/api/me/`, discovers `player.entry`, and verifies the private `/api/my-team/{entry}/` response before saving the connection. The caller cannot choose which team the credential belongs to. All outbound authenticated operations are GET requests to fixed FPL API paths with redirects disabled.

## Deploy on Render with Supabase

1. Install the updated `requirements.txt` (adds `cryptography`).
2. Run `migrations/001_fpl_connections.sql` once in the Supabase SQL editor. It creates an RLS-protected table with no browser access. The existing server `SUPABASE_KEY` must be a service-role key, kept server-side.
3. Generate an encryption key locally:

   ```bash
   python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
   ```

4. Add the result as the private Render environment variable `FPL_CONNECTION_KEY`. Keep the same key across redeploys. Do not put it in source code, SQL, a browser bundle, or a committed configuration file. If the key changes, existing connections must reconnect.
5. Redeploy. Each user signs into the app, opens My Team, and connects their own FPL token. The token field clears on submission. Each connection has its own Disconnect action.

With no Supabase configured, local development uses encrypted records in ignored `data/fpl_connections/`. Render uses ephemeral disks, so use Supabase for production persistence. A configured Supabase failure does not fall back to old local records.

## Transfers shown in the app

- **Free Transfers card:** FPL's `transfers.limit` directly, labelled as the gameweek allowance. Public transfer history never reduces this displayed allowance. `null` means Unlimited when FPL explicitly supplies a null limit.
- **Simulator and planner:** the remaining balance from FPL's own fields, `max(0, transfers.limit - transfers.made)`. This prevents counting an already-used allowance again. It does not inspect early/public transfer records and does not add the next gameweek's transfer prematurely.
- **Current squad, sale prices, bank and value:** the private `my-team` response overrides the public deadline snapshot. Historical points and rank remain from the public history.
- **Not connected / viewing a different public team:** no estimated free-transfer number is presented. The private credential is never sent for a different team ID.
- **Unlimited transfers:** displayed explicitly. The simulator permits unlimited transfers; a normal multi-GW transfer plan waits until the active chip/preseason period finishes.

FPL can expire or reject the access token. The app then requests a reconnect rather than inventing a balance or using an old private snapshot. Access tokens are not refreshed automatically. Reconnect with a fresh token from the FPL browser session; no refresh token or password is collected.

## Isolation and storage

Connection routes require the existing app bearer authentication. Storage keys bind a hash of the authenticated app user's email and account-creation timestamp to the encrypted payload. Fernet protects confidentiality and integrity; the encrypted payload repeats its owner identity so moving a record between users fails. Tokens are never returned by status endpoints, placed in URLs/localStorage, or written to application logs. Connection and live-team responses have `Cache-Control: no-store`.

Saved simulator plans in the browser are scoped to the app account and team; old shared plans are not automatically imported. Saved team IDs in `/api/settings` are now per app user. Importing a team does not overwrite a shared global team ID. Public team import still works without a private FPL connection; only the owner's authenticated connection provides live data.

## Verification

```bash
python -m unittest discover -s tests -p 'test_fpl_*.py' -v
```

Tests use synthetic responses. They cover user isolation, encryption, record rebinding, token expiry/rejection, connection deletion, transfer allowance/remaining semantics, HTTP authentication, private-response cache headers, per-user settings, and planning during a background prediction refresh. A real manager session and a deployed Supabase database are still needed for a live end-to-end check.

API implementation references:
- https://github.com/bobbymond/fantasy-bot/blob/main/docs/SYNC_TEAM.md
- https://github.com/rishijatia/fantasy-pl-mcp#authentication
- https://github.com/mcclowes/fpl-oas/blob/main/endpoints/my-team.omg.md
