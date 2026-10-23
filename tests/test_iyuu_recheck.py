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
            "probe_network": QUERY.probe_network,
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

    def test_direct_queries_override_proxies_only_on_private_client(self):
        self.client.direct = True
        self.api.Client.return_value.torrents_info.return_value = []
        self.client.query(limit=1)
        args = self.api.Client.call_args.kwargs["REQUESTS_ARGS"]
        self.assertEqual(args["proxies"], {"http": "", "https": "", "all": ""})
        self.assertEqual(self.source._REQUESTS_ARGS["proxies"], {"https": "http://proxy"})
        import requests
        from requests.utils import select_proxy
        with patch.dict("os.environ", {"HTTPS_PROXY": "http://environment-proxy:8080", "ALL_PROXY": "http://fallback:8080"}):
            settings = requests.Session().merge_environment_settings("https://qb.example", args["proxies"].copy(), False, None, None)
        self.assertFalse(select_proxy("https://qb.example", settings["proxies"]))

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
        self.plugin._event = Mock()
        self.plugin._event.is_set.return_value = False
        self.plugin._event.wait.return_value = False
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
        isolated = Mock(source=source, config=self.service.config.config, timeout=self.plugin._downloader_timeout, direct=False)
        isolated.query.return_value = {"torrents": [{"hash": "a"}], "error": None, "attempts": 1, "elapsed": 0.1}
        with patch.object(PLUGIN, "QbQueryClient", return_value=isolated):
            result = self.plugin.diagnose_downloader("qb")
        self.assertTrue(result.data["core"]["success"])
        self.assertTrue(result.data["iyuu"]["success"])
        isolated.query.assert_called_once_with(ids=None, status=None, limit=1, tag=None)
        self.server.start_torrents.assert_not_called()
        self.server.add_torrent.assert_not_called()
        self.server.delete_torrents.assert_not_called()
        self.assertNotIn("secret", str(result.data))

    def test_diagnostic_direct_override_does_not_save_or_change_configuration(self):
        self.query.stop()
        self.plugin._downloaders = ["qb"]
        self.plugin._auto_downloader = ""
        self.plugin._downloader_direct = False
        source = types.SimpleNamespace(torrents_info=Mock(return_value=[]), _VERIFY_WEBUI_CERTIFICATE=True)
        self.server.qbc = source
        self.service.config = types.SimpleNamespace(config={"host": "https://qb.example"})
        PLUGIN.DownloaderHelper.return_value.get_service.return_value = self.service
        isolated = Mock()
        isolated.query.return_value = {"torrents": [], "error": None, "attempts": 1, "elapsed": 0.1}
        with patch.object(PLUGIN, "QbQueryClient", return_value=isolated) as factory:
            result = self.plugin.diagnose_downloader("qb", direct=True)
        self.assertTrue(factory.call_args.kwargs["direct"])
        self.assertEqual(result.data["iyuu"]["connection_mode"], "direct")
        self.assertFalse(self.plugin._downloader_direct)
        self.server.start_torrents.assert_not_called()

    def test_direct_configuration_is_loaded_and_saved(self):
        PLUGIN.SiteOper.return_value.list_order_by_pri.return_value = []
        with patch.object(self.plugin, "get_config", return_value={}, create=True), \
                patch.object(self.plugin, "update_config", create=True) as save:
            self.plugin._IYUUAutoSeedPlus__load_config({"downloader_direct": "true"})
            self.plugin._IYUUAutoSeedPlus__update_config()
        self.assertTrue(self.plugin._downloader_direct)
        self.assertTrue(save.call_args.args[0]["downloader_direct"])

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

    def test_legacy_added_hash_lookup_uses_isolated_query_and_checks_exact_tag(self):
        self.fetch.return_value = [{"hash": "a", "tags": "other, unique-tag"}], False
        self.assertEqual(self.plugin._IYUUAutoSeedPlus__find_added_torrent(self.service, "unique-tag"), "a")
        self.fetch.assert_called_once_with(self.service, tag="unique-tag", limit=2)
        self.server.get_torrent_id_by_tag.assert_not_called()

    def test_legacy_added_hash_lookup_waits_for_qb_metadata(self):
        self.fetch.side_effect = [([], False), ([{"hash": "a", "tags": "unique-tag"}], False)]
        self.assertEqual(self.plugin._IYUUAutoSeedPlus__find_added_torrent(self.service, "unique-tag"), "a")
        self.plugin._event.wait.assert_called_once_with(3)

    def test_legacy_hash_lookup_does_not_guess_after_query_failure_or_tag_collision(self):
        for response in [([], True), ([{"hash": "a", "tags": "tag"}, {"hash": "b", "tags": "tag"}], False)]:
            self.fetch.return_value = response
            self.assertIsNone(self.plugin._IYUUAutoSeedPlus__find_added_torrent(self.service, "tag"))

    def test_legacy_hash_lookup_is_bounded_and_can_be_cancelled(self):
        self.fetch.return_value = [{"hash": "a", "tags": "different-tag"}], False
        self.assertIsNone(self.plugin._IYUUAutoSeedPlus__find_added_torrent(self.service, "tag"))
        self.assertEqual(self.fetch.call_count, 9)
        self.plugin._event.is_set.return_value = True
        self.fetch.reset_mock()
        self.assertIsNone(self.plugin._IYUUAutoSeedPlus__find_added_torrent(self.service, "tag"))
        self.fetch.assert_not_called()


class NetworkProbeTest(unittest.TestCase):
    def test_probes_one_address_per_family_and_never_returns_proxy_credentials(self):
        import socket
        source = types.SimpleNamespace(_http_session=types.SimpleNamespace(trust_env=True), _REQUESTS_ARGS={})
        ipv4, ipv6 = Mock(), Mock()
        ipv4.__enter__ = Mock(return_value=ipv4)
        ipv4.__exit__ = Mock(return_value=False)
        ipv6.__enter__ = Mock(return_value=ipv6)
        ipv6.__exit__ = Mock(return_value=False)
        ipv6.connect.side_effect = OSError("network unreachable")
        tls = Mock()
        tls.__enter__ = Mock(return_value=tls)
        tls.__exit__ = Mock(return_value=False)
        tls.version.return_value = "TLSv1.3"
        addresses = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.1", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.2", 443)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::1", 443, 0, 0)),
        ]
        with patch("socket.getaddrinfo", return_value=addresses), \
                patch("socket.socket", side_effect=[ipv4, ipv6]) as sockets, \
                patch("ssl.create_default_context") as context, \
                patch("requests.utils.get_environ_proxies", return_value={"https": "http://secret:password@proxy"}):
            context.return_value.wrap_socket.return_value = tls
            result = QUERY.probe_network(source, {"host": "https://qb.example"})
        self.assertEqual(sockets.call_count, 2)
        self.assertTrue(result["connections"][0]["tls"])
        self.assertIn("error", result["connections"][1])
        self.assertTrue(result["environment_proxy"])
        self.assertNotIn("secret", str(result))
        context.return_value.wrap_socket.assert_called_once_with(ipv4, server_hostname="qb.example")

    def test_dns_failure_returns_diagnostic_instead_of_crashing(self):
        source = types.SimpleNamespace(_http_session=None, _REQUESTS_ARGS={})
        with patch("socket.getaddrinfo", side_effect=OSError("DNS failure")), \
                patch("requests.utils.get_environ_proxies", return_value={}):
            result = QUERY.probe_network(source, {"host": "https://qb.example"})
        self.assertIn("dns_error", result)
        self.assertEqual(result["connections"], [])


if __name__ == "__main__":
    unittest.main()
