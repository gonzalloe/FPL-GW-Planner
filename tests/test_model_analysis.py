import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import model_optimizer as optimizer
with patch.dict(os.environ, {'SUPABASE_URL': '', 'SUPABASE_KEY': ''}):
    import server


class ModelAnalysisMetricsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.events = [{'id': gw, 'finished': True, 'is_current': gw == 9,
                        'deadline_time': f'2026-10-{gw:02d}T10:00:00Z'} for gw in (8, 9)]
        self.bootstrap = {'events': self.events}
        self.settings = {}
        self.live = {'elements': [{'id': 1, 'stats': {'minutes': 90, 'total_points': 4}},
                                  {'id': 2, 'stats': {'minutes': 90, 'total_points': 2}}]}
        for p in (patch.object(optimizer, 'OUTPUT_DIR', self.directory),
                  patch.object(optimizer, 'fetch_bootstrap', return_value=self.bootstrap),
                  patch.object(optimizer, 'fetch_gameweek_live', return_value=self.live),
                  patch('app_storage.get_setting', side_effect=lambda key, default=None: self.settings.get(key, default)),
                  patch('app_storage.set_setting', side_effect=self.save)):
            p.start(); self.addCleanup(p.stop)

    def save(self, key, value):
        self.settings[key] = copy.deepcopy(value)
        return True

    def forecast(self, gw, after=False):
        return {'gameweek': gw, 'generated_at': f'2026-10-{gw:02d}T{(12 if after else 9):02d}:00:00+00:00',
                'predictions': [{'player_id': 1, 'position_id': 2, 'predicted_points': 3},
                                {'player_id': 2, 'position_id': 3, 'predicted_points': 5}]}

    def write(self, gw, after=False):
        (self.directory / f'gw{gw}_predictions.json').write_text(json.dumps(self.forecast(gw, after)))

    def test_latest_finished_current_gw_is_scored_with_historical_actuals(self):
        self.write(8); self.write(9)
        result = optimizer.analyze_recent_gameweeks(3)
        self.assertEqual(result['gameweeks_analyzed'], [9, 8])
        self.assertEqual(result['averages']['mae'], 2)
        self.assertEqual(optimizer.analyze_position_accuracy(8)['MID']['mae'], 3)

    def test_missing_and_post_deadline_forecasts_are_not_generated_or_scored(self):
        empty = optimizer.analyze_recent_gameweeks()
        self.assertIn('No eligible', empty['error'])
        self.write(9, after=True)
        with patch.object(optimizer, 'fetch_gameweek_live') as actuals:
            result = optimizer.analyze_recent_gameweeks()
        actuals.assert_not_called()
        self.assertIn('hindsight', result['skipped_gameweeks'][0]['reason'])
        self.assertNotIn('suggested_weights', result)

    def test_persisted_pre_deadline_snapshot_survives_missing_or_rebuilt_file(self):
        self.assertTrue(optimizer.archive_prediction_forecast(self.forecast(9), self.bootstrap))
        self.assertFalse(optimizer.archive_prediction_forecast(self.forecast(9, after=True), self.bootstrap))
        self.write(9, after=True)
        result = optimizer.analyze_recent_gameweeks()
        self.assertEqual(result['gameweeks_analyzed'], [9])
        self.assertEqual(result['averages']['mae'], 2)
        (self.directory / 'gw9_predictions.json').unlink()
        self.assertEqual(optimizer.analyze_recent_gameweeks()['gameweeks_analyzed'], [9])

    def test_unfinished_gw_is_not_scored(self):
        self.events[1]['finished'] = False
        self.write(9)
        with patch.object(optimizer, 'fetch_gameweek_live') as actuals:
            result = optimizer.analyze_recent_gameweeks()
        actuals.assert_not_called()
        self.assertIn('error', result)


