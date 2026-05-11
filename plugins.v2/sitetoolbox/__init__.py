from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from fastapi import Body

from app import schemas
from app.db.site_oper import SiteOper
from app.helper.rss import RssHelper
from app.log import logger
from app.plugins import _PluginBase


class sitetoolbox(_PluginBase):
    plugin_name = "站点工具箱"
    plugin_desc = "站点诊断工具集合，当前支持测试已有站点 RSS 订阅是否正常。"
    plugin_icon = "mdi-toolbox"
    plugin_version = "1.0.0"
    plugin_author = "Ellick"
    plugin_order = 40
    auth_level = 1

    _instance: Optional["sitetoolbox"] = None
    _enabled = True
    _site_ids: List[int] = []
    _timeout = 20
    _auto_discover = True
    _save_discovered = False
    _latest_results: List[Dict[str, Any]] = []

    def init_plugin(self, config: dict = None):
        config = config or {}
        self.__class__._instance = self
        self._enabled = _to_bool(config.get("enabled", True), True)
        self._site_ids = _int_list(config.get("site_ids"))
        self._timeout = _int_or_default(config.get("timeout"), 20, minimum=5, maximum=120)
        self._auto_discover = _to_bool(config.get("auto_discover", True), True)
        self._save_discovered = _to_bool(config.get("save_discovered", False), False)
        self._latest_results = config.get("latest_results") if isinstance(config.get("latest_results"), list) else []
        self.__class__._enabled = self._enabled
        self.__class__._site_ids = self._site_ids
        self.__class__._timeout = self._timeout
        self.__class__._auto_discover = self._auto_discover
        self.__class__._save_discovered = self._save_discovered
        self.__class__._latest_results = self._latest_results

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[dict]:
        return []

    def get_api(self) -> List[dict]:
        return [
            {
                "path": "/test/rss",
                "endpoint": _api_test_selected_rss,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "测试已选站点RSS",
                "description": "测试插件配置中选择的站点 RSS 是否可以获取并解析。",
            },
            {
                "path": "/test/rss/{site_id}",
                "endpoint": _api_test_one_rss,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "测试单个站点RSS",
                "description": "测试指定站点 RSS 是否可以获取并解析。",
            },
        ]

    def get_form(self) -> Tuple[Optional[List[dict]], dict]:
        site_options = [
            {
                "title": f"{site.name} ({site.domain})" if site.domain else site.name,
                "value": site.id,
            }
            for site in SiteOper().list_order_by_pri()
            if site and site.id
        ]
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            _col(12, 4, {
                                "component": "VSwitch",
                                "props": {"model": "enabled", "label": "启用插件"},
                            }),
                            _col(12, 4, {
                                "component": "VSwitch",
                                "props": {"model": "auto_discover", "label": "未配置RSS时自动获取"},
                            }),
                            _col(12, 4, {
                                "component": "VSwitch",
                                "props": {"model": "save_discovered", "label": "自动保存获取到的RSS"},
                            }),
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            _col(12, 8, {
                                "component": "VSelect",
                                "props": {
                                    "model": "site_ids",
                                    "label": "测试站点",
                                    "items": site_options,
                                    "multiple": True,
                                    "chips": True,
                                    "clearable": True,
                                },
                            }),
                            _col(12, 4, {
                                "component": "VTextField",
                                "props": {
                                    "model": "timeout",
                                    "label": "请求超时(秒)",
                                    "type": "number",
                                    "min": 5,
                                    "max": 120,
                                },
                            }),
                        ],
                    },
                    {
                        "component": "VAlert",
                        "props": {
                            "type": "info",
                            "variant": "tonal",
                            "text": "优先测试站点已保存的 RSS 地址；未配置时可按 MoviePilot 内置规则访问站点生成 RSS。测试结果在插件详情页查看。",
                        },
                    },
                ],
            }
        ], {
            "enabled": True,
            "site_ids": [],
            "timeout": 20,
            "auto_discover": True,
            "save_discovered": False,
            "latest_results": [],
        }

    def get_page(self) -> Optional[List[dict]]:
        return _toolbox_page(self)

    def stop_service(self):
        pass

    def _save_results(self, results: List[Dict[str, Any]]):
        self._latest_results = results
        self.__class__._latest_results = results
        self.update_config({
            "enabled": self._enabled,
            "site_ids": self._site_ids,
            "timeout": self._timeout,
            "auto_discover": self._auto_discover,
            "save_discovered": self._save_discovered,
            "latest_results": results,
        })


