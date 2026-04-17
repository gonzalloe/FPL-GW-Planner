"""
FPL Predictor - Season-Wide Chip Planner
Scans all remaining GWs to find the best gameweek for each chip.
"""
from data_fetcher import (
    fetch_bootstrap, fetch_fixtures, build_player_map, build_team_map,
    get_next_gameweek, get_dgw_teams, get_bgw_teams,
    get_fixtures_for_gameweek, get_player_fixtures
)
from prediction_engine import PredictionEngine
from squad_optimizer import SquadOptimizer


class SeasonChipPlanner:
    """
    Analyzes ALL remaining GWs to find the optimal deployment for each chip.
    Considers DGWs, BGWs, fixture swings, and squad composition.
    """

    def __init__(self):
        self.engine = PredictionEngine()
        self.bootstrap = self.engine.bootstrap
        self.fixtures = self.engine.fixtures
        self.teams = self.engine.teams
        self.next_gw = self.engine.next_gw
        self._cache = {}

    def _get_gw_predictions(self, gw):
        if gw not in self._cache:
            self._cache[gw] = {
                "predictions": self.engine.predict_all(gw),
                "gw_info": self.engine.get_gw_info(gw),
            }
        return self._cache[gw]

    def analyze_season(self, chips_available=None, current_squad_ids=None, bank=0.0):
        """
        Scan all remaining GWs and score each chip for every GW.
        Returns the optimal GW for each chip + season-wide heatmap.
        """
        if chips_available is None:
            chips_available = ["BB", "TC", "FH", "WC"]

        max_gw = 38
        remaining_gws = list(range(self.next_gw, max_gw + 1))

        # Build GW metadata for all remaining weeks
        gw_meta = {}
        for gw in remaining_gws:
            fixes = get_fixtures_for_gameweek(gw, self.fixtures)
            dgw = get_dgw_teams(gw, self.fixtures)
            bgw = get_bgw_teams(gw, self.fixtures, self.bootstrap)
            gw_meta[gw] = {
                "gameweek": gw,
                "total_fixtures": len(fixes),
                "is_dgw": len(dgw) > 0,
                "dgw_team_count": len(dgw),
                "dgw_teams": {tid: self.teams.get(tid, {}).get("short_name", "?")
                              for tid in dgw},
                "is_bgw": len(bgw) > 0,
                "bgw_team_count": len(bgw),
                "bgw_teams": {tid: self.teams.get(tid, {}).get("short_name", "?")
                              for tid in bgw},
            }

        # Score each chip for each GW
        chip_scores = {chip: [] for chip in chips_available}

        for gw in remaining_gws:
            meta = gw_meta[gw]

            # Only compute detailed predictions for GWs with DGW/BGW or nearby
            # For efficiency, use lightweight scoring for most GWs
            detailed = meta["is_dgw"] or meta["is_bgw"] or meta["total_fixtures"] < 8

            if detailed:
                try:
                    data = self._get_gw_predictions(gw)
                    predictions = data["predictions"]
                    gw_info = data["gw_info"]
                except Exception:
                    predictions = []
                    gw_info = meta
            else:
                predictions = []
                gw_info = meta

            for chip in chips_available:
                score_data = self._score_chip_for_gw(
                    chip, gw, meta, predictions, gw_info,
                    current_squad_ids, bank
                )
                chip_scores[chip].append(score_data)

        # Find best GW for each chip
        best_gws = {}
        for chip in chips_available:
            scores = chip_scores[chip]
            best = max(scores, key=lambda x: x["score"])
            top_3 = sorted(scores, key=lambda x: x["score"], reverse=True)[:3]
            best_gws[chip] = {
                "best_gw": best["gameweek"],
                "best_score": best["score"],
                "best_reason": best["reason"],
                "top_3": top_3,
                "all_scores": scores,
            }

        # Build recommended chip sequence
        sequence = self._build_chip_sequence(best_gws, chips_available, gw_meta)

        return {
            "from_gw": self.next_gw,
            "to_gw": max_gw,
            "remaining_gws": len(remaining_gws),
            "gw_metadata": gw_meta,
            "chip_analysis": best_gws,
            "recommended_sequence": sequence,
            "chips_available": chips_available,
        }

    def _score_chip_for_gw(self, chip, gw, meta, predictions, gw_info,
                           current_squad_ids, bank):
        """Score a specific chip for a specific GW."""
        score = 0
        reason = ""
        details = {}

        if chip == "BB":
            score, reason, details = self._score_bb(gw, meta, predictions, gw_info)
        elif chip == "TC":
            score, reason, details = self._score_tc(gw, meta, predictions)
        elif chip == "FH":
            score, reason, details = self._score_fh(gw, meta, predictions, current_squad_ids)
        elif chip == "WC":
            score, reason, details = self._score_wc(gw, meta, predictions, current_squad_ids)

        return {
            "gameweek": gw,
            "score": min(100, max(0, score)),
            "reason": reason,
            "is_dgw": meta["is_dgw"],
            "is_bgw": meta["is_bgw"],
            "dgw_teams": meta.get("dgw_team_count", 0),
            "fixtures": meta["total_fixtures"],
            **details,
        }

    def _score_bb(self, gw, meta, predictions, gw_info):
        """Score Bench Boost for a GW. Best in large DGWs with strong bench."""
        score = 0
        reasons = []
        details = {}

        # DGW is crucial for BB
        if meta["is_dgw"]:
            dgw_count = meta["dgw_team_count"]
            score += min(40, dgw_count * 7)
            reasons.append(f"{dgw_count} DGW teams")

            # Check bench quality if we have predictions
            if predictions:
                optimizer = SquadOptimizer(predictions)
                bb_squad = optimizer.optimize_squad(chip="bench_boost")
                bench_xp = sum(p["predicted_points"] for p in bb_squad.get("bench", []))
                bench_dgw = sum(1 for p in bb_squad.get("bench", []) if p.get("is_dgw"))
                details["bench_xpts"] = round(bench_xp, 1)
                details["bench_dgw_count"] = bench_dgw

                if bench_xp >= 20:
                    score += 30
                    reasons.append(f"Strong bench ({bench_xp:.0f} xPts)")
                elif bench_xp >= 12:
                    score += 15
                    reasons.append(f"Decent bench ({bench_xp:.0f} xPts)")

                if bench_dgw >= 3:
                    score += 20
                    reasons.append(f"{bench_dgw}/4 bench have DGW")
        else:
            # Non-DGW BB is weak
            score += 5
            reasons.append("No DGW — BB less effective")

        if meta["total_fixtures"] >= 12:
            score += 10
            reasons.append(f"{meta['total_fixtures']} fixtures")

        return score, " · ".join(reasons), details

    def _score_tc(self, gw, meta, predictions):
        """Score Triple Captain for a GW. Best when premium has easy DGW."""
        score = 0
        reasons = []
        details = {}

        if predictions:
            top = predictions[0]
            xp = top["predicted_points"]
            details["best_captain"] = top["name"]
            details["captain_xpts"] = round(xp, 1)

            if top.get("is_dgw"):
                score += 35
                reasons.append(f"{top['name']} DGW ({xp:.1f} xPts)")
                if xp >= 15:
                    score += 30
                    reasons.append("Elite xPts (15+)")
                elif xp >= 10:
                    score += 15
                    reasons.append("Strong xPts (10+)")
            else:
                if xp >= 12:
                    score += 20
                    reasons.append(f"{top['name']} SGW ({xp:.1f} xPts)")
                else:
                    score += 5
                    reasons.append(f"Best captain only {xp:.1f} xPts")

            # Easy fixtures bonus
            easy = sum(1 for f in top.get("fixtures", []) if f.get("fdr", 3) <= 2)
            if easy >= 1:
                score += 10
                reasons.append(f"{easy} easy fixture(s)")

            if top.get("starter_quality", {}).get("tier") == "nailed":
                score += 10
                reasons.append("Nailed")
        elif meta["is_dgw"]:
            score += 20
            reasons.append(f"DGW ({meta['dgw_team_count']} teams)")
        else:
            score += 5
            reasons.append("Standard GW")

        return score, " · ".join(reasons), details

    def _score_fh(self, gw, meta, predictions, current_squad_ids):
        """Score Free Hit for a GW. Best for BGWs or one-off DGWs."""
        score = 0
        reasons = []
        details = {}

        # BGW is the primary FH trigger
        if meta["is_bgw"]:
            bgw_count = meta["bgw_team_count"]
            score += min(60, bgw_count * 8)
            reasons.append(f"BGW: {bgw_count} teams missing")

            # If many squad players blank
            if current_squad_ids and predictions:
                pred_map = {p["player_id"]: p for p in predictions}
                blanking = sum(1 for pid in current_squad_ids
                               if pred_map.get(pid, {}).get("num_fixtures", 0) == 0)
                if blanking >= 5:
                    score += 30
                    reasons.append(f"{blanking} squad players blank")
                elif blanking >= 3:
                    score += 15
                    reasons.append(f"{blanking} squad players blank")
                details["blanking_players"] = blanking

        elif meta["total_fixtures"] < 8:
            score += 30
            reasons.append(f"Only {meta['total_fixtures']} fixtures")

        # One-off DGW where squad has low DGW exposure
        if meta["is_dgw"] and not meta["is_bgw"]:
            score += 15
            reasons.append(f"DGW opportunity ({meta['dgw_team_count']} teams)")

        return score, " · ".join(reasons), details

    def _score_wc(self, gw, meta, predictions, current_squad_ids):
        """Score Wildcard for a GW. Best before a big DGW."""
        score = 0
        reasons = []
        details = {}

        # Check if NEXT GW is a big DGW (WC before DGW + BB)
        next_gw_meta = None
        for future_gw in range(gw + 1, min(gw + 3, 39)):
            future_fixes = get_fixtures_for_gameweek(future_gw, self.fixtures)
            future_dgw = get_dgw_teams(future_gw, self.fixtures)
            if len(future_dgw) >= 4:
                next_gw_meta = {
                    "gw": future_gw,
                    "dgw_count": len(future_dgw),
                }
                break

        if next_gw_meta:
            score += min(50, next_gw_meta["dgw_count"] * 8)
            reasons.append(f"GW{next_gw_meta['gw']} has {next_gw_meta['dgw_count']}-team DGW ahead")
            reasons.append("WC to build DGW squad → BB next week")
            details["target_dgw"] = next_gw_meta["gw"]

        # Major fixture swing
        if meta["is_dgw"]:
            score += 10
            reasons.append("Fixture swing opportunity")

        # Late-season WC
        if gw >= 30:
            score += 5
            reasons.append("Late season — WC for final push")

        if not reasons:
            reasons.append("No strong WC trigger — save for later")

        return score, " · ".join(reasons), details

    def _build_chip_sequence(self, best_gws, chips_available, gw_meta):
        """Build a recommended chip deployment sequence avoiding conflicts."""
        sequence = []
        used_gws = set()

        # Sort chips by best score (deploy highest-confidence first)
        sorted_chips = sorted(
            chips_available,
            key=lambda c: best_gws[c]["best_score"],
            reverse=True
        )

        for chip in sorted_chips:
            analysis = best_gws[chip]
            # Find best available GW (not already used)
            for candidate in analysis["top_3"]:
                gw = candidate["gameweek"]
                if gw not in used_gws:
                    sequence.append({
                        "chip": chip,
                        "gameweek": gw,
                        "score": candidate["score"],
                        "reason": candidate["reason"],
                        "is_dgw": gw_meta.get(gw, {}).get("is_dgw", False),
                    })
                    used_gws.add(gw)
                    break

        sequence.sort(key=lambda x: x["gameweek"])
        return sequence