class ModelAnalysisJobTests(unittest.TestCase):
    def setUp(self):
        for p in (patch.object(server, '_ensure_refresh_thread'), patch.object(server, '_auto_setup_accounts'),
                  patch.object(server, '_get_auth_user', return_value={'plan': 'admin'})):
            p.start(); self.addCleanup(p.stop)
        with server._MODEL_ANALYSIS_LOCK:
            server._MODEL_ANALYSIS_JOB.update(status='idle', job_id=None, error=None, result=None)
        server.limiter.enabled = False
        self.client = server.app.test_client()

    def test_slow_analysis_returns_immediately_and_duplicate_click_reuses_job(self):
        entered, release = threading.Event(), threading.Event()
        threads = []
        real_thread = threading.Thread
        def thread_factory(**kwargs):
            thread = real_thread(**kwargs); threads.append(thread); return thread
        def analyze():
            entered.set()
            if not release.wait(3): raise RuntimeError('Test did not release analysis')
            return {'averages': {'mae': 2}, 'suggested_weights': {}}
        with patch('model_optimizer.suggest_weight_adjustments', side_effect=analyze) as analysis, patch.object(server.threading, 'Thread', side_effect=thread_factory), patch.object(server, '_run_predictions') as engine:
            try:
                response = self.client.post('/api/admin/model-analysis')
                self.assertEqual(response.status_code, 202)
                self.assertTrue(entered.wait(1))
                self.assertEqual(self.client.get('/api/admin/model-analysis').json['status'], 'running')
                duplicate = self.client.post('/api/admin/model-analysis')
                self.assertEqual(duplicate.json['job_id'], response.json['job_id'])
                self.assertEqual(analysis.call_count, 1)
            finally:
                release.set()
                for thread in threads: thread.join(2)
            report = self.client.get('/api/admin/model-analysis').json
            self.assertEqual(report['status'], 'completed')
            self.assertEqual(report['result']['averages']['mae'], 2)
            engine.assert_not_called()

    def test_analysis_errors_and_exceptions_are_pollable_json(self):
        for outcome in ({'error': 'No forecasts', 'suggestion': 'Wait for saved forecasts'}, RuntimeError('test failure')):
            with server._MODEL_ANALYSIS_LOCK:
                server._MODEL_ANALYSIS_JOB.update(status='running', job_id='test', error=None, result=None)
            kwargs = {'side_effect': outcome} if isinstance(outcome, Exception) else {'return_value': outcome}
            with patch('model_optimizer.suggest_weight_adjustments', **kwargs), patch.object(server.app.logger, 'exception'):
                server._run_model_analysis_job('test')
            response = self.client.get('/api/admin/model-analysis')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['status'], 'failed')
            self.assertTrue(response.json['error'])

    def test_thread_start_failure_returns_json_and_does_not_leave_running_job(self):
        with patch.object(server.threading, 'Thread') as thread:
            thread.return_value.start.side_effect = RuntimeError('thread unavailable')
            with self.assertLogs(server.app.logger, level='ERROR'):
                response = self.client.post('/api/admin/model-analysis')
        self.assertEqual(response.status_code, 503)
        self.assertIn('error', response.json)
        self.assertEqual(self.client.get('/api/admin/model-analysis').json['status'], 'failed')

    def test_admin_required_for_start_and_poll(self):
        with patch.object(server, '_get_auth_user', return_value={'plan': 'free'}):
            self.assertEqual(self.client.get('/api/admin/model-analysis').status_code, 403)
            self.assertEqual(self.client.post('/api/admin/model-analysis').status_code, 403)

    def test_applying_weights_only_regenerates_upcoming_predictions(self):
        class InlineThread:
            def __init__(self, target, **kwargs): self.target = target
            def start(self): self.target()
        with patch('model_optimizer.apply_weight_adjustments', return_value=True), patch.object(server.threading, 'Thread', InlineThread), patch.object(server, '_run_predictions') as engine:
            response = self.client.post('/api/admin/apply-weights', json={'weights': {'form': 0.2}})
        self.assertTrue(response.json['ok'])
        engine.assert_called_once_with()


if __name__ == '__main__': unittest.main()
