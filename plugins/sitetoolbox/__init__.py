from __future__ import annotations

import base64
import html as html_lib
import importlib
import inspect
import json
import logging
import pkgutil
import re
import sys
import time
from threading import Lock, Thread
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from apscheduler.triggers.cron import CronTrigger
from fastapi import Body
from lxml import etree

from app import schemas
from app.core.config import settings
from app.db.site_oper import SiteOper
from app.db.systemconfig_oper import SystemConfigOper
from app.helper.downloader import DownloaderHelper
from app.helper.rss import RssHelper
from app.helper.sites import SitesHelper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import SystemConfigKey
from app.utils.http import RequestUtils
from app.utils.string import StringUtils


class sitetoolbox(_PluginBase):
    plugin_name = "站点工具箱"
    plugin_desc = "站点诊断与适配工具集合，支持 RSS 测试修复、站点索引、用户数据解析适配、缺失文件种子清理和馒头登录检查。"
    plugin_icon = "mdi-toolbox"
    plugin_version = "1.3.5"
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
    _latest_userdata_results: List[Dict[str, Any]] = []
    _latest_userdata_checked_at = ""
    _latest_job_states: Dict[str, Dict[str, Any]] = {}
    _patch_userdata = True
    _site_conf = ""
    _cleanup_downloader_names: List[str] = []
    _cleanup_delete_files = False
    _cleanup_auto_enabled = False
    _cleanup_auto_cron = "0 */6 * * *"
    _latest_missing_preview: Dict[str, Any] = {}
    _latest_missing_cleanup: Dict[str, Any] = {}
    _error_retention_days = 3
    _mteam_site_ids: List[int] = []
    _mteam_warning_days = 25
    _mteam_auto_enabled = False
    _mteam_auto_cron = "0 9 * * *"
    _latest_mteam_login_check: Dict[str, Any] = {}

    _patched = False
    _originals: dict[tuple[type, str], Any] = {}
    _site_rules: list[dict[str, Any]] = []
    _userdata_rules: list[dict[str, Any]] = []
    _jobs_lock = Lock()
    _error_log_lock = Lock()
    _error_capture_installed = False
    _original_logger_method: Optional[Callable] = None
    _standard_error_handler: Optional[logging.Handler] = None
    _last_error_prune = 0.0

    def init_plugin(self, config: dict = None):
        config = config or {}
        self.__class__._instance = self
        self._enabled = _to_bool(config.get("enabled", True), True)
        self._site_ids = _int_list(config.get("site_ids"))
        self._timeout = _int_or_default(config.get("timeout"), 20, minimum=5, maximum=120)
        self._auto_discover = _to_bool(config.get("auto_discover", True), True)
        self._save_discovered = _to_bool(config.get("save_discovered", False), False)
        self._latest_results = config.get("latest_results") if isinstance(config.get("latest_results"), list) else []
        self._latest_userdata_results = (
            config.get("latest_userdata_results") if isinstance(config.get("latest_userdata_results"), list) else []
        )
        self._latest_userdata_checked_at = str(config.get("latest_userdata_checked_at") or "")
        self._latest_job_states = _normalize_job_states(config.get("latest_job_states"))
        self._patch_userdata = _to_bool(config.get("patch_userdata", True), True)
        self._site_conf = config.get("site_conf") or _merge_legacy_site_conf(
            indexer_conf=config.get("indexer_conf") or config.get("confstr") or "",
            userdata_conf=config.get("userdata_conf") or "",
        )
        self._cleanup_downloader_names = _str_list(
            config.get("cleanup_downloader_names") or config.get("cleanup_downloaders")
        )
        self._cleanup_delete_files = _to_bool(config.get("cleanup_delete_files", False), False)
        self._cleanup_auto_enabled = _to_bool(config.get("cleanup_auto_enabled", False), False)
        self._cleanup_auto_cron = str(config.get("cleanup_auto_cron") or "0 */6 * * *").strip()
        self._latest_missing_preview = (
            config.get("latest_missing_preview") if isinstance(config.get("latest_missing_preview"), dict) else {}
        )
        self._latest_missing_cleanup = (
            config.get("latest_missing_cleanup") if isinstance(config.get("latest_missing_cleanup"), dict) else {}
        )
        self._error_retention_days = _normalize_retention_days(config.get("error_retention_days"))
        self._mteam_site_ids = _int_list(config.get("mteam_site_ids"))
        self._mteam_warning_days = _int_or_default(config.get("mteam_warning_days"), 25, minimum=1, maximum=365)
        self._mteam_auto_enabled = _to_bool(config.get("mteam_auto_enabled", False), False)
        self._mteam_auto_cron = str(config.get("mteam_auto_cron") or "0 9 * * *").strip()
        self._latest_mteam_login_check = (
            config.get("latest_mteam_login_check")
            if isinstance(config.get("latest_mteam_login_check"), dict)
            else {}
        )
        self.__class__._enabled = self._enabled
        self.__class__._site_ids = self._site_ids
        self.__class__._timeout = self._timeout
        self.__class__._auto_discover = self._auto_discover
        self.__class__._save_discovered = self._save_discovered
        self.__class__._latest_results = self._latest_results
        self.__class__._latest_userdata_results = self._latest_userdata_results
        self.__class__._latest_userdata_checked_at = self._latest_userdata_checked_at
        self.__class__._latest_job_states = self._latest_job_states
        self.__class__._patch_userdata = self._patch_userdata
        self.__class__._site_conf = self._site_conf
        self.__class__._cleanup_downloader_names = self._cleanup_downloader_names
        self.__class__._cleanup_delete_files = self._cleanup_delete_files
        self.__class__._cleanup_auto_enabled = self._cleanup_auto_enabled
        self.__class__._cleanup_auto_cron = self._cleanup_auto_cron
        self.__class__._latest_missing_preview = self._latest_missing_preview
        self.__class__._latest_missing_cleanup = self._latest_missing_cleanup
        self.__class__._error_retention_days = self._error_retention_days
        self.__class__._mteam_site_ids = self._mteam_site_ids
        self.__class__._mteam_warning_days = self._mteam_warning_days
        self.__class__._mteam_auto_enabled = self._mteam_auto_enabled
        self.__class__._mteam_auto_cron = self._mteam_auto_cron
        self.__class__._latest_mteam_login_check = self._latest_mteam_login_check
        self.__class__._site_rules = _parse_site_config(self._site_conf)
        self.__class__._userdata_rules = [
            {"domain": rule.get("domain"), "config": rule.get("userdata")}
            for rule in self.__class__._site_rules
            if isinstance(rule.get("userdata"), dict)
        ]

        if self._enabled:
            self._install_error_capture()
            self._get_error_records()
            self._apply_indexers()
            if self._patch_userdata and self.__class__._userdata_rules:
                self._patch_userdata_parsers()
            else:
                self._unpatch_userdata()
        else:
            self._unpatch_userdata()
            self._uninstall_error_capture()

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[dict]:
        return []

    def get_service(self) -> List[Dict[str, Any]]:
        if not self._enabled:
            return []
        services: List[Dict[str, Any]] = []
        if self._cleanup_auto_enabled and self._cleanup_auto_cron and self._cleanup_downloader_names:
            try:
                trigger = CronTrigger.from_crontab(self._cleanup_auto_cron)
            except Exception as err:
                logger.error(f"站点工具箱缺失种子定时任务 cron 无效：{self._cleanup_auto_cron} - {err}")
            else:
                services.append({
                    "id": "sitetoolbox_missing_preview",
                    "name": "站点工具箱缺失种子扫描",
                    "trigger": trigger,
                    "func": self.auto_preview_missing_torrents,
                    "kwargs": {},
                })
        if self._mteam_auto_enabled and self._mteam_auto_cron:
            try:
                trigger = CronTrigger.from_crontab(self._mteam_auto_cron)
            except Exception as err:
                logger.error(f"站点工具箱馒头登录检查定时任务 cron 无效：{self._mteam_auto_cron} - {err}")
            else:
                services.append({
                    "id": "sitetoolbox_mteam_login_check",
                    "name": "站点工具箱馒头登录检查",
                    "trigger": trigger,
                    "func": self.auto_check_mteam_login,
                    "kwargs": {},
                })
        return services

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
            {
                "path": "/repair/rss",
                "endpoint": _api_repair_selected_rss,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "尝试修复已选站点RSS",
                "description": "重新生成或规范化已选站点 RSS 地址，成功后写回站点配置并测试解析。",
            },
            {
                "path": "/repair/rss/{site_id}",
                "endpoint": _api_repair_one_rss,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "尝试修复单个站点RSS",
                "description": "重新生成或规范化指定站点 RSS 地址，成功后写回站点配置并测试解析。",
            },
            {
                "path": "/check/userdata",
                "endpoint": _api_check_userdata,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "检查站点用户数据完整性",
                "description": "检查站点最新用户数据是否存在错误、缺失或关键字段异常。",
            },
            {
                "path": "/cleanup/missing/preview",
                "endpoint": _api_preview_missing_torrents,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "预览缺失文件种子",
                "description": "扫描配置的下载器，预览 qBittorrent missingFiles 状态的种子。",
            },
            {
                "path": "/cleanup/missing",
                "endpoint": _api_cleanup_missing_torrents,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "清理缺失文件种子",
                "description": "清理最近一次预览中仍处于 missingFiles 状态的种子。",
            },
            {
                "path": "/mteam/login/check",
                "endpoint": _api_check_mteam_login,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "检查馒头登录历史",
                "description": "通过 M-Team API Access Token 查询真实登录历史，后台运行并保存最近结果。",
            },
        ]

    def get_form(self) -> Tuple[Optional[List[dict]], dict]:
        sites = [site for site in SiteOper().list_order_by_pri() if site and site.id]
        site_options = [
            {
                "title": f"{site.name} ({site.domain})" if site.domain else site.name,
                "value": site.id,
            }
            for site in sites
        ]
        downloader_options = _downloader_options()
        mteam_site_options = [
            {
                "title": f"{site.name} ({site.domain})" if site.domain else site.name,
                "value": site.id,
            }
            for site in sites
            if _is_mteam_site(site)
        ]
        return [
            {
                "component": "VForm",
                "content": [
                    _form_section("基础", [
                        {
                            "component": "VRow",
                            "content": [
                                _col(12, 4, {
                                    "component": "VSwitch",
                                    "props": {"model": "enabled", "label": "启用插件"},
                                }),
                                _col(12, 4, {
                                    "component": "VSwitch",
                                    "props": {"model": "auto_discover", "label": "自动获取 RSS"},
                                }),
                                _col(12, 4, {
                                    "component": "VSwitch",
                                    "props": {"model": "save_discovered", "label": "保存获取到的 RSS"},
                                }),
                            ],
                        },
                    ]),
                    _form_section("RSS 诊断", [
                        {
                            "component": "VRow",
                            "content": [
                                _col(12, 8, {
                                    "component": "VSelect",
                                    "props": {
                                        "model": "site_ids",
                                        "label": "站点",
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
                                        "label": "超时(秒)",
                                        "type": "number",
                                        "min": 5,
                                        "max": 120,
                                    },
                                }),
                            ],
                        },
                    ]),
                    _form_section("缺失种子", [
                        {
                            "component": "VRow",
                            "content": [
                                _col(12, 8, {
                                    "component": "VSelect",
                                    "props": {
                                        "model": "cleanup_downloader_names",
                                        "label": "下载器",
                                        "items": downloader_options,
                                        "multiple": True,
                                        "chips": True,
                                        "clearable": True,
                                    },
                                }),
                                _col(12, 4, {
                                    "component": "VSwitch",
                                    "props": {"model": "cleanup_delete_files", "label": "同时删除数据文件"},
                                }),
                            ],
                        },
                        {
                            "component": "VRow",
                            "content": [
                                _col(12, 4, {
                                    "component": "VSwitch",
                                    "props": {"model": "cleanup_auto_enabled", "label": "定时扫描"},
                                }),
                                _col(12, 8, {
                                    "component": "VTextField",
                                    "props": {
                                        "model": "cleanup_auto_cron",
                                        "label": "扫描周期(cron)",
                                        "placeholder": "0 */6 * * *",
                                    },
                                }),
                            ],
                        },
                    ]),
                    _form_section("系统错误", [
                        {
                            "component": "VRow",
                            "content": [
                                _col(12, 4, {
                                    "component": "VSelect",
                                    "props": {
                                        "model": "error_retention_days",
                                        "label": "错误保留时间",
                                        "items": _error_retention_options(),
                                    },
                                }),
                            ],
                        },
                    ]),
                    _form_section("馒头登录", [
                        {
                            "component": "VRow",
                            "content": [
                                _col(12, 6, {
                                    "component": "VSelect",
                                    "props": {
                                        "model": "mteam_site_ids",
                                        "label": "馒头站点",
                                        "items": mteam_site_options,
                                        "multiple": True,
                                        "chips": True,
                                        "clearable": True,
                                        "hint": "不选择时检查全部馒头站点",
                                        "persistent-hint": True,
                                    },
                                }),
                                _col(12, 3, {
                                    "component": "VTextField",
                                    "props": {
                                        "model": "mteam_warning_days",
                                        "label": "提醒天数",
                                        "type": "number",
                                        "min": 1,
                                        "max": 365,
                                    },
                                }),
                                _col(12, 3, {
                                    "component": "VSwitch",
                                    "props": {"model": "mteam_auto_enabled", "label": "定时检查"},
                                }),
                            ],
                        },
                        {
                            "component": "VRow",
                            "content": [
                                _col(12, 6, {
                                    "component": "VTextField",
                                    "props": {
                                        "model": "mteam_auto_cron",
                                        "label": "检查周期(cron)",
                                        "placeholder": "0 9 * * *",
                                    },
                                }),
                            ],
                        },
                    ]),
                    _form_section("站点适配", [
                        {
                            "component": "VRow",
                            "content": [
                                _col(12, 3, {
                                    "component": "VSwitch",
                                    "props": {"model": "patch_userdata", "label": "用户数据补丁"},
                                }),
                                _col(12, 9, {
                                    "component": "VTextarea",
                                    "props": {
                                        "model": "site_conf",
                                        "label": "适配规则",
                                        "rows": 10,
                                        "auto-grow": True,
                                        "placeholder": "domain|base64(json)",
                                    },
                                }),
                            ],
                        },
                    ]),
                ],
            }
        ], {
            "enabled": True,
            "site_ids": [],
            "timeout": 20,
            "auto_discover": True,
            "save_discovered": False,
            "latest_results": [],
            "latest_userdata_results": [],
            "latest_userdata_checked_at": "",
            "latest_job_states": {},
            "patch_userdata": True,
            "site_conf": "",
            "cleanup_downloader_names": [],
            "cleanup_delete_files": False,
            "cleanup_auto_enabled": False,
            "cleanup_auto_cron": "0 */6 * * *",
            "latest_missing_preview": {},
            "latest_missing_cleanup": {},
            "error_retention_days": 3,
            "mteam_site_ids": [],
            "mteam_warning_days": 25,
            "mteam_auto_enabled": False,
            "mteam_auto_cron": "0 9 * * *",
            "latest_mteam_login_check": {},
        }

    def get_page(self) -> Optional[List[dict]]:
        return _toolbox_page(self)

    def stop_service(self):
        self._unpatch_userdata()
        self._uninstall_error_capture()

    def _config_payload(self, **overrides) -> Dict[str, Any]:
        payload = {
            "enabled": self._enabled,
            "site_ids": self._site_ids,
            "timeout": self._timeout,
            "auto_discover": self._auto_discover,
            "save_discovered": self._save_discovered,
            "latest_results": self._latest_results,
            "latest_userdata_results": self._latest_userdata_results,
            "latest_userdata_checked_at": self._latest_userdata_checked_at,
            "latest_job_states": self._latest_job_states,
            "patch_userdata": self._patch_userdata,
            "site_conf": self._site_conf,
            "cleanup_downloader_names": self._cleanup_downloader_names,
            "cleanup_delete_files": self._cleanup_delete_files,
            "cleanup_auto_enabled": self._cleanup_auto_enabled,
            "cleanup_auto_cron": self._cleanup_auto_cron,
            "latest_missing_preview": self._latest_missing_preview,
            "latest_missing_cleanup": self._latest_missing_cleanup,
            "error_retention_days": self._error_retention_days,
            "mteam_site_ids": self._mteam_site_ids,
            "mteam_warning_days": self._mteam_warning_days,
            "mteam_auto_enabled": self._mteam_auto_enabled,
            "mteam_auto_cron": self._mteam_auto_cron,
            "latest_mteam_login_check": self._latest_mteam_login_check,
        }
        payload.update(overrides)
        return payload

    def _save_results(self, results: List[Dict[str, Any]]):
        self._latest_results = results
        self.__class__._latest_results = results
        self.update_config(self._config_payload(latest_results=results))

    def _save_userdata_results(self, results: List[Dict[str, Any]]):
        self._latest_userdata_results = results
        self.__class__._latest_userdata_results = results
        self._latest_userdata_checked_at = _now()
        self.__class__._latest_userdata_checked_at = self._latest_userdata_checked_at
        self.update_config(self._config_payload(
            latest_userdata_results=results,
            latest_userdata_checked_at=self._latest_userdata_checked_at,
        ))

    def _save_missing_preview(self, preview: Dict[str, Any]):
        self._latest_missing_preview = preview
        self.__class__._latest_missing_preview = preview
        self.update_config(self._config_payload(latest_missing_preview=preview))

    def _save_missing_cleanup(self, cleanup: Dict[str, Any]):
        self._latest_missing_cleanup = cleanup
        self.__class__._latest_missing_cleanup = cleanup
        self.update_config(self._config_payload(latest_missing_cleanup=cleanup))

    def _save_mteam_login_check(self, result: Dict[str, Any]):
        self._latest_mteam_login_check = result
        self.__class__._latest_mteam_login_check = result
        self.update_config(self._config_payload(latest_mteam_login_check=result))

    def _set_job_state(self, key: str, name: str, status: str, message: str = "",
                       started_at: Optional[str] = None, finished_at: Optional[str] = None):
        with self.__class__._jobs_lock:
            states = dict(self._latest_job_states or {})
            current = dict(states.get(key) or {})
            current.update({
                "key": key,
                "name": name,
                "status": status,
                "message": message,
            })
            if started_at is not None:
                current["started_at"] = started_at
            if finished_at is not None:
                current["finished_at"] = finished_at
            states[key] = current
            self._latest_job_states = states
            self.__class__._latest_job_states = states
        self.update_config(self._config_payload(latest_job_states=states))

    def _start_background_job(self, key: str, name: str, func: Callable[[], str]) -> schemas.Response:
        with self.__class__._jobs_lock:
            state = (self._latest_job_states or {}).get(key) or {}
            if state.get("status") == "running":
                return schemas.Response(
                    success=True,
                    message=f"{name}已在后台运行，开始时间：{state.get('started_at') or '-'}",
                    data=state,
                )

        self._set_job_state(key, name, "running", "后台运行中", started_at=_now(), finished_at="")

        def runner():
            try:
                message = func() or "任务完成"
                self._set_job_state(key, name, "success", message, finished_at=_now())
            except Exception as err:
                logger.error(f"站点工具箱后台任务失败：{name} - {err}")
                self._set_job_state(key, name, "error", str(err), finished_at=_now())

        Thread(target=runner, name=f"sitetoolbox-{key}", daemon=True).start()
        return schemas.Response(
            success=True,
            message=f"{name}已在后台运行，完成后刷新页面查看最新结果",
            data=self._latest_job_states.get(key),
        )

    def auto_preview_missing_torrents(self):
        if not self._cleanup_downloader_names:
            logger.warning("站点工具箱缺失种子定时扫描跳过：未配置下载器")
            return
        name = "缺失种子定时扫描"
        self._set_job_state("missing_auto", name, "running", "后台运行中", started_at=_now(), finished_at="")
        try:
            preview = _build_missing_torrent_preview(self._cleanup_downloader_names)
            self._save_missing_preview(preview)
            message = f"{preview.get('total_count', 0)} 个，{_format_size(preview.get('total_size', 0))}"
            self._set_job_state("missing_auto", name, "success", message, finished_at=_now())
            logger.info(f"站点工具箱缺失种子定时扫描完成：{message}")
        except Exception as err:
            self._set_job_state("missing_auto", name, "error", str(err), finished_at=_now())
            raise

    def auto_check_mteam_login(self):
        name = "馒头登录定时检查"
        self._set_job_state("mteam_login_auto", name, "running", "后台运行中", started_at=_now(), finished_at="")
        try:
            result = _build_mteam_login_check(self._mteam_site_ids, self._mteam_warning_days)
            self._save_mteam_login_check(result)
            warning_count = int(result.get("warning_count") or 0)
            error_count = int(result.get("error_count") or 0)
            total_count = int(result.get("total_count") or 0)
            message = f"异常 {warning_count + error_count}/{total_count}"
            self._set_job_state("mteam_login_auto", name, "success", message, finished_at=_now())
            if warning_count or error_count:
                self.systemmessage.put(
                    f"馒头登录检查发现异常 {warning_count + error_count}/{total_count}，请在站点工具箱查看。",
                    title=self.plugin_name,
                )
            logger.info(f"站点工具箱馒头登录定时检查完成：{message}")
        except Exception as err:
            self._set_job_state("mteam_login_auto", name, "error", str(err), finished_at=_now())
            raise

    def _install_error_capture(self):
        cls = self.__class__
        logger_cls = type(logger)

        if not cls._error_capture_installed:
            current_method = getattr(logger_cls, "logger")
            original_method = getattr(current_method, "_sitetoolbox_original", current_method)
            cls._original_logger_method = original_method

            def wrapped_logger(log_self, method: str, msg: str, *args, **kwargs):
                caller = _caller_info()
                try:
                    return original_method(log_self, method, msg, *args, **kwargs)
                finally:
                    if str(method or "").lower() in {"error", "critical"}:
                        plugin = cls._instance
                        if plugin and plugin._enabled:
                            plugin._record_system_error(
                                level=str(method).upper(),
                                message=_format_log_message(msg, args),
                                source=caller.get("source") or "moviepilot",
                                file=caller.get("file") or "",
                                line=caller.get("line") or 0,
                                traceback_text=_format_exc_info(kwargs.get("exc_info")),
                            )

            wrapped_logger._sitetoolbox_error_capture = True
            wrapped_logger._sitetoolbox_original = original_method
            setattr(logger_cls, "logger", wrapped_logger)
            cls._error_capture_installed = True

        root_logger = logging.getLogger()
        for handler in list(root_logger.handlers):
            if getattr(handler, "_sitetoolbox_error_capture", False):
                root_logger.removeHandler(handler)
        cls._standard_error_handler = _SiteToolboxErrorHandler(cls)
        root_logger.addHandler(cls._standard_error_handler)

    @classmethod
    def _uninstall_error_capture(cls):
        logger_cls = type(logger)
        current_method = getattr(logger_cls, "logger")
        if getattr(current_method, "_sitetoolbox_error_capture", False):
            setattr(logger_cls, "logger", getattr(current_method, "_sitetoolbox_original", cls._original_logger_method))
        cls._error_capture_installed = False
        cls._original_logger_method = None

        root_logger = logging.getLogger()
        for handler in list(root_logger.handlers):
            if getattr(handler, "_sitetoolbox_error_capture", False):
                root_logger.removeHandler(handler)
        cls._standard_error_handler = None

    def _record_system_error(self, level: str, message: str, source: str = "",
                             file: str = "", line: int = 0, traceback_text: str = ""):
        if not self._enabled:
            return
        record = {
            "ts": int(time.time()),
            "time": _now(),
            "level": (level or "ERROR").upper(),
            "source": source or "moviepilot",
            "file": file or "",
            "line": line or 0,
            "message": _clip_text(message, 4000),
            "traceback": _clip_text(traceback_text, 8000),
        }
        path = self._error_log_path()
        with self.__class__._error_log_lock:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as file_obj:
                    file_obj.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                now = time.time()
                if now - self.__class__._last_error_prune > 60:
                    _write_error_records(path, _filter_error_records(_read_error_records(path), self._error_retention_days))
                    self.__class__._last_error_prune = now
            except Exception:
                return

    def _get_error_records(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        path = self._error_log_path()
        with self.__class__._error_log_lock:
            records = _read_error_records(path)
            filtered = _filter_error_records(records, self._error_retention_days)
            if len(filtered) != len(records):
                _write_error_records(path, filtered)
            self.__class__._last_error_prune = time.time()
        filtered.sort(key=lambda item: _error_record_ts(item), reverse=True)
        return filtered[:limit] if limit else filtered

    def _error_log_path(self):
        return self.get_data_path() / "system_errors.jsonl"

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
                logger.error(f"站点工具箱索引配置加载失败：{domain} - {err}")
                self.systemmessage.put(f"{domain} 索引配置加载失败：{err}", title=self.plugin_name)
        if count:
            logger.info(f"站点工具箱索引配置已加载：{count} 个")

    @classmethod
    def _patch_userdata_parsers(cls):
        if cls._patched:
            logger.info(f"站点工具箱用户数据解析规则已更新：{len(cls._userdata_rules)} 个")
            return

        patched_count = 0
        for parser_cls in _iter_site_parser_classes():
            for method in (
                "_parse_site_page",
                "_parse_user_base_info",
                "_parse_user_traffic_info",
                "_parse_user_detail_info",
                "_parse_user_torrent_seeding_info",
            ):
                if method not in parser_cls.__dict__:
                    continue
                key = (parser_cls, method)
                if key in cls._originals:
                    continue
                original = getattr(parser_cls, method)
                cls._originals[key] = original

                def wrapped(self, html_text, *args, _original=original, _method=method, **kwargs):
                    if _method == "_parse_user_torrent_seeding_info":
                        _apply_xloli_uuid_userdata(parser=self, html_text=html_text)
                    try:
                        result = _original(self, html_text, *args, **kwargs)
                    except TypeError as err:
                        if _method == "_parse_user_torrent_seeding_info" and _is_missing_userid_membership_error(err):
                            logger.warning(
                                "站点工具箱跳过无用户ID的做种翻页解析："
                                f"{getattr(self, '_site_name', '') or getattr(self, '_site_domain', '')}"
                            )
                            return False
                        raise
                    if _method == "_parse_user_torrent_seeding_info":
                        result = _normalize_xloli_next_page(parser=self, next_page=result)
                    else:
                        _apply_xloli_uuid_userdata(parser=self, html_text=html_text)
                    _apply_site_userdata_rules(self, html_text, cls._userdata_rules)
                    return result

                setattr(parser_cls, method, wrapped)
                patched_count += 1

        cls._patched = patched_count > 0
        logger.info(f"站点工具箱用户数据解析规则已启用：{len(cls._userdata_rules)} 个，挂载方法 {patched_count} 个")

    @classmethod
    def _unpatch_userdata(cls):
        if not cls._patched:
            return
        for (parser_cls, method), original in list(cls._originals.items()):
            setattr(parser_cls, method, original)
        cls._originals = {}
        cls._patched = False
        logger.info("站点工具箱用户数据解析规则已停用")


class _SiteToolboxErrorHandler(logging.Handler):
    _sitetoolbox_error_capture = True

    def __init__(self, plugin_cls):
        super().__init__(level=logging.ERROR)
        self._plugin_cls = plugin_cls

    def emit(self, record: logging.LogRecord):
        plugin = self._plugin_cls._instance
        if not plugin or not plugin._enabled:
            return
        try:
            plugin._record_system_error(
                level=record.levelname,
                message=record.getMessage(),
                source=record.name or "logging",
                file=record.pathname or "",
                line=record.lineno or 0,
                traceback_text=record.exc_text or (
                    logging.Formatter().formatException(record.exc_info) if record.exc_info else ""
                ),
            )
        except Exception:
            return


def _api_test_selected_rss(payload: Optional[dict] = Body(default=None)) -> schemas.Response:
    plugin = sitetoolbox._instance
    if not plugin:
        return schemas.Response(success=False, message="插件未初始化")
    site_ids = _int_list((payload or {}).get("site_ids")) or plugin._site_ids
    if not site_ids:
        return schemas.Response(success=False, message="未选择站点")

    def worker() -> str:
        results = [_test_site_rss(site_id) for site_id in site_ids]
        plugin._save_results(results)
        ok_count = len([item for item in results if item.get("state") == "success"])
        return f"RSS测试完成：成功 {ok_count}/{len(results)}"

    return plugin._start_background_job("rss_test", "RSS测试", worker)


def _api_test_one_rss(site_id: int) -> schemas.Response:
    plugin = sitetoolbox._instance
    if not plugin:
        return schemas.Response(success=False, message="插件未初始化")

    def worker() -> str:
        result = _test_site_rss(site_id)
        kept = [item for item in sitetoolbox._latest_results if item.get("site_id") != site_id]
        plugin._save_results([result, *kept][:100])
        return result.get("message") or "RSS测试完成"

    return plugin._start_background_job(f"rss_test_{site_id}", "单站RSS测试", worker)


def _api_repair_selected_rss(payload: Optional[dict] = Body(default=None)) -> schemas.Response:
    plugin = sitetoolbox._instance
    if not plugin:
        return schemas.Response(success=False, message="插件未初始化")
    site_ids = _int_list((payload or {}).get("site_ids")) or plugin._site_ids
    if not site_ids:
        return schemas.Response(success=False, message="未选择站点")

    def worker() -> str:
        results = [_test_site_rss(site_id, repair=True) for site_id in site_ids]
        plugin._save_results(results)
        ok_count = len([item for item in results if item.get("state") == "success"])
        fixed_count = len([item for item in results if item.get("fixed")])
        return f"RSS修复完成：成功 {ok_count}/{len(results)}，写回 {fixed_count} 个"

    return plugin._start_background_job("rss_repair", "RSS修复", worker)


def _api_repair_one_rss(site_id: int) -> schemas.Response:
    plugin = sitetoolbox._instance
    if not plugin:
        return schemas.Response(success=False, message="插件未初始化")

    def worker() -> str:
        result = _test_site_rss(site_id, repair=True)
        kept = [item for item in sitetoolbox._latest_results if item.get("site_id") != site_id]
        plugin._save_results([result, *kept][:100])
        return result.get("message") or "RSS修复完成"

    return plugin._start_background_job(f"rss_repair_{site_id}", "单站RSS修复", worker)


def _api_check_userdata(payload: Optional[dict] = Body(default=None)) -> schemas.Response:
    plugin = sitetoolbox._instance
    if not plugin:
        return schemas.Response(success=False, message="插件未初始化")
    site_ids = _int_list((payload or {}).get("site_ids"))

    def worker() -> str:
        results = _check_userdata_health(site_ids=site_ids)
        plugin._save_userdata_results(results)
        bad_count = len([item for item in results if item.get("state") != "success"])
        return f"用户数据检查完成：异常 {bad_count}/{len(results)}"

    return plugin._start_background_job("userdata_check", "用户数据检查", worker)


def _api_preview_missing_torrents(payload: Optional[dict] = Body(default=None)) -> schemas.Response:
    plugin = sitetoolbox._instance
    if not plugin:
        return schemas.Response(success=False, message="插件未初始化")
    downloader_names = _str_list((payload or {}).get("downloader_names") or (payload or {}).get("downloaders"))
    downloader_names = downloader_names or plugin._cleanup_downloader_names
    if not downloader_names:
        return schemas.Response(success=False, message="未选择下载器")

    def worker() -> str:
        preview = _build_missing_torrent_preview(downloader_names)
        plugin._save_missing_preview(preview)
        total_count = preview.get("total_count", 0)
        total_size = _format_size(preview.get("total_size", 0))
        error_count = len(preview.get("errors") or [])
        message = f"缺失种子预览完成：{total_count} 个，{total_size}"
        if error_count:
            message += f"，{error_count} 个下载器异常"
        return message

    return plugin._start_background_job("missing_preview", "缺失种子预览", worker)


def _api_cleanup_missing_torrents(payload: Optional[dict] = Body(default=None)) -> schemas.Response:
    plugin = sitetoolbox._instance
    if not plugin:
        return schemas.Response(success=False, message="插件未初始化")
    downloader_names = _str_list((payload or {}).get("downloader_names") or (payload or {}).get("downloaders"))
    downloader_names = downloader_names or plugin._cleanup_downloader_names
    if not downloader_names:
        return schemas.Response(success=False, message="未选择下载器")

    preview = plugin._latest_missing_preview if isinstance(plugin._latest_missing_preview, dict) else {}
    preview_items = preview.get("items") if isinstance(preview.get("items"), list) else []
    if not preview_items:
        return schemas.Response(success=False, message="请先预览缺失文件种子")

    selected_names = set(downloader_names)
    cleanup_items = [item for item in preview_items if item.get("downloader") in selected_names]
    if not cleanup_items:
        return schemas.Response(success=False, message="最近一次预览中没有选中下载器的缺失种子")

    delete_files = _to_bool((payload or {}).get("delete_files"), plugin._cleanup_delete_files)

    def worker() -> str:
        cleanup = _cleanup_missing_torrents_from_preview(cleanup_items, delete_files=delete_files)
        plugin._save_missing_cleanup(cleanup)

        refreshed = _build_missing_torrent_preview(downloader_names)
        plugin._save_missing_preview(refreshed)

        deleted_count = cleanup.get("deleted_count", 0)
        failed_count = cleanup.get("failed_count", 0)
        skipped_count = cleanup.get("skipped_count", 0)
        message = f"缺失种子清理完成：删除 {deleted_count} 个，跳过 {skipped_count} 个"
        if failed_count:
            message += f"，失败 {failed_count} 个"
        return message

    return plugin._start_background_job("missing_cleanup", "缺失种子清理", worker)


def _api_check_mteam_login(payload: Optional[dict] = Body(default=None)) -> schemas.Response:
    plugin = sitetoolbox._instance
    if not plugin:
        return schemas.Response(success=False, message="插件未初始化")
    site_ids = _int_list((payload or {}).get("site_ids")) or plugin._mteam_site_ids

    def worker() -> str:
        result = _build_mteam_login_check(site_ids, plugin._mteam_warning_days)
        plugin._save_mteam_login_check(result)
        warning_count = int(result.get("warning_count") or 0)
        error_count = int(result.get("error_count") or 0)
        total_count = int(result.get("total_count") or 0)
        return f"馒头登录检查完成：异常 {warning_count + error_count}/{total_count}"

    return plugin._start_background_job("mteam_login_check", "馒头登录检查", worker)


def _build_mteam_login_check(site_ids: Optional[List[int]] = None, warning_days: int = 25) -> Dict[str, Any]:
    selected_ids = set(_int_list(site_ids))
    sites = [
        site for site in SiteOper().list_order_by_pri()
        if site and site.id and (not selected_ids or site.id in selected_ids) and _is_mteam_site(site)
    ]
    results = [_check_mteam_login_site(site, warning_days) for site in sites]
    warning_count = len([item for item in results if item.get("state") == "warning"])
    error_count = len([item for item in results if item.get("state") == "error"])
    return {
        "checked_at": _now(),
        "warning_days": warning_days,
        "site_ids": list(selected_ids),
        "total_count": len(results),
        "warning_count": warning_count,
        "error_count": error_count,
        "items": results,
    }


def _check_mteam_login_site(site: Any, warning_days: int) -> Dict[str, Any]:
    start = time.monotonic()
    site_name = getattr(site, "name", "") or "馒头"
    domain = _mteam_domain(site)
    base = {
        "site_id": getattr(site, "id", None),
        "site_name": site_name,
        "domain": domain,
        "username": "",
        "user_id": "",
        "last_login_at": "",
        "last_login_days": None,
        "last_browse_at": "",
        "record_count": 0,
        "checked_at": _now(),
        "seconds": 0,
    }
    if not getattr(site, "is_active", True):
        return {**base, "state": "error", "message": "站点未启用", "seconds": _elapsed(start)}
    if not getattr(site, "apikey", ""):
        return {**base, "state": "error", "message": "未配置 M-Team API Access Token", "seconds": _elapsed(start)}

    profile_payload, profile_error = _mteam_api_json(site, "member/profile", {})
    profile_data = profile_payload.get("data") if isinstance(profile_payload, dict) else {}
    if isinstance(profile_data, dict):
        base["username"] = str(profile_data.get("username") or "")
        base["user_id"] = str(profile_data.get("id") or "")
        base["last_browse_at"] = _normalize_datetime_text(_find_named_value(
            profile_data,
            ("lastBrowse", "lastBrowseTime", "lastBrowseDate", "lastAccess", "lastAccessTime", "lastVisit"),
        ))

    history_params = {"pageNumber": 1, "pageSize": 10}
    if base["user_id"]:
        history_params["uid"] = _mteam_user_id_value(base["user_id"])
    history_payload, history_error = _mteam_api_json(
        site,
        "member/queryUserLoginHistory",
        history_params,
        json_body=False,
    )
    if history_error:
        message = history_error
        if profile_error:
            message = f"{message}；profile：{profile_error}"
        return {**base, "state": "error", "message": message, "seconds": _elapsed(start)}

    records = _mteam_history_records(history_payload)
    base["record_count"] = len(records)
    if not records:
        return {**base, "state": "warning", "message": "API 未返回登录历史", "seconds": _elapsed(start)}

    login_time, login_ts = _latest_mteam_login_time(records)
    base["last_login_at"] = login_time
    if login_ts:
        days = max(0, int((time.time() - login_ts) // 86400))
        base["last_login_days"] = days
        if days > warning_days:
            return {
                **base,
                "state": "warning",
                "message": f"最近网页登录已超过 {warning_days} 天",
                "seconds": _elapsed(start),
            }
        return {
            **base,
            "state": "success",
            "message": f"最近网页登录 {days} 天前",
            "seconds": _elapsed(start),
        }
    return {
        **base,
        "state": "warning",
        "message": "登录历史存在，但无法解析登录时间",
        "seconds": _elapsed(start),
    }


def _mteam_api_json(site: Any, path: str, payload: Optional[dict], json_body: bool = True) -> Tuple[dict, str]:
    domain = _mteam_domain(site)
    url = f"https://api.{domain}/api/{path.lstrip('/')}"
    headers = {
        "Content-Type": "application/json" if json_body else "application/x-www-form-urlencoded; charset=UTF-8",
        "User-Agent": getattr(site, "ua", "") or settings.USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "x-api-key": getattr(site, "apikey", "") or "",
    }
    request = RequestUtils(
        headers=headers,
        timeout=getattr(site, "timeout", None) or sitetoolbox._timeout,
        proxies=settings.PROXY if getattr(site, "proxy", False) else None,
        referer=f"{getattr(site, 'url', '') or f'https://{domain}/'}index",
    )
    if json_body:
        res = request.post_res(url=url, json=payload or {}, allow_redirects=False)
    else:
        res = request.post_res(url=url, data=payload or {}, allow_redirects=False)
    if res is None:
        return {}, "无法打开 M-Team API"
    if 300 <= int(res.status_code or 0) < 400:
        location = res.headers.get("Location") or res.headers.get("location") or ""
        return {}, f"M-Team API 被重定向：{_mask_url(location) or res.status_code}"
    if res.status_code != 200:
        return {}, f"M-Team API 返回状态码 {res.status_code}"
    try:
        data = res.json() or {}
    except Exception:
        return {}, "M-Team API 返回非 JSON 内容"
    code = data.get("code")
    if code not in (None, 0, "0"):
        return data, str(data.get("message") or data.get("msg") or f"M-Team API 返回 code={code}")
    if "data" not in data:
        return data, "M-Team API 未返回 data"
    return data, ""


def _mteam_history_records(payload: Any) -> List[dict]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    candidates = [data]
    if isinstance(data, dict):
        candidates.extend(data.get(key) for key in ("data", "records", "list", "items", "content", "rows"))
    for candidate in candidates:
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
    return []


def _mteam_user_id_value(value: Any) -> Any:
    text = str(value or "").strip()
    if text.isdigit():
        return int(text)
    return text


def _latest_mteam_login_time(records: List[dict]) -> Tuple[str, float]:
    candidates: List[Tuple[str, float]] = []
    for record in records:
        value = _find_named_value(record, (
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
        ))
        text = _normalize_datetime_text(value)
        ts = _parse_datetime_ts(value)
        if text or ts:
            candidates.append((text or _format_timestamp(ts), ts))
    parsed = [item for item in candidates if item[1]]
    if parsed:
        text, ts = max(parsed, key=lambda item: item[1])
        return text or _format_timestamp(ts), ts
    return candidates[0] if candidates else ("", 0)


def _find_named_value(value: Any, names: Tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        lower_names = {name.lower() for name in names}
        for key, item in value.items():
            if str(key).lower() in lower_names and item not in (None, ""):
                return item
        for item in value.values():
            found = _find_named_value(item, names)
            if found not in (None, ""):
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_named_value(item, names)
            if found not in (None, ""):
                return found
    return None


def _parse_datetime_ts(value: Any) -> float:
    if value in (None, ""):
        return 0
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000
        return number if number > 0 else 0
    text = str(value).strip()
    if not text:
        return 0
    if re.fullmatch(r"\d{10,13}", text):
        return _parse_datetime_ts(int(text))
    normalized = text.replace("T", " ").replace("Z", "").split("+", 1)[0].strip()
    normalized = re.sub(r"\.\d+", "", normalized)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y-%m-%d"):
        try:
            return time.mktime(time.strptime(normalized, fmt))
        except Exception:
            continue
    return 0


def _normalize_datetime_text(value: Any) -> str:
    ts = _parse_datetime_ts(value)
    if ts:
        return _format_timestamp(ts)
    return str(value or "").strip()


def _mteam_domain(site: Any) -> str:
    domain = getattr(site, "domain", "") or getattr(site, "url", "") or "m-team.cc"
    normalized = StringUtils.get_url_domain(domain) or _normalize_domain(domain)
    if normalized == "kp.m-team.cc":
        return "m-team.cc"
    if normalized == "kp.m-team.io":
        return "m-team.io"
    if normalized.endswith(".m-team.cc"):
        return "m-team.cc"
    if normalized.endswith(".m-team.io"):
        return "m-team.io"
    return normalized or "m-team.cc"


def _is_mteam_site(site: Any) -> bool:
    if not site:
        return False
    domain = _normalize_domain(getattr(site, "domain", "") or getattr(site, "url", ""))
    url = str(getattr(site, "url", "") or "").lower()
    return domain in {"m-team.cc", "m-team.io"} or domain.endswith(".m-team.cc") or domain.endswith(".m-team.io") or "m-team" in url


def _build_missing_torrent_preview(downloader_names: List[str]) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    services = DownloaderHelper().get_services(name_filters=downloader_names)

    for downloader_name in downloader_names:
        service = services.get(downloader_name)
        if not service or not service.instance:
            errors.append({"downloader": downloader_name, "message": "下载器未启用或未连接"})
            continue
        if service.type != "qbittorrent":
            errors.append({"downloader": downloader_name, "message": f"暂不支持 {service.type or '未知'} 下载器"})
            continue

        server = service.instance
        try:
            if hasattr(server, "is_inactive") and server.is_inactive():
                server.reconnect()
            torrents, failed = server.get_torrents()
        except Exception as err:
            errors.append({"downloader": downloader_name, "message": f"查询失败：{err}"})
            continue
        if failed:
            errors.append({"downloader": downloader_name, "message": "查询种子列表失败"})
            continue

        for torrent in torrents or []:
            if not _is_missing_torrent(torrent):
                continue
            items.append(_missing_torrent_item(downloader_name, torrent))

    items = sorted(items, key=lambda item: (item.get("downloader") or "", item.get("save_path") or "", item.get("name") or ""))
    total_size = sum(int(item.get("size") or 0) for item in items)
    return {
        "created_at": _now(),
        "downloaders": downloader_names,
        "total_count": len(items),
        "total_size": total_size,
        "by_downloader": _summarize_missing_items(items, "downloader"),
        "by_save_path": _summarize_missing_items(items, "save_path"),
        "items": items,
        "errors": errors,
    }


def _cleanup_missing_torrents_from_preview(items: List[Dict[str, Any]], delete_files: bool = False) -> Dict[str, Any]:
    services = DownloaderHelper().get_services(name_filters=list(dict.fromkeys(item.get("downloader") for item in items)))
    errors: List[Dict[str, Any]] = []
    result_items: List[Dict[str, Any]] = []
    deleted_count = 0
    skipped_count = 0
    failed_count = 0

    for downloader_name, downloader_items in _group_items(items, "downloader").items():
        service = services.get(downloader_name)
        if not service or not service.instance:
            message = "下载器未启用或未连接"
            errors.append({"downloader": downloader_name, "message": message})
            failed_count += len(downloader_items)
            result_items.extend(_cleanup_result_rows(downloader_items, "failed", message))
            continue
        if service.type != "qbittorrent":
            message = f"暂不支持 {service.type or '未知'} 下载器"
            errors.append({"downloader": downloader_name, "message": message})
            failed_count += len(downloader_items)
            result_items.extend(_cleanup_result_rows(downloader_items, "failed", message))
            continue

        server = service.instance
        hashes = [item.get("hash") for item in downloader_items if item.get("hash")]
        try:
            if hasattr(server, "is_inactive") and server.is_inactive():
                server.reconnect()
            torrents, failed = server.get_torrents(ids=hashes)
        except Exception as err:
            message = f"清理前复查失败：{err}"
            errors.append({"downloader": downloader_name, "message": message})
            failed_count += len(downloader_items)
            result_items.extend(_cleanup_result_rows(downloader_items, "failed", message))
            continue
        if failed:
            message = "清理前复查种子状态失败"
            errors.append({"downloader": downloader_name, "message": message})
            failed_count += len(downloader_items)
            result_items.extend(_cleanup_result_rows(downloader_items, "failed", message))
            continue

        missing_hashes = {_torrent_hash(torrent) for torrent in torrents or [] if _is_missing_torrent(torrent)}
        missing_hashes.discard("")
        skipped_items = [item for item in downloader_items if item.get("hash") not in missing_hashes]
        skipped_count += len(skipped_items)
        result_items.extend(_cleanup_result_rows(skipped_items, "skipped", "当前已不是 missingFiles 状态"))

        delete_hashes = [item.get("hash") for item in downloader_items if item.get("hash") in missing_hashes]
        if not delete_hashes:
            continue
        try:
            deleted = bool(server.delete_torrents(delete_file=delete_files, ids=delete_hashes))
        except Exception as err:
            deleted = False
            errors.append({"downloader": downloader_name, "message": f"删除失败：{err}"})
        if deleted:
            deleted_count += len(delete_hashes)
            result_items.extend(_cleanup_result_rows(
                [item for item in downloader_items if item.get("hash") in missing_hashes],
                "deleted",
                "已删除下载器任务",
            ))
        else:
            failed_count += len(delete_hashes)
            result_items.extend(_cleanup_result_rows(
                [item for item in downloader_items if item.get("hash") in missing_hashes],
                "failed",
                "删除失败",
            ))

    return {
        "cleaned_at": _now(),
        "delete_files": delete_files,
        "candidate_count": len(items),
        "deleted_count": deleted_count,
        "skipped_count": skipped_count,
        "failed_count": failed_count,
        "items": result_items,
        "errors": errors,
    }


def _missing_torrent_item(downloader_name: str, torrent: Any) -> Dict[str, Any]:
    size = _torrent_int(torrent, "total_size", "size")
    return {
        "downloader": downloader_name,
        "hash": _torrent_hash(torrent),
        "name": _torrent_text(torrent, "name"),
        "state": _torrent_text(torrent, "state"),
        "progress": _torrent_progress(torrent),
        "size": size,
        "save_path": _torrent_text(torrent, "save_path"),
        "content_path": _torrent_text(torrent, "content_path"),
        "category": _torrent_text(torrent, "category"),
        "tags": _torrent_text(torrent, "tags"),
        "tracker": _torrent_text(torrent, "tracker"),
        "last_activity": _format_timestamp(_torrent_int(torrent, "last_activity")),
    }


def _is_missing_torrent(torrent: Any) -> bool:
    return _torrent_text(torrent, "state").lower() == "missingfiles"


def _torrent_hash(torrent: Any) -> str:
    return _torrent_text(torrent, "hash") or _torrent_text(torrent, "hashString")


def _torrent_text(torrent: Any, key: str) -> str:
    value = _torrent_value(torrent, key)
    return "" if value is None else str(value)


def _torrent_int(torrent: Any, *keys: str) -> int:
    for key in keys:
        value = _torrent_value(torrent, key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _torrent_progress(torrent: Any) -> float:
    value = _torrent_value(torrent, "progress")
    try:
        progress = float(value or 0)
    except (TypeError, ValueError):
        return 0
    if progress <= 1:
        progress *= 100
    return round(progress, 2)


def _torrent_value(torrent: Any, key: str) -> Any:
    if isinstance(torrent, dict):
        return torrent.get(key)
    if hasattr(torrent, "get"):
        try:
            return torrent.get(key)
        except Exception:
            pass
    return getattr(torrent, key, None)


def _summarize_missing_items(items: List[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    summary: Dict[str, Dict[str, Any]] = {}
    for item in items:
        name = item.get(key) or "-"
        bucket = summary.setdefault(name, {"name": name, "count": 0, "size": 0})
        bucket["count"] += 1
        bucket["size"] += int(item.get("size") or 0)
    return sorted(summary.values(), key=lambda item: (-item.get("count", 0), item.get("name") or ""))


def _group_items(items: List[Dict[str, Any]], key: str) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for item in items:
        name = item.get(key) or ""
        grouped.setdefault(name, []).append(item)
    return grouped


def _cleanup_result_rows(items: List[Dict[str, Any]], state: str, message: str) -> List[Dict[str, Any]]:
    return [
        {
            "downloader": item.get("downloader"),
            "hash": item.get("hash"),
            "name": item.get("name"),
            "size": item.get("size"),
            "state": state,
            "message": message,
        }
        for item in items
    ]


def _test_site_rss(site_id: int, repair: bool = False) -> Dict[str, Any]:
    start = time.monotonic()
    site = SiteOper().get(site_id)
    if not site:
        return _result(site_id=site_id, state="error", message="站点不存在", seconds=0)
    if not getattr(site, "is_active", True):
        return _result(site=site, state="error", message="站点未启用", seconds=_elapsed(start))
    if not site.url:
        return _result(site=site, state="error", message="站点地址为空", seconds=_elapsed(start))

    rss_url = _normalize_rss_url(site.url, site.rss)
    source = "saved"
    fixed = False
    should_write_back = False
    if not rss_url and sitetoolbox._auto_discover:
        rss_url, message = _discover_rss_url(site)
        if not rss_url:
            return _result(site=site, state="error", message=message or "未获取到RSS链接", seconds=_elapsed(start))
        source = "discovered"
        if repair or sitetoolbox._save_discovered:
            should_write_back = True
    elif repair and rss_url and rss_url != (site.rss or "").strip():
        should_write_back = True

    if not rss_url:
        return _result(site=site, state="error", message="站点未配置RSS地址", seconds=_elapsed(start))

    items = _parse_rss(site, rss_url)
    if repair and items is False and source == "saved" and sitetoolbox._auto_discover:
        discovered_url, message = _discover_rss_url(site)
        if discovered_url and discovered_url != rss_url:
            discovered_items = _parse_rss(site, discovered_url)
            if discovered_items not in (None, False):
                rss_url = discovered_url
                items = discovered_items
                source = "repaired"
                should_write_back = True
        logger.info(f"{site.name} RSS修复尝试：{message or '已尝试重新生成'}")
    if items is None:
        return _result(site=site, state="error", message="RSS链接已过期", rss_url=rss_url, source=source, fixed=fixed, seconds=_elapsed(start))
    if items is False:
        return _result(site=site, state="error", message="RSS请求或解析失败", rss_url=rss_url, source=source, fixed=fixed, seconds=_elapsed(start))
    if not items:
        return _result(site=site, state="warning", message="RSS可访问但没有解析到条目", rss_url=rss_url, source=source, fixed=fixed, count=0, seconds=_elapsed(start))

    samples = [
        {
            "title": item.get("title"),
            "pubdate": str(item.get("pubdate") or ""),
            "has_enclosure": bool(item.get("enclosure")),
        }
        for item in items[:5]
    ]
    if should_write_back:
        SiteOper().update_rss(site.domain, rss_url)
        fixed = True
    return _result(
        site=site,
        state="success",
        message="RSS正常",
        rss_url=rss_url,
        source=source,
        fixed=fixed,
        count=len(items),
        samples=samples,
        seconds=_elapsed(start),
    )


def _check_userdata_health(site_ids: Optional[List[int]] = None) -> List[Dict[str, Any]]:
    sites = [
        site for site in SiteOper().list_order_by_pri()
        if site and site.id and (not site_ids or site.id in site_ids)
    ]
    latest_by_domain = {}
    for data in SiteOper().get_userdata_latest() or []:
        domain = getattr(data, "domain", "") or ""
        if domain:
            latest_by_domain[domain] = data

    return [_userdata_health_result(site, latest_by_domain.get(site.domain or "")) for site in sites]


def _userdata_health_result(site: Any, data: Any = None) -> Dict[str, Any]:
    issues: List[str] = []
    if not getattr(site, "is_active", True):
        issues.append("站点未启用")
    if not data:
        issues.append("没有用户数据")
        return {
            "site_id": getattr(site, "id", None),
            "site_name": getattr(site, "name", None) or "",
            "domain": getattr(site, "domain", None) or "",
            "state": "error",
            "message": "；".join(issues),
            "username": "",
            "user_level": "",
            "upload": 0,
            "download": 0,
            "ratio": 0,
            "bonus": 0,
            "seeding": 0,
            "seeding_size": 0,
            "leeching": 0,
            "updated_at": "",
            "adapted": _site_has_rule(getattr(site, "domain", "")),
        }

    err_msg = getattr(data, "err_msg", "") or ""
    username = getattr(data, "username", "") or ""
    user_level = getattr(data, "user_level", "") or ""
    upload = _number(getattr(data, "upload", 0))
    download = _number(getattr(data, "download", 0))
    ratio = _number(getattr(data, "ratio", 0))
    bonus = _number(getattr(data, "bonus", 0))
    seeding = _number(getattr(data, "seeding", 0))
    seeding_size = _number(getattr(data, "seeding_size", 0))
    leeching = _number(getattr(data, "leeching", 0))

    if err_msg:
        issues.append(err_msg)
    if not username:
        issues.append("缺用户名")
    if not user_level:
        issues.append("缺用户等级")
    if upload <= 0:
        issues.append("上传量为空")
    if download > 0 and ratio <= 0:
        issues.append("分享率为空")
    if bonus <= 0:
        issues.append("魔力/积分为空")
    hard_issues = [
        issue for issue in issues
        if not (issue == "魔力/积分为空" and download > 0)
    ]
    state = "success"
    if hard_issues:
        state = "error"
    elif issues:
        state = "warning"

    return {
        "site_id": getattr(site, "id", None),
        "site_name": getattr(site, "name", None) or getattr(data, "name", "") or "",
        "domain": getattr(site, "domain", None) or getattr(data, "domain", "") or "",
        "state": state,
        "message": "；".join(issues) if issues else "用户数据完整",
        "username": username,
        "user_level": user_level,
        "upload": upload,
        "download": download,
        "ratio": ratio,
        "bonus": bonus,
        "seeding": seeding,
        "seeding_size": seeding_size,
        "leeching": leeching,
        "updated_at": _join_datetime(getattr(data, "updated_day", ""), getattr(data, "updated_time", "")),
        "adapted": _site_has_rule(getattr(site, "domain", "")),
    }


def _result(site: Any = None, site_id: Optional[int] = None, state: str = "error", message: str = "",
            rss_url: str = "", source: str = "", count: Optional[int] = None, samples: Optional[List[dict]] = None,
            fixed: bool = False, seconds: float = 0) -> Dict[str, Any]:
    return {
        "site_id": getattr(site, "id", None) or site_id,
        "site_name": getattr(site, "name", None) or "",
        "domain": getattr(site, "domain", None) or "",
        "state": state,
        "message": message,
        "rss_url": _mask_url(rss_url),
        "source": source,
        "fixed": fixed,
        "count": count,
        "samples": samples or [],
        "seconds": seconds,
        "tested_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _iter_site_parser_classes() -> list[type]:
    try:
        from app.modules.indexer import parser as parser_pkg
        from app.modules.indexer.parser import SiteParserBase
    except Exception as err:
        logger.warning(f"站点工具箱无法加载解析器基类：{err}")
        return []

    classes: list[type] = []
    for module_info in pkgutil.iter_modules(parser_pkg.__path__):
        module_name = f"app.modules.indexer.parser.{module_info.name}"
        try:
            module = importlib.import_module(module_name)
        except Exception as err:
            logger.warning(f"站点工具箱跳过解析器模块：{module_name} - {err}")
            continue
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if obj is SiteParserBase:
                continue
            try:
                if issubclass(obj, SiteParserBase):
                    classes.append(obj)
            except TypeError:
                continue

    return list(dict.fromkeys(classes))


def _parse_site_config(conf_text: str) -> list[dict[str, Any]]:
    conf_text = (conf_text or "").strip()
    if not conf_text:
        return []

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
            logger.error(f"站点工具箱适配配置格式错误：{err}")
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
            logger.error(f"站点工具箱旧适配配置格式错误：{err}")
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
        logger.warning(f"站点工具箱用户数据 HTML 解析失败：{parser_domain} - {err}")
        return
    if html is None:
        return

    try:
        for rule in matched_rules:
            config = rule.get("config") or {}
            if not isinstance(config, dict):
                continue
            _apply_parser_attr_rules(parser, html, html_text, config)
            _apply_json_stats(parser, html, html_text, config)
            _apply_field_rules(parser, html, html_text, config)
            if config.get("calculate_ratio") and not getattr(parser, "ratio", 0) and getattr(parser, "download", 0):
                parser.ratio = round(getattr(parser, "upload", 0) / getattr(parser, "download", 0), 3)
    except Exception as err:
        logger.warning(f"站点工具箱用户数据规则执行失败：{parser_domain} - {err}")
    finally:
        del html


def _is_missing_userid_membership_error(err: TypeError) -> bool:
    return "'in <string>' requires string as left operand, not NoneType" in str(err)


def _apply_xloli_uuid_userdata(parser: Any, html_text: str = ""):
    if not _is_xloli_parser(parser):
        return
    user_uuid = _get_xloli_user_uuid(parser, html_text)
    if not user_uuid:
        return
    parser.userid = user_uuid
    user_detail_page = str(getattr(parser, "_user_detail_page", "") or "")
    if not user_detail_page or user_detail_page == "userdetails":
        parser._user_detail_page = f"userdetails.php?uuid={user_uuid}"
    parser._torrent_seeding_page = f"getusertorrentlistajax.php?useruuid={user_uuid}&type=seeding"
    parser._torrent_seeding_params = None


def _normalize_xloli_next_page(parser: Any, next_page: Any) -> Any:
    if not next_page or not isinstance(next_page, str) or not _is_xloli_parser(parser):
        return next_page
    user_uuid = _get_xloli_user_uuid(parser, next_page)
    if not user_uuid:
        return next_page

    normalized = next_page.replace("&amp;", "&")
    normalized = re.sub(r"([?&])userid=", r"\1useruuid=", normalized, count=1)
    if "useruuid=" not in normalized:
        separator = "&" if "?" in normalized else "?"
        normalized = f"{normalized}{separator}useruuid={user_uuid}"
    if "type=" not in normalized:
        separator = "&" if "?" in normalized else "?"
        normalized = f"{normalized}{separator}type=seeding"
    return normalized


def _is_xloli_parser(parser: Any) -> bool:
    parser_domain = _normalize_domain(getattr(parser, "_site_domain", "") or getattr(parser, "_base_url", ""))
    return parser_domain == "xloli.cc" or parser_domain.endswith(".xloli.cc")


def _get_xloli_user_uuid(parser: Any, html_text: str = "") -> Optional[str]:
    for text in (
        html_text,
        getattr(parser, "_torrent_seeding_page", ""),
        getattr(parser, "_user_detail_page", ""),
        getattr(parser, "_site_url", ""),
        getattr(parser, "userid", ""),
    ):
        user_uuid = _extract_xloli_user_uuid(str(text or ""))
        if user_uuid:
            return user_uuid
    return None


def _extract_xloli_user_uuid(text: str) -> Optional[str]:
    if not text:
        return None
    patterns = (
        r"userdetails\.php\?uuid=([0-9a-fA-F-]{16,})",
        r"getusertorrentlistajax\(\s*['\"]([0-9a-fA-F-]{16,})['\"]\s*,\s*['\"]seeding['\"]",
        r"[?&]useruuid=([0-9a-fA-F-]{16,})",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


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


def _apply_parser_attr_rules(parser: Any, html, html_text: str, config: dict[str, Any]):
    attrs = config.get("attrs") or config.get("parser_attrs") or {}
    if not isinstance(attrs, dict):
        return

    for attr, spec in attrs.items():
        attr_name = str(attr or "").strip()
        if not attr_name or not attr_name.startswith("_"):
            continue
        value = _extract_value(html, html_text, spec)
        if value is None or value == "":
            continue
        setattr(parser, attr_name, value)


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
    if spec.get("format"):
        try:
            value = str(spec.get("format")).format(value=value)
        except Exception:
            pass
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
        "seeding size": "seeding_size",
        "seed size": "seeding_size",
        "做种体积": "seeding_size",
        "做种大小": "seeding_size",
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


def _site_has_rule(domain: Any) -> bool:
    normalized = _normalize_domain(domain)
    return any(_domain_matches(rule.get("domain"), normalized) for rule in sitetoolbox._site_rules)


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0


def _join_datetime(day: Any, clock: Any) -> str:
    day = str(day or "").strip()
    clock = str(clock or "").strip()
    return " ".join(item for item in (day, clock) if item)


def _caller_info() -> Dict[str, Any]:
    frame = inspect.currentframe()
    while frame:
        filepath = str(frame.f_code.co_filename or "")
        normalized = filepath.replace("\\", "/")
        if normalized and not normalized.endswith("/app/log.py") and "/sitetoolbox/" not in normalized:
            return {
                "source": normalized.rsplit("/", 1)[-1],
                "file": filepath,
                "line": frame.f_lineno,
            }
        frame = frame.f_back
    return {}


def _format_log_message(message: Any, args: Tuple[Any, ...]) -> str:
    text = str(message)
    if not args:
        return text
    try:
        return text % args
    except (TypeError, ValueError):
        return f"{text} {' '.join(str(arg) for arg in args)}"


def _format_exc_info(exc_info: Any) -> str:
    if not exc_info:
        return ""
    if exc_info is True:
        exc_info = sys.exc_info()
    if isinstance(exc_info, tuple):
        try:
            return logging.Formatter().formatException(exc_info)
        except Exception:
            return ""
    return str(exc_info)


def _read_error_records(path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    records: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as file_obj:
            for line in file_obj:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                if isinstance(item, dict):
                    records.append(item)
    except Exception:
        return []
    return records


def _write_error_records(path, records: List[Dict[str, Any]]):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as file_obj:
            for record in records:
                file_obj.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception:
        return


def _filter_error_records(records: List[Dict[str, Any]], retention_days: int) -> List[Dict[str, Any]]:
    cutoff = time.time() - _normalize_retention_days(retention_days) * 86400
    return [record for record in records if _error_record_ts(record) >= cutoff]


def _error_record_ts(record: Dict[str, Any]) -> float:
    try:
        return float(record.get("ts") or 0)
    except (TypeError, ValueError):
        pass
    try:
        return time.mktime(time.strptime(str(record.get("time") or ""), "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return 0


def _toolbox_page(plugin: sitetoolbox) -> List[dict]:
    results = plugin._latest_results or []
    rules = sitetoolbox._site_rules or []
    userdata_results = plugin._latest_userdata_results or []
    missing_preview = plugin._latest_missing_preview or {}
    missing_cleanup = plugin._latest_missing_cleanup or {}
    mteam_check = plugin._latest_mteam_login_check or {}
    error_records = plugin._get_error_records()
    ok = len([item for item in results if item.get("state") == "success"])
    warning = len([item for item in results if item.get("state") == "warning"])
    error = len([item for item in results if item.get("state") == "error"])
    indexer_count = len([rule for rule in rules if isinstance(rule.get("indexer"), dict)])
    userdata_count = len([rule for rule in rules if isinstance(rule.get("userdata"), dict)])
    userdata_bad = len([item for item in userdata_results if item.get("state") == "error"])
    userdata_warning = len([item for item in userdata_results if item.get("state") == "warning"])
    missing_count = int(missing_preview.get("total_count") or 0)
    missing_size = _format_size(missing_preview.get("total_size"))
    mteam_bad = int(mteam_check.get("warning_count") or 0) + int(mteam_check.get("error_count") or 0)
    return [
        _overview_panel(
            plugin=plugin,
            missing_count=missing_count,
            missing_size=missing_size,
            rss_bad=error + warning,
            userdata_bad=userdata_bad + userdata_warning,
            mteam_bad=mteam_bad,
            error_count=len(error_records),
            rule_count=len(rules),
            indexer_count=indexer_count,
            userdata_count=userdata_count,
        ),
        _action_panel(plugin, missing_preview, results, mteam_check),
        _details_panel(
            missing_preview=missing_preview,
            missing_cleanup=missing_cleanup,
            mteam_check=mteam_check,
            plugin=plugin,
            rss_results=results,
            userdata_results=userdata_results,
            error_records=error_records,
            rules=rules,
            rss_ok=ok,
            rss_bad=error + warning,
        ),
    ]


def _overview_panel(plugin: sitetoolbox, missing_count: int, missing_size: str, rss_bad: int,
                    userdata_bad: int, mteam_bad: int, error_count: int, rule_count: int,
                    indexer_count: int, userdata_count: int) -> dict:
    return {
        "component": "VCard",
        "props": {"variant": "outlined", "class": "mb-3"},
        "content": [
            {
                "component": "VCardText",
                "content": [
                    {
                        "component": "div",
                        "props": {"class": "d-flex flex-wrap align-center justify-space-between ga-3 mb-2"},
                        "content": [
                            {
                                "component": "div",
                                "content": [
                                    {
                                        "component": "div",
                                        "props": {"class": "text-h6"},
                                        "text": "站点工具箱",
                                    },
                                    {
                                        "component": "div",
                                        "props": {"class": "text-caption text-medium-emphasis"},
                                        "text": f"版本 {plugin.plugin_version} · {'已启用' if plugin.get_state() else '已停用'}",
                                    },
                                ],
                            },
                            _status_chip("安全模式" if not plugin._cleanup_delete_files else "删除文件", "success" if not plugin._cleanup_delete_files else "warning"),
                        ],
                    },
                    {
                        "component": "VRow",
                        "props": {"dense": True},
                        "content": [
                            _metric_col("缺失种子", missing_count, missing_size),
                            _metric_col("RSS 异常", rss_bad, "最近结果"),
                            _metric_col("数据异常", userdata_bad, "用户数据"),
                            _metric_col("馒头登录", mteam_bad, f"提醒 {plugin._mteam_warning_days} 天"),
                            _metric_col("系统错误", error_count, f"保留 {plugin._error_retention_days} 天"),
                            _metric_col("适配规则", rule_count, f"索引 {indexer_count} / 数据 {userdata_count}"),
                        ],
                    },
                ],
            },
        ],
    }


def _action_panel(plugin: sitetoolbox, missing_preview: Dict[str, Any],
                  rss_results: List[Dict[str, Any]], mteam_check: Dict[str, Any]) -> dict:
    missing_time = missing_preview.get("created_at") or "-"
    rss_time = _latest_rss_time(rss_results) or "-"
    userdata_time = plugin._latest_userdata_checked_at or "-"
    mteam_time = mteam_check.get("checked_at") or "-"
    job_text = _job_state_summary(plugin._latest_job_states)
    content = [
        {
            "component": "VRow",
            "props": {"dense": True},
            "content": [
                _operation_col(
                    "缺失种子",
                    f"最近：{missing_time} · 定时：{plugin._cleanup_auto_cron if plugin._cleanup_auto_enabled else '关闭'}",
                    [
                        _action_button("预览", "mdi-eye-search", "primary", "plugin/sitetoolbox/cleanup/missing/preview"),
                        _action_button("清理", "mdi-delete-alert", "error", "plugin/sitetoolbox/cleanup/missing"),
                    ],
                ),
                _operation_col(
                    "RSS",
                    f"最近：{rss_time} · 站点：{len(plugin._site_ids)} 个",
                    [
                        _action_button("测试", "mdi-rss", "primary", "plugin/sitetoolbox/test/rss"),
                        _action_button("修复", "mdi-wrench", "warning", "plugin/sitetoolbox/repair/rss"),
                    ],
                ),
                _operation_col(
                    "用户数据",
                    f"最近：{userdata_time}",
                    [
                        _action_button("检查", "mdi-account-search", "secondary", "plugin/sitetoolbox/check/userdata"),
                    ],
                ),
                _operation_col(
                    "馒头登录",
                    f"最近：{mteam_time} · 定时：{plugin._mteam_auto_cron if plugin._mteam_auto_enabled else '关闭'}",
                    [
                        _action_button("检查", "mdi-login-variant", "secondary", "plugin/sitetoolbox/mteam/login/check"),
                    ],
                ),
            ],
        },
    ]
    if job_text:
        content.append({
            "component": "div",
            "props": {"class": "text-caption text-medium-emphasis mt-2"},
            "text": job_text,
        })
    return {
        "component": "VCard",
        "props": {"variant": "outlined", "class": "mb-3"},
        "content": [
            {
                "component": "VCardText",
                "content": content,
            },
        ],
    }


def _latest_rss_time(results: List[Dict[str, Any]]) -> str:
    values = [str(item.get("tested_at") or "") for item in results or [] if isinstance(item, dict)]
    values = [value for value in values if value]
    return max(values) if values else ""


def _job_state_summary(states: Dict[str, Dict[str, Any]]) -> str:
    if not isinstance(states, dict):
        return ""
    items = [item for item in states.values() if isinstance(item, dict)]
    running = [item for item in items if item.get("status") == "running"]
    if running:
        names = list(dict.fromkeys(str(item.get("name") or item.get("key") or "任务") for item in running))
        text = "、".join(names[:3])
        if len(names) > 3:
            text += f" 等 {len(names)} 个"
        return f"后台运行：{text}"

    finished = [item for item in items if item.get("finished_at")]
    if not finished:
        return ""
    latest = max(finished, key=lambda item: str(item.get("finished_at") or ""))
    status = {
        "success": "完成",
        "error": "失败",
        "interrupted": "已重置",
    }.get(str(latest.get("status") or ""), str(latest.get("status") or ""))
    name = latest.get("name") or latest.get("key") or "任务"
    return f"最近任务：{name} {status} · {latest.get('finished_at') or '-'}"


def _details_panel(missing_preview: Dict[str, Any], missing_cleanup: Dict[str, Any],
                   mteam_check: Dict[str, Any], plugin: sitetoolbox,
                   rss_results: List[Dict[str, Any]], userdata_results: List[Dict[str, Any]],
                   error_records: List[Dict[str, Any]], rules: List[dict], rss_ok: int, rss_bad: int) -> dict:
    return {
        "component": "VExpansionPanels",
        "props": {"variant": "accordion", "class": "mt-3"},
        "content": [
            _expansion_panel(
                "缺失文件种子",
                f"{missing_preview.get('total_count', 0)} 个 / {_format_size(missing_preview.get('total_size'))}",
                [_missing_torrent_panel(missing_preview, missing_cleanup, plugin)],
            ),
            _expansion_panel(
                "馒头登录",
                f"异常 {int(mteam_check.get('warning_count') or 0) + int(mteam_check.get('error_count') or 0)} / {mteam_check.get('total_count', 0)}",
                [_mteam_login_panel(mteam_check, plugin)],
            ),
            _expansion_panel("用户数据健康", f"{len(userdata_results)} 个站点", [_userdata_table(userdata_results)]),
            _expansion_panel("RSS 结果", f"正常 {rss_ok} / 异常 {rss_bad}", [_result_table(rss_results)]),
            _expansion_panel(
                "系统错误",
                f"{len(error_records)} 条 / {plugin._error_retention_days} 天",
                [_system_error_table(error_records)],
            ),
            _expansion_panel("适配规则", f"{len(rules)} 条", [_adapter_rule_table(rules)]),
        ],
    }


def _operation_col(title: str, subtitle: str, actions: List[dict]) -> dict:
    return {
        "component": "VCol",
        "props": {"cols": 12, "md": 4},
        "content": [
            {
                "component": "div",
                "props": {"class": "d-flex flex-column ga-2"},
                "content": [
                    {"component": "div", "props": {"class": "text-subtitle-2"}, "text": title},
                    {"component": "div", "props": {"class": "text-caption text-medium-emphasis"}, "text": subtitle},
                    {
                        "component": "div",
                        "props": {"class": "d-flex flex-wrap ga-2"},
                        "content": actions,
                    },
                ],
            }
        ],
    }


def _action_button(text: str, icon: str, color: str, api: str) -> dict:
    return {
        "component": "VBtn",
        "props": {
            "variant": "tonal",
            "color": color,
            "prepend-icon": icon,
            "size": "small",
        },
        "text": text,
        "events": {
            "click": {
                "api": api,
                "method": "post",
            }
        },
    }


def _metric_col(title: str, value: Any, subtitle: str) -> dict:
    return {
        "component": "VCol",
        "props": {"cols": 6, "md": 3},
        "content": [{
            "component": "div",
            "props": {"class": "py-2"},
            "content": [
                {"component": "div", "props": {"class": "text-caption text-medium-emphasis"}, "text": title},
                {"component": "div", "props": {"class": "text-h6"}, "text": str(value)},
                {"component": "div", "props": {"class": "text-caption text-medium-emphasis"}, "text": subtitle},
            ],
        }],
    }


def _status_chip(text: str, color: str) -> dict:
    return {
        "component": "VChip",
        "props": {"color": color, "variant": "tonal", "size": "small"},
        "text": text,
    }


def _expansion_panel(title: str, subtitle: str, content: List[dict]) -> dict:
    return {
        "component": "VExpansionPanel",
        "content": [
            {
                "component": "VExpansionPanelTitle",
                "content": [
                    {
                        "component": "div",
                        "props": {"class": "d-flex align-center justify-space-between w-100 pr-4"},
                        "content": [
                            {"component": "span", "text": title},
                            {"component": "span", "props": {"class": "text-caption text-medium-emphasis"}, "text": subtitle},
                        ],
                    },
                ],
            },
            {
                "component": "VExpansionPanelText",
                "content": content,
            },
        ],
    }


def _adapter_summary(rules: List[dict], plugin: sitetoolbox) -> dict:
    indexer_count = len([rule for rule in rules if isinstance(rule.get("indexer"), dict)])
    userdata_count = len([rule for rule in rules if isinstance(rule.get("userdata"), dict)])
    return {
        "component": "VCard",
        "props": {"variant": "outlined", "class": "mb-3"},
        "content": [
            {"component": "VCardTitle", "text": "站点适配"},
            {
                "component": "VCardText",
                "content": [
                    {
                        "component": "VRow",
                        "props": {"dense": True},
                        "content": [
                            _compact_col("索引规则", indexer_count),
                            _compact_col("用户数据规则", userdata_count),
                            _compact_col("解析补丁", "已启用" if plugin._patch_userdata else "已停用"),
                            _compact_col("挂载状态", "已挂载" if sitetoolbox._patched else "未挂载"),
                        ],
                    },
                    {
                        "component": "VAlert",
                        "props": {
                            "type": "info",
                            "variant": "tonal",
                            "text": "配置页保存后会立即加载索引规则，并把用户数据规则挂载到站点解析器。下方规则表会展示每个站点启用了哪些适配能力。",
                        },
                    },
                ],
            },
        ],
    }


def _mteam_login_panel(check: Dict[str, Any], plugin: sitetoolbox) -> dict:
    items = check.get("items") if isinstance(check.get("items"), list) else []
    rows = []
    for item in items:
        rows.append({
            "component": "tr",
            "content": [
                _td(item.get("site_name") or "-", "text-no-wrap"),
                _td(item.get("domain") or "-", "text-no-wrap"),
                _td(_state_text(item.get("state")), "text-no-wrap"),
                _td(item.get("message") or "-"),
                _td(item.get("username") or "-", "text-no-wrap"),
                _td(item.get("last_login_at") or "-", "text-no-wrap"),
                _td("-" if item.get("last_login_days") is None else item.get("last_login_days"), "text-no-wrap"),
                _td(item.get("last_browse_at") or "-", "text-no-wrap"),
                _td(item.get("record_count") or 0, "text-no-wrap"),
                _td(item.get("checked_at") or "-", "text-no-wrap"),
            ],
        })
    return {
        "component": "VCard",
        "props": {"variant": "outlined", "class": "mb-3"},
        "content": [
            {
                "component": "VCardText",
                "content": [
                    {
                        "component": "div",
                        "props": {"class": "d-flex flex-wrap align-center justify-space-between ga-3 mb-2"},
                        "content": [
                            {
                                "component": "div",
                                "content": [
                                    {"component": "div", "props": {"class": "text-subtitle-1"}, "text": "馒头登录历史"},
                                    {
                                        "component": "div",
                                        "props": {"class": "text-caption text-medium-emphasis"},
                                        "text": f"最近检查：{check.get('checked_at') or '-'} · 提醒天数：{plugin._mteam_warning_days}",
                                    },
                                ],
                            },
                            _status_chip(
                                f"异常 {int(check.get('warning_count') or 0) + int(check.get('error_count') or 0)}",
                                "success" if not (check.get("warning_count") or check.get("error_count")) else "warning",
                            ),
                        ],
                    },
                    {
                        "component": "VAlert",
                        "props": {
                            "type": "info",
                            "variant": "tonal",
                            "text": "这里通过馒头 API Access Token 查询真实登录历史；不会伪造浏览器登录，也不会展示 IP 信息。",
                        },
                    },
                    _table_or_empty(
                        rows,
                        "还没有馒头登录检查结果",
                        [
                            _th("站点"),
                            _th("域名"),
                            _th("状态"),
                            _th("说明"),
                            _th("用户"),
                            _th("最近登录"),
                            _th("天数"),
                            _th("最近访问"),
                            _th("记录数"),
                            _th("检查时间"),
                        ],
                    ),
                ],
            },
        ],
    }


def _missing_torrent_panel(preview: Dict[str, Any], cleanup: Dict[str, Any], plugin: sitetoolbox) -> dict:
    items = preview.get("items") if isinstance(preview.get("items"), list) else []
    errors = preview.get("errors") if isinstance(preview.get("errors"), list) else []
    cleanup_items = cleanup.get("items") if isinstance(cleanup.get("items"), list) else []
    rows = []
    for item in items[:200]:
        rows.append({
            "component": "tr",
            "content": [
                _td(item.get("downloader") or "-", "text-no-wrap"),
                _td(item.get("state") or "-", "text-no-wrap"),
                _td(item.get("name") or "-"),
                _td(_format_size(item.get("size")), "text-no-wrap"),
                _td(item.get("save_path") or "-", "text-no-wrap"),
                _td(item.get("content_path") or "-"),
                _td((item.get("hash") or "")[:12], "text-no-wrap"),
            ],
        })

    error_rows = [
        {
            "component": "tr",
            "content": [
                _td(item.get("downloader") or "-", "text-no-wrap"),
                _td(item.get("message") or "-"),
            ],
        }
        for item in errors
    ]
    cleanup_rows = [
        {
            "component": "tr",
            "content": [
                _td(item.get("downloader") or "-", "text-no-wrap"),
                _td(_cleanup_state_text(item.get("state")), "text-no-wrap"),
                _td(item.get("message") or "-"),
                _td(item.get("name") or "-"),
                _td((item.get("hash") or "")[:12], "text-no-wrap"),
            ],
        }
        for item in cleanup_items[:80]
    ]
    path_summary = _path_summary_text(preview.get("by_save_path") or [])

    return {
        "component": "VCard",
        "props": {"variant": "outlined", "class": "mb-3"},
        "content": [
            {
                "component": "VCardText",
                "content": [
                    {
                        "component": "div",
                        "props": {"class": "d-flex flex-wrap align-center justify-space-between ga-3 mb-2"},
                        "content": [
                            {
                                "component": "div",
                                "content": [
                                    {"component": "div", "props": {"class": "text-subtitle-1"}, "text": "缺失文件种子"},
                                    {"component": "div", "props": {"class": "text-caption text-medium-emphasis"}, "text": path_summary or "暂无路径汇总"},
                                ],
                            },
                            _status_chip(f"{preview.get('total_count', 0)} 个", "error" if preview.get("total_count") else "success"),
                        ],
                    },
                    {
                        "component": "VRow",
                        "props": {"dense": True},
                        "content": [
                            _compact_col("配置下载器", ", ".join(plugin._cleanup_downloader_names) or "未选择"),
                            _compact_col("预览数量", preview.get("total_count", 0)),
                            _compact_col("预览体积", _format_size(preview.get("total_size"))),
                            _compact_col("最近清理", f"{cleanup.get('deleted_count', 0)} 个" if cleanup else "-"),
                        ],
                    },
                    {
                        "component": "VAlert",
                        "props": {
                            "type": "warning" if plugin._cleanup_delete_files else "info",
                            "variant": "tonal",
                            "text": (
                                "当前配置会同时删除下载器中的数据文件；清理前请确认预览列表。"
                                if plugin._cleanup_delete_files
                                else "当前为安全清理：只删除下载器任务，不删除数据文件。清理时会复查任务仍为 missingFiles。"
                            ),
                        },
                    },
                    _error_table("下载器异常", error_rows),
                    _cleanup_table(cleanup_rows, cleanup),
                    _empty_alert("还没有预览结果，请先点击“预览缺失种子”。") if not rows else {
                        "component": "VTable",
                        "props": {"density": "compact"},
                        "content": [
                            {
                                "component": "thead",
                                "content": [{
                                    "component": "tr",
                                    "content": [
                                        _th("下载器"),
                                        _th("状态"),
                                        _th("名称"),
                                        _th("大小"),
                                        _th("保存路径"),
                                        _th("内容路径"),
                                        _th("Hash"),
                                    ],
                                }],
                            },
                            {"component": "tbody", "content": rows},
                        ],
                    },
                ],
            },
        ],
    }


def _missing_summary_table(title: str, summary: List[Dict[str, Any]]) -> dict:
    rows = [
        {
            "component": "tr",
            "content": [
                _td(item.get("name") or "-", "text-no-wrap"),
                _td(item.get("count"), "text-no-wrap"),
                _td(_format_size(item.get("size")), "text-no-wrap"),
            ],
        }
        for item in summary
    ]
    return {
        "component": "div",
        "props": {"class": "mt-3"},
        "content": [
            {"component": "div", "props": {"class": "text-subtitle-2 mb-1"}, "text": title},
            _empty_alert("暂无数据") if not rows else {
                "component": "VTable",
                "props": {"density": "compact"},
                "content": [
                    {
                        "component": "thead",
                        "content": [{
                            "component": "tr",
                            "content": [_th("名称"), _th("数量"), _th("体积")],
                        }],
                    },
                    {"component": "tbody", "content": rows},
                ],
            },
        ],
    }


def _path_summary_text(summary: List[Dict[str, Any]]) -> str:
    if not summary:
        return ""
    parts = []
    for item in summary[:3]:
        parts.append(f"{item.get('name') or '-'}：{item.get('count', 0)} 个 / {_format_size(item.get('size'))}")
    if len(summary) > 3:
        parts.append(f"+{len(summary) - 3}")
    return " · ".join(parts)


def _error_table(title: str, rows: List[dict]) -> dict:
    if not rows:
        return {"component": "div"}
    return {
        "component": "div",
        "props": {"class": "mt-3"},
        "content": [
            {"component": "div", "props": {"class": "text-subtitle-2 mb-1"}, "text": title},
            {
                "component": "VTable",
                "props": {"density": "compact"},
                "content": [
                    {
                        "component": "thead",
                        "content": [{
                            "component": "tr",
                            "content": [_th("下载器"), _th("说明")],
                        }],
                    },
                    {"component": "tbody", "content": rows},
                ],
            },
        ],
    }


def _cleanup_table(rows: List[dict], cleanup: Dict[str, Any]) -> dict:
    if not cleanup:
        return {"component": "div"}
    return {
        "component": "div",
        "props": {"class": "mt-3"},
        "content": [
            {
                "component": "div",
                "props": {"class": "text-subtitle-2 mb-1"},
                "text": f"最近清理结果：{cleanup.get('cleaned_at') or '-'}",
            },
            {
                "component": "VAlert",
                "props": {
                    "type": "success" if not cleanup.get("failed_count") else "warning",
                    "variant": "tonal",
                    "text": f"候选 {cleanup.get('candidate_count', 0)} 个，删除 {cleanup.get('deleted_count', 0)} 个，跳过 {cleanup.get('skipped_count', 0)} 个，失败 {cleanup.get('failed_count', 0)} 个。",
                },
            },
            {
                "component": "VTable",
                "props": {"density": "compact"},
                "content": [
                    {
                        "component": "thead",
                        "content": [{
                            "component": "tr",
                            "content": [_th("下载器"), _th("结果"), _th("说明"), _th("名称"), _th("Hash")],
                        }],
                    },
                    {"component": "tbody", "content": rows},
                ],
            } if rows else _empty_alert("没有清理明细"),
        ],
    }


def _adapter_rule_table(rules: List[dict]) -> dict:
    rows = []
    for rule in rules:
        indexer = rule.get("indexer") if isinstance(rule.get("indexer"), dict) else {}
        userdata = rule.get("userdata") if isinstance(rule.get("userdata"), dict) else {}
        fields = userdata.get("fields") if isinstance(userdata.get("fields"), dict) else {}
        json_stats = _as_list(userdata.get("json_stats") or userdata.get("stats_json") or [])
        rows.append({
            "component": "tr",
            "content": [
                _td(rule.get("domain") or "-"),
                _td("是" if indexer else "-", "text-no-wrap"),
                _td(indexer.get("schema") or "-", "text-no-wrap"),
                _td("是" if userdata else "-", "text-no-wrap"),
                _td(", ".join(fields.keys()) if fields else "-", "text-no-wrap"),
                _td(len(json_stats), "text-no-wrap"),
            ],
        })
    return _table_or_empty(
        rows,
        "未配置站点适配规则",
        [_th("域名"), _th("索引"), _th("Schema"), _th("用户数据"), _th("字段"), _th("JSON")],
    )


def _system_error_table(records: List[Dict[str, Any]]) -> dict:
    rows = []
    for item in records[:200]:
        file_line = item.get("file") or ""
        if item.get("line"):
            file_line = f"{file_line}:{item.get('line')}" if file_line else str(item.get("line"))
        rows.append({
            "component": "tr",
            "content": [
                _td(item.get("time") or "-", "text-no-wrap"),
                _td(item.get("level") or "-", "text-no-wrap"),
                _td(item.get("source") or "-", "text-no-wrap"),
                _td(_error_record_message(item)),
                _td(file_line or "-", "text-no-wrap"),
            ],
        })
    return _table_or_empty(
        rows,
        "暂无系统错误记录",
        [_th("时间"), _th("级别"), _th("来源"), _th("错误"), _th("位置")],
    )


def _error_record_message(item: Dict[str, Any]) -> str:
    message = str(item.get("message") or "")
    traceback_text = str(item.get("traceback") or "")
    if traceback_text:
        lines = [line.strip() for line in traceback_text.splitlines() if line.strip()]
        if lines:
            message = f"{message}\n{lines[-1]}"
    return _clip_text(message, 500)


def _userdata_table(results: List[Dict[str, Any]]) -> dict:
    rows = []
    for item in results:
        rows.append({
            "component": "tr",
            "content": [
                _td(item.get("site_name") or "-"),
                _td(item.get("domain") or "-"),
                _td(_state_text(item.get("state")), "text-no-wrap"),
                _td("是" if item.get("adapted") else "-", "text-no-wrap"),
                _td(item.get("message") or "-"),
                _td(item.get("username") or "-", "text-no-wrap"),
                _td(item.get("user_level") or "-", "text-no-wrap"),
                _td(_format_size(item.get("upload")), "text-no-wrap"),
                _td(_format_size(item.get("download")), "text-no-wrap"),
                _td(item.get("ratio"), "text-no-wrap"),
                _td(item.get("bonus"), "text-no-wrap"),
                _td(_format_count(item.get("seeding")), "text-no-wrap"),
                _td(_format_size(item.get("seeding_size")), "text-no-wrap"),
                _td(item.get("updated_at") or "-", "text-no-wrap"),
            ],
        })
    return _table_or_empty(
        rows,
        "还没有用户数据检查结果",
        [
            _th("站点"),
            _th("域名"),
            _th("状态"),
            _th("适配"),
            _th("说明"),
            _th("用户"),
            _th("等级"),
            _th("上传"),
            _th("下载"),
            _th("分享率"),
            _th("魔力"),
            _th("做种数"),
            _th("做种体积"),
            _th("更新时间"),
        ],
    )


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
                _td("是" if item.get("fixed") else "-", "text-no-wrap"),
                _td(item.get("seconds"), "text-no-wrap"),
                _td(item.get("tested_at") or "-", "text-no-wrap"),
            ],
        })
    return _table_or_empty(
        rows,
        "还没有 RSS 测试结果",
        [_th("站点"), _th("域名"), _th("状态"), _th("说明"), _th("条目"), _th("来源"), _th("写回"), _th("耗时"), _th("时间")],
    )


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


def _compact_col(title: str, value: Any) -> dict:
    return {
        "component": "VCol",
        "props": {"cols": 12, "sm": 6, "md": 3},
        "content": [{
            "component": "div",
            "props": {"class": "d-flex flex-column"},
            "content": [
                {"component": "span", "props": {"class": "text-caption text-medium-emphasis"}, "text": title},
                {"component": "span", "props": {"class": "text-h6"}, "text": str(value)},
            ],
        }],
    }


def _table_or_empty(rows: List[dict], empty_text: str, headers: List[dict]) -> dict:
    if not rows:
        return _empty_alert(empty_text)
    return {
        "component": "VTable",
        "props": {"density": "compact"},
        "content": [
            {
                "component": "thead",
                "content": [{"component": "tr", "content": headers}],
            },
            {"component": "tbody", "content": rows},
        ],
    }


def _form_section(title: str, content: List[dict]) -> dict:
    return {
        "component": "div",
        "props": {"class": "mb-4"},
        "content": [
            {"component": "div", "props": {"class": "text-subtitle-1 mb-2"}, "text": title},
            *content,
            {"component": "VDivider", "props": {"class": "mt-2"}},
        ],
    }


def _error_retention_options() -> List[dict]:
    return [
        {"title": "1 天", "value": 1},
        {"title": "3 天", "value": 3},
        {"title": "7 天", "value": 7},
    ]


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
        "warning": "警告",
        "error": "异常",
    }.get(state or "", state or "-")


def _cleanup_state_text(state: Optional[str]) -> str:
    return {
        "deleted": "已删除",
        "skipped": "已跳过",
        "failed": "失败",
    }.get(state or "", state or "-")


def _empty_alert(text: str) -> dict:
    return {
        "component": "VAlert",
        "props": {
            "type": "info",
            "variant": "tonal",
            "text": text,
        },
    }


def _format_size(value: Any) -> str:
    number = _number(value)
    if number <= 0:
        return "0"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    index = 0
    while number >= 1024 and index < len(units) - 1:
        number /= 1024
        index += 1
    if index == 0:
        return f"{int(number)} {units[index]}"
    return f"{number:.2f} {units[index]}"


def _format_count(value: Any) -> str:
    number = _number(value)
    if number.is_integer():
        return str(int(number))
    return str(number)


def _format_timestamp(value: Any) -> str:
    try:
        timestamp = int(value or 0)
    except (TypeError, ValueError):
        return ""
    if timestamp <= 0:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))
    except Exception:
        return ""


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _downloader_options() -> List[dict]:
    downloaders = SystemConfigOper().get(SystemConfigKey.Downloaders) or []
    items = []
    for downloader in downloaders:
        if not isinstance(downloader, dict) or not downloader.get("enabled") or not downloader.get("name"):
            continue
        items.append({
            "title": f"{downloader.get('name')} ({downloader.get('type') or 'unknown'})",
            "value": downloader.get("name"),
        })
    return items


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


def _normalize_rss_url(site_url: str, rss_url: Optional[str]) -> str:
    rss_url = (rss_url or "").strip()
    if not rss_url or rss_url == "#" or rss_url.lower().startswith("javascript:"):
        return ""
    return urljoin(site_url, rss_url)


def _discover_rss_url(site: Any) -> Tuple[str, str]:
    rss_url, message = RssHelper().get_rss_link(
        url=site.url,
        cookie=site.cookie or "",
        ua=site.ua or "",
        proxy=bool(site.proxy),
        timeout=sitetoolbox._timeout,
    )
    if not rss_url:
        return "", message
    rss_url = urljoin(site.url, rss_url)
    if _is_site_home_url(site.url, rss_url):
        return "", "获取RSS链接失败：生成地址不是RSS链接"
    return rss_url, message


def _is_site_home_url(site_url: str, rss_url: str) -> bool:
    site = urlparse(site_url)
    rss = urlparse(rss_url)
    return (
        site.netloc == rss.netloc
        and (rss.path in {"", "/"})
        and not rss.query
    )


def _parse_rss(site: Any, rss_url: str):
    headers = {"Cookie": site.cookie} if site.cookie else None
    return RssHelper().parse(
        url=rss_url,
        proxy=bool(site.proxy),
        timeout=sitetoolbox._timeout,
        headers=headers,
        ua=site.ua or None,
    )


def _elapsed(start: float) -> float:
    return round(time.monotonic() - start, 2)


def _normalize_job_states(value: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    states: Dict[str, Dict[str, Any]] = {}
    for key, state in value.items():
        if not isinstance(state, dict):
            continue
        item = dict(state)
        item.setdefault("key", str(key))
        if item.get("status") == "running":
            item["status"] = "interrupted"
            item["message"] = "插件重新加载，任务状态已重置"
            item["finished_at"] = _now()
        states[str(key)] = item
    return states


def _normalize_retention_days(value: Any) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError):
        days = 3
    return days if days in {1, 3, 7} else 3


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


def _str_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        text = str(item or "").strip()
        if text:
            result.append(text)
    return list(dict.fromkeys(result))


def _clip_text(value: Any, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."
