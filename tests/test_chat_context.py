import copy
import os
import unittest
from unittest.mock import Mock, patch

with patch.dict(os.environ, {'SUPABASE_URL': '', 'SUPABASE_KEY': ''}):
    import server


def sample():
    players = [{'player_id': i, 'name': f'Player {i}', 'team': 'ABC', 'position': 'MID',
                'price': 5.0, 'predicted_points': i / 10, 'selected_by_percent': 1,
                'availability': {'status': 'available', 'chance': 100},
                'fixtures': [{'opponent': 'XYZ', 'venue': 'H', 'fdr': 2}]}
               for i in range(1, 141)]
    team = {'picks': [{'element': i, 'position': i, 'is_captain': i == 1,
                        'selling_price': 45 if i == 1 else None} for i in range(1, 16)],
            'gw_summary': {'bank': 1.5}, 'free_transfers': 2,
            'free_transfers_remaining': None, 'chips': []}
    return players, team


class ChatContextTests(unittest.TestCase):
    def setUp(self):
        self.players, self.team = sample()
        for p in (patch.object(server, '_ensure_refresh_thread'),
                  patch.object(server, '_auto_setup_accounts'),
                  patch.object(server, '_cached_predictions', return_value=(self.players, {'gameweek': 9}, 'ready')),
                  patch('my_team.fetch_my_team', side_effect=lambda team_id: copy.deepcopy(self.team))):
            p.start(); self.addCleanup(p.stop)
        server.limiter.enabled = False
        self.client = server.app.test_client()

    def test_owned_players_outside_top_120_are_in_context(self):
        context = server.build_fpl_context('my team', 123)
        self.assertIn('Player 1|ABC|MID|market £5.0m|sell £4.5m|0.1 xPts|starter, captain|ownership 1%', context)
        self.assertIn('Player 15|ABC|MID|market £5.0m|sell unknown', context)
        self.assertIn('bank £m: 1.5', context)
        self.assertIn('free transfers (entering gameweek, early transfers may not be deducted): 2', context)
        self.assertIn('fixtures XYZ H FDR 2', context)

    def test_no_team_or_failed_import_is_explicit(self):
        self.assertIn('No team imported', server.build_fpl_context('question'))
        with patch('my_team.fetch_my_team', return_value={'error':'not found'}):
            self.assertIn('unavailable or incomplete', server.build_fpl_context('question', 99))

    def test_dify_context_refresh_and_stable_identity(self):
        responses = []
        def post(url, **kwargs):
            responses.append(kwargs['json'])
            response = Mock(status_code=200)
            response.json.return_value = {'answer': 'ok', 'conversation_id': 'conversation'}
            return response
        with patch.object(server, 'DIFY_API_KEY', 'test'), patch.object(server, '_get_auth_user', return_value={'email':'user@example.test', 'team_id':123}), patch.object(server.requests, 'post', side_effect=post):
            first = self.client.post('/api/chat', json={'question':'my squad', 'team_id':999})
            self.team['gw_summary']['bank'] = 2.0
            second = self.client.post('/api/chat', json={'question':'and now?', 'conversation_id':'conversation'})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(responses[0]['user'], responses[1]['user'])
        self.assertEqual(responses[1]['conversation_id'], 'conversation')
        self.assertIn('Team ID: 123', responses[0]['inputs']['fpl_data'])
        self.assertIn('bank £m: 2.0', responses[1]['query'])
        self.assertNotIn('user@example.test', responses[0]['user'])

    def test_guest_identity_is_stable_and_team_is_public_id(self):
        with patch.object(server, '_get_auth_user', return_value=None), patch.object(server, 'ask_dify', return_value={'answer':'ok'}) as ask:
            for _ in range(2):
                self.client.post('/api/chat', json={'question':'hi', 'team_id':123, 'chat_user_id':'a'*32})
        self.assertEqual(ask.call_args_list[0].args[2], 123)
        self.assertEqual(ask.call_args_list[0].args[3], ask.call_args_list[1].args[3])


if __name__ == '__main__': unittest.main()
