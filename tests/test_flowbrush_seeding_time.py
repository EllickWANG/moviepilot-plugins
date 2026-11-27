"""删除计时回归：全部使用内存种子，不连接真实下载器。"""
import unittest
from unittest.mock import PropertyMock, patch
from test_flowbrush_diagnostics import PLUGIN


class SeedingTimeTest(unittest.TestCase):
    def setUp(self):
        self.plugin = PLUGIN.FlowBrush()
        self.plugin._brush_config = PLUGIN.BrushConfig({
            'seed_time': 168, 'hr_seed_time': 168, 'seed_ratio': None,
            'proxy_delete': False, 'del_no_free': False})
        self.torrent = dict(hash='test', name='test', added_on=1,
                            completion_on=100, seeding_time=600,
                            size=1000, total_size=1000, downloaded=1000,
                            amount_left=0, progress=1, uploaded=10**12, ratio=10000)
        patcher = patch.object(PLUGIN.FlowBrush, 'service_info', new_callable=PropertyMock)
        patcher.start()
        self.addCleanup(patcher.stop)
        PLUGIN.DownloaderHelper.return_value.is_downloader.return_value = True

    def info(self, **changes):
        return self.plugin._FlowBrush__get_torrent_info(self.torrent | changes)

    def should_delete(self, info, hr=False):
        return self.plugin._FlowBrush__evaluate_conditions_for_delete(
            '音乐乌托邦', info, {'hit_and_run': hr})[0]

    def test_old_completion_does_not_count_paused_or_offline_time(self):
        with patch.object(PLUGIN.time, 'time', return_value=10**9):
            info = self.info()
        self.assertEqual(info['seeding_time'], 600)
        self.assertFalse(self.should_delete(info))
        self.assertFalse(self.should_delete(info, hr=True))

    def test_missing_or_invalid_duration_never_falls_back_to_wall_clock(self):
        for value in [None, -1, '604800', float('inf'), float('nan'), True]:
            with self.subTest(value=value):
                self.assertEqual(self.info(seeding_time=value)['seeding_time'], 0)

    def test_partial_or_unknown_completion_cannot_trigger_time_deletion(self):
        for changes in [dict(progress=0.5), dict(amount_left=1), dict(size=500),
                        dict(size=None), dict(total_size=0), dict(progress=None),
                        dict(amount_left=None)]:
            with self.subTest(changes=changes):
                info = self.info(seeding_time=10**9, **changes)
                self.assertEqual(info['seeding_time'], 0)
                self.assertFalse(self.should_delete(info))
                self.assertFalse(self.should_delete(info, hr=True))

    def test_same_safe_threshold_for_legacy_unmarked_and_hr_tasks(self):
        for hr in [False, True]:
            self.assertFalse(self.should_delete(self.info(seeding_time=168*3600-1), hr))
            self.assertTrue(self.should_delete(self.info(seeding_time=168*3600), hr))


if __name__ == '__main__':
    unittest.main()
