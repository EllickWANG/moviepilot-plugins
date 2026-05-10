from __future__ import annotations

import base64
import html as html_lib
import importlib
import inspect
import json
import pkgutil
import re
from typing import Any, List, Optional, Tuple
from urllib.parse import urlparse

from lxml import etree

from app.helper.sites import SitesHelper
from app.log import logger
from app.plugins import _PluginBase
from app.utils.string import StringUtils


class siteadapter(_PluginBase):
    plugin_name = "站点适配器"
    plugin_desc = "配置化站点索引与用户数据解析适配。"
    plugin_icon = "mdi-database-cog"
    plugin_version = "1.0.0"
    plugin_author = "Ellick"
    plugin_order = 31
    auth_level = 1

    _enabled = False
    _patch_userdata = True
    _site_conf = ""

    _patched = False
    _originals: dict[tuple[type, str], Any] = {}
    _site_rules: list[dict[str, Any]] = []
    _userdata_rules: list[dict[str, Any]] = []

    def init_plugin(self, config: dict = None):
        config = config or {}
        self._enabled = bool(config.get("enabled", True))
        self._patch_userdata = bool(config.get("patch_userdata", True))
        self._site_conf = config.get("site_conf") or _merge_legacy_site_conf(
            indexer_conf=config.get("indexer_conf") or config.get("confstr") or "",
            userdata_conf=config.get("userdata_conf") or "",
        )
        self.__class__._site_rules = _parse_site_config(self._site_conf)
        self.__class__._userdata_rules = [
            {"domain": rule.get("domain"), "config": rule.get("userdata")}
            for rule in self.__class__._site_rules
            if isinstance(rule.get("userdata"), dict)
        ]

        if not self._enabled:
            self._unpatch_userdata()
            return

        self._apply_indexers()
        if self._patch_userdata and self.__class__._userdata_rules:
            self._patch_userdata_parsers()
        else:
            self._unpatch_userdata()

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[dict]:
        return []

    def get_api(self) -> List[dict]:
        return []

    def get_form(self) -> Tuple[Optional[List[dict]], dict]:
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "enabled",
                                            "label": "启用插件",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "patch_userdata",
                                            "label": "启用用户数据解析规则",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "site_conf",
                                            "label": "站点适配配置",
                                            "rows": 14,
                                            "placeholder": "一行一个站点，格式：域名|配置 json 的 base64 编码（utf-8）。JSON 可包含 indexer 与 userdata。",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "variant": "tonal",
                                            "text": "每个站点一段配置，indexer 负责资源搜索/浏览，userdata 负责上传量、下载量、分享率、魔力值、做种等账号数据。",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                ],
            }
        ], {
            "enabled": True,
            "patch_userdata": True,
            "site_conf": "",
        }

    def get_page(self) -> Optional[List[dict]]:
        return []

    def stop_service(self):
        self._unpatch_userdata()

    def _apply_indexers(self):
        count = 0
        for rule in self.__class__._site_rules:
            domain = rule.get("domain")
            indexer = rule.get("indexer")
            if not domain or not isinstance(indexer, dict):
                continue
            try:
                SitesHelper().add_indexer(domain, indexer)
                count += 1
            except Exception as err:
                logger.error(f"站点适配器索引配置加载失败：{domain} - {err}")
                self.systemmessage.put(f"{domain} 索引配置加载失败：{err}", title=self.plugin_name)
        if count:
            logger.info(f"站点适配器索引配置已加载：{count} 个")

    @classmethod
    def _patch_userdata_parsers(cls):
        if cls._patched:
            logger.info(f"站点适配器用户数据解析规则已更新：{len(cls._userdata_rules)} 个")
            return

        patched_count = 0
        for parser_cls in _iter_site_parser_classes():
            for method in ("_parse_user_base_info", "_parse_user_traffic_info", "_parse_user_detail_info"):
                if method not in parser_cls.__dict__:
                    continue
                key = (parser_cls, method)
                if key in cls._originals:
                    continue
                original = getattr(parser_cls, method)
                cls._originals[key] = original

                def wrapped(self, html_text, *args, _original=original, **kwargs):
                    result = _original(self, html_text, *args, **kwargs)
                    _apply_site_userdata_rules(self, html_text, cls._userdata_rules)
                    return result

                setattr(parser_cls, method, wrapped)
                patched_count += 1

        cls._patched = patched_count > 0
        logger.info(f"站点适配器用户数据解析规则已启用：{len(cls._userdata_rules)} 个，挂载方法 {patched_count} 个")

    @classmethod
    def _unpatch_userdata(cls):
        if not cls._patched:
            return
        for (parser_cls, method), original in list(cls._originals.items()):
            setattr(parser_cls, method, original)
        cls._originals = {}
        cls._patched = False
        logger.info("站点适配器用户数据解析规则已停用")


