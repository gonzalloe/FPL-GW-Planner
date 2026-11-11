"""Current-season actual FPL points, grouped by gameweek (never projections)."""
import math
from datetime import datetime, timezone


def _integer(value, nonnegative=False):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or not number.is_integer() or (nonnegative and number < 0):
        return None
    return int(number)


def _date(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (AttributeError, TypeError, ValueError):
        return None


def build_player_history(bootstrap, detail, player_id, now=None):
    player = next((p for p in bootstrap.get('elements', []) if p.get('id') == player_id), None)
    if player is None:
        raise LookupError('Player not found')
    history = detail.get('history')
    if not isinstance(history, list):
        raise ValueError('Player history is unavailable')
    events = sorted(bootstrap.get('events') or [], key=lambda event: event['id'])
    if not events:
        raise ValueError('Gameweek information is unavailable')
    teams = {team['id']: team.get('short_name') or team.get('name') for team in bootstrap.get('teams', [])}
    groups = {event['id']: [] for event in events}
    seen = set()
    for row in history:
        if not isinstance(row, dict) or _integer(row.get('round')) not in groups:
            raise ValueError('Invalid current-season history')
        gw = _integer(row['round'])
        fixture_id = _integer(row.get('fixture'), nonnegative=True)
        if fixture_id is not None:
            identity = (gw, fixture_id)
            if identity in seen:
                continue
            seen.add(identity)
        groups[gw].append({
            'fixture_id': fixture_id,
            'opponent': teams.get(row.get('opponent_team'), 'Unknown opponent'),
            'venue': 'H' if row.get('was_home') is True else 'A' if row.get('was_home') is False else None,
            'kickoff_time': row.get('kickoff_time'),
            'points': _integer(row.get('total_points')),
            'minutes': _integer(row.get('minutes'), nonnegative=True),
        })
    now = now or datetime.now(timezone.utc)
    gameweeks = []
    for event in events:
        fixtures = sorted(groups[event['id']], key=lambda row: row.get('kickoff_time') or '')
        deadline = _date(event.get('deadline_time'))
        started = bool(fixtures or event.get('finished') or event.get('is_current') or (deadline and deadline <= now))
        points = [row['points'] for row in fixtures]
        minutes = [row['minutes'] for row in fixtures]
        gameweeks.append({
            'gameweek': event['id'],
            'points': sum(points) if points and all(value is not None for value in points) else None,
            'minutes': sum(minutes) if minutes and all(value is not None for value in minutes) else None,
            'fixtures': fixtures,
            'status': ('complete' if event.get('finished') else 'live') if fixtures else
                      'no_record' if started else 'upcoming',
        })
    first_deadline = _date(events[0].get('deadline_time'))
    return {
        'player_id': player_id,
        'name': player.get('web_name') or 'Player',
        'team': teams.get(player.get('team'), ''),
        'season': f'{first_deadline.year}/{str(first_deadline.year + 1)[-2:]}' if first_deadline else None,
        'season_points': _integer(player.get('total_points')),
        'gameweeks': gameweeks,
        'source': 'FPL element-summary current-season history',
    }
