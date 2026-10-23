import hashlib
import json
import time
from typing import Tuple, Optional

from app.utils.http import RequestUtils


class IyuuHelper(object):
    """
    适配新版本IYUU开发版
    """
    _version = "8.2.0"
    _api_base = "https://2025.iyuu.cn"
    _sites = {}
    _token = None
    _sid_sha1 = None

    def __init__(self, token: str, timeout: int = 60):
        self._token = token
        self._timeout = timeout or 60
        self._sites = {}
        self._sid_sha1 = None
        self._last_error = ""
        self._last_request = {}
        self._retryable = False
        if self._token:
            self.init_config()

    def init_config(self):
        pass

    def __request_iyuu(self, url: str, method: str = "get", params: dict = None) -> Tuple[Optional[dict], str]:
        """
        向IYUUApi发送请求
        """
        started = time.monotonic()
        self._retryable = False
        self._last_request = {"path": url}
        try:
            request = RequestUtils(headers={'token': self._token, 'Accept': 'application/json'}, timeout=self._timeout)
            if method == "post":
                ret = request.post_res(self._api_base + url, json=params)
            else:
                ret = request.get_res(self._api_base + url, params=params)
            if ret is None:
                self._retryable = True
                return None, f"IYUU接口 {url} 未获取到返回信息（超时或网络异常，timeout={self._timeout}秒）"
            self._last_request["http_status"] = ret.status_code
            if ret.status_code >= 400:
                self._retryable = ret.status_code == 429 or ret.status_code >= 500
                message = ""
                try:
                    body = ret.json()
                    if isinstance(body, dict):
                        message = str(body.get('msg') or body.get('message') or '')[:500]
                        if self._token:
                            message = message.replace(self._token, '<token>')
                except (TypeError, ValueError):
                    pass
                return None, f"IYUU接口 {url} 返回HTTP {ret.status_code}：{message}"
            try:
                result = ret.json()
            except (ValueError, TypeError):
                self._retryable = True
                return None, f"IYUU接口 {url} 返回非JSON内容（HTTP {ret.status_code}）"
            if not isinstance(result, dict):
                return None, f"IYUU接口 {url} 返回格式错误（{type(result).__name__}）"
            self._last_request["code"] = result.get('code')
            if result.get('code') in (0, "0"):
                return result.get('data'), ""
            message = str(result.get('msg') or result.get('message') or '无错误说明')[:500]
            if self._token:
                message = message.replace(self._token, '<token>')
            self._retryable = str(result.get('code')) in {"429", "500", "502", "503", "504"}
            return None, f'IYUU接口 {url} 业务错误 {result.get("code")}：{message}'
        except Exception as error:
            self._retryable = True
            return None, f"IYUU接口 {url} 请求异常（{type(error).__name__}）"
        finally:
            self._last_request["elapsed"] = round(time.monotonic() - started, 3)

    def get_torrent_url(self, sid: str) -> Tuple[Optional[str], Optional[str]]:
        if not sid:
            return None, None
        if not self._sites:
            self._sites, self._last_error = self.__get_sites()
        if not self._sites.get(sid):
            return None, None
        site = self._sites.get(sid)
        return site.get('base_url'), site.get('download_page')

    def __get_sites(self) -> Tuple[dict, str]:
        """
        返回支持辅种的全部站点
        :return: 站点列表、错误信息
        """
        result, msg = self.__request_iyuu(url='/reseed/sites/index')
        if isinstance(result, dict) and isinstance(result.get('sites'), list) and result['sites']:
            ret_sites = {}
            sites = result.get('sites')
            for site in sites:
                if not isinstance(site, dict) or site.get('id') is None:
                    return {}, "IYUU站点列表响应包含无效站点ID"
                ret_sites[site.get('id')] = site
            return ret_sites, ""
        else:
            return {}, msg or "IYUU站点列表响应缺少有效 sites"

    def __report_existing(self) -> Tuple[Optional[str], str]:
        """
        汇报辅种的站点
        :return:
        """
        if not self._sites:
            self._sites, msg = self.__get_sites()
            if not self._sites:
                return None, msg
        sid_list = list(self._sites.keys())
        result, msg = self.__request_iyuu(url='/reseed/sites/reportExisting',
                                          method='post',
                                          params={'sid_list': sid_list})
        if isinstance(result, dict) and isinstance(result.get('sid_sha1'), str) and result['sid_sha1'].strip():
            return result['sid_sha1'], ""
        return None, msg or "IYUU接口 /reseed/sites/reportExisting 响应缺少 sid_sha1"

    def prepare(self) -> Tuple[bool, str]:
        """在扫描下载器前初始化站点哈希，保留失败接口和真实业务原因。"""
        if not self._sid_sha1:
            self._sid_sha1, self._last_error = self.__report_existing()
        return bool(self._sid_sha1), self._last_error

    def status(self) -> dict:
        return {"ready": bool(self._sid_sha1), "sites_count": len(self._sites),
                "error": self._last_error, "retryable": self._retryable, "request": dict(self._last_request)}

    def get_seed_info(self, info_hashs: list) -> Tuple[Optional[dict], str]:
        """
        返回info_hash对应的站点id、种子id
        :param info_hashs:
        :return:
        """
        info_hashs = [str(info_hash).strip().lower() for info_hash in info_hashs if info_hash]
        if not info_hashs:
            return None, "请求IYUU失败，未提供种子Hash"
        if not self._sid_sha1:
            self.prepare()
        if not self._sid_sha1:
            return None, f"IYUU站点初始化失败：{self._last_error}"
        info_hashs.sort()
        json_data = json.dumps(info_hashs, separators=(',', ':'), ensure_ascii=False)
        sha1 = self.get_sha1(json_data)
        result, msg = self.__request_iyuu(url='/reseed/index/index', method='post', params={
            'hash': json_data,
            'sha1': sha1,
            'sid_sha1': self._sid_sha1,
            'timestamp': int(time.time()),
            'version': self._version
        })
        return result, msg

    @staticmethod
    def get_sha1(json_str: str) -> str:
        return hashlib.sha1(json_str.encode('utf-8')).hexdigest()
