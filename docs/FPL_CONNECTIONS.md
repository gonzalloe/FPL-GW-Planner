# Team-ID import and planning

Import a team using its public FPL Team ID. FPL access tokens and a private account connection are no longer required. The application login and saved team IDs remain scoped to each app user.

Free transfers are calculated from completed gameweek history, capped at five. Wildcard and Free Hit preserve the existing bank without awarding another transfer. Early transfers in the planning gameweek are not subtracted. The public API cannot guarantee the current private balance, squad or bank before the next deadline.

The planner uses cached predictions and lightweight fixture projections so it can run during a background refresh. A cache for the upcoming gameweek must exist. Errors from the server are shown in the dashboard.

`requirements.txt` pins PuLP 3.3.0, which supplies the CBC interface used by the existing optimizer. Render installs it during the normal build. If the solver is unavailable, a bounded beam search provides a valid constrained squad where it finds one, rather than calling missing methods.

No FPL_CONNECTION_KEY or FPL-connection database migration is required for this workflow. An existing fpl_connections table is unused; this change does not delete stored database data. The old migration file is retained as schema history.

Checks:

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
```
