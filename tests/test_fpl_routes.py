"""Authenticated Flask route checks with synthetic FPL responses only."""
import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
import fpl_connection as fpl
from test_fpl_connection import snapshot

# No deployment credentials or background refreshes are used by these tests.
with patch.dict(os.environ, {"SUPABASE_URL": "", "SUPABASE_KEY": ""}):
    import server


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.alice = {"email": "alice@example.test", "created_at": "2026-01-01", "team_id": 101}
        self.bob = {"email": "bob@example.test", "created_at": "2026-01-01", "team_id": 202}
        public = {"team_id": 101, "error": None, "picks": [], "chips": [],
                  "info": {"name": "Public team"}, "gw_summary": {"bank": 0, "value": 100},
                  "free_transfers": None, "free_transfers_remaining": None}
        predictions = [{"player_id": i, "price": 5, "predicted_points": 3,
                        "position_id": 3} for i in range(1, 16)]
        self.users = {"Bearer alice": self.alice, "Bearer bob": self.bob}
        for context in (
            patch.dict(os.environ, {"FPL_CONNECTION_KEY": Fernet.generate_key().decode()}),
            patch.object(fpl, "CONNECTION_DIR", Path(self.temp.name)),
            patch.object(fpl, "_db", return_value=None),
            patch.object(server, "_ensure_refresh_thread"),
            patch.object(server, "_auto_setup_accounts"),
            patch.object(server, "_get_auth_user", side_effect=lambda: self.users.get(server.request.headers.get("Authorization"))),
            patch('auth.update_team_id', return_value={"ok": True}),
            patch('my_team.fetch_my_team', side_effect=lambda team_id: copy.deepcopy({**public, "team_id": team_id})),
            patch.object(server, "_cached_predictions", return_value=(predictions, {"gameweek": 9}, "fresh")),
        ):
            context.start()
            self.addCleanup(context.stop)
        self.client = server.app.test_client()
        server.limiter.enabled = False

    def connect_alice(self):
        with patch.object(fpl, "_get", side_effect=[{"player": {"entry": 101}}, snapshot()]):
            return self.client.post('/api/fpl-connection', headers={"Authorization": "Bearer alice"},
                                    json={"access_token": "alice-fpl-token", "team_id": 999})

    def test_all_connection_methods_require_app_login(self):
        for method in ('get', 'post', 'delete'):
            response = getattr(self.client, method)('/api/fpl-connection')
            self.assertEqual(response.status_code, 401)
            self.assertIn('no-store', response.headers['Cache-Control'])

    def test_connect_discovers_team_and_status_never_returns_token(self):
        response = self.connect_alice()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['team_id'], 101)
        response = self.client.get('/api/fpl-connection', headers={"Authorization": "Bearer alice"})
        self.assertEqual(response.json['team_id'], 101)
        self.assertNotIn('alice-fpl-token', response.get_data(as_text=True))
        response = self.client.get('/api/fpl-connection', headers={"Authorization": "Bearer bob"})
        self.assertFalse(response.json['connected'])

    def test_user_team_uses_live_allowance_bank_and_squad_without_caching(self):
        self.connect_alice()
        with patch.object(fpl, '_get', return_value=snapshot()):
            response = self.client.get('/api/my-team?id=101', headers={"Authorization": "Bearer alice"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['free_transfers'], 2)
        self.assertEqual(response.json['free_transfers_remaining'], 1)
        self.assertEqual(response.json['gw_summary']['bank'], 2.2)
        self.assertEqual(len(response.json['starters']) + len(response.json['bench']), 15)
        self.assertIn('no-store', response.headers['Cache-Control'])

    def test_other_user_or_anonymous_request_cannot_obtain_live_balance(self):
        self.connect_alice()
        with patch.object(fpl, '_get') as get:
            response = self.client.get('/api/my-team?id=101', headers={"Authorization": "Bearer bob"})
            self.assertIsNone(response.json['free_transfers'])
            response = self.client.get('/api/my-team?id=101')
            self.assertIsNone(response.json['free_transfers'])
            get.assert_not_called()

    def test_expired_token_surfaces_reconnect_without_guessing(self):
        self.connect_alice()
        with patch.object(fpl, '_get', side_effect=fpl.FPLConnectionError('Reconnect FPL.', 'fpl_reconnect_required', 409)):
            response = self.client.get('/api/my-team?id=101', headers={"Authorization": "Bearer alice"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json['code'], 'fpl_reconnect_required')
        self.assertNotIn('free_transfers', response.json)

    def test_planner_uses_private_snapshot_even_during_prediction_refresh(self):
        self.connect_alice()
        fake_planner = Mock()
        fake_planner.plan_from_team_id.return_value = {"gw_plans": []}
        response = Mock(ok=True)
        response.json.return_value = {"current_event": 8}
        server._prediction_run_lock.acquire()
        try:
            with patch.object(fpl, '_get', return_value=snapshot()), patch('gw_planner.GWPlanner', return_value=fake_planner), patch.object(server.requests, 'get', return_value=response):
                result = self.client.get('/api/gw-planner?id=101&horizon=5&free_transfers=5', headers={"Authorization": "Bearer alice"})
        finally:
            server._prediction_run_lock.release()
        self.assertEqual(result.status_code, 200)
        kwargs = fake_planner.plan_from_team_id.call_args.kwargs
        self.assertIsNone(kwargs['free_transfers'])  # Client cannot override the live API balance.
        self.assertEqual(kwargs['team_data']['free_transfers_remaining'], 1)
        self.assertEqual(len(kwargs['team_data']['picks']), 15)

    def test_disconnect_removes_only_authenticated_users_connection(self):
        self.connect_alice()
        response = self.client.delete('/api/fpl-connection', headers={"Authorization": "Bearer bob"})
        self.assertEqual(response.status_code, 200)
        response = self.client.get('/api/fpl-connection', headers={"Authorization": "Bearer alice"})
        self.assertTrue(response.json['connected'])
        self.client.delete('/api/fpl-connection', headers={"Authorization": "Bearer alice"})
        response = self.client.get('/api/fpl-connection', headers={"Authorization": "Bearer alice"})
        self.assertFalse(response.json['connected'])

    def test_saved_team_setting_is_scoped_to_app_user(self):
        alice = self.client.get('/api/settings', headers={"Authorization": "Bearer alice"})
        bob = self.client.get('/api/settings', headers={"Authorization": "Bearer bob"})
        self.assertEqual(alice.json['team_id'], 101)
        self.assertEqual(bob.json['team_id'], 202)
        self.assertEqual(self.client.get('/api/settings').status_code, 401)


if __name__ == '__main__':
    unittest.main()
