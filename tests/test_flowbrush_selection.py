"""Quota and demand selection regressions; fake sites/downloaders only."""
import json
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, PropertyMock, patch
from test_flowbrush_diagnostics import PLUGIN


class SelectionTest(TestCase):
    def setUp(self):
        self.plugin = PLUGIN.FlowBrush()
        self.plugin._brush_config = PLUGIN.BrushConfig({
            'site_download_cap': 2, 'min_peers': 2, 'demand_priority': True,
            'site_cycle_cap': 1, 'seeder': '2-500', 'pubtime': '0-2880'})
        self.downloader = Mock()
        p = patch.object(PLUGIN.FlowBrush, 'downloader', new_callable=PropertyMock,
                         return_value=self.downloader)
        p.start(); self.addCleanup(p.stop)
        p = patch.object(PLUGIN.FlowBrush, '_FlowBrush__get_hash', side_effect=lambda t: t.get('hash') if isinstance(t, dict) else t.hashString)
        p.start(); self.addCleanup(p.stop)
        self.torrent = SimpleNamespace(site_name='A', title='sample', description='',
            page_url=None, downloadvolumefactor=0, hit_and_run=False, size=2**30,
            peers=2, seeders=3, pubdate=PLUGIN.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

    def count(self, site='A', tasks=None):
        return self.plugin._FlowBrush__get_site_downloading_count(site, tasks or {})

    def test_live_quota_counts_stalled_and_pending_not_history(self):
        self.downloader.get_downloading_torrents.return_value = [
            {'hash': 'one', 'tags': '刷流, 站点/A', 'state': 'stalledDL'},
            {'hash': 'two', 'tags': '刷流, 站点/AB', 'state': 'queuedDL'},
            {'hash': 'three', 'tags': '刷流', 'state': 'downloading'},
        ]
        tasks = {'three': {'site_name': 'A'}, 'old': {'site_name': 'A'}}
        self.assertEqual(self.count(tasks=tasks), 2)
        self.assertEqual(self.count('AB', tasks), 1)

    def test_unknown_owner_reserves_capacity_and_transmission_labels_work(self):
        self.downloader.get_downloading_torrents.return_value = [
            SimpleNamespace(hashString='tr', labels=['刷流', '站点/A']),
            {'hash': 'unknown', 'tags': '刷流'},
        ]
        self.assertEqual(self.count(), 2)
        self.assertEqual(self.count('B'), 1)

    def test_read_failure_is_not_zero(self):
        self.downloader.get_downloading_torrents.return_value = None
        self.assertIsNone(self.count())
        self.downloader.get_downloading_torrents.side_effect = RuntimeError('private')
        self.assertIsNone(self.count())

    def test_demand_filter_and_unknown_date(self):
        check = self.plugin._FlowBrush__evaluate_conditions_for_brush
        self.assertTrue(check(self.torrent, {})[0])
        for peers in [None, 0, 1, -1, True, float('nan'), float('inf'), 'bad']:
            with self.subTest(peers=peers):
                self.torrent.peers = peers
                self.assertFalse(check(self.torrent, {})[0])
        self.torrent.peers = 2
        for date in [None, '', 'bad']:
            self.torrent.pubdate = date
            self.assertFalse(check(self.torrent, {})[0])

    def test_demand_priority_prefers_real_demand_over_latest_date(self):
        quiet = SimpleNamespace(peers=0, seeders=2, pubdate='2026-11-27')
        busy = SimpleNamespace(peers=10, seeders=5, pubdate='2026-11-26')
        crowded = SimpleNamespace(peers=10, seeders=500, pubdate='2026-11-27')
        key = self.plugin._FlowBrush__demand_sort_key
        self.assertEqual(sorted([quiet, crowded, busy], key=key, reverse=True), [busy, crowded, quiet])

    def test_site_override_and_save_keep_new_fields(self):
        self.plugin._brush_config = PLUGIN.BrushConfig({
            'site_download_cap': 2, 'min_peers': 2, 'demand_priority': True,
            'enable_site_config': True, 'site_config': json.dumps([
                {'sitename': 'A', 'site_download_cap': 1, 'min_peers': 3, 'demand_priority': False}])})
        a = self.plugin._brush_config.get_site_config('A')
        self.assertEqual((a.site_download_cap, a.min_peers, a.demand_priority), (1, 3, False))
        self.plugin.update_config = Mock()
        self.plugin._FlowBrush__update_config()
        saved = self.plugin.update_config.call_args.args[0]
        self.assertEqual((saved['site_download_cap'], saved['min_peers'], saved['demand_priority']), (2, 2, True))

    def test_full_site_skips_feed_but_keeps_other_sites_eligible(self):
        PLUGIN.SiteOper.return_value.get.return_value = SimpleNamespace(id=1, name='A', domain='example')
        self.downloader.get_downloading_torrents.return_value = [{'hash': str(i), 'tags': '刷流,站点/A'} for i in range(2)]
        with patch.object(PLUGIN, 'TorrentsChain') as chain:
            self.assertTrue(self.plugin._FlowBrush__brush_site_torrents(1, {}, {}, set()))
            chain.assert_not_called()
        self.downloader.get_downloading_torrents.return_value = None
        with patch.object(PLUGIN, 'TorrentsChain') as chain:
            self.assertFalse(self.plugin._FlowBrush__brush_site_torrents(1, {}, {}, set()))
            chain.assert_not_called()

    def test_same_round_successes_cannot_exceed_remaining_site_quota(self):
        self.plugin._brush_config.site_cycle_cap = None
        self.plugin._brush_config.except_subscribe = False
        PLUGIN.SiteOper.return_value.get.return_value = SimpleNamespace(id=1, name='A', domain='example')
        self.downloader.get_downloading_torrents.return_value = [{'hash': 'old', 'tags': '刷流,站点/A'}]
        ts = [SimpleNamespace(**(vars(self.torrent) | {'title': str(i), 'site': 1})) for i in range(3)]
        PLUGIN.TorrentsChain.return_value.browse.return_value = ts
        self.plugin.eventmanager = Mock()
        p = patch.object(PLUGIN.FlowBrush, 'service_info', new_callable=PropertyMock, return_value=SimpleNamespace(name='test'))
        p.start(); self.addCleanup(p.stop)
        with patch.object(self.plugin, '_FlowBrush__calculate_seeding_torrents_size', return_value=0), \
             patch.object(self.plugin, '_FlowBrush__evaluate_pre_conditions_for_brush', return_value=(True, None)), \
             patch.object(self.plugin, '_FlowBrush__evaluate_size_condition_for_brush', return_value=(True, None)), \
             patch.object(self.plugin, '_FlowBrush__evaluate_conditions_for_brush', return_value=(True, None)), \
             patch.object(self.plugin, '_FlowBrush__download', return_value='new') as download, \
             patch.object(self.plugin, '_FlowBrush__send_add_message'):
            # TorrentInfo has additional fields used when storing newly admitted tasks.
            for t in ts:
                for k in ['pubdate', 'page_url', 'date_elapsed', 'freedate', 'uploadvolumefactor', 'downloadvolumefactor', 'hit_and_run', 'volume_factor', 'freedate_diff']:
                    if not hasattr(t, k): setattr(t, k, None)
            self.assertTrue(self.plugin._FlowBrush__brush_site_torrents(1, {}, {'count': 0}, set()))
            download.assert_called_once()
