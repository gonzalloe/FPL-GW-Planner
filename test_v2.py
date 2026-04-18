"""Test v2 prediction engine"""
from prediction_engine import PredictionEngine
from squad_optimizer import SquadOptimizer, ChipAdvisor

e = PredictionEngine()
preds = e.predict_all()
gw = e.get_gw_info()

print(f"GW{gw['gameweek']}: {gw['total_fixtures']} fixtures, DGW={gw['is_dgw']}, DGW teams={len(gw['dgw_teams'])}")
for tid, info in gw['dgw_teams'].items():
    print(f"  {info['name']}: {info['fixture_count']} fixtures")

print(f"\nPlayers predicted: {len(preds)}")

print(f"\n{'='*90}")
print(f"TOP 20 PICKS FOR GW{gw['gameweek']}")
print(f"{'='*90}")
for i, p in enumerate(preds[:20]):
    dgw = "DGW" if p["is_dgw"] else "   "
    tier = p["starter_quality"]["tier"][:6]
    fix_parts = []
    for f in p["fixtures"]:
        fix_parts.append(f"{f['opponent']}({f['venue']})")
    fix_str = " + ".join(fix_parts)
    print(f"  {i+1:2d}. [{dgw}] {p['name']:18s} {p['team']:4s} {p['position']:3s} | xPts={p['predicted_points']:5.1f} | {p['price']:5.1f}m | {tier:6s} | starts={p['starts']:2d} | mins={p['minutes']:4d} | {fix_str}")

# Squad optimizer
print(f"\n{'='*90}")
print(f"OPTIMAL SQUAD")
print(f"{'='*90}")
opt = SquadOptimizer(preds)
squad = opt.optimize_squad()
print(f"Formation: {squad['formation']} | Cost: {squad['total_cost']}m | DGW in XI: {squad['dgw_in_xi']}")
print(f"Predicted total: {squad['predicted_total_points']} pts")
print(f"\nStarting XI:")
for p in squad["starting_xi"]:
    dgw = "DGW" if p.get("is_dgw") else "   "
    fix_parts = [f"{f['opponent']}({f['venue']})" for f in p.get("fixtures", [])]
    print(f"  [{dgw}] {p['position']:3s} {p['name']:18s} {p['team']:4s} | xPts={p['predicted_points']:5.1f} | {p['price']}m | {p['starter_quality']['tier']}")
print(f"\nCaptain: {squad['captain']['name']} ({squad['captain']['predicted_points']:.1f} xPts)")
print(f"Vice:    {squad['vice_captain']['name']} ({squad['vice_captain']['predicted_points']:.1f} xPts)")
print(f"\nBench:")
for p in squad["bench"]:
    dgw = "DGW" if p.get("is_dgw") else "   "
    print(f"  [{dgw}] {p['position']:3s} {p['name']:18s} {p['team']:4s} | xPts={p['predicted_points']:5.1f} | {p['price']}m | {p['starter_quality']['tier']}")

# Chip analysis
print(f"\n{'='*90}")
print(f"CHIP STRATEGY")
print(f"{'='*90}")
chip = ChipAdvisor(preds, gw)
analysis = chip.analyze()
for rec in analysis["recommendations"]:
    print(f"\n[{rec['code']}] {rec['name']} (Score: {rec['score']}/100)")
    for r in rec["reasons"]:
        print(f"    - {r}")
    if "predicted_total" in rec:
        print(f"    => Total with BB: {rec['predicted_total']:.1f} pts")
    if "captain_xp" in rec:
        print(f"    => Captain: {rec['captain']} ({rec['captain_xp']:.1f} xPts, +{rec['extra_points']:.1f} from TC)")

best = analysis.get("best_chip")
if best and best["score"] >= 30:
    print(f"\n>>> RECOMMENDATION: {best['name']} ({best['code']}) <<<")