def _iter_site_parser_classes() -> list[type]:
    try:
        from app.modules.indexer import parser as parser_pkg
        from app.modules.indexer.parser import SiteParserBase
    except Exception as err:
        logger.warning(f"站点适配器无法加载解析器基类：{err}")
        return []

    classes: list[type] = []
    for module_info in pkgutil.iter_modules(parser_pkg.__path__):
        module_name = f"app.modules.indexer.parser.{module_info.name}"
        try:
            module = importlib.import_module(module_name)
        except Exception as err:
            logger.warning(f"站点适配器跳过解析器模块：{module_name} - {err}")
            continue
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if obj is SiteParserBase:
                continue
            try:
                if issubclass(obj, SiteParserBase):
                    classes.append(obj)
            except TypeError:
                continue

    # 同一个类可能被多个模块引用，按对象去重，避免重复包裹。
    return list(dict.fromkeys(classes))


def _parse_site_config(conf_text: str) -> list[dict[str, Any]]:
    conf_text = (conf_text or "").strip()
    if not conf_text:
        return []

    # 支持直接粘贴 JSON 数组或 {"sites": [...]}，便于后续批量维护。
    if conf_text[0] in "[{":
        try:
            data = json.loads(conf_text)
            return _normalize_site_items(data)
        except Exception:
            pass

    items: list[dict[str, Any]] = []
    for line in conf_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            if not line.startswith("{") and "|" in line:
                domain, raw_config = line.split("|", 1)
                config = json.loads(base64.b64decode(raw_config.strip()).decode("utf-8"))
                if normalized := _normalize_site_item(domain, config):
                    items.append(normalized)
            else:
                items.extend(_normalize_site_items(json.loads(line)))
        except Exception as err:
            logger.error(f"站点适配器配置格式错误：{err}")
    return [item for item in items if item.get("domain")]


def _normalize_site_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict) and isinstance(data.get("sites"), list):
        data = data.get("sites")
    elif isinstance(data, dict) and isinstance(data.get("rules"), list):
        data = data.get("rules")
    elif isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []

    items: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        domain = item.get("domain") or item.get("site") or item.get("host")
        if "config" in item and not any(key in item for key in ("indexer", "userdata", "user_data")):
            config = item.get("config")
        else:
            config = {key: value for key, value in item.items() if key not in {"domain", "site", "host"}}
        if normalized := _normalize_site_item(domain, config):
            items.append(normalized)
    return items


def _normalize_site_item(domain: Any, config: Any) -> Optional[dict[str, Any]]:
    domain = _normalize_domain(domain)
    if not domain or not isinstance(config, dict):
        return None

    indexer = config.get("indexer")
    userdata = config.get("userdata") or config.get("user_data")

    if indexer is None and userdata is None:
        if _looks_like_indexer(config):
            indexer = config
        else:
            userdata = config

    return {
        "domain": domain,
        "indexer": indexer if isinstance(indexer, dict) else None,
        "userdata": userdata if isinstance(userdata, dict) else None,
    }


def _looks_like_indexer(config: dict[str, Any]) -> bool:
    return any(key in config for key in ("torrents", "search", "browse", "category", "schema"))


