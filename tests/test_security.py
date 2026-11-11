import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

with patch.dict(os.environ, {'SUPABASE_URL':'', 'SUPABASE_KEY':''}):
    import server
from auth import AuthStorageError
from test_team_id_planner import context


class SecurityRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'output').mkdir();(self.root/'data').mkdir()
        (self.root/'dashboard.html').write_text('<html>Public dashboard</html>')
        (self.root/'.env').write_text('SYNTHETIC_SECRET=private')
        (self.root/'data'/'users.json').write_text('{"synthetic":"private"}')
        (self.root/'output'/'latest_predictions.json').write_text('{"predicted_points":99}')
        self.user=None
        for item in (patch.object(server,'_ensure_refresh_thread'),patch.object(server,'_auto_setup_accounts'),
                     patch.object(server,'BASE_DIR',self.root),patch.object(server,'OUTPUT_DIR',self.root/'output'),
                     patch.object(server,'_get_auth_user',side_effect=lambda:self.user)):
            item.start();self.addCleanup(item.stop)
        server.limiter.enabled=False
        server._response_cache.clear();server._GUEST_CHAT_COUNTS.clear()
        self.client=server.app.test_client()

    def test_only_dashboard_is_public_and_path_variants_cannot_read_repository(self):
        for path in ['/', '/dashboard.html']:
            self.assertEqual(self.client.get(path).status_code,200)
        for path in ['/.env','/x/../.env/','/x/../data/users.json/', '/x/../server.py/',
                     '/x/%2e%2e/data/users.json/', '/x/../output/latest_predictions.json/',
                     '/requirements.txt','/user_settings.json']:
            response=self.client.get(path)
            self.assertEqual(response.status_code,404,path)
            self.assertNotIn(b'private',response.data)

    def test_exports_are_admin_only(self):
        for user in [None,{'plan':'free'},{'plan':'premium'}]:
            self.user=user
            self.assertEqual(self.client.get('/api/files').status_code,403)
            self.assertEqual(self.client.get('/output/latest_predictions.json').status_code,403)
        self.user={'plan':'admin'}
        self.assertEqual(self.client.get('/api/files').status_code,200)
        self.assertEqual(self.client.get('/output/latest_predictions.json').json['predicted_points'],99)
        self.assertEqual(self.client.get('/output/users.json').status_code,404)

    def test_paid_routes_reject_anonymous_and_free_before_expensive_work(self):
        routes=[('/api/transfers','get'),('/api/chip-analysis','get'),('/api/gw-planner','get'),
                ('/api/search-players','get'),('/api/squad-predictions','get'),
                ('/api/simulate-transfer','post'),('/api/season-chips','get')]
        with patch.object(server,'_cached_predictions',side_effect=AssertionError('Protected work ran')):
            for user,status in [(None,401),({'plan':'free','email':'free@example.test'},403)]:
                self.user=user
                for route,method in routes:
                    self.assertEqual(getattr(self.client,method)(route).status_code,status,route)

    def test_public_prediction_serialization_covers_nested_data_and_preserves_cache(self):
        player={'player_id':1,'name':'One','predicted_points':8.5,'raw_xpts':8.5,'neutral_xpts':7.5,
                'starter_quality':{'tier':'nailed','p_start':.99},'factors':{'x':99},
                'fixtures':[{'opponent':'ABC','win_probability':.9,'fixture_xg':2,'xp_single':8.5}]}
        data={'gameweek':11,'predictions':[player],'squad':{'starting_xi':[player],'captain':player,
              'predicted_total_points':90},'bb_squad':{'starting_xi':[player],'predicted_total_points':99},
              'top_picks':[player],'chip_analysis':{'best_chip':{'code':'TC','score':99}}}
        original=copy.deepcopy(data)
        with patch.object(server,'_cached_predictions',return_value=([player],data,'ready')):
            for self.user in [None,{'plan':'free'}]:
                result=self.client.get('/api/predictions').json
                self.assertEqual(result['predictions'][0]['predicted_points'],'🔒')
                self.assertNotIn('neutral_xpts',result['predictions'][0])
                self.assertNotIn('fixture_xg',result['predictions'][0]['fixtures'][0])
                self.assertEqual(result['bb_squad']['predicted_total_points'],'🔒')
                self.assertEqual(result['bb_squad']['starting_xi'][0]['starter_quality']['p_start'],'🔒')
                self.assertEqual(result['top_picks'],[])
            self.user={'plan':'premium'}
            self.assertEqual(self.client.get('/api/predictions').json['bb_squad']['predicted_total_points'],99)
        self.assertEqual(data,original)

    def test_public_team_import_still_works_without_exposing_predictions(self):
        _,_,_,preds,_,team=context()
        with patch('my_team.fetch_my_team',return_value=team),patch.object(server,'_cached_predictions',return_value=(preds,{},'ready')):
            result=self.client.get('/api/my-team?id=101')
            self.assertEqual(result.status_code,200)
            self.assertEqual(len(result.json['starters']),11)
            self.assertEqual(result.json['predicted_points'],'🔒')
            self.assertEqual(result.json['transfer_suggestions'],[])
            self.assertEqual(result.json['starters'][0]['predicted_points'],'🔒')
            self.assertEqual(result.json['info']['name'],'Public team')

    def test_chat_daily_limit_and_provider_capacity(self):
        with patch.object(server,'ask_dify',return_value={'answer':'ok'}) as ask:
            for _ in range(3):self.assertEqual(self.client.post('/api/chat',json={'question':'hi'}).status_code,200)
            self.assertEqual(self.client.post('/api/chat',json={'question':'hi','chat_user_id':'b'*32}).status_code,429)
            self.assertEqual(ask.call_count,3)
            self.user={'plan':'free','email':'free@example.test'}
            with patch('auth.reserve_chat_message',return_value={'allowed':False}):
                self.assertEqual(self.client.post('/api/chat',json={'question':'hi'}).status_code,429)
            self.user={'plan':'premium'}
            server._CHAT_PROVIDER_SLOTS.acquire();server._CHAT_PROVIDER_SLOTS.acquire()
            try:self.assertEqual(self.client.post('/api/chat',json={'question':'hi'}).status_code,429)
            finally:server._CHAT_PROVIDER_SLOTS.release();server._CHAT_PROVIDER_SLOTS.release()
            self.assertEqual(ask.call_count,3)
            self.assertEqual(self.client.post('/api/chat',json={'question':'hi'}).status_code,200)

    def test_storage_outage_returns_retryable_error_not_success(self):
        with patch('auth.register',side_effect=AuthStorageError('synthetic')):
            self.assertEqual(self.client.post('/api/auth/register',json={}).status_code,503)
        self.user={'plan':'free','email':'free@example.test'}
        with patch('auth.reserve_chat_message',side_effect=AuthStorageError('synthetic')),patch.object(server,'ask_dify') as ask:
            self.assertEqual(self.client.post('/api/chat',json={'question':'hi'}).status_code,503)
            ask.assert_not_called()

    def test_authenticated_cache_is_scoped_to_user(self):
        @server.require_premium
        @server.cached_response(ttl_seconds=60,key_prefix='test-scope')
        def endpoint():return server.jsonify({'email':server.request._plan_user['email']})
        for email in ['a@example.test','b@example.test','a@example.test']:
            self.user={'plan':'premium','email':email}
            with server.app.test_request_context('/test-scope'):
                self.assertEqual(endpoint().json['email'],email)

    def test_stripe_signature_failure_and_transient_failure_have_correct_status(self):
        with patch.dict(os.environ,{'STRIPE_WEBHOOK_SECRET':'synthetic'}),patch('stripe.Webhook.construct_event',side_effect=ValueError('bad')):
            self.assertEqual(self.client.post('/api/stripe/webhook',data='{}').status_code,400)
        event={'id':'evt_1','created':100,'type':'checkout.session.completed',
               'data':{'object':{'subscription':'sub_1','customer':'cus_1','client_reference_id':'paid@example.test'}}}
        sub={'id':'sub_1','customer':'cus_1','status':'active','current_period_end':2000000000}
        with patch.dict(os.environ,{'STRIPE_WEBHOOK_SECRET':'synthetic'}),patch('stripe.Webhook.construct_event',return_value=event),patch('stripe.Subscription.retrieve',return_value=sub),patch.object(server,'_find_email_by_stripe_customer',return_value=''),patch('auth.apply_stripe_subscription',return_value={'ok':True}) as save:
            self.assertEqual(self.client.post('/api/stripe/webhook',data='{}').status_code,200)
            self.assertEqual(save.call_args.args[:3],('paid@example.test','evt_1',100))
            save.side_effect=AuthStorageError('synthetic')
            self.assertEqual(self.client.post('/api/stripe/webhook',data='{}').status_code,503)

    def test_auth_responses_are_not_reused_across_tokens_or_gzip_variants(self):
        player={'player_id':1,'name':'One','predicted_points':8.5}
        data={'gameweek':11,'predictions':[player],'squad':{},'padding':'x'*3000}
        with patch.object(server,'_cached_predictions',return_value=([player],data,'ready')):
            response=self.client.get('/api/predictions',headers={'Accept-Encoding':'gzip'})
        self.assertIn('no-store',response.headers['Cache-Control'])
        self.assertIn('Authorization',response.vary)
        self.assertIn('Accept-Encoding',response.vary)

    def test_premium_and_admin_search_squad_and_simulator_still_work(self):
        _,_,_,predictions,_,_=context()
        with patch.object(server,'_cached_predictions',return_value=(predictions,{'gameweek':9},'ready')):
            for plan in ['premium','admin']:
                self.user={'plan':plan,'email':plan+'@example.test'}
                result=self.client.get('/api/search-players')
                self.assertEqual(result.status_code,200)
                self.assertIsInstance(result.json['players'][0]['predicted_points'],(int,float))
                self.assertEqual(self.client.get('/api/squad-predictions?gw=9&ids=1,3').status_code,200)
                result=self.client.post('/api/simulate-transfer',json={
                    'squad_ids':list(range(1,16)),'out_id':3,'in_id':17,'gw':9})
                self.assertEqual(result.status_code,200)
                self.assertIsInstance(result.json['impact']['xi_gain'],(int,float))
