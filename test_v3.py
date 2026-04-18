"""Quick test of prediction engine v3 + optimizer v3."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from prediction_engine import PredictionEngine
from squad_optimizer import SquadOptimizer

e = PredictionEngine()
p = e.predict_all()
print(f"Predicted {len(p)} players")

# Show top 5 with new team data
print("\n=== TOP 5 PLAYERS ===")
for x in p[:5]:
    fx = x.get("fixtures", [{}])
    fix_str = " + ".join(f"{f.get('opponent','?')}({f.get('venue','?')}) xG:{f.get('fixture_xg',0)} xGC:{f.get('fixture_xgc',0)}" for f in fx)
    print(f"  {x['name']:18} {x['team']:4} {x['predicted_points']:6.2f} xPts | {x['position']} | Form:{x['form']:.1f} | TeamL5:{x.get('team_last5_form','')} WR:{x.get('team_season_wr',0):.0%} Mom:{x.get('team_momentum',0):.2f}")
    print(f"    Fixtures: {fix_str}")

o = SquadOptimizer(p)
sq = o.optimize_squad()

print(f"\n=== OPTIMAL SQUAD ({sq['formation']}) ===")
print(f"Total cost: {sq['total_cost']}m | Budget left: {sq['budget_remaining']}m")
print(f"Squad total xPts: {sq['squad_total_xpts']} | XI predicted: {sq['predicted_total_points']}")
print(f"DGW: {sq['dgw_players']}/15 squad, {sq['dgw_in_xi']}/11 XI")

c = sq.get("captain", {})
v = sq.get("vice_captain", {})
print(f"\nCaptain: {c.get('name','?')} ({c.get('predicted_points',0)} xPts)")
print(f"Vice:    {v.get('name','?')} ({v.get('predicted_points',0)} xPts)")

print("\n--- Starting XI ---")
for x in sq["starting_xi"]:
    dgw = "DGW" if x.get("is_dgw") else "SGW"
    tier = x.get("starter_quality", {}).get("tier", "?")
    avail = x.get("availability", {})
    flag = ""
    if avail.get("status") == "doubtful":
        flag = f" ⚠{avail.get('chance',50)}%"
    l5 = x.get("team_last5_form", "")
    wr = x.get("team_season_wr", 0)
    print(f"  {x['position']:3} {x['name']:18} {x['team']:4} {x['predicted_points']:6.2f}xP  {dgw}  {tier:10}{flag}  L5:{l5} WR:{wr:.0%}")

print("\n--- Bench ---")
for x in sq["bench"]:
    dgw = "DGW" if x.get("is_dgw") else "SGW"
    print(f"  {x['position']:3} {x['name']:18} {x['team']:4} {x['predicted_points']:6.2f}xP  {dgw}")

# Verify constraints
total_cost = sum(x.get("price", 0) for x in sq["squad"])
team_counts = {}
for x in sq["squad"]:
    tid = x.get("team_id", x.get("team", 0))
    team_counts[tid] = team_counts.get(tid, 0) + 1

pos_counts = {}
for x in sq["squad"]:
    pid = x.get("position_id", 0)
    pos_counts[pid] = pos_counts.get(pid, 0) + 1

print(f"\n=== CONSTRAINT CHECK ===")
print(f"Budget: {total_cost:.1f} / 100.0 {'✓' if total_cost <= 100.0 else '✗'}")
print(f"Squad size: {len(sq['squad'])} {'✓' if len(sq['squad']) == 15 else '✗'}")
print(f"Max team: {max(team_counts.values())} {'✓' if max(team_counts.values()) <= 3 else '✗'}")
print(f"Positions: GKP={pos_counts.get(1,0)} DEF={pos_counts.get(2,0)} MID={pos_counts.get(3,0)} FWD={pos_counts.get(4,0)}")
print(f"XI size: {len(sq['starting_xi'])} {'✓' if len(sq['starting_xi']) == 11 else '✗'}")
