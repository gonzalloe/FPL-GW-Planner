"""Bounded multi-week transfer search. Money is represented in tenths of £m."""
from collections import Counter
from functools import lru_cache

from config import MAX_PER_TEAM, POSITION_LIMITS


class TransferSearch:
    def __init__(self, players, predictions, gameweeks, width=24, candidates=8):
        self.players, self.predictions, self.gameweeks = players, predictions, gameweeks
        self.width, self.candidates = width, candidates
        self.pools = {}
        for gw in gameweeks:
            for pos in POSITION_LIMITS:
                ids = [pid for pid in predictions[gw] if pid in players and self.position(pid) == pos
                       and players[pid].get('status') not in ('u', 'n')]
                ids.sort(key=lambda pid: sum(self.points(pid, future) for future in gameweeks if future >= gw), reverse=True)
                self.pools[gw, pos] = list(dict.fromkeys(ids[:candidates] + sorted(ids, key=lambda pid: (self.price(pid), -self.points(pid, gw)))[:4]))

    def position(self, pid):
        return self.players[pid].get('element_type', self.players[pid].get('position_id'))

    def price(self, pid):
        return int(self.players[pid].get('now_cost', 0))

    def points(self, pid, gw):
        return float(self.predictions[gw].get(pid, {}).get('predicted_points', 0) or 0)

    def validate(self, squad):
        if len(squad) != 15 or len(set(squad)) != 15 or any(p not in self.players for p in squad):
            raise ValueError('A squad must contain 15 distinct known players.')
        positions = Counter(self.position(p) for p in squad)
        if any(not (row['squad_min'] <= positions[pos] <= row['squad_max']) for pos, row in POSITION_LIMITS.items()):
            raise ValueError('The squad has an invalid positional composition.')
        if max(Counter(self.players[p]['team'] for p in squad).values()) > MAX_PER_TEAM:
            raise ValueError('The squad exceeds the club player limit.')

    @lru_cache(maxsize=100000)
    def lineup(self, squad, gw):
        by_pos = {pos: sorted((p for p in squad if self.position(p) == pos),
                              key=lambda p: (-self.points(p, gw), p)) for pos in POSITION_LIMITS}
        best = None
        for defenders in range(3, 6):
            for midfielders in range(2, 6):
                forwards = 10 - defenders - midfielders
                if not 1 <= forwards <= 3:
                    continue
                counts = {1: 1, 2: defenders, 3: midfielders, 4: forwards}
                if any(len(by_pos[pos]) < count or not POSITION_LIMITS[pos]['play_min'] <= count <= POSITION_LIMITS[pos]['play_max'] for pos, count in counts.items()):
                    continue
                starters = tuple(p for pos, count in counts.items() for p in by_pos[pos][:count])
                captain = max(starters, key=lambda p: (self.points(p, gw), -p))
                score = sum(self.points(p, gw) for p in starters) + self.points(captain, gw)
                if best is None or score > best[0]:
                    best = (score, starters, captain)
        return best

    def moves(self, squad, bank, sell, gw):
        owned = set(squad)
        teams = Counter(self.players[p]['team'] for p in squad)
        for out in squad:
            old = self.players[out]
            for incoming in self.pools[gw, self.position(out)]:
                if incoming in owned:
                    continue
                new = self.players[incoming]
                if new['team'] != old['team'] and teams[new['team']] >= MAX_PER_TEAM:
                    continue
                new_bank = bank + sell[out] - self.price(incoming)
                if new_bank < 0:
                    continue
                new_squad = tuple(sorted((owned - {out}) | {incoming}))
                new_sell = dict(sell)
                del new_sell[out]
                new_sell[incoming] = self.price(incoming)
                yield new_squad, new_bank, new_sell, (out, incoming)

    def run(self, squad, bank, ft, sell, max_moves, allow_hits):
        # State: squad, bank, FT, selling prices, cumulative net score, path.
        initial = (tuple(sorted(squad)), bank, ft, dict(sell), 0.0, [])
        beam = [initial]
        for index, gw in enumerate(self.gameweeks):
            next_states = {}
            for old_squad, old_bank, old_ft, old_sell, score, path in beam:
                choices = [(old_squad, old_bank, old_sell, ())]
                singles = list(self.moves(old_squad, old_bank, old_sell, gw)) if max_moves else []
                def move_value(item):
                    out, incoming = item[3]
                    return sum(self.points(incoming, g) - self.points(out, g) for g in self.gameweeks[index:])
                singles.sort(key=move_value, reverse=True)
                # Limit branching but retain cash-raising moves as well as high scorers.
                first_moves = singles[:16] + sorted(singles, key=lambda m: m[1], reverse=True)[:4]
                choices.extend((s, b, prices, (move,)) for s, b, prices, move in first_moves)
                if max_moves >= 2 and (allow_hits or old_ft >= 2):
                    for s, b, prices, move in first_moves[:6] + first_moves[-2:]:
                        second_moves = [m for m in self.moves(s, b, prices, gw)
                                        if m[3][0] != move[1] and m[3][1] != move[0]]
                        second_moves.sort(key=move_value, reverse=True)
                        for s2, b2, prices2, move2 in second_moves[:6]:
                            choices.append((s2, b2, prices2, (move, move2)))
                for new_squad, new_bank, prices, moves in choices:
                    if not allow_hits and len(moves) > old_ft:
                        continue
                    hits = max(0, len(moves) - old_ft)
                    new_ft = min(5, max(0, old_ft - len(moves)) + 1)
                    points = self.lineup(new_squad, gw)[0]
                    new_score = score + points - 4 * hits
                    record = (old_squad, old_bank, old_ft, old_sell, new_squad, new_bank, moves, hits, points)
                    state = (new_squad, new_bank, new_ft, prices, new_score, path + [record])
                    key = (new_squad, new_bank, new_ft, tuple(sorted(prices.items())))
                    if key not in next_states or new_score > next_states[key][4]:
                        next_states[key] = state
            remaining = self.gameweeks[index + 1:]
            ranked = sorted(next_states.values(), key=lambda s: (s[4] + sum(self.lineup(s[0], g)[0] for g in remaining), s[4], -sum(len(x[6]) for x in s[5])), reverse=True)
            # The fixed-squad lookahead does not price the option of spending
            # saved FTs later. Preserve the strongest path at each FT balance,
            # otherwise short-term upgrades can eliminate every rolling path.
            by_ft = {}
            for state in ranked:
                by_ft.setdefault(state[2], state)
            reserved = {id(state) for state in by_ft.values()}
            capacity = max(self.width, len(reserved))
            beam = list(by_ft.values()) + [state for state in ranked if id(state) not in reserved][:capacity - len(reserved)]
        return max(beam, key=lambda s: (s[4], -sum(len(x[6]) for x in s[5])))
