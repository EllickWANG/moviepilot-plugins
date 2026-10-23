"""统一计划、跨模块观察、清理门槛及数据接口的行为回归。"""
import copy
import importlib.util
import json
import time
import tempfile
import types
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import Mock, patch

from test_sitetoolbox_rss_history import PLUGIN

spec = importlib.util.spec_from_file_location('sitetoolbox_monitor_logic', Path(PLUGIN.__file__).with_name('monitor.py'))
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


class ScheduleTest(unittest.TestCase):
    def plan(self, **config):
        return monitor.normalize_plan(config)

    def test_interval_is_anchored_to_window_start(self):
        plan = self.plan(monitor_start='08:15', monitor_end='13:00', monitor_interval=120)
        slots = monitor.daily_slots(plan, date(2026, 10, 23))
        self.assertEqual([slot.strftime('%H:%M') for slot in slots], ['08:15', '10:15', '12:15'])
        self.assertTrue(monitor.is_due(plan, datetime(2026, 10, 23, 10, 15, 59)))
        self.assertFalse(monitor.is_due(plan, datetime(2026, 10, 23, 10, 0)))

    def test_overnight_window_keeps_previous_day_anchor(self):
        plan = self.plan(monitor_start='22:00', monitor_end='06:00', monitor_interval=120)
        self.assertTrue(monitor.is_due(plan, datetime(2026, 10, 24, 0, 0)))
        self.assertTrue(monitor.is_due(plan, datetime(2026, 10, 24, 6, 0)))
        self.assertFalse(monitor.is_due(plan, datetime(2026, 10, 24, 8, 0)))
        self.assertEqual(monitor.next_run(plan, datetime(2026, 10, 24, 5, 0)), datetime(2026, 10, 24, 6))

    def test_fixed_times_are_deduplicated_and_restricted_to_window(self):
        plan = self.plan(monitor_start='22:00', monitor_end='06:00', monitor_mode='fixed', monitor_times='22:30, 01:15, 12:00,22:30')
        self.assertEqual(monitor.daily_slots(plan, date(2026, 10, 23)), [datetime(2026, 10, 23, 22, 30), datetime(2026, 10, 24, 1, 15)])
        self.assertEqual(monitor.next_run(plan, datetime(2026, 10, 24, 1, 15)), datetime(2026, 10, 24, 22, 30))

    def test_full_day_does_not_repeat_boundary(self):
        slots = monitor.daily_slots(self.plan(monitor_start='08:00', monitor_end='08:00', monitor_interval=60), date(2026, 10, 23))
        self.assertEqual(len(slots), 24)
        self.assertEqual(slots[-1], datetime(2026, 10, 24, 7))

    def test_disabled_and_empty_modules_have_no_schedule(self):
        for plan in [self.plan(monitor_enabled=False), self.plan(monitor_modules=[])]:
            self.assertIsNone(monitor.next_run(plan, datetime(2026, 10, 23)))
            self.assertFalse(monitor.is_due(plan, datetime(2026, 10, 23, 8)))

    def test_legacy_threshold_migrates_to_one_global_value(self):
        plan = self.plan(rss_failure_days=7, monitor_failure_days=2)
        self.assertEqual(plan['monitor_failure_days'], 2)
        self.assertEqual(self.plan(rss_failure_days=7)['monitor_failure_days'], 7)


