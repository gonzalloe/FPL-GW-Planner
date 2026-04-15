"""
FPL Predictor — AI Chat Engine
Answers user questions about FPL predictions, player comparisons,
squad decisions, chip strategy, and transfer advice using real data.

The engine parses user intent, retrieves relevant player/squad/fixture data,
and constructs intelligent, data-backed answers.
"""
import re
import json
from typing import Optional


class FPLChatEngine:
    """
    Context-aware chat engine that answers FPL questions using live prediction data.
    No external LLM needed — uses rule-based NLU + data lookups.
    Generates structured answers with stats, comparisons, and reasoning.
    """

    def __init__(self, predictions: list[dict], squad: dict,
                 gw_info: dict, chip_analysis: dict,
                 players_map: dict = None, bb_squad: dict = None):
        self.predictions = predictions
        self.squad = squad
        self.gw_info = gw_info
        self.chip_analysis = chip_analysis
        self.players_map = players_map or {}
        self.bb_squad = bb_squad or {}

        # Index predictions by name (lowercase) for fast lookup
        self._by_name = {}
        self._by_team = {}
        self._by_position = {}
        for p in predictions:
            name_lower = p.get("name", "").lower()
            self._by_name[name_lower] = p
            # Also index by full name parts
            full = p.get("full_name", "").lower()
            for part in full.split():
                if len(part) > 2:
                    if part not in self._by_name:
                        self._by_name[part] = p

            team = p.get("team", "").lower()
            self._by_team.setdefault(team, []).append(p)

            pos = p.get("position", "").lower()
            self._by_position.setdefault(pos, []).append(p)

    def answer(self, question: str) -> dict:
        """
        Main entry: parse intent, gather data, generate answer.
        Returns { "answer": str, "data": dict, "suggestions": list[str] }
        """
        q = question.strip().lower()

        # Detect intent
        if self._is_comparison(q):
            return self._handle_comparison(q, question)
        elif self._is_captain_question(q):
            return self._handle_captain(q)
        elif self._is_chip_question(q):
            return self._handle_chip(q)
        elif self._is_transfer_question(q):
            return self._handle_transfer(q, question)
        elif self._is_player_question(q):
            return self._handle_player_lookup(q, question)
        elif self._is_team_question(q):
            return self._handle_team_query(q, question)
        elif self._is_position_question(q):
            return self._handle_position_query(q, question)
        elif self._is_dgw_question(q):
            return self._handle_dgw(q)
        elif self._is_squad_question(q):
            return self._handle_squad(q)
        elif self._is_differential_question(q):
            return self._handle_differentials(q)
        else:
            return self._handle_general(q, question)

    # ── Intent Detection ────────────────────────────────────

    def _is_comparison(self, q: str) -> bool:
        patterns = [r'\bvs?\b', r'\bover\b', r'\bor\b', r'\bcompare\b',
                    r'\bbetter\b', r'\binstead\b', r'\brather\b',
                    r'\bversus\b', r'\bvs\.?\b']
        return any(re.search(p, q) for p in patterns)

    def _is_captain_question(self, q: str) -> bool:
        return any(w in q for w in ['captain', 'cap ', 'armband', 'triple captain', 'tc '])

    def _is_chip_question(self, q: str) -> bool:
        return any(w in q for w in ['chip', 'bench boost', 'bb ', 'free hit', 'wildcard',
                                      'triple captain', 'tc ', 'when to use'])

    def _is_transfer_question(self, q: str) -> bool:
        return any(w in q for w in ['transfer', 'bring in', 'sell', 'buy',
                                      'replace', 'swap', 'upgrade', 'downgrade'])

    def _is_player_question(self, q: str) -> bool:
        return any(w in q for w in ['how many points', 'predict', 'expected',
                                      'xpts', 'pick', 'worth', 'should i get',
                                      'good pick', 'bad pick'])

    def _is_team_question(self, q: str) -> bool:
        return any(w in q for w in ['team', 'squad from', 'players from',
                                      'arsenal', 'city', 'liverpool', 'chelsea',
                                      'united', 'spurs', 'tottenham', 'newcastle',
                                      'villa', 'brighton', 'west ham', 'wolves',
                                      'bournemouth', 'fulham', 'palace', 'brentford',
                                      'nottingham', 'forest', 'everton', 'leicester',
                                      'ipswich', 'southampton'])

    def _is_position_question(self, q: str) -> bool:
        return any(w in q for w in ['best goalkeeper', 'best gk', 'best defender',
                                      'best midfielder', 'best forward', 'best striker',
                                      'best def', 'best mid', 'best fwd',
                                      'top gkp', 'top def', 'top mid', 'top fwd',
                                      'which keeper', 'which gk'])

    def _is_dgw_question(self, q: str) -> bool:
        return any(w in q for w in ['dgw', 'double', 'double gameweek',
                                      'two fixtures', 'play twice', 'blank'])

    def _is_squad_question(self, q: str) -> bool:
        return any(w in q for w in ['squad', 'starting', 'lineup', 'formation',
                                      'best team', 'optimal', 'xi', 'eleven'])

    def _is_differential_question(self, q: str) -> bool:
        return any(w in q for w in ['differential', 'underowned', 'under-owned',
                                      'low ownership', 'punt', 'hidden gem', 'sleeper'])

    # ── Handlers ────────────────────────────────────────────

    def _handle_comparison(self, q: str, original: str) -> dict:
        """Compare two players with full reasoning."""
        players = self._extract_player_names(q)
        if len(players) < 2:
            # Try harder
            players = self._extract_player_names(original.lower())
        if len(players) < 2:
            return self._fallback("I couldn't identify two players to compare. Try: 'Compare Salah vs Haaland'")

        p1_data = self._find_player(players[0])
        p2_data = self._find_player(players[1])

        if not p1_data:
            return self._fallback(f"Couldn't find player: {players[0]}")
        if not p2_data:
            return self._fallback(f"Couldn't find player: {players[1]}")

        comparison = self._build_comparison(p1_data, p2_data)
        return comparison

    def _build_comparison(self, p1: dict, p2: dict) -> dict:
        """Build a detailed comparison between two players."""
        n1, n2 = p1["name"], p2["name"]
        gw = self.gw_info.get("gameweek", "?")

        # Determine winner
        xp1, xp2 = p1["predicted_points"], p2["predicted_points"]
        winner = p1 if xp1 >= xp2 else p2
        loser = p2 if xp1 >= xp2 else p1

        sections = []

        # Header
        sections.append(f"## {n1} vs {n2} — GW{gw} Comparison\n")

        # Points verdict
        diff = abs(xp1 - xp2)
        sections.append(f"**🏆 Verdict: {winner['name']}** is predicted to score **{diff:.1f} more points** this GW.\n")

        # Quick stats table
        sections.append("### 📊 Head-to-Head\n")
        sections.append(f"| Metric | {n1} | {n2} |")
        sections.append(f"|--------|------|------|")
        sections.append(f"| **Predicted Points** | **{xp1:.1f}** | **{xp2:.1f}** |")
        sections.append(f"| Position | {p1['position']} | {p2['position']} |")
        sections.append(f"| Price | £{p1['price']:.1f}m | £{p2['price']:.1f}m |")
        sections.append(f"| Form | {p1.get('form', '--')} | {p2.get('form', '--')} |")
        sections.append(f"| ICT Index | {p1.get('ict_index', '--')} | {p2.get('ict_index', '--')} |")
        sections.append(f"| Season Points | {p1.get('total_points', '--')} | {p2.get('total_points', '--')} |")
        sections.append(f"| PPG | {p1.get('ppg', '--')} | {p2.get('ppg', '--')} |")
        sections.append(f"| Starts | {p1.get('starts', '--')} | {p2.get('starts', '--')} |")
        sections.append(f"| Ownership | {p1.get('selected_by_percent', '--')}% | {p2.get('selected_by_percent', '--')}% |")
        sections.append(f"| Starter Tier | {p1.get('starter_quality', {}).get('tier', '?')} | {p2.get('starter_quality', {}).get('tier', '?')} |")
        sections.append(f"| Confidence | {int(p1.get('confidence', 0)*100)}% | {int(p2.get('confidence', 0)*100)}% |")

        # DGW status
        dgw1, dgw2 = p1.get("is_dgw", False), p2.get("is_dgw", False)
        sections.append(f"| DGW? | {'✅ Yes' if dgw1 else '❌ No'} | {'✅ Yes' if dgw2 else '❌ No'} |")

        # Fixtures
        fix1 = ", ".join(f"{f['opponent']}({f['venue']}) FDR:{f['fdr']}" for f in p1.get("fixtures", []))
        fix2 = ", ".join(f"{f['opponent']}({f['venue']}) FDR:{f['fdr']}" for f in p2.get("fixtures", []))
        sections.append(f"| Fixtures | {fix1 or 'BGW'} | {fix2 or 'BGW'} |\n")

        # Reasoning
        sections.append("### 💡 Key Reasons\n")
        reasons = []

        # DGW advantage
        if dgw1 and not dgw2:
            reasons.append(f"🔥 **{n1} has a DGW** ({p1.get('num_fixtures', 1)} fixtures) while {n2} only plays once — double the opportunity for points.")
        elif dgw2 and not dgw1:
            reasons.append(f"🔥 **{n2} has a DGW** ({p2.get('num_fixtures', 1)} fixtures) while {n1} only plays once — double the opportunity for points.")

        # Form comparison
        f1, f2 = float(p1.get("form", 0)), float(p2.get("form", 0))
        if abs(f1 - f2) > 0.5:
            better = n1 if f1 > f2 else n2
            reasons.append(f"📈 **{better}** is in better form ({max(f1,f2)} vs {min(f1,f2)}).")

        # Fixture difficulty
        avg_fdr1 = sum(f["fdr"] for f in p1.get("fixtures", [])) / max(len(p1.get("fixtures", [])), 1)
        avg_fdr2 = sum(f["fdr"] for f in p2.get("fixtures", [])) / max(len(p2.get("fixtures", [])), 1)
        if abs(avg_fdr1 - avg_fdr2) >= 0.5:
            easier = n1 if avg_fdr1 < avg_fdr2 else n2
            reasons.append(f"🎯 **{easier}** has easier fixtures (avg FDR: {min(avg_fdr1, avg_fdr2):.1f} vs {max(avg_fdr1, avg_fdr2):.1f}).")

        # Starter quality
        tier1 = p1.get("starter_quality", {}).get("tier", "unknown")
        tier2 = p2.get("starter_quality", {}).get("tier", "unknown")
        tier_order = {"nailed": 4, "regular": 3, "rotation": 2, "fringe": 1, "bench_warmer": 0}
        if tier_order.get(tier1, 0) != tier_order.get(tier2, 0):
            more_nailed = n1 if tier_order.get(tier1, 0) > tier_order.get(tier2, 0) else n2
            reasons.append(f"🔒 **{more_nailed}** is more nailed (guaranteed to play full minutes).")

        # Price/value
        val1 = xp1 / max(p1["price"], 3.5)
        val2 = xp2 / max(p2["price"], 3.5)
        if abs(val1 - val2) > 0.2:
            better_val = n1 if val1 > val2 else n2
            reasons.append(f"💰 **{better_val}** offers better value for money ({max(val1,val2):.2f} vs {min(val1,val2):.2f} xPts/£m).")

        # ICT
        ict1, ict2 = float(p1.get("ict_index", 0)), float(p2.get("ict_index", 0))
        if abs(ict1 - ict2) > 20:
            better_ict = n1 if ict1 > ict2 else n2
            reasons.append(f"⚡ **{better_ict}** has a higher ICT index ({max(ict1,ict2):.0f} vs {min(ict1,ict2):.0f}), indicating more involvement in attacking play.")

        # Home advantage
        home1 = any(f.get("venue") == "H" for f in p1.get("fixtures", []))
        home2 = any(f.get("venue") == "H" for f in p2.get("fixtures", []))
        if home1 and not home2:
            reasons.append(f"🏟️ **{n1}** plays at home, giving a statistical advantage.")
        elif home2 and not home1:
            reasons.append(f"🏟️ **{n2}** plays at home, giving a statistical advantage.")

        if not reasons:
            reasons.append("Both players are closely matched this GW. The model gives a slight edge based on combined factor weighting.")

        sections.extend(reasons)

        # Bottom line
        sections.append(f"\n### ✅ Recommendation")
        sections.append(f"**Pick {winner['name']}** for GW{gw}. Expected {winner['predicted_points']:.1f} pts vs {loser['predicted_points']:.1f} pts.")

        answer = "\n".join(sections)

        suggestions = []
        if not p1.get("is_dgw") and not p2.get("is_dgw"):
            suggestions.append(f"Who are the best DGW players this week?")
        suggestions.append(f"Who should I captain this GW?")
        suggestions.append(f"Best {winner['position']} picks this week?")

        return {
            "answer": answer,
            "data": {
                "type": "comparison",
                "players": [
                    self._player_card(p1),
                    self._player_card(p2)
                ],
                "winner": winner["name"]
            },
            "suggestions": suggestions
        }

    def _handle_captain(self, q: str) -> dict:
        """Captain advice with detailed reasoning."""
        gw = self.gw_info.get("gameweek", "?")
        cap = self.squad.get("captain", {})
        vice = self.squad.get("vice_captain", {})
        top5 = self.predictions[:5]

        sections = []
        sections.append(f"## 👑 Captain Pick — GW{gw}\n")
        sections.append(f"**Model's #1 Captain: {cap.get('name', '?')}** — {cap.get('predicted_points', 0):.1f} xPts")
        if cap.get("is_dgw"):
            sections.append(f"🔥 DGW player — plays {cap.get('num_fixtures', 2)} fixtures!\n")
        sections.append(f"\n**Vice Captain: {vice.get('name', '?')}** — {vice.get('predicted_points', 0):.1f} xPts\n")

        sections.append("### Top 5 Captain Options\n")
        sections.append("| Rank | Player | Team | xPts | Fixtures | Form | DGW? |")
        sections.append("|------|--------|------|------|----------|------|------|")
        for i, p in enumerate(top5, 1):
            fix = ", ".join(f"{f['opponent']}({f['venue']})" for f in p.get("fixtures", []))
            dgw = "✅" if p.get("is_dgw") else ""
            sections.append(f"| {i} | **{p['name']}** | {p['team']} | {p['predicted_points']:.1f} | {fix} | {p.get('form', '--')} | {dgw} |")

        sections.append(f"\n### Why {cap.get('name', '?')}?\n")
        if cap.get("is_dgw"):
            sections.append(f"- **Double Gameweek**: 2 chances to score, doubled as captain = 4x total impact")
        sections.append(f"- **Form**: {cap.get('form', '--')} (recent points average)")
        sections.append(f"- **Confidence**: {int(cap.get('confidence', 0)*100)}%")
        tier = cap.get("starter_quality", {}).get("tier", "?")
        sections.append(f"- **Starter tier**: {tier} — guaranteed full minutes")

        # TC suggestion?
        chip_recs = self.chip_analysis.get("recommendations", [])
        tc_rec = next((r for r in chip_recs if r["chip"] == "triple_captain"), None)
        if tc_rec and tc_rec.get("score", 0) >= 60:
            sections.append(f"\n💡 **Triple Captain alert!** TC scores {tc_rec['score']}/100 this week. Consider using it on {cap.get('name', '?')}.")

        return {
            "answer": "\n".join(sections),
            "data": {
                "type": "captain",
                "captain": self._player_card(cap) if cap else None,
                "top5": [self._player_card(p) for p in top5]
            },
            "suggestions": [
                f"Compare {cap.get('name', '?')} vs {top5[1]['name'] if len(top5) > 1 else '?'} as captain",
                "Should I use Triple Captain this week?",
                "Best differential captain?"
            ]
        }

    def _handle_chip(self, q: str) -> dict:
        """Chip strategy advice."""
        gw = self.gw_info.get("gameweek", "?")
        recs = self.chip_analysis.get("recommendations", [])
        best = self.chip_analysis.get("best_chip", {})

        sections = []
        sections.append(f"## 🎯 Chip Strategy — GW{gw}\n")

        if self.gw_info.get("is_dgw"):
            dgw_count = len(self.gw_info.get("dgw_teams", {}))
            sections.append(f"⚡ **This is a DGW** with {dgw_count} teams playing twice!\n")

        if best:
            sections.append(f"### Recommended: {best.get('name', '?')} ({best.get('code', '?')}) — Score: {best.get('score', 0)}/100\n")
            for reason in best.get("reasons", []):
                sections.append(f"✅ {reason}")
            sections.append("")

        sections.append("### All Chips Ranked\n")
        sections.append("| Chip | Score | Recommendation |")
        sections.append("|------|-------|---------------|")
        for rec in recs:
            emoji = "🟢" if rec["score"] >= 70 else "🟡" if rec["score"] >= 40 else "🔴"
            sections.append(f"| {emoji} **{rec['name']}** | {rec['score']}/100 | {rec['reasons'][0] if rec['reasons'] else 'N/A'} |")

        # Specific chip advice
        if "bench boost" in q or "bb" in q:
            bb = next((r for r in recs if r["chip"] == "bench_boost"), None)
            if bb:
                sections.append(f"\n### Bench Boost Deep Dive")
                sections.append(f"Score: **{bb['score']}/100**")
                for r in bb.get("reasons", []):
                    sections.append(f"- {r}")
                sections.append(f"\n**BB works best when**: All 15 players are nailed DGW starters. This maximizes the extra points from your bench playing.")

        elif "triple captain" in q or "tc" in q:
            tc = next((r for r in recs if r["chip"] == "triple_captain"), None)
            if tc:
                sections.append(f"\n### Triple Captain Deep Dive")
                sections.append(f"Score: **{tc['score']}/100**")
                for r in tc.get("reasons", []):
                    sections.append(f"- {r}")
                cap = self.squad.get("captain", {})
                if cap:
                    sections.append(f"\n**Best TC target**: {cap['name']} — {cap.get('predicted_points', 0):.1f} xPts{'  [DGW]' if cap.get('is_dgw') else ''}")

        return {
            "answer": "\n".join(sections),
            "data": {"type": "chip", "recommendations": recs, "best": best},
            "suggestions": [
                "Should I use Bench Boost or Triple Captain?",
                "When is the best time to use Free Hit?",
                "Who should I captain if I TC?"
            ]
        }

    def _handle_transfer(self, q: str, original: str) -> dict:
        """Transfer advice."""
        gw = self.gw_info.get("gameweek", "?")

        # Check if specific player mentioned
        names = self._extract_player_names(q)
        sections = []
        sections.append(f"## 🔄 Transfer Advice — GW{gw}\n")

        if names:
            # Specific player transfer question
            player = self._find_player(names[0])
            if player:
                sections.append(f"### {'Sell' if 'sell' in q else 'Buy'} {player['name']}?\n")
                sections.append(f"- **Predicted points**: {player['predicted_points']:.1f}")
                sections.append(f"- **Form**: {player.get('form', '--')}")
                sections.append(f"- **DGW?**: {'Yes ✅' if player.get('is_dgw') else 'No'}")
                sections.append(f"- **Tier**: {player.get('starter_quality', {}).get('tier', '?')}")

                # Find alternatives
                pos = player["position"].lower()
                budget = player["price"] + 1.0
                alternatives = [p for p in self._by_position.get(pos, [])
                               if p["name"] != player["name"]
                               and p["predicted_points"] > player["predicted_points"]
                               and p["price"] <= budget][:5]

                if alternatives:
                    sections.append(f"\n### Better alternatives (≤ £{budget:.1f}m):\n")
                    for alt in alternatives:
                        gain = alt["predicted_points"] - player["predicted_points"]
                        sections.append(f"- **{alt['name']}** ({alt['team']}) — {alt['predicted_points']:.1f} xPts — £{alt['price']:.1f}m — +{gain:.1f} pts {'🔥 DGW' if alt.get('is_dgw') else ''}")
        else:
            # General transfer advice
            sections.append("### Top Transfer Targets This Week\n")

            for pos_name, pos_key in [("Goalkeepers", "gkp"), ("Defenders", "def"),
                                        ("Midfielders", "mid"), ("Forwards", "fwd")]:
                pos_picks = sorted(self._by_position.get(pos_key, []),
                                  key=lambda x: x["predicted_points"], reverse=True)[:3]
                sections.append(f"\n**{pos_name}:**")
                for p in pos_picks:
                    dgw = " 🔥DGW" if p.get("is_dgw") else ""
                    sections.append(f"- {p['name']} ({p['team']}) — {p['predicted_points']:.1f} xPts — £{p['price']:.1f}m{dgw}")

            # Value transfers
            sections.append("\n### Best Value Transfers (≤ £6.5m):")
            value = [p for p in self.predictions
                    if p["price"] <= 6.5
                    and p.get("starter_quality", {}).get("tier") in ("nailed", "regular")][:5]
            for p in value:
                sections.append(f"- {p['name']} ({p['team']}, {p['position']}) — {p['predicted_points']:.1f} xPts — £{p['price']:.1f}m")

        return {
            "answer": "\n".join(sections),
            "data": {"type": "transfer"},
            "suggestions": [
                "Best budget midfielders under £7m?",
                "Who are the best DGW defenders?",
                "Should I take a hit this week?"
            ]
        }

    def _handle_player_lookup(self, q: str, original: str) -> dict:
        """Look up a specific player's prediction details."""
        names = self._extract_player_names(q)
        if not names:
            # Try original case
            names = self._extract_player_names(original.lower())
        if not names:
            return self._fallback("Which player? Try: 'How many points will Haaland get?'")

        player = self._find_player(names[0])
        if not player:
            return self._fallback(f"Couldn't find player: {names[0]}. Try the full name or web name.")

        gw = self.gw_info.get("gameweek", "?")
        p = player
        sections = []
        sections.append(f"## {p['name']} — GW{gw} Prediction\n")
        sections.append(f"**Predicted Points: {p['predicted_points']:.1f}**")
        if p.get("is_dgw"):
            sections.append(f"🔥 **DGW** — plays {p.get('num_fixtures', 2)} fixtures!\n")

        sections.append(f"\n| Metric | Value |")
        sections.append(f"|--------|-------|")
        sections.append(f"| Team | {p.get('team_name', p['team'])} |")
        sections.append(f"| Position | {p['position']} |")
        sections.append(f"| Price | £{p['price']:.1f}m |")
        sections.append(f"| Form | {p.get('form', '--')} |")
        sections.append(f"| PPG | {p.get('ppg', '--')} |")
        sections.append(f"| Season Total | {p.get('total_points', '--')} pts |")
        sections.append(f"| ICT Index | {p.get('ict_index', '--')} |")
        sections.append(f"| Starts | {p.get('starts', '--')} |")
        sections.append(f"| Minutes | {p.get('minutes', '--')} |")
        sections.append(f"| Ownership | {p.get('selected_by_percent', '--')}% |")
        sections.append(f"| Starter Tier | {p.get('starter_quality', {}).get('tier', '?')} |")
        sections.append(f"| Confidence | {int(p.get('confidence', 0)*100)}% |")

        # Fixtures
        sections.append(f"\n### Fixtures")
        for f in p.get("fixtures", []):
            sections.append(f"- vs **{f.get('opponent', '?')}** ({f.get('venue', '?')}) — FDR: {f.get('fdr', '?')} — {f.get('xp_single', 0):.1f} xPts")

        # Factors
        factors = p.get("factors", {})
        if factors:
            sections.append(f"\n### Factor Breakdown")
            factor_names = {
                "form": "📈 Form", "fixture_difficulty": "🎯 Fixture",
                "ict_index": "⚡ ICT", "season_avg": "📊 Season",
                "minutes_consistency": "🔒 Minutes", "home_away": "🏟️ Home/Away",
                "team_strength": "💪 Team", "set_pieces": "⚽ Set Pieces",
                "ownership_momentum": "📊 Transfers", "bonus_tendency": "🌟 Bonus"
            }
            for k, v in sorted(factors.items(), key=lambda x: abs(x[1]), reverse=True):
                label = factor_names.get(k, k)
                arrow = "▲" if v > 0 else "▼" if v < 0 else "—"
                sections.append(f"- {label}: {arrow} {v:+.3f}")

        # Rank
        rank = next((i+1 for i, pp in enumerate(self.predictions) if pp["player_id"] == p["player_id"]), "?")
        sections.append(f"\n**Overall rank: #{rank}** out of {len(self.predictions)} players")

        return {
            "answer": "\n".join(sections),
            "data": {"type": "player", "player": self._player_card(p)},
            "suggestions": [
                f"Who are the best {p['position']} picks this week?",
                f"Compare {p['name']} vs ?",
                f"Should I captain {p['name']}?"
            ]
        }

    def _handle_team_query(self, q: str, original: str) -> dict:
        """Best players from a specific team."""
        team = self._extract_team(q)
        if not team:
            return self._fallback("Which team? Try: 'Best Arsenal players this week'")

        team_players = self._by_team.get(team, [])
        if not team_players:
            # Try partial match
            for t_key, t_list in self._by_team.items():
                if team in t_key or t_key in team:
                    team_players = t_list
                    team = t_key
                    break

        if not team_players:
            return self._fallback(f"No players found for team: {team}")

        team_players.sort(key=lambda x: x["predicted_points"], reverse=True)
        gw = self.gw_info.get("gameweek", "?")
        team_name = team_players[0].get("team_name", team.upper())
        is_dgw = team_players[0].get("is_dgw", False)

        sections = []
        sections.append(f"## {team_name} — GW{gw} {'🔥 DGW' if is_dgw else ''}\n")

        sections.append("| Player | Pos | xPts | Price | Form | Tier |")
        sections.append("|--------|-----|------|-------|------|------|")
        for p in team_players[:10]:
            sections.append(f"| **{p['name']}** | {p['position']} | {p['predicted_points']:.1f} | £{p['price']:.1f}m | {p.get('form', '--')} | {p.get('starter_quality', {}).get('tier', '?')} |")

        # Best pick
        best = team_players[0]
        sections.append(f"\n**Best pick: {best['name']}** — {best['predicted_points']:.1f} xPts")

        return {
            "answer": "\n".join(sections),
            "data": {"type": "team", "team": team.upper(), "players": [self._player_card(p) for p in team_players[:5]]},
            "suggestions": [
                f"Should I triple up on {team_name}?",
                f"Compare {team_players[0]['name']} vs {team_players[1]['name']}" if len(team_players) > 1 else "Best captain pick?",
                "Which DGW teams should I target?"
            ]
        }

    def _handle_position_query(self, q: str, original: str) -> dict:
        """Best players by position."""
        pos = "mid"  # default
        if any(w in q for w in ["goalkeeper", "gk", "gkp", "keeper"]):
            pos = "gkp"
        elif any(w in q for w in ["defender", "def", "cb", "fullback", "centre-back"]):
            pos = "def"
        elif any(w in q for w in ["midfielder", "mid", "winger", "cam", "cm"]):
            pos = "mid"
        elif any(w in q for w in ["forward", "fwd", "striker", "cf", "st"]):
            pos = "fwd"

        players = sorted(self._by_position.get(pos, []),
                        key=lambda x: x["predicted_points"], reverse=True)[:10]

        gw = self.gw_info.get("gameweek", "?")
        pos_full = {"gkp": "Goalkeepers", "def": "Defenders", "mid": "Midfielders", "fwd": "Forwards"}[pos]

        sections = []
        sections.append(f"## Best {pos_full} — GW{gw}\n")
        sections.append("| # | Player | Team | xPts | Price | Form | DGW? | Tier |")
        sections.append("|---|--------|------|------|-------|------|------|------|")
        for i, p in enumerate(players, 1):
            dgw = "✅" if p.get("is_dgw") else ""
            sections.append(f"| {i} | **{p['name']}** | {p['team']} | {p['predicted_points']:.1f} | £{p['price']:.1f}m | {p.get('form', '--')} | {dgw} | {p.get('starter_quality', {}).get('tier', '?')} |")

        # Budget pick
        budget = [p for p in self._by_position.get(pos, [])
                 if p["price"] <= 6.0 and p.get("starter_quality", {}).get("tier") in ("nailed", "regular")]
        budget.sort(key=lambda x: x["predicted_points"], reverse=True)
        if budget:
            sections.append(f"\n💰 **Best budget pick**: {budget[0]['name']} ({budget[0]['team']}) — £{budget[0]['price']:.1f}m — {budget[0]['predicted_points']:.1f} xPts")

        return {
            "answer": "\n".join(sections),
            "data": {"type": "position", "position": pos.upper(), "players": [self._player_card(p) for p in players[:5]]},
            "suggestions": [
                f"Compare {players[0]['name']} vs {players[1]['name']}" if len(players) > 1 else "Best captain?",
                f"Best budget {pos_full.lower()}?",
                f"Best differential {pos_full.lower()}?"
            ]
        }

    def _handle_dgw(self, q: str) -> dict:
        """DGW-specific advice."""
        gw = self.gw_info.get("gameweek", "?")
        dgw_teams = self.gw_info.get("dgw_teams", {})
        dgw_players = [p for p in self.predictions if p.get("is_dgw")]
        dgw_players.sort(key=lambda x: x["predicted_points"], reverse=True)

        sections = []
        sections.append(f"## 🔥 Double Gameweek {gw}\n")

        if not dgw_teams:
            sections.append("This is a standard gameweek — no teams play twice.")
            return {"answer": "\n".join(sections), "data": {"type": "dgw"}, "suggestions": ["Best picks this GW?", "Captain advice?"]}

        sections.append(f"**{len(dgw_teams)} teams play twice:**\n")
        for tid, info in dgw_teams.items():
            sections.append(f"- **{info.get('name', '?')}** ({info.get('short_name', '?')}) — {info.get('fixture_count', 2)} fixtures")

        sections.append(f"\n### Top DGW Players\n")
        sections.append("| # | Player | Team | Pos | xPts | Price | Form |")
        sections.append("|---|--------|------|-----|------|-------|------|")
        for i, p in enumerate(dgw_players[:15], 1):
            sections.append(f"| {i} | **{p['name']}** | {p['team']} | {p['position']} | {p['predicted_points']:.1f} | £{p['price']:.1f}m | {p.get('form', '--')} |")

        sections.append(f"\n**Key DGW strategy tips:**")
        sections.append(f"- Prioritize nailed DGW starters — they get 2 bites at the cherry")
        sections.append(f"- Captain the highest-ceiling DGW player (usually premium FWD/MID)")
        sections.append(f"- Consider Bench Boost if your full 15 are DGW nailed starters")
        sections.append(f"- DGW defenders from clean-sheet-likely teams are gold")

        return {
            "answer": "\n".join(sections),
            "data": {"type": "dgw", "dgw_teams": list(dgw_teams.values()), "top_players": [self._player_card(p) for p in dgw_players[:5]]},
            "suggestions": [
                "Should I Bench Boost this DGW?",
                "Best DGW defenders?",
                "Best DGW captain?"
            ]
        }

    def _handle_squad(self, q: str) -> dict:
        """Squad/lineup explanation."""
        gw = self.gw_info.get("gameweek", "?")
        sq = self.squad
        xi = sq.get("starting_xi", [])
        bench = sq.get("bench", [])

        sections = []
        sections.append(f"## ⚽ Optimal Squad — GW{gw}\n")
        sections.append(f"**Formation**: {sq.get('formation', '?')} | **Cost**: £{sq.get('total_cost', '?')}m | **Predicted**: {sq.get('predicted_total_points', '?')} pts\n")
        sections.append(f"**Captain**: {sq.get('captain', {}).get('name', '?')} | **Vice**: {sq.get('vice_captain', {}).get('name', '?')}\n")

        sections.append("### Starting XI\n")
        for p in xi:
            fix = ", ".join(f"{f['opponent']}({f['venue']})" for f in p.get("fixtures", []))
            sections.append(f"- **{p['name']}** ({p['position']}, {p['team']}) — {p['predicted_points']:.1f} xPts — {fix} {'🔥DGW' if p.get('is_dgw') else ''}")

        sections.append(f"\n### Bench")
        for i, p in enumerate(bench):
            sections.append(f"- B{i+1}: {p['name']} ({p['position']}, {p['team']}) — {p['predicted_points']:.1f} xPts")

        dgw_in_xi = sum(1 for p in xi if p.get("is_dgw"))
        sections.append(f"\n📊 **DGW players in XI**: {dgw_in_xi}/{len(xi)}")

        return {
            "answer": "\n".join(sections),
            "data": {"type": "squad"},
            "suggestions": [
                "Why is this the best formation?",
                "Should I use Bench Boost?",
                "Who's the best captain?"
            ]
        }

    def _handle_differentials(self, q: str) -> dict:
        """Low-ownership differential picks."""
        gw = self.gw_info.get("gameweek", "?")
        diffs = [p for p in self.predictions
                if float(p.get("selected_by_percent", 100)) < 10
                and p.get("starter_quality", {}).get("tier") in ("nailed", "regular")]
        diffs.sort(key=lambda x: x["predicted_points"], reverse=True)

        sections = []
        sections.append(f"## 💎 Differentials — GW{gw}\n")
        sections.append("Low-ownership players (<10%) who can gain you a big rank boost:\n")

        sections.append("| # | Player | Team | Pos | xPts | Price | Own% | DGW? |")
        sections.append("|---|--------|------|-----|------|-------|------|------|")
        for i, p in enumerate(diffs[:10], 1):
            dgw = "✅" if p.get("is_dgw") else ""
            sections.append(f"| {i} | **{p['name']}** | {p['team']} | {p['position']} | {p['predicted_points']:.1f} | £{p['price']:.1f}m | {p.get('selected_by_percent', '?')}% | {dgw} |")

        if diffs:
            sections.append(f"\n🔥 **Top differential**: {diffs[0]['name']} — Only {diffs[0].get('selected_by_percent', '?')}% owned but predicted {diffs[0]['predicted_points']:.1f} pts!")

        return {
            "answer": "\n".join(sections),
            "data": {"type": "differentials", "players": [self._player_card(p) for p in diffs[:5]]},
            "suggestions": [
                f"Tell me more about {diffs[0]['name']}" if diffs else "Best picks this week?",
                "Best differential captain?",
                "Best budget differentials?"
            ]
        }

    def _handle_general(self, q: str, original: str) -> dict:
        """General/catch-all handler — try to extract player names or give overview."""
        # Try to find any player name in the question
        names = self._extract_player_names(q)
        if names:
            player = self._find_player(names[0])
            if player:
                return self._handle_player_lookup(q, original)

        gw = self.gw_info.get("gameweek", "?")
        cap = self.squad.get("captain", {})

        sections = []
        sections.append(f"## GW{gw} Quick Summary\n")
        sections.append(f"- **Top pick**: {self.predictions[0]['name']} ({self.predictions[0]['predicted_points']:.1f} xPts)" if self.predictions else "No predictions available")
        sections.append(f"- **Captain**: {cap.get('name', '?')} ({cap.get('predicted_points', 0):.1f} xPts)")
        if self.gw_info.get("is_dgw"):
            dgw_count = len(self.gw_info.get("dgw_teams", {}))
            sections.append(f"- **DGW**: {dgw_count} teams with double fixtures")
        best_chip = self.chip_analysis.get("best_chip", {})
        if best_chip:
            sections.append(f"- **Chip advice**: {best_chip.get('name', '?')} ({best_chip.get('score', 0)}/100)")

        sections.append(f"\nI can help with:")
        sections.append(f"- 🔄 **Player comparisons**: 'Compare Salah vs Haaland'")
        sections.append(f"- 👑 **Captain advice**: 'Who should I captain?'")
        sections.append(f"- 🎯 **Chip strategy**: 'Should I Bench Boost?'")
        sections.append(f"- 📊 **Player lookups**: 'How many points will Haaland get?'")
        sections.append(f"- 💰 **Transfer advice**: 'Best midfielders to buy?'")
        sections.append(f"- 💎 **Differentials**: 'Best differential picks?'")
        sections.append(f"- ⚽ **Squad help**: 'Show me the optimal squad'")

        return {
            "answer": "\n".join(sections),
            "data": {"type": "general"},
            "suggestions": [
                "Who should I captain this GW?",
                "Best DGW players?",
                "Show me the optimal squad",
                "Best differential picks?"
            ]
        }

    # ── Utilities ───────────────────────────────────────────

    def _extract_player_names(self, q: str) -> list[str]:
        """Extract likely player names from a question."""
        # Remove common words
        noise = {'who', 'what', 'why', 'how', 'should', 'pick', 'get', 'buy', 'sell',
                 'the', 'this', 'that', 'over', 'instead', 'better', 'between', 'compare',
                 'and', 'or', 'vs', 'versus', 'with', 'for', 'from', 'captain', 'cap',
                 'many', 'points', 'will', 'dgw', 'gw', 'gameweek', 'week', 'best',
                 'is', 'it', 'he', 'his', 'my', 'team', 'player', 'players',
                 'a', 'an', 'in', 'on', 'i', 'me', 'of', 'to', 'do', 'does',
                 'good', 'bad', 'worth', 'any', 'more', 'can', 'than', 'would',
                 'transfer', 'bring', 'swap', 'replace', 'predicted', 'predict',
                 'expected', 'about', 'tell', 'show', 'have', 'be'}

        found = []
        # Match against known player names
        for name, p_data in self._by_name.items():
            if len(name) > 2 and name in q and name not in noise:
                web_name = p_data.get("name", "").lower()
                if web_name not in [f.lower() for f in found]:
                    found.append(p_data.get("name", name))

        # Deduplicate preserving order
        seen = set()
        result = []
        for n in found:
            if n.lower() not in seen:
                seen.add(n.lower())
                result.append(n)

        return result[:2]

    def _find_player(self, name: str) -> Optional[dict]:
        """Find player by name (fuzzy)."""
        name_lower = name.lower()
        # Exact match
        if name_lower in self._by_name:
            return self._by_name[name_lower]
        # Partial match
        for key, p in self._by_name.items():
            if name_lower in key or key in name_lower:
                return p
        # Search in full names
        for p in self.predictions:
            full = p.get("full_name", "").lower()
            web = p.get("name", "").lower()
            if name_lower in full or name_lower in web:
                return p
        return None

    def _extract_team(self, q: str) -> Optional[str]:
        """Extract a team name from the question."""
        team_aliases = {
            "arsenal": "ars", "ars": "ars",
            "aston villa": "avl", "villa": "avl", "avl": "avl",
            "bournemouth": "bou", "bou": "bou",
            "brentford": "bre", "bre": "bre",
            "brighton": "bha", "bha": "bha",
            "chelsea": "che", "che": "che",
            "crystal palace": "cry", "palace": "cry", "cry": "cry",
            "everton": "eve", "eve": "eve",
            "fulham": "ful", "ful": "ful",
            "ipswich": "ips", "ips": "ips",
            "leicester": "lei", "lei": "lei",
            "liverpool": "liv", "liv": "liv",
            "man city": "mci", "city": "mci", "mci": "mci", "manchester city": "mci",
            "man utd": "mun", "united": "mun", "mun": "mun", "manchester united": "mun",
            "newcastle": "new", "new": "new",
            "nottingham": "nfo", "forest": "nfo", "nfo": "nfo", "nottingham forest": "nfo",
            "southampton": "sou", "sou": "sou",
            "spurs": "tot", "tottenham": "tot", "tot": "tot",
            "west ham": "whu", "whu": "whu",
            "wolves": "wol", "wol": "wol", "wolverhampton": "wol",
            "burnley": "bur", "bur": "bur",
            "luton": "lut", "lut": "lut",
            "sheffield": "shu", "shu": "shu",
            "sunderland": "sun", "sun": "sun",
        }
        for alias, code in team_aliases.items():
            if alias in q:
                return code
        return None

    def _player_card(self, p: dict) -> dict:
        """Minimal player card for frontend rendering."""
        if not p:
            return {}
        return {
            "name": p.get("name", "?"),
            "team": p.get("team", "?"),
            "position": p.get("position", "?"),
            "price": p.get("price", 0),
            "predicted_points": p.get("predicted_points", 0),
            "form": p.get("form", 0),
            "is_dgw": p.get("is_dgw", False),
            "fixtures": p.get("fixtures", []),
            "confidence": p.get("confidence", 0),
            "starter_quality": p.get("starter_quality", {}),
        }

    def _fallback(self, msg: str) -> dict:
        return {
            "answer": msg,
            "data": {"type": "error"},
            "suggestions": [
                "Who should I captain this GW?",
                "Compare Salah vs Haaland",
                "Best DGW players?",
                "Show me the optimal squad"
            ]
        }
