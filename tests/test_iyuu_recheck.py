"""辅种连接恢复与校验队列回归，不连接真实下载器。"""
import importlib.util
import sys
import threading
import types
import unittest
from collections import UserDict, UserList
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "plugins.v2/iyuuautoseedplus"
spec = importlib.util.spec_from_file_location("iyuu_qb_query_test", PACKAGE / "qb_query.py")
QUERY = importlib.util.module_from_spec(spec)
spec.loader.exec_module(QUERY)


class ConnectionFailure(Exception):
    pass


def load_plugin():
    modules = {}
    dependencies = {
        "pytz": {},
        "apscheduler.schedulers.background": {"BackgroundScheduler": Mock()},
        "apscheduler.triggers.cron": {"CronTrigger": Mock()},
        "lxml": {"etree": Mock()},
        "ruamel.yaml": {"CommentedMap": dict},
        "app.core.config": {"settings": types.SimpleNamespace(USER_AGENT="test", TZ="Asia/Shanghai")},
        "app.core.event": {"eventmanager": types.SimpleNamespace(register=lambda *args: lambda fn: fn)},
        "app.db.site_oper": {"SiteOper": Mock()},
        "app.helper.downloader": {"DownloaderHelper": Mock()},
        "app.helper.sites": {"SitesHelper": Mock()},
        "app.helper.torrent": {"TorrentHelper": Mock()},
        "app.log": {"logger": Mock()},
        "app.plugins": {"_PluginBase": object},
        "app.plugins.iyuuautoseedplus.iyuu_helper": {"IyuuHelper": Mock()},
        "app.plugins.iyuuautoseedplus.qb_query": {
            "QbQueryClient": QUERY.QbQueryClient, "qb_recheck_state": QUERY.qb_recheck_state,
            "query_error_message": QUERY.query_error_message,
        },
        "app.schemas": {"ServiceInfo": object, "NotificationType": Mock(), "Response": types.SimpleNamespace},
        "app.schemas.types": {"EventType": Mock()},
        "app.utils.http": {"RequestUtils": Mock()},
        "app.utils.string": {"StringUtils": Mock()},
    }
    for name, attrs in dependencies.items():
        parts = name.split(".")
        for i in range(1, len(parts) + 1):
            key = ".".join(parts[:i])
            if key not in modules:
                modules[key] = types.ModuleType(key)
                modules[key].__path__ = []
                if i > 1:
                    setattr(modules[".".join(parts[:i - 1])], parts[i - 1], modules[key])
        modules[name].__dict__.update(attrs)
    spec = importlib.util.spec_from_file_location("iyuu_plugin_test", PACKAGE / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


PLUGIN = load_plugin()


class QueryClientTest(unittest.TestCase):
    def setUp(self):
        self.api = types.ModuleType("qbittorrentapi")
        self.api.APIConnectionError = ConnectionFailure
        self.api.Client = Mock()
        self.source = types.SimpleNamespace(
            host="https://qb.example", username="user", _VERIFY_WEBUI_CERTIFICATE=True,
            _REQUESTS_ARGS={"timeout": (15, 60), "proxies": {"https": "http://proxy"}},
            _EXTRA_HEADERS={"Authorization": "Bearer test"}, _HTTPADAPTER_ARGS={},
        )
        self.client = QUERY.QbQueryClient(self.source, {"host": self.source.host, "password": "secret"}, 120)
        self.modules = patch.dict(sys.modules, {"qbittorrentapi": self.api})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def test_eof_rebuilds_once_and_keeps_shared_session_unchanged(self):
        first, second = Mock(), Mock()
        first.torrents_info.side_effect = ConnectionFailure("UNEXPECTED_EOF_WHILE_READING")
        second.torrents_info.return_value = [{"hash": "a"}]
        self.api.Client.side_effect = [first, second]
        result = self.client.query(ids=["a"])
        self.assertIsNone(result["error"])
        self.assertEqual((result["attempts"], result["torrents"]), (2, [{"hash": "a"}]))
        first._http_session.close.assert_called_once()
        self.assertEqual(self.source._REQUESTS_ARGS["timeout"], (15, 60))
        self.assertTrue(self.api.Client.call_args.kwargs["VERIFY_WEBUI_CERTIFICATE"])
        self.assertEqual(self.api.Client.call_args.kwargs["EXTRA_HEADERS"], self.source._EXTRA_HEADERS)
        self.assertEqual(self.api.Client.call_args.kwargs["REQUESTS_ARGS"]["timeout"], (15, 120))

    def test_failed_recovery_is_bounded_and_not_empty_success(self):
        connection = Mock()
        connection.torrents_info.side_effect = ConnectionFailure("UNEXPECTED_EOF_WHILE_READING")
        self.api.Client.return_value = connection
        result = self.client.query(ids=["a"])
        self.assertEqual(result["attempts"], 2)
        self.assertIn("TLS 连接提前断开", result["error"])
        self.assertEqual(self.api.Client.call_count, 2)

    def test_authentication_failure_is_not_retried(self):
        self.api.Client.return_value.torrents_info.side_effect = PermissionError("secret credentials")
        result = self.client.query(ids=["a"])
        self.assertEqual(result["attempts"], 1)
        self.assertNotIn("secret", result["error"])

    def test_malformed_response_is_not_an_empty_success(self):
        self.api.Client.return_value.torrents_info.return_value = {"error": "invalid response"}
        self.assertIn("ValueError", self.client.query(ids=["a"])["error"])

    def test_qb_dictionary_and_list_wrappers_are_accepted(self):
        self.api.Client.return_value.torrents_info.return_value = UserList([UserDict(hash="a")])
        result = self.client.query(ids=["a"])
        self.assertIsNone(result["error"])
        self.assertEqual(result["torrents"], [{"hash": "a"}])

    def test_rows_without_hash_are_not_interpreted_as_deleted_tasks(self):
        self.api.Client.return_value.torrents_info.return_value = [{"error": "not a torrent"}]
        self.assertIsNotNone(self.client.query(ids=["a"])["error"])

    def test_existing_certificate_setting_is_preserved(self):
        self.source._VERIFY_WEBUI_CERTIFICATE = False
        self.api.Client.return_value.torrents_info.return_value = []
        self.client.query(limit=1)
        self.assertFalse(self.api.Client.call_args.kwargs["VERIFY_WEBUI_CERTIFICATE"])

    def test_concurrent_queries_do_not_share_session_concurrently(self):
        entered, release = threading.Event(), threading.Event()
        active = []
        def query(**kwargs):
            active.append(threading.get_ident())
            self.assertEqual(len(active), 1)
            entered.set()
            self.assertTrue(release.wait(2))
            active.pop()
            return []
        self.api.Client.return_value.torrents_info.side_effect = query
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(self.client.query, ["a"])
            self.assertTrue(entered.wait(2))
            second = pool.submit(self.client.query, ["b"])
            self.assertEqual(len(active), 1)
            release.set()
            self.assertFalse(first.result()["error"])
            self.assertFalse(second.result()["error"])

    def test_close_does_not_start_another_request(self):
        self.client.close()
        self.assertIsNotNone(self.client.query(ids=["a"])["error"])
        self.api.Client.assert_not_called()

    def test_close_during_query_cannot_return_rows_for_starting(self):
        def fetch(**kwargs):
            self.client.close()
            return [{"hash": "a"}]
        self.api.Client.return_value.torrents_info.side_effect = fetch
        result = self.client.query(ids=["a"])
        self.assertEqual(result["torrents"], [])
        self.assertIsNotNone(result["error"])


class RecheckTest(unittest.TestCase):
    def setUp(self):
        self.plugin = PLUGIN.IYUUAutoSeedPlus()
        self.plugin._recheck_torrents = {"qb": ["ready", "blocked", "gone", "running"]}
        self.plugin._recheck_messages = {}
        self.plugin._recheck_queue_lock = threading.RLock()
        self.plugin._query_clients_lock = threading.RLock()
        self.plugin._query_clients = {}
        self.plugin._last_queries = {}
        self.server = Mock()
        self.server.start_torrents.return_value = True
        self.service = types.SimpleNamespace(name="qb", type="qbittorrent", instance=self.server)
        self.query = patch.object(self.plugin, "_IYUUAutoSeedPlus__query_torrents")
        self.fetch = self.query.start()
        self.addCleanup(self.query.stop)
        PLUGIN.logger.reset_mock()

    def rows(self):
        return [
            {"hash": "ready", "state": "pausedUP", "progress": 1},
            {"hash": "blocked", "name": "文件大小不匹配.ts", "state": "pausedDL", "progress": 0.5},
            {"hash": "running", "state": "uploading", "progress": 1},
        ]

    def test_partial_success_removes_deleted_and_running_but_keeps_incomplete(self):
        self.fetch.return_value = self.rows(), False
        self.plugin.check_recheck_service(self.service)
        self.server.start_torrents.assert_called_once_with(ids=["ready"])
        self.assertEqual(self.plugin._recheck_torrents["qb"], ["ready", "blocked"])
        self.assertIn("文件未完整匹配", str(PLUGIN.logger.warning.call_args))

    def test_accepted_start_requires_a_later_observed_seeding_state(self):
        self.plugin._recheck_torrents["qb"] = ["ready"]
        self.fetch.return_value = [self.rows()[0]], False
        self.plugin.check_recheck_service(self.service)
        self.assertEqual(self.plugin._recheck_torrents["qb"], ["ready"])
        self.fetch.return_value = [{"hash": "ready", "state": "uploading", "progress": 1}], False
        self.plugin.check_recheck_service(self.service)
        self.assertEqual(self.plugin._recheck_torrents["qb"], [])
        self.server.start_torrents.assert_called_once_with(ids=["ready"])

    def test_start_failure_keeps_ready_task_for_retry(self):
        self.fetch.return_value = self.rows(), False
        self.server.start_torrents.return_value = False
        self.plugin.check_recheck_service(self.service)
        self.assertEqual(self.plugin._recheck_torrents["qb"], ["ready", "blocked"])

    def test_start_exception_keeps_ready_task(self):
        self.fetch.return_value = self.rows(), False
        self.server.start_torrents.side_effect = RuntimeError("failure")
        self.plugin.check_recheck_service(self.service)
        self.assertEqual(self.plugin._recheck_torrents["qb"], ["ready", "blocked"])

    def test_plugin_stop_during_query_cannot_start_tasks(self):
        stopped = threading.Event()
        def fetch(*args, **kwargs):
            stopped.set()
            return self.rows(), False
        self.fetch.side_effect = fetch
        self.plugin.check_recheck_service(self.service, stop_event=stopped)
        self.server.start_torrents.assert_not_called()
        self.assertEqual(self.plugin._recheck_torrents["qb"], ["ready", "blocked", "gone", "running"])

    def test_query_failure_keeps_whole_batch(self):
        before = list(self.plugin._recheck_torrents["qb"])
        self.fetch.return_value = [], True
        self.plugin.check_recheck_service(self.service)
        self.assertEqual(self.plugin._recheck_torrents["qb"], before)
        self.server.start_torrents.assert_not_called()

    def test_unexpected_hash_cannot_clear_queue_or_start_tasks(self):
        before = list(self.plugin._recheck_torrents["qb"])
        self.fetch.return_value = [
            {"hash": "foreign", "state": "pausedUP", "progress": 1},
            {"hash": "running", "state": "uploading", "progress": 1},
            {"hash": "ready", "state": "pausedUP", "progress": 1},
        ], False
        self.plugin.check_recheck_service(self.service)
        self.assertEqual(self.plugin._recheck_torrents["qb"], before)
        self.server.start_torrents.assert_not_called()

    def test_deleted_only_removed_within_successful_batches(self):
        self.plugin._recheck_torrents["qb"] = [str(i) for i in range(101)]
        self.fetch.side_effect = [([], False), ([], True), ([], False)]
        self.plugin.check_recheck_service(self.service)
        self.assertEqual([len(call.kwargs["ids"]) for call in self.fetch.call_args_list], [50, 50, 1])
        self.assertEqual(self.plugin._recheck_torrents["qb"], [str(i) for i in range(50, 100)])

    def test_tasks_added_during_query_are_not_lost(self):
        def fetch(*args, **kwargs):
            self.plugin._IYUUAutoSeedPlus__add_recheck_torrents(self.service, "new")
            return [], False
        self.fetch.side_effect = fetch
        self.plugin.check_recheck_service(self.service)
        self.assertEqual(self.plugin._recheck_torrents["qb"], ["new"])

    def test_repeated_blocked_state_does_not_repeat_warning(self):
        self.plugin._recheck_torrents["qb"] = ["blocked"]
        self.fetch.return_value = [self.rows()[1]], False
        self.plugin.check_recheck_service(self.service)
        self.plugin.check_recheck_service(self.service)
        self.assertEqual(PLUGIN.logger.warning.call_count, 1)

    def test_timeout_configuration_does_not_modify_shared_client(self):
        self.plugin._applied_downloader_timeouts = {}
        self.server.qbc = types.SimpleNamespace(_REQUESTS_ARGS={"timeout": (15, 60)})
        self.plugin._IYUUAutoSeedPlus__apply_downloader_timeout(self.service)
        self.assertEqual(self.server.qbc._REQUESTS_ARGS, {"timeout": (15, 60)})

    def test_diagnostic_compares_core_and_isolated_queries_without_actions(self):
        self.query.stop()
        self.plugin._downloaders = ["qb"]
        self.plugin._auto_downloader = ""
        source = types.SimpleNamespace(torrents_info=Mock(return_value=UserList([{ "hash": "a"} ])),
                                       _VERIFY_WEBUI_CERTIFICATE=True)
        self.server.qbc = source
        self.service.config = types.SimpleNamespace(config={"host": "https://qb.example", "password": "secret"})
        PLUGIN.DownloaderHelper.return_value.get_service.return_value = self.service
        isolated = Mock(source=source, config=self.service.config.config, timeout=self.plugin._downloader_timeout)
        isolated.query.return_value = {"torrents": [{"hash": "a"}], "error": None, "attempts": 1, "elapsed": 0.1}
        with patch.object(PLUGIN, "QbQueryClient", return_value=isolated):
            result = self.plugin.diagnose_downloader("qb")
        self.assertTrue(result.data["core"]["success"])
        self.assertTrue(result.data["iyuu"]["success"])
        isolated.query.assert_called_once_with(ids=None, status=None, limit=1)
        self.server.start_torrents.assert_not_called()
        self.server.add_torrent.assert_not_called()
        self.server.delete_torrents.assert_not_called()
        self.assertNotIn("secret", str(result.data))

    def test_diagnostic_rejects_downloaders_outside_plugin_configuration(self):
        self.plugin._downloaders = ["other"]
        self.plugin._auto_downloader = ""
        self.assertFalse(self.plugin.diagnose_downloader("qb").success)
        self.fetch.assert_not_called()

    def test_transmission_completed_query_preserves_original_api(self):
        self.query.stop()
        self.service.type = "transmission"
        self.server.get_completed_torrents.return_value = None
        rows, failed = self.plugin._IYUUAutoSeedPlus__query_torrents(self.service, status="seeding")
        self.assertEqual(rows, [])
        self.assertTrue(failed)
        self.server.get_completed_torrents.assert_called_once_with()

    def test_transmission_recheck_still_starts_complete_stopped_tasks(self):
        self.service.type = "transmission"
        self.plugin._recheck_torrents["qb"] = ["ready"]
        torrent = types.SimpleNamespace(hashString="ready", status=types.SimpleNamespace(stopped=True), percent_done=1)
        self.fetch.return_value = [torrent], False
        self.plugin.check_recheck_service(self.service)
        self.server.start_torrents.assert_called_once_with(ids=["ready"])
        self.assertEqual(self.plugin._recheck_torrents["qb"], ["ready"])
        torrent.status.stopped = False
        self.plugin.check_recheck_service(self.service)
        self.assertEqual(self.plugin._recheck_torrents["qb"], [])

    def test_qb_state_safety(self):
        for state in ["missingFiles", "error", "checkingUP", "checkingResumeData", "pausedDL", "stoppedDL"]:
            self.assertNotEqual(QUERY.qb_recheck_state({"state": state, "progress": 1})[0], "ready")
        self.assertNotEqual(QUERY.qb_recheck_state({"state": "pausedUP", "progress": 0.5})[0], "ready")
        self.assertEqual(QUERY.qb_recheck_state({"state": "stoppedUP", "progress": 1})[0], "ready")


if __name__ == "__main__":
    unittest.main()
