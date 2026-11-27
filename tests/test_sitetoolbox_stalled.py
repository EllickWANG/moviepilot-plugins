"""长期未启动辅种的标签边界、观察历史和清理门槛。"""
import copy
import unittest
from unittest.mock import patch

from test_sitetoolbox_rss_history import PLUGIN

NOW = 1800000000


def candidate(**overrides):
    return {"hash": "candidate", "name": "content", "state": "pausedDL", "tags": "keep_iyuu, PT, 站点/观众",
            "category": "any", "progress": 0, "completed": 0, "downloaded": 0, "uploaded": 0, "seeding_time": 0,
            "added_on": NOW - 8 * 86400, "last_activity": NOW - 8 * 86400, "completion_on": -1,
            "save_path": "/data/fresh", "content_path": "/data/fresh/content", **overrides}


class CandidateTest(unittest.TestCase):
    def matches(self, row, peers=None, **policy):
        return PLUGIN.is_stalled_crossseed(row, [row, *(peers or [])], now=NOW, **policy)

    def test_requires_exact_configured_tag_never_category(self):
        self.assertTrue(self.matches(candidate()))
        self.assertTrue(self.matches(candidate(tags="PT, keep_iyuu , 站点/馒头")))
        for tags in ("", "keep_iyuu_old", "old_keep_iyuu", "KEEP_IYUU", "辅种"):
            with self.subTest(tags=tags):
                self.assertFalse(self.matches(candidate(tags=tags, category="keep_iyuu")))
        self.assertTrue(self.matches(candidate(tags="PT, 自定义辅种"), required_tag="自定义辅种"))
        self.assertFalse(self.matches(candidate(), required_tag="自定义辅种"))
        for tag in ("", " ", "keep_iyuu,PT", "keep_iyuu，PT"):
            self.assertFalse(self.matches(candidate(), required_tag=tag))

    def test_age_boundary_and_qb5_stopped_state(self):
        added = NOW - 7 * 86400
        self.assertTrue(self.matches(candidate(state="stoppedDL", added_on=added, last_activity=added)))
        self.assertFalse(self.matches(candidate(added_on=added + 1, last_activity=added + 1)))
        self.assertFalse(self.matches(candidate(added_on=0)))
        self.assertFalse(self.matches(candidate(added_on=NOW + 1)))
        self.assertFalse(self.matches(candidate(), age_days=9))

    def test_any_transfer_completion_or_activity_protects_task(self):
        for key in ("progress", "completed", "downloaded", "uploaded", "seeding_time"):
            with self.subTest(key=key):
                self.assertFalse(self.matches(candidate(**{key: 0.01})))
                self.assertFalse(self.matches(candidate(**{key: None})))
                self.assertFalse(self.matches(candidate(**{key: "NaN"})))
        self.assertFalse(self.matches(candidate(completion_on=NOW - 86400)))
        self.assertFalse(self.matches(candidate(last_activity=NOW - 86400)))
        for state in ("checkingDL", "checkingUP", "queuedDL", "stalledDL", "downloading", "pausedUP", "missingFiles"):
            self.assertFalse(self.matches(candidate(state=state)))

    def test_same_content_active_or_partial_peer_protects_candidate(self):
        row = candidate()
        for changes in ({"state": "stalledUP", "progress": 1}, {"state": "pausedDL", "completed": 1},
                        {"state": "downloading"}, {"state": "checkingDL"}):
            with self.subTest(changes=changes):
                self.assertFalse(self.matches(row, [candidate(hash="original", **changes)]))
        self.assertTrue(self.matches(row, [candidate(hash="other")]))
        self.assertTrue(self.matches(row, [candidate(hash="missing", state="missingFiles", completed=100)]))
        self.assertTrue(self.matches(row, [candidate(hash="unrelated", content_path="/other/content", state="stalledUP")]))