def _merge_legacy_site_conf(indexer_conf: str, userdata_conf: str) -> str:
    merged: dict[str, dict[str, Any]] = {}

    for rule in _parse_legacy_domain_config(indexer_conf):
        domain = rule.get("domain")
        if not domain:
            continue
        merged.setdefault(domain, {})["indexer"] = rule.get("config")

    for rule in _parse_legacy_domain_config(userdata_conf):
        domain = rule.get("domain")
        if not domain:
            continue
        merged.setdefault(domain, {})["userdata"] = rule.get("config")

    lines = []
    for domain, config in merged.items():
        raw = base64.b64encode(json.dumps(config, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).decode()
        lines.append(f"{domain}|{raw}")
    return "\n".join(lines)


def _parse_legacy_domain_config(conf_text: str) -> list[dict[str, Any]]:
    conf_text = (conf_text or "").strip()
    if not conf_text:
        return []

    if conf_text[0] in "[{":
        try:
            return _normalize_legacy_items(json.loads(conf_text))
        except Exception:
            pass

    items: list[dict[str, Any]] = []
    for line in conf_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            if not line.startswith("{") and "|" in line:
                domain, raw_config = line.split("|", 1)
                config = json.loads(base64.b64decode(raw_config.strip()).decode("utf-8"))
                items.append({"domain": _normalize_domain(domain), "config": config})
            else:
                items.extend(_normalize_legacy_items(json.loads(line)))
        except Exception as err:
            logger.error(f"站点适配器旧配置格式错误：{err}")
    return [item for item in items if item.get("domain")]


def _normalize_legacy_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict) and isinstance(data.get("sites"), list):
        data = data.get("sites")
    elif isinstance(data, dict) and isinstance(data.get("rules"), list):
        data = data.get("rules")
    elif isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []

    items: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        domain = item.get("domain") or item.get("site") or item.get("host")
        config = item.get("config")
        if config is None:
            config = {key: value for key, value in item.items() if key not in {"domain", "site", "host"}}
        items.append({"domain": _normalize_domain(domain), "config": config})
    return items


def _apply_site_userdata_rules(parser: Any, html_text: str, rules: list[dict[str, Any]]):
    if not html_text or not rules:
        return

    parser_domain = _normalize_domain(getattr(parser, "_site_domain", "") or getattr(parser, "_base_url", ""))
    if not parser_domain:
        return

    matched_rules = [rule for rule in rules if _domain_matches(rule.get("domain"), parser_domain)]
    if not matched_rules:
        return

    try:
        html = etree.HTML(html_text)
    except Exception as err:
        logger.warning(f"站点适配器用户数据 HTML 解析失败：{parser_domain} - {err}")
        return
    if html is None:
        return

    try:
        for rule in matched_rules:
            config = rule.get("config") or {}
            if not isinstance(config, dict):
                continue
            _apply_json_stats(parser, html, html_text, config)
            _apply_field_rules(parser, html, html_text, config)
            if config.get("calculate_ratio") and not getattr(parser, "ratio", 0) and getattr(parser, "download", 0):
                parser.ratio = round(getattr(parser, "upload", 0) / getattr(parser, "download", 0), 3)
    except Exception as err:
        logger.warning(f"站点适配器用户数据规则执行失败：{parser_domain} - {err}")
    finally:
        del html


def _apply_field_rules(parser: Any, html, html_text: str, config: dict[str, Any]):
    fields = config.get("fields") or {}
    if not isinstance(fields, dict):
        return

    for field, specs in fields.items():
        for spec in _as_list(specs):
            value = _extract_value(html, html_text, spec)
            if value is None or value == "":
                continue
            if isinstance(spec, dict) and spec.get("only_empty") and getattr(parser, field, None):
                continue
            _set_parser_value(parser, field, value, spec.get("type") if isinstance(spec, dict) else None)
            if not (isinstance(spec, dict) and spec.get("continue")):
                break


def _apply_json_stats(parser: Any, html, html_text: str, config: dict[str, Any]):
    for spec in _as_list(config.get("json_stats") or config.get("stats_json") or []):
        if not isinstance(spec, dict):
            continue
        raw_value = _extract_value(html, html_text, spec)
        if not raw_value:
            continue
        try:
            payload = json.loads(html_lib.unescape(raw_value))
        except Exception:
            continue

        mapping = spec.get("mapping") or spec.get("map") or {}
        if not isinstance(mapping, dict):
            continue
        label_keys = spec.get("label_keys") or ["tone", "label", "name", "title"]
        value_key = spec.get("value_key") or "value"

        for item in _json_stat_items(payload):
            label = _first_item_value(item, label_keys)
            value = item.get(value_key)
            if label is None or value is None:
                continue
            target_field = mapping.get(str(label)) or mapping.get(str(label).strip().lower())
            if not target_field:
                continue
            _set_parser_value(parser, target_field, str(value), None)


