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
from datetime import datetime
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

from .monitor import MODULES, classify as classify_monitor, is_due, next_run, normalize_plan, observe


class sitetoolbox(_PluginBase):
    plugin_name = "站点工具箱"
    plugin_desc = "站点诊断与维护工作台，统一监控计划及连续异常确认，支持 RSS、用户数据、缺失种子和馒头登录检查。"
    plugin_icon = "mdi-toolbox"
    plugin_version = "1.4.1"
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
    _cleanup_auto_cleanup = False
    _cleanup_auto_cron = "0 */6 * * *"
    _latest_missing_preview: Dict[str, Any] = {}
    _latest_missing_cleanup: Dict[str, Any] = {}
    _error_retention_days = 3
    _mteam_site_ids: List[int] = []
    _mteam_warning_days = 25
    _userdata_stale_days = 2
    _mteam_auto_enabled = False
    _mteam_auto_cron = "0 9 * * *"
    _rss_auto_enabled = True
    _rss_auto_cron = "0 8 * * *"
    _rss_failure_days = 3
    _latest_mteam_login_check: Dict[str, Any] = {}

    _patched = False
    _originals: dict[tuple[type, str], Any] = {}
    _site_rules: list[dict[str, Any]] = []
    _userdata_rules: list[dict[str, Any]] = []
    _asyncfix_raw_cookie_domains: list[str] = []
    _aru_patched = False
    _aru_originals: dict[tuple[type, str], Any] = {}
    _jobs_lock = Lock()
    _rss_history_lock = Lock()
    _monitor_run_lock = Lock()
    _monitor_plan = normalize_plan({})
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
        self._latest_userdata_results = [{**item, "tested_at": item.get("tested_at") or self._latest_userdata_checked_at}
                                         for item in self._latest_userdata_results]
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
        self._cleanup_auto_cleanup = _to_bool(config.get("cleanup_auto_cleanup", False), False)
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
        self._userdata_stale_days = _int_or_default(config.get("userdata_stale_days"), 2, minimum=1, maximum=365)
        self._mteam_auto_enabled = _to_bool(config.get("mteam_auto_enabled", False), False)
        self._mteam_auto_cron = str(config.get("mteam_auto_cron") or "0 9 * * *").strip()
        self._rss_auto_enabled = _to_bool(config.get("rss_auto_enabled", True), True)
        self._rss_auto_cron = str(config.get("rss_auto_cron") or "0 8 * * *").strip()
        self._monitor_plan = normalize_plan(config)
        self._rss_failure_days = self._monitor_plan["monitor_failure_days"]
        self.__class__._monitor_plan = self._monitor_plan
        self._latest_results = [
            _rss_result_status(item, self._rss_failure_days) for item in self._latest_results
        ]
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
        self.__class__._cleanup_auto_cleanup = self._cleanup_auto_cleanup
        self.__class__._cleanup_auto_cron = self._cleanup_auto_cron
        self.__class__._latest_missing_preview = self._latest_missing_preview
        self.__class__._latest_missing_cleanup = self._latest_missing_cleanup
        self.__class__._error_retention_days = self._error_retention_days
        self.__class__._mteam_site_ids = self._mteam_site_ids
        self.__class__._mteam_warning_days = self._mteam_warning_days
        self.__class__._userdata_stale_days = self._userdata_stale_days
        self.__class__._mteam_auto_enabled = self._mteam_auto_enabled
        self.__class__._mteam_auto_cron = self._mteam_auto_cron
        self.__class__._rss_auto_enabled = self._rss_auto_enabled
        self.__class__._rss_auto_cron = self._rss_auto_cron
        self.__class__._rss_failure_days = self._rss_failure_days
        self.__class__._latest_mteam_login_check = self._latest_mteam_login_check
        self.__class__._site_rules = _parse_site_config(self._site_conf)
        self.__class__._userdata_rules = [
            {"domain": rule.get("domain"), "config": rule.get("userdata")}
            for rule in self.__class__._site_rules
            if isinstance(rule.get("userdata"), dict)
        ]
        self.__class__._asyncfix_raw_cookie_domains = [
            rule.get("domain")
            for rule in self.__class__._site_rules
            if isinstance(rule.get("asyncfix"), dict) and rule["asyncfix"].get("raw_cookie")
        ]

        if self._enabled:
            self._install_error_capture()
            self._get_error_records()
            self._apply_indexers()
            if self._patch_userdata and self.__class__._userdata_rules:
                self._patch_userdata_parsers()
            else:
                self._unpatch_userdata()
            if self.__class__._asyncfix_raw_cookie_domains:
                self._patch_async_raw_cookie()
            else:
                self._unpatch_async_raw_cookie()
        else:
            self._unpatch_userdata()
            self._unpatch_async_raw_cookie()
            self._uninstall_error_capture()

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[dict]:
        return []

    def get_service(self) -> List[Dict[str, Any]]:
        if not self._enabled or not self._monitor_plan["monitor_enabled"] or not self._monitor_plan["monitor_modules"]:
            return []
        # 统一入口按分钟检查计划，执行时段、跨午夜和定点时间都由同一份配置判定。
        return [{
            "id": "sitetoolbox_monitor",
            "name": "站点工具箱统一监控",
            "trigger": CronTrigger.from_crontab("* * * * *"),
            "func": self.run_scheduled_monitor,
            "kwargs": {},
        }]

    def run_scheduled_monitor(self):
        now = datetime.now()
        if not is_due(self._monitor_plan, now):
            return
        slot = now.strftime("%Y-%m-%d %H:%M")
        if self.get_data("monitor_last_slot") == slot:
            return
        self.run_monitor(notify=True, slot=slot)

    def get_api(self) -> List[dict]:
        return [
            {"path": "/overview", "endpoint": _api_overview, "methods": ["GET"], "auth": "bear", "summary": "工具箱工作台"},
            {"path": "/options", "endpoint": _api_options, "methods": ["GET"], "auth": "bear", "summary": "配置范围选项"},
            {"path": "/monitor/run", "endpoint": _api_monitor_run, "methods": ["POST"], "auth": "bear", "summary": "执行统一监控"},
            {"path": "/errors", "endpoint": _api_errors, "methods": ["GET"], "auth": "bear", "summary": "最近系统错误聚合"},
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
                "path": "/probe/indexer/{site_id}",
                "endpoint": _api_probe_indexer,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "诊断站点索引器",
                "description": "分步报告索引器定义、同步/异步抓取和蜘蛛解析结果，定位搜索/刷流拿不到种子的问题。",
            },
            {
                "path": "/probe/page/{site_id}",
                "endpoint": _api_probe_page,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "诊断站点页面",
                "description": "在服务器上用站点 Cookie/UA/代理抓取站点域名下的页面并返回 HTML，用于排查用户数据解析规则。",
            },
            {
                "path": "/probe/logfile",
                "endpoint": _api_probe_logfile,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "按时间窗/关键字读取日志",
                "description": "读取任意日志文件的历史段落（系统日志接口只能看尾部），支持时间窗与正则过滤。",
            },
            {
                "path": "/probe/alembic",
                "endpoint": _api_probe_alembic,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "诊断数据库迁移状态",
                "description": "报告 alembic_version 表与迁移文件链 head，定位启动时数据库更新失败。",
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

    @staticmethod
    def get_render_mode() -> Tuple[str, Optional[str]]:
        return "vue", "dist/assets"

    def get_form(self) -> Tuple[Optional[List[dict]], dict]:
        return None, self._settings_snapshot()

    def get_page(self) -> Optional[List[dict]]:
        return None

    def _settings_snapshot(self) -> dict:
        return {key: value for key, value in self._config_payload().items()
                if not key.startswith("latest_") and key not in {
                    "cleanup_auto_enabled", "cleanup_auto_cron", "mteam_auto_enabled", "mteam_auto_cron",
                    "rss_auto_enabled", "rss_auto_cron", "rss_failure_days"}}

    def stop_service(self):
        self._unpatch_userdata()
        self._unpatch_async_raw_cookie()
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
            "cleanup_auto_cleanup": self._cleanup_auto_cleanup,
            "cleanup_auto_cron": self._cleanup_auto_cron,
            "latest_missing_preview": self._latest_missing_preview,
            "latest_missing_cleanup": self._latest_missing_cleanup,
            "error_retention_days": self._error_retention_days,
            "mteam_site_ids": self._mteam_site_ids,
            "mteam_warning_days": self._mteam_warning_days,
            "userdata_stale_days": self._userdata_stale_days,
            "mteam_auto_enabled": self._mteam_auto_enabled,
            "mteam_auto_cron": self._mteam_auto_cron,
            "rss_auto_enabled": self._rss_auto_enabled,
            "rss_auto_cron": self._rss_auto_cron,
            "rss_failure_days": self._rss_failure_days,
            "latest_mteam_login_check": self._latest_mteam_login_check,
        }
        payload.update(self._monitor_plan)
        payload.update(overrides)
        return payload

    def _record_monitor_results(self, module: str, results: List[dict]) -> List[dict]:
        with self._rss_history_lock:
            history = self.get_data("monitor_failure_history") or {}
            history = history if isinstance(history, dict) else {}
            legacy = (self.get_data("rss_failure_history") or {}) if module == "rss" else {}
            recorded = []
            for item in results:
                item = dict(item)
                object_id = str(item.get("object_id", item.get("site_id", "module")))
                key = f"{module}:{object_id}"
                if key not in history and module == "rss":
                    previous = legacy.get(object_id) if isinstance(legacy, dict) else None
                    if not previous:
                        last = next((row for row in self._latest_results if str(row.get("site_id")) == object_id), {})
                        previous = {"date": str(last.get("tested_at") or "")[:10],
                                    "days": last.get("failure_days", 1)
                                    if last.get("raw_state", last.get("rss_state", last.get("state"))) in {"error", "warning", "offline"} else 0}
                    history[key] = previous
                item.setdefault("tested_at", item.get("checked_at") or _now())
                result, history = observe(history, key, item, self._rss_failure_days)
                if module == "rss":
                    result.update(rss_state=result["raw_state"], rss_message=result["raw_message"])
                recorded.append(result)
            self.save_data("monitor_failure_history", history)
        return recorded

    def _record_rss_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        return self._record_monitor_results("rss", [result])[0]

    def run_monitor(self, site_ids: Optional[List[int]] = None, modules: Optional[List[str]] = None,
                    notify: bool = False, slot: str = "", _lock_held: bool = False) -> str:
        if not _lock_held and not self._monitor_run_lock.acquire(blocking=False):
            return "已有检测正在运行，请等待当前检测完成"
        started = _now()
        run = {"id": str(time.time_ns()), "started_at": started, "status": "running", "steps": []}
        try:
            if slot:
                self.save_data("monitor_last_slot", slot)
            selected = modules if modules is not None else self._monitor_plan["monitor_modules"]
            selected = [key for key in MODULES if key in selected]
            ids = site_ids if site_ids is not None else self._site_ids
            ids = ids or [site.id for site in SiteOper().list_order_by_pri() if site and site.id and getattr(site, "is_active", True)]
            confirmed = 0
            for index, module in enumerate(selected):
                self._set_job_state("monitor", "统一检测", "running", f"{index + 1}/{len(selected)} · {MODULES[module]}",
                                    started_at=started, finished_at="")
                if module != "missing" and not ids:
                    run["steps"].append({"module": module, "state": "skipped", "message": "没有启用的目标站点"})
                    continue
                try:
                    if module == "rss":
                        results = [_test_site_rss(site_id) for site_id in ids]
                        kept = [row for row in self._latest_results if row.get("site_id") not in ids]
                        self._save_results([*results, *kept])
                    elif module == "userdata":
                        results = _check_userdata_health(ids)
                        self._save_userdata_results(results)
                        results = [row for row in self._latest_userdata_results if row.get("site_id") in ids]
                    elif module == "mteam":
                        mteam_ids = [site_id for site_id in ids if not self._mteam_site_ids or site_id in self._mteam_site_ids]
                        if not mteam_ids:
                            run["steps"].append({"module": module, "state": "skipped", "message": "没有匹配的馒头站点"})
                            continue
                        result = _build_mteam_login_check(mteam_ids, self._mteam_warning_days)
                        self._save_mteam_login_check(result, site_ids=mteam_ids)
                        results = [row for row in result["items"] if row.get("site_id") in mteam_ids]
                    else:
                        if not self._cleanup_downloader_names:
                            run["steps"].append({"module": module, "state": "skipped", "message": "未选择下载器"})
                            continue
                        preview = _build_missing_torrent_preview(self._cleanup_downloader_names)
                        self._save_missing_preview(preview)
                        results = [item["monitor"] for item in preview["items"]] + preview.get("downloader_status", [])
                        cleanup_items = self._confirmed_missing_items(preview)
                        if notify and self._cleanup_auto_cleanup and cleanup_items:
                            self._save_missing_cleanup(_cleanup_missing_torrents_from_preview(cleanup_items, self._cleanup_delete_files))
                            self._save_missing_preview(_build_missing_torrent_preview(self._cleanup_downloader_names))
                    self._record_monitor_results(module, [{"object_id": "module", "state": "success", "message": "模块执行完成"}])
                    bad = sum(bool(row.get("confirmed")) for row in results)
                    pending = sum(row.get("state") == "pending" for row in results)
                    confirmed += bad
                    run["steps"].append({"module": module, "state": "success", "total": len(results),
                                         "confirmed": bad, "pending": pending, "message": f"检测 {len(results)} 项，待观察 {pending}，确认异常 {bad}"})
                except Exception as err:
                    result = self._record_monitor_results(module, [{"object_id": "module", "state": "error", "message": str(err)}])[0]
                    confirmed += int(result["confirmed"])
                    run["steps"].append({"module": module, "state": "error", "message": result["message"]})
                    logger.error(f"站点工具箱 {MODULES[module]} 执行失败：{err}")
            run.update(finished_at=_now(), status="error" if any(step["state"] == "error" for step in run["steps"]) else "success",
                       confirmed=confirmed)
            failures = sum(step["state"] == "error" for step in run["steps"])
            message = f"完成 {len(run['steps'])} 项检测，确认异常 {confirmed} 项" + (f"，{failures} 项执行失败" if failures else "")
            self._set_job_state("monitor", "统一检测", run["status"], message, started_at=started, finished_at=run["finished_at"])
            runs = self.get_data("monitor_runs") or []
            runs = runs if isinstance(runs, list) else []
            self.save_data("monitor_runs", [run, *runs][:50])
            if notify and confirmed:
                self.systemmessage.put(f"站点工具箱发现 {confirmed} 项连续异常达到 {self._rss_failure_days} 天，请查看诊断详情。", title=self.plugin_name)
            return message
        finally:
            self._monitor_run_lock.release()

    def _confirmed_missing_items(self, preview: dict) -> List[dict]:
        return [item for item in preview.get("items", [])
                if classify_monitor(item.get("monitor") or {"state": "error"}, self._rss_failure_days)["confirmed"]]

    def _save_results(self, results: List[Dict[str, Any]]):
        ids = {item.get("site_id") for item in results}
        results = [*results, *[item for item in self._latest_results if item.get("site_id") not in ids]]
        self._latest_results = results
        self.__class__._latest_results = results
        self.update_config(self._config_payload(latest_results=results))

    def _save_userdata_results(self, results: List[Dict[str, Any]]):
        results = self._record_monitor_results("userdata", results)
        observed = list(results)
        ids = {row.get("site_id") for row in results}
        results += [row for row in self._latest_userdata_results if row.get("site_id") not in ids]
        self._latest_userdata_results = results
        self.__class__._latest_userdata_results = results
        self._latest_userdata_checked_at = _now()
        self.__class__._latest_userdata_checked_at = self._latest_userdata_checked_at
        self.update_config(self._config_payload(
            latest_userdata_results=results,
            latest_userdata_checked_at=self._latest_userdata_checked_at,
        ))
        return observed

    def _save_missing_preview(self, preview: Dict[str, Any]):
        seen = self.get_data("missing_seen") or {}
        seen = seen if isinstance(seen, dict) else {}
        failed = {row["downloader"] for row in preview.get("errors", [])}
        observations = []
        for name in preview.get("downloaders", []):
            if name in failed:
                message = next(row["message"] for row in preview["errors"] if row["downloader"] == name)
                observations.append({"object_id": f"downloader:{name}", "state": "error", "message": message})
                continue
            observations.append({"object_id": f"downloader:{name}", "state": "success", "message": "下载器连接正常"})
            hashes = {item["hash"] for item in preview.get("items", []) if item["downloader"] == name}
            for torrent_hash in set(seen.get(name, [])) - hashes:
                observations.append({"object_id": f"torrent:{name}:{torrent_hash}", "state": "success", "message": "文件缺失已恢复"})
            seen[name] = sorted(hashes)
        for item in preview.get("items", []):
            observations.append({"object_id": f"torrent:{item['downloader']}:{item['hash']}", "state": "error", "message": "下载器报告文件缺失"})
        results = {row["object_id"]: row for row in self._record_monitor_results("missing", observations)}
        for item in preview.get("items", []):
            item["monitor"] = results[f"torrent:{item['downloader']}:{item['hash']}"]
        preview["downloader_status"] = [results[f"downloader:{name}"] for name in preview.get("downloaders", [])]
        self.save_data("missing_seen", seen)
        self._latest_missing_preview = preview
        self.__class__._latest_missing_preview = preview
        self.update_config(self._config_payload(latest_missing_preview=preview))

    def _save_missing_cleanup(self, cleanup: Dict[str, Any]):
        self._latest_missing_cleanup = cleanup
        self.__class__._latest_missing_cleanup = cleanup
        self.update_config(self._config_payload(latest_missing_cleanup=cleanup))

    def _save_mteam_login_check(self, result: Dict[str, Any], site_ids: Optional[List[int]] = None):
        items = self._record_monitor_results("mteam", result.get("items", []))
        observed = list(items)
        ids = set(site_ids) if site_ids is not None else {row.get("site_id") for row in items}
        kept = [row for row in self._latest_mteam_login_check.get("items", []) if row.get("site_id") not in ids]
        items += kept
        result.update(items=items, total_count=len(items), warning_count=sum(row["state"] == "warning" for row in items),
                      error_count=sum(row["state"] == "error" for row in items), pending_count=sum(row["state"] == "pending" for row in items))
        self._latest_mteam_login_check = result
        self.__class__._latest_mteam_login_check = result
        self.update_config(self._config_payload(latest_mteam_login_check=result))
        return observed

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
                # 统一监控会自行记录各阶段失败，不能被外层后台任务覆盖成成功。
                state = (self._latest_job_states or {}).get(key) or {}
                status = state.get("status") if state.get("status") in {"error", "success"} else "success"
                self._set_job_state(key, name, status, message, finished_at=_now())
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
            total_count = int(preview.get("total_count") or 0)
            message = f"{total_count} 个，{_format_size(preview.get('total_size', 0))}"
            cleanup_items = self._confirmed_missing_items(preview)
            if self._cleanup_auto_cleanup and cleanup_items:
                cleanup = _cleanup_missing_torrents_from_preview(
                    cleanup_items,
                    delete_files=self._cleanup_delete_files,
                )
                self._save_missing_cleanup(cleanup)
                refreshed = _build_missing_torrent_preview(self._cleanup_downloader_names)
                self._save_missing_preview(refreshed)
                message = (
                    f"扫描 {total_count} 个，自动清理删除 {cleanup.get('deleted_count', 0)} 个，"
                    f"跳过 {cleanup.get('skipped_count', 0)} 个，失败 {cleanup.get('failed_count', 0)} 个，"
                    f"剩余 {refreshed.get('total_count', 0)} 个"
                )
            self._set_job_state("missing_auto", name, "success", message, finished_at=_now())
            logger.info(
                f"站点工具箱缺失种子定时扫描完成：{message}，"
                f"auto_cleanup={self._cleanup_auto_cleanup}，delete_files={self._cleanup_delete_files}"
            )
        except Exception as err:
            self._set_job_state("missing_auto", name, "error", str(err), finished_at=_now())
            raise

    def auto_test_rss(self):
        name = "RSS定时测试"
        self._set_job_state("rss_test_auto", name, "running", "后台运行中", started_at=_now(), finished_at="")
        try:
            results = [_test_site_rss(site_id) for site_id in self._site_ids]
            self._save_results(results)
            ok_count = len([item for item in results if item.get("state") == "success"])
            offline_count = len([item for item in results if item.get("state") == "offline"])
            bad_count = len([item for item in results if item.get("state") in ("error", "warning")])
            pending_count = len([item for item in results if item.get("state") == "pending"])
            message = f"成功 {ok_count}/{len(results)}，待观察 {pending_count}，异常 {bad_count}，站点连接失败 {offline_count}"
            self._set_job_state("rss_test_auto", name, "success", message, finished_at=_now())
            if bad_count:
                self.systemmessage.put(f"RSS定时测试发现 {bad_count}/{len(results)} 个站点连续失败达到 "
                                       f"{self._rss_failure_days} 天，请在站点工具箱查看。",
                                       title=self.plugin_name)
            logger.info(f"站点工具箱 RSS 定时测试完成：{message}")
        except Exception as err:
            self._set_job_state("rss_test_auto", name, "error", str(err), finished_at=_now())
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
                        _apply_seeding_summary_fallback(self, html_text)
                        _preserve_seeding_on_fetch_failure(self, html_text)
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

    @classmethod
    def _patch_async_raw_cookie(cls):
        """
        对配置了 asyncfix.raw_cookie 的域名，异步请求改用原始 Cookie 头。

        根因：AsyncRequestUtils 会用 cookie_parse 把 cookie 字符串 URL 解码成字典，
        再交给 httpx 的 cookie jar 重新序列化。对 cookie 值含 %3D 等编码的站点
        （如 hdvideo.top），重组后的 Cookie 头无法通过站点鉴权（返回伪装 404），
        而同步 requests 及原始 Cookie 头都正常。此补丁保留原始 cookie 字符串，
        请求命中配置域名时直接放入 Cookie 头并跳过 cookie jar。
        """
        if cls._aru_patched:
            logger.info(
                f"站点工具箱异步Cookie修正域名已更新：{cls._asyncfix_raw_cookie_domains}")
            return
        from app.utils.http import AsyncRequestUtils

        orig_init = AsyncRequestUtils.__init__
        orig_request = AsyncRequestUtils.request
        cls._aru_originals[(AsyncRequestUtils, "__init__")] = orig_init
        cls._aru_originals[(AsyncRequestUtils, "request")] = orig_request

        def patched_init(self, *args, **kwargs):
            raw_cookie = kwargs.get("cookies")
            if raw_cookie is None and len(args) >= 3:
                raw_cookie = args[2]
            orig_init(self, *args, **kwargs)
            if isinstance(raw_cookie, str) and raw_cookie.strip():
                self._sitetoolbox_raw_cookie = raw_cookie.strip()

        async def patched_request(self, method, url, *args, **kwargs):
            raw = getattr(self, "_sitetoolbox_raw_cookie", None)
            if raw and _asyncfix_domain_match(url, sitetoolbox._asyncfix_raw_cookie_domains):
                # 直连通道：ARU 原生请求特征会被该类站点 WAF 识别为伪装 404，
                # 这里用裸 httpx + 原始 Cookie 头重发（实测全变体 200），返回同类型 Response
                import httpx
                headers = {k: v for k, v in (self._headers or {}).items() if v is not None}
                headers["Cookie"] = raw
                try:
                    async with httpx.AsyncClient(
                            proxy=getattr(self, "_proxies", None),
                            timeout=getattr(self, "_timeout", 20),
                            verify=False,
                            follow_redirects=True) as client:
                        res = await client.request(
                            method, url, headers=headers,
                            params=kwargs.get("params"),
                            data=kwargs.get("data"),
                            json=kwargs.get("json"))
                    sitetoolbox._aru_inject_count = getattr(sitetoolbox, "_aru_inject_count", 0) + 1
                    logger.debug(f"站点工具箱异步直连通道：{url} -> {res.status_code}")
                    return res
                except Exception as err:
                    logger.warning(f"站点工具箱异步直连通道失败，回退原生请求：{err}")
            return await orig_request(self, method, url, *args, **kwargs)

        AsyncRequestUtils.__init__ = patched_init
        AsyncRequestUtils.request = patched_request
        cls._aru_patched = True
        logger.info(
            f"站点工具箱异步Cookie修正已启用，域名：{cls._asyncfix_raw_cookie_domains}")

    @classmethod
    def _unpatch_async_raw_cookie(cls):
        if not cls._aru_patched:
            return
        for (target_cls, method), original in list(cls._aru_originals.items()):
            setattr(target_cls, method, original)
        cls._aru_originals = {}
        cls._aru_patched = False
        logger.info("站点工具箱异步Cookie修正已停用")


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


