import copy
import unittest
from unittest.mock import patch

import data_fetcher
from prediction_engine import PredictionEngine, PLAYER_ROLE_MODEL_VERSION, sanitize_legacy_role_cache


def player(**updates):
    data = dict(id=1,team=1,position_id=3,element_type=3,status='a',
                chance_of_playing_next_round=None,minutes=720,starts=8,
                previous_minutes=3300,previous_starts=36,previous_games=38,
                web_name='Starter',now_cost=50,form='4',points_per_game='4',ict_index='1')
    data.update(updates)
    return data


def engine(p, games=8):
    model=PredictionEngine.__new__(PredictionEngine)
    model.players={p['id']:p}
    model.current_gw=8; model.next_gw=9
    model.fixtures=[{'team_h':1,'team_a':2,'event':i,'finished':True} for i in range(1,games+1)]
    model.fixtures.append({'team_h':1,'team_a':2,'event':9,'finished':False})
    model.bootstrap={'events':[{'id':8,'finished':True}], 'teams':[{'id':1},{'id':2}]}
    model.teams={1:{'short_name':'ONE'},2:{'short_name':'TWO'}}
    model.team_stats={}; model.fixture_xg_cache={}; model.promoted_team_ids=set()
    return model


def role(model, p, **kwargs):
    profile=model.calculate_expected_minutes(p,**kwargs)
    return profile, model._derive_tier_label(profile,p['position_id'],model._get_availability(p))