class PluginMonitorTest(unittest.TestCase):
    def setUp(self):
        self.storage = {}
        self.plugin = PLUGIN.sitetoolbox(self.storage)
        with patch.object(PLUGIN.sitetoolbox, '_uninstall_error_capture'):
            self.plugin.init_plugin({'enabled': False, 'site_ids': [1], 'cleanup_downloader_names': ['qb'], 'monitor_failure_days': 3})
        PLUGIN.SiteOper.return_value.list_order_by_pri.return_value = []

    def record(self, module, day, state='error', **extra):
        return self.plugin._record_monitor_results(module, [{'site_id': 1, 'state': state, 'message': '检测失败', 'tested_at': f'2026-10-{day:02} 08:00:00', **extra}])[0]

    def test_shared_threshold_keeps_module_and_object_history_independent(self):
        self.record('rss', 21)
        self.record('rss', 22)
        self.record('userdata', 22)
        self.assertTrue(self.record('rss', 23)['confirmed'])
        self.assertFalse(self.record('userdata', 23)['confirmed'])
        self.assertEqual(self.record('missing', 23, object_id='torrent:qb:hash')['failure_days'], 1)
        self.assertEqual(self.record('mteam', 23, 'warning')['state'], 'pending')
        self.assertEqual(self.record('rss', 23, site_id=2)['failure_days'], 1)

    def test_success_and_calendar_gap_reset_every_module(self):
        for module in monitor.MODULES:
            self.record(module, 21)
            self.record(module, 22)
            self.assertEqual(self.record(module, 22, 'success')['failure_days'], 0)
            self.assertEqual(self.record(module, 23)['failure_days'], 1)
            self.assertEqual(self.record(module, 25)['failure_days'], 1)

    def test_missing_query_failure_does_not_report_recovery(self):
        preview = {'downloaders': ['qb'], 'items': [{'downloader': 'qb', 'hash': 'abc'}], 'errors': []}
        self.plugin._save_missing_preview(copy.deepcopy(preview))
        self.plugin._save_missing_preview({'downloaders': ['qb'], 'items': [], 'errors': [{'downloader': 'qb', 'message': 'timeout'}]})
        self.assertEqual(self.storage['monitor_failure_history']['missing:torrent:qb:abc']['days'], 1)
        self.plugin._save_missing_preview({'downloaders': ['qb'], 'items': [], 'errors': []})
        self.assertEqual(self.storage['monitor_failure_history']['missing:torrent:qb:abc']['days'], 0)

    def test_cleanup_uses_current_threshold_and_rejects_pending_items(self):
        items = [{'hash': 'confirmed', 'downloader': 'qb', 'monitor': {'state': 'error', 'failure_days': 3}}, {'hash': 'pending', 'downloader': 'qb', 'monitor': {'state': 'pending', 'raw_state': 'error', 'failure_days': 1}}]
        self.plugin._latest_missing_preview = {'items': items}
        self.assertEqual([item['hash'] for item in self.plugin._confirmed_missing_items({'items': items})], ['confirmed'])
        self.plugin._rss_failure_days = 5
        response = PLUGIN._api_cleanup_missing_torrents({})
        self.assertFalse(response.success)
        self.assertIn('没有达到', response.message)

    def test_subset_save_preserves_other_sites(self):
        self.plugin._save_userdata_results([{'site_id': 2, 'state': 'error', 'message': 'stale'}])
        self.plugin._save_userdata_results([{'site_id': 1, 'state': 'success'}])
        self.assertEqual({item['site_id'] for item in self.plugin._latest_userdata_results}, {1, 2})
        self.plugin._save_mteam_login_check({'items': [{'site_id': 2, 'state': 'warning'}]})
        self.plugin._save_mteam_login_check({'items': [{'site_id': 1, 'state': 'success'}]}, site_ids=[1])
        self.assertEqual(self.plugin._latest_mteam_login_check['total_count'], 2)
        self.assertEqual(self.plugin._latest_mteam_login_check['pending_count'], 1)

    def test_registers_only_one_schedule_and_deduplicates_slot(self):
        self.plugin._enabled = True
        self.assertEqual([service['id'] for service in self.plugin.get_service()], ['sitetoolbox_monitor'])
        with patch.object(PLUGIN, 'datetime') as clock, patch.object(self.plugin, 'run_monitor') as run:
            clock.now.return_value = datetime(2026, 10, 23, 8)
            self.plugin.run_scheduled_monitor()
            run.assert_called_once_with(notify=True, slot='2026-10-23 08:00')
            run.reset_mock()
            self.plugin.save_data('monitor_last_slot', '2026-10-23 08:00')
            self.plugin.run_scheduled_monitor()
            run.assert_not_called()

    def test_failed_module_is_reported_and_next_module_still_runs(self):
        with patch.object(PLUGIN, '_test_site_rss', side_effect=RuntimeError('RSS worker failed')), patch.object(PLUGIN, '_check_userdata_health', return_value=[{'site_id': 1, 'state': 'success'}]):
            self.plugin.run_monitor(modules=['rss', 'userdata'])
        run = self.storage['monitor_runs'][0]
        self.assertEqual((run['status'], [step['state'] for step in run['steps']]), ('error', ['error', 'success']))
        self.assertEqual(self.plugin._latest_job_states['monitor']['status'], 'error')
        self.assertEqual(run['confirmed'], 0)

    def test_module_success_clears_execution_failure_history(self):
        self.record('rss', 23, object_id='module')
        with patch.object(PLUGIN, '_test_site_rss', return_value={'site_id': 1, 'state': 'success'}):
            self.plugin.run_monitor(modules=['rss'])
        self.assertEqual(self.storage['monitor_failure_history']['rss:module']['days'], 0)

    def test_no_targets_does_not_scan_unselected_sites(self):
        self.plugin._site_ids = []
        with patch.object(PLUGIN, '_check_userdata_health') as check:
            self.plugin.run_monitor(modules=['userdata'])
            check.assert_not_called()
        self.assertEqual(self.storage['monitor_runs'][0]['steps'][0]['state'], 'skipped')

    def test_manual_monitor_reserves_lock_before_thread_starts(self):
        with patch.object(PLUGIN, 'Thread') as thread, patch.object(PLUGIN, '_check_userdata_health', return_value=[{'site_id': 1, 'state': 'success'}]):
            response = PLUGIN._api_monitor_run({'modules': ['userdata']})
            self.assertTrue(response.success)
            self.assertTrue(self.plugin._monitor_run_lock.locked())
            self.assertFalse(PLUGIN._api_monitor_run({'modules': ['userdata']}).success)
            thread.call_args.kwargs['target']()
            self.assertFalse(self.plugin._monitor_run_lock.locked())
            self.assertEqual(self.plugin._latest_job_states['monitor']['status'], 'success')

    def test_monitor_api_rejects_empty_module_selection(self):
        self.assertFalse(PLUGIN._api_monitor_run({'modules': []}).success)
        self.assertFalse(PLUGIN._api_monitor_run({'modules': ['not-a-module']}).success)

    def test_overview_preserves_unknown_data_and_removes_url_secrets(self):
        site = types.SimpleNamespace(id=1, name='站点', domain='example.test', url='https://user:secret@example.test/path?token=secret', is_active=True)
        self.plugin._latest_results = [{'site_id': 1, 'state': 'success', 'tested_at': '2026-10-23 08:00:00'}]
        with patch.object(PLUGIN.SiteOper.return_value, 'list_order_by_pri', return_value=[site]), patch.object(PLUGIN, '_is_mteam_site', return_value=False), patch.object(PLUGIN, '_site_has_rule', return_value=False):
            overview = PLUGIN._api_overview().data
        row = overview['sites'][0]
        self.assertEqual((row['state'], row['checks']['userdata']['state']), ('unknown', 'unknown'))
        self.assertEqual(row['url'], 'https://example.test/path')
        self.assertIn('timezone', overview['plan'])

    def test_rss_cookie_request_keeps_site_user_agent(self):
        site = types.SimpleNamespace(cookie='test_cookie', ua='SiteUA', proxy=False)
        PLUGIN._parse_rss(site, 'https://example.test/rss')
        args = PLUGIN.RssHelper.return_value.parse.call_args.kwargs
        self.assertEqual(args['headers'], {'Cookie': 'test_cookie', 'User-Agent': 'SiteUA'})

    def test_specialist_userdata_check_uses_shared_confirmation_and_scope(self):
        with patch.object(PLUGIN, '_check_userdata_health', return_value=[{'site_id': 1, 'state': 'error'}]) as check, patch.object(self.plugin, '_start_background_job', side_effect=lambda _key, _name, worker: worker()):
            message = PLUGIN._api_check_userdata({})
        check.assert_called_once_with(site_ids=[1])
        self.assertIn('确认异常 0/1，待观察 1', message)

    def test_error_api_groups_redacted_records_and_honors_sample_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'errors.jsonl'
            records = [{'ts': time.time(), 'time': '2026-10-23 08:00:00', 'level': 'ERROR', 'source': 'RSS', 'message': f'RSS timeout token={token}'} for token in ('one', 'two')]
            # 首行超出样本大小，后续完整行应正常读取并聚合。
            path.write_text('x' * (2 * 1024 * 1024) + '\n' + '\n'.join(json.dumps(record) for record in records))
            with patch.object(self.plugin, '_error_log_path', return_value=path):
                response = PLUGIN._api_errors(query='RSS', level='ERROR', page=1, limit=10).data
            self.assertTrue(response['sampled'])
            self.assertEqual((response['total'], response['items'][0]['count']), (1, 2))
            self.assertNotIn('one', response['items'][0]['message'])

    def test_scheduled_cleanup_waits_for_confirmation(self):
        self.plugin._cleanup_auto_cleanup = True
        preview = {'downloaders': ['qb'], 'items': [{'downloader': 'qb', 'hash': 'abc'}], 'errors': []}
        with patch.object(PLUGIN, '_build_missing_torrent_preview', side_effect=lambda *_: copy.deepcopy(preview)), patch.object(PLUGIN, '_cleanup_missing_torrents_from_preview') as cleanup:
            self.plugin.run_monitor(modules=['missing'], notify=True)
            cleanup.assert_not_called()
        self.plugin._rss_failure_days = 1
        with patch.object(PLUGIN, '_build_missing_torrent_preview', side_effect=lambda *_: copy.deepcopy(preview)), patch.object(PLUGIN, '_cleanup_missing_torrents_from_preview', return_value={}) as cleanup:
            self.plugin.run_monitor(modules=['missing'], notify=True)
            self.assertEqual(cleanup.call_args.args[0][0]['hash'], 'abc')

    def test_error_display_hides_headers_and_url_credentials(self):
        samples = ['Authorization: Bearer abcdef12345', 'Cookie: uid=alice; pass=secret; other=value', "{'Authorization': 'Bearer secret', 'Cookie': 'sid=secret; id=alice'}", 'password="two words" token=12345', 'https://example.test/rss?passkey=secret', 'https://alice:secret@example.test/rss']
        for value in samples:
            result = PLUGIN._redact_error_text(value)
            for secret in ['abcdef12345', 'alice', 'secret', 'two words', '12345']:
                self.assertNotIn(secret, result)

