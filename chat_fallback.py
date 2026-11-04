"""Small, deterministic answers from the same snapshot sent to the chat model.

This path never calls a provider or guesses missing squad/forecast information.
"""
import re
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


def local_chat_response(question, snapshot):
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
