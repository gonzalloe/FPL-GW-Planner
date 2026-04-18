"""
FPL Predictor - News Aggregator
Gathers football news from multiple reliable web sources.
Focuses on: injuries, transfers, team news, tactical changes, press conferences.
"""
import re
import json
import time
import hashlib
import requests
from pathlib import Path
from datetime import datetime, timedelta

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

# Reliable football news sources with RSS/API endpoints
NEWS_SOURCES = {
    "fpl_official": {
        "name": "FPL Official",
        "url": "https://fantasy.premierleague.com/api/bootstrap-static/",
        "type": "api",
        "reliability": 10,
        "icon": "⚽",
    },
    "premier_injuries": {
        "name": "PremierInjuries.com",
        "url": "https://www.premierinjuries.com/injury-table.php",
        "type": "web",
        "reliability": 9,
        "icon": "🏥",
    },
    "premier_league": {
        "name": "PremierLeague.com",
        "url": "https://www.premierleague.com/news",
        "type": "web",
        "reliability": 10,
        "icon": "🏟️",
    },
    "bbc_football": {
        "name": "BBC Sport Football",
        "url": "https://feeds.bbci.co.uk/sport/football/rss.xml",
        "type": "rss",
        "reliability": 9,
        "icon": "📺",
    },
    "guardian_football": {
        "name": "The Guardian Football",
        "url": "https://www.theguardian.com/football/rss",
        "type": "rss",
        "reliability": 8,
        "icon": "📰",
    },
    "sky_sports": {
        "name": "Sky Sports Football",
        "url": "https://feeds.skysports.com/feeds/rss/football.xml",
        "type": "rss",
        "reliability": 8,
        "icon": "📡",
    },
    "athletic": {
        "name": "The Athletic",
        "url": "https://theathletic.com/football/premier-league/",
        "type": "web",
        "reliability": 9,
        "icon": "📊",
    },
    "fpl_community": {
        "name": "FPL Community",
        "url": "https://www.fplgameweek.com/",
        "type": "web",
        "reliability": 7,
        "icon": "👥",
    },
}

# Premier League team keywords for matching news
PL_TEAMS = {
    "Arsenal": ["Arsenal", "Gunners", "ARS"],
    "Aston Villa": ["Aston Villa", "Villa", "AVL"],
    "Bournemouth": ["Bournemouth", "Cherries", "BOU"],
    "Brentford": ["Brentford", "Bees", "BRE"],
    "Brighton": ["Brighton", "Seagulls", "BHA"],
    "Chelsea": ["Chelsea", "Blues", "CHE"],
    "Crystal Palace": ["Crystal Palace", "Palace", "Eagles", "CRY"],
    "Everton": ["Everton", "Toffees", "EVE"],
    "Fulham": ["Fulham", "Cottagers", "FUL"],
    "Ipswich": ["Ipswich", "Tractor Boys", "IPS"],
    "Leicester": ["Leicester", "Foxes", "LEI"],
    "Liverpool": ["Liverpool", "Reds", "LIV"],
    "Man City": ["Manchester City", "Man City", "Citizens", "MCI"],
    "Man Utd": ["Manchester United", "Man Utd", "Man United", "Red Devils", "MUN"],
    "Newcastle": ["Newcastle", "Magpies", "NEW"],
    "Nott'm Forest": ["Nottingham Forest", "Forest", "NFO"],
    "Southampton": ["Southampton", "Saints", "SOU"],
    "Spurs": ["Tottenham", "Spurs", "TOT"],
    "West Ham": ["West Ham", "Hammers", "WHU"],
    "Wolves": ["Wolverhampton", "Wolves", "WOL"],
}

# Injury-related keywords
INJURY_KEYWORDS = [
    "injury", "injured", "hamstring", "knee", "ankle", "muscle", "strain",
    "fracture", "surgery", "operation", "ligament", "groin", "calf",
    "shoulder", "concussion", "illness", "sick", "virus", "covid",
    "fitness", "scan", "doubt", "doubtful", "ruled out", "sidelined",
    "setback", "blow", "miss", "absent", "unavailable", "out for",
    "recovery", "rehab", "return", "comeback", "fit again", "back in training",
]

# Transfer keywords
TRANSFER_KEYWORDS = [
    "transfer", "sign", "signing", "deal", "loan", "bid", "offer",
    "move", "agree", "contract", "fee", "target", "interest",
    "swap", "deadline", "window", "sold", "buy", "purchase",
]