class UserdataRefreshTest(unittest.TestCase):
    def setUp(self):
        self.site = types.SimpleNamespace(id=26, name='红豆饭', domain='hdfans.org', is_active=True)
        self.old = types.SimpleNamespace(domain='hdfans.org', username='test', user_level='', upload=1000, download=500,
                                         ratio=2, bonus=1, updated_day='2026-10-23', updated_time='08:48:32')
        self.fresh = copy.copy(self.old)
        self.fresh.user_level = 'Veteran User'
        self.fresh.updated_time = '14:47:03'

    def check(self, refresh, latest=None, sites=None, ids=None):
        with patch.object(PLUGIN.SiteOper.return_value, 'list_order_by_pri', return_value=sites or [self.site]), \
             patch.object(PLUGIN.SiteOper.return_value, 'get_userdata_latest', side_effect=latest or (lambda: [self.old])), \
             patch.object(PLUGIN, '_refresh_site_userdata', side_effect=refresh), \
             patch.object(PLUGIN, '_site_statistics', return_value={}), \
             patch.object(PLUGIN, '_site_has_rule', return_value=False):
            return PLUGIN._check_userdata_health(ids if ids is not None else [26])

    def test_retest_collects_before_reading_snapshot_and_recovers_missing_level(self):
        collected = []
        def refresh(site):
            collected.append(site.id)
        def latest():
            self.assertEqual(collected, [26])
            return [self.fresh]
        result = self.check(refresh, latest=latest)[0]
        self.assertEqual((result['state'], result['user_level'], result['updated_at']),
                         ('success', 'Veteran User', '2026-10-23 14:47:03'))
        self.assertTrue(result['refreshed'])

    def test_failed_collection_cannot_pass_using_complete_old_data(self):
        self.old.user_level = 'Veteran User'
        result = self.check(lambda _: (_ for _ in ()).throw(TimeoutError('请求超时')))[0]
        self.assertEqual(result['state'], 'error')
        self.assertFalse(result['refreshed'])
        self.assertIn('本次用户数据采集失败', result['message'])
        self.assertIn('08:48:32', result['message'])

    def test_single_site_retest_does_not_refresh_other_sites(self):
        other = types.SimpleNamespace(id=1, name='其他站', domain='other.test', is_active=True)
        refreshed = []
        self.check(lambda site: refreshed.append(site.id), sites=[other, self.site])
        self.assertEqual(refreshed, [26])

    def test_collector_failure_uses_shared_observation_instead_of_resetting_history(self):
        result = self.check(lambda _: (_ for _ in ()).throw(RuntimeError('采集失败')))[0]
        result['tested_at'] = '2026-10-23 15:00:00'
        recorded, history = monitor.observe({'userdata:26': {'date': '2026-10-22', 'days': 1}}, 'userdata:26', result, 3)
        self.assertEqual((recorded['state'], recorded['failure_days'], recorded['confirmed']), ('pending', 2, False))

    def test_collector_empty_and_error_payloads_are_not_success(self):
        import sys
        chain_module = types.ModuleType('app.chain.site')
        chain_module.SiteChain = Mock()
        with patch.dict(sys.modules, {'app.chain.site': chain_module}), patch.object(PLUGIN.SitesHelper.return_value, 'get_indexer', return_value={'domain': 'hdfans.org'}):
            for payload in (None, {}, {'err_msg': 'Cookie 失效'}, types.SimpleNamespace(err_msg='请求失败')):
                chain_module.SiteChain.return_value.refresh_userdata.return_value = payload
                with self.assertRaises(ValueError):
                    PLUGIN._refresh_site_userdata(self.site)
            chain_module.SiteChain.return_value.refresh_userdata.return_value = {'user_level': 'Veteran User'}
            PLUGIN._refresh_site_userdata(self.site)

    def test_one_collection_failure_does_not_stop_other_selected_sites(self):
        other = types.SimpleNamespace(id=27, name='其他站', domain='other.test', is_active=True)
        other_data = copy.copy(self.fresh)
        other_data.domain = 'other.test'
        calls = []
        def refresh(site):
            calls.append(site.id)
            if site.id == 26:
                raise RuntimeError()
        results = self.check(refresh, latest=lambda: [self.old, other_data], sites=[self.site, other], ids=[26, 27])
        self.assertEqual(calls, [26, 27])
        self.assertEqual([row['state'] for row in results], ['error', 'success'])
        self.assertIn('RuntimeError', results[0]['message'])


if __name__ == '__main__':
    unittest.main()
