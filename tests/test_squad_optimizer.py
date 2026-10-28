import unittest
from collections import Counter
from unittest.mock import patch
from squad_optimizer import SquadOptimizer


def pool():
    players=[]
    for pos,count in ((1,8),(2,16),(3,16),(4,10)):
        for index in range(count):
            pid=len(players)+1
            players.append({'player_id':pid,'position_id':pos,'team_id':index%10+1,
                            'name':str(pid),'price':4+(index%4)*0.5,
                            'predicted_points':3+(index%5),'starter_quality':{'tier':'nailed'}})
    return players


class SquadOptimizerTests(unittest.TestCase):
    def assert_valid(self,result,budget):
        self.assertNotIn('error',result)
        squad=result['squad']
        self.assertEqual(len(squad),15)
        self.assertEqual(len({p['player_id'] for p in squad}),15)
        self.assertEqual(Counter(p['position_id'] for p in squad),{1:2,2:5,3:5,4:3})
        self.assertLessEqual(max(Counter(p['team_id'] for p in squad).values()),3)
        self.assertLessEqual(sum(p['price'] for p in squad),budget)
        self.assertEqual(len(result['starting_xi']),11)
        self.assertIsNotNone(result['captain'])

    def test_bundled_cbc_solver_returns_valid_squad(self):
        self.assert_valid(SquadOptimizer(pool(),budget=75).optimize_squad(),75)

    def test_missing_solver_uses_real_fallback(self):
        optimizer=SquadOptimizer(pool(),budget=70)
        with patch.object(optimizer,'_solve_ilp_squad',side_effect=ImportError('missing solver')):
            self.assert_valid(optimizer.optimize_squad(),70)

    def test_fallback_reports_infeasible_budget_without_crashing(self):
        optimizer=SquadOptimizer(pool(),budget=10)
        with patch.object(optimizer,'_solve_ilp_squad',side_effect=OSError('missing executable')):
            self.assertIn('error',optimizer.optimize_squad())


if __name__=='__main__': unittest.main()