def _api_test_selected_rss(payload: Optional[dict] = Body(default=None)) -> schemas.Response:
    plugin = sitetoolbox._instance
    if not plugin:
        return schemas.Response(success=False, message="插件未初始化")
    site_ids = _int_list((payload or {}).get("site_ids")) or plugin._site_ids
    if not site_ids:
        return schemas.Response(success=False, message="未选择站点")
    results = [_test_site_rss(site_id) for site_id in site_ids]
    plugin._save_results(results)
    ok_count = len([item for item in results if item.get("state") == "success"])
    return schemas.Response(success=ok_count == len(results), message=f"RSS测试完成：成功 {ok_count}/{len(results)}", data=results)


def _api_test_one_rss(site_id: int) -> schemas.Response:
    plugin = sitetoolbox._instance
    if not plugin:
        return schemas.Response(success=False, message="插件未初始化")
    result = _test_site_rss(site_id)
    kept = [item for item in sitetoolbox._latest_results if item.get("site_id") != site_id]
    plugin._save_results([result, *kept][:100])
    return schemas.Response(success=result.get("state") == "success", message=result.get("message"), data=result)


def _test_site_rss(site_id: int) -> Dict[str, Any]:
    start = time.monotonic()
    site = SiteOper().get(site_id)
    if not site:
        return _result(site_id=site_id, state="error", message="站点不存在", seconds=0)
    if not getattr(site, "is_active", True):
        return _result(site=site, state="error", message="站点未启用", seconds=_elapsed(start))
    if not site.url:
        return _result(site=site, state="error", message="站点地址为空", seconds=_elapsed(start))

    rss_url = (site.rss or "").strip()
    source = "saved"
    if not rss_url and sitetoolbox._auto_discover:
        rss_url, message = RssHelper().get_rss_link(
            url=site.url,
            cookie=site.cookie or "",
            ua=site.ua or "",
            proxy=bool(site.proxy),
            timeout=sitetoolbox._timeout,
        )
        if not rss_url:
            return _result(site=site, state="error", message=message or "未获取到RSS链接", seconds=_elapsed(start))
        rss_url = urljoin(site.url, rss_url)
        source = "discovered"
        if sitetoolbox._save_discovered:
            SiteOper().update_rss(site.domain, rss_url)

    if not rss_url:
        return _result(site=site, state="error", message="站点未配置RSS地址", seconds=_elapsed(start))

    headers = {"Cookie": site.cookie} if site.cookie else None
    items = RssHelper().parse(
        url=rss_url,
        proxy=bool(site.proxy),
        timeout=sitetoolbox._timeout,
        headers=headers,
        ua=site.ua or None,
    )
    if items is None:
        return _result(site=site, state="error", message="RSS链接已过期", rss_url=rss_url, source=source, seconds=_elapsed(start))
    if items is False:
        return _result(site=site, state="error", message="RSS请求或解析失败", rss_url=rss_url, source=source, seconds=_elapsed(start))
    if not items:
        return _result(site=site, state="warning", message="RSS可访问但没有解析到条目", rss_url=rss_url, source=source, count=0, seconds=_elapsed(start))

    samples = [
        {
            "title": item.get("title"),
            "pubdate": str(item.get("pubdate") or ""),
            "has_enclosure": bool(item.get("enclosure")),
        }
        for item in items[:5]
    ]
    return _result(
        site=site,
        state="success",
        message="RSS正常",
        rss_url=rss_url,
        source=source,
        count=len(items),
        samples=samples,
        seconds=_elapsed(start),
    )


