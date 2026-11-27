"""连接诊断、敏感信息隔离和下载返回值的回归，不操作真实种子。"""
import importlib.util
import json
import socket
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "plugins.v2/flowbrush"
spec = importlib.util.spec_from_file_location("flowbrush_test.diagnostics", PACKAGE / "diagnostics.py")
DIAG = importlib.util.module_from_spec(spec)
spec.loader.exec_module(DIAG)
spec = importlib.util.spec_from_file_location("flowbrush_test.direct_downloader", PACKAGE / "direct_downloader.py")
DIRECT = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {"flowbrush_test.diagnostics": DIAG}):
    spec.loader.exec_module(DIRECT)


def load_plugin():
    modules = {"flowbrush_test.diagnostics": DIAG, "flowbrush_test.direct_downloader": DIRECT}
    dependencies = {
        "pytz": {},
        "apscheduler.schedulers.background": {"BackgroundScheduler": Mock()},
        "apscheduler.triggers.cron": {"CronTrigger": Mock()},
        "app": {"schemas": Mock()},
        "app.helper.sites": {"SitesHelper": Mock()},
        "app.chain.torrents": {"TorrentsChain": Mock()},
        "app.core.config": {"settings": types.SimpleNamespace(PROXY=None, TORRENT_TAG="PT", TZ="Asia/Shanghai")},
        "app.db.site_oper": {"SiteOper": Mock()},
        "app.db.subscribe_oper": {"SubscribeOper": Mock()},
        "app.helper.downloader": {"DownloaderHelper": Mock()},
        "app.log": {"logger": Mock()},
        "app.modules.qbittorrent": {"Qbittorrent": type("Qbittorrent", (), {})},
        "app.modules.transmission": {"Transmission": type("Transmission", (), {})},
        "app.plugins": {"_PluginBase": object},
        "app.schemas": {"NotificationType": Mock(), "TorrentInfo": object, "ServiceInfo": object},
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
        modules[name].__dict__.update(attrs)
    spec = importlib.util.spec_from_file_location("flowbrush_test", PACKAGE / "__init__.py",
                                                 submodule_search_locations=[str(PACKAGE)])
    plugin = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(plugin)
    return plugin


PLUGIN = load_plugin()


class DownloadTest(unittest.TestCase):
    def setUp(self):
        PLUGIN.logger.reset_mock()
        self.plugin = PLUGIN.FlowBrush()
        self.plugin._brush_config = PLUGIN.BrushConfig({"downloader": "test", "maxdlcount": 5})
        self.downloader = Mock()
        self.downloader.is_inactive.return_value = False
        self.service = types.SimpleNamespace(instance=self.downloader, name="test", config={"config": {}})
        direct = patch.object(PLUGIN, "direct_service", return_value=self.service)
        direct.start()
        self.addCleanup(direct.stop)
        PLUGIN.DownloaderHelper.return_value.get_service.return_value = self.service
        PLUGIN.DownloaderHelper.return_value.is_downloader.return_value = True
        self.torrent = types.SimpleNamespace(enclosure="magnet:?xt=urn:btih:example",
                                            site_name="城市", site_proxy=False, site_cookie="secret-cookie")

    def test_failed_tuple_does_not_poll_or_retry_add(self):
        self.downloader.add_torrent.return_value = (False, [])
        self.assertIsNone(self.plugin._FlowBrush__download(self.torrent))
        self.downloader.get_torrent_id_by_tag.assert_not_called()
        self.downloader.add_torrent.assert_called_once()
        self.assertNotIn("secret-cookie", str(PLUGIN.logger.mock_calls))

    def test_returned_hash_skips_tag_query(self):
        self.downloader.add_torrent.return_value = (True, ["known-hash"])
        self.assertEqual(self.plugin._FlowBrush__download(self.torrent), "known-hash")
        self.downloader.get_torrent_id_by_tag.assert_not_called()
        self.downloader.delete_torrents_tag.assert_called_once()

    def test_legacy_success_and_missing_hash_do_not_retry_add(self):
        self.downloader.add_torrent.return_value = True
        self.downloader.get_torrent_id_by_tag.return_value = None
        self.assertIsNone(self.plugin._FlowBrush__download(self.torrent))
        self.downloader.get_torrent_id_by_tag.assert_called_once()
        self.downloader.add_torrent.assert_called_once()

    def test_unknown_download_count_stops_before_bandwidth_or_add(self):
        self.downloader.get_downloading_torrents.return_value = None
        with patch.object(self.plugin, "_FlowBrush__get_average_bandwidth") as bandwidth:
            passed, reason = self.plugin._FlowBrush__evaluate_pre_conditions_for_brush()
        self.assertFalse(passed)
        self.assertIn("读取下载任务数失败", reason)
        bandwidth.assert_not_called()
        self.downloader.add_torrent.assert_not_called()

    def test_zero_count_is_distinct_from_connection_failure(self):
        self.downloader.get_downloading_torrents.return_value = []
        self.assertEqual(self.plugin._FlowBrush__get_downloading_count(), 0)
        self.downloader.is_inactive.return_value = True
        self.assertIsNone(self.plugin._FlowBrush__get_downloading_count())

    def test_exception_logging_excludes_secret_body(self):
        self.downloader.add_torrent.side_effect = RuntimeError("https://admin:password@host/?passkey=abc Cookie=secret-cookie")
        self.assertIsNone(self.plugin._FlowBrush__download(self.torrent))
        for secret in ("password", "passkey", "secret-cookie"):
            self.assertNotIn(secret, str(PLUGIN.logger.mock_calls))

    def test_diagnostic_uses_offline_service_and_is_rate_limited(self):
        self.service.config = types.SimpleNamespace(config={"host": "https://qb.example"})
        self.plugin._diagnostic_lock = threading.Lock()
        self.plugin._last_diagnostic = 0
        with patch.object(PLUGIN, "diagnose_connection", return_value={"read_only": True}) as probe, \
                patch.object(PLUGIN, "authenticated_probe", return_value={"success": True}), \
                patch.object(PLUGIN.threading, "Thread") as thread:
            thread.return_value.start.side_effect = lambda: thread.call_args.kwargs["target"]()
            self.plugin._FlowBrush__start_downloader_diagnostic("test")
            self.plugin._FlowBrush__start_downloader_diagnostic("test")
        probe.assert_called_once()
        self.downloader.add_torrent.assert_not_called()

    def test_service_is_cached_and_rebuilt_when_credentials_change(self):
        first = self.plugin.service_info
        self.assertIs(first, self.plugin.service_info)
        self.assertEqual(PLUGIN.direct_service.call_count, 1)
        self.service.config["config"]["password"] = "new-password"
        self.plugin.service_info
        self.assertEqual(PLUGIN.direct_service.call_count, 2)

    def test_bandwidth_query_uses_plugin_client_not_shared_chain(self):
        self.plugin.chain = Mock()
        self.downloader.transfer_info.return_value = {"dl_info_speed": 123, "up_info_speed": 456}
        self.plugin._FlowBrush__get_downloader_info()
        self.downloader.transfer_info.assert_called_once()
        self.plugin.chain.run_module.assert_not_called()

    def test_failed_direct_initialization_cannot_fall_back_to_shared_client(self):
        PLUGIN.direct_service.side_effect = RuntimeError("failed")
        self.assertIsNone(self.plugin.downloader)
        self.downloader.add_torrent.assert_not_called()
        self.downloader.delete_torrents.assert_not_called()
        self.downloader.reconnect.assert_not_called()

    def test_one_shot_flag_is_consumed_even_when_plugin_is_disabled(self):
        self.plugin.update_config = Mock()
        with patch.object(self.plugin, "_FlowBrush__validate_and_fix_config", return_value=True), \
                patch.object(self.plugin, "stop_service"), \
                patch.object(self.plugin, "_FlowBrush__start_downloader_diagnostic") as start:
            self.plugin.init_plugin({"enabled": False, "downloader": "test", "diagnostic_once": True})
        start.assert_called_once_with("test")
        self.assertFalse(self.plugin.update_config.call_args.args[0]["diagnostic_once"])
        self.downloader.add_torrent.assert_not_called()


class DiagnosticsTest(unittest.TestCase):
    def test_known_errors_do_not_expose_exception_text(self):
        for message in ["UNEXPECTED_EOF_WHILE_READING", "CERTIFICATE_VERIFY_FAILED", "timed out", "unknown"]:
            report = DIAG.error_summary(RuntimeError(message + " password=secret Cookie=hidden"))
            self.assertNotIn("secret", str(report))
            self.assertNotIn("hidden", str(report))
        self.assertIn("提前断开", DIAG.error_summary(RuntimeError("UNEXPECTED_EOF_WHILE_READING"))["reason"])
        self.assertIn("证书", DIAG.error_summary(RuntimeError("CERTIFICATE_VERIFY_FAILED"))["reason"])

    def test_false_result_and_malformed_hash_list(self):
        self.assertEqual(DIAG.normalize_add_result((False, ["hash"])), (False, []))
        self.assertEqual(DIAG.normalize_add_result((True, "hash")), (True, []))
        self.assertEqual(DIAG.normalize_add_result(False), (False, []))

    def test_dns_wait_is_bounded(self):
        release = threading.Event()
        try:
            with patch.object(DIAG.socket, "getaddrinfo", side_effect=lambda *a, **kw: release.wait(1)):
                with self.assertRaises(TimeoutError):
                    DIAG._resolve("qb.example", 443, timeout=0.01)
        finally:
            release.set()

    def test_target_credentials_are_stripped_and_shared_client_is_untouched(self):
        source = types.SimpleNamespace(_VERIFY_WEBUI_CERTIFICATE=True, _http_session=None,
                                       _REQUESTS_ARGS={"timeout": (15, 60)})
        with patch.object(DIAG, "_resolve", return_value=[]), \
                patch.object(DIAG, "_http_probe", return_value={"http_status": 403}) as http:
            report = DIAG.diagnose_connection(source, {"host": "https://user:secret@qb.example/base?passkey=hidden#token"})
        self.assertEqual(http.call_args.args[0], "https://qb.example:443/base/api/v2/app/webapiVersion")
        self.assertEqual(source._REQUESTS_ARGS, {"timeout": (15, 60)})
        self.assertIsNone(source._http_session)
        self.assertNotIn("secret", json.dumps(report))
        self.assertNotIn("hidden", json.dumps(report))

    def test_http_probe_has_no_auth_redirects_or_insecure_tls(self):
        source = types.SimpleNamespace(_http_session=types.SimpleNamespace(trust_env=True, proxies={}),
                                       _REQUESTS_ARGS={"proxies": {"https": "http://user:secret@proxy:8080"}},
                                       _EXTRA_HEADERS={"Authorization": "Bearer sensitive"})
        session = Mock()
        session.merge_environment_settings.return_value = {"proxies": source._REQUESTS_ARGS["proxies"]}
        session.get.return_value.__enter__ = Mock(return_value=types.SimpleNamespace(status_code=403))
        session.get.return_value.__exit__ = Mock(return_value=False)
        with patch.object(DIAG.requests, "Session") as factory:
            factory.return_value.__enter__.return_value = session
            report = DIAG._http_probe("https://qb.example/api/v2/app/webapiVersion", source)
        kwargs = session.get.call_args.kwargs
        self.assertTrue(kwargs["verify"])
        self.assertFalse(kwargs["allow_redirects"])
        self.assertTrue(callable(session.auth))
        self.assertNotIn("headers", kwargs)
        self.assertEqual(report["http_status"], 403)
        self.assertTrue(report["reachable"])
        self.assertNotIn("secret", json.dumps(report))
        self.assertNotIn("sensitive", json.dumps(report))
        self.assertEqual(source._REQUESTS_ARGS["proxies"]["https"], "http://user:secret@proxy:8080")

    def test_tcp_and_tls_errors_are_attributed_to_the_correct_stage(self):
        address = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 443))
        for tls_failure in (False, True):
            with self.subTest(tls_failure=tls_failure), patch.object(DIAG, "_resolve", return_value=[address]), \
                    patch.object(DIAG.socket, "socket") as sock, \
                    patch.object(DIAG.ssl, "create_default_context") as ctx, \
                    patch.object(DIAG, "_http_probe", return_value={}):
                if tls_failure:
                    ctx.return_value.wrap_socket.side_effect = RuntimeError("UNEXPECTED_EOF_WHILE_READING")
                else:
                    sock.return_value.__enter__.return_value.connect.side_effect = ConnectionRefusedError("Connection refused")
                report = DIAG.diagnose_connection(None, {"host": "https://qb.example"})
                self.assertEqual(report["connections"][0]["stage"], "tls" if tls_failure else "tcp")


if __name__ == "__main__":
    unittest.main()
