import copy
import os
import unittest
from unittest.mock import ANY, Mock, patch

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
        self.assertIn('free transfers (entering gameweek; target or live remaining unconfirmed): 2', context)
        self.assertIn('fixtures XYZ H FDR 2', context)

    def test_current_gw_transfers_not_deducted_twice(self):
        self.team.update({'info': {'planning_gw': 9}, 'picks_are_planning_gw': True,
                          'current_event_transfers': 1, 'free_transfers': 1})
        context = server.build_fpl_context('transfer', 123)
        self.assertIn('free transfers (entering gameweek; live remaining unconfirmed): 1', context)
        self.assertIn('one transfer costs 0 points; two transfers cost 4 points', context)

    def test_confirmed_remaining_and_chip_sensitive_rollover(self):
        self.team.update({'info': {'planning_gw': 9}, 'free_transfers': 2,
                          'free_transfers_remaining': 0, 'current_event_transfers': 2})
        context = server.build_fpl_context('transfer', 123)
        self.assertIn('free transfers (confirmed remaining): 0', context)
        self.assertIn('one transfer costs 4 points; two transfers cost 8 points', context)
        team = {'info': {'planning_gw': 8}, 'history': [{'event': 1},
                 {'event': 2, 'event_transfers': 0}, {'event': 3, 'event_transfers': 10}],
                'chips': [{'event': 3, 'name': 'wildcard'}]}
        self.assertEqual(server._chat_free_transfers(team, 4)[0], 2)

    def test_next_prediction_gw_uses_history_not_current_gw_balance(self):
        self.team.update({'info': {'planning_gw': 8}, 'free_transfers': 2,
                          'history': [{'event': 1}, {'event': 2, 'event_transfers': 0},
                                      {'event': 3, 'event_transfers': 2},
                                      {'event': 4, 'event_transfers': 1},
                                      {'event': 5, 'event_transfers': 1},
                                      {'event': 6, 'event_transfers': 1},
                                      {'event': 7, 'event_transfers': 1},
                                      {'event': 8, 'event_transfers': 1}]})
        context = server.build_fpl_context('transfer', 123)
        self.assertIn('free transfers (entering gameweek from public history; live remaining unconfirmed): 1', context)
        self.assertIn('one transfer costs 0 points; two transfers cost 4 points', context)
        self.team['history'].pop()
        context = server.build_fpl_context('transfer', 123)
        self.assertIn('free transfers (unknown for prediction gameweek): unknown', context)

    def test_autosub_rules_and_legal_suggested_lineup(self):
        positions = [1]*2 + [2]*5 + [3]*5 + [4]*3
        for i, position in enumerate(positions, 1):
            self.players[i-1]['position_id'] = position
            self.players[i-1]['position'] = ('GK','DEF','MID','FWD')[position-1]
        self.players[14]['predicted_points'] = 0
        context = server.build_fpl_context('keep my injured forward', 123)
        xi = server._chat_suggested_lineup(self.team['picks'], {p['player_id']:p for p in self.players})
        self.assertEqual(len(xi), 11)
        self.assertGreaterEqual(sum(p['position_id'] == 2 for p in xi), 3)
        self.assertGreaterEqual(sum(p['position_id'] == 4 for p in xi), 1)
        self.assertNotIn(15, [p['player_id'] for p in xi])
        self.assertIn('MODEL-SUGGESTED LEGAL XI', context)
        self.assertIn('not guaranteed autosubs', context)
        self.assertIn('Do not assume an injured starter means a 10-player lineup', context)

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


