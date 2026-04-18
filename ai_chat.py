"""
FPL Predictor — AI Chat Engine v2
Semantic NLU engine that understands natural language questions about FPL.

Instead of rigid keyword matching, this engine:
1. Tokenizes and normalizes the question
2. Extracts entities (player names, team names, positions, price ranges, GW refs)
3. Scores multiple intents simultaneously using weighted semantic signals
4. Routes to the highest-confidence handler
5. Generates context-aware, data-backed answers with reasoning
"""
import re
import math
from typing import Optional
from difflib import SequenceMatcher


class FPLChatEngine:
    """
    Context-aware chat engine that answers FPL questions using live prediction data.
    Uses semantic intent scoring — not keyword matching — to understand user questions.
    """

    # ── Semantic signal definitions ──────────────────────────
    # Each intent has a list of (signal_pattern, weight) tuples.
    # Patterns are regex. The engine scores ALL intents for every question,
    # then picks the highest scorer.

    INTENT_SIGNALS = {
        "comparison": [
            (r'\bvs\.?\b', 3.0), (r'\bversus\b', 3.0), (r'\bcompare\b', 2.5),
            (r'\bover\b', 1.5), (r'\binstead\s+of\b', 2.5), (r'\brather\s+than\b', 2.5),
            (r'\bor\b', 1.0), (r'\bbetter\b', 1.5), (r'\bworse\b', 1.5),
            (r'\bpick\b.*\bover\b', 3.0), (r'\bchoose\b.*\bover\b', 3.0),
            (r'\bwhy\b.*\bover\b', 3.0), (r'\bwhy\b.*\binstead\b', 3.0),
            (r'\bwhy\b.*\bnot\b', 2.0), (r'\bdifference\b', 2.0),
            (r'\bhead\s*to\s*head\b', 3.0), (r'\bh2h\b', 3.0),
        ],
        "captain": [
            (r'\bcaptain\b', 3.0), (r'\bcap\b(?!ital|ture|acity)', 2.5),
            (r'\barmband\b', 3.0), (r'\btriple\s+captain\b', 2.5), (r'\btc\b', 2.0),
            (r'\bwho\b.*\bcaptain\b', 3.5), (r'\bcaptaincy\b', 3.0),
            (r'\bshould\s+i\s+captain\b', 3.5), (r'\bgive\b.*\bband\b', 2.0),
            (r'\bwho\b.*\b(c|vc)\b', 2.0),
        ],
        "chip": [
            (r'\bchip\b', 3.0), (r'\bbench\s*boost\b', 3.5), (r'\bbb\b', 2.5),
            (r'\bfree\s*hit\b', 3.5), (r'\bwildcard\b', 3.5), (r'\bwc\b', 2.0),
            (r'\btriple\s+captain\b', 2.5), (r'\btc\b', 2.0),
            (r'\bwhen\b.*\buse\b', 2.0), (r'\bactivate\b', 2.0),
            (r'\bplay\b.*\bchip\b', 3.0), (r'\bsave\b.*\bchip\b', 3.0),
        ],
        "transfer": [
            (r'\btransfer\b', 3.0), (r'\bbring\s+in\b', 3.0), (r'\bsell\b', 2.5),
            (r'\bbuy\b', 2.5), (r'\breplace\b', 2.5), (r'\bswap\b', 2.5),
            (r'\bupgrade\b', 2.5), (r'\bdowngrade\b', 2.5), (r'\bdrop\b', 2.0),
            (r'\bget\s+rid\b', 2.5), (r'\bremove\b', 2.0), (r'\btake\s+out\b', 2.5),
            (r'\bkeep\b.*\bor\b.*\bsell\b', 3.0), (r'\bhit\b', 1.5),
            (r'\bfree\s+transfer\b', 3.0), (r'\bft\b', 1.5),
            (r'\bwho\b.*\b(buy|sell|transfer|replace)\b', 3.0),
            (r'\balternative\b', 2.0), (r'\breplacement\b', 2.5),
            (r'\bshould\s+i\s+(sell|buy|get|keep|drop)\b', 3.0),
        ],
        "player_lookup": [
            (r'\bhow\s+many\s+points\b', 3.0), (r'\bpredict\b', 2.5),
            (r'\bexpected\b', 2.0), (r'\bxpts?\b', 2.5), (r'\bwhat\b.*\bscore\b', 2.5),
            (r'\btell\s+me\s+about\b', 2.5), (r'\bstats?\b.*\bfor\b', 2.5),
            (r'\bshow\s+me\b', 1.5), (r'\binfo\b', 1.5), (r'\bdetails?\b', 1.5),
            (r'\bgood\s+pick\b', 2.0), (r'\bbad\s+pick\b', 2.0),
            (r'\bworth\b.*\b(it|getting|buying)\b', 2.5),
            (r'\bhow\b.*\b(is|are|will|would)\b', 1.5),
            (r'\bwhat\s+do\s+you\s+think\b', 2.0),
            (r'\bshould\s+i\s+get\b', 2.0), (r'\bis\b.*\bgood\b', 1.5),
        ],
        "team_query": [
            (r'\bplayers?\s+from\b', 3.0), (r'\bbest\b.*\b(from|in|at)\b', 2.0),
            (r'\bwho\b.*\b(from|at)\b', 2.0), (r'\btriple\s+up\b', 2.5),
            (r'\bassets?\b', 2.0), (r'\btargets?\s+from\b', 2.5),
        ],
        "position_query": [
            (r'\bbest\s+(goalkeepers?|gkp?|keepers?)\b', 3.5),
            (r'\bbest\s+(defenders?|def|cbs?|fullbacks?)\b', 3.5),
            (r'\bbest\s+(midfielders?|mid|wingers?|cams?)\b', 3.5),
            (r'\bbest\s+(forwards?|fwd|strikers?|cf)\b', 3.5),
            (r'\btop\s+(gkp|def|mid|fwd)\b', 3.0),
            (r'\bwhich\s+(keeper|goalkeeper|defender|midfielder|forward|striker)\b', 3.0),
            (r'\bbest\b.*\b(under|below|budget)\b.*\b(mid|def|fwd|gk)\b', 3.5),
            (r'\bcheap(est)?\s+(mid|def|fwd|gk)\b', 3.0),
        ],
        "dgw": [
            (r'\bdgw\b', 3.0), (r'\bdouble\s*(gw|gameweek)\b', 3.5),
            (r'\btwo\s+fixtures\b', 2.5), (r'\bplay\s+twice\b', 3.0),
            (r'\bblank\b', 2.0), (r'\bbgw\b', 2.5),
            (r'\bdouble\b', 1.5), (r'\bwhich\s+teams?\s+(play|have)\s+twice\b', 3.0),
        ],
        "squad": [
            (r'\bsquad\b', 3.0), (r'\bstarting\b', 2.0), (r'\blineup\b', 3.0),
            (r'\bformation\b', 2.5), (r'\bbest\s+team\b', 3.0), (r'\boptimal\b', 2.5),
            (r'\bxi\b', 3.0), (r'\beleven\b', 2.0), (r'\bstarting\s+eleven\b', 3.5),
            (r'\bshow\b.*\bsquad\b', 3.0), (r'\bwhy\b.*\bformation\b', 3.0),
            (r'\bbench\b', 2.0),
        ],
        "differential": [
            (r'\bdifferential\b', 3.5), (r'\bunder.?owned\b', 3.0),
            (r'\blow\s+ownership\b', 3.0), (r'\bpunt\b', 2.5),
            (r'\bhidden\s+gem\b', 3.0), (r'\bsleeper\b', 2.5),
            (r'\blow\b.*\bowned\b', 2.5), (r'\bunique\b', 1.5),
            (r'\bnobody\s+(has|owns)\b', 3.0), (r'\btemplat\b', 1.5),
            (r'\bnot\s+many\b.*\b(own|have)\b', 2.5), (r'\bnobody\b', 2.0),
            (r'\bgems?\b', 2.5), (r'\bunder\s*the\s*radar\b', 2.5),
        ],
        "value": [
            (r'\bbudget\b', 2.5), (r'\bcheap\b', 2.5), (r'\bvalue\b', 2.5),
            (r'\baffordable\b', 2.5), (r'\bunder\s*\d+\.?\d*\s*m?\b', 3.0),
            (r'\bbargain\b', 2.5), (r'\bbang\s+for\b', 2.5),
            (r'\benabled?r?\b', 1.5), (r'\bprice\b.*\brise\b', 2.0),
        ],
        "what_if": [
            (r'\bif\b.*\b(100|guaranteed|nailed|definitely|certain|sure|will)\b.*\bplay\b', 4.0),
            (r'\bif\b.*\bplay\b.*\bboth\b', 4.0), (r'\bif\b.*\bstarts?\b.*\bboth\b', 4.0),
            (r'\bwhat\s+if\b', 3.0), (r'\bassume\b', 2.5), (r'\bhypothetical\b', 3.0),
            (r'\bif\b.*\b(plays?|starts?)\b.*\b(90|full|every)\b', 3.5),
            (r'\bwithout\b.*\b(discount|rotation|risk)\b', 3.0),
            (r'\braw\b.*\bxpts?\b', 3.0), (r'\bmax\b.*\bxpts?\b', 2.5),
            (r'\bceiling\b', 2.5), (r'\bupside\b', 2.0),
            (r'\bif\b.*\b100%\b', 3.5), (r'\bper\s*fixture\b', 3.0),
            (r'\bbreakdown\b', 2.5), (r'\bper\s*game\b', 2.5),
        ],
        "methodology": [
            (r'\bhow\b.*\b(do\s+you|does\s+(it|the\s+model|this))\b.*\b(calculate|compute|evaluate|measure|estimate|predict|work)\b', 4.0),
            (r'\bhow\b.*\b(is|are)\b.*\bcalculated\b', 3.5),
            (r'\bwhat\b.*\b(is|are)\b.*\b(based\s+on|formula|algorithm|methodology)\b', 3.5),
            (r'\bexplain\b.*\b(model|prediction|algorithm|calculation|methodology)\b', 3.5),
            (r'\bhow\b.*\bwork\b', 2.5), (r'\bwhy\b.*\b(is|are)\b.*\b(the\s+same|different|higher|lower)\b', 3.0),
            (r'\bwin\s*rate\b', 2.5), (r'\bxpts?\b.*\b(calculated|computed|formula)\b', 3.0),
            (r'\btier\b.*\b(mean|determined|calculated)\b', 3.0), (r'\bfdr\b.*\b(mean|work|calculated)\b', 3.0),
            (r'\bconfidence\b.*\b(mean|calculated|score)\b', 3.0), (r'\bform\b.*\b(calculated|measured|work)\b', 2.5),
            (r'\bmomentum\b.*\b(calculated|mean|work)\b', 2.5), (r'\brotation\b.*\b(risk|calculated|work)\b', 2.5),
            (r'\bxg\b.*\b(calculated|mean|work|from)\b', 2.5), (r'\bdocumentation\b', 2.0),
            (r'\bmethod(ology)?\b', 3.0), (r'\bmodel\b.*\b(work|use|factor)\b', 2.5),
        ],
    }

    # Team aliases — maps various ways to refer to a team to its FPL 3-letter code
    TEAM_ALIASES = {
        "arsenal": "ars", "ars": "ars", "gunners": "ars",
        "aston villa": "avl", "villa": "avl", "avl": "avl",
        "bournemouth": "bou", "bou": "bou", "cherries": "bou",
        "brentford": "bre", "bre": "bre", "bees": "bre",
        "brighton": "bha", "bha": "bha", "seagulls": "bha",
        "chelsea": "che", "che": "che", "blues": "che",
        "crystal palace": "cry", "palace": "cry", "cry": "cry", "eagles": "cry",
        "everton": "eve", "eve": "eve", "toffees": "eve",
        "fulham": "ful", "ful": "ful", "cottagers": "ful",
        "ipswich": "ips", "ips": "ips", "tractor boys": "ips",
        "leicester": "lei", "lei": "lei", "foxes": "lei",
        "liverpool": "liv", "liv": "liv", "lfc": "liv", "reds": "liv",
        "man city": "mci", "city": "mci", "mci": "mci", "manchester city": "mci", "mcfc": "mci",
        "man utd": "mun", "united": "mun", "mun": "mun", "manchester united": "mun", "mufc": "mun",
        "newcastle": "new", "new": "new", "magpies": "new", "nufc": "new", "toon": "new",
        "nottingham": "nfo", "forest": "nfo", "nfo": "nfo", "nottingham forest": "nfo",
        "southampton": "sou", "sou": "sou", "saints": "sou",
        "spurs": "tot", "tottenham": "tot", "tot": "tot", "thfc": "tot",
        "west ham": "whu", "whu": "whu", "hammers": "whu",
        "wolves": "wol", "wol": "wol", "wolverhampton": "wol",
        "burnley": "bur", "bur": "bur", "clarets": "bur",
        "luton": "lut", "lut": "lut", "hatters": "lut",
        "sheffield": "shu", "shu": "shu", "blades": "shu",
        "sunderland": "sun", "sun": "sun", "black cats": "sun",
    }

    # Position aliases
    POS_ALIASES = {
        "goalkeeper": "gkp", "gk": "gkp", "gkp": "gkp", "keeper": "gkp", "keepers": "gkp",
        "goalkeepers": "gkp",
        "defender": "def", "def": "def", "defenders": "def", "cb": "def", "fullback": "def",
        "centre-back": "def", "center-back": "def", "fullbacks": "def", "rb": "def", "lb": "def",
        "midfielder": "mid", "mid": "mid", "midfielders": "mid", "winger": "mid", "cam": "mid",
        "cm": "mid", "wingers": "mid", "am": "mid",
        "forward": "fwd", "fwd": "fwd", "forwards": "fwd", "striker": "fwd", "strikers": "fwd",
        "cf": "fwd", "st": "fwd", "attacker": "fwd", "attackers": "fwd",
    }

    def __init__(self, predictions: list[dict], squad: dict,
                 gw_info: dict, chip_analysis: dict,
                 players_map: dict = None, bb_squad: dict = None):
        self.predictions = predictions
        self.squad = squad
        self.gw_info = gw_info
        self.chip_analysis = chip_analysis
        self.players_map = players_map or {}
        self.bb_squad = bb_squad or {}

        # Build search indices
        self._by_name = {}
        self._by_team = {}
        self._by_position = {}
        self._name_list = []  # (lowercase_name, player_dict) for fuzzy matching

        for p in predictions:
            # Index by web name
            web_name = p.get("name", "").lower().strip()
            if web_name:
                self._by_name[web_name] = p
                self._name_list.append((web_name, p))

            # Index by full name parts
            full = p.get("full_name", "").lower().strip()
            if full:
                self._by_name[full] = p
                for part in full.split():
                    if len(part) > 2 and part not in self._by_name:
                        self._by_name[part] = p

            # By team
            team = p.get("team", "").lower()
            self._by_team.setdefault(team, []).append(p)

            # By position
            pos = p.get("position", "").lower()
            self._by_position.setdefault(pos, []).append(p)

    def answer(self, question: str) -> dict:
        """
        Main entry: understand intent via semantic scoring, extract entities, route to handler.
        """
        q = question.strip()
        q_lower = q.lower()

        # ── Entity extraction ──
        entities = self._extract_entities(q_lower)

        # ── Score all intents ──
        scores = self._score_intents(q_lower, entities)

        # ── Special case: if 2 player names found + any comparison signal, force comparison ──
        if len(entities.get("players", [])) >= 2 and scores.get("comparison", 0) > 0:
            scores["comparison"] = max(scores["comparison"], 10.0)

        # ── Route to highest scorer ──
        if not scores or max(scores.values()) == 0:
            # No strong signal — try player lookup if entities found, else general
            if entities.get("players"):
                return self._handle_player_lookup(q_lower, q, entities)
            if entities.get("teams"):
                return self._handle_team_query(q_lower, q, entities)
            return self._handle_general(q_lower, q, entities)

        best_intent = max(scores, key=scores.get)

        # Dispatch
        handlers = {
            "comparison": self._handle_comparison,
            "captain": self._handle_captain,
            "chip": self._handle_chip,
            "transfer": self._handle_transfer,
            "player_lookup": self._handle_player_lookup,
            "team_query": self._handle_team_query,
            "position_query": self._handle_position_query,
            "dgw": self._handle_dgw,
            "squad": self._handle_squad,
            "differential": self._handle_differentials,
            "value": self._handle_value,
            "what_if": self._handle_what_if,
            "methodology": self._handle_methodology,
        }

        handler = handlers.get(best_intent, self._handle_general)
        return handler(q_lower, q, entities)

    # ── Intent Scoring ─────────────────────────────────────

    def _score_intents(self, q: str, entities: dict) -> dict:
        """Score every intent against the question. Returns {intent: score}."""
        scores = {}
        for intent, signals in self.INTENT_SIGNALS.items():
            score = 0.0
            for pattern, weight in signals:
                if re.search(pattern, q):
                    score += weight
            # Entity bonuses
            if intent == "comparison" and len(entities.get("players", [])) >= 2:
                score += 3.0
            if intent == "team_query" and entities.get("teams"):
                score += 2.5
            if intent == "position_query" and entities.get("positions"):
                score += 2.0
            if intent in ("player_lookup", "transfer") and entities.get("players"):
                score += 1.5
            if intent == "what_if" and entities.get("players"):
                score += 2.0
            if intent == "value" and entities.get("price_range"):
                score += 2.0

            scores[intent] = score

        return scores

    # ── Entity Extraction ──────────────────────────────────

    def _extract_entities(self, q: str) -> dict:
        """Extract all entities from the question."""
        entities = {
            "players": self._extract_players(q),
            "teams": self._extract_teams(q),
            "positions": self._extract_positions(q),
            "price_range": self._extract_price(q),
        }
        return entities

    def _extract_players(self, q: str) -> list[dict]:
        """Find player names using fuzzy matching against the prediction database."""
        noise = {
            'who', 'what', 'why', 'how', 'should', 'pick', 'get', 'the', 'this', 'that',
            'over', 'instead', 'better', 'between', 'compare', 'and', 'or', 'vs', 'versus',
            'with', 'for', 'from', 'captain', 'cap', 'many', 'points', 'will', 'dgw', 'gw',
            'gameweek', 'week', 'best', 'is', 'it', 'he', 'his', 'my', 'team', 'player',
            'players', 'a', 'an', 'in', 'on', 'i', 'me', 'of', 'to', 'do', 'does', 'good',
            'bad', 'worth', 'any', 'more', 'can', 'than', 'would', 'transfer', 'bring', 'swap',
            'replace', 'predicted', 'predict', 'expected', 'about', 'tell', 'show', 'have', 'be',
            'sell', 'buy', 'keep', 'drop', 'think', 'much', 'score', 'going', 'next', 'bench',
            'boost', 'free', 'hit', 'chip', 'wildcard', 'triple', 'not', 'but', 'are', 'was',
            'were', 'been', 'being', 'if', 'then', 'too', 'also', 'some', 'which', 'when',
            'games', 'game', 'both', 'play', 'plays', 'playing', 'start', 'starts',
            'starting', 'guaranteed', 'certain', 'definitely', 'sure', 'assume', 'assuming',
            'hypothetical', 'raw', 'max', 'ceiling', 'upside', 'breakdown', 'per', 'each',
            'fixture', 'fixtures', 'minutes', 'nailed', '100',
        }

        found = []
        found_ids = set()

        # Pass 1: Exact match on web name or full name
        for name, p in self._by_name.items():
            if len(name) > 2 and name in q and name not in noise:
                pid = p.get("player_id")
                if pid not in found_ids:
                    found_ids.add(pid)
                    found.append(p)

        # Pass 2: Fuzzy matching on remaining words (for typos/partial names)
        if len(found) < 2:
            words = re.findall(r'[a-z]{3,}', q)
            for word in words:
                if word in noise:
                    continue
                # Check fuzzy match against all player names
                best_match = None
                best_ratio = 0.0
                for pname, pdata in self._name_list:
                    # Skip if already found
                    if pdata.get("player_id") in found_ids:
                        continue
                    ratio = SequenceMatcher(None, word, pname).ratio()
                    if ratio > 0.75 and ratio > best_ratio:
                        best_ratio = ratio
                        best_match = pdata
                    # Also check if word is a substring of the name
                    if len(word) >= 4 and word in pname and pdata.get("player_id") not in found_ids:
                        if not best_match or ratio > best_ratio:
                            best_match = pdata
                            best_ratio = max(ratio, 0.8)

                if best_match and best_ratio >= 0.75 and best_match.get("player_id") not in found_ids:
                    found_ids.add(best_match.get("player_id"))
                    found.append(best_match)

        return found[:3]

    def _extract_teams(self, q: str) -> list[str]:
        """Extract team references from the question."""
        teams = []
        # Sort by length (longest first) to match "man city" before "city"
        for alias in sorted(self.TEAM_ALIASES.keys(), key=len, reverse=True):
            if alias in q:
                code = self.TEAM_ALIASES[alias]
                if code not in teams:
                    teams.append(code)
                    # Don't break — might mention multiple teams
        return teams

    def _extract_positions(self, q: str) -> list[str]:
        """Extract position references."""
        positions = []
        for alias in sorted(self.POS_ALIASES.keys(), key=len, reverse=True):
            if alias in q:
                code = self.POS_ALIASES[alias]
                if code not in positions:
                    positions.append(code)
        return positions

    def _extract_price(self, q: str) -> Optional[float]:
        """Extract price constraint (e.g. 'under 7m', 'below £6.5')."""
        m = re.search(r'(?:under|below|max|up\s+to|less\s+than|<)\s*[£]?\s*(\d+\.?\d*)\s*m?', q)
        if m:
            return float(m.group(1))
        return None

    # ── Handlers ───────────────────────────────────────────

    def _handle_comparison(self, q: str, original: str, entities: dict) -> dict:
        """Compare two players with full reasoning."""
        players = entities.get("players", [])
        if len(players) < 2:
            return self._fallback("I couldn't identify two players to compare. Try: **'Compare Salah vs Haaland'** or **'Why pick Palmer over Saka?'**")

        p1, p2 = players[0], players[1]
        return self._build_comparison(p1, p2, original)

    def _build_comparison(self, p1: dict, p2: dict, question: str = "") -> dict:
        """Build detailed comparison with reasoning."""
        n1, n2 = p1["name"], p2["name"]
        gw = self.gw_info.get("gameweek", "?")
        xp1, xp2 = p1["predicted_points"], p2["predicted_points"]
        winner = p1 if xp1 >= xp2 else p2
        loser = p2 if xp1 >= xp2 else p1

        sections = []
        sections.append(f"## {n1} vs {n2} — GW{gw} Comparison\n")

        diff = abs(xp1 - xp2)
        sections.append(f"**🏆 Verdict: {winner['name']}** is predicted to score **{diff:.1f} more points** this GW.\n")

        # Stats table
        sections.append("### 📊 Head-to-Head\n")
        sections.append(f"| Metric | {n1} | {n2} |")
        sections.append("|--------|------|------|")
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

        dgw1, dgw2 = p1.get("is_dgw", False), p2.get("is_dgw", False)
        sections.append(f"| DGW? | {'✅ Yes' if dgw1 else '❌ No'} | {'✅ Yes' if dgw2 else '❌ No'} |")

        fix1 = ", ".join(f"{f['opponent']}({f['venue']}) FDR:{f['fdr']}" for f in p1.get("fixtures", []))
        fix2 = ", ".join(f"{f['opponent']}({f['venue']}) FDR:{f['fdr']}" for f in p2.get("fixtures", []))
        sections.append(f"| Fixtures | {fix1 or 'BGW'} | {fix2 or 'BGW'} |\n")

        # Reasoning
        sections.append("### 💡 Key Reasons\n")
        reasons = self._generate_comparison_reasons(p1, p2)
        sections.extend(reasons)

        # Contextual note if question was "why pick X over Y"
        if re.search(r'why\b.*\b(pick|choose|select|prefer|have)', question.lower()):
            sections.append(f"\n### 📝 Context")
            if xp1 >= xp2:
                sections.append(f"The model picks **{n1}** because the combined factors (form, fixture difficulty, DGW status, nailedness, ICT) produce a higher expected output this GW.")
            else:
                sections.append(f"Actually, the model **favors {n2}** over {n1} for this GW. If {n1} is in your squad instead, consider the transfer suggestion above.")

        sections.append(f"\n### ✅ Recommendation")
        sections.append(f"**Pick {winner['name']}** for GW{gw}. Expected {winner['predicted_points']:.1f} pts vs {loser['predicted_points']:.1f} pts.")

        return {
            "answer": "\n".join(sections),
            "data": {
                "type": "comparison",
                "players": [self._player_card(p1), self._player_card(p2)],
                "winner": winner["name"]
            },
            "suggestions": [
                f"Who should I captain this GW?",
                f"Best {winner['position']} picks this week?",
                f"Should I sell {loser['name']}?"
            ]
        }

    def _generate_comparison_reasons(self, p1: dict, p2: dict) -> list[str]:
        """Generate natural reasoning comparing two players."""
        n1, n2 = p1["name"], p2["name"]
        reasons = []

        # DGW
        dgw1, dgw2 = p1.get("is_dgw", False), p2.get("is_dgw", False)
        if dgw1 and not dgw2:
            reasons.append(f"🔥 **{n1} has a DGW** ({p1.get('num_fixtures', 1)} fixtures) while {n2} only plays once — double the opportunity for points.")
        elif dgw2 and not dgw1:
            reasons.append(f"🔥 **{n2} has a DGW** ({p2.get('num_fixtures', 1)} fixtures) while {n1} only plays once — double the opportunity for points.")

        # Form
        f1, f2 = float(p1.get("form", 0)), float(p2.get("form", 0))
        if abs(f1 - f2) > 0.3:
            better = n1 if f1 > f2 else n2
            reasons.append(f"📈 **{better}** is in better form ({max(f1,f2)} vs {min(f1,f2)}).")

        # Fixture difficulty
        avg_fdr1 = sum(f["fdr"] for f in p1.get("fixtures", [])) / max(len(p1.get("fixtures", [])), 1)
        avg_fdr2 = sum(f["fdr"] for f in p2.get("fixtures", [])) / max(len(p2.get("fixtures", [])), 1)
        if abs(avg_fdr1 - avg_fdr2) >= 0.3:
            easier = n1 if avg_fdr1 < avg_fdr2 else n2
            reasons.append(f"🎯 **{easier}** has easier fixtures (avg FDR: {min(avg_fdr1, avg_fdr2):.1f} vs {max(avg_fdr1, avg_fdr2):.1f}).")

        # Starter quality
        tier_order = {"nailed": 4, "regular": 3, "rotation": 2, "fringe": 1, "bench_warmer": 0}
        tier1 = p1.get("starter_quality", {}).get("tier", "unknown")
        tier2 = p2.get("starter_quality", {}).get("tier", "unknown")
        if tier_order.get(tier1, 0) != tier_order.get(tier2, 0):
            more_nailed = n1 if tier_order.get(tier1, 0) > tier_order.get(tier2, 0) else n2
            reasons.append(f"🔒 **{more_nailed}** is more nailed ({tier1 if tier_order.get(tier1,0) > tier_order.get(tier2,0) else tier2} starter).")

        # Value
        xp1, xp2 = p1["predicted_points"], p2["predicted_points"]
        val1 = xp1 / max(p1["price"], 3.5)
        val2 = xp2 / max(p2["price"], 3.5)
        if abs(val1 - val2) > 0.15:
            better_val = n1 if val1 > val2 else n2
            reasons.append(f"💰 **{better_val}** offers better value ({max(val1,val2):.2f} vs {min(val1,val2):.2f} xPts/£m).")

        # ICT
        ict1, ict2 = float(p1.get("ict_index", 0)), float(p2.get("ict_index", 0))
        if abs(ict1 - ict2) > 15:
            better_ict = n1 if ict1 > ict2 else n2
            reasons.append(f"⚡ **{better_ict}** has a higher ICT index ({max(ict1,ict2):.0f} vs {min(ict1,ict2):.0f}).")

        # Home advantage
        home1 = any(f.get("venue") == "H" for f in p1.get("fixtures", []))
        home2 = any(f.get("venue") == "H" for f in p2.get("fixtures", []))
        if home1 and not home2:
            reasons.append(f"🏟️ **{n1}** plays at home, giving a statistical advantage.")
        elif home2 and not home1:
            reasons.append(f"🏟️ **{n2}** plays at home, giving a statistical advantage.")

        # Availability
        avail1 = p1.get("availability", {})
        avail2 = p2.get("availability", {})
        if avail1.get("status") == "doubtful" and avail2.get("status") != "doubtful":
            reasons.append(f"⚠️ **{n1} is flagged** ({avail1.get('chance', '?')}% chance) — {n2} is fully available.")
        elif avail2.get("status") == "doubtful" and avail1.get("status") != "doubtful":
            reasons.append(f"⚠️ **{n2} is flagged** ({avail2.get('chance', '?')}% chance) — {n1} is fully available.")

        if not reasons:
            reasons.append("Both players are closely matched. The model gives a slight edge based on combined factor weighting.")

        return reasons

    def _handle_captain(self, q: str, original: str, entities: dict) -> dict:
        """Captain advice with reasoning."""
        gw = self.gw_info.get("gameweek", "?")
        cap = self.squad.get("captain", {})
        vice = self.squad.get("vice_captain", {})

        # If a specific player is asked about as captain
        if entities.get("players"):
            player = entities["players"][0]
            return self._assess_captain_candidate(player)

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
            sections.append("- **Double Gameweek**: 2 chances to score, doubled as captain = 4x total impact")
        sections.append(f"- **Form**: {cap.get('form', '--')} (recent points average)")
        sections.append(f"- **Confidence**: {int(cap.get('confidence', 0)*100)}%")
        tier = cap.get("starter_quality", {}).get("tier", "?")
        sections.append(f"- **Starter tier**: {tier} — guaranteed full minutes")

        # TC suggestion
        chip_recs = self.chip_analysis.get("recommendations", [])
        tc_rec = next((r for r in chip_recs if r["chip"] == "triple_captain"), None)
        if tc_rec and tc_rec.get("score", 0) >= 60:
            sections.append(f"\n💡 **Triple Captain alert!** TC scores {tc_rec['score']}/100 this week. Consider using it on {cap.get('name', '?')}.")

        return {
            "answer": "\n".join(sections),
            "data": {"type": "captain", "captain": self._player_card(cap) if cap else None},
            "suggestions": [
                f"Compare {cap.get('name', '?')} vs {top5[1]['name'] if len(top5) > 1 else '?'}",
                "Should I use Triple Captain this week?",
                "Best differential captain?"
            ]
        }

    def _assess_captain_candidate(self, player: dict) -> dict:
        """Assess a specific player as captain candidate."""
        gw = self.gw_info.get("gameweek", "?")
        cap = self.squad.get("captain", {})
        sections = []
        sections.append(f"## Should you captain {player['name']}? — GW{gw}\n")

        sections.append(f"| Metric | {player['name']} | Model's Pick ({cap.get('name', '?')}) |")
        sections.append("|--------|------|------|")
        sections.append(f"| **xPts** | {player['predicted_points']:.1f} | {cap.get('predicted_points', 0):.1f} |")
        sections.append(f"| Form | {player.get('form', '--')} | {cap.get('form', '--')} |")
        sections.append(f"| DGW? | {'✅' if player.get('is_dgw') else '❌'} | {'✅' if cap.get('is_dgw') else '❌'} |")
        sections.append(f"| Tier | {player.get('starter_quality', {}).get('tier', '?')} | {cap.get('starter_quality', {}).get('tier', '?')} |")

        if player["predicted_points"] >= cap.get("predicted_points", 0):
            sections.append(f"\n✅ **Yes!** {player['name']} is the model's top pick too (or equally strong).")
        else:
            diff = cap.get("predicted_points", 0) - player["predicted_points"]
            sections.append(f"\n⚠️ **{cap.get('name', '?')} is a better captain choice** — {diff:.1f} more xPts.")
            sections.append(f"Captaining {player['name']} would cost you an expected **{diff * 2:.1f} points** (doubled as captain miss).")

        return {
            "answer": "\n".join(sections),
            "data": {"type": "captain_assessment"},
            "suggestions": [
                f"Compare {player['name']} vs {cap.get('name', '?')}",
                "Who are the top 5 captain options?",
            ]
        }

    def _handle_chip(self, q: str, original: str, entities: dict) -> dict:
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

        # Deep dive if asking about a specific chip
        target_chip = None
        if re.search(r'bench\s*boost|bb\b', q):
            target_chip = "bench_boost"
        elif re.search(r'triple\s*captain|tc\b', q):
            target_chip = "triple_captain"
        elif re.search(r'free\s*hit|fh\b', q):
            target_chip = "free_hit"
        elif re.search(r'wildcard|wc\b', q):
            target_chip = "wildcard"

        if target_chip:
            rec = next((r for r in recs if r["chip"] == target_chip), None)
            if rec:
                chip_names = {"bench_boost": "Bench Boost", "triple_captain": "Triple Captain",
                              "free_hit": "Free Hit", "wildcard": "Wildcard"}
                sections.append(f"\n### {chip_names.get(target_chip, target_chip)} Deep Dive")
                sections.append(f"Score: **{rec['score']}/100**")
                for r in rec.get("reasons", []):
                    sections.append(f"- {r}")

        return {
            "answer": "\n".join(sections),
            "data": {"type": "chip", "recommendations": recs, "best": best},
            "suggestions": ["Should I Bench Boost or Triple Captain?", "When to Free Hit?", "Who to TC?"]
        }

    def _handle_transfer(self, q: str, original: str, entities: dict) -> dict:
        """Transfer advice — contextual based on whether a player is mentioned."""
        gw = self.gw_info.get("gameweek", "?")
        sections = []
        sections.append(f"## 🔄 Transfer Advice — GW{gw}\n")

        players = entities.get("players", [])
        if players:
            player = players[0]
            is_sell = bool(re.search(r'sell|drop|get\s+rid|remove|take\s+out|replace', q))
            is_buy = bool(re.search(r'buy|bring|get|pick|worth', q))
            is_keep_or_sell = bool(re.search(r'keep|hold|sell', q))

            if is_keep_or_sell:
                return self._assess_keep_or_sell(player)

            sections.append(f"### {'Sell' if is_sell else 'Assessment of'} {player['name']}\n")
            sections.append(f"| Metric | Value |")
            sections.append(f"|--------|-------|")
            sections.append(f"| Predicted xPts | {player['predicted_points']:.1f} |")
            sections.append(f"| Form | {player.get('form', '--')} |")
            sections.append(f"| DGW? | {'Yes ✅' if player.get('is_dgw') else 'No'} |")
            sections.append(f"| Tier | {player.get('starter_quality', {}).get('tier', '?')} |")
            sections.append(f"| Price | £{player['price']:.1f}m |")

            # Find alternatives
            pos = player["position"].lower()
            budget = player["price"] + 1.5
            alternatives = [p for p in self._by_position.get(pos, [])
                          if p["name"] != player["name"]
                          and p["predicted_points"] > player["predicted_points"]
                          and p["price"] <= budget][:5]

            if alternatives:
                sections.append(f"\n### Better alternatives (≤ £{budget:.1f}m)\n")
                for alt in alternatives:
                    gain = alt["predicted_points"] - player["predicted_points"]
                    sections.append(f"- **{alt['name']}** ({alt['team']}) — {alt['predicted_points']:.1f} xPts — £{alt['price']:.1f}m — **+{gain:.1f} pts** {'🔥 DGW' if alt.get('is_dgw') else ''}")
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

        return {
            "answer": "\n".join(sections),
            "data": {"type": "transfer"},
            "suggestions": ["Best budget midfielders?", "Best DGW defenders?", "Should I take a hit?"]
        }

    def _assess_keep_or_sell(self, player: dict) -> dict:
        """Assess whether to keep or sell a specific player."""
        gw = self.gw_info.get("gameweek", "?")
        rank = next((i+1 for i, p in enumerate(self.predictions) if p.get("player_id") == player.get("player_id")), "?")
        total = len(self.predictions)

        sections = []
        sections.append(f"## Keep or Sell {player['name']}? — GW{gw}\n")
        sections.append(f"**Overall rank: #{rank}** out of {total} players\n")

        verdict = "KEEP" if isinstance(rank, int) and rank <= 30 else "CONSIDER SELLING"
        emoji = "✅" if verdict == "KEEP" else "⚠️"
        sections.append(f"### {emoji} Verdict: **{verdict}**\n")

        sections.append(f"| Metric | Value | Assessment |")
        sections.append(f"|--------|-------|-----------|")

        xpts = player["predicted_points"]
        xpts_assessment = "🟢 Strong" if xpts >= 8 else "🟡 Moderate" if xpts >= 5 else "🔴 Weak"
        sections.append(f"| xPts | {xpts:.1f} | {xpts_assessment} |")

        form = float(player.get("form", 0))
        form_assessment = "🟢 Hot" if form >= 6 else "🟡 Average" if form >= 3 else "🔴 Cold"
        sections.append(f"| Form | {form} | {form_assessment} |")

        tier = player.get("starter_quality", {}).get("tier", "unknown")
        tier_assessment = "🟢" if tier in ("nailed", "regular") else "🔴"
        sections.append(f"| Starter Tier | {tier} | {tier_assessment} |")

        dgw = player.get("is_dgw", False)
        sections.append(f"| DGW? | {'Yes ✅' if dgw else 'No ❌'} | {'🟢 Major boost' if dgw else '—'} |")

        return {
            "answer": "\n".join(sections),
            "data": {"type": "keep_or_sell"},
            "suggestions": [f"Who should I replace {player['name']} with?", "Best transfers this week?"]
        }

    def _handle_player_lookup(self, q: str, original: str, entities: dict) -> dict:
        """Look up specific player prediction details."""
        players = entities.get("players", [])
        if not players:
            return self._fallback("Which player? Try: **'How many points will Haaland get?'** or **'Tell me about Salah'**")

        p = players[0]
        gw = self.gw_info.get("gameweek", "?")
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
        sections.append(f"| Ownership | {p.get('selected_by_percent', '--')}% |")
        sections.append(f"| Starter Tier | {p.get('starter_quality', {}).get('tier', '?')} |")
        sections.append(f"| Confidence | {int(p.get('confidence', 0)*100)}% |")

        # Availability
        avail = p.get("availability", {})
        if avail.get("status") == "doubtful":
            sections.append(f"\n⚠️ **Flagged**: {p.get('news', 'Doubtful')} ({avail.get('chance', '?')}% chance)")

        # Fixtures with per-fixture breakdown
        sections.append(f"\n### Fixtures")
        fixtures = p.get("fixtures", [])
        if fixtures:
            sections.append("")
            sections.append("| Fixture | xPts | xMins | xG | CS% | FDR |")
            sections.append("|---------|------|-------|-----|-----|-----|")
            for f in fixtures:
                opp = f.get("opponent", "?")
                venue = f.get("venue", "?")
                xp = f.get("xp_adjusted", f.get("xp_single", 0))
                xmins = f.get("xmins", 0)
                xg = f.get("fixture_xg", 0)
                cs = f.get("cs_probability", 0)
                fdr = f.get("fdr", "?")
                sections.append(f"| {opp}({venue}) | {xp:.2f} | {xmins:.0f} | {xg:.2f} | {cs*100:.0f}% | {fdr} |")
        else:
            sections.append("- **Blank Gameweek** — no fixtures")

        rank = next((i+1 for i, pp in enumerate(self.predictions) if pp.get("player_id") == p.get("player_id")), "?")
        sections.append(f"\n**Overall rank: #{rank}** out of {len(self.predictions)} players")

        return {
            "answer": "\n".join(sections),
            "data": {"type": "player", "player": self._player_card(p)},
            "suggestions": [
                f"Best {p['position']} picks this week?",
                f"Should I captain {p['name']}?",
                f"Keep or sell {p['name']}?"
            ]
        }

    def _handle_team_query(self, q: str, original: str, entities: dict) -> dict:
        """Best players from a specific team."""
        teams = entities.get("teams", [])
        if not teams:
            return self._fallback("Which team? Try: **'Best Arsenal players'** or **'Players from Man City'**")

        team_code = teams[0]
        team_players = self._by_team.get(team_code, [])

        # Try broader match
        if not team_players:
            for t_key, t_list in self._by_team.items():
                if team_code in t_key or t_key in team_code:
                    team_players = t_list
                    break

        if not team_players:
            return self._fallback(f"No players found for team code: {team_code}")

        team_players = sorted(team_players, key=lambda x: x["predicted_points"], reverse=True)
        gw = self.gw_info.get("gameweek", "?")
        team_name = team_players[0].get("team_name", team_code.upper())
        is_dgw = team_players[0].get("is_dgw", False)

        sections = []
        sections.append(f"## {team_name} — GW{gw} {'🔥 DGW' if is_dgw else ''}\n")

        sections.append("| Player | Pos | xPts | Price | Form | Tier |")
        sections.append("|--------|-----|------|-------|------|------|")
        for p in team_players[:10]:
            sections.append(f"| **{p['name']}** | {p['position']} | {p['predicted_points']:.1f} | £{p['price']:.1f}m | {p.get('form', '--')} | {p.get('starter_quality', {}).get('tier', '?')} |")

        best = team_players[0]
        sections.append(f"\n**Best pick: {best['name']}** — {best['predicted_points']:.1f} xPts")

        return {
            "answer": "\n".join(sections),
            "data": {"type": "team", "team": team_code.upper()},
            "suggestions": [
                f"Should I triple up on {team_name}?",
                f"Compare {team_players[0]['name']} vs {team_players[1]['name']}" if len(team_players) > 1 else "Best captain?",
            ]
        }

    def _handle_position_query(self, q: str, original: str, entities: dict) -> dict:
        """Best players by position."""
        positions = entities.get("positions", [])
        pos = positions[0] if positions else "mid"

        # Check for budget constraint
        max_price = entities.get("price_range")

        players = sorted(self._by_position.get(pos, []),
                        key=lambda x: x["predicted_points"], reverse=True)

        if max_price:
            players = [p for p in players if p["price"] <= max_price]

        players = players[:10]
        gw = self.gw_info.get("gameweek", "?")
        pos_full = {"gkp": "Goalkeepers", "def": "Defenders", "mid": "Midfielders", "fwd": "Forwards"}.get(pos, pos)

        sections = []
        title = f"Best {pos_full}" + (f" under £{max_price}m" if max_price else "") + f" — GW{gw}"
        sections.append(f"## {title}\n")
        sections.append("| # | Player | Team | xPts | Price | Form | DGW? | Tier |")
        sections.append("|---|--------|------|------|-------|------|------|------|")
        for i, p in enumerate(players, 1):
            dgw = "✅" if p.get("is_dgw") else ""
            sections.append(f"| {i} | **{p['name']}** | {p['team']} | {p['predicted_points']:.1f} | £{p['price']:.1f}m | {p.get('form', '--')} | {dgw} | {p.get('starter_quality', {}).get('tier', '?')} |")

        # Budget pick
        budget = [p for p in self._by_position.get(pos, [])
                 if p["price"] <= 6.0 and p.get("starter_quality", {}).get("tier") in ("nailed", "regular")]
        budget.sort(key=lambda x: x["predicted_points"], reverse=True)
        if budget and not max_price:
            sections.append(f"\n💰 **Best budget pick**: {budget[0]['name']} ({budget[0]['team']}) — £{budget[0]['price']:.1f}m — {budget[0]['predicted_points']:.1f} xPts")

        return {
            "answer": "\n".join(sections),
            "data": {"type": "position", "position": pos.upper()},
            "suggestions": [
                f"Compare {players[0]['name']} vs {players[1]['name']}" if len(players) > 1 else "Best captain?",
                f"Best budget {pos_full.lower()}?",
                f"Best differential {pos_full.lower()}?"
            ]
        }

    def _handle_dgw(self, q: str, original: str, entities: dict) -> dict:
        """DGW-specific advice."""
        gw = self.gw_info.get("gameweek", "?")
        dgw_teams = self.gw_info.get("dgw_teams", {})
        dgw_players = [p for p in self.predictions if p.get("is_dgw")]
        dgw_players.sort(key=lambda x: x["predicted_points"], reverse=True)

        sections = []
        sections.append(f"## 🔥 Double Gameweek {gw}\n")

        if not dgw_teams:
            sections.append("This is a standard gameweek — no teams play twice.")
            return {"answer": "\n".join(sections), "data": {"type": "dgw"}, "suggestions": ["Best picks?", "Captain?"]}

        sections.append(f"**{len(dgw_teams)} teams play twice:**\n")
        for tid, info in dgw_teams.items():
            sections.append(f"- **{info.get('name', '?')}** ({info.get('short_name', '?')})")

        sections.append(f"\n### Top DGW Players\n")
        sections.append("| # | Player | Team | Pos | xPts | Price | Form |")
        sections.append("|---|--------|------|-----|------|-------|------|")
        for i, p in enumerate(dgw_players[:15], 1):
            sections.append(f"| {i} | **{p['name']}** | {p['team']} | {p['position']} | {p['predicted_points']:.1f} | £{p['price']:.1f}m | {p.get('form', '--')} |")

        sections.append(f"\n**Key DGW tips:**")
        sections.append("- Prioritize nailed DGW starters — they get 2 bites at the cherry")
        sections.append("- Captain the highest-ceiling DGW player")
        sections.append("- Consider Bench Boost if your full 15 are DGW nailed starters")

        return {
            "answer": "\n".join(sections),
            "data": {"type": "dgw"},
            "suggestions": ["Should I Bench Boost?", "Best DGW defenders?", "Best DGW captain?"]
        }

    def _handle_squad(self, q: str, original: str, entities: dict) -> dict:
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
            avail = p.get("availability", {})
            flag = f" ⚠️{avail.get('chance', '?')}%" if avail.get("status") == "doubtful" else ""
            sections.append(f"- **{p['name']}** ({p['position']}, {p['team']}) — {p['predicted_points']:.1f} xPts — {fix} {'🔥DGW' if p.get('is_dgw') else ''}{flag}")

        sections.append(f"\n### Bench")
        for i, p in enumerate(bench):
            sections.append(f"- B{i+1}: {p['name']} ({p['position']}, {p['team']}) — {p['predicted_points']:.1f} xPts")

        return {
            "answer": "\n".join(sections),
            "data": {"type": "squad"},
            "suggestions": ["Why this formation?", "Should I Bench Boost?", "Who's the best captain?"]
        }

    def _handle_differentials(self, q: str, original: str, entities: dict) -> dict:
        """Low-ownership picks."""
        gw = self.gw_info.get("gameweek", "?")
        diffs = [p for p in self.predictions
                if float(p.get("selected_by_percent", 100)) < 10
                and p.get("starter_quality", {}).get("tier") in ("nailed", "regular")]
        diffs.sort(key=lambda x: x["predicted_points"], reverse=True)

        sections = []
        sections.append(f"## 💎 Differentials — GW{gw}\n")
        sections.append("Low-ownership players (<10%) who can boost your rank:\n")
        sections.append("| # | Player | Team | Pos | xPts | Price | Own% | DGW? |")
        sections.append("|---|--------|------|-----|------|-------|------|------|")
        for i, p in enumerate(diffs[:10], 1):
            dgw = "✅" if p.get("is_dgw") else ""
            sections.append(f"| {i} | **{p['name']}** | {p['team']} | {p['position']} | {p['predicted_points']:.1f} | £{p['price']:.1f}m | {p.get('selected_by_percent', '?')}% | {dgw} |")

        if diffs:
            sections.append(f"\n🔥 **Top differential**: {diffs[0]['name']} — Only {diffs[0].get('selected_by_percent', '?')}% owned but predicted {diffs[0]['predicted_points']:.1f} pts!")

        return {
            "answer": "\n".join(sections),
            "data": {"type": "differentials"},
            "suggestions": [f"Tell me about {diffs[0]['name']}" if diffs else "Best picks?", "Best differential captain?"]
        }

    def _handle_value(self, q: str, original: str, entities: dict) -> dict:
        """Budget/value picks."""
        gw = self.gw_info.get("gameweek", "?")
        max_price = entities.get("price_range") or 6.5

        value = [p for p in self.predictions
                if p["price"] <= max_price
                and p.get("starter_quality", {}).get("tier") in ("nailed", "regular")]
        value.sort(key=lambda x: x["predicted_points"] / max(x["price"], 3.5), reverse=True)

        sections = []
        sections.append(f"## 💰 Value Picks (≤ £{max_price}m) — GW{gw}\n")
        sections.append("| # | Player | Team | Pos | xPts | Price | Value | DGW? |")
        sections.append("|---|--------|------|-----|------|-------|-------|------|")
        for i, p in enumerate(value[:10], 1):
            val = p["predicted_points"] / max(p["price"], 3.5)
            dgw = "✅" if p.get("is_dgw") else ""
            sections.append(f"| {i} | **{p['name']}** | {p['team']} | {p['position']} | {p['predicted_points']:.1f} | £{p['price']:.1f}m | {val:.2f} xPts/£m | {dgw} |")

        return {
            "answer": "\n".join(sections),
            "data": {"type": "value"},
            "suggestions": ["Best budget defenders?", "Best budget midfielders?", "Cheapest nailed starters?"]
        }

    def _handle_what_if(self, q: str, original: str, entities: dict) -> dict:
        """Handle hypothetical/what-if questions about player performance."""
        players = entities.get("players", [])
        if not players:
            return self._fallback("Which player? Try: **'If Darlow plays both DGW games, what's his xPts?'**")

        p = players[0]
        gw = self.gw_info.get("gameweek", "?")
        fixtures = p.get("fixtures", [])
        avail = p.get("availability", {})
        starter = p.get("starter_quality", {})

        sections = []
        sections.append(f"## 🔮 What-If Analysis: {p['name']} — GW{gw}\n")

        # Current prediction (with discounts)
        current_xpts = p["predicted_points"]
        raw_xpts = p.get("raw_xpts", current_xpts)

        sections.append("### Current Prediction\n")
        sections.append(f"| Metric | Value |")
        sections.append(f"|--------|-------|")
        sections.append(f"| **Predicted xPts** | **{current_xpts:.2f}** (after availability/rotation discounts) |")
        sections.append(f"| **Raw xPts** | **{raw_xpts:.2f}** (before availability discount) |")
        sections.append(f"| Starter Tier | {starter.get('tier', '?')} (start rate: {starter.get('start_rate', '?')}) |")
        sections.append(f"| Availability | {avail.get('status', 'available')} ({avail.get('chance', 100)}%) |")
        if p.get("is_dgw"):
            sections.append(f"| DGW | ✅ {p.get('num_fixtures', 2)} fixtures |")
            if starter.get("dgw_both_start_prob") is not None:
                sections.append(f"| P(start both) | {int(starter['dgw_both_start_prob'] * 100)}% |")

        # Per-fixture breakdown
        if fixtures:
            sections.append(f"\n### Per-Fixture Breakdown\n")
            sections.append("| Fixture | xPts (raw) | xPts (adj) | xMins | xG | xGC | CS% |")
            sections.append("|---------|-----------|-----------|-------|-----|------|-----|")
            total_raw_fix = 0
            for i, f in enumerate(fixtures):
                xp_raw = f.get("xp_single", 0)
                xp_adj = f.get("xp_adjusted", 0)
                xmins = f.get("xmins", 0)
                xg = f.get("fixture_xg", 0)
                xgc = f.get("fixture_xgc", 0)
                cs = f.get("cs_probability", 0)
                opp = f.get("opponent", "?")
                venue = f.get("venue", "?")
                total_raw_fix += xp_raw
                sections.append(f"| {opp}({venue}) | {xp_raw:.2f} | {xp_adj:.2f} | {xmins:.0f} | {xg:.2f} | {xgc:.2f} | {cs*100:.0f}% |")
            sections.append(f"| **Total** | **{total_raw_fix:.2f}** | **{current_xpts:.2f}** | | | | |")

        # The hypothetical: 100% playing both games
        sections.append(f"\n### 💡 If {p['name']} plays 90 mins in ALL fixtures\n")

        if fixtures:
            # Sum the raw per-fixture xPts — this is the "ceiling" without rotation discount
            # But we need to explain: raw xPts already has the rotation/minutes baked in
            # The "100% play" scenario means using xp_single (raw) for each fixture
            total_100 = sum(f.get("xp_single", 0) for f in fixtures)
            sections.append(f"**If guaranteed to play 90 mins every game: ~{total_100:.2f} xPts**\n")
            sections.append(f"This is the **raw total** before any availability discount.")
            diff = total_100 - current_xpts
            if diff > 0.5:
                sections.append(f"\nThat's **+{diff:.2f} more** than the current prediction of {current_xpts:.2f}.")
                sections.append(f"The gap comes from:")
                if avail.get("status") == "doubtful":
                    sections.append(f"- ⚠️ **Availability discount**: flagged at {avail.get('chance', '?')}% chance of playing")
                if starter.get("tier") not in ("nailed",) and p.get("is_dgw"):
                    sections.append(f"- 🔄 **Rotation risk**: {starter.get('tier', '?')} tier — model expects some rest in 2nd fixture")
                if starter.get("tier") == "nailed" and p.get("is_dgw"):
                    sections.append(f"- 📉 **Slight DGW rest risk**: even nailed players have ~8% chance of being rested for 1 game")
            else:
                sections.append(f"\nMinimal difference — the model already expects {p['name']} to play most/all minutes.")
        else:
            sections.append(f"No fixtures this GW (blank gameweek).")

        return {
            "answer": "\n".join(sections),
            "data": {"type": "what_if", "player": self._player_card(p)},
            "suggestions": [
                f"Compare {p['name']} vs {self.predictions[0]['name']}" if self.predictions else "Best picks?",
                f"Should I captain {p['name']}?",
                f"Best {p['position']} picks this week?",
            ]
        }

    def _handle_methodology(self, q: str, original: str, entities: dict) -> dict:
        """Explain methodology, calculations, and how metrics work."""
        q_lower = q.lower()
        sections = []

        # Detect which metric/concept they're asking about
        if re.search(r'win\s*rate|wr\b', q_lower):
            sections.append("## 📊 Win Rate (WR) — How It's Calculated\n")
            sections.append("**Win Rate** is the team's **season-long** winning percentage from all Premier League matches played this season.\n")
            sections.append("### Formula")
            sections.append("```")
            sections.append("Win Rate = Wins / Total Matches Played")
            sections.append("```\n")
            sections.append("### Example")
            sections.append("If Manchester United has 15 wins from 33 matches:")
            sections.append("- WR = 15 / 33 = **45.5%**\n")
            sections.append("### Important Notes")
            sections.append("- ⚠️ **WR is static per season** — it doesn't change based on opponent")
            sections.append("- The same team will show the same WR in GW33 vs GW34 (unless more matches are played)")
            sections.append("- For **opponent-adjusted** metrics, the model uses:")
            sections.append("  - **Fixture xG/xGC** (expected goals based on specific opponent)")
            sections.append("  - **FDR** (fixture difficulty rating — 1-5 scale)")
            sections.append("  - **H2H record** (head-to-head this season)")
            sections.append("  - **Team Momentum** (recent form trend)\n")
            sections.append("### Related Metrics")
            sections.append("| Metric | Description |")
            sections.append("|--------|-------------|")
            sections.append("| **Last 5 Form** | W/D/L string from last 5 matches |")
            sections.append("| **Last 5 WR** | Win rate from last 5 matches only |")
            sections.append("| **Momentum** | Weighted recent form (-1 to +1) |")
            sections.append("| **Fixture xG** | Expected goals vs specific opponent |")

        elif re.search(r'xpts?|predicted\s*points|prediction', q_lower):
            sections.append("## ⚽ xPts — How It's Calculated\n")
            sections.append("**xPts (Expected Points)** is the model's prediction for how many FPL points a player will score.\n")
            sections.append("### Factors (Weighted)")
            sections.append("| Factor | Weight | Description |")
            sections.append("|--------|--------|-------------|")
            sections.append("| Form | 20% | Recent GW performance (last 5) |")
            sections.append("| Fixture Difficulty | 15% | FDR-based multiplier |")
            sections.append("| ICT Index | 10% | FPL's Influence/Creativity/Threat |")
            sections.append("| Team Form | 10% | Team's recent results & goals |")
            sections.append("| H2H Factor | 8% | Head-to-head + fixture xG/xGC |")
            sections.append("| Season Average | 8% | PPG this season |")
            sections.append("| Home/Away | 7% | +10% home, -8% away |")
            sections.append("| Minutes Consistency | 7% | Start rate & minutes volatility |")
            sections.append("| Team Strength | 5% | FPL strength ratings |")
            sections.append("| Set Pieces | 5% | Penalty/FK/corner duties |")
            sections.append("| Ownership Trend | 3% | Transfer momentum |")
            sections.append("| Bonus Tendency | 2% | Historical BPS rate |")
            sections.append("\n### Adjustments")
            sections.append("- **Rotation Risk**: Non-nailed players get ~15-50% discount")
            sections.append("- **Availability**: Flagged players (75%/50%/25%) discounted accordingly")
            sections.append("- **DGW**: 2 fixtures → higher xPts (but P(start both) < 100%)\n")
            sections.append("### Raw vs Adjusted xPts")
            sections.append("- **Raw xPts**: If player plays 90 mins every game")
            sections.append("- **Adjusted xPts**: After rotation/injury discount")

        elif re.search(r'tier|starter|nailed|rotation', q_lower):
            sections.append("## 🔒 Starter Tier — How It's Determined\n")
            sections.append("**Tier** classifies how likely a player is to start matches.\n")
            sections.append("### Tier Definitions")
            sections.append("| Tier | Start Rate | Description |")
            sections.append("|------|------------|-------------|")
            sections.append("| **Nailed** | >85% | Almost always starts |")
            sections.append("| **Regular** | 65-85% | Usually starts |")
            sections.append("| **Rotation** | 40-65% | Rotates with others |")
            sections.append("| **Fringe** | <40% | Rarely starts |")
            sections.append("\n### Calculation Inputs")
            sections.append("- Minutes played / total available minutes")
            sections.append("- Starts / total matches")
            sections.append("- Minutes volatility (consistency)")
            sections.append("- Recent trend (fading or rising?)")

        elif re.search(r'fdr|fixture\s*difficulty', q_lower):
            sections.append("## 🎯 FDR — Fixture Difficulty Rating\n")
            sections.append("**FDR** is FPL's official 1-5 scale for fixture difficulty.\n")
            sections.append("### FDR Scale")
            sections.append("| FDR | Difficulty | xPts Multiplier |")
            sections.append("|-----|------------|-----------------|")
            sections.append("| 1 | Very Easy | ×1.30 (+30%) |")
            sections.append("| 2 | Easy | ×1.15 (+15%) |")
            sections.append("| 3 | Medium | ×1.00 (baseline) |")
            sections.append("| 4 | Tough | ×0.85 (-15%) |")
            sections.append("| 5 | Very Tough | ×0.70 (-30%) |")
            sections.append("\n### Position-Aware FDR")
            sections.append("The model also uses **position-aware FDR**:")
            sections.append("- GKP/DEF: Based on opponent's attack strength")
            sections.append("- MID/FWD: Based on opponent's defense strength")

        elif re.search(r'xg|expected\s*goals', q_lower):
            sections.append("## 📈 Fixture xG / xGC — How It Works\n")
            sections.append("**Fixture xG** predicts goals for a specific matchup.\n")
            sections.append("### Calculation")
            sections.append("Based on this season's actual results:")
            sections.append("- Team's **goals per game** (home vs away)")
            sections.append("- Opponent's **goals conceded** (home vs away)")
            sections.append("- **H2H record** this season (if played)")
            sections.append("- Weighted average of these factors\n")
            sections.append("### Example")
            sections.append("Arsenal (H) vs Southampton:")
            sections.append("- Arsenal home GF/G: 2.1")
            sections.append("- Southampton away GA/G: 2.4")
            sections.append("- → Fixture xG ≈ 2.2 for Arsenal")

        elif re.search(r'confidence|conf\b', q_lower):
            sections.append("## 🎲 Confidence Score — What It Means\n")
            sections.append("**Confidence** (0-100%) indicates prediction reliability.\n")
            sections.append("### Factors That Increase Confidence")
            sections.append("- ✅ Nailed starter (high minutes)")
            sections.append("- ✅ Easy fixture (low FDR)")
            sections.append("- ✅ Good recent form")
            sections.append("- ✅ Team in good form")
            sections.append("- ✅ No injury doubts\n")
            sections.append("### Factors That Decrease Confidence")
            sections.append("- ❌ Rotation risk")
            sections.append("- ❌ Injury flag")
            sections.append("- ❌ Tough fixture")
            sections.append("- ❌ Inconsistent minutes")

        elif re.search(r'momentum|form\s*(calculation|work)', q_lower):
            sections.append("## 📈 Team Momentum — How It's Calculated\n")
            sections.append("**Momentum** measures recent form trend (-1 to +1).\n")
            sections.append("### Formula")
            sections.append("Weighted average of last 5 results:")
            sections.append("- Most recent match: weight 1.0")
            sections.append("- 2nd most recent: weight 0.8")
            sections.append("- 3rd: weight 0.6, 4th: 0.4, 5th: 0.2\n")
            sections.append("Result values: W=+1, D=0, L=-1\n")
            sections.append("### Example")
            sections.append("Last 5: W W D L W")
            sections.append("- Momentum = (1×1 + 1×0.8 + 0×0.6 + (-1)×0.4 + 1×0.2) / 3")
            sections.append("- = 1.6 / 3 = **+0.53** (positive, improving)")

        else:
            # General methodology overview
            sections.append("## 📖 FPL Predictor — Model Methodology\n")
            sections.append("The prediction model combines **12+ factors** to estimate expected points.\n")
            sections.append("### Key Concepts")
            sections.append("Ask me about any of these:")
            sections.append("- **xPts**: How predictions are calculated")
            sections.append("- **Win Rate**: Season-long team win percentage")
            sections.append("- **Tier**: Starter classification (nailed/regular/rotation/fringe)")
            sections.append("- **FDR**: Fixture difficulty rating (1-5)")
            sections.append("- **Fixture xG**: Expected goals for specific matchup")
            sections.append("- **Confidence**: Prediction reliability score")
            sections.append("- **Momentum**: Recent form trend\n")
            sections.append("### Example Questions")
            sections.append("- \"How do you calculate xPts?\"")
            sections.append("- \"What does win rate mean?\"")
            sections.append("- \"How is tier determined?\"")
            sections.append("- \"Explain FDR\"")

        return {
            "answer": "\n".join(sections),
            "data": {"type": "methodology"},
            "suggestions": [
                "How is xPts calculated?",
                "What does tier mean?",
                "How does FDR work?",
                "Explain fixture xG",
            ]
        }

    def _handle_general(self, q: str, original: str, entities: dict) -> dict:
        """General catch-all — try to be helpful based on any entities found."""
        # If a player name was found, give player info
        if entities.get("players"):
            return self._handle_player_lookup(q, original, entities)

        # If a team was mentioned
        if entities.get("teams"):
            return self._handle_team_query(q, original, entities)

        # General summary
        gw = self.gw_info.get("gameweek", "?")
        cap = self.squad.get("captain", {})

        sections = []
        sections.append(f"## GW{gw} Quick Summary\n")
        if self.predictions:
            sections.append(f"- **Top pick**: {self.predictions[0]['name']} ({self.predictions[0]['predicted_points']:.1f} xPts)")
        sections.append(f"- **Captain**: {cap.get('name', '?')} ({cap.get('predicted_points', 0):.1f} xPts)")
        if self.gw_info.get("is_dgw"):
            dgw_count = len(self.gw_info.get("dgw_teams", {}))
            sections.append(f"- **DGW**: {dgw_count} teams with double fixtures")
        best_chip = self.chip_analysis.get("best_chip", {})
        if best_chip:
            sections.append(f"- **Chip advice**: {best_chip.get('name', '?')} ({best_chip.get('score', 0)}/100)")

        sections.append(f"\nI can help with:")
        sections.append("- 🔄 **Player comparisons**: 'Salah vs Haaland'")
        sections.append("- 👑 **Captain advice**: 'Who should I captain?'")
        sections.append("- 🎯 **Chip strategy**: 'Should I Bench Boost?'")
        sections.append("- 📊 **Player lookups**: 'Tell me about Haaland'")
        sections.append("- 💰 **Transfer advice**: 'Should I sell Salah?'")
        sections.append("- 💎 **Differentials**: 'Best hidden gems?'")
        sections.append("- ⚽ **Squad help**: 'Show the optimal squad'")
        sections.append("- 🔥 **DGW info**: 'Which teams play twice?'")

        return {
            "answer": "\n".join(sections),
            "data": {"type": "general"},
            "suggestions": ["Who should I captain?", "Best DGW players?", "Show optimal squad", "Best differentials?"]
        }

    # ── Utilities ──────────────────────────────────────────

    def _player_card(self, p: dict) -> dict:
        if not p:
            return {}
        return {
            "name": p.get("name", "?"), "team": p.get("team", "?"),
            "position": p.get("position", "?"), "price": p.get("price", 0),
            "predicted_points": p.get("predicted_points", 0),
            "form": p.get("form", 0), "is_dgw": p.get("is_dgw", False),
            "fixtures": p.get("fixtures", []), "confidence": p.get("confidence", 0),
            "starter_quality": p.get("starter_quality", {}),
        }

    def _fallback(self, msg: str) -> dict:
        return {
            "answer": msg,
            "data": {"type": "error"},
            "suggestions": ["Who should I captain?", "Compare Salah vs Haaland", "Best DGW players?", "Show optimal squad"]
        }
