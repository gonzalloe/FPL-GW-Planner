"""
FPL Predictor - Main Runner v2
Generates predictions with DGW awareness and chip strategy.
"""
import json
import sys
from pathlib import Path
from datetime import datetime

from prediction_engine import PredictionEngine
from squad_optimizer import SquadOptimizer, ChipAdvisor, TransferAdvisor
from ai_analyst import AIAnalyst


def main():
    target_gw = int(sys.argv[1]) if len(sys.argv) > 1 else None

    print("=" * 60)
    print("  FPL PREDICTOR v2 - AI-Powered Squad Optimizer")
    print("=" * 60)

    # ── Step 1: Fetch data & predict ──
    print("\n[1/5] Fetching FPL data...")
    engine = PredictionEngine()

    gw = target_gw or engine.next_gw
    print(f"  Current GW: {engine.current_gw}")
    print(f"  Predicting for: GW{gw}")

    # ── Step 2: GW Info (DGW/BGW detection) ──
    print("\n[2/5] Analyzing gameweek...")
    gw_info = engine.get_gw_info(gw)

    if gw_info["is_dgw"]:
        dgw_teams = gw_info["dgw_teams"]
        print(f"  *** DOUBLE GAMEWEEK detected! ***")
        print(f"  {gw_info['total_fixtures']} total fixtures")
        print(f"  DGW teams ({len(dgw_teams)}):")
        for tid, info in dgw_teams.items():
            print(f"    - {info['name']} ({info['fixture_count']} fixtures)")
    else:
        print(f"  Standard gameweek ({gw_info['total_fixtures']} fixtures)")

    bgw = gw_info.get("bgw_teams", {})
    if bgw:
        print(f"  BGW teams ({len(bgw)}): {', '.join(bgw.values())}")

    # ── Step 3: Predict all players ──
    print("\n[3/5] Running predictions for all players...")
    predictions = engine.predict_all(gw)
    print(f"  {len(predictions)} players analyzed")

    dgw_players = [p for p in predictions if p.get("is_dgw")]
    nailed = [p for p in predictions if p.get("starter_quality", {}).get("tier") == "nailed"]
    print(f"  {len(dgw_players)} DGW players (play twice)")
    print(f"  {len(nailed)} nailed starters")

    # ── Step 4: Optimize squad ──
    print("\n[4/5] Optimizing squad...")
    optimizer = SquadOptimizer(predictions)
    squad = optimizer.optimize_squad()

    print(f"\n  OPTIMAL SQUAD (GW{gw})")
    print(f"  Formation: {squad['formation']}")
    print(f"  Cost: {squad['total_cost']}m / Budget: {engine.budget if hasattr(engine, 'budget') else 100.0}m")
    print(f"  DGW players: {squad['dgw_players']} (in XI: {squad['dgw_in_xi']})")
    print(f"  Predicted points: {squad['predicted_total_points']}")

    print(f"\n  --- Starting XI ---")
    for p in squad["starting_xi"]:
        dgw_tag = " [DGW]" if p.get("is_dgw") else ""
        fixtures_str = " + ".join(
            f"{f['opponent']}({f['venue']})" for f in p.get("fixtures", [])
        )
        tier = p.get("starter_quality", {}).get("tier", "?")
        print(f"    {p['position']:3s} {p['name']:20s} {p['team']:4s} | xPts={p['predicted_points']:5.1f} | {p['price']}m | {tier} | {fixtures_str}{dgw_tag}")

    if squad["captain"]:
        print(f"\n  Captain: {squad['captain']['name']} ({squad['captain']['predicted_points']:.1f} xPts)")
    if squad["vice_captain"]:
        print(f"  Vice:    {squad['vice_captain']['name']} ({squad['vice_captain']['predicted_points']:.1f} xPts)")

    print(f"\n  --- Bench ---")
    for p in squad["bench"]:
        dgw_tag = " [DGW]" if p.get("is_dgw") else ""
        print(f"    {p['position']:3s} {p['name']:20s} {p['team']:4s} | xPts={p['predicted_points']:5.1f} | {p['price']}m{dgw_tag}")

    # ── Step 5: Chip Strategy ──
    print("\n[5/5] Analyzing chip strategy...")
    chip_advisor = ChipAdvisor(predictions, gw_info)
    chip_analysis = chip_advisor.analyze()

    print(f"\n  CHIP RECOMMENDATIONS (GW{gw})")
    if chip_analysis.get("save_chips"):
        print("  >> Standard GW - consider saving chips for a DGW")
    else:
        for rec in chip_analysis["recommendations"]:
            emoji = {"BB": "BB", "TC": "TC", "FH": "FH", "WC": "WC"}.get(rec["code"], "?")
            print(f"\n  [{emoji}] {rec['name']} (Score: {rec['score']}/100)")
            for reason in rec["reasons"]:
                print(f"      - {reason}")
            if "predicted_total" in rec:
                print(f"      => Predicted total: {rec['predicted_total']:.1f} pts")
            if "captain_xp" in rec:
                print(f"      => Captain extra: +{rec['extra_points']:.1f} pts")

    best_chip = chip_analysis.get("best_chip")
    if best_chip and best_chip["score"] >= 50:
        print(f"\n  >>> RECOMMENDED: Use {best_chip['name']} ({best_chip['code']}) this GW! <<<")

    # ── Save output ──
    output_dir = Path(__file__).parent / "output"
    output_dir.mkdir(exist_ok=True)

    output = {
        "generated_at": datetime.now().isoformat(),
        "gameweek": gw,
        "gw_info": gw_info,
        "predictions": predictions,  # ALL players
        "squad": squad,
        "chip_analysis": chip_analysis,
        "top_picks": predictions[:30],
        "differentials": [
            p for p in predictions
            if float(p.get("selected_by_percent", 0)) < 10
            and p.get("starter_quality", {}).get("tier") in ("nailed", "regular")
        ][:15],
        "value_picks": sorted(
            [p for p in predictions
             if p.get("price", 99) <= 6.5
             and p.get("starter_quality", {}).get("tier") in ("nailed", "regular")],
            key=lambda x: x["predicted_points"] / max(x.get("price", 4), 3.5),
            reverse=True
        )[:15],
    }

    filename = output_dir / f"gw{gw}_predictions.json"
    filename.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  Saved to {filename}")

    # ── Top 10 overall ──
    print(f"\n{'='*60}")
    print(f"  TOP 10 PICKS FOR GW{gw}")
    print(f"{'='*60}")
    for i, p in enumerate(predictions[:10]):
        dgw = "DGW " if p.get("is_dgw") else "    "
        tier = p.get("starter_quality", {}).get("tier", "?")[:6]
        fixtures_str = " + ".join(
            f"{f['opponent']}({f['venue']})" for f in p.get("fixtures", [])
        )
        print(f"  {i+1:2d}. {dgw}{p['name']:20s} {p['team']:4s} {p['position']:3s} | xPts={p['predicted_points']:5.1f} | {p['price']:4.1f}m | {tier:6s} | {fixtures_str}")

    print(f"\nDone!")


if __name__ == "__main__":
    main()
