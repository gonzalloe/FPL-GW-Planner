import copy
import os
import unittest
from unittest.mock import Mock, patch

import gw_planner
from my_team import calculate_free_transfers, fetch_my_team as fetch_public_team
with patch.dict(os.environ, {"SUPABASE_URL": "", "SUPABASE_KEY": ""}):
    import server


def context():
    bootstrap = {'events': [{'id':8,'is_current':True,'is_next':False},
                            {'id':9,'is_current':False,'is_next':True}]}
    teams = {i: {'id':i,'name':str(i),'short_name':str(i)} for i in range(1,21)}
    bootstrap["teams"] = list(teams.values())
    positions = [1]*2 + [2]*5 + [3]*5 + [4]*3 + [1,2,3,4]
    players, predictions = {}, []
    for pid, pos in enumerate(positions, 1):
        team = (pid - 1) % 10 + 1
        players[pid] = {'id':pid,'team':team,'element_type':pos,'position_id':pos,
                        'now_cost':50,'web_name':str(pid),'form':3}
        predictions.append({'player_id':pid,'team_id':team,'team':str(team),'name':str(pid),
                            'position_id':pos,'position':str(pos),'price':5,
                            'predicted_points':3 if pid <= 15 else 5,'raw_xpts':3,
                            'starter_quality':{'tier':'nailed'}})
    fixtures = [{'event':gw,'team_h':i,'team_a':i+1,'team_h_difficulty':3,'team_a_difficulty':3}
                for gw in range(9,18) for i in range(1,21,2)]
    team = {'error':None,'team_id':101,'info':{'name':'Public team'},
            'picks':[{'element':i,'position':i} for i in range(1,16)],
            'chips':[{'name':'wildcard','event':6},{'name':'bboost','event':1},{'name':'3xc','event':3}],
            'free_transfers':1,'starting_free_transfers':1,'free_transfers_remaining':None,
            'gw_summary':{'bank':2.2,'value':100}}
    return bootstrap, teams, players, predictions, fixtures, team


