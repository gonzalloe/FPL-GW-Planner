import copy
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

with patch.dict(os.environ, {'SUPABASE_URL':'', 'SUPABASE_KEY':''}):
    import auth


class FakeDatabase:
    def __init__(self):
        self.rows = {'users': {}, 'sessions': {}}
        self.fail_read = self.fail_write = False
        self.lock = threading.Lock()

    def table(self, name):
        return Query(self, name)


class Query:
    def __init__(self, database, table):
        self.db, self.table_name = database, table
        self.filters = []
        self.mode, self.payload, self.page = 'select', None, None

    def select(self, columns):
        return self

    def order(self, key):
        return self

    def range(self, start, end):
        self.page = (start, end)
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def is_(self, key, value):
        assert value == "null"
        return self.eq(key, None)

    def filter(self, key, operator, value):
        assert operator == 'eq'
        return self.eq(key, json.loads(value))

    def insert(self, row):
        self.mode, self.payload = 'insert', row
        return self

    def update(self, row):
        self.mode, self.payload = 'update', row
        return self

    def delete(self):
        self.mode = 'delete'
        return self

    def execute(self):
        with self.db.lock:
            key = 'email' if self.table_name == 'users' else 'token'
            records = self.db.rows[self.table_name]
            if self.mode == 'select' and self.db.fail_read:
                raise RuntimeError('synthetic database outage')
            if self.mode != 'select' and self.db.fail_write:
                raise RuntimeError('synthetic write failure')
            if self.mode == 'insert':
                identity = self.payload[key]
                if identity in records:
                    raise RuntimeError('duplicate primary key')
                records[identity] = copy.deepcopy(self.payload['data'])
                return SimpleNamespace(data=[self.payload])
            selected = [{key: identity, 'data': copy.deepcopy(value)}
                        for identity, value in sorted(records.items())]
            def field_value(row, field):
                if field.startswith('data->>'):
                    value = row['data'].get(field[7:])
                    return str(value) if value is not None else None
                return row[field]
            selected = [row for row in selected if all(field_value(row, field) == value for field, value in self.filters)]
            if self.page:
                selected = selected[self.page[0]:self.page[1]+1]
            if self.mode == 'update':
                for row in selected:
                    records[row[key]] = copy.deepcopy(self.payload['data'])
            elif self.mode == 'delete':
                for row in selected:
                    del records[row[key]]
            return SimpleNamespace(data=selected)


class AccountStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.db = FakeDatabase()
        self.db.rows['users'] = {email: {'email':email, 'team_id':i, 'plan':'free'}
                                 for i, email in enumerate(['a@example.test','b@example.test'],1)}
        for item in (patch.object(auth, '_get_supabase', return_value=self.db),
                     patch.object(auth, 'USERS_FILE', self.directory/'users.json'),
                     patch.object(auth, 'SESSIONS_FILE', self.directory/'sessions.json')):
            item.start();self.addCleanup(item.stop)

    def test_read_failure_cannot_delete_or_replace_existing_accounts(self):
        initial = copy.deepcopy(self.db.rows)
        self.db.fail_read = True
        with self.assertRaises(auth.AuthStorageError):
            auth.register('new@example.test','synthetic-password')
        self.assertEqual(self.db.rows,initial)
        self.assertFalse(auth.USERS_FILE.exists())

    def test_failed_database_write_is_not_acknowledged_in_ephemeral_file(self):
        rows = auth._load_users('a@example.test')
        rows['a@example.test']['team_id'] = 99
        self.db.fail_write = True
        with self.assertRaises(auth.AuthStorageError):auth._save_users(rows)
        self.assertFalse(auth.USERS_FILE.exists())
        self.assertEqual(self.db.rows['users']['a@example.test']['team_id'],1)

    def test_independent_updates_do_not_overwrite_other_users(self):
        first, second = auth._load_users(), auth._load_users()
        first['a@example.test']['team_id'] = 10
        second['b@example.test']['team_id'] = 20
        auth._save_users(first);auth._save_users(second)
        self.assertEqual([self.db.rows['users'][email]['team_id'] for email in sorted(self.db.rows['users'])],[10,20])

    def test_conflicting_updates_fail_instead_of_overwriting(self):
        first, second = auth._load_users('a@example.test'), auth._load_users('a@example.test')
        first['a@example.test']['team_id'] = 10
        second['a@example.test']['team_id'] = 20
        auth._save_users(first)
        with self.assertRaises(auth.ConcurrentAccountUpdate):auth._save_users(second)
        self.assertEqual(self.db.rows['users']['a@example.test']['team_id'],10)

    def test_account_created_since_snapshot_is_not_deleted(self):
        old = auth._load_users()
        self.db.rows['users']['new@example.test'] = {'plan':'free'}
        old['a@example.test']['team_id'] = 9
        auth._save_users(old)
        self.assertIn('new@example.test',self.db.rows['users'])
        with self.assertRaises(auth.AuthStorageError):auth._save_users({})

    def test_pagination_does_not_drop_accounts_or_sessions(self):
        self.db.rows['users'] = {str(i):{'email':str(i)} for i in range(1200)}
        self.db.rows['sessions'] = {str(i):{'email':str(i)} for i in range(1200)}
        self.assertEqual(len(auth._load_users()),1200)
        self.assertEqual(len(auth._load_sessions()),1200)

    def test_register_login_and_password_reset_preserve_other_accounts(self):
        with patch.object(auth,'REQUIRE_EMAIL_VERIFICATION',False):
            registered = auth.register('new@example.test','synthetic-password')
        self.assertTrue(registered['ok'])
        self.assertEqual(auth.get_user_from_token(registered['token'])['email'],'new@example.test')
        self.assertTrue(auth.login('new@example.test','synthetic-password')['ok'])
        rows = auth._load_users('new@example.test')
        rows['new@example.test'].update(reset_token='synthetic-reset',reset_token_expires=auth.time.time()+60)
        auth._save_users(rows)
        reset = auth.reset_password_with_token('synthetic-reset','replacement-password')
        self.assertTrue(reset['ok'])
        self.assertIsNone(auth.get_user_from_token(registered['token']))
        self.assertTrue(auth.login('new@example.test','replacement-password')['ok'])
        self.assertIn('a@example.test',self.db.rows['users'])

    def test_quota_reservation_is_atomic_and_rolls_over_each_day(self):
        results = []
        threads = [threading.Thread(target=lambda:results.append(auth.reserve_chat_message('a@example.test')['allowed'])) for _ in range(10)]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        self.assertEqual(sum(results),3)
        self.db.rows['users']['a@example.test']['chat_date'] = '2000-01-01'
        self.assertTrue(auth.reserve_chat_message('a@example.test')['allowed'])

    def test_file_backend_merges_only_changed_records_and_fails_on_corruption(self):
        auth.USERS_FILE.write_text(json.dumps(self.db.rows['users']))
        with patch.object(auth,'_get_supabase',return_value=None):
            first,second=auth._load_users(),auth._load_users()
            first['a@example.test']['team_id']=7
            second['b@example.test']['team_id']=8
            auth._save_users(first);auth._save_users(second)
            self.assertEqual(auth._load_users()['a@example.test']['team_id'],7)
            self.assertEqual(auth._load_users()['b@example.test']['team_id'],8)
            auth.USERS_FILE.write_text('{bad')
            with self.assertRaises(auth.AuthStorageError):auth._load_users()

    def test_stripe_expiry_is_absolute_replay_safe_and_atomic(self):
        end = int((datetime.now(timezone.utc)+timedelta(days=30)).timestamp())
        sub = {'id':'sub_test','status':'active','current_period_end':end}
        auth.apply_stripe_subscription('a@example.test','evt_1',100,sub,'cus_test')
        expiry=self.db.rows['users']['a@example.test']['plan_expires']
        self.assertTrue(auth.apply_stripe_subscription('a@example.test','evt_1',100,sub,'cus_test')['duplicate'])
        auth.apply_stripe_subscription('a@example.test','evt_initial_invoice',101,sub,'cus_test')
        self.assertEqual(self.db.rows['users']['a@example.test']['plan_expires'],expiry)
        sub['current_period_end']=end+30*86400
        auth.apply_stripe_subscription('a@example.test','evt_renew',200,sub,'cus_test')
        updated=self.db.rows['users']['a@example.test']['plan_expires']
        self.assertNotEqual(updated,expiry)
        self.assertTrue(auth.apply_stripe_subscription('a@example.test','evt_old',99,{'id':'sub_test','status':'canceled'},'cus_test')['ignored'])
        self.assertEqual(self.db.rows['users']['a@example.test']['plan'],'premium')
        self.db.fail_write=True
        with self.assertRaises(auth.AuthStorageError):auth.apply_stripe_subscription('a@example.test','evt_fail',201,sub,'cus_test')
        self.assertNotIn('evt_fail',self.db.rows['users']['a@example.test']['stripe_processed_events'])

    def test_stripe_new_shape_cancellation_and_old_subscription_events(self):
        sub={'id':'sub_current','status':'active','items':{'data':[{'current_period_end':2000000000}]}}
        auth.apply_stripe_subscription('a@example.test','evt_1',100,sub,'cus_test',True)
        result=auth.apply_stripe_subscription('a@example.test','evt_other',101,{'id':'sub_old','status':'canceled'},'cus_test')
        self.assertTrue(result['ignored'])
        auth.apply_stripe_subscription('a@example.test','evt_cancel',102,{'id':'sub_current','status':'canceled'},'cus_test')
        self.assertEqual(self.db.rows['users']['a@example.test']['plan'],'free')

    def test_invalid_email_cannot_enter_account_store(self):
        self.assertIn('error',auth.register("');alert(1);//@example.test",'synthetic-password'))

    def test_legacy_sessions_survive_upgrade_but_not_account_recreation(self):
        self.db.rows['sessions']['legacy-token']={'email':'a@example.test','created_at':auth.time.time()}
        self.assertEqual(auth.get_user_from_token('legacy-token')['email'],'a@example.test')
        rows=auth._load_users('a@example.test');del rows['a@example.test'];auth._save_users(rows)
        with patch.object(auth,'REQUIRE_EMAIL_VERIFICATION',False):
            self.assertTrue(auth.register('a@example.test','new-password')['ok'])
        self.assertIsNone(auth.get_user_from_token('legacy-token'))

    def test_expired_premium_session_is_downgraded_before_authorization(self):
        self.db.rows['users']['a@example.test'].update(plan='premium',
            plan_expires=(datetime.now()-timedelta(days=1)).isoformat())
        self.db.rows['sessions']['legacy-token']={'email':'a@example.test','created_at':auth.time.time()}
        self.assertEqual(auth.get_user_from_token('legacy-token')['plan'],'free')
        self.assertEqual(self.db.rows['users']['b@example.test']['team_id'],2)
