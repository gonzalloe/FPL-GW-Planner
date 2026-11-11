import copy
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from player_history import build_player_history
with patch.dict(os.environ, {'SUPABASE_URL':'','SUPABASE_KEY':''}):
    import server


def sample():
    bootstrap={'elements':[{'id':7,'web_name':'Sample','team':1,'total_points':8}],
               'teams':[{'id':1,'short_name':'ONE'},{'id':2,'short_name':'TWO'}],
               'events':[{'id':1,'finished':True,'deadline_time':'2026-08-01T10:00:00Z'},
                         {'id':2,'finished':True,'deadline_time':'2026-08-08T10:00:00Z'},
                         {'id':3,'is_current':True,'deadline_time':'2026-08-15T10:00:00Z'},
                         {'id':4,'deadline_time':'2026-08-22T10:00:00Z'}]}
    detail={'history':[{'round':1,'fixture':1,'total_points':0,'minutes':0,'opponent_team':2,'was_home':True},
                       {'round':2,'fixture':2,'total_points':6,'minutes':90,'opponent_team':2,'was_home':False},
                       {'round':2,'fixture':3,'total_points':-1,'minutes':30,'opponent_team':1,'was_home':True},
                       {'round':3,'fixture':4,'total_points':3,'minutes':45,'opponent_team':2,'was_home':False}],
            'history_past':[{'season_name':'2025/26','total_points':9999}]}
    return bootstrap,detail


class PlayerHistoryTests(unittest.TestCase):
    def setUp(self):
        self.bootstrap,self.detail=sample()
        self.now=datetime(2026,8,16,tzinfo=timezone.utc)

    def build(self):return build_player_history(self.bootstrap,self.detail,7,self.now)

    def test_full_season_groups_doubles_preserves_zero_and_negative_points(self):
        result=self.build();rows=result['gameweeks']
        self.assertEqual(result['season'],'2026/27')
        self.assertEqual([row['gameweek'] for row in rows],[1,2,3,4])
        self.assertEqual(rows[0]['points'],0)
        self.assertEqual(rows[1]['points'],5)
        self.assertEqual(rows[1]['minutes'],120)
        self.assertEqual(rows[1]['fixtures'][1]['points'],-1)
        self.assertEqual(rows[1]['fixtures'][0]['opponent'],'TWO')
        self.assertEqual(rows[1]['fixtures'][0]['venue'],'A')
        self.assertEqual(rows[2]['status'],'live')
        self.assertEqual(rows[3]['status'],'upcoming')
        self.assertIsNone(rows[3]['points'])
        self.assertEqual(result['season_points'],8)
        self.assertNotIn('history_past',result)

    def test_absent_records_and_missing_fixture_values_are_not_zero(self):
        self.detail['history']=self.detail['history'][1:]
        self.detail['history'][0]['total_points']=None
        rows=self.build()['gameweeks']
        self.assertEqual(rows[0]['status'],'no_record')
        self.assertIsNone(rows[0]['points'])
        self.assertIsNone(rows[1]['points'])
        self.assertEqual(rows[1]['minutes'],120)

    def test_duplicate_fixture_is_not_counted_twice(self):
        self.detail['history'].append(copy.deepcopy(self.detail['history'][1]))
        self.assertEqual(self.build()['gameweeks'][1]['points'],5)

    def test_preseason_and_unavailable_players_keep_real_history(self):
        self.bootstrap['elements'][0]['status']='i'
        self.assertEqual(self.build()['gameweeks'][1]['points'],5)
        self.detail['history']=[]
        for event in self.bootstrap['events']:
            event.update(finished=False,is_current=False)
        preseason=build_player_history(self.bootstrap,self.detail,7,datetime(2026,7,1,tzinfo=timezone.utc))
        self.assertTrue(all(row['status']=='upcoming' and row['points'] is None for row in preseason['gameweeks']))

    def test_invalid_history_does_not_become_fabricated_empty_history(self):
        for value in [None,{},[{'round':99}],[None]]:
            self.detail['history']=value
            with self.assertRaises(ValueError):self.build()
        with self.assertRaises(LookupError):build_player_history(self.bootstrap,{'history':[]},99,self.now)

    def test_api_is_public_and_reuses_fpl_fetchers(self):
        server.limiter.enabled=False
        with patch.object(server,'_ensure_refresh_thread'),patch.object(server,'_auto_setup_accounts'),patch.object(server,'fetch_bootstrap',return_value=self.bootstrap),patch('data_fetcher.fetch_player_detail',return_value=self.detail) as fetch,patch.object(server,'_get_auth_user',side_effect=AssertionError('Actual history is public')):
            response=server.app.test_client().get('/api/players/7/history')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['gameweeks'][1]['points'],5)
        fetch.assert_called_once_with(7)
        self.assertNotIn('predicted_points',response.json)

    def test_api_handles_unknown_players_and_upstream_failure(self):
        server.limiter.enabled=False
        with patch.object(server,'_ensure_refresh_thread'),patch.object(server,'_auto_setup_accounts'),patch.object(server,'fetch_bootstrap',return_value=self.bootstrap),patch('data_fetcher.fetch_player_detail') as fetch:
            client=server.app.test_client()
            self.assertEqual(client.get('/api/players/99/history').status_code,404)
            fetch.assert_not_called()
            fetch.side_effect=RuntimeError('synthetic outage')
            result=client.get('/api/players/7/history')
            self.assertEqual(result.status_code,503)
            self.assertIn('temporarily unavailable',result.json['error'])
            self.assertNotIn('synthetic',result.json['error'])