class StalledMonitorTest(unittest.TestCase):
    def setUp(self):
        self.storage = {}
        self.plugin = PLUGIN.sitetoolbox(self.storage)
        with patch.object(PLUGIN.sitetoolbox, "_uninstall_error_capture"):
            self.plugin.init_plugin({"enabled": False, "cleanup_downloader_names": ["qb"], "monitor_failure_days": 3})

    def record(self, day, items=True, failed=False):
        preview = {"downloaders": ["qb"], "items": [], "stalled_items": [{"downloader": "qb", "hash": "a"}] if items else [],
                   "errors": [{"downloader": "qb", "message": "timeout"}] if failed else []}
        with patch.object(PLUGIN, "_now", return_value=f"2026-11-{day:02} 10:00:00"):
            self.plugin._save_missing_preview(preview)
        return preview

    def test_stalled_history_is_independent_from_old_missing_confirmation(self):
        self.storage["monitor_failure_history"] = {"missing:torrent:qb:a": {"date": "2026-11-26", "days": 9}}
        first = self.record(27)
        self.assertEqual(first["stalled_items"][0]["monitor"]["failure_days"], 1)
        self.assertEqual(self.plugin._confirmed_stalled_items(first), [])
        self.record(27)
        self.record(28)
        confirmed = self.record(29)
        self.assertEqual(len(self.plugin._confirmed_stalled_items(confirmed)), 1)
        self.record(29, items=False, failed=True)
        self.assertEqual(self.storage["monitor_failure_history"]["missing:stalled:qb:a"]["days"], 3)
        self.record(29, items=False)
        self.assertEqual(self.storage["monitor_failure_history"]["missing:stalled:qb:a"]["days"], 0)

    def test_pending_api_cannot_bypass_days_or_delete_files(self):
        self.record(27)
        response = PLUGIN._api_cleanup_stalled_torrents({"delete_files": True, "confirmed": True})
        self.assertFalse(response.success)

    def test_manual_endpoint_forces_record_only_cleanup(self):
        for day in (27, 28, 29):
            self.record(day)
        with patch.object(self.plugin, "_start_background_job", side_effect=lambda key, name, fn: fn()), \
                patch.object(PLUGIN, "_cleanup_missing_torrents_from_preview", return_value={"deleted_count": 1, "skipped_count": 0, "failed_count": 0}) as cleanup, \
                patch.object(PLUGIN, "_build_missing_torrent_preview", return_value={"downloaders": ["qb"], "items": [], "stalled_items": [], "errors": []}):
            PLUGIN._api_cleanup_stalled_torrents({"delete_files": True})
            self.assertEqual(cleanup.call_args.kwargs, {"kind": "stalled"})

    def test_automatic_stalled_cleanup_requires_its_own_opt_in(self):
        for day in (27, 28, 29):
            self.record(day)
        preview = copy.deepcopy(self.plugin._latest_missing_preview)
        self.plugin._cleanup_auto_cleanup = True
        with patch.object(PLUGIN, "_build_missing_torrent_preview", side_effect=lambda *_: copy.deepcopy(preview)), \
                patch.object(PLUGIN, "_now", return_value="2026-11-29 11:00:00"), \
                patch.object(PLUGIN, "_cleanup_missing_torrents_from_preview", return_value={}) as cleanup:
            self.plugin.run_monitor(modules=["missing"], notify=True)
            cleanup.assert_not_called()
            self.plugin._cleanup_stalled_auto = True
            self.plugin.run_monitor(modules=["missing"], notify=True)
            self.assertEqual(cleanup.call_args.kwargs, {"kind": "stalled"})

    def test_settings_persist_tag_and_age_and_default_auto_is_off(self):
        self.assertFalse(self.plugin._cleanup_stalled_auto)
        self.assertEqual(self.plugin._config_payload()["cleanup_stalled_tag"], "keep_iyuu")
        with patch.object(PLUGIN.sitetoolbox, "_uninstall_error_capture"):
            self.plugin.init_plugin({"enabled": False, "cleanup_stalled_tag": "custom", "cleanup_stalled_days": 14})
        self.assertEqual(PLUGIN._stalled_policy(), {"required_tag": "custom", "age_days": 14})


if __name__ == "__main__":
    unittest.main()
