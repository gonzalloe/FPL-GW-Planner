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
            self._target_prediction_cache = {}
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
            self._target_prediction_cache = {}


    def _position_fdr_modifier(self, position_id, fdr, is_home):
        """
        Convert FPL fixture difficulty into an xPts multiplier.
        FDR:
            1 = easiest
            2 = easy
            3 = neutral
            4 = difficult
            5 = hardest
        The baseline prediction is treated as a neutral-fixture
        per-fixture expectation.
        """

        try:
            position_id = int(position_id)
        except (TypeError, ValueError):
            position_id = 3

        try:
            fdr = int(fdr)
        except (TypeError, ValueError):
            fdr = 3

        fdr = max(1, min(5, fdr))

        # Base FDR multipliers.
        base = {
            1: 1.12,   # FDR 1
            2: 1.06,   # FDR 2
            3: 1.00,   # FDR 3
            4: 0.93,   # FDR 4
            5: 0.86,   # FDR 5
        }

        modifier = base[fdr]

        # Small home advantage.
        if is_home:
            modifier *= 1.03
        else:
            modifier *= 0.98

        # Goalkeepers/defenders are slightly more sensitive
        # to fixture difficulty because clean-sheet probability
        # is heavily fixture dependent.
        if position_id in (1, 2):
            defensive_adjustment = {
                1: 1.04,
                2: 1.02,
                3: 1.00,
                4: 0.96,
                5: 0.92,
            }
            modifier *= defensive_adjustment[fdr]

        # For midfielders/forwards, attacking output is affected
        # by fixture difficulty, but less aggressively.
        elif position_id in (3, 4):
            attacking_adjustment = {
                1: 1.03,
                2: 1.015,
                3: 1.00,
                4: 0.985,
                5: 0.97,
            }
            modifier *= attacking_adjustment[fdr]

        return modifier


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

    def _get_target_predictions(self, gw):
        """
        Get target-GW player projections.

        Full PredictionEngine mode:
            Uses engine.predict_player(pid, gw).

        Lightweight mode:
            Uses cached baseline predictions and projects them onto
            the target GW using that GW's fixtures/FDR.

        This avoids constructing a second expensive PredictionEngine
        during /api/season-chips.
        """

        cache = getattr(
            self,
            "_target_prediction_cache",
            {}
        )

        if gw in cache:
            return cache[gw]

        # ============================================================
        # FULL ENGINE MODE
        # ============================================================

        if hasattr(self.engine, "predict_player"):
            results = []

            for pid in self.engine.players:
                try:
                    pred = self.engine.predict_player(
                        pid,
                        gw,
                    )

                    if pred and not pred.get("error"):
                        results.append(pred)

                except Exception as exc:
                    print(
                        f"[CHIP] Target prediction failed "
                        f"GW{gw} player={pid}: {exc}"
                    )

            cache[gw] = results
            self._target_prediction_cache = cache

            print(
                f"[CHIP] Target predictions GW{gw}: "
                f"{len(results)} players"
            )

            return results

        # ============================================================
        # LIGHTWEIGHT MODE
        # ============================================================

        baseline = getattr(
            self,
            "_baseline_predictions",
            []
        )

        if not baseline:
            cache[gw] = []
            self._target_prediction_cache = cache

            print(
                f"[CHIP] No baseline predictions available for GW{gw}"
            )

            return []

        results = []

        for base in baseline:
            try:
                pid = base.get("player_id")

                if pid is None:
                    continue

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

                # ----------------------------------------------------
                # BGW
                # ----------------------------------------------------

                if not fixtures:
                    projected = dict(base)

                    projected["predicted_points"] = 0.0
                    projected["raw_xpts"] = 0.0
                    projected["num_fixtures"] = 0
                    projected["is_dgw"] = False
                    projected["fixtures"] = []

                    results.append(projected)
                    continue

                # ----------------------------------------------------
                # Project each fixture
                # ----------------------------------------------------

                base_xpts = float(
                    base.get(
                        "raw_xpts",
                        base.get("predicted_points", 0.0)
                    ) or 0.0
                )

                # Avoid negative/invalid projections.
                base_xpts = max(base_xpts, 0.0)

                fixture_values = []

                for fixture in fixtures:
                    fdr = int(
                        fixture.get("fdr", 3) or 3
                    )

                    is_home = bool(
                        fixture.get("is_home", False)
                    )

                    # Position-aware FDR modifier.
                    position_id = int(
                        base.get(
                            "position_id",
                            player.get(
                                "position_id",
                                3
                            )
                        ) or 3
                    )

                    try:
                        modifier = self._position_fdr_modifier(position_id, fdr, is_home)
                    except Exception as exc:
                        print(
                            f"[CHIP] FDR modifier failed "
                            f"GW{gw} player={pid} "
                            f"fdr={fdr}: {exc}"
                        )
                        modifier = 1.0

                    # The baseline prediction is a per-fixture estimate.
                    # Apply the target fixture difficulty and home/away adjustment.
                    fixture_xpts = base_xpts * modifier
                    fixture_xpts = max(0.0, fixture_xpts)

                    fixture_values.append(
                        {
                            "fixture": fixture,
                            "xpts": fixture_xpts,
                        }
                    )


                projected_xpts = sum(
                    item["xpts"]
                    for item in fixture_values
                )

                #debug print 
                if gw <= self.next_gw + 3:
                    modifiers = [
                        round(item["xpts"] / base_xpts, 3)
                        for item in fixture_values
                        if base_xpts > 0
                    ]
                    print(
                        f"[CHIP PROJECTION DEBUG] "
                        f"GW{gw} {base.get('name')} "
                        f"base={base_xpts:.2f} "
                        f"fixtures={len(fixtures)} "
                        f"modifiers={modifiers} "
                        f"projected={projected_xpts:.2f}"
                    )               

                projected = dict(base)

                projected["predicted_points"] = round(
                    projected_xpts,
                    3,
                )

                projected["raw_xpts"] = round(
                    projected_xpts,
                    3,
                )

                projected["num_fixtures"] = len(
                    fixtures
                )

                projected["is_dgw"] = (
                    len(fixtures) >= 2
                )

                projected["fixtures"] = fixtures

                results.append(projected)

            except Exception as exc:
                print(
                    f"[CHIP] Lightweight projection failed "
                    f"GW{gw} player={base.get('player_id')}: "
                    f"{exc}"
                )

        cache[gw] = results
        self._target_prediction_cache = cache

        print(
            f"[CHIP] Lightweight target projections GW{gw}: "
            f"{len(results)} players"
        )
        print(
            "[CHIP DEBUG] GW",
            gw,
            "engine_has_predict_player=",
            hasattr(self.engine, "predict_player"),
            "baseline=",
            len(getattr(self, "_baseline_predictions", [])),
        )

        if results:
            xpts_values = [
                round(
                    float(p.get("predicted_points", 0.0) or 0.0),3)
                for p in results
            ]

            unique_xpts = len(set(xpts_values))

            print(
                f"[CHIP TARGET CHECK] GW{gw}: "
                f"players={len(results)} "
                f"unique_xpts={unique_xpts} "
                f"min={min(xpts_values):.3f} "
                f"max={max(xpts_values):.3f}"
            )

        return results

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
            predictions = self._get_target_predictions(gw)
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

            if scores:
                numeric_scores = [
                    float(x["score"])
                    for x in scores
                    if x.get("score") is not None
                ]

                if numeric_scores:
                    min_score = min(numeric_scores)
                    max_score = max(numeric_scores)

                    for item in scores:
                        if item.get("score") is None:
                            continue

                        raw_score = float(item["score"])
                        item["raw_score"] = round(raw_score, 2)

                        if max_score > min_score:
                            item["score"] = round(
                                100.0 * (
                                    (raw_score - min_score)
                                    / (max_score - min_score)
                                )
                            )
                        else:
                            item["score"] = 50

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


    def _project_squad_over_gws(self, player_ids, start_gw, num_gws=4):
        total = 0.0
    
        for check_gw in range(start_gw, min(start_gw + num_gws, 39)):
            predictions = self._get_target_predictions(check_gw)
    
            pred_map = {
                p["player_id"]: p
                for p in predictions
                if p.get("player_id") is not None
            }
    
            players = [
                pred_map[pid]
                for pid in player_ids
                if pid in pred_map
            ]
    
            # Reuse legal XI selection.
            _, xi_xpts = self._select_best_xi(players)
    
            total += xi_xpts
    
        return total

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

    def _score_tc(self, gw, meta, predictions, current_squad_ids=None):
        """
        Score Triple Captain for a specific target GW.

        Uses the PredictionEngine's actual target-GW xPts.
        For a DGW, predict_player() already sums the expected value
        of both fixtures.

        TC score is based on:
            1. Best captain xPts
            2. Incremental value of the third captain multiplier
            3. DGW opportunity
            4. Captain quality
        """

        if not current_squad_ids:
            return (
                None,
                "Unavailable — no current squad",
                {
                    "score_available": False,
                    "reason_code": "NO_SQUAD",
                },
            )

        if not predictions:
            return (
                None,
                "Unavailable — no target-GW predictions",
                {
                    "score_available": False,
                    "reason_code": "NO_TARGET_PREDICTIONS",
                },
            )

        pred_map = {
            p["player_id"]: p
            for p in predictions
            if p.get("player_id") is not None
        }

        candidates = [
            pred_map[pid]
            for pid in current_squad_ids
            if pid in pred_map
        ]

        if not candidates:
            return (
                None,
                "Unavailable — no captain candidates",
                {
                    "score_available": False,
                    "reason_code": "NO_CAPTAIN_CANDIDATES",
                },
            )

        # Best captain for THIS GW.
        best = max(
            candidates,
            key=lambda p: float(
                p.get("predicted_points", 0.0) or 0.0
            )
        )

        captain_xpts = float(
            best.get("predicted_points", 0.0) or 0.0
        )

        fixture_count = int(
            best.get("num_fixtures", 0) or 0
        )

        if captain_xpts <= 0:
            return (
                None,
                "Unavailable — captain has no expected points",
                {
                    "score_available": False,
                    "reason_code": "ZERO_CAPTAIN_XPTS",
                },
            )

        # ------------------------------------------------------------
        # TC incremental value
        #
        # Normal captain = 2x
        # Triple captain = 3x
        #
        # Therefore the incremental value of TC is 1x captain EV.
        # ------------------------------------------------------------
        tc_incremental_xpts = captain_xpts

        # ------------------------------------------------------------
        # Normalize captain EV into a 0-100 score.
        #
        # 15 xPts is treated as extremely strong.
        # ------------------------------------------------------------
        score = min(
            70.0,
            tc_incremental_xpts * (70.0 / 15.0)
        )

        reasons = [
            f"{best['name']} ~{captain_xpts:.1f} xPts"
        ]

        details = {
            "best_captain": best["name"],
            "captain_xpts": round(captain_xpts, 2),
            "tc_incremental_xpts": round(
                tc_incremental_xpts,
                2
            ),
            "captain_fixture_count": fixture_count,
        }

        # ------------------------------------------------------------
        # DGW bonus
        # ------------------------------------------------------------

        if fixture_count >= 2:
            score += 20
            reasons.append(
                f"DGW captain ({fixture_count} fixtures)"
            )

            details["is_dgw_captain"] = True
        else:
            details["is_dgw_captain"] = False

        # ------------------------------------------------------------
        # Large overall DGW
        # ------------------------------------------------------------

        dgw_count = meta.get(
            "dgw_team_count",
            0
        )

        if dgw_count >= 6:
            score += 10
            reasons.append(
                f"Large DGW: {dgw_count} teams"
            )

        elif dgw_count >= 4:
            score += 6
            reasons.append(
                f"Strong DGW: {dgw_count} teams"
            )

        elif dgw_count >= 2:
            score += 3
            reasons.append(
                f"DGW: {dgw_count} teams"
            )

        score = min(100, max(0, round(score)))

        return (
            score,
            " · ".join(reasons),
            details,
        )

    def _select_best_xi(self, player_pool):
        """
        Select the highest-xPts legal FPL starting XI.
        Formation rules:
            GK  = 1
            DEF = 3-5
            MID = 2-5
            FWD = 1-3
        Club rule:
            Maximum 3 players from one club.
        Uses a bounded candidate search rather than the previous
        full combinatorial search, which could take minutes for
        a 600-player prediction pool.
        """
        player_lookup = getattr(self.engine, "players", {}) or {}

        def position_id(player):
            value = player.get("position_id")

            if value is None:
                value = player.get("element_type")

            if value is None:
                engine_player = player_lookup.get(
                    player.get("player_id"),
                    {},
                )
                value = engine_player.get("position_id")
                
                if value is None:
                    value = engine_player.get("element_type")

            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        def xpts(player):
            try:
                return float(
                    player.get("predicted_points", 0.0) or 0.0
                )
            except (TypeError, ValueError):
                return 0.0

        def team_id(player):
            team = player.get("team")

            if team is not None:
                return team

            engine_player = player_lookup.players.get(
                player.get("player_id"),
                {},
            )
            return engine_player.get("team")

        def add_best(players, count, used_team_counts):
            """
            Select the best available players while respecting
            the 3-player-per-club limit.
            """
            selected = []

            for player in players:
                team = team_id(player)

                if team is not None:
                    if used_team_counts.get(team, 0) >= 3:
                        continue

                selected.append(player)

                if team is not None:
                    used_team_counts[team] = (
                        used_team_counts.get(team, 0) + 1
                    )

                if len(selected) >= count:
                    break

            return selected

        # ------------------------------------------------------------
        # Separate positions
        # ------------------------------------------------------------

        goalkeepers = sorted(
            [
                p for p in player_pool
                if position_id(p) == 1
            ],
            key=xpts,
            reverse=True,
        )

        defenders = sorted(
            [
                p for p in player_pool
                if position_id(p) == 2
            ],
            key=xpts,
            reverse=True,
        )

        midfielders = sorted(
            [
                p for p in player_pool
                if position_id(p) == 3
            ],
            key=xpts,
            reverse=True,
        )

        forwards = sorted(
            [
                p for p in player_pool
                if position_id(p) == 4
            ],
            key=xpts,
            reverse=True,
        )

        if not goalkeepers:
            return [], 0.0

        # ------------------------------------------------------------
        # Keep a bounded candidate pool.
        #
        # We only need the strongest candidates to find a good XI.
        # ------------------------------------------------------------

        goalkeepers = goalkeepers[:3]
        defenders = defenders[:12]
        midfielders = midfielders[:12]
        forwards = forwards[:8]

        formations = (
            (3, 5, 2),
            (3, 4, 3),
            (3, 3, 4),  # invalid FPL formation, retained nowhere
            (4, 5, 1),
            (4, 4, 2),
            (4, 3, 3),
            (5, 4, 1),
            (5, 3, 2),
            (5, 2, 3),
        )

        best_xi = []
        best_points = -1.0

        # ------------------------------------------------------------
        # Try every valid formation and GK.
        #
        # Instead of combinations of 30+ players, we build the XI
        # greedily while checking club limits.
        # ------------------------------------------------------------

        valid_formations = [
            (d, m, f)
            for d, m, f in formations
            if (
                3 <= d <= 5
                and 2 <= m <= 5
                and 1 <= f <= 3
                and d + m + f == 10
            )
        ]

        for gk in goalkeepers:

            for defender_count, midfielder_count, forward_count in (
                valid_formations
            ):

                team_counts = {}

                gk_team = team_id(gk)

                if gk_team is not None:
                    team_counts[gk_team] = 1

                selected_defs = add_best(
                    defenders,
                    defender_count,
                    team_counts,
                )

                if len(selected_defs) != defender_count:
                    continue

                selected_mids = add_best(
                    midfielders,
                    midfielder_count,
                    team_counts,
                )

                if len(selected_mids) != midfielder_count:
                    continue

                selected_fwds = add_best(
                    forwards,
                    forward_count,
                    team_counts,
                )

                if len(selected_fwds) != forward_count:
                    continue

                xi = (
                    [gk]
                    + selected_defs
                    + selected_mids
                    + selected_fwds
                )

                if len(xi) != 11:
                    continue

                total = sum(
                    xpts(player)
                    for player in xi
                )

                if total > best_points:
                    best_points = total
                    best_xi = xi

        if not best_xi:
            return [], 0.0

        return (
            best_xi,
            best_points,
        )

    # ------------------------------------------------------------------
    # Free Hit
    # ------------------------------------------------------------------

    def _score_fh(self, gw, meta, predictions, current_squad_ids):
        """
        Score Free Hit based on the actual expected-point gain
        from using a completely different squad for this GW.

        FH value:

            optimal FH XI xPts
            -
            current squad XI xPts

        Then add strategic value for BGWs/DGWs.
        """

        if not current_squad_ids:
            return (
                None,
                "Unavailable — no current squad",
                {
                    "score_available": False,
                    "reason_code": "NO_SQUAD",
                },
            )

        if not predictions:
            return (
                None,
                "Unavailable — no target-GW predictions",
                {
                    "score_available": False,
                    "reason_code": "NO_TARGET_PREDICTIONS",
                },
            )

        pred_map = {
            p["player_id"]: p
            for p in predictions
            if p.get("player_id") is not None
        }

        # ------------------------------------------------------------
        # Current squad expected points
        # ------------------------------------------------------------

        current_players = [
            pred_map[pid]
            for pid in current_squad_ids
            if pid in pred_map
        ]

        if not current_players:
            return (
                None,
                "Unavailable — current squad not in predictions",
                {
                    "score_available": False,
                    "reason_code": "CURRENT_SQUAD_NOT_FOUND",
                },
            )

        current_players = sorted(
            current_players,
            key=lambda p: float(
                p.get("predicted_points", 0.0) or 0.0
            ),
            reverse=True,
        )

        # ------------------------------------------------------------
        # Current squad XI
        # ------------------------------------------------------------

        current_xi, current_xi_xpts = self._select_best_xi(current_players)

        if not current_xi:
            return (
                None,
                "Unavailable — could not build current XI",
                {
                    "score_available": False,
                    "reason_code": "CURRENT_XI_FAILED",
                },
            )

        # ------------------------------------------------------------
        # Best possible FH XI
        # ------------------------------------------------------------

        # Only use players with positive expected points.
        fh_pool = [
            p for p in predictions
            if float(
                p.get("predicted_points", 0.0) or 0.0
            ) > 0
        ]

        fh_xi, fh_xi_xpts = self._select_best_xi(
            fh_pool
        )

        if not fh_xi:
            return (
                None,
                "Unavailable — could not build FH XI",
                {
                    "score_available": False,
                    "reason_code": "FH_XI_FAILED",
                },
            )

        # ------------------------------------------------------------
        # Core FH value
        # ------------------------------------------------------------

        fh_gain = max(
            0.0,
            fh_xi_xpts - current_xi_xpts
        )

        details = {
            "current_xi_xpts": round(
                current_xi_xpts,
                2
            ),
            "fh_xi_xpts": round(
                fh_xi_xpts,
                2
            ),
            "fh_gain_xpts": round(
                fh_gain,
                2
            ),
            "current_xi": [
                p["name"]
                for p in current_xi
            ],
            "fh_xi": [
                p["name"]
                for p in fh_xi
            ],
        }

        # ------------------------------------------------------------
        # Convert xPts gain into score.
        #
        # 10 xPts of gain = approximately 60 points.
        # ------------------------------------------------------------

        score = min(90.0, fh_gain * 4.5)
        reasons = []
        if fh_gain > 0:
            reasons.append(
                f"+{fh_gain:.1f} xPts vs current XI"
            )

        # ------------------------------------------------------------
        # BGW bonus
        # ------------------------------------------------------------

        if meta["is_bgw"]:
            bgw_count = meta.get("bgw_team_count", 0)
            score += min(25, bgw_count * 4)
            reasons.append(f"BGW: {bgw_count} teams missing")
            blanking = 0
            for pid in current_squad_ids:
                player = self.engine.players.get(pid)

                if not player:
                    continue
                team_id = player.get("team")
                fixtures = get_player_fixtures(team_id, gw, self.fixtures)
                if not fixtures:
                    blanking += 1
            details["blanking_players"] = blanking
            if blanking >= 5:
                score += 15
                reasons.append(
                    f"{blanking} squad players blank"
                )
            elif blanking >= 3:
                score += 8
                reasons.append(
                    f"{blanking} squad players blank"
                )

        # ------------------------------------------------------------
        # DGW bonus
        # ------------------------------------------------------------

        if meta["is_dgw"]:
            dgw_count = meta.get("dgw_team_count", 0)
            score += min(10, dgw_count * 1.5)
            reasons.append(f"DGW opportunity ({dgw_count} teams)")
        score = min(100, max(0, round(score)))
        if not reasons:
            reasons.append(
                "Limited Free Hit upside this GW"
            )

        return (
            score,
            " · ".join(reasons),
            details,
        )

    # ------------------------------------------------------------------
    # Wildcard
    # ------------------------------------------------------------------

    def _score_wc(self, gw, meta, predictions, current_squad_ids):
        """
        Wildcard score based on projected squad improvement.

        Compares:
            best squad available for the target GW
            versus
            current squad

        over the next 4 GWs.
        """

        if not current_squad_ids:
            return (
                None,
                "Unavailable — no current squad",
                {
                    "score_available": False,
                    "reason_code": "NO_SQUAD",
                },
            )

        if not predictions:
            return (
                None,
                "Unavailable — no target-GW predictions",
                {
                    "score_available": False,
                    "reason_code": "NO_TARGET_PREDICTIONS",
                },
            )

        pred_map = {
            p["player_id"]: p
            for p in predictions
            if p.get("player_id") is not None
        }

        current_players = [
            pred_map[pid]
            for pid in current_squad_ids
            if pid in pred_map
        ]

        if not current_players:
            return (
                None,
                "Unavailable — current squad predictions missing",
                {
                    "score_available": False,
                    "reason_code": "CURRENT_SQUAD_NOT_FOUND",
                },
            )

        # ------------------------------------------------------------
        # Current squad fixture problems
        # ------------------------------------------------------------

        poor_players = 0
        blank_players = 0

        for pid in current_squad_ids:
            player = self.engine.players.get(pid)

            if not player:
                continue

            team_id = player.get("team")

            has_good_fixture = False
            has_fixture = False

            for check_gw in range(
                gw,
                min(gw + 4, 39)
            ):
                fixtures = get_player_fixtures(
                    team_id,
                    check_gw,
                    self.fixtures,
                )

                for fixture in fixtures:
                    has_fixture = True

                    if fixture.get("fdr", 3) <= 2:
                        has_good_fixture = True

            if not has_fixture:
                blank_players += 1
            elif not has_good_fixture:
                poor_players += 1

        details = {
            "blank_players": blank_players,
            "poor_fixture_players": poor_players,
        }

        score = 0
        reasons = []

        # ------------------------------------------------------------
        # Squad problems
        # ------------------------------------------------------------

        if blank_players >= 4:
            score += 30
            reasons.append(
                f"{blank_players} players have no fixture coverage"
            )
        elif blank_players >= 2:
            score += 15
            reasons.append(
                f"{blank_players} players have no fixture coverage"
            )

        if poor_players >= 6:
            score += 25
            reasons.append(
                f"{poor_players} players lack good fixtures"
            )
        elif poor_players >= 4:
            score += 15
            reasons.append(
                f"{poor_players} players lack good fixtures"
            )

        # ------------------------------------------------------------
        # Future DGW setup
        # ------------------------------------------------------------

        future_dgw = []

        for future_gw in range(
            gw + 1,
            min(gw + 5, 39)
        ):
            dgw = get_dgw_teams(
                future_gw,
                self.fixtures,
            )

            if len(dgw) >= 4:
                future_dgw.append(
                    (future_gw, len(dgw))
                )

        if future_dgw:
            target_gw, dgw_count = future_dgw[0]

            details["target_dgw"] = target_gw
            details["target_dgw_count"] = dgw_count

            score += min(
                20,
                (dgw_count - 3) * 5
            )

            reasons.append(
                f"GW{target_gw} has "
                f"{dgw_count}-team DGW ahead"
            )

        # ------------------------------------------------------------
        # Fixture swing
        # ------------------------------------------------------------

        current_fixture_ease = self._lightweight_fixture_ease(
            gw,
            current_squad_ids,
        )

        future_fixture_ease = max(
            (
                self._lightweight_fixture_ease(
                    check_gw,
                    current_squad_ids,
                )
                for check_gw in range(
                    gw,
                    min(gw + 4, 39)
                )
            ),
            default=0.0,
        )

        details["current_fixture_ease"] = round(
            current_fixture_ease,
            3,
        )

        details["future_fixture_ease"] = round(
            future_fixture_ease,
            3,
        )

        # Strong negative fixture environment.
        if current_fixture_ease < -0.4:
            score += 15
            reasons.append(
                "Current squad faces a difficult fixture run"
            )

        # ------------------------------------------------------------
        # Late season
        # ------------------------------------------------------------

        if gw >= 30:
            score += 5
            reasons.append("Late season — final-push flexibility")

        score = min(100, max(0, round(score)))

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