def _api_options() -> schemas.Response:
    sites = [{"value": site.id, "title": f"{site.name} ({site.domain})", "mteam": _is_mteam_site(site)}
             for site in SiteOper().list_order_by_pri() if site and site.id]
    return schemas.Response(success=True, data={"sites": sites, "downloaders": _downloader_options(), "modules": MODULES})


def _selected_site_ids(plugin, payload: Optional[dict] = None) -> List[int]:
    return (_int_list((payload or {}).get("site_ids")) or plugin._site_ids
            or [site.id for site in SiteOper().list_order_by_pri() if site and site.id and getattr(site, "is_active", True)])


def _api_monitor_run(payload: Optional[dict] = Body(default=None)) -> schemas.Response:
    plugin = sitetoolbox._instance
    if not plugin:
        return schemas.Response(success=False, message="插件未初始化")
    payload = payload or {}
    ids = _int_list(payload.get("site_ids")) if "site_ids" in payload else None
    modules = payload.get("modules")
    if modules is not None and (not isinstance(modules, list) or any(key not in MODULES for key in modules)):
        return schemas.Response(success=False, message="检测模块无效")
    if not (modules if modules is not None else plugin._monitor_plan["monitor_modules"]):
        return schemas.Response(success=False, message="请在设置中至少选择一个检测模块")
    if ids is not None and not ids:
        return schemas.Response(success=False, message="请选择需要复测的站点")
    # 在后台线程启动前预留执行锁，避免与同一分钟的定时任务同时启动并覆盖进度。
    if not plugin._monitor_run_lock.acquire(blocking=False):
        return schemas.Response(success=False, message="已有检测正在运行，请等待当前检测完成")
    def worker():
        try:
            plugin.run_monitor(ids, modules, _lock_held=True)
        except Exception as err:
            logger.error(f"站点工具箱统一检测执行失败：{err}")
            plugin._set_job_state("monitor", "统一检测", "error", str(err), finished_at=_now())
    try:
        plugin._set_job_state("monitor", "统一检测", "running", "正在准备检测", started_at=_now(), finished_at="")
        Thread(target=worker, name="sitetoolbox-monitor", daemon=True).start()
    except Exception:
        plugin._monitor_run_lock.release()
        raise
    return schemas.Response(success=True, message="统一检测已开始，结果会自动更新", data=plugin._latest_job_states.get("monitor"))


