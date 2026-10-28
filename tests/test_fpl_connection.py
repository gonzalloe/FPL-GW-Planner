"""Run: python -m unittest discover -s tests -p 'test_fpl_connection.py' -v"""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
import fpl_connection as fpl


def snapshot(limit=2, made=1):
    return {
        "picks": [{"element": i, "position": i, "purchase_price": 50,
                   "selling_price": 51, "is_captain": i == 1} for i in range(1, 16)],
        "chips": [],
        "transfers": {"limit": limit, "made": made, "status": "free", "cost": 0,
                      "bank": 22, "value": 1020},
    }


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for context in (
            patch.dict(os.environ, {"FPL_CONNECTION_KEY": Fernet.generate_key().decode()}, clear=False),
            patch.object(fpl, "CONNECTION_DIR", Path(self.temp.name)),
            patch.object(fpl, "_db", return_value=None),
        ):
            context.start()
            self.addCleanup(context.stop)
        self.alice = {"email": "alice@example.test", "created_at": "2026-01-01"}
        self.bob = {"email": "bob@example.test", "created_at": "2026-01-01"}

    def connect(self, user, team_id=101, token="fake-token"):
        with patch.object(fpl, "_get", side_effect=[{"player": {"entry": team_id}}, snapshot()]):
            return fpl.connect(user, token)

    def test_encrypts_and_isolates_connections(self):
        result = self.connect(self.alice, 101, "alice-secret-token")
        self.connect(self.bob, 202, "bob-secret-token")
        self.assertNotIn("token", json.dumps(result))
        stored = "".join(p.read_text() for p in Path(self.temp.name).glob("*.json"))
        self.assertNotIn("alice-secret-token", stored)
        self.assertNotIn("bob-secret-token", stored)
        self.assertEqual(fpl.status(self.alice)["team_id"], 101)
        self.assertEqual(fpl.status(self.bob)["team_id"], 202)
        with patch.object(fpl, "_snapshot", return_value=snapshot()) as request:
            self.assertIsNone(fpl.fetch_snapshot(self.bob, 101))
            request.assert_not_called()
            fpl.fetch_snapshot(self.alice, 101)
            request.assert_called_once_with(101, "alice-secret-token")
        fpl.disconnect(self.alice)
        self.assertFalse(fpl.status(self.alice)["connected"])
        self.assertTrue(fpl.status(self.bob)["connected"])

    def test_recreated_app_account_cannot_reuse_old_connection(self):
        self.connect(self.alice)
        recreated = {**self.alice, "created_at": "2026-02-01"}
        self.assertFalse(fpl.status(recreated)["connected"])

    def test_ciphertext_cannot_be_rebound_to_another_user(self):
        self.connect(self.alice)
        record = fpl._read(fpl._owner_key(self.alice))
        fpl._write(fpl._owner_key(self.bob), record)
        with self.assertRaises(fpl.FPLConnectionError) as error:
            fpl.status(self.bob)
        self.assertEqual(error.exception.code, "fpl_reconnect_required")

    def test_rejected_credentials_are_not_saved(self):
        with patch.object(fpl, "_get", side_effect=fpl.FPLConnectionError("Rejected", status=409)):
            with self.assertRaises(fpl.FPLConnectionError):
                fpl.connect(self.alice, "fake-token")
        self.assertFalse(fpl.status(self.alice)["connected"])

    def test_expiry_never_returns_old_balance(self):
        self.connect(self.alice)
        with patch.object(fpl.requests, "get", return_value=Mock(status_code=401)):
            with self.assertRaises(fpl.FPLConnectionError) as error:
                fpl.fetch_snapshot(self.alice, 101)
        self.assertEqual(error.exception.code, "fpl_reconnect_required")
        self.assertNotIn("fake-token", str(error.exception))

    def test_requests_only_fixed_fpl_urls_without_redirects(self):
        response = Mock(status_code=200)
        response.json.return_value = {"player": {"entry": 101}}
        with patch.object(fpl.requests, "get", return_value=response) as get:
            fpl._get("/me/", "fake-token")
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://fantasy.premierleague.com/api/me/")
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["headers"]["X-Api-Authorization"], "Bearer fake-token")

    def test_invalid_tokens_rejected_before_network(self):
        for token in (None, "", "fake\r\nInjected: value", "a" * 16385):
            with self.subTest(token_type=type(token).__name__), patch.object(fpl, "_get") as get:
                with self.assertRaises(fpl.FPLConnectionError):
                    fpl.connect(self.alice, token)
                get.assert_not_called()

    def test_allowance_display_does_not_change_when_api_made_increases(self):
        for made in (0, 1, 2, 4):
            data = {"picks": [], "gw_summary": {"points": 46},
                    "starting_free_transfers": 5, "transfers": [{"event": 9}] * 100}
            fpl.apply_snapshot(data, snapshot(limit=2, made=made))
            self.assertEqual(data["free_transfers"], 2)
            self.assertEqual(data["free_transfers_remaining"], max(0, 2 - made))
            self.assertEqual(data["gw_summary"]["bank"], 2.2)
            self.assertEqual(data["gw_summary"]["points"], 46)
            self.assertEqual(data["picks"][0]["selling_price"], 51)

    def test_unlimited_and_invalid_transfer_status(self):
        self.assertTrue(fpl.transfer_status(snapshot(limit=None))["unlimited"])
        for limit in (-1, 6, True, "2"):
            with self.subTest(limit=limit), self.assertRaises(fpl.FPLConnectionError):
                fpl.transfer_status(snapshot(limit=limit))
        with self.assertRaises(fpl.FPLConnectionError):
            fpl.transfer_status({"transfers": {"made": 1}})

    def test_storage_failure_does_not_fall_back_to_local_credentials(self):
        with patch.object(fpl, "_db", side_effect=RuntimeError("private backend failure")):
            with self.assertRaises(fpl.FPLConnectionError) as error:
                fpl.status(self.alice)
        self.assertNotIn("private backend failure", str(error.exception))
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
