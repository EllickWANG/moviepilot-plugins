"""使用本地隔离 qB HTTP 服务验证代理绕过、认证及宿主会话隔离。"""
import json
import os
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import parse_qs

from qbittorrentapi import Client
from requests.utils import select_proxy
from test_flowbrush_diagnostics import DIRECT


class FakeQb(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.server.requests.append(self.path)
        path = self.path.split("?", 1)[0]
        if path in {"/api/v2/torrents/info", "/api/v2/transfer/info"} and "SID=isolated-test" not in self.headers.get("Cookie", ""):
            self.send_error(403)
            return
        body = {
            "/api/v2/app/webapiVersion": "2.9.3",
            "/api/v2/app/version": "v4.6.4",
            "/api/v2/torrents/info": '[{"hash":"isolated-test","name":"test"}]',
            "/api/v2/transfer/info": '{"up_info_speed":12,"dl_info_speed":34}',
        }.get(path)
        if body is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json" if body[0] in "[{" else "text/plain")
        self.end_headers()
        self.wfile.write(body.encode())

    def do_POST(self):
        data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if self.path in {"/api/v2/torrents/info", "/api/v2/transfer/info"}:
            self.do_GET()
            return
        self.server.requests.append(self.path)
        if self.path != "/api/v2/auth/login":
            self.send_error(405)
            return
        credentials = parse_qs(data.decode())
        if credentials != {"username": ["test"], "password": ["test-secret"]}:
            self.send_error(403)
            return
        self.send_response(200)
        self.send_header("Set-Cookie", "SID=isolated-test; Path=/")
        self.end_headers()
        self.wfile.write(b"Ok.")


class Wrapper:
    def __init__(self, qbc):
        self.qbc = qbc

    def get_torrents(self):
        return self.qbc.torrents_info()


class DirectClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeQb)
        cls.server.requests = []
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.config = {"host": self.url, "username": "test", "password": "test-secret"}
        self.source = Client(host=self.url, VERIFY_WEBUI_CERTIFICATE=True,
                             EXTRA_HEADERS={"X-Test": "preserved", "Proxy-Authorization": "proxy-secret"},
                             REQUESTS_ARGS={"timeout": (3, 10), "proxies": {"http": "http://127.0.0.1:1"}})
        self.service = types.SimpleNamespace(name="test", instance=Wrapper(self.source),
                                             config=types.SimpleNamespace(config=self.config))
        self.env = patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:1",
                                          "HTTPS_PROXY": "http://127.0.0.1:1",
                                          "ALL_PROXY": "http://127.0.0.1:1", "NO_PROXY": "", "no_proxy": ""})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_real_client_reaches_server_with_bad_environment_and_explicit_proxies(self):
        private = DIRECT.direct_service(self.service)
        private.instance.qbc.auth_log_in()
        self.assertEqual(private.instance.get_torrents()[0]["hash"], "isolated-test")
        self.assertIsNot(private, self.service)
        self.assertIsNot(private.instance, self.service.instance)
        self.assertIs(self.service.instance.qbc, self.source)
        self.assertIsNone(self.source._http_session)
        self.assertEqual(self.source._REQUESTS_ARGS["proxies"], {"http": "http://127.0.0.1:1"})
        self.assertEqual(os.environ["HTTPS_PROXY"], "http://127.0.0.1:1")
        self.assertNotIn("Proxy-Authorization", private.instance.qbc._EXTRA_HEADERS)
        self.assertEqual(private.instance.qbc._EXTRA_HEADERS["X-Test"], "preserved")
        self.assertTrue(private.instance.qbc._VERIFY_WEBUI_CERTIFICATE)
        private.instance.qbc._session.close()

    def test_recreated_session_still_has_no_proxy(self):
        client = DIRECT.direct_service(self.service).instance.qbc
        first = client._session
        client._trigger_session_initialization()
        second = client._session
        self.assertIsNot(first, second)
        self.assertFalse(second.trust_env)
        merged = second.merge_environment_settings(self.url, {}, False, None, None)
        self.assertFalse(select_proxy(self.url, merged["proxies"]))
        self.assertEqual(client.torrents_info()[0]["hash"], "isolated-test")
        second.close()

    def test_authenticated_diagnostic_is_read_only_and_accepts_qb_wrappers(self):
        self.server.requests.clear()
        report = DIRECT.authenticated_probe(self.service)
        self.assertTrue(report["success"], report)
        self.assertEqual(report["sample_count"], 1)
        self.assertEqual(report["upload_speed"], 12)
        self.assertTrue(all(path.split("?", 1)[0] in {
            "/api/v2/app/version", "/api/v2/app/webapiVersion", "/api/v2/torrents/info",
            "/api/v2/transfer/info", "/api/v2/auth/login"} for path in self.server.requests))
        self.assertNotIn("test-secret", json.dumps(report))
        self.assertIsNone(self.source._http_session)

    def test_uninitialized_shared_qb_can_be_recovered_by_private_connection(self):
        self.service.instance.qbc = None
        private = DIRECT.direct_service(self.service)
        self.assertEqual(private.instance.get_torrents()[0]["hash"], "isolated-test")
        self.assertIsNone(self.service.instance.qbc)
        private.instance.qbc._session.close()


if __name__ == "__main__":
    unittest.main()