def _view_monitor_result(item: Optional[dict], threshold: int, tested_at: str = "") -> dict:
    if not item:
        return {"state": "unknown", "raw_state": "unknown", "message": "尚未检测", "failure_days": 0, "confirmed": False}
    result = classify_monitor(item, threshold)
    result.setdefault("tested_at", item.get("checked_at") or tested_at)
    return result


def _api_overview() -> schemas.Response:
    plugin = sitetoolbox._instance
    if not plugin:
        return schemas.Response(success=False, message="插件未初始化")
    threshold = plugin._rss_failure_days
    rss = {row.get("site_id"): row for row in plugin._latest_results}
    userdata = {row.get("site_id"): row for row in plugin._latest_userdata_results}
    mteam = {row.get("site_id"): row for row in plugin._latest_mteam_login_check.get("items", [])}
    rows = []
    for site in SiteOper().list_order_by_pri():
        if not site or not site.id:
            continue
        checks = {"rss": _view_monitor_result(rss.get(site.id), threshold),
                  "userdata": _view_monitor_result(userdata.get(site.id), threshold, plugin._latest_userdata_checked_at)}
        if _is_mteam_site(site):
            checks["mteam"] = _view_monitor_result(mteam.get(site.id), threshold)
        active_checks = [item for module, item in checks.items() if module in plugin._monitor_plan["monitor_modules"]]
        states = {item["state"] for item in active_checks}
        overall = next((state for state in ("offline", "error", "warning", "pending", "success") if state in states), "unknown")
        if any(item["confirmed"] for item in active_checks):
            overall = "error"
        # 未检测项目不能由其他项目的成功结果推断为正常。
        if overall == "success" and "unknown" in states:
            overall = "unknown"
        times = [item.get("tested_at") or "" for item in checks.values()]
        url = urlparse(site.url or "")
        safe_url = urlunparse((url.scheme or "https", url.netloc.rsplit("@", 1)[-1], url.path or "/", "", "", ""))
        rows.append({"id": site.id, "name": site.name, "domain": site.domain, "url": safe_url,
                     "active": bool(getattr(site, "is_active", True)), "adapted": _site_has_rule(site.domain),
                     "monitored": not plugin._site_ids or site.id in plugin._site_ids,
                     "checks": checks, "state": overall, "confirmed": any(item["confirmed"] for item in active_checks),
                     "failure_days": max((item["failure_days"] for item in active_checks), default=0), "last_checked_at": max(times, default="")})
    upcoming = next_run(plugin._monitor_plan, datetime.now()) if plugin._enabled else None
    preview = dict(plugin._latest_missing_preview)
    preview["items"] = [{**item, "monitor": _view_monitor_result(item.get("monitor") or {"state": "error", "message": "下载器报告文件缺失"}, threshold)}
                        for item in preview.get("items", [])]
    preview["confirmed_count"] = sum(item["monitor"]["confirmed"] for item in preview["items"])
    preview["downloader_status"] = [_view_monitor_result(item, threshold) for item in preview.get("downloader_status", [])]
    return schemas.Response(success=True, data={
        "plugin_version": plugin.plugin_version, "enabled": plugin._enabled, "sites": rows,
        "plan": {**plugin._monitor_plan, "timezone": str(getattr(settings, "TZ", "服务器本地时区")),
                 "next_run_at": upcoming.strftime("%Y-%m-%d %H:%M:%S") if upcoming else ""},
        "jobs": plugin._latest_job_states, "runs": plugin.get_data("monitor_runs") or [],
        "maintenance": {"missing": preview, "cleanup": plugin._latest_missing_cleanup,
                        "delete_files": plugin._cleanup_delete_files, "auto_cleanup": plugin._cleanup_auto_cleanup},
        "rules": [{"domain": rule.get("domain"), "indexer": isinstance(rule.get("indexer"), dict),
                   "userdata": isinstance(rule.get("userdata"), dict), "asyncfix": bool(rule.get("asyncfix"))}
                  for rule in plugin._site_rules],
    })


