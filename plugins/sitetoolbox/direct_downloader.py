"""为缺失种子维护创建独立直连 qB 会话，不改动宿主共享实例。"""

from copy import copy
from contextlib import contextmanager

from qbittorrentapi import Client


class DirectQbClient(Client):
    @property
    def _session(self):
        # qB 库会在登录恢复等情况下重建 Session；每个新会话都必须保持直连。
        session = super()._session
        session.trust_env = False
        session.proxies.clear()
        return session


def downloader_config(service):
    conf = getattr(service, "config", None)
    config = conf.get("config") if isinstance(conf, dict) else getattr(conf, "config", None)
    return dict(config or {})


def direct_service(service):
    """复用宿主的下载方法与返回值兼容逻辑，认证/连接均由插件自己的客户端承担。"""
    config = downloader_config(service)
    instance = service.instance
    source = getattr(instance, "qbc", None)
    host = config.get("host") or getattr(source, "host", None) or getattr(instance, "_host", None)
    if not host:
        raise ValueError("Missing downloader host")
    request_args = dict(getattr(source, "_REQUESTS_ARGS", None) or {})
    request_args.setdefault("timeout", (15, 60))
    # 同时覆盖显式代理和环境代理；不修改进程环境或宿主的字典。
    request_args["proxies"] = {"http": "", "https": "", "all": ""}
    headers = {key: value for key, value in (getattr(source, "_EXTRA_HEADERS", None) or {}).items()
               if key.lower() != "proxy-authorization"}
    if config.get("apikey"):
        headers["Authorization"] = f"Bearer {config['apikey']}"
    client = DirectQbClient(
        host=host, port=config.get("port"),
        username=config.get("username", getattr(instance, "_username", None)),
        password=config.get("password", getattr(instance, "_password", None)),
        FORCE_SCHEME_FROM_HOST=True,
        VERIFY_WEBUI_CERTIFICATE=getattr(source, "_VERIFY_WEBUI_CERTIFICATE", True),
        EXTRA_HEADERS=headers,
        HTTPADAPTER_ARGS=dict(getattr(source, "_HTTPADAPTER_ARGS", None) or {}),
        REQUESTS_ARGS=request_args,
    )
    private = copy(service)
    private_instance = copy(instance)
    if private is service or private_instance is instance:
        raise TypeError("Downloader wrapper cannot be isolated")
    private.instance = private_instance
    private.instance.qbc = client
    return private


@contextmanager
def direct_downloader(service):
    """扫描或清理各自持有一份会话，复查和删除共用，结束后立即释放。"""
    private = direct_service(service)
    client = private.instance.qbc
    try:
        yield private.instance
    finally:
        session = getattr(client, "_http_session", None)
        if session is not None:
            session.close()