class PlayerRoleTests(unittest.TestCase):
    def test_reliable_outfield_and_goalkeeper_are_nailed(self):
        for position in (1,2,3,4):
            p=player(position_id=position)
            profile,tier=role(engine(p),p)
            self.assertEqual(tier,'nailed')
            self.assertGreaterEqual(profile['p_start'],.85)
            self.assertLessEqual(profile['p_start'],1)

    def test_preseason_relies_on_measured_previous_season_role(self):
        p=player(minutes=0,starts=0)
        profile,tier=role(engine(p,games=0),p)
        self.assertAlmostEqual(profile['p_start'],round(36/38,3))
        self.assertEqual(profile['historical_role_weight'],1)
        self.assertEqual(profile['current_season_role_weight'],0)
        self.assertEqual(tier,'nailed')

    def test_history_fades_by_team_opportunities_not_individual_minutes(self):
        for promoted,horizon in ((False,10),(True,6)):
            probabilities=[]
            for games in range(horizon+1):
                p=player(minutes=0,starts=0)
                model=engine(p,games=games)
                if promoted:
                    model.promoted_team_ids={1}
                profile,_=role(model,p)
                self.assertAlmostEqual(profile['historical_role_weight'],round(1-games/horizon,3))
                self.assertAlmostEqual(profile['p_start'],round((36/38)*(1-games/horizon),3))
                probabilities.append(profile['p_start'])
            self.assertEqual(probabilities[-1],0)
            self.assertTrue(all(a>=b for a,b in zip(probabilities,probabilities[1:])))

    def test_early_current_season_data_does_not_instantly_replace_history(self):
        p=player(minutes=90,starts=1,previous_starts=0,previous_minutes=450)
        profile,_=role(engine(p,games=1),p)
        self.assertEqual(profile['current_season_role_weight'],.1)
        self.assertEqual(profile['p_start'],.1)

    def test_mature_season_uses_current_role_for_starters_and_nonplayers(self):
        for starts in (0,1,5,10):
            p=player(minutes=starts*90,starts=starts,previous_starts=38)
            profile,tier=role(engine(p,games=10),p)
            self.assertEqual(profile['historical_role_weight'],0)
            self.assertEqual(profile['p_start'],starts/10)
            self.assertEqual(tier=='nailed', starts==10)
        p=player(minutes=0,starts=0)
        profile,_=role(engine(p,games=10),p)
        self.assertEqual(profile['xmins'],7)  # Existing substitute-appearance model, no old minutes.

    def test_mature_missing_current_data_does_not_resurrect_history(self):
        p=player(minutes=None,starts=None)
        model=engine(p,games=10)
        profile,tier=role(model,p)
        self.assertIsNone(profile['p_start'])
        self.assertEqual(profile['historical_role_weight'],0)
        self.assertEqual(tier,'unknown')
        self.assertEqual(model._current_team_position_competition(p)['competition_score'],0)

    def test_zero_current_minutes_is_evidence_even_with_high_historical_minutes(self):
        p=player(minutes=0,starts=0)
        profile,tier=role(engine(p),p)
        self.assertEqual(tier,'bench_warmer')
        self.assertTrue(profile['has_current_season_evidence'])
        self.assertLess(profile['p_start'],.2)

    def test_rotation_and_fringe_do_not_become_nailed(self):
        for starts in (1,3,4,5):
            p=player(minutes=starts*75,starts=starts)
            profile,tier=role(engine(p),p)
            self.assertNotEqual(tier,'nailed')
        p=player(minutes=375,starts=5)
        profile,tier=role(engine(p),p,teammates_out=3,out_minutes=1000)
        self.assertGreater(profile['p_start'],.85)
        self.assertNotEqual(tier,'nailed')  # A short-term opportunity is not a nailed role.

    def test_unavailable_overrides_history_chance_and_teammate_boost(self):
        for status in ('i','s','u','n'):
            p=player(status=status,chance_of_playing_next_round=100)
            model=engine(p)
            profile,tier=role(model,p,teammates_out=3,out_minutes=2000)
            self.assertEqual(tier,'unavailable')
            self.assertEqual(profile['p_start'],0)
            self.assertEqual(profile['xmins'],0)
            self.assertEqual(profile['p_plays_60'],0)
            prediction=model.predict_player(1)
            self.assertEqual(prediction['starter_quality']['p_start'],0)
            self.assertEqual(prediction['predicted_points'],0)

    def test_blank_fixture_has_explicit_zero_start_probability(self):
        p=player()
        prediction=engine(p).predict_player(1,38)
        self.assertEqual(prediction['starter_quality']['p_start'],0)
        self.assertEqual(prediction['starter_quality']['tier'],'unavailable')

    def test_explicit_zero_chance_is_unavailable_even_status_a(self):
        p=player(chance_of_playing_next_round=0)
        profile,tier=role(engine(p),p)
        self.assertEqual((profile['p_start'],tier),(0,'unavailable'))

    def test_doubtful_is_discounted_once_after_boost_never_nailed(self):
        for chance in (25,50,75,100):
            p=player(status='d',chance_of_playing_next_round=chance)
            profile,tier=role(engine(p),p,teammates_out=3,out_minutes=2000)
            self.assertNotEqual(tier,'nailed')
            self.assertLessEqual(profile['p_start'],chance/100)
            self.assertAlmostEqual(profile['p_start'],round(profile['p_start_if_available']*chance/100,3))
        p=player(status='d',chance_of_playing_next_round=None)
        profile,tier=role(engine(p),p)
        self.assertIsNone(profile['p_start'])
        self.assertEqual(tier,'unknown')

    def test_missing_invalid_data_is_not_silently_100_percent(self):
        for chance in ('bad',float('nan'),float('inf'),101,-1):
            p=player(chance_of_playing_next_round=chance)
            profile,tier=role(engine(p),p)
            self.assertIsNone(profile['p_start'])
            self.assertEqual(tier,'unknown')
        for updates in ({'status':None}, {'starts':None}, {'minutes':None,'starts':None}):
            p=player(**updates)
            profile,tier=role(engine(p),p)
            self.assertIsNone(profile['p_start'])
            self.assertNotEqual(tier,'nailed')
        p=player(minutes=0,starts=None,previous_minutes=0,previous_starts=None,previous_games=0)
        profile,tier=role(engine(p),p)
        self.assertIsNone(profile['p_start'])
        self.assertEqual(tier,'unknown')

    def test_available_status_with_null_chance_does_not_imply_start(self):
        p=player(minutes=0,starts=0)
        model=engine(p)
        self.assertEqual(model._get_availability(p)['chance'],100)
        self.assertLess(role(model,p)[0]['p_start'],.2)

    def test_decimal_probability_and_fpl_percentage_have_distinct_units(self):
        p=player(chance_of_playing_next_round=75)
        profile,_=role(engine(p),p)
        self.assertEqual(profile['availability_probability'],.75)
        self.assertLessEqual(profile['p_start'],.75)
        model=engine(p)
        bad={'p_start':95,'rotation_risk':.15,'xmins':90,'selection_evidence':True,'availability_status':'available'}
        self.assertEqual(model._derive_tier_label(bad,3),'unknown')

    def test_finished_current_gw_and_double_fixtures_use_full_opportunities(self):
        p=player(starts=7,minutes=630)
        profile,_=role(engine(p,games=8),p)
        expected=.2*(36/38)+.8*(7/8)
        self.assertAlmostEqual(profile['p_start'],round(expected,3))
        p=player(starts=8,minutes=720)
        profile,_=role(engine(p,games=9),p)
        self.assertEqual(profile['selection_fixtures'],9)
        self.assertAlmostEqual(profile['p_start'],round(.1*(36/38)+.9*(8/9),3))

    def test_recent_zero_starts_demotes_former_starter(self):
        p=player(_recent_games=5,_recent_start_rate=0,_recent_avg_mins=5)
        profile,tier=role(engine(p),p)
        self.assertNotEqual(tier,'nailed')
        self.assertLess(profile['p_start'],.45)

    def test_preparation_loads_recent_role_from_same_detail_source(self):
        p=player()
        model=engine(p)
        with patch('prediction_engine.get_last_season_rates',return_value={}), patch('prediction_engine.get_recent_gw_stats',return_value={'recent_games':5,'recent_start_rate':0,'recent_avg_mins':0}) as recent:
            model._prepare_player_priors()
        recent.assert_called_once_with(1)
        self.assertEqual(p['_recent_start_rate'],0)
        self.assertNotEqual(role(model,p)[1],'nailed')

    def test_doubtful_news_override_is_used_by_both_availability_and_probability(self):
        p=player(_news_override={'status':'d','chance':50})
        model=engine(p)
        profile,tier=role(model,p)
        self.assertEqual(model._get_availability(p)['status'],'doubtful')
        self.assertLessEqual(profile['p_start'],.5)
        self.assertNotEqual(tier,'nailed')

    def test_predict_player_exports_same_profile_and_tier(self):
        p=player()
        model=engine(p)
        xg={'team_xg':1.3,'team_xgc':1,'cs_probability':.3,'h2h':{}}
        summary={'last5_form':'','last5_win_rate':0,'opp_last5_form':'','opp_last5_win_rate':0,'momentum':0}
        with patch('prediction_engine.get_fixture_xg',return_value=xg), patch('prediction_engine.get_team_analysis_summary',return_value=summary), patch.object(model,'_fixture_ev',return_value={'appearance':1,'other':1}), patch.object(model,'_calc_all_factors',return_value={}):
            prediction=model.predict_player(1,9)
        profile,tier=role(model,p)
        self.assertEqual(prediction['starter_quality']['p_start'],profile['p_start'])
        self.assertEqual(prediction['starter_quality']['tier'],tier)

    def test_stale_cache_does_not_keep_old_labels_in_any_player_list(self):
        row={'player_id':1,'starter_quality':{'tier':'nailed','p_start':1}}
        old={'predictions':[row], 'squad':{'captain':copy.deepcopy(row)}}
        result=sanitize_legacy_role_cache(old)
        self.assertIsNone(result['predictions'][0]['starter_quality']['p_start'])
        self.assertEqual(result['squad']['captain']['starter_quality']['tier'],'unknown')
        self.assertEqual(old['predictions'][0]['starter_quality']['tier'],'nailed')
        fresh={**old,'role_model_version':PLAYER_ROLE_MODEL_VERSION}
        self.assertIs(sanitize_legacy_role_cache(fresh),fresh)


