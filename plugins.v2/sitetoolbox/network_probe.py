"""馒头固定接口的只读网络诊断，不返回认证内容或账户完整响应。"""
from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

import requests
import urllib3

API_HOSTS = ("api.m-team.cc", "api.m-team.io")
SITE_HOSTS = (*API_HOSTS, "kp.m-team.cc", "kp.m-team.io", "rss.m-team.cc")
PROFILE_PATH = "/api/member/profile"


def safe_proxy(value):
    """仅返回代理连接位置，避免输出用户名、口令和查询参数。"""
    try:
        parsed = urlsplit(str(value or ""))
        return {"scheme": parsed.scheme, "host": parsed.hostname, "port": parsed.port}
    except ValueError:
        return {"configured": bool(value)}


def resolve_host(host):
    try:
        return {"addresses": sorted({item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})}
    except OSError as err:
        return {"error": type(err).__name__}


def hosts_overrides(text):
    matches = []
    for line in text.splitlines():
        parts = line.split("#", 1)[0].split()
        if len(parts) < 2:
            continue
        names = [name for name in parts[1:] if name.lower() in SITE_HOSTS]
        if names:
            matches.append({"address": parts[0], "names": names})
    return matches


def _summary(status, headers, body):
    text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
    result = {"status": status, "bytes": len(body),
              "sha256": hashlib.sha256(body if isinstance(body, bytes) else body.encode()).hexdigest(),
              "headers": {name: headers.get(name) for name in ("Server", "Content-Type", "Via", "CF-Ray") if headers.get(name)}}
    try:
        import json
        payload = json.loads(text)
    except (ValueError, TypeError):
        payload = None
    if isinstance(payload, dict):
        data = payload.get("data")
        # 不返回完整 profile，它可能包含地址、令牌或其他隐私字段。
        result["json"] = {"code": payload.get("code"), "has_user": isinstance(data, dict) and bool(data.get("username"))}
    else:
        title = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
        result["title"] = re.sub(r"\s+", " ", title.group(1)).strip()[:100] if title else ""
    return result


def _request(host, mode, headers, proxies, request_factory):
    started = time.monotonic()
    result = {"host": host, "mode": mode}
    response = None
    session = None
    try:
        url = f"https://{host}{PROFILE_PATH}"
        if mode == "core":
            response = request_factory(headers=dict(headers), proxies=proxies, timeout=8).post_res(url=url, data={}, allow_redirects=False, stream=True)
            if response is None:
                return {**result, "error": "NoResponse"}
        else:
            session = requests.Session()
            # direct/configured 两组都排除容器 HTTP(S)_PROXY、NETRC 和 NO_PROXY 的隐式影响。
            session.trust_env = False
            response = session.post(url, headers=dict(headers), data={}, proxies=proxies if mode == "configured" else {},
                                    timeout=(5, 8), verify=True, allow_redirects=False, stream=True)
        connection = getattr(getattr(response, "raw", None), "_connection", None)
        sock = getattr(connection, "sock", None)
        if sock:
            result["peer"] = list(sock.getpeername())
            cert = sock.getpeercert()
            result["certificate_issuer"] = [list(pair) for group in cert.get("issuer", ()) for pair in group]
        body = response.raw.read(65537, decode_content=True)
        result.update(_summary(response.status_code, response.headers, body[:65536]))
        result["truncated"] = len(body) > 65536
    except Exception as err:
        # requests 异常字符串可能带代理口令，只保留类型与 TLS/CONNECT 分类。
        result["error"] = type(err).__name__
        result["tls_error"] = isinstance(err, requests.exceptions.SSLError)
        result["proxy_error"] = isinstance(err, requests.exceptions.ProxyError)
    finally:
        if response is not None:
            response.close()
        if session is not None:
            session.close()
        result["seconds"] = round(time.monotonic() - started, 3)
    return result


def _public_dns(host):
    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.get("https://dns.google/resolve", params={"name": host, "type": "A"}, timeout=(5, 8))
            response.raise_for_status()
            payload = response.json()
            return {"status": payload.get("Status"), "addresses": [row["data"] for row in payload.get("Answer", []) if row.get("type") == 1]}
    except Exception as err:
        return {"error": type(err).__name__}


def _pinned(host, address, headers):
    """连接公共 DNS 的 IP，同时保留原域名 SNI 和证书校验，避免信任本机错误解析。"""
    result = {"host": host, "mode": "public_dns_pinned", "address": address}
    pool = None
    try:
        if host not in API_HOSTS or not ipaddress.ip_address(address).is_global:
            return {**result, "error": "InvalidDestination"}
        pool = urllib3.HTTPSConnectionPool(address, port=443, server_hostname=host, assert_hostname=host,
                                           cert_reqs="CERT_REQUIRED", ca_certs=requests.certs.where(),
                                           timeout=urllib3.Timeout(connect=5, read=8), retries=False)
        response = pool.request("POST", PROFILE_PATH, headers={**headers, "Host": host}, body=b"", redirect=False, preload_content=False)
        try:
            body = response.read(65537)
            result.update(_summary(response.status, response.headers, body[:65536]))
        finally:
            response.release_conn()
    except Exception as err:
        result["error"] = type(err).__name__
    finally:
        if pool:
            pool.close()
    return result


def probe_mteam(site, settings, request_factory):
    from app.utils.string import StringUtils

    domain = StringUtils.get_url_domain(site.url or "")
    if domain not in {"m-team.cc", "m-team.io"}:
        raise ValueError("只允许诊断馒头站点")
    if not site.apikey:
        raise ValueError("未配置馒头 API Key")
    selected_host = f"api.{domain}"
    proxies = settings.PROXY if site.proxy else None
    headers = {"User-Agent": site.ua or settings.USER_AGENT,
               "Accept": "application/json, text/plain, */*", "x-api-key": site.apikey}
    report = {"site_id": site.id, "site_proxy": bool(site.proxy), "selected_host": selected_host,
              "api_key_configured": True, "proxy": safe_proxy(settings.PROXY_HOST),
              "proxy_mapping": {name: safe_proxy(value) for name, value in (settings.PROXY or {}).items()},
              "environment_proxies": {key: safe_proxy(os.environ[key]) for key in
                                      ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy") if os.environ.get(key)},
              "system_dns": {host: resolve_host(host) for host in SITE_HOSTS},
              "proxy_dns": resolve_host(urlsplit(settings.PROXY_HOST or "").hostname) if settings.PROXY_HOST else None}
    try:
        report["hosts_overrides"] = hosts_overrides(Path("/etc/hosts").read_text())
    except OSError:
        report["hosts_overrides"] = []
    tasks = [(selected_host, "core"), *[(host, mode) for host in API_HOSTS for mode in ("direct", "configured")]]
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(lambda pair: _request(pair[0], pair[1], headers, proxies, request_factory), tasks))
    report["requests"] = results
    report["public_dns"] = {host: _public_dns(host) for host in API_HOSTS}
    # 两个域名各取一个公共地址，避免对站点做无界或高并发请求。
    report["pinned_requests"] = [_pinned(host, values["addresses"][0], headers)
                                 for host, values in report["public_dns"].items() if values.get("addresses")]
    return report