def _redact_error_text(text: str) -> str:
    text = re.sub(r"https?://[^\s<>\"']+", lambda match: _mask_url(match.group()), str(text))
    # HTTP 头可能含空格、分号及多项 Cookie，需要整体隐藏，不能只替换 Bearer 或首个值。
    text = re.sub(r'''(?im)(["']?(?:authorization|cookie|set-cookie)["']?\s*[:=]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\r\n}\]]*)''', r"\1***", text)
    return re.sub(r'''(?i)(["']?(?:api[-_]?key|passkey|token|password)["']?\s*[:=]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,;}'"\]]+)''', r"\1***", text)


def _api_errors(query: str = "", level: str = "all", page: int = 1, limit: int = 20) -> schemas.Response:
    plugin = sitetoolbox._instance
    if not plugin:
        return schemas.Response(success=False, message="插件未初始化")
    # 只读取最近的有界样本，避免每次刷新工作台都载入数十万条日志。
    path = plugin._error_log_path()
    groups = {}
    sampled = False
    if path.exists():
        with path.open("rb") as file_obj:
            file_obj.seek(0, 2)
            size = file_obj.tell()
            offset = max(0, size - 2 * 1024 * 1024)
            sampled = offset > 0
            file_obj.seek(offset)
            if offset:
                file_obj.readline()
            lines = file_obj.read().decode("utf-8", errors="replace").splitlines()
        cutoff = time.time() - plugin._error_retention_days * 86400
        for line in lines:
            try:
                item = json.loads(line)
            except (ValueError, TypeError):
                continue
            if _error_record_ts(item) < cutoff or (level != "all" and item.get("level") != level):
                continue
            message = _clip_text(_redact_error_text(_error_record_message(item)), 500)
            source = str(item.get("source") or "系统")
            if query.lower() not in f"{message} {source}".lower():
                continue
            key = (source, item.get("level"), message)
            group = groups.setdefault(key, {"source": source, "level": item.get("level"), "message": message,
                                             "count": 0, "first_at": item.get("time", ""), "last_at": item.get("time", "")})
            group["count"] += 1
            group["last_at"] = max(group["last_at"], item.get("time", ""))
    items = sorted(groups.values(), key=lambda item: item["last_at"], reverse=True)
    page, limit = max(1, page), max(1, min(100, limit))
    return schemas.Response(success=True, data={"items": items[(page - 1) * limit:page * limit], "total": len(items),
                                               "page": page, "limit": limit, "sampled": sampled})


def _api_test_selected_rss(payload: Optional[dict] = Body(default=None)) -> schemas.Response:
    plugin = sitetoolbox._instance
    if not plugin:
        return schemas.Response(success=False, message="插件未初始化")
    site_ids = _selected_site_ids(plugin, payload)
    if not site_ids:
        return schemas.Response(success=False, message="未选择站点")

    def worker() -> str:
        results = [_test_site_rss(site_id) for site_id in site_ids]
        plugin._save_results(results)
        ok_count = len([item for item in results if item.get("state") == "success"])
        offline_count = len([item for item in results if item.get("state") == "offline"])
        pending_count = len([item for item in results if item.get("state") == "pending"])
        return f"RSS测试完成：成功 {ok_count}/{len(results)}，待观察 {pending_count}，站点连接失败 {offline_count}"

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
    site_ids = _selected_site_ids(plugin, payload)
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
    site_ids = _selected_site_ids(plugin, payload)
    if not site_ids:
        return schemas.Response(success=False, message="没有启用的目标站点")

    def worker() -> str:
        results = _check_userdata_health(site_ids=site_ids)
        results = plugin._save_userdata_results(results)
        bad_count = sum(bool(item.get("confirmed")) for item in results)
        pending_count = sum(item.get("state") == "pending" for item in results)
        offline_count = len([item for item in results if item.get("state") == "offline"])
        return f"用户数据检查完成：确认异常 {bad_count}/{len(results)}，待观察 {pending_count}，站点连接失败 {offline_count}"

    return plugin._start_background_job("userdata_check", "用户数据检查", worker)


