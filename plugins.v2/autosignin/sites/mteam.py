import base64
import json
import re
import time
from typing import Tuple

from ruamel.yaml import CommentedMap

from app.core.config import settings
from app.plugins.autosignin.sites import _ISiteSigninHandler
from app.utils.http import RequestUtils
from app.utils.string import StringUtils


class MTorrent(_ISiteSigninHandler):
    """
    m-team签到
    """
    # 匹配的站点Url，每一个实现类都需要设置为自己的站点Url
    site_url = "m-team"

    @classmethod
    def match(cls, url: str) -> bool:
        """
        根据站点Url判断是否匹配当前站点签到类，大部分情况使用默认实现即可
        :param url: 站点Url
        :return: 是否匹配，如匹配则会调用该类的signin方法
        """
        return True if cls.site_url in url.split(".") else False

    def signin(self, site_info: CommentedMap) -> Tuple[bool, str]:
        """
        执行签到操作，馒头实际没有签到；优先检查真实网页登录历史，避免把 API 重定向误判为成功
        :param site_info: 站点信息，含有站点Url、站点Cookie、UA等信息
        :return: 签到结果信息
        """
        if site_info.get("apikey"):
            return self.__check_login_history(site_info)

        token = site_info.get("token")
        if not token:
            return False, "模拟登录失败，未配置 M-Team API Access Token 或 Authorization token"
        expired, expired_at = self.__token_expired(token)
        if expired:
            return False, f"模拟登录失败，Authorization token 已过期（{expired_at}），请浏览器重新登录后更新站点 Token"

        headers = {
            "Content-Type": "application/json",
            "User-Agent": site_info.get("ua") or settings.USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Authorization": token
        }
        url = site_info.get('url')
        timeout = site_info.get("timeout")
        domain = StringUtils.get_url_domain(url)
        # 更新最后访问时间
        res = RequestUtils(headers=headers,
                           timeout=timeout,
                           proxies=settings.PROXY if site_info.get("proxy") else None,
                           referer=f"{url}index"
                           ).post_res(url=f"https://api.{domain}/api/member/updateLastBrowse",
                                      allow_redirects=False)
        if res is None:
            return False, "模拟登录失败，无法打开网站"
        if 300 <= int(res.status_code or 0) < 400:
            return False, f"模拟登录失败，接口被重定向：{res.headers.get('Location') or res.status_code}"
        if res.status_code != 200:
            return False, f"模拟登录失败，状态码：{res.status_code}"
        try:
            data = res.json() or {}
        except Exception:
            return False, "模拟登录失败，接口返回非 JSON 内容"
        if data.get("code") not in (None, 0, "0"):
            return False, f"模拟登录失败，{data.get('message') or data.get('msg') or data.get('code')}"
        return True, "模拟浏览成功"

    def login(self, site_info: CommentedMap) -> Tuple[bool, str]:
        """
        执行登录操作
        :param site_info: 站点信息，含有站点Url、站点Cookie、UA等信息
        :return: 登录结果信息
        """
        return self.signin(site_info)

    @staticmethod
    def __check_login_history(site_info: CommentedMap) -> Tuple[bool, str]:
        url = site_info.get('url')
        timeout = site_info.get("timeout")
        domain = StringUtils.get_url_domain(url)
        headers = {
            "Content-Type": "application/json",
            "User-Agent": site_info.get("ua") or settings.USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "x-api-key": site_info.get("apikey")
        }
        uid = ""
        profile_res = RequestUtils(headers=headers,
                                   timeout=timeout,
                                   proxies=settings.PROXY if site_info.get("proxy") else None,
                                   referer=f"{url}index"
                                   ).post_res(url=f"https://api.{domain}/api/member/profile",
                                              json={},
                                              allow_redirects=False)
        if profile_res and profile_res.status_code == 200:
            try:
                profile_data = (profile_res.json() or {}).get("data") or {}
                uid = str(profile_data.get("id") or "").strip()
            except Exception:
                uid = ""
        payload = {"pageNumber": 1, "pageSize": 10}
        if uid:
            payload["uid"] = int(uid) if uid.isdigit() else uid
        history_headers = dict(headers)
        history_headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        res = RequestUtils(headers=history_headers,
                           timeout=timeout,
                           proxies=settings.PROXY if site_info.get("proxy") else None,
                           referer=f"{url}index"
                           ).post_res(url=f"https://api.{domain}/api/member/queryUserLoginHistory",
                                      data=payload,
                                      allow_redirects=False)
        if res is None:
            return False, "登录历史检查失败，无法打开 M-Team API"
        if 300 <= int(res.status_code or 0) < 400:
            return False, f"登录历史检查失败，接口被重定向：{res.headers.get('Location') or res.status_code}"
        if res.status_code != 200:
            return False, f"登录历史检查失败，状态码：{res.status_code}"
        try:
            data = res.json() or {}
        except Exception:
            return False, "登录历史检查失败，接口返回非 JSON 内容"
        if data.get("code") not in (None, 0, "0"):
            return False, f"登录历史检查失败，{data.get('message') or data.get('msg') or data.get('code')}"
        records = MTorrent.__history_records(data)
        if not records:
            return False, "登录历史检查失败，API 未返回登录历史"
        latest_time, latest_ts = MTorrent.__latest_login_time(records)
        if not latest_ts:
            return True, "登录历史可读取，但无法解析最近登录时间"
        days = max(0, int((time.time() - latest_ts) // 86400))
        if days > 25:
            return False, f"最近网页登录已超过 25 天（{latest_time}），请亲自浏览器登录"
        return True, f"最近网页登录 {days} 天前（{latest_time}）"

    @staticmethod
    def __history_records(payload: dict) -> list:
        data = payload.get("data")
        candidates = [data]
        if isinstance(data, dict):
            candidates.extend(data.get(key) for key in ("data", "records", "list", "items", "content", "rows"))
        for candidate in candidates:
            if isinstance(candidate, list):
                return [item for item in candidate if isinstance(item, dict)]
        return []

    @staticmethod
    def __latest_login_time(records: list) -> Tuple[str, float]:
        candidates = []
        keys = (
            "loginDate",
            "loginTime",
            "loginAt",
            "lastLogin",
            "lastLoginTime",
            "createdDate",
            "createdAt",
            "createTime",
            "time",
            "date",
        )
        for record in records:
            value = MTorrent.__find_named_value(record, keys)
            ts = MTorrent.__parse_time(value)
            text = MTorrent.__format_time(ts) if ts else str(value or "").strip()
            if text or ts:
                candidates.append((text, ts))
        parsed = [item for item in candidates if item[1]]
        if parsed:
            return max(parsed, key=lambda item: item[1])
        return candidates[0] if candidates else ("", 0)

    @staticmethod
    def __find_named_value(value, names: Tuple[str, ...]):
        if isinstance(value, dict):
            lower_names = {name.lower() for name in names}
            for key, item in value.items():
                if str(key).lower() in lower_names and item not in (None, ""):
                    return item
            for item in value.values():
                found = MTorrent.__find_named_value(item, names)
                if found not in (None, ""):
                    return found
        elif isinstance(value, list):
            for item in value:
                found = MTorrent.__find_named_value(item, names)
                if found not in (None, ""):
                    return found
        return None

    @staticmethod
    def __token_expired(token: str) -> Tuple[bool, str]:
        raw_token = str(token or "").strip()
        if raw_token.lower().startswith("bearer "):
            raw_token = raw_token.split(" ", 1)[1].strip()
        parts = raw_token.split(".")
        if len(parts) < 2:
            return False, ""
        try:
            payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)))
        except Exception:
            return False, ""
        exp = payload.get("exp")
        try:
            exp_ts = float(exp)
        except (TypeError, ValueError):
            return False, ""
        return exp_ts <= time.time(), MTorrent.__format_time(exp_ts)

    @staticmethod
    def __parse_time(value) -> float:
        if value in (None, ""):
            return 0
        if isinstance(value, (int, float)):
            number = float(value)
            if number > 10_000_000_000:
                number /= 1000
            return number if number > 0 else 0
        text = str(value).strip()
        if re.fullmatch(r"\d{10,13}", text):
            return MTorrent.__parse_time(int(text))
        normalized = text.replace("T", " ").replace("Z", "").split("+", 1)[0].strip()
        normalized = re.sub(r"\.\d+", "", normalized)
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y-%m-%d"):
            try:
                return time.mktime(time.strptime(normalized, fmt))
            except Exception:
                continue
        return 0

    @staticmethod
    def __format_time(timestamp: float) -> str:
        try:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(timestamp)))
        except Exception:
            return ""
