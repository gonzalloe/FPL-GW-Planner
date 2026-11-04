"""Small, deterministic answers from the same snapshot sent to the chat model.

This path never calls a provider or guesses missing squad/forecast information.
"""
import re
import math
import unicodedata


def _normalise(text):
    text = unicodedata.normalize('NFKD', str(text)).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', ' ', text).strip()


def _number(value, suffix='', prefix=''):
    try:
        return f'{prefix}{float(value):.1f}{suffix}' if value is not None else '—'
    except (TypeError, ValueError):
        return '—'


def _cell(value):
    return str(value if value is not None else '—').replace('|', '/').replace('\n', ' ')


def _table(players):
    lines = ['| Player | Team | Pos | Price | xPts |', '|---|---|---|---|---|']
    for player in players:
        lines.append('| ' + ' | '.join((
            _cell(player.get('name')), _cell(player.get('team')), _cell(player.get('position')),
            _number(player.get('price'), 'm', '£'), _number(player.get('predicted_points')))) + ' |')
    return '\n'.join(lines)


def win_probability_percent(value):
    """Cached fixture probabilities are fractions; missing values stay unknown."""
    if value is None or isinstance(value, bool):
        return 'unavailable'
    try:
        probability = float(value)
    except (TypeError, ValueError):
        return 'unavailable'
    if not math.isfinite(probability) or not 0 <= probability <= 1:
        return 'unavailable'
    return f'{probability * 100:.1f}%'


def fixture_win_probabilities(players):
    rows = {}
    for player in players:
        team = player.get('team')
        if not team:
            continue
        for fixture in player.get('fixtures') or []:
            if not isinstance(fixture, dict) or not fixture.get('opponent'):
                continue
            venue = fixture.get('venue') or ('H' if fixture.get('is_home') is True else 'A' if fixture.get('is_home') is False else '?')
            key = (team, fixture['opponent'], venue)
            probability = win_probability_percent(fixture.get('win_probability'))
            if key not in rows or rows[key]['win_probability'] == 'unavailable':
                rows[key] = {'team': team, 'opponent': fixture['opponent'], 'venue': venue,
                             'win_probability': probability}
    return list(rows.values())


def match_probability_context(players):
    rows = fixture_win_probabilities(players)
    if not rows:
        return ''
    lines = ['MATCH WIN PROBABILITIES (same fixture values as Players table):',
             'These are model estimates of the named team winning that fixture. '
             'They differ from season win rate and player starting probability. '
             'A draw is possible: the opponent win probability is not 100% minus this value. '
             'Keep each double-gameweek fixture separate. Unavailable means missing, not zero.']
    lines.extend(f"{row['team']} vs {row['opponent']} ({row['venue']}): team win probability {row['win_probability']}" for row in rows)
    return '\n'.join(lines) + '\n'


def match_probability_response(question, snapshot):
    query = _normalise(question)
    if not (re.search(r'\b(win|winning)\b', query) and re.search(r'\b(probability|probabilities|prob|chance|chances|odds|likelihood)\b', query)):
        return None
    from ai_chat import FPLChatEngine
    players = snapshot.get('predictions') or []
    aliases = dict(FPLChatEngine.TEAM_ALIASES)
    for player in players:
        code = str(player.get('team') or '').lower()
        if code:
            aliases[code] = code
            if player.get('team_name'):
                aliases[_normalise(player['team_name'])] = code
    # Prefer full names over overlapping aliases such as "United".
    matches = [(m.start(), m.end(), code) for alias, code in aliases.items()
               for m in re.finditer(r'\b' + re.escape(_normalise(alias)) + r'\b', query)]
    spans, teams = [], set()
    for start, end, code in sorted(matches, key=lambda item: item[1] - item[0], reverse=True):
        if not any(start < right and end > left for left, right in spans):
            spans.append((start, end)); teams.add(code)
    if not teams:
        return None
    rows = [row for row in fixture_win_probabilities(players)
            if row['team'].lower() in teams and
            (len(teams) == 1 or row['opponent'].lower() in teams)]
    gw = (snapshot.get('metadata') or {}).get('gameweek', 'unknown')
    if not rows:
        answer = f'No matching fixture win probabilities are available for the named teams in the saved GW{gw} projections.'
    else:
        lines = [f'Model win probabilities from the saved GW{gw} projections, using the same values as the Players table:',
                 '| Team | Opponent | Venue | Win probability |', '|---|---|---|---|']
        lines.extend('| ' + ' | '.join(_cell(row[key]) for key in ('team', 'opponent', 'venue', 'win_probability')) + ' |' for row in rows)
        lines.append('H = home; A = away. These are fixture-specific model estimates. A draw is possible, so the opposing win probability cannot be inferred by subtracting one team’s probability from 100%.')
        answer = '\n\n'.join([lines[0], '\n'.join(lines[1:-1]), lines[-1]])
    return {'answer': answer, 'suggestions': ['Who should I captain?'], 'source': 'fixture_model'}