def _api_probe_indexer(site_id: int, steps: Optional[str] = None) -> schemas.Response:
    """
    诊断站点索引器：索引器定义、同步/异步抓取、蜘蛛解析分步报告。
    用于定位"站点连通但搜索/刷流拿不到种子"类问题。
    steps 可选，逗号分隔按需执行以避免网关超时：fetch, matrix, aru, spider
    """
    import asyncio
    import traceback as _tb

    wanted = set((steps or "fetch,matrix,aru,spider").replace(" ", "").split(","))

    site = SiteOper().get(site_id)
    if not site:
        return schemas.Response(success=False, message=f"站点 {site_id} 不存在")
    domain = StringUtils.get_url_domain(site.url)
    report: Dict[str, Any] = {
        "site_id": site_id,
        "site_name": site.name,
        "domain": domain,
        "site_proxy": bool(site.proxy),
    }

    # 1. 索引器定义
    indexer = None
    try:
        indexer = SitesHelper().get_indexer(domain)
    except Exception as err:
        report["indexer"] = {"found": False, "error": f"{type(err).__name__}: {err}"}
    if indexer:
        torrents_conf = indexer.get("torrents") or {}
        browse_conf = indexer.get("browse") or {}
        search_conf = indexer.get("search") or {}
        report["indexer"] = {
            "found": True,
            "id": indexer.get("id"),
            "name": indexer.get("name"),
            "schema": indexer.get("schema"),
            "parser": indexer.get("parser"),
            "render": indexer.get("render"),
            "public": indexer.get("public"),
            "domain": indexer.get("domain"),
            "search_paths": [p.get("path") for p in (search_conf.get("paths") or []) if isinstance(p, dict)],
            "browse_path": browse_conf.get("path") if isinstance(browse_conf, dict) else None,
            "has_torrents_fields": bool(torrents_conf.get("fields")),
            "torrents_list_selector": (torrents_conf.get("list") or {}).get("selector"),
        }
    elif "indexer" not in report:
        report["indexer"] = {"found": False}

    # 2. 目标 URL：优先索引器搜索路径，否则默认 torrents.php
    base = (indexer.get("domain") if indexer else None) or site.url or ""
    if base and not base.endswith("/"):
        base += "/"
    paths = ((indexer or {}).get("search") or {}).get("paths") or []
    path = paths[0].get("path") if paths and isinstance(paths[0], dict) else "torrents.php"
    target = urljoin(base, path or "torrents.php")
    report["target_url"] = target

    ua = site.ua or settings.USER_AGENT
    proxies = settings.PROXY if site.proxy else None
    timeout = site.timeout or 15

    def _res_summary(res) -> Dict[str, Any]:
        if res is None:
            return {"ok": False, "error": "无响应(None)，请求层异常被吞"}
        text = res.text or ""
        lower = text.lower()
        title_match = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
        return {
            "ok": res.status_code == 200,
            "status": res.status_code,
            "final_url": str(getattr(res, "url", "") or ""),
            "length": len(text),
            "logged_in": ("logout.php" in lower or "usercp.php" in lower),
            "login_wall": ("takelogin" in lower or ("login.php" in lower and "logout.php" not in lower)),
            "title": title_match.group(1).strip()[:80] if title_match else "",
        }

    # 3. 同步 requests 抓取（spider 步骤也依赖它的 HTML）
    sync_html = ""
    if wanted & {"fetch", "spider"}:
        try:
            res = RequestUtils(cookies=site.cookie, ua=ua, timeout=timeout,
                               proxies=proxies).get_res(target, allow_redirects=True)
            report["sync_fetch"] = _res_summary(res)
            sync_html = res.text if res is not None else ""
        except Exception as err:
            report["sync_fetch"] = {"ok": False, "error": f"{type(err).__name__}: {err}"}

    # 4. 异步 httpx 抓取（直连 httpx，不吞异常，暴露核心异步链路的真实错误）
    async def _async_probe():
        import httpx
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            return await client.get(target, headers={"User-Agent": ua, "Cookie": site.cookie or ""})

    if "fetch" in wanted:
        try:
            ares = asyncio.run(_async_probe())
            atext = ares.text or ""
            alower = atext.lower()
            report["async_fetch"] = {
                "ok": ares.status_code == 200,
                "status": ares.status_code,
                "final_url": str(ares.url),
                "length": len(atext),
                "logged_in": ("logout.php" in alower or "usercp.php" in alower),
            }
        except Exception as err:
            report["async_fetch"] = {"ok": False, "error": f"{type(err).__name__}: {err}"}

    # 4.5 httpx 请求特征矩阵：定位站点 WAF 对异步请求 404 的触发因子
    async def _matrix_probe(verify: bool, with_content_type: bool):
        import httpx
        headers = {"User-Agent": ua, "Cookie": site.cookie or ""}
        if with_content_type:
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True,
                                     verify=verify) as client:
            res = await client.get(target, headers=headers)
            return res.status_code, len(res.text or "")

    matrix = {}
    if "matrix" in wanted:
        for verify_flag in (True, False):
            for ct_flag in (True, False):
                key = f"verify={verify_flag},content_type={ct_flag}"
                try:
                    status, length = asyncio.run(_matrix_probe(verify_flag, ct_flag))
                    matrix[key] = {"status": status, "length": length}
                except Exception as err:
                    matrix[key] = {"error": f"{type(err).__name__}: {err}"}

    # 追加变体：UA 插件后缀 / 完整 ARU 头组合
    async def _matrix_probe_headers(headers: dict, verify: bool):
        import httpx
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True,
                                     verify=verify) as client:
            res = await client.get(target, headers=headers)
            return res.status_code, len(res.text or "")

    ua_suffixed = f"{ua} Plugin/sitetoolbox"
    extra_variants = {
        "ua_plugin_suffix": ({"User-Agent": ua_suffixed, "Cookie": site.cookie or ""}, False),
        "aru_exact_headers": ({"User-Agent": ua_suffixed,
                               "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                               "Cookie": site.cookie or ""}, False),
    }
    # http2 变体（v2.14 ARU 可能启用 http2，ALPN 指纹不同）
    async def _matrix_probe_http2():
        import httpx
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, verify=False,
                                     http2=True) as client:
            res = await client.get(target, headers={"User-Agent": ua_suffixed,
                                                    "Cookie": site.cookie or ""})
            return res.status_code, len(res.text or ""), str(res.http_version)

    if "matrix" in wanted:
        for key, (variant_headers, verify_flag) in extra_variants.items():
            try:
                status, length = asyncio.run(_matrix_probe_headers(variant_headers, verify_flag))
                matrix[key] = {"status": status, "length": length}
            except Exception as err:
                matrix[key] = {"error": f"{type(err).__name__}: {err}"}
        try:
            status, length, http_version = asyncio.run(_matrix_probe_http2())
            matrix["http2"] = {"status": status, "length": length, "http_version": http_version}
        except Exception as err:
            matrix["http2"] = {"error": f"{type(err).__name__}: {err}"}
        report["httpx_matrix"] = matrix

    # asyncfix 补丁挂载状态自检
    report["asyncfix_status"] = {
        "patched": sitetoolbox._aru_patched,
        "domains": sitetoolbox._asyncfix_raw_cookie_domains,
        "domain_matched": _asyncfix_domain_match(target, sitetoolbox._asyncfix_raw_cookie_domains),
    }

    # 5. AsyncRequestUtils 原样复刻（与核心 async_get_torrents 完全相同的调用方式）
    if "aru" not in wanted:
        pass
    else:
      try:
        from app.utils.http import AsyncRequestUtils

        aru_obj = AsyncRequestUtils(ua=ua, cookies=site.cookie, timeout=timeout,
                                    referer=None, proxies=proxies)
        report["aru_instance"] = {
            "has_raw_cookie_attr": bool(getattr(aru_obj, "_sitetoolbox_raw_cookie", None)),
            "init_func": f"{AsyncRequestUtils.__init__.__module__}.{AsyncRequestUtils.__init__.__qualname__}",
            "request_func": f"{AsyncRequestUtils.request.__module__}.{AsyncRequestUtils.request.__qualname__}",
            "inject_count_before": getattr(sitetoolbox, "_aru_inject_count", 0),
        }

        async def _aru_probe():
            return await aru_obj.get_res(target, allow_redirects=True)

        aru_res = asyncio.run(_aru_probe())
        report["async_requestutils_fetch"] = _res_summary(aru_res)
        report["aru_instance"]["inject_count_after"] = getattr(sitetoolbox, "_aru_inject_count", 0)
      except Exception as err:
        report["async_requestutils_fetch"] = {"ok": False, "error": f"{type(err).__name__}: {err}",
                                              "trace": _tb.format_exc()[-600:]}

    # 6. 动态发现蜘蛛类（v2.10 为 TorrentSpider，v2.14 可能改名/移位）
    def _find_spider_cls():
        import app.modules.indexer.spider as spider_pkg
        candidates = []
        for attr in dir(spider_pkg):
            obj = getattr(spider_pkg, attr, None)
            if isinstance(obj, type) and hasattr(obj, "get_torrents"):
                candidates.append(obj)
        for cls in candidates:
            if cls.__name__ == "TorrentSpider":
                return cls, [c.__name__ for c in candidates]
        for module_info in pkgutil.iter_modules(spider_pkg.__path__):
            try:
                mod = importlib.import_module(f"app.modules.indexer.spider.{module_info.name}")
            except Exception:
                continue
            for attr in dir(mod):
                obj = getattr(mod, attr, None)
                if isinstance(obj, type) and attr == "TorrentSpider" and hasattr(obj, "get_torrents"):
                    return obj, [attr]
                if isinstance(obj, type) and hasattr(obj, "get_torrents") and hasattr(obj, "parse"):
                    candidates.append(obj)
        return (candidates[0], [c.__name__ for c in candidates]) if candidates else (None, [])

    # 7. 蜘蛛解析（对同步抓到的 HTML 跑解析，隔离"抓取失败"与"解析失败"）
    probe_indexer = None
    spider_cls = None
    if "spider" not in wanted:
        return schemas.Response(success=True, data=report)
    try:
        spider_cls, spider_names = _find_spider_cls()
        report["spider_class"] = {"picked": spider_cls.__name__ if spider_cls else None,
                                  "candidates": spider_names}
        if spider_cls and indexer and indexer.get("torrents"):
            probe_indexer = dict(indexer)
            probe_indexer.update({"cookie": site.cookie, "ua": ua, "proxy": site.proxy})
            if sync_html:
                spider = spider_cls(probe_indexer)
                rows = spider.parse(sync_html)
                report["spider_parse"] = {
                    "ok": True,
                    "count": len(rows or []),
                    "first_title": (rows[0].get("title") if rows else None),
                }
            else:
                report["spider_parse"] = {"ok": False, "error": "同步抓取无内容，跳过解析"}
        else:
            report["spider_parse"] = {"ok": False,
                                      "error": "未找到蜘蛛类" if not spider_cls else "索引器缺失或没有 torrents 字段定义"}
    except Exception as err:
        report["spider_parse"] = {"ok": False, "error": f"{type(err).__name__}: {err}",
                                  "trace": _tb.format_exc()[-800:]}

    # 8. 蜘蛛完整流程（含请求），与核心刷流/搜索同路径；重点报告促销字段解析结果
    try:
        if spider_cls and probe_indexer:
            live = spider_cls(probe_indexer).get_torrents() or []
            factor_dist: Dict[str, int] = {}
            for row in live:
                key = f"dl={row.get('downloadvolumefactor')},up={row.get('uploadvolumefactor')}"
                factor_dist[key] = factor_dist.get(key, 0) + 1
            frees = [r for r in live if r.get("downloadvolumefactor") in (0, 0.0)]
            seeder_dist: Dict[str, int] = {}
            size_zero = 0
            for row in frees:
                seeders = row.get("seeders")
                bucket = "None" if seeders is None else (
                    "0" if not str(seeders).strip() or str(seeders) == "0" else "1+")
                seeder_dist[bucket] = seeder_dist.get(bucket, 0) + 1
                if not row.get("size"):
                    size_zero += 1
            report["spider_live"] = {
                "count": len(live),
                "free_count": len(frees),
                "factor_distribution": factor_dist,
                "free_seeders_dist": seeder_dist,
                "free_size_zero_count": size_zero,
                "samples": [
                    {"title": (r.get("title") or "")[:50],
                     "dl": r.get("downloadvolumefactor"),
                     "up": r.get("uploadvolumefactor"),
                     "hr": r.get("hit_and_run"),
                     "seeders": r.get("seeders"),
                     "size": r.get("size"),
                     "pubdate": r.get("pubdate")}
                    for r in frees[:5]
                ],
            }
    except Exception as err:
        report["spider_live"] = {"error": f"{type(err).__name__}: {err}"}

    return schemas.Response(success=True, data=report)


def _api_probe_page(site_id: int, path: Optional[str] = None, max_length: Optional[int] = 300000) -> schemas.Response:
    """
    诊断站点页面：只允许访问站点自身域名，返回状态和 HTML（截断到 max_length）。
    """
    site = SiteOper().get(site_id)
    if not site or not site.url:
        return schemas.Response(success=False, message=f"站点 {site_id} 不存在或地址为空")
    target = urljoin(site.url if site.url.endswith("/") else f"{site.url}/", (path or "").lstrip("/"))
    if StringUtils.get_url_domain(target) != StringUtils.get_url_domain(site.url):
        return schemas.Response(success=False, message="只允许访问站点自身域名")
    try:
        res = RequestUtils(cookies=site.cookie, ua=site.ua or settings.USER_AGENT,
                           timeout=site.timeout or 20,
                           proxies=settings.PROXY if site.proxy else None).get_res(target, allow_redirects=True)
    except Exception as err:
        return schemas.Response(success=False, message=f"{type(err).__name__}: {err}")
    if res is None:
        return schemas.Response(success=False, message="无响应")
    text = res.text or ""
    limit = _int_or_default(max_length, 300000, minimum=1000, maximum=2000000)
    return schemas.Response(success=True, data={
        "status": res.status_code,
        "final_url": str(getattr(res, "url", "") or ""),
        "length": len(text),
        "html": text[:limit],
    })


def _api_probe_logfile(logfile: str, keyword: Optional[str] = None,
                       start: Optional[str] = None, end: Optional[str] = None,
                       max_lines: int = 300) -> schemas.Response:
    """
    读取日志文件并按时间窗/关键字过滤（系统日志接口只能看尾部，这个探针可以挖历史段落）。
    :param logfile: 相对 LOG_PATH 的文件名，如 moviepilot.log 或 plugins/xxx.log
    :param keyword: 可选正则，命中才返回
    :param start/end: 可选时间过滤，格式 "2026-07-25 02:00"（按行内时间戳前缀比较）
    """
    from pathlib import Path as _Path

    base = _Path(settings.LOG_PATH).resolve()
    path = (base / logfile).resolve()
    if not str(path).startswith(str(base)):
        return schemas.Response(success=False, message="非法路径")
    if not path.exists():
        return schemas.Response(success=False, message=f"日志不存在：{logfile}")

    ts_re = re.compile(r"】(\d{4}-\d{2}-\d{2} \d{2}:\d{2})")
    kw_re = None
    if keyword:
        try:
            kw_re = re.compile(keyword)
        except re.error:
            return schemas.Response(success=False, message="keyword 正则错误")

    matched: List[str] = []
    scanned = 0
    truncated = False
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                scanned += 1
                if scanned > 2_000_000:
                    truncated = True
                    break
                if start or end:
                    m = ts_re.search(line[:40])
                    if m:
                        ts = m.group(1)
                        if start and ts < start:
                            continue
                        if end and ts > end:
                            continue
                    elif start or end:
                        continue
                if kw_re and not kw_re.search(line):
                    continue
                matched.append(line.rstrip("\n")[:500])
                if len(matched) >= max_lines:
                    truncated = True
                    break
    except Exception as err:
        return schemas.Response(success=False, message=f"读取失败：{err}")

    return schemas.Response(success=True, data={
        "file": str(path), "scanned_lines": scanned,
        "matched": len(matched), "truncated": truncated, "lines": matched,
    })


def _api_probe_alembic(detail: Optional[bool] = False) -> schemas.Response:
    """
    诊断数据库迁移状态：alembic_version 表内容 vs 迁移文件链的 head。
    用于定位启动时"数据库更新失败：Multiple head revisions"。
    """
    from pathlib import Path as _Path

    report: Dict[str, Any] = {}
    # 1. 数据库中的版本记录
    try:
        from sqlalchemy import text as _text
        from app.db import Engine
        with Engine.connect() as conn:
            rows = conn.execute(_text("SELECT version_num FROM alembic_version")).fetchall()
        report["db_version_rows"] = [r[0] for r in rows]
    except Exception as err:
        report["db_version_rows_error"] = f"{type(err).__name__}: {err}"

    # 2. 迁移文件链
    try:
        versions_dir = _Path(settings.ROOT_PATH) / "database" / "versions"
        revisions: Dict[str, Optional[str]] = {}
        for f in versions_dir.glob("*.py"):
            content = f.read_text(encoding="utf-8", errors="ignore")
            rev = re.search(r"^revision\s*=\s*['\"]([^'\"]+)['\"]", content, re.M)
            down = re.search(r"^down_revision\s*=\s*['\"]([^'\"]+)['\"]", content, re.M)
            if rev:
                revisions[rev.group(1)] = down.group(1) if down else None
        referenced = {d for d in revisions.values() if d}
        heads = [r for r in revisions if r not in referenced]
        report["versions_dir"] = str(versions_dir)
        report["revision_count"] = len(revisions)
        report["heads"] = heads
        # head 对应文件名
        head_files = {}
        for f in versions_dir.glob("*.py"):
            content = f.read_text(encoding="utf-8", errors="ignore")
            for h in heads:
                if re.search(rf"^revision\s*=\s*['\"]{re.escape(h)}['\"]", content, re.M):
                    head_files[h] = f.name
        report["head_files"] = head_files

        if detail:
            details = {}
            for h, fname in head_files.items():
                fp = versions_dir / fname
                st = fp.stat()
                details[fname] = {
                    "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
                    "size": st.st_size,
                    "content": fp.read_text(encoding="utf-8", errors="ignore")[:4000],
                }
            report["head_file_details"] = details
    except Exception as err:
        report["versions_error"] = f"{type(err).__name__}: {err}"

    return schemas.Response(success=True, data=report)


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
    cleanup_items = [item for item in plugin._confirmed_missing_items(preview) if item.get("downloader") in selected_names]
    if not cleanup_items:
        return schemas.Response(success=False, message="没有达到连续异常确认天数的缺失种子")

    delete_files = _to_bool((payload or {}).get("delete_files"), plugin._cleanup_delete_files)

    def worker() -> str:
        logger.info(
            f"站点工具箱开始清理缺失种子：候选 {len(cleanup_items)} 个，"
            f"downloaders={downloader_names}，delete_files={delete_files}"
        )
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
        logger.info(f"站点工具箱{message}，delete_files={delete_files}")
        return message

    return plugin._start_background_job("missing_cleanup", "缺失种子清理", worker)


def _api_check_mteam_login(payload: Optional[dict] = Body(default=None)) -> schemas.Response:
    plugin = sitetoolbox._instance
    if not plugin:
        return schemas.Response(success=False, message="插件未初始化")
    site_ids = _selected_site_ids(plugin, payload)
    if plugin._mteam_site_ids:
        site_ids = [site_id for site_id in site_ids if site_id in plugin._mteam_site_ids]
    if not site_ids:
        return schemas.Response(success=False, message="没有匹配的馒头检查站点")

    def worker() -> str:
        result = _build_mteam_login_check(site_ids, plugin._mteam_warning_days)
        items = plugin._save_mteam_login_check(result, site_ids=site_ids)
        confirmed = sum(bool(item.get("confirmed")) for item in items)
        pending = sum(item.get("state") == "pending" for item in items)
        return f"馒头登录检查完成：确认异常 {confirmed}/{len(items)}，待观察 {pending}"

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

    history_params = {}
    if base["user_id"]:
        history_params["userId"] = _mteam_user_id_value(base["user_id"])
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
            logger.info(
                f"站点工具箱调用下载器删除缺失种子：downloader={downloader_name}，"
                f"count={len(delete_hashes)}，delete_files={delete_files}"
            )
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
    result = _test_site_rss_inner(site_id, repair=repair)
    # RSS 失败时，若系统站点管理记录站点最近连接失败（如站点维护），不判为 RSS 异常
    if result.get("state") == "error" and result.get("domain"):
        site = SiteOper().get(site_id)
        failure = _site_failure(site, _site_statistics().get(result["domain"])) if site else None
        if failure:
            result["state"] = failure[0]
            result["message"] = f"{failure[1]}，RSS 结果不可信（{result.get('message') or '请求失败'}）"
    plugin = sitetoolbox._instance
    return plugin._record_rss_result(result) if plugin else result


def _rss_result_status(result: Dict[str, Any], threshold: int) -> Dict[str, Any]:
    result = classify_monitor(result, threshold)
    result.update(rss_state=result["raw_state"], rss_message=result["raw_message"])
    return result


def _test_site_rss_inner(site_id: int, repair: bool = False) -> Dict[str, Any]:
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


def _refresh_site_userdata(site: Any):
    """沿用宿主的单站采集入口；不能把读取旧快照当作本次采集成功。"""
    if not getattr(site, "is_active", True):
        raise ValueError("站点未启用")
    from app.chain.site import SiteChain
    indexer = SitesHelper().get_indexer(site.domain)
    if not indexer:
        raise ValueError("站点不支持用户数据采集或未通过认证")
    data = SiteChain().refresh_userdata(site=indexer)
    if not data:
        raise ValueError("采集未返回用户数据")
    error = data.get("err_msg") if isinstance(data, dict) else getattr(data, "err_msg", None)
    if error:
        raise ValueError(str(error))


def _check_userdata_health(site_ids: Optional[List[int]] = None) -> List[Dict[str, Any]]:
    sites = [
        site for site in SiteOper().list_order_by_pri()
        if site and site.id and (not site_ids or site.id in site_ids)
    ]
    # 单站复测、专项检查和统一定时检测都先采集，再读取宿主保存的新记录。
    refresh_errors = {}
    for site in sites:
        try:
            _refresh_site_userdata(site)
        except Exception as err:
            refresh_errors[site.id] = _redact_error_text(str(err).strip() or type(err).__name__)
    latest_by_domain = {}
    for data in SiteOper().get_userdata_latest() or []:
        domain = getattr(data, "domain", "") or ""
        if domain:
            latest_by_domain[domain] = data

    statistics = _site_statistics()
    results = []
    for site in sites:
        error = refresh_errors.get(site.id)
        result = _userdata_health_result(
            site, latest_by_domain.get(site.domain or ""),
            failure=None if error else _site_failure(site, statistics.get(site.domain or "")),
            stale_days=sitetoolbox._userdata_stale_days,
        )
        result["refreshed"] = not bool(error)
        if error:
            retained = f"；保留上次数据（{result['updated_at']}）" if result.get("updated_at") else ""
            result.update(state="error", message=f"本次用户数据采集失败：{error}{retained}", refresh_error=error)
        results.append(result)
    return results


def _site_statistics() -> Dict[str, Any]:
    """
    读取系统站点管理的连接统计，按域名索引；读取失败时返回空字典，不影响原有判断
    """
    try:
        from app.db.models.sitestatistic import SiteStatistic
        try:
            # v3
            from app.db.session import SessionFactory
        except ImportError:
            # v2
            from app.db import SessionFactory
        db = SessionFactory()
        try:
            return {item.domain: item for item in db.query(SiteStatistic).all() if item.domain}
        finally:
            db.close()
    except Exception as err:
        logger.warning(f"读取站点连接统计失败：{err}")
        return {}


def _site_failure(site: Any, statistic: Any) -> Optional[Tuple[str, str]]:
    """
    系统站点管理最后一次访问失败（lst_state=1）时，用系统站点测试复测并按原因分类。
    :return: (状态, 说明)；站点正常或复测已恢复时返回 None
    """
    if not statistic or getattr(statistic, "lst_state", None) != 1:
        return None
    try:
        from app.chain.site import SiteChain
        ok, reason = SiteChain().test(site.url)
    except Exception as err:
        ok, reason = False, f"站点测试异常：{err}"
    if ok:
        return None
    reason = str(reason or "").strip() or "未知原因"
    # 站点维护时常跳转登录页，系统测试会误报 Cookie 失效，先识别维护提示页
    maintenance = _site_maintenance_notice(site)
    if maintenance:
        return "offline", f"站点维护中（页面提示：{maintenance}）"
    if "cookie" in reason.lower():
        return "error", f"Cookie 已失效，请更新站点 Cookie（站点测试：{reason}）"
    if "403" in reason:
        return "offline", f"站点拒绝访问，可能被 Cloudflare 拦截或 Cookie 失效（站点测试：{reason}）"
    return "offline", f"站点连接失败（站点测试：{reason}）"


def _site_maintenance_notice(site: Any) -> Optional[str]:
    """
    抓取站点首页，识别简短的维护提示页，返回页面提示文字；不是维护页时返回 None
    """
    try:
        res = RequestUtils(cookies=site.cookie, ua=site.ua or settings.USER_AGENT,
                           timeout=site.timeout or 20,
                           proxies=settings.PROXY if site.proxy else None).get_res(site.url, allow_redirects=True)
    except Exception:
        return None
    if res is None or not res.text:
        return None
    text = re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", res.text))).strip()
    # 只认短页面，避免正常页面里的公告误判
    if len(text) > 500 or not re.search(r"维护|maintenance|升级中|暂停访问|closed", text, re.I):
        return None
    return text[:100]


def _userdata_health_result(site: Any, data: Any = None, failure: Optional[Tuple[str, str]] = None,
                            stale_days: int = 2) -> Dict[str, Any]:
    issues: List[str] = []
    if not getattr(site, "is_active", True):
        issues.append("站点未启用")
    if not data:
        issues.append("没有用户数据")
        return {
            "site_id": getattr(site, "id", None),
            "site_name": getattr(site, "name", None) or "",
            "domain": getattr(site, "domain", None) or "",
            "state": failure[0] if failure else "error",
            "message": f"{failure[1]}，跳过用户数据判断" if failure else "；".join(issues),
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
    updated_at = _join_datetime(getattr(data, "updated_day", ""), getattr(data, "updated_time", ""))
    if failure:
        # 站点访问失败（维护、拦截、Cookie 失效）时，库里只有旧数据，不能当作正常，也不判为解析异常
        state = failure[0]
        issues = [f"{failure[1]}，用户数据停留在 {updated_at or '-'}"]
    else:
        updated_ts = _parse_datetime_ts(updated_at)
        if updated_ts and time.time() - updated_ts > stale_days * 86400:
            issues.append(f"用户数据已过期（{updated_at}，超过 {stale_days} 天未更新）")
            if state == "success":
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
        "updated_at": updated_at,
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
    asyncfix = config.get("asyncfix")

    if indexer is None and userdata is None and asyncfix is None:
        if _looks_like_indexer(config):
            indexer = config
        else:
            userdata = config

    return {
        "domain": domain,
        "indexer": indexer if isinstance(indexer, dict) else None,
        "userdata": userdata if isinstance(userdata, dict) else None,
        "asyncfix": asyncfix if isinstance(asyncfix, dict) else None,
    }


def _asyncfix_domain_match(url: str, domains: list) -> bool:
    """
    判断请求 URL 是否命中 asyncfix 配置域名（含子域名）
    """
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return False
    host = host.lower()
    for domain in domains or []:
        domain = str(domain or "").lower().strip()
        if domain and (host == domain or host.endswith("." + domain)):
            return True
    return False


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


_SEEDING_SUMMARY_COUNT_RE = re.compile(r"(\d[\d,]*)\s*条\s*记录")
_SEEDING_SUMMARY_SIZE_RES = [
    re.compile(r"总大小[：:\s]*([\d.]+\s*[KMGTP]i?B)", re.IGNORECASE),
    re.compile(r"Total[:：]?\s*([\d.]+\s*[KMGTP]i?B)", re.IGNORECASE),
    re.compile(r"共计\s*([\d.]+\s*[KMGTP]i?B)", re.IGNORECASE),
    re.compile(r"资源总量[：:\s]*([\d.]+\s*[KMGTP]i?B)", re.IGNORECASE),
]


def _apply_seeding_summary_fallback(parser: Any, html_text: str):
    """
    通用做种兜底：部分 NexusPHP 站点做种列表页表格结构与核心列检测失配，
    导致核心数到 0 条。这些站点的 getusertorrentlistajax 页面顶部均有一行
    规整的“N 条记录 … X TB”汇总。当核心解析出 seeding=0 时，直接从汇总行
    补回做种数量与做种体积，避免逐站写配置。
    """
    if not html_text or "条记录" not in str(html_text):
        return
    # 仅在核心未能解析出做种数时兜底，避免影响正常站点
    if getattr(parser, "seeding", 0):
        return
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(html_text)))
    count_match = _SEEDING_SUMMARY_COUNT_RE.search(text)
    if not count_match:
        return
    count = StringUtils.str_int(count_match.group(1).replace(",", ""))
    if count <= 0:
        return
    parser.seeding = count
    if not getattr(parser, "seeding_size", 0):
        for size_re in _SEEDING_SUMMARY_SIZE_RES:
            size_match = size_re.search(text)
            if size_match:
                parser.seeding_size = StringUtils.num_filesize(size_match.group(1).strip())
                break


