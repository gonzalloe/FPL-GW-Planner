# Admin model performance analysis

Analyze Performance starts a background job with `POST /api/admin/model-analysis`.
The dashboard polls the same endpoint with GET and displays the result or an
explanation of why no report is available. Concurrent clicks share the running
job. Starting a job requires normal admin authentication. The response includes
an unpredictable polling token scoped to that job, valid for ten minutes. Status
requests use this token in X-Analysis-Token, avoiding repeated Supabase session
and user queries. It cannot authorise starting a job or other admin actions.
Tokens rotate with a new job and are never included in access-log URLs or normal
status responses. A restart invalidates the token and the UI allows a retry.

Gunicorn uses one gthread worker with four request threads. Job state is held in
that worker; a restart interrupts the job. Both deployment commands load
gunicorn.conf.py explicitly, and Gunicorn also discovers it for the basic
`gunicorn server:app` command. An existing Render service with a custom Start
Command should use `gunicorn --config gunicorn.conf.py server:app`. Explicit
command-line settings on an existing service can override configuration defaults.

Analysis scores saved forecasts against the official per-player results for the
same completed gameweek. It never runs PredictionEngine to manufacture missing
historical forecasts. Forecasts generated at or after the deadline, or with
unverifiable timestamps, are excluded. A finished current gameweek is eligible.
Applying suggested weights regenerates upcoming predictions only; historical
forecasts remain unchanged. The proposed weights are heuristic suggestions,
not demonstrated improvements from a held-out validation set.

Future pre-deadline prediction runs save compact accuracy snapshots through
app_storage, using the existing Supabase app_settings table when configured.
This preserves snapshots when Render clears local files. Without Supabase the
store falls back to local JSON and remains subject to host disk persistence.
Each season has a separate index. Rebuilt local forecasts can be bypassed in
favour of a valid archived forecast. Previously missing or overwritten forecasts
cannot be recovered from results; the UI explains that analysis must wait for
eligible saved forecasts and completed results.

The browser bounds each status request and retries up to three consecutive
transient polling failures while retaining the same job. It handles HTML gateway
errors without trying to render them as JSON. Settings storage uses a five-second
database-operation timeout instead of the SDK's 120-second default. Backend
exceptions become a failed job, and the button and loading indicator recover on
both success and error. Logs include job duration and start-request authentication
time to distinguish analysis delays from request/authentication delays.

When only upcoming forecasts exist, the report is a neutral waiting state that
names their gameweeks. Refresh Data before the relevant deadline, retain the
snapshot, and analyse after that gameweek finishes. The analyser also recognises
a matching latest_predictions.json restored from the cloud cache, even when the
GW-specific local file is absent. Post-deadline forecasts remain excluded.
