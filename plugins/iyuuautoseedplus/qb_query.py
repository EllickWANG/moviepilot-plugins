"""辅种查询与幂等控制使用独立、串行的 qB 会话。"""
from collections.abc import Mapping, Sequence
from threading import RLock
from time import monotonic


def query_error_message(error):
    """只返回错误类别，避免将认证参数和完整请求写入诊断结果。"""
    text = str(error)
    if "UNEXPECTED_EOF_WHILE_READING" in text:
        return "TLS 连接提前断开（UNEXPECTED_EOF_WHILE_READING）"
    if "CERTIFICATE_VERIFY_FAILED" in text:
        return "TLS 证书校验失败（CERTIFICATE_VERIFY_FAILED）"
    name = type(error).__name__
    if "timeout" in name.lower() or "timed out" in text.lower():
        return f"下载器请求超时（{name}）"
    return f"下载器请求失败（{name}）"


class QbQueryClient:
    def __init__(self, source, config, timeout, direct=False):
        self.source = source
        self.config = dict(config)
        self.timeout = timeout
        self.direct = direct
        self._client = None
        self._lock = RLock()
        self._closed = False

    def _new_client(self):
        from qbittorrentapi import Client

        # 保留宿主的证书、请求和认证设置，不改动共享客户端。
        request_args = dict(getattr(self.source, "_REQUESTS_ARGS", None) or {})
        request_args["timeout"] = (min(self.timeout, 15), self.timeout)
        if self.direct:
            # 空协议代理覆盖 Requests 从环境合并的代理，仅作用于该独立客户端。
            request_args["proxies"] = {"http": "", "https": "", "all": ""}
        return Client(
            host=self.config.get("host") or self.source.host,
            port=self.config.get("port"),
            username=self.config.get("username", getattr(self.source, "username", None)),
            password=self.config.get("password"),
            EXTRA_HEADERS=dict(getattr(self.source, "_EXTRA_HEADERS", None) or {}),
            HTTPADAPTER_ARGS=dict(getattr(self.source, "_HTTPADAPTER_ARGS", None) or {}),
            FORCE_SCHEME_FROM_HOST=True,
            VERIFY_WEBUI_CERTIFICATE=getattr(self.source, "_VERIFY_WEBUI_CERTIFICATE", True),
            REQUESTS_ARGS=request_args,
        )

    def _discard_client(self):
        client, self._client = self._client, None
        session = getattr(client, "_http_session", None)
        if session is not None:
            try:
                session.close()
            except Exception:
                # 清理旧连接失败不能掩盖查询结果或阻止重新连接。
                pass

    def close(self):
        # 插件重载不等待正在进行的网络请求；请求结束后自行释放会话。
        self._closed = True
        if self._lock.acquire(blocking=False):
            try:
                self._discard_client()
            finally:
                self._lock.release()

    def query(self, ids=None, status=None, limit=None, tag=None):
        from qbittorrentapi import APIConnectionError
        from requests.exceptions import ConnectionError, Timeout

        started = monotonic()
        result = {"torrents": [], "error": None, "attempts": 0}
        with self._lock:
            try:
                if self._closed:
                    result["error"] = "辅种查询会话已停止"
                    return result
                for attempt in range(1, 3):
                    result["attempts"] = attempt
                    try:
                        if self._client is None:
                            self._client = self._new_client()
                        rows = self._client.torrents_info(
                            torrent_hashes=ids, status_filter=status, limit=limit, tag=tag,
                            requests_args={"timeout": (min(self.timeout, 15), self.timeout)},
                        )
                        # 非列表响应不能当成“种子已不存在”来清空队列。
                        if (not isinstance(rows, Sequence) or isinstance(rows, (str, bytes))
                                or any(not isinstance(row, Mapping) or not row.get("hash") for row in rows)):
                            raise ValueError("Invalid torrent list response")
                        if self._closed:
                            result["error"] = "辅种查询会话已停止"
                        else:
                            result.update(torrents=list(rows), error=None)
                        break
                    except Exception as error:
                        result["error"] = query_error_message(error)
                        self._discard_client()
                        if self._closed or not isinstance(error, (APIConnectionError, ConnectionError, Timeout)):
                            break
            finally:
                result["elapsed"] = round(monotonic() - started, 3)
                if self._closed:
                    self._discard_client()
        return result

    def command(self, method, **params):
        """仅允许辅种需要的幂等控制操作；添加、删除种子不走自动重试。"""
        from qbittorrentapi import APIConnectionError
        from requests.exceptions import ConnectionError, Timeout

        allowed = {"torrents_resume", "torrents_recheck", "torrents_delete_tags", "torrents_set_category"}
        result = {"success": False, "error": None, "attempts": 0}
        started = monotonic()
        if method not in allowed:
            result.update(error="不支持的辅种控制操作", elapsed=0)
            return result
        with self._lock:
            try:
                if self._closed:
                    result["error"] = "辅种连接已停止"
                    return result
                for attempt in range(1, 3):
                    result["attempts"] = attempt
                    try:
                        if self._client is None:
                            self._client = self._new_client()
                        getattr(self._client, method)(
                            **params, requests_args={"timeout": (min(self.timeout, 15), self.timeout)})
                        result.update(success=not self._closed, error="辅种连接已停止" if self._closed else None)
                        break
                    except Exception as error:
                        result["error"] = query_error_message(error)
                        self._discard_client()
                        if self._closed or not isinstance(error, (APIConnectionError, ConnectionError, Timeout)):
                            break
            finally:
                result["elapsed"] = round(monotonic() - started, 3)
                if self._closed:
                    self._discard_client()
        return result