def local_chat_response(question, snapshot):
    match_answer = match_probability_response(question, snapshot)
    if match_answer:
        return match_answer
    players = snapshot.get('predictions') or []
    metadata = snapshot.get('metadata') or {}
    if not players:
        return {'answer': 'Current player projections are not available yet. Use Refresh Data, then ask about a player, captain or your squad.',
                'suggestions': [], 'source': 'local_data'}
    query = _normalise(question)
    gw = metadata.get('gameweek') or metadata.get('current_gw') or 'unknown'
    lines = [f'Using saved GW{gw} model projections for this answer.']
    by_id = {p.get('player_id'): p for p in players}
    picks = (snapshot.get('team') or {}).get('picks') or []
    owned = [by_id[p['element']] for p in picks if p.get('element') in by_id]
    mentioned = [p for p in players if len(_normalise(p.get('name', ''))) >= 4
                 and re.search(r'\b' + re.escape(_normalise(p['name'])) + r'\b', query)]
    personal = bool(re.search(r'\b(my|mine|squad|lineup|bench|captain|transfer|sell|keep)\b', query))
    # Reuse the existing team's alias list, without its recommendation handlers.
    from ai_chat import FPLChatEngine
    teams = {code for alias, code in FPLChatEngine.TEAM_ALIASES.items()
             if re.search(r'\b' + re.escape(_normalise(alias)) + r'\b', query)}
    def points(player):
        try:
            return float(player.get('predicted_points') or 0)
        except (TypeError, ValueError):
            return 0
    pool = sorted(players, key=points, reverse=True)
    if mentioned:
        pool = sorted(mentioned, key=points, reverse=True)
        lines.append('Current projections for the named players:')
    elif 'captain' in query and owned:
        pool = sorted(owned, key=points, reverse=True)
        lines.append(f"Highest projected captain option in your imported squad: **{pool[0].get('name', 'Unknown')}**.")
    elif personal and owned:
        pool = owned
        lines.append('Your imported squad projections:')
    elif teams:
        pool = [p for p in pool if str(p.get('team', '')).lower() in teams]
        lines.append('Current projections for players from the named teams:')
    else:
        for label, pattern in (('GKP', r'\b(goalkeeper|goalkeepers|keeper|keepers|gk)\b'),
                               ('DEF', r'\b(defender|defenders|def)\b'),
                               ('MID', r'\b(midfielder|midfielders|mid)\b'),
                               ('FWD', r'\b(forward|forwards|striker|strikers|fwd)\b')):
            if re.search(pattern, query):
                pool = [p for p in pool if p.get('position') == label]
                break
        budget = re.search(r'\b(?:under|below|less than)\s*£?\s*(\d+(?:\.\d+)?)', question.lower())
        if budget:
            limit = float(budget.group(1))
            pool = [p for p in pool if p.get('price') is not None and float(p['price']) <= limit]
        lines.append('Leading options in the saved projections:')
    if personal and not owned:
        lines.append('Import your team on My Team & Planner to personalise these options.')
    limit = 15 if personal and owned and 'captain' not in query and not mentioned else 5
    lines.append(_table(pool[:limit]) if pool else 'No matching player projections are available in this snapshot.')
    if re.search(r'\b(score|scoreline|result)\b', query):
        lines.append('An exact match-score forecast is not available in this snapshot; the table shows player expected points.')
    if re.search(r'\b(transfer|sell|buy|keep|roll|hit)\b', query):
        details = snapshot.get('team_context')
        if details:
            # Include the server-calculated allowance/lineup rather than inventing
            # a transfer cost, selling price, bench replacement or budget.
            selected = [line for line in details.splitlines() if line.startswith((
                'Team ID:', 'If ', 'MODEL-SUGGESTED LEGAL XI', 'MODEL-SUGGESTED BENCH'))]
            lines.extend(selected)
        lines.append('Use the GW Planner to compare holding with transfers against your budget, free transfers and legal starting XI.')
    lines.append('Expected points are estimates. This saved-data response uses your current question; repeat any constraints from earlier messages.')
    return {'answer': '\n\n'.join(lines), 'suggestions': ['Who should I captain?', 'Show my squad projections'], 'source': 'local_data'}
