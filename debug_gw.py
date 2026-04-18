"""Debug: check GW33 fixtures and DGW teams"""
from data_fetcher import fetch_fixtures, fetch_bootstrap, get_next_gameweek, build_team_map, build_player_map

bs = fetch_bootstrap()
gw = get_next_gameweek(bs)
fixtures = fetch_fixtures()
teams = build_team_map(bs)
players = build_player_map(bs)

gw_fix = [f for f in fixtures if f.get("event") == gw]
print(f"Next GW: {gw}")
print(f"Total fixtures in GW{gw}: {len(gw_fix)}")
print()

# Track which teams play more than once
team_fixture_count = {}
for f in gw_fix:
    th = f["team_h"]
    ta = f["team_a"]
    team_fixture_count[th] = team_fixture_count.get(th, 0) + 1
    team_fixture_count[ta] = team_fixture_count.get(ta, 0) + 1
    th_name = teams.get(th, {}).get("short_name", "???")
    ta_name = teams.get(ta, {}).get("short_name", "???")
    print(f"  {th_name} vs {ta_name}")

print()
dgw_teams = {tid for tid, cnt in team_fixture_count.items() if cnt > 1}
if dgw_teams:
    print(f"DGW teams ({len(dgw_teams)}):")
    for tid in sorted(dgw_teams):
        name = teams.get(tid, {}).get("name", "???")
        cnt = team_fixture_count[tid]
        print(f"  {name}: {cnt} fixtures")
else:
    print("No DGW teams found (all teams play once)")

# Check what get_player_fixture returns for a DGW team
if dgw_teams:
    sample_tid = list(dgw_teams)[0]
    from data_fetcher import get_player_fixture
    result = get_player_fixture(sample_tid, gw, fixtures)
    print(f"\nget_player_fixture for team {teams.get(sample_tid, {}).get('name')}: returns ONLY 1 fixture")
    print(f"  Result: opponent={teams.get(result['opponent_id'],{}).get('short_name')}, home={result['is_home']}")

# Check top predicted players - are they starters?
print("\n--- Top 15 predicted players (current engine output) ---")
from prediction_engine import PredictionEngine
engine = PredictionEngine()
preds = engine.predict_all(gw)
for i, p in enumerate(preds[:15]):
    pid = p["player_id"]
    raw = players.get(pid, {})
    minutes = raw.get("minutes", 0)
    starts = raw.get("starts", 0)
    form = raw.get("form", "0")
    status = raw.get("status", "?")
    sel = raw.get("selected_by_percent", "0")
    print(f"  {i+1}. {p['name']} ({p['team']}, {p['position']}) | xPts={p['predicted_points']} | price={p['price']} | minutes={minutes} | starts={starts} | form={form} | sel%={sel} | status={status}")