# Tactical/team news keywords
TACTICAL_KEYWORDS = [
    "lineup", "line-up", "team news", "starting", "formation",
    "rotation", "rest", "dropped", "benched", "substitute",
    "tactics", "system", "press conference", "presser",
    "captain", "penalty", "set piece", "corner", "free kick",
]


class NewsAggregator:
    """Aggregates football news from multiple sources."""

    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }

    def get_all_news(self, max_items: int = 50) -> list:
        """
        Gather news from all sources. Returns a list of news items.
        Each item: {title, summary, source, url, category, teams, reliability, timestamp}
        """
        all_news = []

        # 1. FPL Official player news (most reliable for FPL decisions)
        fpl_news = self._get_fpl_player_news()
        all_news.extend(fpl_news)

        # 2. RSS feeds
        for source_key in ["bbc_football", "guardian_football", "sky_sports"]:
            source = NEWS_SOURCES[source_key]
            try:
                items = self._fetch_rss(source)
                all_news.extend(items)
            except Exception:
                pass

        # 3. Web search for specific injury/transfer news
        injury_news = self._search_injury_news()
        all_news.extend(injury_news)

        # Deduplicate by title similarity
        all_news = self._deduplicate(all_news)

        # Sort by reliability * recency
        all_news.sort(key=lambda x: (x.get("reliability", 5), x.get("timestamp", "")), reverse=True)

        return all_news[:max_items]

    def _get_fpl_player_news(self) -> list:
        """Extract injury/status news from FPL API player data."""
        news = []
        try:
            cache_file = CACHE_DIR / "bootstrap.json"
            if cache_file.exists():
                data = json.loads(cache_file.read_text(encoding="utf-8"))
            else:
                resp = requests.get(
                    "https://fantasy.premierleague.com/api/bootstrap-static/",
                    headers=self.headers, timeout=15
                )
                resp.raise_for_status()
                data = resp.json()

            teams = {t["id"]: t["short_name"] for t in data.get("teams", [])}

            for player in data.get("elements", []):
                player_news = player.get("news", "").strip()
                if not player_news:
                    continue

                status = player.get("status", "a")
                chance = player.get("chance_of_playing_next_round")
                name = player.get("web_name", "Unknown")
                team = teams.get(player.get("team"), "???")

                category = "status"
                if status in ("i", "u", "s"):
                    category = "injury"
                elif status == "d":
                    category = "doubt"
                elif status == "n":
                    category = "unavailable"

                severity = "info"
                if status in ("i", "u", "s", "n"):
                    severity = "critical"
                elif status == "d":
                    severity = "warning"

                news.append({
                    "title": f"{name} ({team}) — {player_news}",
                    "summary": player_news,
                    "source": "FPL Official",
                    "source_icon": "⚽",
                    "url": f"https://fantasy.premierleague.com/player-list",
                    "category": category,
                    "severity": severity,
                    "teams": [team],
                    "players": [name],
                    "reliability": 10,
                    "timestamp": datetime.now().isoformat(),
                    "chance_of_playing": chance,
                    "status": status,
                })

        except Exception:
            pass

        return news

    def _fetch_rss(self, source: dict) -> list:
        """Fetch and parse an RSS feed."""
        news = []
        try:
            cache_key = hashlib.md5(source["url"].encode()).hexdigest()
            cache_file = CACHE_DIR / f"rss_{cache_key}.json"

            # Cache RSS for 30 minutes
            if cache_file.exists():
                age = time.time() - cache_file.stat().st_mtime
                if age < 1800:
                    return json.loads(cache_file.read_text(encoding="utf-8"))

            time.sleep(0.5)
            resp = requests.get(source["url"], headers=self.headers, timeout=15)
            resp.raise_for_status()
            text = resp.text

            # Simple RSS parsing (no external deps)
            items = self._parse_rss_xml(text)

            for item in items[:20]:
                title = item.get("title", "")
                desc = item.get("description", "")
                link = item.get("link", "")

                # Filter: only PL-related news
                full_text = f"{title} {desc}".lower()
                matched_teams = self._match_teams(full_text)
                if not matched_teams and not any(
                    kw in full_text for kw in ["premier league", "epl", "fpl"]
                ):
                    continue

                category = self._categorize(full_text)

                news.append({
                    "title": title,
                    "summary": desc[:200] if desc else "",
                    "source": source["name"],
                    "source_icon": source.get("icon", "📰"),
                    "url": link,
                    "category": category,
                    "severity": "info",
                    "teams": matched_teams,
                    "players": [],
                    "reliability": source["reliability"],
                    "timestamp": item.get("pubDate", datetime.now().isoformat()),
                })

            # Cache results
            cache_file.write_text(json.dumps(news, ensure_ascii=False), encoding="utf-8")

        except Exception:
            pass

        return news

    def _parse_rss_xml(self, xml_text: str) -> list:
        """Minimal RSS XML parser (no lxml dependency)."""
        items = []
        # Find all <item>...</item> blocks
        item_pattern = re.compile(r'<item>(.*?)</item>', re.DOTALL)
        title_pattern = re.compile(r'<title>(.*?)</title>', re.DOTALL)
        desc_pattern = re.compile(r'<description>(.*?)</description>', re.DOTALL)
        link_pattern = re.compile(r'<link>(.*?)</link>', re.DOTALL)
        date_pattern = re.compile(r'<pubDate>(.*?)</pubDate>', re.DOTALL)

        for match in item_pattern.finditer(xml_text):
            block = match.group(1)
            item = {}

            t = title_pattern.search(block)
            if t:
                item["title"] = self._clean_html(t.group(1))

            d = desc_pattern.search(block)
            if d:
                item["description"] = self._clean_html(d.group(1))

            l = link_pattern.search(block)
            if l:
                item["link"] = l.group(1).strip()

            p = date_pattern.search(block)
            if p:
                item["pubDate"] = p.group(1).strip()

            if item.get("title"):
                items.append(item)

        return items

    def _clean_html(self, text: str) -> str:
        """Remove HTML tags and CDATA."""
        text = re.sub(r'<!\[CDATA\[(.*?)\]\]>', r'\1', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', '', text)
        text = re.sub(r'&amp;', '&', text)
        text = re.sub(r'&lt;', '<', text)
        text = re.sub(r'&gt;', '>', text)
        text = re.sub(r'&quot;', '"', text)
        text = re.sub(r'&#39;', "'", text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    def _search_injury_news(self) -> list:
        """Search for PL injury news via web search."""
        news = []
        # We'll use a simple approach: check FPL API data for injury flags
        # and supplement with status data
        try:
            cache_file = CACHE_DIR / "bootstrap.json"
            if not cache_file.exists():
                return news

            data = json.loads(cache_file.read_text(encoding="utf-8"))
            teams = {t["id"]: t for t in data.get("teams", [])}

            # Find recently changed statuses (players with news_added dates)
            for player in data.get("elements", []):
                news_added = player.get("news_added")
                if not news_added:
                    continue

                # Only recent news (within last 7 days)
                try:
                    news_date = datetime.fromisoformat(
                        news_added.replace("Z", "+00:00")
                    ).replace(tzinfo=None)
                    if (datetime.now() - news_date).days > 7:
                        continue
                except (ValueError, TypeError):
                    continue

                status = player.get("status", "a")
                if status == "a":
                    continue  # Skip fully available players

                name = player.get("web_name", "Unknown")
                team = teams.get(player.get("team"), {})
                team_name = team.get("short_name", "???")
                player_news = player.get("news", "")

                # Determine if returning or out
                chance = player.get("chance_of_playing_next_round")
                if chance is not None and chance >= 75:
                    severity = "positive"
                    category = "return"
                elif chance is not None and chance <= 25:
                    severity = "critical"
                    category = "injury"
                else:
                    severity = "warning"
                    category = "doubt"

                news.append({
                    "title": f"🏥 {name} ({team_name}): {player_news or 'Status update'}",
                    "summary": f"{name} has a {chance}% chance of playing. {player_news}",
                    "source": "FPL Status Tracker",
                    "source_icon": "🏥",
                    "url": "https://fantasy.premierleague.com",
                    "category": category,
                    "severity": severity,
                    "teams": [team_name],
                    "players": [name],
                    "reliability": 10,
                    "timestamp": news_added,
                    "chance_of_playing": chance,
                })

        except Exception:
            pass

        return news

    def _match_teams(self, text: str) -> list:
        """Find which PL teams are mentioned in text."""
        matched = []
        text_lower = text.lower()
        for team_short, keywords in PL_TEAMS.items():
            for kw in keywords:
                if kw.lower() in text_lower:
                    if team_short not in matched:
                        matched.append(team_short)
                    break
        return matched

    def _categorize(self, text: str) -> str:
        """Categorize news by content."""
        text_lower = text.lower()
        if any(kw in text_lower for kw in INJURY_KEYWORDS):
            return "injury"
        if any(kw in text_lower for kw in TRANSFER_KEYWORDS):
            return "transfer"
        if any(kw in text_lower for kw in TACTICAL_KEYWORDS):
            return "team_news"
        return "general"

    def _deduplicate(self, news: list) -> list:
        """Remove duplicate news items based on title similarity."""
        seen = set()
        unique = []
        for item in news:
            # Simple dedup by first 50 chars of title
            key = item.get("title", "")[:50].lower().strip()
            if key and key not in seen:
                seen.add(key)
                unique.append(item)
        return unique

    def get_news_for_player(self, player_name: str) -> list:
        """Get news specifically about a player."""
        all_news = self.get_all_news()
        player_lower = player_name.lower()
        return [
            n for n in all_news
            if player_lower in n.get("title", "").lower()
            or player_lower in n.get("summary", "").lower()
            or player_name in n.get("players", [])
        ]

    def get_news_for_team(self, team_short: str) -> list:
        """Get news specifically about a team."""
        all_news = self.get_all_news()
        return [n for n in all_news if team_short in n.get("teams", [])]

    def get_news_summary(self) -> dict:
        """Get a structured summary of all news for the dashboard."""
        all_news = self.get_all_news()

        return {
            "total": len(all_news),
            "injuries": [n for n in all_news if n["category"] == "injury"],
            "transfers": [n for n in all_news if n["category"] == "transfer"],
            "team_news": [n for n in all_news if n["category"] == "team_news"],
            "returns": [n for n in all_news if n["category"] == "return"],
            "doubts": [n for n in all_news if n["category"] == "doubt"],
            "general": [n for n in all_news if n["category"] == "general"],
            "all": all_news,
            "fetched_at": datetime.now().isoformat(),
            "sources": list(NEWS_SOURCES.keys()),
        }

    def get_injury_overrides(self, player_map: dict) -> dict:
        """
        Cross-reference external news with FPL player data to find
        injury information that FPL hasn't updated yet.

        Returns: {player_id: {"status": "i"/"d"/"a", "chance": int, "news": str, "source": str}}
        Only returns overrides for players whose FPL status doesn't match external reports.
        """
        overrides = {}

        # Build reverse lookup: name → player_id
        name_to_pid = {}
        for pid, p in player_map.items():
            web_name = p.get("web_name", "").lower()
            full_name = f"{p.get('first_name', '')} {p.get('second_name', '')}".lower().strip()
            second_name = p.get("second_name", "").lower()
            if web_name:
                name_to_pid[web_name] = pid
            if full_name:
                name_to_pid[full_name] = pid
            if second_name and len(second_name) > 3:
                name_to_pid[second_name] = pid

        # Get external news
        try:
            all_news = self.get_all_news()
        except Exception:
            return overrides

        # Extract injury-related news and match to players
        for item in all_news:
            if item.get("source") == "FPL Official":
                continue  # Skip FPL's own data, we already have it

            title_lower = item.get("title", "").lower()
            summary_lower = item.get("summary", "").lower()
            full_text = f"{title_lower} {summary_lower}"
            category = item.get("category", "")

            if category not in ("injury", "doubt", "return", "team_news"):
                continue

            # Try to match player names from the article
            for player_name_lower, pid in name_to_pid.items():
                if len(player_name_lower) < 4:
                    continue
                if player_name_lower in full_text:
                    p = player_map.get(pid, {})
                    fpl_status = p.get("status", "a")
                    fpl_chance = p.get("chance_of_playing_next_round")

                    # Determine what the news suggests
                    is_ruled_out = any(kw in full_text for kw in [
                        "ruled out", "out for", "miss", "sidelined", "surgery",
                        "fracture", "long-term", "confirmed injured", "will not play",
                        "not available", "absent", "knee injury", "ankle injury",
                    ])
                    is_doubtful = any(kw in full_text for kw in [
                        "doubt", "doubtful", "fitness test", "assess", "scan",
                        "50-50", "touch and go", "not certain",
                    ])
                    is_returning = any(kw in full_text for kw in [
                        "fit again", "back in training", "return", "available",
                        "passed fit", "in contention", "recovered", "comeback",
                    ])

                    # Only override if there's a mismatch with FPL's data
                    if is_ruled_out and fpl_status == "a":
                        # External says out, FPL says available → override
                        overrides[pid] = {
                            "status": "i", "chance": 0,
                            "news": item.get("title", "Ruled out per external report"),
                            "source": item.get("source", "External"),
                        }
                    elif is_doubtful and fpl_status == "a":
                        overrides[pid] = {
                            "status": "d", "chance": 50,
                            "news": item.get("title", "Doubtful per external report"),
                            "source": item.get("source", "External"),
                        }
                    elif is_returning and fpl_status in ("i", "u", "s"):
                        overrides[pid] = {
                            "status": "d", "chance": 75,
                            "news": item.get("title", "Returning per external report"),
                            "source": item.get("source", "External"),
                        }
                    break  # One match per news item

        return overrides
