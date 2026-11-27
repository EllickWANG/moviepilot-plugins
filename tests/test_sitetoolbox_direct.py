"""隔离 HTTP 服务验证真实 qB 客户端的扫描、复查和删除调用路径。"""
import importlib.util
import json
import os
import threading
import types
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs

from qbittorrentapi import Client
from test_flowbrush_direct import FakeQb
from test_sitetoolbox_rss_history import PLUGIN

spec = importlib.util.spec_from_file_location(
    "sitetoolbox_direct_test", Path(PLUGIN.__file__).with_name("direct_downloader.py"))
DIRECT = importlib.util.module_from_spec(spec)
spec.loader.exec_module(DIRECT)


class MaintenanceQb(FakeQb):
    def do_GET(self):
        if self.path.split("?", 1)[0] != "/api/v2/torrents/info":
            return super().do_GET()
        if "SID=isolated-test" not in self.headers.get("Cookie", ""):
            self.send_error(403)
            return
        self.server.requests.append(self.path)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps(self.server.rows).encode())

    def do_POST(self):
        if self.path != "/api/v2/torrents/delete":
            return super().do_POST()
        if "SID=isolated-test" not in self.headers.get("Cookie", ""):
            self.send_error(403)
            return
        data = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode())
        self.server.deletions.append(data)
        self.send_response(200)
        self.end_headers()


class Wrapper:
    def __init__(self, qbc):
        self.qbc = qbc
        self.reconnect = Mock(side_effect=AssertionError("不能重连宿主共享实例"))

    def get_torrents(self, ids=None):
        return self.qbc.torrents_info(torrent_hashes=ids), False

    def delete_torrents(self, delete_file, ids):
        self.qbc.torrents_delete(delete_files=delete_file, torrent_hashes=ids)
        return True


class MaintenanceDirectTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), MaintenanceQb)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.server.requests = []
        self.server.deletions = []
        self.server.rows = [
            {"hash": "missing", "name": "missing", "state": "missingFiles", "total_size": 12},
            {"hash": "paused", "name": "paused", "state": "pausedDL", "total_size": 12},
            {"hash": "recovered", "name": "recovered", "state": "stalledUP", "total_size": 12},
        ]
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.source = Client(host=self.url, VERIFY_WEBUI_CERTIFICATE=True,
                             EXTRA_HEADERS={"X-Test": "kept", "Proxy-Authorization": "secret"},
                             REQUESTS_ARGS={"timeout": (3, 10), "proxies": {"http": "http://127.0.0.1:1"}})
        self.service = types.SimpleNamespace(
            name="qb", type="qbittorrent", instance=Wrapper(self.source),
            config=types.SimpleNamespace(config={"host": self.url, "username": "test", "password": "test-secret"}))
        env = patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:1", "HTTPS_PROXY": "http://127.0.0.1:1",
                                     "ALL_PROXY": "http://127.0.0.1:1", "NO_PROXY": "", "no_proxy": ""})
        env.start()
        self.addCleanup(env.stop)
        services = patch.object(PLUGIN, "DownloaderHelper")
        services.start().return_value.get_services.return_value = {"qb": self.service}
        self.addCleanup(services.stop)

    def test_scan_bypasses_both_proxy_sources_without_changing_shared_client(self):
        preview = PLUGIN._build_missing_torrent_preview(["qb"])
        self.assertEqual(preview["errors"], [])
        self.assertEqual([row["hash"] for row in preview["items"]], ["missing"])
        self.assertIs(self.service.instance.qbc, self.source)
        self.assertIsNone(self.source._http_session)
        self.assertEqual(self.source._REQUESTS_ARGS["proxies"], {"http": "http://127.0.0.1:1"})
        self.assertEqual(os.environ["HTTPS_PROXY"], "http://127.0.0.1:1")
        self.service.instance.reconnect.assert_not_called()
        self.assertEqual(self.server.deletions, [])

    def test_cleanup_rechecks_and_deletes_only_still_missing_using_one_login(self):
        items = [{"downloader": "qb", "hash": h} for h in ("missing", "recovered", "gone")]
        result = PLUGIN._cleanup_missing_torrents_from_preview(items, delete_files=False)
        self.assertEqual((result["deleted_count"], result["skipped_count"], result["failed_count"]), (1, 2, 0))
        self.assertEqual(self.server.deletions, [{"hashes": ["missing"], "deleteFiles": ["False"]}])
        self.assertEqual(self.server.requests.count("/api/v2/auth/login"), 1)
        self.assertIsNone(self.source._http_session)

    def test_cleanup_preserves_explicit_delete_files_setting(self):
        result = PLUGIN._cleanup_missing_torrents_from_preview([{"downloader": "qb", "hash": "missing"}], True)
        self.assertEqual(result["deleted_count"], 1)
        self.assertEqual(self.server.deletions[0]["deleteFiles"], ["True"])

    def test_failed_recheck_never_deletes(self):
        with patch.object(Wrapper, "get_torrents", return_value=([], True)):
            result = PLUGIN._cleanup_missing_torrents_from_preview([{"downloader": "qb", "hash": "missing"}])
        self.assertEqual((result["deleted_count"], result["failed_count"]), (0, 1))
        self.assertEqual(self.server.deletions, [])

    def test_private_client_recovers_without_initialized_shared_session(self):
        self.service.instance.qbc = None
        self.assertEqual(PLUGIN._build_missing_torrent_preview(["qb"])["total_count"], 1)
        self.assertIsNone(self.service.instance.qbc)

    def test_recreated_sessions_remain_direct_and_preserve_security_settings(self):
        with DIRECT.direct_downloader(self.service) as server:
            client = server.qbc
            first = client._session
            client._trigger_session_initialization()
            self.assertIsNot(first, client._session)
            self.assertFalse(client._session.trust_env)
            self.assertTrue(client._VERIFY_WEBUI_CERTIFICATE)
            self.assertEqual(client._EXTRA_HEADERS["X-Test"], "kept")
            self.assertNotIn("Proxy-Authorization", client._EXTRA_HEADERS)
            self.assertEqual(len(server.get_torrents()[0]), 3)

    def test_context_closes_private_session_on_success_and_failure(self):
        for fail in (False, True):
            client = types.SimpleNamespace(_http_session=Mock())
            service = types.SimpleNamespace(instance=types.SimpleNamespace(qbc=client))
            with patch.object(DIRECT, "direct_service", return_value=service):
                try:
                    with DIRECT.direct_downloader(self.service):
                        if fail:
                            raise ValueError("query failed")
                except ValueError:
                    pass
            client._http_session.close.assert_called_once()

    def test_stalled_cleanup_rechecks_tag_and_forces_no_file_deletion(self):
        from test_sitetoolbox_stalled import candidate
        import time
        added = time.time() - 9 * 86400
        self.server.rows = [candidate(added_on=added, last_activity=added),
                            candidate(hash="tag-removed", tags="PT", added_on=added, last_activity=added)]
        items = [{"downloader": "qb", "hash": h} for h in ("candidate", "tag-removed")]
        with patch.object(PLUGIN, "_stalled_policy", return_value={"required_tag": "keep_iyuu", "age_days": 7}):
            result = PLUGIN._cleanup_missing_torrents_from_preview(items, delete_files=True, kind="stalled")
        self.assertEqual((result["deleted_count"], result["skipped_count"]), (1, 1))
        self.assertEqual(self.server.deletions, [{"hashes": ["candidate"], "deleteFiles": ["False"]}])
        self.assertFalse(result["delete_files"])

    def test_scan_keeps_missing_and_stalled_candidates_separate(self):
        from test_sitetoolbox_stalled import candidate
        import time
        added = time.time() - 9 * 86400
        self.server.rows.append(candidate(added_on=added, last_activity=added))
        with patch.object(PLUGIN, "_stalled_policy", return_value={"required_tag": "keep_iyuu", "age_days": 7}):
            preview = PLUGIN._build_missing_torrent_preview(["qb"])
        self.assertEqual(preview["total_count"], 1)
        self.assertEqual(preview["stalled_count"], 1)
        self.assertEqual(preview["items"][0]["hash"], "missing")
        self.assertEqual(preview["stalled_items"][0]["hash"], "candidate")
        self.assertEqual(preview["errors"], [])

    def test_stalled_cleanup_queries_all_tasks_to_protect_new_same_path_peer(self):
        from test_sitetoolbox_stalled import candidate
        import time
        added = time.time() - 9 * 86400
        self.server.rows = [candidate(added_on=added, last_activity=added),
                            candidate(hash="new-original", state="stalledUP", progress=1)]
        with patch.object(Wrapper, "get_torrents", autospec=True, side_effect=lambda server, ids=None: (self.server.rows, False)) as query:
            result = PLUGIN._cleanup_missing_torrents_from_preview([{"downloader": "qb", "hash": "candidate"}], kind="stalled")
        self.assertIsNone(query.call_args.kwargs["ids"])
        self.assertEqual((result["deleted_count"], result["skipped_count"]), (0, 1))
        self.assertEqual(self.server.deletions, [])


if __name__ == "__main__":
    unittest.main()
