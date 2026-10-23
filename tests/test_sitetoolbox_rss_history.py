"""RSS 连续失败判定与插件接入测试，不依赖运行中的 MoviePilot。"""

from __future__ import annotations

import copy
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]


class PluginBaseStub:
    def __init__(self, storage=None):
        self.storage = storage if storage is not None else {}
        self.config = {}
        self.systemmessage = Mock()

    def get_data(self, key):
        return copy.deepcopy(self.storage.get(key))

    def save_data(self, key, value):
        self.storage[key] = copy.deepcopy(value)

    def update_config(self, config):
        self.config = copy.deepcopy(config)
        return True


def load_plugin():
    """只替换宿主依赖，加载完整插件以验证实际调用路径。"""
    modules = {}
    site_oper = Mock()
    site_oper.return_value.list_order_by_pri.return_value = []
    dependencies = {
        "apscheduler.triggers.cron": {"CronTrigger": Mock()},
        "fastapi": {"Body": lambda default=None: default},
        "lxml": {"etree": Mock()},
        "app.schemas": {"Response": types.SimpleNamespace},
        "app.core.config": {"settings": types.SimpleNamespace(USER_AGENT="test", PROXY={})},
        "app.db.site_oper": {"SiteOper": site_oper},
        "app.db.systemconfig_oper": {"SystemConfigOper": Mock()},
        "app.helper.downloader": {"DownloaderHelper": Mock()},
        "app.helper.rss": {"RssHelper": Mock()},
        "app.helper.sites": {"SitesHelper": Mock()},
        "app.log": {"logger": Mock()},
        "app.plugins": {"_PluginBase": PluginBaseStub},
        "app.schemas.types": {"SystemConfigKey": Mock()},
        "app.utils.http": {"RequestUtils": Mock()},
        "app.utils.string": {"StringUtils": Mock()},
    }
    for name, attributes in dependencies.items():
        parts = name.split(".")
        for index in range(1, len(parts) + 1):
            prefix = ".".join(parts[:index])
            if prefix not in modules:
                modules[prefix] = types.ModuleType(prefix)
                modules[prefix].__path__ = []
                if index > 1:
                    setattr(modules[".".join(parts[:index - 1])], parts[index - 1], modules[prefix])
        modules[name].__dict__.update(attributes)
    path = ROOT / "plugins.v2/sitetoolbox/__init__.py"
    spec = importlib.util.spec_from_file_location("sitetoolbox_rss_test", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {**modules, "sitetoolbox_rss_test": module}):
        spec.loader.exec_module(module)
    return module


PLUGIN = load_plugin()


