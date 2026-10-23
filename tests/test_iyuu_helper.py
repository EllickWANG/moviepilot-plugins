"""IYUU 初始化、错误归因与站点哈希协议回归。"""
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


HTTP = Mock()
module = types.ModuleType("app.utils.http")
module.RequestUtils = HTTP
spec = importlib.util.spec_from_file_location("iyuu_helper_test", Path(__file__).resolve().parents[1] / "plugins.v2/iyuuautoseedplus/iyuu_helper.py")
HELPER = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {"app.utils.http": module}):
    spec.loader.exec_module(HELPER)


def response(data=None, code=0, message="ok", status=200):
    return Mock(status_code=status, json=Mock(return_value={"code": code, "msg": message, "data": data}))


class IyuuInitializationTest(unittest.TestCase):
    def setUp(self):
        HTTP.reset_mock()
        self.request = HTTP.return_value
        self.request.get_res.side_effect = None
        self.request.post_res.side_effect = None
        self.request.get_res.return_value = response({"sites": [{"id": 1, "base_url": "example.com"}]})
        self.request.post_res.return_value = response({"sid_sha1": "site-hash"})
        self.helper = HELPER.IyuuHelper("test-token", timeout=50)

    def test_prepare_once_then_query_sends_required_site_hash(self):
        self.request.post_res.side_effect = [response({"sid_sha1": "site-hash"}), response({"a": {"torrent": []}})]
        self.assertEqual(self.helper.prepare(), (True, ""))
        result, error = self.helper.get_seed_info(["A"])
        self.assertEqual(result, {"a": {"torrent": []}})
        self.assertEqual(error, "")
        self.assertEqual(self.request.get_res.call_count, 1)
        self.assertEqual(self.request.post_res.call_args.kwargs["json"]["sid_sha1"], "site-hash")
        self.assertEqual(HTTP.call_args.kwargs["headers"]["Accept"], "application/json")

    def test_report_business_error_is_preserved_and_not_called_network_timeout(self):
        self.request.post_res.return_value = response(code=401, message="invalid test-token")
        ready, error = self.helper.prepare()
        self.assertFalse(ready)
        self.assertIn("/reseed/sites/reportExisting", error)
        self.assertIn("业务错误 401", error)
        self.assertNotIn("test-token", error)
        self.assertFalse(self.helper.status()["retryable"])

    def test_sites_failure_does_not_post_empty_site_list(self):
        self.request.get_res.return_value = response(status=503)
        self.assertFalse(self.helper.prepare()[0])
        self.request.post_res.assert_not_called()
        self.assertTrue(self.helper.status()["retryable"])
        self.assertIn("/reseed/sites/index", self.helper.status()["error"])

    def test_missing_hash_in_success_response_has_specific_error(self):
        self.request.post_res.return_value = response({})
        ready, error = self.helper.prepare()
        self.assertFalse(ready)
        self.assertIn("响应缺少 sid_sha1", error)
        self.assertNotIn("网络异常", error)

    def test_invalid_json_is_reported_without_crashing(self):
        self.request.get_res.return_value = Mock(status_code=200, json=Mock(side_effect=ValueError("html")))
        self.assertFalse(self.helper.prepare()[0])
        self.assertIn("非JSON", self.helper.status()["error"])
        self.assertTrue(self.helper.status()["retryable"])

    def test_invalid_site_id_does_not_generate_an_invalid_report(self):
        self.request.get_res.return_value = response({"sites": [{"name": "missing ID"}]})
        self.assertFalse(self.helper.prepare()[0])
        self.request.post_res.assert_not_called()

    def test_failed_initialization_does_not_query_seed_endpoint(self):
        self.request.post_res.return_value = response(code=403, message="not authorized")
        result, error = self.helper.get_seed_info(["A"])
        self.assertIsNone(result)
        self.assertIn("初始化失败", error)
        self.assertEqual(self.request.post_res.call_count, 1)
        self.assertTrue(self.request.post_res.call_args.args[0].endswith("/reseed/sites/reportExisting"))


if __name__ == "__main__":
    unittest.main()