class TeamIDPlannerTests(unittest.TestCase):
    def test_chips_preserve_bank_without_extra_transfer(self):
        for chip in ('wildcard','freehit'):
            history=[{'event':1},{'event':2,'event_transfers':0},{'event':3,'event_transfers':12}]
            self.assertEqual(calculate_free_transfers(history,[{'name':chip,'event':3}]),2)
        self.assertEqual(calculate_free_transfers([{'event':1},{'event':2,'event_transfers':10}],
                                                [{'name':'wildcard','event':2}]),1)

    def test_normal_rollover_and_hits(self):
        self.assertEqual(calculate_free_transfers([{'event':1},{'event':2,'event_transfers':0}]),2)
        self.assertEqual(calculate_free_transfers([{'event':1},{'event':2,'event_transfers':4}]),1)
        self.assertEqual(calculate_free_transfers([{'event':i,'event_transfers':0} for i in range(1,10)]),5)

    def test_public_import_does_not_deduct_early_transfers(self):
        def get(url, **kwargs):
            data = {'current_event':2}
            if url.endswith('bootstrap-static/'):
                data = {'events':[{'id':2,'finished':True}]}
            elif url.endswith('history/'):
                data = {'current':[{'event':1},{'event':2,'event_transfers':0}], 'chips':[]}
            elif url.endswith('transfers/'):
                data = [{'event':3,'element_in':i,'element_out':i+100} for i in range(4)]
            elif 'picks/' in url:
                data = {'picks':[{'element':i,'position':i} for i in range(1,16)],'entry_history':{}}
            response = Mock(ok=True,status_code=200)
            response.json.return_value = data
            return response
        with patch('my_team.requests.get',side_effect=get), patch('my_team.time.sleep'):
            data = fetch_public_team(101)
        self.assertEqual(data['free_transfers'],2)
        self.assertIsNone(data['free_transfers_remaining'])

    def setUp(self):
        self.bootstrap, self.teams, self.players, self.preds, self.fixtures, self.team = context()
        for manager in (
            patch.object(server,'_ensure_refresh_thread'), patch.object(server,'_auto_setup_accounts'),
            patch.object(server,'_get_auth_user',return_value={'email':'test@example.test','team_id':101}),
            patch('auth.update_team_id'),
            patch('my_team.fetch_my_team',side_effect=lambda *args: copy.deepcopy(self.team)),
            patch.object(server,'_cached_predictions',return_value=(self.preds,{'gameweek':9},'fresh')),
            patch.object(gw_planner,'fetch_bootstrap',return_value=self.bootstrap),
            patch.object(gw_planner,'fetch_fixtures',return_value=self.fixtures),
            patch.object(gw_planner,'build_player_map',return_value=self.players),
            patch.object(gw_planner,'build_team_map',return_value=self.teams),
        ):
            manager.start(); self.addCleanup(manager.stop)
        server.limiter.enabled=False
        self.client=server.app.test_client()

    def test_team_id_import_without_private_account(self):
        response=self.client.get('/api/my-team?id=101')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['free_transfers'],1)
        self.assertNotIn('fpl_connected',response.json)
        self.assertEqual(self.client.get('/api/fpl-connection').status_code,404)

    def test_real_five_week_planner_during_refresh_without_token(self):
        remote=Mock(ok=True); remote.json.return_value={'current_event':8}
        server._prediction_run_lock.acquire()
        try:
            with patch.object(server.requests,'get',return_value=remote):
                response=self.client.get('/api/gw-planner?id=101&horizon=5')
        finally:
            server._prediction_run_lock.release()
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(len(response.json['gw_plans']),5)
        self.assertEqual(response.json['gw_plans'][0]['free_transfers_available'],1)
        self.assertEqual(response.json['free_transfers_source'],'completed_history')

    def test_roll_from_one_free_transfer_and_no_double_deduction(self):
        planner = gw_planner.GWPlanner(horizon=2, baseline_predictions=self.preds, baseline_gw=9)
        planner.next_gw = 10
        planner.horizon = 2
        # With no possible replacements, the beam must hold in both GWs.
        for gw in (10, 11):
            planner._gw_predictions[gw] = self.preds[:15]
            planner._gw_pred_maps[gw] = {p['player_id']: p for p in self.preds[:15]}
            planner._gw_infos[gw] = {}
        self.team.update({'info': {'planning_gw': 10}, 'free_transfers': 1,
                          'picks_are_planning_gw': True, 'current_event_transfers': 1})
        result = planner.plan_from_team_id(101, team_data=self.team)
        self.assertEqual(result['gw_plans'][0]['gameweek'], 10)
        self.assertEqual(result['gw_plans'][0]['free_transfers_available'], 1)
        self.assertEqual(result['gw_plans'][0]['num_transfers'], 0)
        self.assertEqual(result['gw_plans'][1]['free_transfers_available'], 2)
        self.assertEqual(result['free_transfers_source'], 'completed_history')

    def test_explicit_and_confirmed_balances_are_not_subtracted(self):
        planner = gw_planner.GWPlanner(horizon=1, baseline_predictions=self.preds, baseline_gw=9)
        self.team.update({'info': {'planning_gw': 9}, 'current_event_transfers': 4,
                          'picks_are_planning_gw': True, 'free_transfers_remaining': 2})
        result = planner.plan_from_team_id(101, team_data=self.team)
        self.assertEqual(result['free_transfers'], 2)
        self.assertEqual(result['free_transfers_source'], 'confirmed_remaining')
        result = planner.plan_from_team_id(101, team_data=self.team, free_transfers=1)
        self.assertEqual(result['free_transfers'], 1)
        self.assertEqual(result['free_transfers_source'], 'explicit_override')

    def test_planner_uses_history_for_target_gameweek(self):
        planner = gw_planner.GWPlanner(horizon=1, baseline_predictions=self.preds, baseline_gw=9)
        self.team.update({'info': {'planning_gw': 8}, 'free_transfers': 2,
                          'history': [{'event': i, 'event_transfers': 1} for i in range(1, 9)],
                          'chips': []})
        result = planner.plan_from_team_id(101, team_data=self.team)
        self.assertEqual(result['free_transfers'], 1)
        self.assertEqual(result['free_transfers_source'], 'target_history')
        self.team['history'].pop()
        result = planner.plan_from_team_id(101, team_data=self.team)
        self.assertIn('Free transfers for GW9 are unavailable', result['error'])

    def test_api_reads_imported_ft_automatically(self):
        remote = Mock(ok=True); remote.json.return_value = {'current_event': 8}
        with patch.object(server.requests, 'get', return_value=remote):
            response = self.client.get('/api/gw-planner?id=101&horizon=1&free_transfers=0')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['free_transfers'], self.team['free_transfers'])
        self.assertEqual(response.json['free_transfers_source'], 'completed_history')

    def test_chip_recommendations_are_advisory(self):
        planner=gw_planner.GWPlanner(horizon=2,baseline_predictions=self.preds,baseline_gw=9)
        with patch.object(planner,'_recommend_chip',return_value={'use_chip':True,'chip_code':'WC','score':90}):
            result=planner.plan_transfers(list(range(1,16)),bank=2.2,free_transfers=2,chips_available=['WC'],max_transfers_per_gw=0)
        self.assertFalse(result['gw_plans'][0]['chip_recommendation']['use_chip'])
        self.assertEqual(result['gw_plans'][1]['free_transfers_available'],3)
        self.assertEqual(result['summary']['chips_used'],[])


if __name__=='__main__': unittest.main()