class DifyRetryTests(unittest.TestCase):
    def setUp(self):
        for manager in (patch.object(server, 'DIFY_API_KEY', 'test-key'),
                        patch.object(server, 'build_fpl_context', return_value='current squad'),
                        patch.object(server.time, 'sleep')):
            manager.start(); self.addCleanup(manager.stop)

    def response(self, status, data):
        response = server.requests.Response()
        response.status_code = status
        response._content = server.json.dumps(data).encode()
        return response

    def busy(self):
        return self.response(400, {'code': 'completion_request_error',
            'message': "[models] Connection Error, 503 UNAVAILABLE. {'error': {'code': 503, 'message': 'This model is currently experiencing high demand.', 'status': 'UNAVAILABLE'}}"})

    def test_reported_wrapped_503_retries_first_question_and_preserves_context(self):
        payloads = []
        responses = [self.busy(), self.response(200, {'answer': 'Advice', 'conversation_id': 'new-chat'})]
        def post(*args, **kwargs):
            payloads.append(copy.deepcopy(kwargs['json']))
            return responses.pop(0)
        with patch.object(server.requests, 'post', side_effect=post) as post, patch.object(server, 'build_fpl_context', return_value='full current squad') as context:
            result = server.ask_dify('captain?', None, 123, 'same-user')
        self.assertEqual(result['answer'], 'Advice')
        self.assertEqual(result['conversation_id'], 'new-chat')
        self.assertEqual(post.call_count, 2)
        context.assert_called_once_with('captain?', 123, snapshot=ANY)
        self.assertEqual(payloads[0], payloads[1])
        self.assertEqual(payloads[1]['inputs']['fpl_data'], 'full current squad')
        self.assertEqual(payloads[1]['user'], 'same-user')

    def test_provider_remains_busy_attempts_are_bounded_and_message_is_friendly(self):
        with patch.object(server.requests, 'post', side_effect=[self.busy() for _ in range(3)]) as post:
            result = server.ask_dify('question', 'existing-chat', dify_user='user')
        self.assertEqual(post.call_count, 3)
        self.assertEqual(result['error_code'], 'model_busy')
        self.assertTrue(result['retryable'])
        self.assertEqual(result['conversation_id'], 'existing-chat')
        self.assertNotIn('400', result['answer'])
        self.assertNotIn('https://', result['answer'])
        self.assertEqual([call.args[0] for call in server.time.sleep.call_args_list], [1, 2])

    def test_validation_and_authentication_errors_are_not_retried(self):
        for status, body in [(400, {'code': 'invalid_param', 'message': 'missing fpl_data'}),
                             (400, {'code': 'completion_request_error', 'message': 'invalid model configuration'}),
                             (401, {'code': 'unauthorized'})]:
            with self.subTest(status=status, body=body), patch.object(server.requests, 'post', return_value=self.response(status, body)) as post:
                result = server.ask_dify('question', dify_user='user')
                self.assertEqual(post.call_count, 1)
                self.assertFalse(result['retryable'])

    def test_http_503_and_rate_limit_responses_can_recover(self):
        for status in (429, 503):
            busy = self.response(status, {'message': 'busy'})
            busy.headers['Retry-After'] = '2'
            with self.subTest(status=status), patch.object(server.requests, 'post', side_effect=[busy, self.response(200, {'answer': 'OK'})]) as post:
                result = server.ask_dify('question', dify_user='user')
                self.assertEqual(result['answer'], 'OK')
                self.assertEqual(post.call_count, 2)

    def test_expired_conversation_then_busy_recovers_with_same_identity(self):
        payloads = []
        responses = [self.response(404, {}), self.busy(), self.response(200, {'answer': 'OK', 'conversation_id': 'fresh'})]
        def post(*args, **kwargs):
            payloads.append(copy.deepcopy(kwargs['json']))
            return responses.pop(0)
        with patch.object(server.requests, 'post', side_effect=post):
            result = server.ask_dify('question', 'expired', dify_user='same-user')
        self.assertEqual(result['conversation_id'], 'fresh')
        self.assertEqual(payloads[0]['conversation_id'], 'expired')
        self.assertNotIn('conversation_id', payloads[1])
        self.assertEqual(payloads[1], payloads[2])

    def test_ambiguous_read_timeout_is_not_automatically_duplicated(self):
        with patch.object(server.requests, 'post', side_effect=server.requests.exceptions.ReadTimeout) as post:
            result = server.ask_dify('question', dify_user='user')
        self.assertEqual(post.call_count, 1)
        self.assertEqual(result['error_code'], 'timeout')

    def test_busy_retry_respects_shared_time_budget(self):
        clock = [0.0]
        def post(*args, **kwargs):
            clock[0] += 86
            return self.busy()
        with patch.object(server.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(server.requests, 'post', side_effect=post) as request:
            result = server.ask_dify('question', dify_user='user')
        self.assertEqual(request.call_count, 1)
        self.assertEqual(result['error_code'], 'model_busy')
        server.time.sleep.assert_not_called()


class LocalChatFallbackTests(unittest.TestCase):
    def setUp(self):
        self.players, self.team = sample()
        self.snapshot = {'predictions': self.players, 'metadata': {'gameweek': 10}, 'team': self.team,
                         'team_context': 'Team ID: 123; free transfers: 1\nIf 1 free transfer(s) remain: one transfer costs 0 points'}

    def test_captain_fallback_uses_owned_players(self):
        from chat_fallback import local_chat_response
        result = local_chat_response('Who should I captain?', self.snapshot)
        self.assertEqual(result['source'], 'local_data')
        self.assertIn('**Player 15**', result['answer'])
        self.assertNotIn('Player 140', result['answer'])
        self.assertIn('saved GW10', result['answer'])

    def test_no_prediction_data_is_explicit_without_service_error(self):
        from chat_fallback import local_chat_response
        result = local_chat_response('captain?', {})
        self.assertIn('projections are not available', result['answer'])
        self.assertNotIn('AI service', result['answer'])
        self.assertEqual(result['source'], 'local_data')

    def test_named_player_data_and_unknown_match_score_are_honest(self):
        from chat_fallback import local_chat_response
        result = local_chat_response('Player 10 score prediction', self.snapshot)
        self.assertIn('| Player 10 | ABC | MID | £5.0m | 1.0 |', result['answer'])
        self.assertIn('exact match-score forecast is not available', result['answer'])

    def test_provider_failure_delivers_data_answer_without_refetching(self):
        def build(question, team_id, snapshot):
            snapshot.update(self.snapshot)
            return 'current context'
        for failure in ('busy', 'timeout'):
            with self.subTest(failure=failure), patch.object(server, 'DIFY_API_KEY', 'key'), patch.object(server, 'build_fpl_context', side_effect=build) as context, patch.object(server.time, 'sleep'):
                if failure == 'busy':
                    response = server.requests.Response(); response.status_code = 503
                    response._content = b'{"message":"busy"}'
                    kwargs = {'return_value': response}
                else:
                    kwargs = {'side_effect': server.requests.exceptions.ReadTimeout}
                with patch.object(server.requests, 'post', **kwargs) as post:
                    result = server.ask_dify('Who should I captain?', 'chat', 123, 'user')
                self.assertEqual(result['source'], 'local_data')
                self.assertEqual(result['conversation_id'], 'chat')
                self.assertIn('**Player 15**', result['answer'])
                self.assertNotIn('AI service', result['answer'])
                self.assertEqual(context.call_count, 1)
                self.assertEqual(post.call_count, 3 if failure == 'busy' else 1)

    def test_snapshot_captures_predictions_and_imported_team_once(self):
        snapshot = {}
        with patch.object(server, '_cached_predictions', return_value=(self.players, {'gameweek': 10}, 'ready')), patch('my_team.fetch_my_team', return_value=copy.deepcopy(self.team)) as fetch:
            server.build_fpl_context('my team', 123, snapshot=snapshot)
        self.assertEqual(snapshot['team']['picks'], self.team['picks'])
        self.assertEqual(len(snapshot['predictions']), len(self.players))
        self.assertIn('CURRENT IMPORTED FPL SQUAD', snapshot['team_context'])
        fetch.assert_called_once_with(123)


class MatchProbabilityChatTests(unittest.TestCase):
    def setUp(self):
        self.players = [
            {'player_id': 1, 'name': 'Mbeumo', 'team': 'MUN', 'team_name': 'Manchester United',
             'position': 'MID', 'predicted_points': 4.51, 'price': 7.9,
             'team_season_wr': 0.11, 'starter_quality': {'p_start': 0.98},
             'fixtures': [{'opponent': 'AVL', 'venue': 'H', 'win_probability': 0.29, 'fdr': 3},
                          {'opponent': 'CHE', 'venue': 'A', 'win_probability': 0.2, 'fdr': 4}]},
            {'player_id': 2, 'name': 'Bruno', 'team': 'MUN', 'position': 'MID', 'price': 10,
             'predicted_points': 3, 'fixtures': [{'opponent': 'AVL', 'venue': 'H', 'win_probability': 0.29}]},
            {'player_id': 3, 'name': 'Villa Player', 'team': 'AVL', 'team_name': 'Aston Villa',
             'position': 'MID', 'price': 5, 'predicted_points': 3,
             'fixtures': [{'opponent': 'MUN', 'venue': 'A', 'win_probability': 0.43}]},
        ]
        self.snapshot = {'predictions': self.players, 'metadata': {'gameweek': 10}}

    def test_exact_question_uses_cached_fixture_values_without_model_call(self):
        question = 'check the probability of win between the match of Manchester united and aston villa'
        with patch.object(server, '_cached_predictions', return_value=(self.players, {'gameweek': 10}, 'ready')), patch.object(server.requests, 'post') as post:
            result = server.ask_dify(question, conversation_id='chat')
        post.assert_not_called()
        self.assertEqual(result['source'], 'fixture_model')
        self.assertEqual(result['conversation_id'], 'chat')
        self.assertIn('| MUN | AVL | H | 29.0% |', result['answer'])
        self.assertIn('| AVL | MUN | A | 43.0% |', result['answer'])
        self.assertNotIn('71.0%', result['answer'])
        self.assertNotIn('| MUN | CHE', result['answer'])
        self.assertEqual(result['answer'].count('| MUN | AVL'), 1)

    def test_single_team_question_keeps_double_gameweek_fixtures_separate(self):
        from chat_fallback import local_chat_response
        result = local_chat_response('Manchester United win probability?', self.snapshot)
        self.assertIn('| MUN | AVL | H | 29.0% |', result['answer'])
        self.assertIn('| MUN | CHE | A | 20.0% |', result['answer'])

    def test_context_contains_match_probabilities_even_outside_top_120(self):
        others = [{'player_id': i, 'name': str(i), 'predicted_points': 10} for i in range(4, 130)]
        with patch.object(server, '_cached_predictions', return_value=(others + self.players, {'gameweek': 10}, 'ready')):
            context = server.build_fpl_context('who wins MUN vs AVL?')
        block = context.split('PLAYERS (name|')[0]
        self.assertIn('MUN vs AVL (H): team win probability 29.0%', block)
        self.assertIn('AVL vs MUN (A): team win probability 43.0%', block)

    def test_missing_zero_and_invalid_probabilities_are_distinct(self):
        from chat_fallback import win_probability_percent, match_probability_response
        self.assertEqual(win_probability_percent(0), '0.0%')
        self.assertEqual(win_probability_percent(0.68), '68.0%')
        for value in (None, '🔒', float('nan'), 29, -0.1):
            self.assertEqual(win_probability_percent(value), 'unavailable')
        result = match_probability_response('Liverpool vs Arsenal win chance?', self.snapshot)
        self.assertIn('No matching fixture', result['answer'])


if __name__ == '__main__': unittest.main()