def _result(site: Any = None, site_id: Optional[int] = None, state: str = "error", message: str = "",
            rss_url: str = "", source: str = "", count: Optional[int] = None, samples: Optional[List[dict]] = None,
            seconds: float = 0) -> Dict[str, Any]:
    return {
        "site_id": getattr(site, "id", None) or site_id,
        "site_name": getattr(site, "name", None) or "",
        "domain": getattr(site, "domain", None) or "",
        "state": state,
        "message": message,
        "rss_url": _mask_url(rss_url),
        "source": source,
        "count": count,
        "samples": samples or [],
        "seconds": seconds,
        "tested_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _toolbox_page(plugin: sitetoolbox) -> List[dict]:
    results = plugin._latest_results or []
    ok = len([item for item in results if item.get("state") == "success"])
    warning = len([item for item in results if item.get("state") == "warning"])
    error = len([item for item in results if item.get("state") == "error"])
    return [
        {
            "component": "VRow",
            "props": {"dense": True},
            "content": [
                _stat_card("插件状态", "已启用" if plugin.get_state() else "已停用", f"版本 {plugin.plugin_version}", "primary"),
                _stat_card("已选站点", len(plugin._site_ids), "配置页选择", "info"),
                _stat_card("正常", ok, "最近一次结果", "success"),
                _stat_card("异常", error + warning, f"失败 {error} / 空结果 {warning}", "error" if error else "warning"),
            ],
        },
        {
            "component": "VRow",
            "content": [
                _col(12, None, {
                    "component": "VBtn",
                    "props": {
                        "variant": "tonal",
                        "color": "primary",
                        "prepend-icon": "mdi-rss",
                    },
                    "text": "测试已选站点 RSS",
                    "events": {
                        "click": {
                            "api": "plugin/sitetoolbox/test/rss",
                            "method": "post",
                        }
                    },
                })
            ],
        },
        _result_table(results),
    ]


def _result_table(results: List[Dict[str, Any]]) -> dict:
    rows = []
    for item in results:
        rows.append({
            "component": "tr",
            "content": [
                _td(item.get("site_name") or "-"),
                _td(item.get("domain") or "-"),
                _td(_state_text(item.get("state")), "text-no-wrap"),
                _td(item.get("message") or "-"),
                _td(str(item.get("count") if item.get("count") is not None else "-"), "text-no-wrap"),
                _td(item.get("source") or "-", "text-no-wrap"),
                _td(item.get("seconds"), "text-no-wrap"),
                _td(item.get("tested_at") or "-", "text-no-wrap"),
            ],
        })
    return {
        "component": "VCard",
        "props": {"variant": "outlined"},
        "content": [
            {
                "component": "VCardTitle",
                "text": "RSS 测试结果",
            },
            {
                "component": "VCardText",
                "content": [
                    {
                        "component": "VAlert",
                        "props": {
                            "type": "info",
                            "variant": "tonal",
                            "text": "点击测试后如页面未立即刷新，可关闭再打开插件详情页查看最新结果。",
                        },
                    } if not results else {
                        "component": "VTable",
                        "props": {"density": "compact"},
                        "content": [
                            {
                                "component": "thead",
                                "content": [{
                                    "component": "tr",
                                    "content": [
                                        _th("站点"),
                                        _th("域名"),
                                        _th("状态"),
                                        _th("说明"),
                                        _th("条目"),
                                        _th("来源"),
                                        _th("耗时"),
                                        _th("时间"),
                                    ],
                                }],
                            },
                            {
                                "component": "tbody",
                                "content": rows,
                            },
                        ],
                    }
                ],
            },
        ],
    }


def _stat_card(title: str, value: Any, subtitle: str, color: str) -> dict:
    return {
        "component": "VCol",
        "props": {"cols": 12, "sm": 6, "md": 3},
        "content": [{
            "component": "VCard",
            "props": {"variant": "tonal", "color": color},
            "content": [
                {"component": "VCardSubtitle", "text": title},
                {"component": "VCardTitle", "text": str(value)},
                {"component": "VCardText", "text": subtitle},
            ],
        }],
    }


def _col(cols: int, md: Optional[int], child: dict) -> dict:
    props = {"cols": cols}
    if md:
        props["md"] = md
    return {"component": "VCol", "props": props, "content": [child]}


def _th(text: str) -> dict:
    return {"component": "th", "text": text}


def _td(text: Any, class_name: Optional[str] = None) -> dict:
    props = {"class": class_name} if class_name else {}
    return {"component": "td", "props": props, "text": "" if text is None else str(text)}


def _state_text(state: Optional[str]) -> str:
    return {
        "success": "正常",
        "warning": "空结果",
        "error": "异常",
    }.get(state or "", state or "-")


def _mask_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    if not parsed.query:
        return url
    masked_query = []
    sensitive_keys = {"passkey", "pass", "key", "token", "auth", "uid", "secure"}
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in sensitive_keys or len(value) >= 16:
            value = "***"
        masked_query.append((key, value))
    return urlunparse(parsed._replace(query=urlencode(masked_query)))


def _elapsed(start: float) -> float:
    return round(time.monotonic() - start, 2)


def _to_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _int_or_default(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _int_list(value: Any) -> List[int]:
    if value is None:
        return []
    if isinstance(value, (str, int)):
        value = [value]
    result = []
    for item in value if isinstance(value, list) else []:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result
