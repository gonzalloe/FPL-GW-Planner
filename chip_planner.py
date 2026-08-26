"""
FPL Predictor - Season-Wide Chip Planner
Scans all remaining GWs to find the best gameweek for each chip.

Preseason behavior:
- FPL account/team info may exist before GW1.
- Current squad picks are not available before the first deadline.
- Chip scores are therefore NOT calculated preseason.
- Scores are returned as None rather than misleading 0/5 values.
"""
from types import SimpleNamespace
from data_fetcher import (
    get_dgw_teams,
    get_bgw_teams,
    get_fixtures_for_gameweek,
    get_player_fixtures,
)


class SeasonChipPlanner:
    """
    Analyzes ALL remaining GWs to find the optimal deployment for each chip.

    Considers:
    - DGWs
    - BGWs
    - fixture swings
    - current squad composition
    - bench quality
    - captain candidates

    Preseason:
    - Returns score=None until GW1 squad data exists.
    """

    def __init__(
        self,
        engine=None,
        *,
        bootstrap=None,
        fixtures=None,
        teams=None,
        players=None,
        next_gw=None,
        baseline_predictions=None,
    ):
        if engine is not None:
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

        else:
            if (
                bootstrap is None
                or fixtures is None
                or teams is None
                or players is None
                or next_gw is None
            ):
                raise ValueError(
                    "Lightweight SeasonChipPlanner requires "
                    "bootstrap, fixtures, teams, players and next_gw."
                )

            # Small compatibility object.
            # This is NOT a PredictionEngine.
            self.engine = SimpleNamespace(
                bootstrap=bootstrap,
                fixtures=fixtures,
                teams=teams,
                players=players,
                next_gw=next_gw,
                _baseline_predictions=(
                    baseline_predictions or []
                ),
            )

            self.bootstrap = bootstrap
            self.fixtures = fixtures
            self.teams = teams
            self.next_gw = next_gw

            self._baseline_predictions = (
                baseline_predictions or []
            )

    # ------------------------------------------------------------------
    # Fixture helper
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Main analysis
    # ------------------------------------------------------------------

    def analyze_season(
        self,
        chips_available=None,
        current_squad_ids=None,
        bank=0.0,
    ):
        """
        Scan all remaining GWs and score each chip.
        Preseason:
            If this is GW1 and no current squad exists, chip scores are
            unavailable and returned as None.
        Normal season:
            Scores are calculated normally.
        """

        if chips_available is None:
            chips_available = ["BB", "TC", "FH", "WC"]
        max_gw = 38
        remaining_gws = list(range(self.next_gw, max_gw + 1))
        preseason = (
            self.next_gw == 1
            and not current_squad_ids
        )
        print(
            f"[CHIP] Analyze season: GW{self.next_gw}-38 | "
            f"squad={len(current_squad_ids) if current_squad_ids else 0} | "
            f"preseason={preseason}"
        )

        # ------------------------------------------------------------
        # PRESEASON
        # ------------------------------------------------------------
 
        if preseason:
            chip_scores = {
                chip: [
                    {
                        "gameweek": gw,
                        "score": None,
                        "reason": (
                            "Preseason — current squad "
                            "data unavailable"
                        ),
                        "is_dgw": False,
                        "is_bgw": False,
                        "dgw_teams": 0,
                        "fixtures": 0,
                        "score_available": False,
                        "reason_code": "PRESEASON",
                    }
                    for gw in remaining_gws
                ]
                for chip in chips_available
            }

            return {
                "from_gw": self.next_gw,
                "to_gw": max_gw,
                "remaining_gws": len(remaining_gws),
                "gw_metadata": {},
                "chip_analysis": {
                    chip: {
                        "best_gw": None,
                        "best_score": None,
                        "best_reason": (
                            "Preseason — waiting for "
                            "GW1 squad data"
                        ),
                        "top_3": [],
                        "all_scores": chip_scores[chip],
                        "score_available": False,
                        "reason_code": "PRESEASON",
                    }
                    for chip in chips_available
                },

                "recommended_sequence": [],
                "chips_available": chips_available,
                "preseason": True,
                "squad_data_available": False,
                "score_status": "unavailable",
                "score_status_reason": (
                    "Chip scores will be calculated "
                    "once GW1 squad data is available."
                ),
            }

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
                    tid: self.teams.get(
                        tid,
                        {}
                    ).get(
                        "short_name",
                        "?"
                    )
                    for tid in dgw
                },
                "is_bgw": len(bgw) > 0,
                "bgw_team_count": len(bgw),
                "bgw_teams": {
                    tid: self.teams.get(
                        tid,
                        {}
                    ).get(
                        "short_name",
                        "?"
                    )
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

                chip_scores[chip].append(
                    score_data
                )

        # ------------------------------------------------------------
        # Find best GW for each chip
        # ------------------------------------------------------------

        best_gws = {}

        for chip in chips_available:
            scores = chip_scores[chip]

            if not scores:
                continue

            # Only numerical scores participate.
            valid_scores = [
                x
                for x in scores
                if x.get("score") is not None
            ]

            if not valid_scores:
                best_gws[chip] = {
                    "best_gw": None,
                    "best_score": None,
                    "best_reason": (
                        "Chip score unavailable"
                    ),
                    "top_3": [],
                    "all_scores": scores,
                    "score_available": False,
                }
                continue

            best = max(
                valid_scores,
                key=lambda x: x["score"]
            )

            top_3 = sorted(
                valid_scores,
                key=lambda x: x["score"],
                reverse=True
            )[:3]

            best_gws[chip] = {
                "best_gw": best["gameweek"],
                "best_score": best["score"],
                "best_reason": best["reason"],
                "top_3": top_3,
                "all_scores": scores,
                "score_available": True,
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
            "preseason": False,
            "squad_data_available": bool(
                current_squad_ids
            ),
            "score_status": "available",
            "score_status_reason": None,
        }

    # ------------------------------------------------------------------
    # Individual chip scoring
    # ------------------------------------------------------------------

    def _score_chip_for_gw(
        self,
        chip,
        gw,
        meta,
        predictions,
        gw_info,
        current_squad_ids,
        bank,
    ):
        """Score one chip for one GW."""

        score = 0
        reason = ""
        details = {}

        if chip == "BB":
            score, reason, details = self._score_bb(
                gw,
                meta,
                predictions,
                gw_info,
                current_squad_ids,
            )

        elif chip == "TC":
            score, reason, details = self._score_tc(
                gw,
                meta,
                predictions,
                current_squad_ids,
            )

        elif chip == "FH":
            score, reason, details = self._score_fh(
                gw,
                meta,
                predictions,
                current_squad_ids,
            )

        elif chip == "WC":
            score, reason, details = self._score_wc(
                gw,
                meta,
                predictions,
                current_squad_ids,
            )

        # ------------------------------------------------------------
        # Preserve None.
        #
        # Never convert unavailable -> 0.
        # ------------------------------------------------------------

        score_value = (
            None
            if score is None
            else min(
                100,
                max(0, score)
            )
        )

        return {
            "gameweek": gw,
            "score": score_value,
            "reason": reason,

            "is_dgw": meta["is_dgw"],
            "is_bgw": meta["is_bgw"],

            "dgw_teams": meta.get(
                "dgw_team_count",
                0
            ),

            "fixtures": meta[
                "total_fixtures"
            ],

            **details,
        }

    # ------------------------------------------------------------------
    # Bench Boost
    # ------------------------------------------------------------------

    def _score_bb(
        self,
        gw,
        meta,
        predictions,
        gw_info,
        current_squad_ids=None,
    ):
        """Score Bench Boost for a GW."""

        score = 0
        reasons = []
        details = {}

        # ------------------------------------------------------------
        # DGW strength
        # ------------------------------------------------------------

        if meta["is_dgw"]:
            dgw_count = meta[
                "dgw_team_count"
            ]

            score += min(
                40,
                dgw_count * 7
            )

            reasons.append(
                f"{dgw_count} DGW teams"
            )

            details[
                "dgw_team_count"
            ] = dgw_count

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
                key=lambda p: p.get(
                    "predicted_points",
                    0
                ),
                reverse=True
            )

            # Approximation:
            # top 11 = starters
            # remaining 4 = bench
            bench_preds = squad_preds[11:15]

            bench_xpts = sum(
                p.get(
                    "predicted_points",
                    0
                )
                for p in bench_preds
            )

            bench_dgw = sum(
                1
                for p in bench_preds
                if p.get("is_dgw")
            )

            details["bench_xpts"] = round(
                bench_xpts,
                1
            )

            details[
                "bench_dgw_count"
            ] = bench_dgw

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

            # Normally only reachable if squad data unexpectedly
            # disappears during the season.
            return (
                None,
                "Unavailable — current squad missing",
                {
                    "score_available": False,
                    "reason_code": "NO_SQUAD",
                },
            )

        elif not baseline:

            return (
                None,
                "Unavailable — baseline predictions missing",
                {
                    "score_available": False,
                    "reason_code": "NO_BASELINE",
                },
            )

        # ------------------------------------------------------------
        # Large fixture count
        # ------------------------------------------------------------

        if meta["total_fixtures"] >= 12:
            score += 10

            reasons.append(
                f"{meta['total_fixtures']} fixtures"
            )

        return (
            score,
            " · ".join(reasons),
            details,
        )

    # ------------------------------------------------------------------
    # Triple Captain
    # ------------------------------------------------------------------

    def _score_tc(
        self,
        gw,
        meta,
        predictions,
        current_squad_ids=None,
    ):
        """Score Triple Captain for a GW."""

        score = 0
        reasons = []
        details = {}

        if not current_squad_ids:
            return (
                None,
                "Unavailable — no current squad",
                {
                    "score_available": False,
                    "reason_code": "NO_SQUAD",
                },
            )

        candidates = getattr(
            self.engine,
            "_baseline_predictions",
            []
        )

        if not candidates:
            return (
                None,
                "Unavailable — no baseline predictions",
                {
                    "score_available": False,
                    "reason_code": "NO_BASELINE",
                },
            )

        baseline_by_id = {
            p["player_id"]: p
            for p in candidates
            if p.get("player_id") is not None
        }

        squad_candidates = [
            baseline_by_id[pid]
            for pid in current_squad_ids
            if pid in baseline_by_id
        ]

        if not squad_candidates:
            return (
                None,
                "Unavailable — no captain candidates",
                {
                    "score_available": False,
                    "reason_code": "NO_CAPTAIN_CANDIDATES",
                },
            )

        # ------------------------------------------------------------
        # Find the best captain for THIS GW.
        #
        # Important:
        # baseline predictions are current-GW predictions, so fixture
        # difficulty and DGW status are applied per target GW.
        # ------------------------------------------------------------

        best = None
        best_xp = -1.0
        best_fixture_count = 0

        for player in squad_candidates:
            team_id = self.engine.players.get(
                player["player_id"],
                {}
            ).get("team")
            fixtures = get_player_fixtures(team_id, gw, self.fixtures)
            fixture_count = len(fixtures)
            base_xp = float(player.get("predicted_points", 0.0) or 0.0)

            # Fixture adjustment
            if fixtures:
                avg_fdr = (
                    sum(
                        float(f.get("fdr", 3) or 3)
                        for f in fixtures
                    )
                    / len(fixtures)
                )

                fixture_multiplier = (1.0 + ((3.0 - avg_fdr) * 0.08))
                adjusted_xp = (base_xp * fixture_multiplier)

            else:
                adjusted_xp = 0.0

            # DGW bonus
            if fixture_count >= 2:
                adjusted_xp *= 1.85

            if adjusted_xp > best_xp:
                best_xp = adjusted_xp
                best = player
                best_fixture_count = fixture_count

        if best is None or best_xp <= 0:
            return (
                None,
                "Unavailable — no captain candidate",
                {
                    "score_available": False,
                    "reason_code": "NO_CAPTAIN",
                },
            )

        details["best_captain"] = best["name"]
        details["captain_xpts"] = round(best_xp, 1)
        details["captain_fixture_count"] = (best_fixture_count)

        # Base captain quality
        if best_xp >= 12:
            score += 45
        elif best_xp >= 10:
            score += 35
        elif best_xp >= 8:
            score += 25
        elif best_xp >= 7:
            score += 18
        elif best_xp >= 6:
            score += 12
        elif best_xp >= 5:
            score += 7
        else:
            score += 3

        reasons.append(
            f"{best['name']} ~{best_xp:.1f} xPts"
        )

        # DGW value
        if best_fixture_count >= 2:

            score += 35
            reasons.append(f"DGW captain ({best_fixture_count} fixtures)")

        # Large DGW
        if meta["is_dgw"]:

            dgw_count = meta.get("dgw_team_count",0)
            details["dgw_team_count"] = dgw_count

            if dgw_count >= 6:
                score += 20
                reasons.append(
                    f"Large DGW: {dgw_count} teams"
                )

            elif dgw_count >= 4:
                score += 12
                reasons.append(
                    f"Strong DGW: {dgw_count} teams"
                )

            elif dgw_count >= 2:
                score += 6
                reasons.append(
                    f"DGW: {dgw_count} teams"
                )

        # Cap score.
        score = min(100, max(0, score))

        return (
            score,
            " · ".join(reasons),
            details,
        )

    # ------------------------------------------------------------------
    # Free Hit
    # ------------------------------------------------------------------

    def _score_fh(
        self,
        gw,
        meta,
        predictions,
        current_squad_ids,
    ):
        """Score Free Hit for a GW."""

        score = 0
        reasons = []
        details = {}

        # ------------------------------------------------------------
        # BGW
        # ------------------------------------------------------------

        if meta["is_bgw"]:

            bgw_count = meta[
                "bgw_team_count"
            ]

            score += min(
                60,
                bgw_count * 8
            )

            reasons.append(
                f"BGW: {bgw_count} teams missing"
            )

            # Current squad impact
            if current_squad_ids:

                blanking = 0

                for pid in current_squad_ids:

                    player = self.engine.players.get(
                        pid
                    )

                    if not player:
                        continue

                    team_id = player.get(
                        "team"
                    )

                    if not team_id:
                        continue

                    fixtures = get_player_fixtures(
                        team_id,
                        gw,
                        self.fixtures,
                    )

                    if not fixtures:
                        blanking += 1

                details[
                    "blanking_players"
                ] = blanking

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

            else:
                return (
                    None,
                    "Unavailable — no current squad",
                    {
                        "score_available": False,
                        "reason_code": "NO_SQUAD",
                    },
                )

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
                f"DGW opportunity "
                f"({meta['dgw_team_count']} teams)"
            )

        return (
            score,
            " · ".join(reasons),
            details,
        )

    # ------------------------------------------------------------------
    # Wildcard
    # ------------------------------------------------------------------

    def _score_wc(self, gw, meta, predictions, current_squad_ids,):
        score = 0
        reasons = []
        details = {}

        # ------------------------------------------------------------
        # Wildcard should NOT be triggered merely because a DGW exists.
        # A future DGW is a reason to CONSIDER WC, not automatically USE it.
        # ------------------------------------------------------------

        next_gw_meta = None

        for future_gw in range(
            gw + 1,
            min(gw + 4, 39)
        ):
            future_dgw = get_dgw_teams(
                future_gw,
                self.fixtures
            )

            if len(future_dgw) >= 4:
                next_gw_meta = {
                    "gw": future_gw,
                    "dgw_count": len(future_dgw),
                }
                break

        # ------------------------------------------------------------
        # Current squad must exist
        # ------------------------------------------------------------

        if not current_squad_ids:
            return (
                None,
                "Unavailable — no current squad",
                {
                    "score_available": False,
                    "reason_code": "NO_SQUAD",
                },
            )

        # ------------------------------------------------------------
        # Future DGW
        #
        # Give only a moderate score.
        # A DGW alone is NOT enough to recommend WC.
        # ------------------------------------------------------------

        if next_gw_meta:
            dgw_count = next_gw_meta["dgw_count"]

            # 4-team DGW = useful planning signal
            # 6+ team DGW = stronger signal
            score += min(
                25,
                max(0, (dgw_count - 3) * 6)
            )

            details["target_dgw"] = next_gw_meta["gw"]
            details["target_dgw_count"] = dgw_count

            reasons.append(
                f"GW{next_gw_meta['gw']} has "
                f"{dgw_count}-team DGW ahead"
            )

        # ------------------------------------------------------------
        # Current squad fixture exposure
        #
        # Count how many current players have poor fixture coverage
        # in the upcoming GWs. This is a much better WC signal than
        # simply detecting a DGW.
        # ------------------------------------------------------------

        poor_fixture_players = 0
        blank_players = 0

        check_gws = range(
            gw,
            min(gw + 4, 39)
        )

        for pid in current_squad_ids:

            player = self.engine.players.get(pid)

            if not player:
                continue

            team_id = player.get("team")

            if not team_id:
                continue

            fixtures_seen = 0
            good_fixtures = 0

            for check_gw in check_gws:

                fixtures = get_player_fixtures(
                    team_id,
                    check_gw,
                    self.fixtures
                )

                if not fixtures:
                    continue

                fixtures_seen += len(fixtures)

                for fixture in fixtures:
                    fdr = fixture.get("fdr", 3)

                    if fdr <= 2:
                        good_fixtures += 1

            if fixtures_seen == 0:
                blank_players += 1

            elif good_fixtures == 0:
                poor_fixture_players += 1

        details["blank_players"] = blank_players
        details["poor_fixture_players"] = poor_fixture_players

        # ------------------------------------------------------------
        # Squad problem
        #
        # Only award meaningful WC points when the actual squad
        # has structural problems.
        # ------------------------------------------------------------

        if blank_players >= 4:
            score += 30
            reasons.append(
                f"{blank_players} current squad players "
                f"have poor fixture coverage"
            )

        elif blank_players >= 2:
            score += 15
            reasons.append(
                f"{blank_players} current squad players "
                f"have poor fixture coverage"
            )

        if poor_fixture_players >= 6:
            score += 25
            reasons.append(
                f"{poor_fixture_players} players lack "
                f"good fixtures over the next 3 GWs"
            )

        elif poor_fixture_players >= 4:
            score += 15
            reasons.append(
                f"{poor_fixture_players} players lack "
                f"good fixtures over the next 3 GWs"
            )

        # ------------------------------------------------------------
        # Current GW DGW
        #
        # Small bonus only. Do NOT heavily reward WC simply because
        # the current GW is a DGW.
        # ------------------------------------------------------------

        if meta["is_dgw"]:
            score += 5
            reasons.append(
                "Current GW has a DGW"
            )

        # ------------------------------------------------------------
        # Late season
        # ------------------------------------------------------------

        if gw >= 30:
            score += 5
            reasons.append(
                "Late season — WC for final push"
            )

        # ------------------------------------------------------------
        # Conservative cap
        #
        # Without a demonstrated squad problem, WC should never
        # become an extremely high-confidence recommendation.
        # ------------------------------------------------------------

        if score < 20:
            reasons = [
                "Current squad does not justify a Wildcard yet"
            ]

        details["recommendation_strength"] = (
            "strong"
            if score >= 70
            else "consider"
            if score >= 40
            else "save"
        )

        return (
            score,
            " · ".join(reasons),
            details,
        )

    # ------------------------------------------------------------------
    # Recommended sequence
    # ------------------------------------------------------------------

    def _build_chip_sequence(
        self,
        best_gws,
        chips_available,
        gw_meta,
    ):
        """
        Build recommended chip deployment sequence.

        One chip per GW.
        """

        sequence = []
        used_gws = set()

        # ------------------------------------------------------------
        # Only chips with valid scores
        # ------------------------------------------------------------

        valid_chips = [
            chip
            for chip in chips_available
            if chip in best_gws
            and best_gws[chip].get(
                "best_score"
            ) is not None
        ]

        sorted_chips = sorted(
            valid_chips,
            key=lambda c: best_gws[c][
                "best_score"
            ],
            reverse=True
        )

        for chip in sorted_chips:

            analysis = best_gws[chip]

            all_sorted = sorted(
                [
                    x
                    for x in analysis[
                        "all_scores"
                    ]
                    if x.get("score") is not None
                ],
                key=lambda x: x["score"],
                reverse=True
            )

            for candidate in all_sorted:

                gw = candidate[
                    "gameweek"
                ]

                if (
                    gw not in used_gws
                    and candidate["score"] > 0
                ):

                    sequence.append({
                        "chip": chip,
                        "gameweek": gw,
                        "score": candidate["score"],
                        "reason": candidate["reason"],
                        "is_dgw": gw_meta.get(
                            gw,
                            {}
                        ).get(
                            "is_dgw",
                            False
                        ),
                    })

                    used_gws.add(gw)

                    break

        sequence.sort(
            key=lambda x: x["gameweek"]
        )

        return sequence