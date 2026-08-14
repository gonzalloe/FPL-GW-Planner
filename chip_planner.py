"""
FPL Predictor - Season-Wide Chip Planner
Scans all remaining GWs to find the best gameweek for each chip.
"""
from data_fetcher import (get_dgw_teams, get_bgw_teams, get_fixtures_for_gameweek, get_player_fixtures)

class SeasonChipPlanner:
    """
    Analyzes ALL remaining GWs to find the optimal deployment for each chip.
    Considers DGWs, BGWs, fixture swings, and squad composition.
    """

    def __init__(self, engine):
        self.engine = engine
        self.bootstrap = engine.bootstrap
        self.fixtures = engine.fixtures
        self.teams = engine.teams
        self.next_gw = engine.next_gw
        self._baseline_predictions = getattr(
            engine,
            "_baseline_predictions",
            []
        )

    def _lightweight_fixture_ease(self, gw, player_ids):
        if not player_ids:
            return 0.0
        total = 0.0
        count = 0
        for pid in player_ids:
            player = self.engine.players.get(pid)
            if not player:
                continue
            team_id = player.get("team")
            if not team_id:
                continue
            fixtures = get_player_fixtures(
                team_id,
                gw,
                self.fixtures
            )
            for fixture in fixtures:
                fdr = fixture.get("fdr", 3)

                # FDR 3 = neutral
                # FDR 1/2 = positive
                # FDR 4/5 = negative
                total += 3 - fdr
                count += 1

        return total / count if count else 0.0

    def analyze_season(self, chips_available=None, current_squad_ids=None, bank=0.0):
        """
        Scan all remaining GWs and score each chip for every GW.

        Performance strategy:
        - Run predict_all() ONCE for baseline player projections.
        - Run predict_all() only for genuine DGWs.
        - Normal GWs use lightweight fixture adjustments.
        - BGWs do not need full prediction generation.
        """
        print(
            f"[CHIP] Analyze season: GW{self.next_gw}-38 | "
            f"squad={len(current_squad_ids) if current_squad_ids else 0}"
        )
        if chips_available is None:
            chips_available = ["BB", "TC", "FH", "WC"]

        max_gw = 38
        remaining_gws = list(range(self.next_gw, max_gw + 1))

        # ------------------------------------------------------------
        # Build GW metadata
        # ------------------------------------------------------------
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
                "dgw_teams": {
                    tid: self.teams.get(tid, {}).get("short_name", "?")
                    for tid in dgw
                },

                "is_bgw": len(bgw) > 0,
                "bgw_team_count": len(bgw),
                "bgw_teams": {
                    tid: self.teams.get(tid, {}).get("short_name", "?")
                    for tid in bgw
                },
            }

        # ------------------------------------------------------------
        # Score every chip for every GW
        # ------------------------------------------------------------
        chip_scores = {
            chip: []
            for chip in chips_available
        }

        for gw in remaining_gws:
            meta = gw_meta[gw]    
            predictions = []
            gw_info = meta

            for chip in chips_available:
                score_data = self._score_chip_for_gw(
                    chip,
                    gw,
                    meta,
                    predictions,
                    gw_info,
                    current_squad_ids,
                    bank,
                )

                chip_scores[chip].append(score_data)

        # ------------------------------------------------------------
        # Find best GW for each chip
        # ------------------------------------------------------------
        best_gws = {}

        for chip in chips_available:
            scores = chip_scores[chip]

            if not scores:
                continue

            best = max(
                scores,
                key=lambda x: x["score"]
            )

            top_3 = sorted(
                scores,
                key=lambda x: x["score"],
                reverse=True
            )[:3]

            best_gws[chip] = {
                "best_gw": best["gameweek"],
                "best_score": best["score"],
                "best_reason": best["reason"],
                "top_3": top_3,
                "all_scores": scores,
            }

        # ------------------------------------------------------------
        # Build recommended chip sequence
        # ------------------------------------------------------------
        sequence = self._build_chip_sequence(
            best_gws,
            chips_available,
            gw_meta,
        )

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
            score, reason, details = self._score_bb(gw, meta, predictions, gw_info, current_squad_ids)
        elif chip == "TC":
            score, reason, details = self._score_tc(gw, meta, predictions, current_squad_ids)
        elif chip == "FH":
            score, reason, details = self._score_fh(gw, meta, predictions, current_squad_ids)
        elif chip == "WC":
            score, reason, details = self._score_wc(gw, meta, predictions, current_squad_ids)

        if chip in ("BB", "TC"):
            print(
                f"[CHIP DEBUG] GW{gw} {chip}: "
                f"{min(100, max(0, score))} | {reason}"
            )    

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

    def _score_bb(self, gw, meta, predictions, gw_info, current_squad_ids=None):
        """Score Bench Boost for a GW."""
        score = 0
        reasons = []
        details = {}

        # ------------------------------------------------------------
        # DGW strength
        # ------------------------------------------------------------
        if meta["is_dgw"]:
            dgw_count = meta["dgw_team_count"]

            score += min(40, dgw_count * 7)
            reasons.append(f"{dgw_count} DGW teams")
            details["dgw_team_count"] = dgw_count

        # ------------------------------------------------------------
        # Current squad / bench quality
        # ------------------------------------------------------------
        baseline = getattr(
            self.engine,
            "_baseline_predictions",
            []
        )

        if current_squad_ids and baseline:
            pred_map = {
                p["player_id"]: p
                for p in baseline
            }

            squad_preds = [
                pred_map[pid]
                for pid in current_squad_ids
                if pid in pred_map
            ]

            squad_preds.sort(
                key=lambda p: p.get("predicted_points", 0),
                reverse=True
            )

            # FPL squad = 15 players.
            # Approximation: top 11 = starters, remaining 4 = bench.
            bench_preds = squad_preds[11:15]

            bench_xpts = sum(
                p.get("predicted_points", 0)
                for p in bench_preds
            )

            bench_dgw = sum(
                1
                for p in bench_preds
                if p.get("is_dgw")
            )

            details["bench_xpts"] = round(bench_xpts, 1)
            details["bench_dgw_count"] = bench_dgw

            if bench_xpts >= 20:
                score += 30
                reasons.append(
                    f"Strong bench ({bench_xpts:.1f} xPts)"
                )
            elif bench_xpts >= 15:
                score += 20
                reasons.append(
                    f"Good bench ({bench_xpts:.1f} xPts)"
                )
            elif bench_xpts >= 10:
                score += 10
                reasons.append(
                    f"Decent bench ({bench_xpts:.1f} xPts)"
                )

            if bench_dgw >= 3:
                score += 20
                reasons.append(
                    f"{bench_dgw}/4 bench have DGW"
                )
            elif bench_dgw >= 2:
                score += 10
                reasons.append(
                    f"{bench_dgw}/4 bench have DGW"
                )

        elif not current_squad_ids:
            reasons.append("No current squad")

        elif not baseline:
            reasons.append("No baseline predictions")

        # ------------------------------------------------------------
        # Large fixture count
        # ------------------------------------------------------------
        if meta["total_fixtures"] >= 12:
            score += 10
            reasons.append(
                f"{meta['total_fixtures']} fixtures"
            )
        return score, " · ".join(reasons), details


    def _score_tc(self, gw, meta, predictions, current_squad_ids=None):
        """Score Triple Captain for a GW."""
        score = 0
        reasons = []
        details = {}

        # ------------------------------------------------------------
        # DGW
        # No predict_all() here. Use DGW strength only.
        # ------------------------------------------------------------
        if meta["is_dgw"]:
            dgw_count = meta["dgw_team_count"]

            score += min(45, dgw_count * 9)

            reasons.append(
                f"DGW: {dgw_count} teams"
            )

            if dgw_count >= 6:
                score += 20
                reasons.append("Large DGW")
            elif dgw_count >= 4:
                score += 10
                reasons.append("Strong DGW")

            return score, " · ".join(reasons), details

        # ------------------------------------------------------------
        # Normal GW
        # ------------------------------------------------------------
        if not current_squad_ids:
            return 5, "Standard GW — no squad data", details

        # IMPORTANT:
        # Do not call predict_all() here. Only use predictions if they were already supplied/cached.
        candidates = getattr(
            self.engine,
            "_baseline_predictions",
            []
        )

        if not candidates:
            return 5, "Standard GW — no baseline data", details

        baseline_by_id = {
            p["player_id"]: p
            for p in candidates
        }

        squad_candidates = [
            baseline_by_id[pid]
            for pid in current_squad_ids
            if pid in baseline_by_id
        ]

        if not squad_candidates:
            return 5, "Standard GW — no captain candidates", details

        best = None
        best_xp = -1

        for player in squad_candidates:
            fixtures = get_player_fixtures(
                self.engine.players.get(player["player_id"], {}).get("team"),
                gw,
                self.fixtures
            )

            if not fixtures:
                adjusted_xp = player.get("predicted_points", 0.0)
            else:
                # Average fixture adjustment.
                avg_fdr = sum(
                    f.get("fdr", 3)
                    for f in fixtures
                ) / len(fixtures)

                # Small, bounded fixture adjustment.
                multiplier = 1.0 + ((3.0 - avg_fdr) * 0.05)

                adjusted_xp = (
                    player.get("predicted_points", 0.0)
                    * multiplier
                )

            if adjusted_xp > best_xp:
                best_xp = adjusted_xp
                best = player

        if best is None:
            return 5, "Standard GW — no captain candidate", details

        details["best_captain"] = best["name"]
        details["captain_xpts"] = round(best_xp, 1)

        if best_xp >= 15:
            score += 20
        elif best_xp >= 12:
            score += 12
        elif best_xp >= 10:
            score += 8
        else:
            score += 5

        reasons.append(
            f"{best['name']} ~{best_xp:.1f} xPts"
        )

        return score, " · ".join(reasons), details


    def _score_fh(self, gw, meta, predictions, current_squad_ids):
        """Score Free Hit for a GW. Best for BGWs or one-off DGWs."""
        score = 0
        reasons = []
        details = {}

        # BGW
        if meta["is_bgw"]:
            bgw_count = meta["bgw_team_count"]

            score += min(60, bgw_count * 8)

            reasons.append(
                f"BGW: {bgw_count} teams missing"
            )

            # Count how many current squad players have no fixture.
            # This uses fixture data directly and avoids predict_all().
            if current_squad_ids:
                blanking = 0

                for pid in current_squad_ids:
                    player = self.engine.players.get(pid)

                    if not player:
                        continue

                    team_id = player.get("team")

                    if not team_id:
                        continue

                    fixtures = get_player_fixtures(
                        team_id,
                        gw,
                        self.fixtures,
                    )

                    if not fixtures:
                        blanking += 1

                if blanking >= 5:
                    score += 30
                    reasons.append(
                        f"{blanking} squad players blank"
                    )

                elif blanking >= 3:
                    score += 15
                    reasons.append(
                        f"{blanking} squad players blank"
                    )

                details["blanking_players"] = blanking

        # ------------------------------------------------------------
        # Small fixture week
        # ------------------------------------------------------------
        elif meta["total_fixtures"] < 8:
            score += 30

            reasons.append(
                f"Only {meta['total_fixtures']} fixtures"
            )

        # ------------------------------------------------------------
        # DGW
        # ------------------------------------------------------------
        if meta["is_dgw"] and not meta["is_bgw"]:
            score += 15

            reasons.append(
                f"DGW opportunity ({meta['dgw_team_count']} teams)"
            )
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
        """Build a recommended chip deployment sequence — 1 chip per GW."""
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
            # Search ALL scores (sorted desc), not just top 3
            all_sorted = sorted(analysis["all_scores"],
                                key=lambda x: x["score"], reverse=True)
            for candidate in all_sorted:
                gw = candidate["gameweek"]
                if gw not in used_gws and candidate["score"] > 0:
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