def _extract_value(html, html_text: str, spec: Any) -> Optional[str]:
    if isinstance(spec, str):
        spec = {"xpath": spec}
    if not isinstance(spec, dict):
        return None

    if "value" in spec:
        value = spec.get("value")
    elif spec.get("xpath"):
        value = _extract_xpath_value(html, spec)
    elif spec.get("regex"):
        value = html_text
    else:
        return None

    if value is None:
        return None

    value = html_lib.unescape(str(value)).strip()
    regex = spec.get("regex")
    if regex:
        flags = re.IGNORECASE if spec.get("ignore_case", True) else 0
        match = re.search(regex, value, flags)
        if not match:
            return None
        group = spec.get("group", 1 if match.lastindex else 0)
        value = match.group(group)

    for old, new in spec.get("replace") or []:
        value = value.replace(str(old), str(new))
    return value.strip()


def _extract_xpath_value(html, spec: dict[str, Any]) -> Optional[str]:
    try:
        values = html.xpath(spec.get("xpath"))
    except Exception:
        return None
    if not values:
        return None

    index = spec.get("index", 0)
    if index == "last":
        index = -1
    try:
        value = values[int(index)]
    except Exception:
        return None

    attribute = spec.get("attribute")
    if isinstance(value, etree._Element):
        if attribute == "text" or not attribute:
            return value.xpath("string(.)")
        if attribute == "tail":
            return value.tail or ""
        if attribute == "html":
            return etree.tostring(value, encoding="unicode")
        return value.get(attribute) or ""
    return str(value)


def _set_parser_value(parser: Any, field: str, value: str, value_type: Optional[str] = None):
    field = str(field or "").strip()
    if not field:
        return
    normalized_field = _normalize_field(field)
    value_type = value_type or _default_value_type(normalized_field)
    value = str(value or "").strip()

    if normalized_field == "active" or value_type == "active":
        active = re.search(r"↑\s*(\d+)\s*/\s*↓\s*(\d+)", value) or re.search(r"(\d+)\s*/\s*(\d+)", value)
        if active:
            parser.seeding = StringUtils.str_int(active.group(1))
            parser.leeching = StringUtils.str_int(active.group(2))
        return

    if value_type == "size":
        setattr(parser, normalized_field, StringUtils.num_filesize(value))
    elif value_type == "int":
        setattr(parser, normalized_field, StringUtils.str_int(value))
    elif value_type == "float":
        setattr(parser, normalized_field, StringUtils.str_float(_clean_number(value)))
    else:
        setattr(parser, normalized_field, value)


def _json_stat_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("items", "data", "stats"):
            if isinstance(payload.get(key), list):
                return [item for item in payload.get(key) if isinstance(item, dict)]
        return [payload]
    return []


def _first_item_value(item: dict[str, Any], keys: list[str]) -> Optional[Any]:
    for key in keys:
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip(" ：:")
    return None


def _normalize_field(field: str) -> str:
    aliases = {
        "uploaded": "upload",
        "上传量": "upload",
        "downloaded": "download",
        "下载量": "download",
        "downloaded_bytes": "download",
        "bonus": "bonus",
        "魔力": "bonus",
        "魔力值": "bonus",
        "爆米花": "bonus",
        "karma points": "bonus",
        "ratio": "ratio",
        "分享率": "ratio",
        "seeders": "seeding",
        "当前做种": "seeding",
        "torrents seeding": "seeding",
        "leechers": "leeching",
        "当前下载": "leeching",
        "torrents leeching": "leeching",
    }
    key = field.strip().lower()
    return aliases.get(key, field.strip())


def _default_value_type(field: str) -> str:
    if field in {"upload", "download", "seeding_size"}:
        return "size"
    if field in {"ratio", "bonus"}:
        return "float"
    if field in {"seeding", "leeching"}:
        return "int"
    return "text"


def _clean_number(value: str) -> str:
    value = value.replace("---", "0").replace("∞", "0").replace(",", "")
    return re.sub(r"\s+", "", value)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _normalize_domain(domain: Any) -> str:
    domain = str(domain or "").strip().lower()
    if not domain:
        return ""
    parsed = urlparse(domain if "://" in domain else f"https://{domain}")
    host = parsed.netloc or parsed.path.split("/", 1)[0]
    return host.split("@")[-1].split(":", 1)[0].strip()


def _domain_matches(rule_domain: Any, parser_domain: str) -> bool:
    rule_domain = _normalize_domain(rule_domain)
    parser_domain = _normalize_domain(parser_domain)
    if not rule_domain or not parser_domain:
        return False
    return parser_domain == rule_domain or parser_domain.endswith(f".{rule_domain}")
