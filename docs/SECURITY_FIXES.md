# Critical and high-risk fixes

The changes address the seven critical/high findings from the repository audit:

- Public requests serve only the dashboard. Prediction exports require administrator authentication and an allowed filename.
- Administrator user buttons pass email addresses as data through event listeners, rather than interpolating them into JavaScript.
- Account/session writes update only changed records. Database failures stop the operation; they do not become empty stores or successful ephemeral backups. JSON revision fields provide optimistic concurrency checks. Account/session reads paginate or use an exact identity lookup.
- Premium API routes enforce plans on the server. Public prediction and imported-team responses use a public-field allowlist. Browser snapshots are scoped by verified account and plan; authenticated responses vary by Authorization.
- Stripe synchronization records absolute subscription expiry and processed event IDs together in the account row. Duplicate, initial-invoice and delayed events do not add more days. Checkout reuses existing Stripe customers.
- Free-account chat quota reservation is atomic. Anonymous requests are capped at three per IP per day in the running process; anonymous counters reset on restart and are not a cross-instance quota. Two provider slots prevent chat requests from consuming every web thread.
- New predictions include a reference-fixture baseline, including during blank gameweeks. Future projections apply each target fixture modifier to that baseline. Legacy non-blank caches are normalized; legacy blank caches require a refresh rather than fabricated predictions.

## Deployment

No new dependencies, environment variables, database tables or SQL migrations are required. Existing user/session rows are upgraded lazily inside their JSON data. Existing valid sessions remain supported.

1. Back up the existing `users` and `sessions` tables before deploying account-storage changes.
2. Deploy through the normal Render build. Shell access is not required.
3. Allow the background prediction refresh to finish, or trigger Refresh Data as an administrator. A legacy blank-GW cache can return a refresh-needed error until the new baseline is generated.
4. Smoke-test sign-in, public team import, premium simulation/planning, administrator controls and Stripe test-mode checkout/renewal/cancellation. Payment processing and a live Supabase database are not exercised by the local mocked regression suite.

The file-serving fix prevents new disclosure. If sensitive files were present on a publicly reachable affected deployment, assess the exposure, rotate affected credentials and revoke affected sessions. This source change does not rotate external credentials or revoke all production sessions automatically.

Only critical/high findings are in scope. The audit's remaining medium/low calculation, cache, dependency-locking and operational findings need separate follow-up.

## Verification

```sh
python -m unittest discover -s tests -p 'test_*.py'
node --test tests/*.test.js
```

Billing behavior follows [Stripe webhook guidance](https://docs.stripe.com/webhooks) and [subscription webhook guidance](https://docs.stripe.com/billing/subscriptions/webhooks).
