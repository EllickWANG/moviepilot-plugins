"""辅种查询使用独立、串行的 qB 会话，连接失败时只重试读取操作。"""
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
    return f"下载器查询失败（{name}）"


class QbQueryClient:
    def __init__(self, source, config, timeout):
        self.source = source
        self.config = dict(config)
        self.timeout = timeout
        self._client = None
        self._lock = RLock()
        self._closed = False

    def _new_client(self):
        from qbittorrentapi import Client

        # 保留宿主的证书、请求和认证设置，不改动共享客户端。
        request_args = dict(getattr(self.source, "_REQUESTS_ARGS", None) or {})
        request_args["timeout"] = (min(self.timeout, 15), self.timeout)
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

    def query(self, ids=None, status=None, limit=None):
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
                            torrent_hashes=ids, status_filter=status, limit=limit,
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