class RoleSourceTests(unittest.TestCase):
    def test_previous_starts_not_inferred_and_appearances_not_denominator(self):
        for starts in (None,0,10):
            detail={'history_past':[{'season_name':'2025/26','minutes':900,'starts':starts,
                                     'appearances':10,'expected_goals':'1','expected_assists':'1','bonus':1}]}
            with patch.object(data_fetcher,'fetch_player_detail',return_value=detail), patch.object(data_fetcher,'_get_previous_season_name',return_value='2025/26'):
                rates=data_fetcher.get_last_season_rates(1)
            self.assertEqual(rates['starts'],starts)
            self.assertEqual(rates['games'],38)

    def test_recent_starts_count_starts_not_60_minutes_and_include_bench(self):
        detail={'history':[{'minutes':40,'starts':1}, {'minutes':65,'starts':0}, {'minutes':0,'starts':0}]}
        with patch.object(data_fetcher,'fetch_player_detail',return_value=detail):
            stats=data_fetcher.get_recent_gw_stats(1)
        self.assertAlmostEqual(stats['recent_start_rate'],1/3)
        self.assertEqual(stats['recent_games'],3)
        detail['history'][1]['starts']=None
        with patch.object(data_fetcher,'fetch_player_detail',return_value=detail):
            self.assertIsNone(data_fetcher.get_recent_gw_stats(1)['recent_start_rate'])

    def test_championship_uses_all_team_matches_and_explicit_start_flags(self):
        for known_flags in (True, False):
            p=player(web_name='Example',minutes=0,starts=0,previous_minutes=0)
            model=engine(p)
            model.promoted_team_ids={1}
            model.teams={1:{'name':'Team One','short_name':'ONE'},2:{'name':'Team Two','short_name':'TWO'}}
            events=[]
            for index in range(10):
                event={'match_id':index+1,'match_hometeam_id':100,'match_awayteam_id':200,
                       'match_hometeam_name':'Team One','match_awayteam_name':'Team Two'}
                if index < 5:
                    record={'player_key':1,'player_name':'Example','team_id':100,'player_minutes_played':90}
                    if known_flags:
                        record['player_substitute']='no'
                    event['lineup']=[record,dict(record)]  # Duplicate nested rows are one match.
                events.append(event)
            model.championship_history=events
            model._prepare_championship_roles()
            self.assertEqual(p['championship_role']['matches'],10)
            self.assertEqual(p['championship_role']['minutes'],450)
            self.assertEqual(p['championship_role']['start_rate'],.5 if known_flags else None)

    def test_player_detail_memo_expires(self):
        with patch.dict(data_fetcher._PLAYER_DETAIL_MEMO,{},clear=True), patch.object(data_fetcher.time,'time',return_value=1000), patch.object(data_fetcher,'_get',return_value={'history':[]}) as get:
            data_fetcher.fetch_player_detail(1)
            data_fetcher.fetch_player_detail(1)
            self.assertEqual(get.call_count,1)
            with patch.object(data_fetcher.time,'time',return_value=1000+data_fetcher.PLAYER_DETAIL_TTL+1):
                data_fetcher.fetch_player_detail(1)
            self.assertEqual(get.call_count,2)
            self.assertEqual(get.call_args.kwargs['cache_ttl'],data_fetcher.PLAYER_DETAIL_TTL)


if __name__=='__main__': unittest.main()