def qb_recheck_state(torrent):
    """仅完整且暂停的任务可启动；缺文件或未完成的任务继续保留等待人工处理。"""
    state = torrent.get("state") or "unknown"
    try:
        progress = float(torrent.get("progress", 0))
    except (TypeError, ValueError):
        progress = 0
    if state in {"error", "missingFiles"}:
        return "blocked", "qB 报告文件缺失或读写错误，请检查文件路径、大小及权限"
    if state in {"checkingUP", "checkingDL", "checkingResumeData", "moving"}:
        return "waiting", "正在校验或移动文件"
    if progress >= 1 and state in {"pausedUP", "stoppedUP"}:
        return "ready", "文件完整，等待启动"
    if progress >= 1 and state in {"uploading", "stalledUP", "queuedUP", "forcedUP"}:
        return "seeding", "任务已启动"
    if state in {"pausedDL", "stoppedDL"}:
        return "blocked", "文件未完整匹配，不能自动启动；请检查路径、文件大小或手动校验"
    return "waiting", "等待文件完整且校验结束"


def probe_network(source, config):
    """仅探测已配置下载器的 DNS、直连 TCP/TLS，不发送认证或操作请求。"""
    import socket
    import ssl
    from urllib.parse import urlparse
    from requests.utils import get_environ_proxies

    host = config.get("host") or source.host
    target = urlparse(host if "://" in host else f"http://{host}")
    port = config.get("port") or target.port or (443 if target.scheme == "https" else 80)
    session = getattr(source, "_http_session", None)
    trust_env = getattr(session, "trust_env", True)
    proxies = get_environ_proxies(host) if trust_env else {}
    explicit = (getattr(source, "_REQUESTS_ARGS", None) or {}).get("proxies") or {}
    report = {
        "host": target.hostname, "port": port, "mode": "direct", "trust_env": trust_env,
        "environment_proxy": any(proxies.get(key) for key in ("http", "https", "all")),
        "explicit_proxy": any(explicit.get(key) for key in ("http", "https", "all")),
        "connections": [],
    }
    started = monotonic()
    try:
        addresses = socket.getaddrinfo(target.hostname, port, type=socket.SOCK_STREAM)
    except Exception as error:
        report.update(dns_error=query_error_message(error), dns_elapsed=round(monotonic() - started, 3))
        return report
    report.update(dns_elapsed=round(monotonic() - started, 3),
                  addresses=list(dict.fromkeys(item[4][0] for item in addresses))[:8])
    seen = set()
    for family, socktype, protocol, _, address in addresses:
        if family in seen or family not in (socket.AF_INET, socket.AF_INET6):
            continue
        seen.add(family)
        result = {"family": "IPv6" if family == socket.AF_INET6 else "IPv4", "address": address[0]}
        started = monotonic()
        try:
            with socket.socket(family, socktype, protocol) as connection:
                connection.settimeout(3)
                connection.connect(address)
                result.update(tcp=True, tcp_elapsed=round(monotonic() - started, 3))
                if target.scheme == "https":
                    # 该诊断始终验证证书，不修改应用客户端已有设置。
                    started = monotonic()
                    with ssl.create_default_context().wrap_socket(connection, server_hostname=target.hostname) as tls:
                        result.update(tls=True, tls_elapsed=round(monotonic() - started, 3), protocol=tls.version())
        except Exception as error:
            result.update(error=query_error_message(error), elapsed=round(monotonic() - started, 3))
        report["connections"].append(result)
    return report
