import unittest
from unittest.mock import patch

from chip_planner import SeasonChipPlanner, ProjectionDataUnavailable
from test_team_id_planner import context
from test_player_roles import engine, player


class ProjectionTests(unittest.TestCase):
    def planner(self, baseline):
        bootstrap,teams,players,_,fixtures,_=context()
        return SeasonChipPlanner(bootstrap=bootstrap,fixtures=fixtures,players=players,
                                 teams=teams,next_gw=9,baseline_predictions=baseline)

    def test_neutral_baseline_is_independent_of_original_fixture_count_and_difficulty(self):
        values=[]
        for points,count in [(0,0),(4,1),(8,2)]:
            baseline={'player_id':1,'position_id':1,'raw_xpts':points,'num_fixtures':count,'neutral_xpts':4}
            values.append(self.planner([baseline])._get_target_predictions(10)[0]['predicted_points'])
        self.assertEqual(values[0],values[1]);self.assertEqual(values[1],values[2])
        self.assertGreater(values[0],0)

    def test_legacy_cache_normalizes_double_gameweek_and_difficulty_once(self):
        planner=self.planner([])
        modifier=planner._position_fdr_modifier(3,2,True)
        fixture={'fdr':2,'is_home':True,'xp_adjusted':4*modifier}
        for count in [1,2]:
            base={'position_id':3,'raw_xpts':count*4*modifier,'num_fixtures':count,'fixtures':[fixture]*count}
            self.assertAlmostEqual(planner._neutral_baseline(base),4)
        with self.assertRaises(ProjectionDataUnavailable):planner._neutral_baseline({'num_fixtures':0,'raw_xpts':0})

    def test_blank_prediction_exports_role_and_neutral_points_for_future_gw(self):
        p=player();model=engine(p)
        with patch.object(model,'_neutral_fixture_xpts',return_value=4.0):
            blank=model.predict_player(1,10)
        self.assertEqual(blank['predicted_points'],0)
        self.assertEqual(blank['neutral_xpts'],4)
        self.assertEqual(blank['starter_quality']['p_start'],0)
        self.assertGreater(blank['neutral_starter_quality']['p_start'],0)
        projected=self.planner([blank])._get_target_predictions(10)[0]
        self.assertGreater(projected['predicted_points'],0)
        self.assertGreater(projected['starter_quality']['p_start'],0)
        self.assertEqual(projected['availability']['status'],'available')

    def test_actual_neutral_model_is_same_for_single_double_and_blank_gw(self):
        p=player();model=engine(p)
        from team_analysis import build_team_stats
        model.team_stats = build_team_stats([], model.teams)
        model.fixtures += [{'team_h':1,'team_a':2,'event':10,'finished':False}]*2
        points=[model.predict_player(1,gw)['neutral_xpts'] for gw in [9,10,11]]
        self.assertGreater(points[0],0)
        self.assertEqual(points[0],points[1]);self.assertEqual(points[1],points[2])

    def test_unavailable_player_does_not_gain_a_neutral_projection(self):
        p=player(status='i');baseline=engine(p).predict_player(1,9)
        self.assertEqual(baseline['neutral_xpts'],0)
        self.assertEqual(self.planner([baseline])._get_target_predictions(10)[0]['predicted_points'],0)