def _get_last_seeding(domain: str) -> Optional[dict]:
    """读取该站点历史上最近一次做种数 > 0 的记录，用于抓取失败时沿用。"""
    if not domain:
        return None
    try:
        from app.db.site_oper import SiteOper
        rows = SiteOper().get_userdata_by_domain(domain) or []
    except Exception:
        return None
    best = None
    for row in rows:
        try:
            seeding = int(getattr(row, "seeding", 0) or 0)
        except (TypeError, ValueError):
            seeding = 0
        if seeding <= 0:
            continue
        key = (str(getattr(row, "updated_day", "") or ""), str(getattr(row, "updated_time", "") or ""))
        size = getattr(row, "seeding_size", 0) or 0
        if best is None or key > best[0]:
            best = (key, seeding, size)
    return {"seeding": best[1], "seeding_size": best[2]} if best else None


def _preserve_seeding_on_fetch_failure(parser: Any, html_text: str):
    """
    抗抖动：做种页抓取失败（SSL 中断等返回空响应）时，核心会得到 seeding=0，
    并在站点其余解析正常（err 为空）时用 0 覆盖历史好值，导致做种数在好值与
    0 之间跳变。此处仅在「本次解析其余正常、但做种页明显是抓取失败（空响应）」
    时，沿用历史最近一次的做种数据，避免 0 覆盖。
    """
    if getattr(parser, "seeding", 0):
        return
    # 仅对其余解析成功（已登录、有上传量）的情况兜底，避免影响未登录/异常场景
    if getattr(parser, "err_msg", None):
        return
    if not getattr(parser, "upload", 0):
        return
    text = str(html_text or "")
    # 页面确实返回了内容（含“没有记录”=真 0，或正常表格=核心已判定）→ 信任核心结果
    if text.strip():
        return
    # 到这里：做种页响应为空 = 抓取失败 → 沿用历史好值
    domain = _normalize_domain(getattr(parser, "_site_domain", "") or getattr(parser, "_base_url", ""))
    prev = _get_last_seeding(domain)
    if prev and prev.get("seeding"):
        parser.seeding = prev["seeding"]
        if not getattr(parser, "seeding_size", 0) and prev.get("seeding_size"):
            parser.seeding_size = prev["seeding_size"]
        logger.info(f"站点工具箱：{domain} 做种页抓取失败，沿用上次做种数据 seeding={prev['seeding']}")


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
    results = [_rss_result_status(item, plugin._rss_failure_days) for item in plugin._latest_results or []]
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
                            _status_chip("定时清理" if plugin._cleanup_auto_cleanup else "仅定时预览", "warning" if plugin._cleanup_auto_cleanup else "info"),
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
                    f"最近：{missing_time} · 定时：{plugin._cleanup_auto_cron if plugin._cleanup_auto_enabled else '关闭'}"
                    f"{' · 自动清理' if plugin._cleanup_auto_cleanup else ''}",
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
            _expansion_panel(
                "RSS 结果",
                f"正常 {rss_ok} / 待观察 {sum(item.get('state') == 'pending' for item in rss_results)} / 异常 {rss_bad}",
                [_result_table(rss_results)],
            ),
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
                            _compact_col(
                                "最近清理",
                                (
                                    f"{cleanup.get('deleted_count', 0)} 个 / "
                                    f"{'删文件' if cleanup.get('delete_files') else '仅任务'}"
                                ) if cleanup else "-",
                            ),
                        ],
                    },
                    {
                        "component": "VAlert",
                        "props": {
                            "type": "warning" if plugin._cleanup_delete_files else "info",
                            "variant": "tonal",
                            "text": (
                                "当前配置会同时删除下载器中的数据文件；清理前请确认预览列表。"
                                + (" 定时自动清理已开启。" if plugin._cleanup_auto_cleanup else "")
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


def _form_section(title: str, subtitle: str, content: List[dict]) -> dict:
    return {
        "component": "VCard",
        "props": {"variant": "outlined", "class": "mb-4"},
        "content": [
            {
                "component": "VCardText",
                "content": [
                    {
                        "component": "div",
                        "props": {"class": "d-flex flex-column ga-1 mb-3"},
                        "content": [
                            {"component": "div", "props": {"class": "text-subtitle-1 font-weight-medium"}, "text": title},
                            {"component": "div", "props": {"class": "text-caption text-medium-emphasis"}, "text": subtitle},
                        ],
                    },
                    *content,
                ],
            },
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
        "offline": "连接失败",
        "pending": "待观察",
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
    parsed = parsed._replace(netloc=parsed.netloc.rsplit("@", 1)[-1], fragment="")
    masked_query = []
    sensitive_keys = {"passkey", "pass", "password", "key", "api_key", "api-key", "token", "auth", "authorization", "cookie", "uid", "secure"}
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
    # 宿主使用自定义 headers 时不会补 UA，带 Cookie 的 RSS 请求也需保留站点 UA。
    headers = {"Cookie": site.cookie, "User-Agent": site.ua or settings.USER_AGENT} if site.cookie else None
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