class RssFailureHistoryTest(unittest.TestCase):
    def setUp(self):
        self.storage = {}
        self.plugin = PLUGIN.sitetoolbox(self.storage)
        self.configure(rss_failure_days=3)

    def configure(self, **overrides):
        config = {"enabled": False, **overrides}
        with patch.object(PLUGIN.sitetoolbox, "_uninstall_error_capture"):
            self.plugin.init_plugin(config)

    @staticmethod
    def result(day, state="error", site_id=1):
        return {
            "site_id": site_id,
            "site_name": f"测试站点 {site_id}",
            "domain": "example.test",
            "state": state,
            "message": "RSS正常" if state == "success" else "RSS请求或解析失败",
            "tested_at": f"2026-10-{day:02} 08:00:00",
        }

    def record(self, day, state="error", site_id=1):
        result = self.plugin._record_rss_result(self.result(day, state, site_id))
        kept = [item for item in self.plugin._latest_results if item["site_id"] != site_id]
        self.plugin._save_results([result, *kept])
        return result

    def run_auto(self, day, state="error"):
        self.plugin._site_ids = [1]
        with patch.object(PLUGIN, "_test_site_rss_inner", return_value=self.result(day, state)), \
                patch.object(PLUGIN, "_site_statistics", return_value={}):
            self.plugin.auto_test_rss()
        return self.plugin._latest_results[0]

    def test_only_consecutive_dates_reach_threshold(self):
        for day, count, state in ((21, 1, "pending"), (22, 2, "pending"), (23, 3, "error")):
            with self.subTest(day=day):
                result = self.record(day)
                self.assertEqual((result["failure_days"], result["state"]), (count, state))
                self.assertEqual(result["rss_message"], "RSS请求或解析失败")

    def test_repeated_failures_on_same_day_count_once(self):
        for _ in range(10):
            result = self.record(21)
        self.assertEqual(result["failure_days"], 1)
        self.assertEqual(result["state"], "pending")

    def test_success_resets_even_on_same_day(self):
        self.record(21)
        self.record(22)
        self.assertEqual(self.record(22, "success")["failure_days"], 0)
        self.assertEqual(self.record(22)["failure_days"], 1)
        self.assertEqual(self.record(23)["failure_days"], 2)

    def test_missing_calendar_day_breaks_streak(self):
        self.record(21)
        self.assertEqual(self.record(23)["failure_days"], 1)

    def test_offline_is_visible_and_uses_the_shared_streak(self):
        self.record(21)
        offline = self.record(22, "offline")
        self.assertEqual((offline["state"], offline["failure_days"], offline["confirmed"]), ("offline", 2, False))
        self.assertEqual(self.record(23)["failure_days"], 3)

    def test_warning_and_error_share_failure_streak(self):
        self.record(21, "warning")
        self.record(22)
        result = self.record(23, "warning")
        self.assertEqual((result["state"], result["failure_days"]), ("warning", 3))

    def test_restart_preserves_history(self):
        self.record(21)
        self.record(22)
        config = copy.deepcopy(self.plugin.config)
        self.plugin = PLUGIN.sitetoolbox(self.storage)
        self.configure(**config)
        self.assertEqual(self.record(23)["failure_days"], 3)

    def test_changing_threshold_reclassifies_without_counting(self):
        self.record(21)
        self.record(22)
        history = copy.deepcopy(self.storage)
        for threshold, state in ((2, "error"), (5, "pending"), (1, "error")):
            self.configure(**{**self.plugin.config, "monitor_failure_days": threshold})
            result = self.plugin._latest_results[0]
            self.assertEqual(result["state"], state)
            self.assertEqual(result["failure_days"], 2)
            self.assertEqual(result["message"].count("RSS请求或解析失败"), 1)
            self.assertEqual(self.storage, history)
            self.assertEqual(self.plugin._config_payload()["rss_failure_days"], threshold)

    def test_subset_tests_do_not_erase_other_site_history(self):
        self.record(21, site_id=1)
        self.record(21, site_id=2)
        self.plugin._save_results([self.record(22, site_id=2)])
        self.assertEqual(self.record(22, site_id=1)["failure_days"], 2)

    def test_legacy_last_result_contributes_at_most_one_date(self):
        self.configure(latest_results=[self.result(21)])
        self.assertEqual(self.record(22)["failure_days"], 2)
        self.storage.clear()
        self.configure(latest_results=[self.result(18)])
        self.assertEqual(self.record(23)["failure_days"], 1)

    def test_one_day_threshold_reports_first_failure(self):
        self.configure(rss_failure_days=1)
        self.assertEqual(self.record(21)["state"], "error")

    def test_threshold_is_configurable_and_validated(self):
        for value, expected in ((None, 3), ("bad", 3), (0, 1), (999, 365), ("7", 7)):
            self.configure(rss_failure_days=value)
            self.assertEqual(self.plugin._rss_failure_days, expected)

    def test_automatic_notification_waits_for_threshold(self):
        for day in (21, 22):
            self.assertEqual(self.run_auto(day)["state"], "pending")
            self.plugin.systemmessage.put.assert_not_called()
            self.assertIn("异常 0", self.plugin._latest_job_states["rss_test_auto"]["message"])
        self.assertEqual(self.run_auto(23)["state"], "error")
        self.plugin.systemmessage.put.assert_called_once()
        self.assertIn("连续失败达到 3 天", self.plugin.systemmessage.put.call_args.args[0])

    def test_manual_success_resets_automatic_history(self):
        self.run_auto(21)
        self.run_auto(22)
        with patch.object(PLUGIN, "_test_site_rss_inner", return_value=self.result(22, "success")), \
                patch.object(self.plugin, "_start_background_job", side_effect=lambda _key, _name, worker: worker()):
            self.assertEqual(PLUGIN._api_test_one_rss(1), "RSS正常")
        result = self.run_auto(23)
        self.assertEqual((result["failure_days"], result["state"]), (1, "pending"))
        self.plugin.systemmessage.put.assert_not_called()

    def test_pending_results_are_visible_but_not_counted_as_abnormal(self):
        self.record(21)
        with patch.object(self.plugin, "_get_error_records", return_value=[]), \
                patch.object(PLUGIN, "_overview_panel", return_value={}) as overview:
            page = PLUGIN._toolbox_page(self.plugin)
        self.assertEqual(overview.call_args.kwargs["rss_bad"], 0)
        self.assertIn("待观察", str(page))
        self.assertIn("连续异常 1/3 天", str(page))

    def test_form_exposes_failure_days_with_default(self):
        with patch.object(PLUGIN, "_downloader_options", return_value=[]):
            form, defaults = self.plugin.get_form()
        self.assertEqual(defaults["monitor_failure_days"], 3)
        self.assertNotIn("rss_failure_days", defaults)
        self.assertIsNone(form)
        self.assertEqual(self.plugin.get_render_mode(), ("vue", "dist/assets"))


if __name__ == "__main__":
    unittest.main()
