import unittest

from transfer_search import TransferSearch
from gw_planner import GWPlanner


def fixture(weeks=3):
    positions = [1]*2 + [2]*5 + [3]*5 + [4]*3 + [2, 3, 4]
    players = {i: {'element_type': pos, 'team': (i-1) % 8 + 1,
                   'now_cost': 50, 'web_name': str(i), 'status': 'a'}
               for i, pos in enumerate(positions, 1)}
    predictions = {gw: {i: {'player_id': i, 'predicted_points': 3.0,
                            'position_id': p['element_type'], 'team_id': p['team'],
                            'price': 5.0, 'name': str(i), 'team': str(p['team']),
                            'position': str(p['element_type'])}
                        for i, p in players.items()} for gw in range(1, weeks+1)}
    return players, predictions


class TransferSearchTests(unittest.TestCase):
    def test_formation_and_captain(self):
        players, preds = fixture(1)
        for i in range(3, 8): preds[1][i]['predicted_points'] = 0
        search = TransferSearch(players, preds, [1])
        search.validate(list(range(1,16)))
        score, xi, captain = search.lineup(tuple(range(1,16)), 1)
        self.assertEqual(sum(search.position(p) == 2 for p in xi), 3)
        self.assertEqual(len(xi), 11)
        self.assertEqual(score, sum(search.points(p, 1) for p in xi) + search.points(captain, 1))
        with self.assertRaises(ValueError): search.validate([1]*15)

    def test_budget_selling_price_and_team_limit(self):
        players, preds = fixture(1)
        players[16]['now_cost'] = 60
        search = TransferSearch(players, preds, [1])
        squad = tuple(range(1,16))
        sell = {p: 50 for p in squad}; sell[3] = 45
        moves = list(search.moves(squad, 10, sell, 1))
        self.assertFalse(any(m[3] == (3, 16) for m in moves))
        for new, bank, _, _ in moves:
            search.validate(new)
            self.assertGreaterEqual(bank, 0)

    def test_lookahead_and_hits(self):
        players, preds = fixture(3)
        # A one-week improvement is inferior to investing in the future.
        for gw in (2, 3): preds[gw][16]['predicted_points'] = 10
        preds[1][17]['predicted_points'] = 5
        search = TransferSearch(players, preds, [1,2,3], width=32)
        result = search.run(list(range(1,16)), 0, 1, {i:50 for i in range(1,16)}, 1, False)
        self.assertTrue(any(pair[1] == 16 for record in result[5] for pair in record[6]))
        self.assertGreater(result[4], sum(search.lineup(tuple(range(1,16)), g)[0] for g in (1,2,3)))
        # Two large upgrades justify a single hit; it is deducted exactly once.
        players, preds = fixture(1)
        preds[1][16]['predicted_points'] = 20
        preds[1][17]['predicted_points'] = 20
        search = TransferSearch(players, preds, [1], width=64)
        result = search.run(list(range(1,16)), 0, 1, {i:50 for i in range(1,16)}, 2, True)
        self.assertEqual(result[5][0][7], 1)
        self.assertAlmostEqual(result[4], result[5][0][8] - 4)

    def test_balanced_search_rolls_for_future_funded_pair(self):
        players, preds = fixture(3)
        players[16]['now_cost'] = 40  # Fund the midfielder by downgrading a defender.
        players[17]['now_cost'] = 60
        for gw in preds:
            for pid in range(3, 8):
                preds[gw][pid]['predicted_points'] = 10
            preds[gw][16]['predicted_points'] = 0 if gw == 1 else 9
            preds[gw][17]['predicted_points'] = 0 if gw == 1 else 20
        preds[1][18]['predicted_points'] = 3.1  # Tempting but negligible immediate upgrade.
        search = TransferSearch(players, preds, [1, 2, 3], width=1)
        result = search.run(list(range(1, 16)), 0, 1, {i:50 for i in range(1,16)}, 2, False)
        self.assertEqual(result[5][0][6], ())
        self.assertEqual(result[5][1][2], 2)
        self.assertEqual(len(result[5][1][6]), 2)
        self.assertEqual({move[1] for move in result[5][1][6]}, {16, 17})
        self.assertEqual(sum(row[7] for row in result[5]), 0)

    def test_strategies_hold_without_improvement(self):
        players, preds = fixture(8)
        search = TransferSearch(players, preds, list(preds))
        for max_moves, hits in ((1, False), (2, False), (2, True)):
            result = search.run(list(range(1, 16)), 0, 1, {i:50 for i in range(1,16)}, max_moves, hits)
            self.assertTrue(all(not row[6] for row in result[5]))
            self.assertEqual([row[2] for row in result[5]], [1, 2, 3, 4, 5, 5, 5, 5])

    def test_planner_summary_uses_hold_baseline_and_no_double_hits(self):
        players, preds = fixture(2)
        for gw in preds: preds[gw][16]['predicted_points'] = 9
        planner = GWPlanner.__new__(GWPlanner)
        planner.players, planner.next_gw, planner.horizon = players, 1, 2
        planner._gw_pred_maps, planner._gw_predictions = preds, {g:list(p.values()) for g,p in preds.items()}
        planner._gw_infos = {1:{},2:{}}
        planner._build_squad_ticker = lambda squad: {}
        result = planner.plan_transfers(list(range(1,16)), free_transfers=1, chips_available=[])
        summary = result['summary']
        self.assertAlmostEqual(summary['net_gain_after_hits'], summary['total_xpts_gain'] - summary['total_hit_cost'], places=1)
        self.assertAlmostEqual(summary['net_gain_after_hits'], summary['expected_net_points'] - summary['save_transfer_points'], places=1)
        self.assertEqual(len(result['strategies']), 3)
        self.assertEqual(result['gw_plans'][0]['free_transfers_available'], 1)

    def test_aggressive_summary_subtracts_hit_once(self):
        players, preds = fixture(1)
        preds[1][16]['predicted_points'] = 20
        preds[1][17]['predicted_points'] = 20
        planner = GWPlanner.__new__(GWPlanner)
        planner.players, planner.next_gw, planner.horizon = players, 1, 1
        planner._gw_pred_maps, planner._gw_predictions = preds, {1:list(preds[1].values())}
        planner._gw_infos = {1:{}}
        planner._build_squad_ticker = lambda squad: {}
        plan = planner.plan_transfers(list(range(1,16)), free_transfers=1, chips_available=[], strategy='aggressive')
        self.assertEqual(plan['summary']['total_hit_cost'], 4)
        self.assertAlmostEqual(plan['summary']['total_xpts_gain'] - 4, plan['summary']['net_gain_after_hits'])
        self.assertAlmostEqual(plan['gw_plans'][0]['post_transfer_xpts'], plan['summary']['expected_net_points'])

    def test_synthetic_comparison_with_legacy_and_hold(self):
        players, preds = fixture(2)
        players[19] = {'element_type': 1, 'team': 9, 'now_cost': 50, 'web_name': '19'}
        for gw in preds:
            preds[gw][19] = {'player_id':19, 'predicted_points':4, 'position_id':1,
                              'team_id':9, 'price':5, 'name':'19', 'team':'9', 'position':'1'}
            preds[gw][16]['predicted_points'] = 10
        planner = GWPlanner.__new__(GWPlanner)
        planner.players, planner.next_gw, planner.horizon = players, 1, 2
        planner._gw_pred_maps, planner._gw_predictions = preds, {g:list(p.values()) for g,p in preds.items()}
        planner._gw_infos = {1:{}, 2:{}}
        planner._build_squad_ticker = lambda squad: {}
        search = TransferSearch(players, preds, [1,2])
        squad = list(range(1,16)); legacy = 0
        for gw in (1,2):
            enriched = [{'player_id':p, 'name':str(p), 'team':str(players[p]['team']),
                         'team_id':players[p]['team'], 'position':str(search.position(p)),
                         'position_id':search.position(p), 'price':5,
                         'predicted_points':search.points(p, gw)} for p in squad]
            moves = planner._find_best_transfers(enriched, preds[gw], 0, 1, 1, gw)
            for move in moves:
                squad.remove(move['out_id']); squad.append(move['in_id'])
            legacy += search.lineup(tuple(sorted(squad)), gw)[0]
        plan = planner.plan_transfers(list(range(1,16)), chips_available=[])
        self.assertGreater(plan['summary']['expected_net_points'], legacy)
        self.assertGreater(plan['summary']['expected_net_points'], plan['summary']['save_transfer_points'])


if __name__ == '__main__': unittest.main()
