# Admin model performance analysis

Analyze Performance starts a background job with `POST /api/admin/model-analysis`.
The dashboard polls the same endpoint with GET and displays the result or an
explanation of why no report is available. Concurrent clicks share the running
job. The current deployment uses one Gunicorn worker; job state is held in that
worker and a restart interrupts the job. The UI detects an interrupted job and
allows a retry.

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

The browser bounds each status request and handles HTML gateway errors without
trying to render them as JSON. Backend exceptions become a failed job, and the
button and loading indicator recover on both success and error.
