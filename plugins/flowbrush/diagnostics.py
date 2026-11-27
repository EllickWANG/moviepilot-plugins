"""刷流的只读连接诊断；不复用或修改宿主下载器会话。"""

import platform
import os
import queue
import socket
import ssl
import threading
from importlib.metadata import PackageNotFoundError, version
from time import monotonic
from urllib.parse import urlsplit, urlunsplit

import requests


def error_summary(error):
    """错误正文可能包含认证 URL、Cookie 或请求头，只输出类别和已知错误码。"""
    text = str(error).lower()
    for marker, label in (
        ("unexpected_eof_while_reading", "TLS 连接提前断开"),
        ("certificate_verify_failed", "TLS 证书校验失败"),
        ("connection refused", "TCP 连接被拒绝"),
        ("connection reset", "连接被重置"),
        ("timed out", "请求超时"),
        ("name or service not known", "DNS 解析失败"),
    ):
        if marker in text:
            return {"type": type(error).__name__, "reason": label, "code": marker.upper()}
    return {"type": type(error).__name__, "reason": "请求失败，原始异常正文已省略"}


def normalize_add_result(result):
    """兼容旧版 bool 和新版 (成功状态, Hash 列表)，失败元组不能视为成功。"""
    if isinstance(result, tuple) and len(result) == 2:
        success, hashes = result
        values = hashes if isinstance(hashes, (list, tuple)) else []
        return bool(success), [h for h in values if isinstance(h, str) and h] if success else []
    return result is True, []


def _resolve(host, port, timeout=3):
    # getaddrinfo 不受 socket 超时控制；限制等待时间且不阻塞插件重载。
    result = queue.Queue(maxsize=1)

    def resolve():
        try:
            result.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except Exception as error:
            result.put(error)

    threading.Thread(target=resolve, daemon=True).start()
    try:
        value = result.get(timeout=timeout)
    except queue.Empty as error:
        raise TimeoutError("DNS resolution timed out") from error
    if isinstance(value, Exception):
        raise value
    return value


def _http_probe(url, source):
    """新建无认证会话，使用当前客户端的代理选择；HTTP 401/403 也能证明 TLS 通。"""
    started = monotonic()
    with requests.Session() as session:
        existing = getattr(source, "_http_session", None)
        session.trust_env = getattr(existing, "trust_env", True)
        # trust_env 也会读取 .netrc；探测仅继承代理路由，禁止隐式发送站点账号。
        session.auth = lambda request: request
        session.proxies = dict(getattr(existing, "proxies", None) or {})
        args = dict(getattr(source, "_REQUESTS_ARGS", None) or {})
        # 仅复用路由设置，不复制账号、请求头、Cookie 或客户端证书。
        explicit = dict(args.get("proxies") or {})
        merged = session.merge_environment_settings(url, explicit, True, True, None)
        proxy = requests.utils.select_proxy(url, merged["proxies"])
        report = {"mode": "fresh_session_no_auth", "trust_env": session.trust_env,
                  "proxy_selected": bool(proxy), "certificate_verification": True}
        if proxy:
            # 不输出代理 userinfo、路径或查询参数。
            parsed = urlsplit(proxy)
            report["proxy"] = {"scheme": parsed.scheme, "host": parsed.hostname, "port": parsed.port}
        try:
            # 强制校验证书，禁止跟随重定向，不读取响应正文。
            with session.get(url, timeout=(3, 3), allow_redirects=False, stream=True,
                             proxies=explicit, verify=True) as response:
                report.update(reachable=True, http_status=response.status_code)
        except Exception as error:
            report.update(reachable=False, error=error_summary(error))
        report["elapsed"] = round(monotonic() - started, 3)
        return report


def diagnose_connection(source, config):
    """探测已配置下载器的 DNS、每种地址族的首个 TCP/TLS 地址及实际代理路径。"""
    runtime = {"python": platform.python_version(), "openssl": ssl.OPENSSL_VERSION}
    for name in ("qbittorrent-api", "requests", "urllib3"):
        try:
            runtime[name] = version(name)
        except PackageNotFoundError:
            runtime[name] = "unknown"
    report = {"runtime": runtime, "connections": [], "read_only": True}
    try:
        host = config.get("host") or getattr(source, "host", "")
        target = urlsplit(host if "://" in host else f"http://{host}")
        if target.scheme not in {"http", "https"} or not target.hostname:
            raise ValueError("Invalid configured downloader address")
        port = int(config.get("port") or target.port or (443 if target.scheme == "https" else 80))
        hostname = target.hostname
        authority = f"[{hostname}]" if ":" in hostname else hostname
        # URL 中的账号、查询参数和片段均不带入探测请求。
        url = urlunsplit((target.scheme, f"{authority}:{port}",
                          target.path.rstrip("/") + "/api/v2/app/webapiVersion", "", ""))
        report.update(target={"scheme": target.scheme, "host": hostname, "port": port},
                      client_certificate_verification=getattr(source, "_VERIFY_WEBUI_CERTIFICATE", None),
                      client_session_present=getattr(source, "_http_session", None) is not None)
        args = getattr(source, "_REQUESTS_ARGS", None) or {}
        timeout = args.get("timeout")
        if isinstance(timeout, (int, float)) or (
                isinstance(timeout, (list, tuple)) and all(isinstance(v, (int, float)) for v in timeout)):
            report["client_timeout"] = timeout
        report["proxy_environment_keys"] = sorted(
            key for key in os.environ if key.lower() in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"})
        started = monotonic()
        try:
            addresses = _resolve(hostname, port)
            report["dns"] = {"addresses": list(dict.fromkeys(a[4][0] for a in addresses))[:8]}
        except Exception as error:
            addresses = []
            report["dns"] = {"error": error_summary(error)}
        report["dns"]["elapsed"] = round(monotonic() - started, 3)
        seen = set()
        for family, socktype, protocol, _, address in addresses:
            if family in seen or family not in {socket.AF_INET, socket.AF_INET6}:
                continue
            seen.add(family)
            result = {"mode": "direct", "family": "IPv6" if family == socket.AF_INET6 else "IPv4",
                      "address": address[0], "stage": "tcp"}
            started = monotonic()
            try:
                with socket.socket(family, socktype, protocol) as connection:
                    connection.settimeout(3)
                    connection.connect(address)
                    result.update(tcp=True, tcp_elapsed=round(monotonic() - started, 3))
                    if target.scheme == "https":
                        result["stage"] = "tls"
                        started = monotonic()
                        with ssl.create_default_context().wrap_socket(connection, server_hostname=hostname) as tls:
                            result.update(tls=True, tls_elapsed=round(monotonic() - started, 3),
                                          protocol=tls.version(), certificate_expires=tls.getpeercert().get("notAfter"))
            except Exception as error:
                result.update(error=error_summary(error), elapsed=round(monotonic() - started, 3))
            report["connections"].append(result)
        report["http"] = _http_probe(url, source)
    except Exception as error:
        report["error"] = error_summary(error)
    return report
