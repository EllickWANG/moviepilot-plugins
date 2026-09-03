"""完全由插件维护的站点直搜订阅。"""

from __future__ import annotations

import copy
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from apscheduler.triggers.cron import CronTrigger
from fastapi import Body

from app import schemas
from app.chain.download import DownloadChain
from app.chain.search import SearchChain
from app.chain.transfer import JobManager, TransferChain
from app.core.config import global_vars, settings
from app.core.context import Context, MediaInfo
from app.core.event import Event as MPEvent, eventmanager
from app.core.metainfo import MetaInfo
from app.db.downloadhistory_oper import DownloadHistoryOper
from app.db.site_oper import SiteOper
from app.db.transferhistory_oper import TransferHistoryOper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import EventType, MediaType, NotificationType

from .core import (
    MAX_RESOURCE_HISTORY,
    MAX_TASK_LOGS,
    candidate_score,
    candidate_sort_key,
    episodes_text,
    extract_episode_numbers,
    is_duplicate_download_message,
    missing_episodes,
    normalize_task,
    normalize_priority_mode,
    now_text,
    parse_bool,
    parse_episodes,
    parse_int,
    resource_fingerprint,
    resource_identity,
    target_episodes,
    task_search_keywords,
    title_matches,
    validate_task,
    word_filter_reason,
)


PLUGIN_ID = "directsearchsubscribe"
TASKS_KEY = "tasks_v2"
TRASH_KEY = "tasks_v2_trash"
LEGACY_TASKS_KEY = "direct_subscribes"
MAX_TRASH = 100


class directsearchsubscribe(_PluginBase):
    """自包含的直搜订阅插件。"""

    plugin_name = "直搜订阅"
    plugin_desc = "手工维护节目与集数，定时直搜站点；下载完成后按人工信息整理。"
    plugin_icon = "mdi-magnify-scan"
    plugin_version = "2.3.1"
    plugin_author = "Ellick"
    plugin_order = 30
    auth_level = 1

    _instance: Optional["directsearchsubscribe"] = None
    _enabled = True
    _cron = "*/30 * * * *"
    _notify = True
    _max_downloads = 3
    _task_gap = 2
    _config: Dict[str, Any] = {}
    _data_lock = threading.RLock()
    _running_lock = threading.Lock()
    _download_lock = threading.Lock()
    _running_ids: Set[str] = set()
    _active_stop_event = threading.Event()
    _stop_event: threading.Event
    _transfer_patch_lock = threading.RLock()
    _transfer_retry_lock = threading.Lock()
    _transfer_context = threading.local()
    _transfer_patched = False
    _transfer_originals: Dict[str, Any] = {}

    def init_plugin(self, config: dict = None):
        """加载全局配置，并处理配置页的一次性建任务动作。"""
        self.__class__._active_stop_event.set()
        self._stop_event = threading.Event()
        self.__class__._active_stop_event = self._stop_event
        config = dict(config or {})
        self.__class__._instance = self
        self._enabled = parse_bool(config.get("enabled"), True)
        self._cron = str(config.get("cron") or "*/30 * * * *").strip()
        self._notify = parse_bool(config.get("notify"), True)
        self._max_downloads = parse_int(config.get("max_downloads"), 3, 1, 20) or 3
        self._task_gap = parse_int(config.get("task_gap"), 2, 0, 60) or 0
        self.__class__._enabled = self._enabled
        self.__class__._cron = self._cron
        self.__class__._notify = self._notify
        self.__class__._max_downloads = self._max_downloads
        self.__class__._task_gap = self._task_gap
        self._config = config

        # 下载完成后仍使用 MoviePilot 的转移链，但只为本插件下载注入手工媒体信息，
        # 并阻止电视剧整理阶段再次读取 TMDB 集信息。
        self._patch_transfer_chain()

        # 2.0.0 创建的任务只有插件标签，MoviePilot 下载管理会将其过滤掉。
        # 标签追加是幂等操作，每次加载时顺便修复仍保留在下载器中的历史任务。
        self._repair_download_tags()

        # 2.2 之前已经完成下载的任务可能因缺少外部媒体 ID 留下“未识别”失败历史。
        # 只自动补偿从未登记过整理状态的旧记录，明确失败的新记录留给用户手工重试。
        if self._enabled:
            self._start_failed_transfer_retry(legacy_only=True)

        if parse_bool(config.get("save_task_now"), False):
            result = self.create_task(_task_payload_from_config(config), update_same=True)
            config["save_task_now"] = False
            self.update_config(config)
            self._config = config
            if result.get("success") and parse_bool(config.get("run_after_save"), False):
                self._start_task_thread(str(result["task"]["id"]))

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_service(self) -> List[Dict[str, Any]]:
        """注册插件自己的周期任务，不复用系统订阅调度器。"""
        if not self._enabled or not self._cron:
            return []
        try:
            trigger = CronTrigger.from_crontab(self._cron)
        except Exception as err:
            logger.error(f"直搜订阅 cron 无效：{self._cron} - {err}")
            return []
        return [{
            "id": "directsearchsubscribe_scan",
            "name": "直搜订阅定时检查",
            "trigger": trigger,
            "func": self.run_scheduled,
            "kwargs": {},
        }]

    def get_api(self) -> List[Dict[str, Any]]:
        return [
            _api("/tasks", self.api_list_tasks, ["GET"], "查询直搜任务"),
            _api("/tasks", self.api_create_task, ["POST"], "创建直搜任务"),
            _api("/tasks/{task_id}", self.api_update_task, ["PUT"], "更新直搜任务"),
            _api("/tasks/{task_id}/run", self.api_run_task, ["POST"], "立即检查直搜任务"),
            _api("/tasks/{task_id}/toggle", self.api_toggle_task, ["POST"], "暂停或恢复直搜任务"),
            _api("/tasks/{task_id}/auto", self.api_toggle_auto_download, ["POST"], "切换自动下载"),
            _api("/tasks/{task_id}/reset", self.api_reset_task, ["POST"], "重置直搜任务进度"),
            _api("/tasks/{task_id}/cleanup/prepare", self.api_prepare_cleanup,
                 ["POST"], "准备清理下载任务并重新处理"),
            _api("/tasks/{task_id}/cleanup/prepare-files", self.api_prepare_cleanup_files,
                 ["POST"], "准备清理下载任务和下载文件并重新处理"),
            _api("/tasks/{task_id}/cleanup/confirm", self.api_confirm_cleanup,
                 ["POST"], "确认清理并重新处理"),
            _api("/tasks/{task_id}/cleanup/cancel", self.api_cancel_cleanup,
                 ["POST"], "取消清理"),
            _api("/tasks/{task_id}/delete", self.api_delete_task, ["POST", "DELETE"], "移入回收站"),
            _api("/trash/{task_id}/restore", self.api_restore_task, ["POST"], "恢复直搜任务"),
            _api("/tasks/{task_id}/results", self.api_task_results, ["GET"], "查询最近候选"),
            _api("/tasks/{task_id}/logs", self.api_task_logs, ["GET"], "查询任务详细日志"),
            _api("/transfers/retry-failed", self.api_retry_failed_transfers, ["POST"], "重试失败整理"),
        ]

    def get_form(self) -> Tuple[Optional[List[dict]], Dict[str, Any]]:
        site_options = _active_site_options()
        return [
            {
                "component": "VForm",
                "content": [
                    _section("运行设置", [
                        _row([
                            _col(12, 3, _switch("enabled", "启用插件", "关闭后停止定时检查")),
                            _col(12, 3, _switch("notify", "下载结果通知", "有新下载时发送插件通知")),
                            _col(12, 3, _field("cron", "检查周期 (cron)", "*/30 * * * *")),
                            _col(12, 3, _number("max_downloads", "单次最多下载", 1, 20)),
                        ]),
                        _row([
                            _col(12, 3, _number("task_gap", "任务间隔（秒）", 0, 60)),
                            _col(12, 9, _alert(
                                "info",
                                "任务、进度和结果只保存在本插件中；不会创建系统订阅，也不会调用 TMDB、豆瓣或 Bangumi。",
                            )),
                        ]),
                    ]),
                    _section("创建或更新节目", [
                        _row([
                            _col(12, 8, _field("title", "节目名称", "从零开始的异世界生活")),
                            _col(12, 4, {
                                "component": "VSelect",
                                "props": {
                                    "model": "type",
                                    "label": "类型",
                                    "items": [
                                        {"title": "电视剧", "value": "电视剧"},
                                        {"title": "电影", "value": "电影"},
                                    ],
                                },
                            }),
                        ]),
                        _row([
                            _col(12, 3, _field("year", "年份（可选）", "2026")),
                            _col(12, 3, _number("season", "季（电影留空）", 1, 999)),
                            _col(12, 3, _number("start_episode", "起始集", 1, 99999)),
                            _col(12, 3, _number("total_episode", "总集数（可留空）", 1, 99999)),
                        ]),
                        _row([
                            _col(12, 6, _field("episodes", "指定目标集数", "1-12,14", "优先于总集数")),
                            _col(12, 6, _field("owned_episodes", "已有集数", "1-3", "创建时直接记为已获取")),
                        ]),
                        _row([
                            _col(12, 6, _textarea(
                                "keywords", "站点搜索关键词（每行一个）",
                                "Re Zero S04\n从零开始的异世界生活 第四季",
                            )),
                            _col(12, 6, _textarea(
                                "aliases", "标题别名（每行一个）",
                                "Re:Zero\nリゼロ",
                            )),
                        ]),
                    ]),
                    _section("匹配与下载", [
                        _row([
                            _col(12, 6, _field("include", "必须包含", "2160p,HEVC", "全部命中才保留")),
                            _col(12, 6, _field("exclude", "排除关键词", "试看,预告", "命中任意一个就跳过")),
                        ]),
                        _row([
                            _col(12, 8, {
                                "component": "VSelect",
                                "props": {
                                    "model": "sites",
                                    "label": "检查站点",
                                    "items": site_options,
                                    "multiple": True,
                                    "chips": True,
                                    "clearable": True,
                                    "hint": "留空时使用系统允许搜索的活动站点",
                                },
                            }),
                            _col(12, 4, _number("search_pages", "每个关键词搜索页数", 1, 5)),
                        ]),
                        _row([
                            _col(12, 4, {
                                "component": "VSelect",
                                "props": {
                                    "model": "priority_mode",
                                    "label": "种子优先规则",
                                    "items": [
                                        {"title": "做种数优先", "value": "seeders"},
                                        {"title": "综合优先", "value": "balanced"},
                                        {"title": "免费优先", "value": "free"},
                                        {"title": "发布时间优先", "value": "latest"},
                                        {"title": "小体积优先", "value": "smallest"},
                                        {"title": "大体积优先", "value": "largest"},
                                    ],
                                },
                            }),
                            _col(12, 4, _number("min_seeders", "最低做种数", 0, 1000000)),
                            _col(12, 4, _switch(
                                "dedupe_history", "下载历史去重",
                                "检查插件记录和 MoviePilot 下载历史，避免跨任务重复下载",
                            )),
                        ]),
                        _row([
                            _col(12, 4, _field("downloader", "下载器（可选）", "留空使用站点或系统默认")),
                            _col(12, 4, _field("save_path", "保存路径（可选）", "/media/downloads")),
                            _col(12, 4, _field("media_category", "二级分类（可选）", "日番")),
                        ]),
                        _row([
                            _col(12, 4, _switch("task_enabled", "任务启用", "关闭时只保存为暂停任务")),
                            _col(12, 4, _switch("auto_download", "自动下载", "默认关闭；关闭时仅更新候选预览")),
                            _col(12, 4, _switch("strict_title_match", "严格标题匹配", "要求标题命中节目名称、别名或搜索词")),
                        ]),
                        _row([
                            _col(12, 3, _switch(
                                "prefer_full_pack", "优先整包下载",
                                "整包覆盖任一缺集时，下载整包全部文件并覆盖记录整段集数",
                            )),
                            _col(12, 3, _switch(
                                "accept_unknown_episode", "允许未知集数下载",
                                "高风险：标题解析不出集数时也可自动下载；每个任务最多选择一个",
                            )),
                            _col(12, 3, _switch("save_task_now", "保存为插件任务", "保存配置时执行一次并自动复位")),
                            _col(12, 3, _switch("run_after_save", "保存后立即检查", "创建或更新成功后启动后台检查")),
                        ]),
                        _alert("warning", "同名、同类型、同季任务已存在时会更新配置并保留下载进度。"),
                    ]),
                ],
            }
        ], {
            "enabled": True,
            "notify": True,
            "cron": "*/30 * * * *",
            "max_downloads": 3,
            "task_gap": 2,
            "save_task_now": False,
            "run_after_save": False,
            "title": "",
            "type": "电视剧",
            "year": "",
            "season": "",
            "start_episode": 1,
            "total_episode": "",
            "episodes": "",
            "owned_episodes": "",
            "keywords": "",
            "aliases": "",
            "include": "",
            "exclude": "",
            "sites": [],
            "search_pages": 1,
            "priority_mode": "seeders",
            "min_seeders": 0,
            "dedupe_history": True,
            "downloader": "",
            "save_path": "",
            "media_category": "",
            "task_enabled": True,
            "auto_download": False,
            "strict_title_match": True,
            "accept_unknown_episode": False,
            "prefer_full_pack": True,
        }

    def get_page(self) -> Optional[List[dict]]:
        tasks = list(self._load_tasks().values())
        tasks.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        trash = list(self._load_trash().values())
        legacy = self.get_data(LEGACY_TASKS_KEY) or {}
        active = sum(1 for task in tasks if task.get("enabled") and task.get("status") != "completed")
        auto = sum(1 for task in tasks if task.get("auto_download"))
        contents = [
            _hero(self, len(tasks), active, auto), _task_collection(tasks),
            _recent_results(tasks), _run_log_table(tasks),
        ]
        if legacy:
            contents.insert(1, _alert(
                "warning",
                f"检测到旧版插件映射 {len(legacy)} 条。2.0 不读取、不执行也不删除这些旧数据；"
                "如系统订阅列表仍有旧任务，请人工确认后在系统订阅页删除。",
            ))
        if trash:
            contents.append(_trash_collection(trash[:20]))
        return contents

    def stop_service(self):
        """通知正在运行的任务尽快停止。"""
        getattr(self, "_stop_event", self.__class__._active_stop_event).set()
        self._unpatch_transfer_chain()

    @classmethod
    def _patch_transfer_chain(cls):
        """为本插件下载安装最小范围的手工整理钩子。"""
        with cls._transfer_patch_lock:
            if cls._transfer_patched:
                return
            handle_name = "_TransferChain__handle_transfer"
            media_id_name = "_JobManager__get_media_id"
            if not hasattr(TransferChain, handle_name) or not hasattr(JobManager, media_id_name):
                logger.error("直搜订阅无法启用手工整理：当前 MoviePilot 转移链接口不兼容")
                return
            cls._transfer_originals = {
                "do_transfer": TransferChain.do_transfer,
                "handle_transfer": getattr(TransferChain, handle_name),
                "job_media_id": JobManager.__dict__[media_id_name],
                "history_media": TransferHistoryOper.get_by_type_tmdbid,
            }
            TransferChain.do_transfer = _patched_transfer_do_transfer
            setattr(TransferChain, handle_name, _patched_transfer_handle)
            setattr(JobManager, media_id_name, staticmethod(_patched_job_media_id))
            TransferHistoryOper.get_by_type_tmdbid = _patched_transfer_history_media
            cls._transfer_patched = True
            logger.info("直搜订阅已启用下载完成后的手工媒体信息整理")

    @classmethod
    def _unpatch_transfer_chain(cls):
        """卸载时恢复转移链，避免影响非本插件下载。"""
        with cls._transfer_patch_lock:
            if not cls._transfer_patched:
                return
            handle_name = "_TransferChain__handle_transfer"
            media_id_name = "_JobManager__get_media_id"
            originals = cls._transfer_originals
            if TransferChain.do_transfer is _patched_transfer_do_transfer:
                TransferChain.do_transfer = originals["do_transfer"]
            if getattr(TransferChain, handle_name) is _patched_transfer_handle:
                setattr(TransferChain, handle_name, originals["handle_transfer"])
            if getattr(JobManager, media_id_name) is _patched_job_media_id:
                setattr(JobManager, media_id_name, originals["job_media_id"])
            if TransferHistoryOper.get_by_type_tmdbid is _patched_transfer_history_media:
                TransferHistoryOper.get_by_type_tmdbid = originals["history_media"]
            cls._transfer_originals = {}
            cls._transfer_patched = False
            logger.info("直搜订阅已恢复 MoviePilot 默认整理流程")

    @eventmanager.register([EventType.TransferComplete, EventType.TransferFailed])
    def on_transfer_result(self, event: MPEvent):
        """把本插件下载的整理结果回写到插件记录。"""
        data = event.event_data or {}
        download_hash = str(data.get("download_hash") or "").strip()
        if not download_hash:
            return
        direct_task = _direct_transfer_task(download_hash=download_hash)
        if not direct_task:
            return
        transferinfo = data.get("transferinfo")
        if event.event_type == EventType.TransferComplete:
            status = "completed"
            target_item = getattr(transferinfo, "target_item", None) \
                or getattr(transferinfo, "target_diritem", None)
            target = str(getattr(target_item, "path", "") or "")
            message = f"已整理到 {target}" if target else "整理完成"
        else:
            status = "failed"
            message = str(getattr(transferinfo, "message", "") or "整理失败")
            target = ""
        self._update_transfer_record(download_hash, status, message, target)

    def api_list_tasks(self) -> schemas.Response:
        return schemas.Response(success=True, data=list(self._load_tasks().values()))

    def api_create_task(self, payload: Optional[Dict[str, Any]] = Body(default=None)) -> schemas.Response:
        return _response(self.create_task(payload or {}, update_same=False))

    def api_update_task(self, task_id: str,
                        payload: Optional[Dict[str, Any]] = Body(default=None)) -> schemas.Response:
        return _response(self.update_task(task_id, payload or {}))

    def api_run_task(self, task_id: str) -> schemas.Response:
        return _response(self._start_task_thread(task_id))

    def api_toggle_task(self, task_id: str) -> schemas.Response:
        with self.__class__._data_lock:
            tasks = self._load_tasks()
            task = tasks.get(task_id)
            if not task:
                return schemas.Response(success=False, message="任务不存在")
            task["enabled"] = not parse_bool(task.get("enabled"), True)
            task["status"] = "active" if task["enabled"] else "paused"
            task["updated_at"] = now_text()
            tasks[task_id] = task
            self._save_tasks(tasks)
        return schemas.Response(success=True, message="任务已恢复" if task["enabled"] else "任务已暂停", data=task)

    def api_toggle_auto_download(self, task_id: str) -> schemas.Response:
        with self.__class__._data_lock:
            tasks = self._load_tasks()
            task = tasks.get(task_id)
            if not task:
                return schemas.Response(success=False, message="任务不存在")
            task["auto_download"] = not parse_bool(task.get("auto_download"), False)
            task["updated_at"] = now_text()
            tasks[task_id] = task
            self._save_tasks(tasks)
        state = "开启" if task["auto_download"] else "关闭"
        return schemas.Response(success=True, message=f"自动下载已{state}", data=task)

    def api_reset_task(self, task_id: str) -> schemas.Response:
        with self.__class__._data_lock:
            tasks = self._load_tasks()
            task = tasks.get(task_id)
            if not task:
                return schemas.Response(success=False, message="任务不存在")
            task["downloaded_episodes"] = sorted(parse_episodes(task.get("owned_episodes")))
            task["downloaded_fingerprints"] = []
            task["download_records"] = []
            task["status"] = "active" if task.get("enabled") else "paused"
            task["last_message"] = "进度已重置"
            task["updated_at"] = now_text()
            tasks[task_id] = task
            self._save_tasks(tasks)
        return schemas.Response(success=True, message="任务进度已重置", data=task)

    def api_prepare_cleanup(self, task_id: str) -> schemas.Response:
        return self._prepare_cleanup(task_id, delete_files=False)

    def api_prepare_cleanup_files(self, task_id: str) -> schemas.Response:
        return self._prepare_cleanup(task_id, delete_files=True)

    def _prepare_cleanup(self, task_id: str, delete_files: bool) -> schemas.Response:
        """进入五分钟的两步清理确认期，第一次调用不会删除任何内容。"""
        with self.__class__._data_lock:
            tasks = self._load_tasks()
            task = tasks.get(task_id)
            if not task:
                return schemas.Response(success=False, message="任务不存在")
            pending = {
                "delete_files": delete_files,
                "prepared_at": now_text(),
                "expires_at": (datetime.now() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"),
            }
            task["cleanup_pending"] = pending
            scope = "下载任务及下载文件" if delete_files else "下载任务（保留下载文件）"
            task["run_logs"] = [_audit_entry(
                "清理", "等待确认", f"已准备清理{scope}；五分钟内再次确认才会执行", level="warning"
            ), *(task.get("run_logs") or [])][:MAX_TASK_LOGS]
            task["updated_at"] = now_text()
            tasks[task_id] = task
            self._save_tasks(tasks)
        return schemas.Response(
            success=True,
            message=f"已准备清理{scope}，请在五分钟内点击“确认清理并重处理”",
            data=task,
        )

    def api_cancel_cleanup(self, task_id: str) -> schemas.Response:
        with self.__class__._data_lock:
            tasks = self._load_tasks()
            task = tasks.get(task_id)
            if not task:
                return schemas.Response(success=False, message="任务不存在")
            task["cleanup_pending"] = {}
            task["run_logs"] = [_audit_entry(
                "清理", "已取消", "用户取消了待确认的清理操作"
            ), *(task.get("run_logs") or [])][:MAX_TASK_LOGS]
            task["updated_at"] = now_text()
            tasks[task_id] = task
            self._save_tasks(tasks)
        return schemas.Response(success=True, message="已取消清理", data=task)

    def api_confirm_cleanup(self, task_id: str) -> schemas.Response:
        task = self._load_tasks().get(task_id)
        if not task:
            return schemas.Response(success=False, message="任务不存在")
        pending = task.get("cleanup_pending") or {}
        if not _cleanup_pending_active(pending):
            self._update_runtime(task_id, cleanup_pending={})
            return schemas.Response(success=False, message="清理确认已失效，请重新准备清理")
        if not self._claim_task(task_id):
            return schemas.Response(success=False, message="任务正在运行，请稍后再清理")
        try:
            result = self._cleanup_task(task, delete_files=parse_bool(pending.get("delete_files"), False))
        finally:
            self._release_task(task_id)
        if not result.get("success"):
            return _response(result)
        started = self._start_task_thread(task_id)
        message = str(result.get("message") or "清理完成")
        if started.get("success"):
            message += "；已开始重新检查和处理"
        return schemas.Response(success=True, message=message, data=self._load_tasks().get(task_id))

    def api_delete_task(self, task_id: str) -> schemas.Response:
        with self.__class__._data_lock:
            tasks = self._load_tasks()
            task = tasks.pop(task_id, None)
            if not task:
                return schemas.Response(success=False, message="任务不存在")
            task["deleted_at"] = now_text()
            trash = self._load_trash()
            trash[task_id] = task
            if len(trash) > MAX_TRASH:
                ordered = sorted(trash.values(), key=lambda item: str(item.get("deleted_at") or ""), reverse=True)
                trash = {item["id"]: item for item in ordered[:MAX_TRASH]}
            self._save_tasks(tasks)
            self._save_trash(trash)
        return schemas.Response(success=True, message="任务已移入回收站", data=task)

    def api_restore_task(self, task_id: str) -> schemas.Response:
        with self.__class__._data_lock:
            trash = self._load_trash()
            task = trash.pop(task_id, None)
            if not task:
                return schemas.Response(success=False, message="回收站中没有该任务")
            task.pop("deleted_at", None)
            task["updated_at"] = now_text()
            tasks = self._load_tasks()
            tasks[task_id] = task
            self._save_trash(trash)
            self._save_tasks(tasks)
        return schemas.Response(success=True, message="任务已恢复", data=task)

    def api_task_results(self, task_id: str) -> schemas.Response:
        task = self._load_tasks().get(task_id)
        if not task:
            return schemas.Response(success=False, message="任务不存在")
        return schemas.Response(success=True, data=task.get("last_results") or [])

    def api_task_logs(self, task_id: str) -> schemas.Response:
        task = self._load_tasks().get(task_id)
        if not task:
            return schemas.Response(success=False, message="任务不存在")
        return schemas.Response(success=True, data=task.get("run_logs") or [])

    def api_retry_failed_transfers(self) -> schemas.Response:
        if not self._start_failed_transfer_retry(legacy_only=False):
            return schemas.Response(success=True, message="失败整理重试已在运行")
        return schemas.Response(success=True, message="已开始在后台重试本插件的失败整理")

    def create_task(self, payload: Dict[str, Any], update_same: bool = False) -> Dict[str, Any]:
        task = normalize_task(payload)
        error = validate_task(task, payload)
        if error:
            return {"success": False, "message": error}
        with self.__class__._data_lock:
            tasks = self._load_tasks()
            duplicate = next((item for item in tasks.values() if _same_identity(item, task)), None)
            if duplicate and not update_same:
                return {"success": False, "message": f"同名任务已存在：{duplicate['id']}", "task": duplicate}
            if duplicate:
                task = normalize_task(payload, existing=duplicate)
                message = f"插件任务已更新：{task['id']}"
            else:
                message = f"插件任务已创建：{task['id']}"
            tasks[task["id"]] = task
            self._save_tasks(tasks)
        return {"success": True, "message": message, "task": task}

    def update_task(self, task_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self.__class__._data_lock:
            tasks = self._load_tasks()
            existing = tasks.get(task_id)
            if not existing:
                return {"success": False, "message": "任务不存在"}
            task = normalize_task(payload, existing=existing)
            error = validate_task(task, payload)
            if error:
                return {"success": False, "message": error}
            tasks[task_id] = task
            self._save_tasks(tasks)
        return {"success": True, "message": "任务已更新", "task": task}

    def run_scheduled(self):
        """按顺序检查全部启用任务，避免同时轰击站点。"""
        if not self._enabled or global_vars.is_system_stopped:
            return
        stop_event = self._stop_event
        task_ids = [task_id for task_id, task in self._load_tasks().items()
                    if task.get("enabled") and task.get("status") != "completed"]
        for index, task_id in enumerate(task_ids):
            if stop_event.is_set() or global_vars.is_system_stopped:
                break
            if self._claim_task(task_id):
                try:
                    self._execute_task(task_id, manual=False, stop_event=stop_event)
                finally:
                    self._release_task(task_id)
            if index < len(task_ids) - 1 and self._task_gap:
                stop_event.wait(self._task_gap)

    def _start_task_thread(self, task_id: str) -> Dict[str, Any]:
        if task_id not in self._load_tasks():
            return {"success": False, "message": "任务不存在"}
        if not self._claim_task(task_id):
            return {"success": True, "message": "任务已在运行", "task_id": task_id}

        stop_event = self._stop_event

        def runner():
            try:
                self._execute_task(task_id, manual=True, stop_event=stop_event)
            finally:
                self._release_task(task_id)

        threading.Thread(target=runner, name=f"direct-search-{task_id}", daemon=True).start()
        return {"success": True, "message": "任务已在后台开始", "task_id": task_id}

    def _execute_task(self, task_id: str, manual: bool, stop_event: threading.Event):
        task = self._load_tasks().get(task_id)
        if not task:
            return
        if not manual and (not task.get("enabled") or task.get("status") == "completed"):
            return

        run_id = f"{datetime.now().strftime('%Y%m%d%H%M%S%f')}-{threading.get_ident()}"
        audit: List[Dict[str, Any]] = []
        initial_missing = missing_episodes(task)
        _audit(audit, run_id, "运行", "开始", (
            f"{'手动' if manual else '定时'}检查；缺集 {episodes_text(initial_missing) or '持续追更'}；"
            f"优先规则 {normalize_priority_mode(task.get('priority_mode'))}；"
            f"整包优先 {'开启' if parse_bool(task.get('prefer_full_pack'), True) else '关闭'}；"
            f"自动下载 {'开启' if task.get('auto_download') else '关闭'}"
        ))
        known_identities = self._known_resource_identities()
        known_identities.difference_update(task.get("ignored_resource_identities") or [])
        if parse_bool(task.get("dedupe_history"), True):
            history_identities, history_episodes, history_has_movie = self._history_snapshot(task)
            known_identities.update(history_identities)
            downloaded = parse_episodes(task.get("downloaded_episodes"))
            recovered = history_episodes.difference(downloaded)
            _audit(audit, run_id, "历史", "检查", (
                f"命中 {len(history_identities)} 个历史发布标识；"
                f"恢复集数 {episodes_text(recovered) or '无'}"
            ))
            if recovered:
                downloaded.update(recovered)
                self._update_runtime(task_id, downloaded_episodes=sorted(downloaded))
                task = self._load_tasks().get(task_id) or task
            target = target_episodes(task)
            if history_has_movie or target and target.issubset(downloaded):
                message = "下载历史已覆盖全部目标，未重复搜索下载"
                _audit(audit, run_id, "运行", "完成", message)
                self._update_runtime(
                    task_id, status="completed", last_status="success", last_run_at=now_text(),
                    last_message=message, last_reason_summary=message,
                    last_download_count=0, last_duplicate_count=len(recovered),
                )
                self._save_run_logs(task_id, audit)
                return

        self._update_runtime(task_id, status="running", last_status="running",
                             last_run_at=now_text(), last_message="正在直接搜索站点")
        try:
            candidates, search_errors, duplicate_count = self._search_task(
                task, stop_event, known_identities, audit, run_id
            )
            results = [item[1] for item in candidates]
            self._update_runtime(
                task_id,
                last_results=results,
                last_match_count=len(results),
                last_message=f"找到 {len(results)} 个候选" + (f"；{'; '.join(search_errors[:2])}" if search_errors else ""),
            )
            task = self._load_tasks().get(task_id)
            if not task:
                return
            if task.get("auto_download"):
                downloads, runtime_duplicates = self._download_candidates(
                    task, candidates, stop_event, known_identities, audit, run_id
                )
                duplicate_count += runtime_duplicates
            else:
                downloads = []
                for _, result in candidates:
                    result["skip_reason"] = "预览模式，自动下载已关闭"
                    _audit(audit, run_id, "下载", "跳过", result["skip_reason"], result=result)
                self._update_runtime(task_id, last_results=results)
            latest = self._load_tasks().get(task_id)
            if not latest:
                return
            target = target_episodes(latest)
            downloaded = parse_episodes(latest.get("downloaded_episodes"))
            if latest.get("type") == MediaType.MOVIE.value and downloads:
                status = "completed"
            elif target and target.issubset(downloaded):
                status = "completed"
            elif latest.get("enabled"):
                status = "active"
            else:
                status = "paused"
            message = f"候选 {len(results)}，新增下载 {len(downloads)}"
            if duplicate_count:
                message += f"，跳过重复 {duplicate_count}"
            if not latest.get("auto_download"):
                message += "（预览模式）"
            if search_errors:
                message += f"，搜索异常 {len(search_errors)} 个"
            reason_summary = _reason_summary(results, downloads, duplicate_count, search_errors)
            _audit(audit, run_id, "运行", "完成", f"{message}；{reason_summary}")
            self._update_runtime(task_id, status=status,
                                 last_status="success" if not search_errors or results else "warning",
                                 last_message=message, last_download_count=len(downloads),
                                 last_duplicate_count=duplicate_count,
                                 last_reason_summary=reason_summary)
            self._save_run_logs(task_id, audit)
            if downloads and self._notify:
                self.post_message(mtype=NotificationType.Plugin,
                                  title=f"直搜订阅：{latest.get('name')}", text=message)
            logger.info(f"直搜订阅 {latest.get('name')} 完成：{message}")
        except Exception as err:
            logger.error(f"直搜订阅 {task.get('name')} 执行失败：{err}", exc_info=True)
            _audit(audit, run_id, "运行", "失败", str(err), level="error")
            self._update_runtime(task_id, status="error", last_status="error",
                                 last_message=str(err), last_reason_summary=str(err))
            self._save_run_logs(task_id, audit)

    def _search_task(self, task: Dict[str, Any], stop_event: threading.Event,
                     known_identities: Set[str], audit: List[Dict[str, Any]], run_id: str) \
            -> Tuple[List[Tuple[Context, Dict[str, Any]]], List[str], int]:
        contexts: Dict[str, Tuple[Context, Dict[str, Any]]] = {}
        errors = []
        duplicate_count = 0
        missing = missing_episodes(task)
        downloaded = parse_episodes(task.get("downloaded_episodes"))
        downloaded_fingerprints = set(task.get("downloaded_fingerprints") or [])
        pages = parse_int(task.get("search_pages"), 1, 1, 5) or 1
        sites = task.get("sites") or None
        search_chain = SearchChain()
        for keyword in task_search_keywords(task):
            for page in range(pages):
                if stop_event.is_set() or global_vars.is_system_stopped:
                    break
                try:
                    found = search_chain.search_by_title(keyword, page=page, sites=sites) or []
                except Exception as err:
                    errors.append(f"{keyword} 第{page + 1}页：{err}")
                    _audit(audit, run_id, "搜索", "异常",
                           f"关键词“{keyword}”第 {page + 1} 页：{err}", level="error")
                    continue
                _audit(audit, run_id, "搜索", "返回",
                       f"关键词“{keyword}”第 {page + 1} 页返回 {len(found)} 条")
                for context in found:
                    prepared, reason = _prepare_candidate(task, context, missing, downloaded)
                    if not prepared:
                        torrent = getattr(context, "torrent_info", None)
                        _audit(audit, run_id, "筛选", "排除", reason,
                               title=getattr(torrent, "title", ""),
                               site=getattr(torrent, "site_name", "") or getattr(torrent, "site", ""),
                               seeders=getattr(torrent, "seeders", 0))
                        continue
                    prepared[1]["reason"] = reason
                    fingerprint = prepared[1]["fingerprint"]
                    identity = prepared[1]["resource_identity"] or fingerprint
                    if fingerprint in downloaded_fingerprints or identity in known_identities:
                        duplicate_count += 1
                        _audit(audit, run_id, "去重", "跳过",
                               "发布标题已存在于插件记录或 MoviePilot 下载历史", result=prepared[1])
                        continue
                    existing = contexts.get(identity)
                    if existing:
                        duplicate_count += 1
                        if _candidate_priority_key(task, prepared[1], missing) \
                                > _candidate_priority_key(task, existing[1], missing):
                            contexts[identity] = prepared
                            _audit(audit, run_id, "去重", "替换",
                                   "同一发布标题重复，保留排序优先级更高的候选", result=prepared[1])
                        else:
                            _audit(audit, run_id, "去重", "跳过",
                                   "同一发布标题重复，已有候选优先级更高", result=prepared[1])
                        continue
                    contexts[identity] = prepared
                    _audit(audit, run_id, "筛选", "保留", reason, result=prepared[1])
        ordered = sorted(
            contexts.values(),
            key=lambda item: _candidate_priority_key(task, item[1], missing),
            reverse=True,
        )
        return ordered[:50], errors, duplicate_count

    def _download_candidates(self, task: Dict[str, Any],
                             candidates: List[Tuple[Context, Dict[str, Any]]],
                             stop_event: threading.Event,
                             known_identities: Set[str], audit: List[Dict[str, Any]],
                             run_id: str) -> Tuple[List[Dict[str, Any]], int]:
        """串行执行跨任务下载，避免两个手动任务竞态添加同一资源。"""
        with self.__class__._download_lock:
            known_identities.update(self._known_resource_identities())
            known_identities.difference_update(task.get("ignored_resource_identities") or [])
            return self._download_candidates_locked(
                task, candidates, stop_event, known_identities, audit, run_id
            )

    def _download_candidates_locked(self, task: Dict[str, Any],
                                    candidates: List[Tuple[Context, Dict[str, Any]]],
                                    stop_event: threading.Event,
                                    known_identities: Set[str], audit: List[Dict[str, Any]],
                                    run_id: str) -> Tuple[List[Dict[str, Any]], int]:
        task_id = str(task["id"])
        downloaded = parse_episodes(task.get("downloaded_episodes"))
        target = target_episodes(task)
        fingerprints = set(task.get("downloaded_fingerprints") or [])
        records = list(task.get("download_records") or [])
        downloads = []
        duplicate_count = 0
        unknown_downloaded = any(
            not parse_episodes(record.get("episodes")) for record in records
            if not record.get("duplicate")
        )
        for context, result in candidates:
            if len(downloads) >= self._max_downloads:
                _audit(audit, run_id, "下载", "停止", f"已达到单次下载上限 {self._max_downloads}")
                break
            if task.get("type") == MediaType.MOVIE.value and downloads:
                _audit(audit, run_id, "下载", "停止", "电影任务每轮只选择一个发布")
                break
            if stop_event.is_set() or global_vars.is_system_stopped:
                _audit(audit, run_id, "下载", "停止", "插件或系统已收到停止信号", level="warning")
                break
            current = self._load_tasks().get(task_id)
            if not current or not current.get("auto_download"):
                _audit(audit, run_id, "下载", "停止", "运行期间任务被删除或自动下载被关闭", level="warning")
                break
            fingerprint = result["fingerprint"]
            identity = result.get("resource_identity") or fingerprint
            if fingerprint in fingerprints or identity in known_identities:
                duplicate_count += 1
                result["skip_reason"] = "运行期间再次命中插件记录或下载历史"
                _audit(audit, run_id, "去重", "跳过", result["skip_reason"], result=result)
                continue
            candidate_episodes = set(result.get("episode_numbers") or [])
            selected: Optional[Set[int]] = None
            progress_episodes: Set[int] = set()
            if task.get("type") == MediaType.TV.value:
                missing_overlap = candidate_episodes.difference(downloaded)
                if target:
                    missing_overlap.intersection_update(target)
                if candidate_episodes and not missing_overlap:
                    result["skip_reason"] = "资源集数未覆盖当前缺集"
                    _audit(audit, run_id, "下载", "跳过", result["skip_reason"], result=result)
                    continue
                if not candidate_episodes and not task.get("accept_unknown_episode"):
                    result["skip_reason"] = "未解析出具体集数，且“允许未知集数下载”已关闭"
                    _audit(audit, run_id, "下载", "跳过", result["skip_reason"], result=result)
                    continue
                if not candidate_episodes:
                    if unknown_downloaded:
                        result["skip_reason"] = "已选择过未知集数资源"
                        duplicate_count += 1
                        _audit(audit, run_id, "下载", "跳过", result["skip_reason"], result=result)
                        continue
                    selected = None
                    result["selection_reason"] = "未知集数下载已开启，本任务尚未选择未知集数资源"
                elif parse_bool(task.get("prefer_full_pack"), True) and len(candidate_episodes) > 1:
                    # episodes=None 表示不做文件级选集，整包中的所有文件都交给下载器。
                    selected = None
                    progress_episodes = set(candidate_episodes)
                    result["download_scope"] = "full_pack"
                    result["selection_reason"] = (
                        f"整包 {episodes_text(candidate_episodes)} 覆盖缺集 "
                        f"{episodes_text(missing_overlap)}，按整包下载全部文件"
                    )
                else:
                    selected = missing_overlap
                    progress_episodes = set(missing_overlap)
                    result["download_scope"] = "selected_episodes"
                    result["selection_reason"] = f"下载缺集 {episodes_text(missing_overlap)}"
            _audit(audit, run_id, "下载", "选择",
                   result.get("selection_reason") or "候选满足下载条件", result=result)
            try:
                download_hash, error = DownloadChain().download_single(
                    context=context,
                    episodes=selected,
                    save_path=task.get("save_path") or None,
                    source=f"DirectSearchSubscribe|{task_id}",
                    downloader=task.get("downloader") or None,
                    username=self.plugin_name,
                    # MoviePilot 的下载管理只展示带系统 TORRENT_TAG 的任务；
                    # 自定义标签会替代下载模块的默认标签，因此必须显式同时传入。
                    label=_download_labels(),
                    return_detail=True,
                )
            except Exception as err:
                logger.warning(f"直搜订阅添加候选失败：{result.get('title')} - {err}")
                result["download_error"] = str(err)
                _audit(audit, run_id, "下载", "失败", str(err), level="error", result=result)
                continue
            if is_duplicate_download_message(error):
                result["duplicate"] = True
                result["skip_reason"] = error or "下载任务已存在"
                duplicate_count += 1
                fingerprints.add(fingerprint)
                known_identities.add(identity)
                downloaded.update(progress_episodes)
                if not candidate_episodes and task.get("type") == MediaType.TV.value:
                    unknown_downloaded = True
                _audit(audit, run_id, "下载", "重复",
                       result["skip_reason"], level="warning", result=result)
                self._update_runtime(
                    task_id,
                    downloaded_episodes=sorted(downloaded),
                    downloaded_fingerprints=list(fingerprints)[-MAX_RESOURCE_HISTORY:],
                    last_results=[item[1] for item in candidates],
                )
                continue
            if not download_hash:
                result["download_error"] = error or "添加下载失败"
                _audit(audit, run_id, "下载", "失败", result["download_error"],
                       level="error", result=result)
                continue
            downloaded.update(progress_episodes)
            fingerprints.add(fingerprint)
            known_identities.add(identity)
            normalized_hash = str(download_hash).strip().casefold()
            ignored_hashes = {
                str(item).strip().casefold() for item in current.get("ignored_history_hashes") or []
            }
            ignored_hashes.discard(normalized_hash)
            ignored_identities = set(current.get("ignored_resource_identities") or [])
            ignored_identities.discard(identity)
            record = {
                "time": now_text(), "fingerprint": fingerprint, "hash": download_hash,
                "site": result.get("site"), "title": result.get("title"),
                "episodes": episodes_text(progress_episodes), "size": result.get("size") or 0,
                "resource_identity": identity, "transfer_status": "waiting",
                "download_scope": result.get("download_scope") or "all",
                "selection_reason": result.get("selection_reason") or "",
            }
            records.append(record)
            if not candidate_episodes and task.get("type") == MediaType.TV.value:
                unknown_downloaded = True
            result["downloaded"] = True
            downloads.append(record)
            _audit(audit, run_id, "下载", "成功",
                   f"已加入下载器；{result.get('selection_reason') or '下载全部内容'}；"
                   f"Hash {normalized_hash[:12]}", result=result)
            self._update_runtime(
                task_id,
                downloaded_episodes=sorted(downloaded),
                downloaded_fingerprints=list(fingerprints)[-MAX_RESOURCE_HISTORY:],
                download_records=records[-MAX_RESOURCE_HISTORY:],
                ignored_history_hashes=sorted(ignored_hashes)[-MAX_RESOURCE_HISTORY:],
                ignored_resource_identities=sorted(ignored_identities)[-MAX_RESOURCE_HISTORY:],
                last_transfer_status="waiting",
                last_transfer_message="等待下载完成",
                last_results=[item[1] for item in candidates],
            )
        self._update_runtime(task_id, last_results=[item[1] for item in candidates])
        return downloads, duplicate_count

    def _cleanup_task(self, task: Dict[str, Any], delete_files: bool) -> Dict[str, Any]:
        """只清理当前插件任务关联的下载器项目，然后重置任务供重新处理。"""
        task_id = str(task.get("id") or "")
        record_hashes = {
            str(record.get("hash") or "").strip().casefold()
            for record in task.get("download_records") or []
            if str(record.get("hash") or "").strip()
        }
        history_hashes = self._matching_plugin_history_hashes(task)
        hashes = sorted(record_hashes.union(history_hashes))
        ignored_identities = set(task.get("ignored_resource_identities") or [])
        ignored_identities.update(
            str(record.get("resource_identity") or "") or resource_identity(record.get("title"))
            for record in task.get("download_records") or []
        )
        try:
            for history_hash in history_hashes:
                history = DownloadHistoryOper().get_by_hash(history_hash)
                if history:
                    ignored_identities.add(resource_identity(getattr(history, "torrent_name", "")))
        except Exception as err:
            logger.warning(f"直搜订阅读取待清理发布标识失败：{err}")
        ignored_identities.discard("")
        removed = 0
        remove_error = ""
        if hashes:
            try:
                state = DownloadChain().remove_torrents(
                    hashs=hashes,
                    delete_file=delete_files,
                    downloader=task.get("downloader") or None,
                )
                if state:
                    removed = len(hashes)
                else:
                    remove_error = "下载器未确认删除；项目可能已不存在或下载器当前不可用"
            except Exception as err:
                remove_error = str(err)

        ignored_hashes = set(task.get("ignored_history_hashes") or [])
        ignored_hashes.update(hashes)
        scope = "下载任务和下载文件" if delete_files else "下载任务，下载文件已保留"
        reason = f"已清理 {removed}/{len(hashes)} 个关联 Hash（{scope}），任务进度已重置"
        if not hashes:
            reason = "未找到仍可定位的插件下载 Hash；任务进度已重置"
        if remove_error:
            reason += f"；下载器提示：{remove_error}"
        with self.__class__._data_lock:
            tasks = self._load_tasks()
            latest = tasks.get(task_id)
            if not latest:
                return {"success": False, "message": "任务不存在"}
            latest["downloaded_episodes"] = sorted(parse_episodes(latest.get("owned_episodes")))
            latest["downloaded_fingerprints"] = []
            latest["download_records"] = []
            latest["ignored_history_hashes"] = sorted(ignored_hashes)[-MAX_RESOURCE_HISTORY:]
            latest["ignored_resource_identities"] = sorted(ignored_identities)[-MAX_RESOURCE_HISTORY:]
            latest["last_results"] = []
            latest["last_download_count"] = 0
            latest["last_duplicate_count"] = 0
            latest["last_transfer_status"] = ""
            latest["last_transfer_message"] = ""
            latest["last_transfer_at"] = ""
            latest["cleanup_pending"] = {}
            latest["status"] = "active" if latest.get("enabled") else "paused"
            latest["last_status"] = "warning" if remove_error else "success"
            latest["last_message"] = reason
            latest["last_reason_summary"] = reason
            latest["run_logs"] = [_audit_entry(
                "清理", "已执行", reason, level="warning" if remove_error else "info"
            ), *(latest.get("run_logs") or [])][:MAX_TASK_LOGS]
            latest["updated_at"] = now_text()
            tasks[task_id] = latest
            self._save_tasks(tasks)
        logger.info(f"直搜订阅 {task.get('name')} 清理：{reason}")
        return {"success": True, "message": reason, "task": latest}

    @staticmethod
    def _matching_plugin_history_hashes(task: Dict[str, Any]) -> Set[str]:
        """查找同节目且明确由本插件创建的历史 Hash，兼容任务删除后重建。"""
        hashes: Set[str] = set()
        try:
            histories = DownloadHistoryOper().list_by_page(page=1, count=5000) or []
        except Exception as err:
            logger.warning(f"直搜订阅读取待清理历史失败：{err}")
            return hashes
        for history in histories:
            note = getattr(history, "note", None)
            source = str(note.get("source") or "") if isinstance(note, dict) else ""
            if not source.startswith("DirectSearchSubscribe|"):
                continue
            if not _history_matches_task(task, history):
                continue
            download_hash = str(getattr(history, "download_hash", "") or "").strip().casefold()
            if download_hash:
                hashes.add(download_hash)
        return hashes

    @classmethod
    def _claim_task(cls, task_id: str) -> bool:
        with cls._running_lock:
            if task_id in cls._running_ids:
                return False
            cls._running_ids.add(task_id)
            return True

    @classmethod
    def _release_task(cls, task_id: str):
        with cls._running_lock:
            cls._running_ids.discard(task_id)

    def _load_tasks(self) -> Dict[str, Dict[str, Any]]:
        with self.__class__._data_lock:
            data = self.get_data(TASKS_KEY) or {}
            if isinstance(data, list):
                data = {str(item.get("id")): item for item in data
                        if isinstance(item, dict) and item.get("id")}
            return copy.deepcopy(data) if isinstance(data, dict) else {}

    def _save_tasks(self, tasks: Dict[str, Dict[str, Any]]):
        with self.__class__._data_lock:
            self.save_data(TASKS_KEY, copy.deepcopy(tasks))

    def _load_trash(self) -> Dict[str, Dict[str, Any]]:
        with self.__class__._data_lock:
            data = self.get_data(TRASH_KEY) or {}
            return copy.deepcopy(data) if isinstance(data, dict) else {}

    def _save_trash(self, trash: Dict[str, Dict[str, Any]]):
        with self.__class__._data_lock:
            self.save_data(TRASH_KEY, copy.deepcopy(trash))

    def _update_runtime(self, task_id: str, **values):
        with self.__class__._data_lock:
            tasks = self._load_tasks()
            task = tasks.get(task_id)
            if not task:
                return
            task.update(values)
            task["updated_at"] = now_text()
            tasks[task_id] = task
            self._save_tasks(tasks)

    def _save_run_logs(self, task_id: str, entries: List[Dict[str, Any]]):
        if not entries:
            return
        with self.__class__._data_lock:
            tasks = self._load_tasks()
            task = tasks.get(task_id)
            if not task:
                return
            task["run_logs"] = [*reversed(entries), *(task.get("run_logs") or [])][:MAX_TASK_LOGS]
            task["updated_at"] = now_text()
            tasks[task_id] = task
            self._save_tasks(tasks)

    def _update_transfer_record(self, download_hash: str, status: str,
                                message: str = "", target: str = ""):
        """按下载 Hash 更新活动任务或回收站中的整理状态。"""
        normalized_hash = str(download_hash or "").strip().casefold()
        if not normalized_hash:
            return
        with self.__class__._data_lock:
            for loader, saver in (
                    (self._load_tasks, self._save_tasks),
                    (self._load_trash, self._save_trash),
            ):
                tasks = loader()
                changed = False
                for task_id, task in tasks.items():
                    records = list(task.get("download_records") or [])
                    for record in records:
                        if str(record.get("hash") or "").strip().casefold() != normalized_hash:
                            continue
                        # 调度器可能在事件完成后再次扫描；不能把 completed 降级回 queued。
                        if record.get("transfer_status") == "completed" and status == "queued":
                            return
                        previous_status = str(record.get("transfer_status") or "")
                        record["transfer_status"] = status
                        record["transfer_message"] = message
                        record["transfer_updated_at"] = now_text()
                        if target:
                            record["transfer_target"] = target
                        task["download_records"] = records
                        task["last_transfer_status"] = status
                        task["last_transfer_message"] = message
                        task["last_transfer_at"] = now_text()
                        if previous_status != status:
                            action = {
                                "waiting": "等待", "queued": "入队", "completed": "完成",
                                "failed": "失败",
                            }.get(status, status or "更新")
                            task["run_logs"] = [_audit_entry(
                                "整理", action, message or f"整理状态更新为 {status}",
                                level="error" if status == "failed" else "info",
                                title=record.get("title"), site=record.get("site"),
                                episodes=record.get("episodes"),
                            ), *(task.get("run_logs") or [])][:MAX_TASK_LOGS]
                        task["updated_at"] = now_text()
                        tasks[task_id] = task
                        changed = True
                        break
                    if changed:
                        break
                if changed:
                    saver(tasks)
                    return

    def _start_failed_transfer_retry(self, legacy_only: bool) -> bool:
        """后台重试本插件下载产生的失败整理记录。"""
        if not self.__class__._transfer_retry_lock.acquire(blocking=False):
            return False

        def runner():
            try:
                count = self._retry_failed_transfers(legacy_only=legacy_only)
                if count:
                    logger.info(f"直搜订阅已重新提交 {count} 个失败整理文件")
            except Exception as err:
                logger.error(f"直搜订阅重试失败整理异常：{err}", exc_info=True)
            finally:
                self.__class__._transfer_retry_lock.release()

        threading.Thread(
            target=runner, name="direct-search-transfer-retry", daemon=True
        ).start()
        return True

    def _retry_failed_transfers(self, legacy_only: bool = False) -> int:
        """按插件下载记录定位失败历史，并重新送入已接管的转移链。"""
        submitted = 0
        seen_sources: Set[Tuple[str, str]] = set()
        tasks = [*self._load_tasks().values(), *self._load_trash().values()]
        for task in tasks:
            if self._stop_event.is_set() or global_vars.is_system_stopped:
                break
            for record in task.get("download_records") or []:
                transfer_status = str(record.get("transfer_status") or "")
                if transfer_status in {"completed", "queued", "waiting"}:
                    continue
                if legacy_only and transfer_status:
                    continue
                download_hash = str(record.get("hash") or "").strip()
                if not download_hash:
                    continue
                try:
                    histories = TransferHistoryOper().list_by_hash(download_hash) or []
                except Exception as err:
                    logger.warning(f"直搜订阅读取失败整理历史异常：{download_hash[:12]} - {err}")
                    continue
                for history in histories:
                    if bool(getattr(history, "status", False)) or not getattr(history, "src_fileitem", None):
                        continue
                    source_key = (download_hash.casefold(), str(getattr(history, "src", "") or ""))
                    if source_key in seen_sources:
                        continue
                    seen_sources.add(source_key)
                    try:
                        state, message = TransferChain().do_transfer(
                            fileitem=schemas.FileItem(**history.src_fileitem),
                            downloader=getattr(history, "downloader", None),
                            download_hash=download_hash,
                            force=True,
                            scrape=False,
                            background=True,
                        )
                    except Exception as err:
                        state, message = False, str(err)
                    if state:
                        submitted += 1
                    else:
                        self._update_transfer_record(
                            download_hash, "failed", message or "重新提交整理失败"
                        )
        return submitted

    def _known_resource_identities(self) -> Set[str]:
        """汇总活动任务和回收站记录，任务删除重建后仍然可以去重。"""
        identities: Set[str] = set()
        stored_tasks = [*self._load_tasks().values(), *self._load_trash().values()]
        for task in stored_tasks:
            for record in task.get("download_records") or []:
                identity = str(record.get("resource_identity") or "") \
                    or resource_identity(record.get("title"))
                if identity:
                    identities.add(identity)
        return identities

    @staticmethod
    def _history_snapshot(task: Dict[str, Any]) -> Tuple[Set[str], Set[int], bool]:
        """读取 MoviePilot 下载历史中的资源标识和本任务已有集数。"""
        identities: Set[str] = set()
        episodes: Set[int] = set()
        matched_movie = False
        try:
            histories = DownloadHistoryOper().list_by_page(page=1, count=5000) or []
        except Exception as err:
            logger.warning(f"直搜订阅读取下载历史失败，继续仅按插件记录去重：{err}")
            return identities, episodes, matched_movie

        strict_task = dict(task)
        strict_task["strict_title_match"] = True
        task_season = parse_int(task.get("season"), minimum=1)
        ignored_hashes = {
            str(item or "").strip().casefold()
            for item in task.get("ignored_history_hashes") or []
        }
        for history in histories:
            history_hash = str(getattr(history, "download_hash", "") or "").strip().casefold()
            if history_hash and history_hash in ignored_hashes:
                continue
            torrent_title = str(getattr(history, "torrent_name", "") or "").strip()
            identity = resource_identity(torrent_title)
            if identity:
                identities.add(identity)
            if not torrent_title:
                continue
            description = " ".join(filter(None, (
                str(getattr(history, "torrent_description", "") or ""),
                str(getattr(history, "title", "") or ""),
            )))
            if not title_matches(strict_task, torrent_title, description):
                continue
            history_type = str(getattr(history, "type", "") or "")
            if history_type and history_type != task.get("type"):
                continue
            if task.get("type") == MediaType.MOVIE.value:
                matched_movie = True
                continue
            torrent_meta = MetaInfo(title=torrent_title, subtitle=description)
            stored_meta = MetaInfo(
                title=f"{getattr(history, 'seasons', '') or ''}"
                      f"{getattr(history, 'episodes', '') or ''}"
            )
            parsed_season = torrent_meta.begin_season or stored_meta.begin_season
            if task_season and parsed_season and task_season != parsed_season:
                continue
            episodes.update(torrent_meta.episode_list or [])
            episodes.update(stored_meta.episode_list or [])
            episodes.update(parse_episodes(getattr(history, "episodes", None)))
            episodes.update(extract_episode_numbers(torrent_title))
            episodes.update(extract_episode_numbers(
                f"{getattr(history, 'seasons', '') or ''} "
                f"{getattr(history, 'episodes', '') or ''}"
            ))
        return identities, episodes, matched_movie

    def _repair_download_tags(self):
        """为旧版插件已添加的下载任务补充 MoviePilot 系统标签。"""
        system_tag = str(settings.TORRENT_TAG or "").strip()
        if not system_tag:
            return
        for task in self._load_tasks().values():
            hashes = list(dict.fromkeys(
                str(record.get("hash") or "").strip()
                for record in task.get("download_records") or []
                if record.get("hash")
            ))
            if not hashes:
                continue
            try:
                DownloadChain().set_torrents_tag(
                    hashs=hashes,
                    tags=[system_tag],
                    downloader=task.get("downloader") or None,
                )
                logger.info(f"直搜订阅已为 {len(hashes)} 个历史下载补充系统标签：{system_tag}")
            except Exception as err:
                logger.warning(f"直搜订阅补充历史下载系统标签失败：{err}")


def _download_history_for_transfer(download_hash: Optional[str], fileitem: Any = None) -> Optional[Any]:
    """定位 MoviePilot 下载历史，仅用于确认下载是否来自本插件。"""
    downloadhis = DownloadHistoryOper()
    if download_hash:
        history = downloadhis.get_by_hash(str(download_hash))
        if history:
            return history
    file_path = str(getattr(fileitem, "path", "") or "")
    if not file_path:
        return None
    try:
        download_file = downloadhis.get_file_by_fullpath(Path(file_path).as_posix())
    except Exception:
        download_file = None
    if download_file and getattr(download_file, "download_hash", None):
        return downloadhis.get_by_hash(download_file.download_hash)
    return None


def _task_from_download_history(history: Any, task_id: str) -> Dict[str, Any]:
    """插件记录丢失时，从本插件写入的下载历史恢复最小手工整理上下文。"""
    season_raw = getattr(history, "seasons", None)
    season_meta = MetaInfo(str(season_raw or ""))
    season = parse_int(season_raw, minimum=1) or season_meta.begin_season
    downloaded = parse_episodes(getattr(history, "episodes", None))
    return {
        "id": task_id,
        "name": str(getattr(history, "title", "") or getattr(history, "torrent_name", "") or "未命名"),
        "type": str(getattr(history, "type", "") or MediaType.TV.value),
        "year": str(getattr(history, "year", "") or ""),
        "season": season,
        "episodes": episodes_text(downloaded),
        "start_episode": min(downloaded) if downloaded else 1,
        "total_episode": max(downloaded) if downloaded else None,
        "media_category": str(getattr(history, "media_category", "") or ""),
        "aliases": [],
        "download_records": [{
            "hash": str(getattr(history, "download_hash", "") or ""),
            "episodes": episodes_text(downloaded),
        }],
    }


def _direct_transfer_task(download_hash: Optional[str], fileitem: Any = None) -> Optional[Dict[str, Any]]:
    """仅为明确由 DirectSearchSubscribe 创建的下载返回任务上下文。"""
    plugin = directsearchsubscribe._instance
    if not plugin:
        return None
    normalized_hash = str(download_hash or "").strip().casefold()
    stored_tasks = [*plugin._load_tasks().values(), *plugin._load_trash().values()]
    if normalized_hash:
        for task in stored_tasks:
            if normalized_hash in {
                    str(item or "").strip().casefold()
                    for item in task.get("ignored_history_hashes") or []
            }:
                continue
            if any(
                    str(record.get("hash") or "").strip().casefold() == normalized_hash
                    for record in task.get("download_records") or []
            ):
                return task
        if any(
                normalized_hash in {
                    str(item or "").strip().casefold()
                    for item in task.get("ignored_history_hashes") or []
                }
                for task in stored_tasks
        ):
            return None

    history = _download_history_for_transfer(download_hash, fileitem)
    note = getattr(history, "note", None) if history else None
    source = str(note.get("source") or "") if isinstance(note, dict) else ""
    prefix = "DirectSearchSubscribe|"
    if not source.startswith(prefix):
        return None
    task_id = source[len(prefix):].strip()
    for task in stored_tasks:
        if str(task.get("id") or "") == task_id:
            if normalized_hash and normalized_hash in {
                    str(item or "").strip().casefold()
                    for item in task.get("ignored_history_hashes") or []
            }:
                return None
            return task
    return _task_from_download_history(history, task_id)


def _call_arg(args: List[Any], kwargs: Dict[str, Any], name: str, index: int) -> Any:
    return args[index] if len(args) > index else kwargs.get(name)


def _set_call_arg(args: List[Any], kwargs: Dict[str, Any], name: str, index: int, value: Any):
    if len(args) > index:
        args[index] = value
    else:
        kwargs[name] = value


def _record_episodes(task: Dict[str, Any], download_hash: Optional[str]) -> Set[int]:
    normalized_hash = str(download_hash or "").strip().casefold()
    for record in task.get("download_records") or []:
        if str(record.get("hash") or "").strip().casefold() == normalized_hash:
            return parse_episodes(record.get("episodes"))
    return set()


def _manual_episode_infos(task: Dict[str, Any], download_hash: Optional[str], meta: Any) -> List[Any]:
    """生成本地剧集占位信息，使转移链无需请求 TMDB 季集接口。"""
    season = parse_int(task.get("season"), 1, minimum=1) or 1
    episodes = target_episodes(task) or _record_episodes(task, download_hash)
    if not episodes and getattr(meta, "begin_episode", None) is not None:
        begin = int(meta.begin_episode)
        end = int(getattr(meta, "end_episode", None) or begin)
        episodes = set(range(begin, end + 1))
    # TransferChain 以 truthy 判断是否需要请求 TMDB；0 只作本地哨兵，不参与命名匹配。
    episode_numbers = sorted(episodes) or [0]
    return [schemas.TmdbEpisode(season_number=season, episode_number=episode)
            for episode in episode_numbers]


def _prepare_manual_transfer_task(transfer_task: Any, direct_task: Dict[str, Any]):
    """把人工任务信息写入单个转移任务，同时保留文件名解析出的集数和技术参数。"""
    transfer_task.mediainfo = _manual_media_info(direct_task)
    transfer_task.scrape = False
    meta = transfer_task.meta
    if meta:
        meta.name = str(direct_task.get("name") or meta.name or "")
        meta.type = MediaType(direct_task.get("type") or MediaType.TV.value)
        if direct_task.get("year"):
            meta.year = str(direct_task.get("year"))
        season = parse_int(direct_task.get("season"), minimum=1)
        if season is not None:
            meta.begin_season = season
        selected = _record_episodes(direct_task, transfer_task.download_hash)
        if meta.type == MediaType.TV and meta.begin_episode is None and len(selected) == 1:
            meta.begin_episode = next(iter(selected))
    if transfer_task.mediainfo.type == MediaType.TV:
        transfer_task.episodes_info = _manual_episode_infos(
            direct_task, transfer_task.download_hash, meta
        )


def _patched_transfer_do_transfer(self: TransferChain, *args, **kwargs):
    """在 MoviePilot 建立转移任务前为本插件下载注入手工媒体上下文。"""
    args_list = list(args)
    fileitem = _call_arg(args_list, kwargs, "fileitem", 0)
    download_hash = _call_arg(args_list, kwargs, "download_hash", 14)
    direct_task = _direct_transfer_task(download_hash, fileitem)
    original = directsearchsubscribe._transfer_originals["do_transfer"]
    if not direct_task:
        return original(self, *args_list, **kwargs)

    _set_call_arg(args_list, kwargs, "mediainfo", 2, _manual_media_info(direct_task))
    _set_call_arg(args_list, kwargs, "scrape", 7, False)
    season = parse_int(direct_task.get("season"), minimum=1)
    if season is not None:
        _set_call_arg(args_list, kwargs, "season", 10, season)
    result = original(self, *args_list, **kwargs)
    plugin = directsearchsubscribe._instance
    if plugin and download_hash:
        state = bool(result[0]) if isinstance(result, tuple) and result else bool(result)
        message = str(result[1] or "") if isinstance(result, tuple) and len(result) > 1 else ""
        plugin._update_transfer_record(
            str(download_hash), "queued" if state else "failed",
            message or ("已加入整理队列" if state else "加入整理队列失败"),
        )
    logger.info(
        f"直搜订阅使用手工媒体信息整理：{direct_task.get('name')} "
        f"{getattr(fileitem, 'name', '')}"
    )
    return result


def _patched_transfer_handle(self: TransferChain, task: Any, callback: Any = None):
    """在实际整理前补齐本地剧集信息，跳过 TMDB 季集查询和元数据刮削。"""
    direct_task = _direct_transfer_task(task.download_hash, task.fileitem)
    original = directsearchsubscribe._transfer_originals["handle_transfer"]
    if not direct_task:
        return original(self, task, callback)
    _prepare_manual_transfer_task(task, direct_task)
    directsearchsubscribe._transfer_context.active = True
    try:
        return original(self, task, callback)
    finally:
        directsearchsubscribe._transfer_context.active = False


def _patched_job_media_id(media: MediaInfo = None, season: Optional[int] = None) -> Tuple[Any, Optional[int]]:
    """无外部 ID 的手工节目使用稳定插件标识，避免不同节目共用同一整理作业。"""
    if media and getattr(media, "source", None) == PLUGIN_ID:
        identity = resource_identity(
            f"{media.type.value if media.type else ''}|{media.title}|{media.year or ''}"
        )
        return f"{PLUGIN_ID}:{identity}", season
    descriptor = directsearchsubscribe._transfer_originals["job_media_id"]
    original = descriptor.__func__ if isinstance(descriptor, staticmethod) else descriptor
    return original(media, season)


def _patched_transfer_history_media(self: TransferHistoryOper, mtype: Optional[str] = None,
                                    tmdbid: Optional[int] = None) -> Any:
    """手工整理没有 TMDB ID，不允许 NULL 查询误用另一部手工节目的历史标题。"""
    if getattr(directsearchsubscribe._transfer_context, "active", False) and tmdbid is None:
        return None
    original = directsearchsubscribe._transfer_originals["history_media"]
    return original(self, mtype=mtype, tmdbid=tmdbid)


def _audit_entry(stage: str, action: str, reason: str, level: str = "info",
                 run_id: str = "", result: Optional[Dict[str, Any]] = None,
                 title: Any = "", site: Any = "", episodes: Any = "",
                 seeders: Any = None) -> Dict[str, Any]:
    """构造不包含下载地址、Cookie 或 Passkey 的任务审计日志。"""
    result = result or {}
    return {
        "time": now_text(),
        "run_id": str(run_id or ""),
        "stage": str(stage or ""),
        "action": str(action or ""),
        "level": str(level or "info"),
        "reason": _safe_log_text(reason),
        "title": str(title or result.get("title") or ""),
        "site": str(site or result.get("site") or ""),
        "episodes": str(episodes or result.get("episodes") or ""),
        "seeders": parse_int(
            seeders if seeders is not None else result.get("seeders"), 0, minimum=0
        ) or 0,
    }


def _safe_log_text(value: Any) -> str:
    """隐藏日志中可能由异常文本带出的下载链接查询参数。"""
    text = str(value or "")
    return re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[参数已隐藏]", text)


def _audit(entries: List[Dict[str, Any]], run_id: str, stage: str, action: str,
           reason: str, level: str = "info", result: Optional[Dict[str, Any]] = None,
           **fields):
    """追加单轮详细日志；限制单轮条数，避免大结果集挤爆插件数据。"""
    max_run_entries = 300
    if len(entries) >= max_run_entries:
        if len(entries) == max_run_entries:
            entries.append(_audit_entry(
                "日志", "截断", f"本轮详细日志超过 {max_run_entries} 条，后续明细已省略",
                level="warning", run_id=run_id,
            ))
        return
    entries.append(_audit_entry(
        stage, action, reason, level=level, run_id=run_id, result=result, **fields
    ))


def _reason_summary(results: List[Dict[str, Any]], downloads: List[Dict[str, Any]],
                    duplicate_count: int, search_errors: List[str]) -> str:
    if downloads:
        pack_count = sum(1 for item in downloads if item.get("download_scope") == "full_pack")
        return f"已加入 {len(downloads)} 个下载" + (f"，其中整包 {pack_count} 个" if pack_count else "")
    reasons: Dict[str, int] = {}
    for result in results:
        reason = str(result.get("download_error") or result.get("skip_reason") or "").strip()
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
    if reasons:
        reason, count = sorted(reasons.items(), key=lambda item: item[1], reverse=True)[0]
        return f"未新增下载：{reason}" + (f"（{count} 个候选）" if count > 1 else "")
    if duplicate_count:
        return f"未新增下载：{duplicate_count} 个资源被历史或任务记录去重"
    if search_errors:
        return f"未新增下载：搜索发生 {len(search_errors)} 个异常且没有可用候选"
    return "未新增下载：没有通过标题、集数、关键词和做种条件的候选"


def _cleanup_pending_active(pending: Dict[str, Any]) -> bool:
    try:
        expires_at = datetime.strptime(str(pending.get("expires_at") or ""), "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return False
    return datetime.now() <= expires_at


def _history_matches_task(task: Dict[str, Any], history: Any) -> bool:
    torrent_title = str(getattr(history, "torrent_name", "") or "").strip()
    if not torrent_title:
        return False
    description = " ".join(filter(None, (
        str(getattr(history, "torrent_description", "") or ""),
        str(getattr(history, "title", "") or ""),
    )))
    strict_task = dict(task)
    strict_task["strict_title_match"] = True
    if not title_matches(strict_task, torrent_title, description):
        return False
    history_type = str(getattr(history, "type", "") or "")
    if history_type and history_type != task.get("type"):
        return False
    if task.get("type") == MediaType.MOVIE.value:
        task_year = str(task.get("year") or "").strip()
        history_year = str(getattr(history, "year", "") or "").strip()
        return not task_year or not history_year or task_year == history_year
    task_season = parse_int(task.get("season"), minimum=1)
    torrent_meta = MetaInfo(title=torrent_title, subtitle=description)
    stored_meta = MetaInfo(
        title=f"{getattr(history, 'seasons', '') or ''}{getattr(history, 'episodes', '') or ''}"
    )
    parsed_season = torrent_meta.begin_season or stored_meta.begin_season
    if task_season and parsed_season and task_season != parsed_season:
        return False
    task_year = str(task.get("year") or "").strip()
    history_year = str(getattr(history, "year", "") or torrent_meta.year or "").strip()
    return not task_year or not history_year or task_year == history_year


def _task_payload_from_config(config: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": config.get("title"), "type": config.get("type"), "year": config.get("year"),
        "season": config.get("season"), "start_episode": config.get("start_episode"),
        "total_episode": config.get("total_episode"), "episodes": config.get("episodes"),
        "owned_episodes": config.get("owned_episodes"),
        "keywords": config.get("keywords") or config.get("keyword"), "aliases": config.get("aliases"),
        "include": config.get("include"), "exclude": config.get("exclude"), "sites": config.get("sites"),
        "search_pages": config.get("search_pages"), "downloader": config.get("downloader"),
        "priority_mode": config.get("priority_mode"), "min_seeders": config.get("min_seeders"),
        "dedupe_history": config.get("dedupe_history"),
        "prefer_full_pack": config.get("prefer_full_pack"),
        "save_path": config.get("save_path"), "media_category": config.get("media_category"),
        "enabled": config.get("task_enabled"), "auto_download": config.get("auto_download"),
        "strict_title_match": config.get("strict_title_match"),
        "accept_unknown_episode": config.get("accept_unknown_episode"),
    }


def _prepare_candidate(task: Dict[str, Any], context: Context, missing: Set[int],
                       downloaded: Set[int]) \
        -> Tuple[Optional[Tuple[Context, Dict[str, Any]]], str]:
    torrent = context.torrent_info
    if not torrent or not torrent.title:
        return None, "资源缺少标题"
    if not title_matches(task, torrent.title, torrent.description or ""):
        return None, "标题未命中节目名称、别名或搜索词"
    filter_reason = word_filter_reason(task, torrent.title, torrent.description or "")
    if filter_reason:
        return None, filter_reason
    media_type = MediaType(task.get("type") or MediaType.TV.value)
    meta = MetaInfo(title=torrent.title, subtitle=torrent.description)
    meta.type = media_type
    task_year = str(task.get("year") or "").strip()
    parsed_year = str(getattr(meta, "year", None) or "").strip()
    if task_year and parsed_year and task_year != parsed_year:
        return None, f"年份不符：任务 {task_year}，资源 {parsed_year}"
    if media_type == MediaType.TV:
        task_season = parse_int(task.get("season"), minimum=1)
        parsed_season = meta.begin_season
        if task_season and parsed_season and task_season != parsed_season:
            return None, f"季号不符：任务 S{task_season:02d}，资源 S{parsed_season:02d}"
        if task_season and not parsed_season:
            meta.begin_season = task_season
        elif not meta.begin_season:
            meta.begin_season = 1
    episodes = set(meta.episode_list or [])
    episodes.update(extract_episode_numbers(f"{torrent.title} {torrent.description or ''}"))
    target = target_episodes(task)
    if media_type == MediaType.TV and episodes:
        needed = missing if target else episodes.difference(downloaded)
        if not episodes.intersection(needed):
            return None, (
                f"资源集数 {episodes_text(episodes)} 未覆盖当前缺集 "
                f"{episodes_text(needed) or '-'}"
            )
    seeders = int(torrent.seeders or 0)
    min_seeders = parse_int(task.get("min_seeders"), 0, minimum=0) or 0
    if seeders < min_seeders:
        return None, f"做种数 {seeders} 低于最低要求 {min_seeders}"
    fingerprint = resource_fingerprint(torrent.site, torrent.enclosure, torrent.page_url,
                                       torrent.title, torrent.size)
    identity = resource_identity(torrent.title)
    context.meta_info = meta
    context.media_info = _manual_media_info(task)
    context.media_info.season = meta.begin_season
    context.resource_source = "direct_search_subscribe"
    context.match_source = "plugin"
    context.candidate_recognized = False
    context.media_info_is_target = True
    score = candidate_score(episodes, missing, seeders,
                            torrent.downloadvolumefactor, int(torrent.size or 0))
    if media_type == MediaType.MOVIE:
        accepted_reason = f"电影候选通过；做种 {seeders}"
    elif episodes:
        overlap = episodes.intersection(missing) if target else episodes.difference(downloaded)
        pack = "整包" if len(episodes) > 1 else "单集"
        accepted_reason = (
            f"{pack} {episodes_text(episodes)} 覆盖缺集 {episodes_text(overlap)}；做种 {seeders}"
        )
    else:
        accepted_reason = f"未解析出具体集数；做种 {seeders}"
    return (context, {
        "fingerprint": fingerprint, "resource_identity": identity, "site_id": torrent.site,
        "site": torrent.site_name or str(torrent.site or ""), "title": torrent.title,
        "size": int(torrent.size or 0), "seeders": seeders,
        "free": torrent.downloadvolumefactor == 0,
        "pubdate": torrent.pubdate or "",
        "season": meta.begin_season, "episodes": episodes_text(episodes),
        "episode_numbers": sorted(episodes),
        "downloadable": bool(episodes) or media_type == MediaType.MOVIE
                        or parse_bool(task.get("accept_unknown_episode"), False),
        "downloaded": False, "score": score,
    }), accepted_reason


def _candidate_priority_key(task: Dict[str, Any], result: Dict[str, Any],
                            missing: Set[int]) -> Tuple[int, ...]:
    return candidate_sort_key(
        priority_mode=task.get("priority_mode"),
        episodes=set(result.get("episode_numbers") or []),
        missing=missing,
        seeders=int(result.get("seeders") or 0),
        free_factor=0 if result.get("free") else 1,
        size=int(result.get("size") or 0),
        pubdate=result.get("pubdate"),
        prefer_full_pack=parse_bool(task.get("prefer_full_pack"), True),
    )


def _manual_media_info(task: Dict[str, Any]) -> MediaInfo:
    """构造完整的手工媒体上下文，显式阻止下载链再次调用媒体识别。"""
    media = MediaInfo()
    media.source = PLUGIN_ID
    media.type = MediaType(task.get("type") or MediaType.TV.value)
    media.title = str(task.get("name") or "")
    media.year = str(task.get("year") or "") or None
    media.season = parse_int(task.get("season"), minimum=1)
    media.number_of_episodes = parse_int(task.get("total_episode"), minimum=1)
    media.category = str(task.get("media_category") or "")
    media.names = [str(item) for item in task.get("aliases") or []]
    if media.type == MediaType.TV and media.season is not None:
        media.seasons[media.season] = sorted(target_episodes(task))
    # MoviePilot 会把空 genre_ids 视为媒体信息不完整并调用识别器。
    # 自定义来源没有外部 ID，用专用哨兵声明手工信息已经完整。
    media.genre_ids = [-1]
    return media


def _same_identity(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    return str(left.get("name") or "").casefold() == str(right.get("name") or "").casefold() \
        and left.get("type") == right.get("type") \
        and parse_int(left.get("season")) == parse_int(right.get("season"))


def _response(result: Dict[str, Any]) -> schemas.Response:
    return schemas.Response(success=bool(result.get("success")), message=result.get("message"),
                            data=result.get("task") or result)


def _download_labels() -> str:
    """组合 MoviePilot 系统标签和插件标签，并保持顺序去重。"""
    labels = []
    for raw in (settings.TORRENT_TAG, "直搜订阅"):
        for label in str(raw or "").split(","):
            label = label.strip()
            if label and label not in labels:
                labels.append(label)
    return ",".join(labels)


def _api(path: str, endpoint: Any, methods: List[str], summary: str) -> Dict[str, Any]:
    return {"path": path, "endpoint": endpoint, "methods": methods, "auth": "bear",
            "summary": summary, "description": summary}


def _active_site_options() -> List[Dict[str, Any]]:
    try:
        return [{"title": f"{site.name} ({site.id})", "value": site.id}
                for site in SiteOper().list_active()]
    except Exception as err:
        logger.warning(f"直搜订阅读取站点列表失败：{err}")
        return []


def _hero(plugin: directsearchsubscribe, total: int, active: int, auto: int) -> Dict[str, Any]:
    state = "运行中" if plugin._enabled else "已停用"
    return {
        "component": "VCard", "props": {"variant": "tonal", "color": "primary", "class": "mb-4"},
        "content": [
            {"component": "VCardTitle", "text": f"直搜订阅 {plugin.plugin_version} · {state}"},
            {"component": "VCardSubtitle", "text": "插件独立维护 · 站点直搜 · 不使用外部媒体信息源"},
            {"component": "VCardText", "content": [
                {"component": "div", "props": {"class": "d-flex flex-wrap ga-2"}, "content": [
                    _chip(f"任务 {total}", "primary"), _chip(f"活动 {active}", "success"),
                    _chip(f"自动下载 {auto}", "warning"), _chip("下载后手工信息整理", "success"),
                    _chip(f"周期 {plugin._cron}", "info"),
                ]},
                {"component": "div", "props": {"class": "text-caption mt-3"},
                 "text": "新建或更新节目请打开插件配置；本页可立即检查、暂停、切换自动下载或移入回收站。"},
                {"component": "div", "props": {"class": "mt-3"}, "content": [
                    _action("重试失败整理", "mdi-folder-refresh", "secondary",
                            f"plugin/{PLUGIN_ID}/transfers/retry-failed"),
                ]},
            ]},
        ],
    }


def _task_collection(tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    cards = [_task_card(task) for task in tasks] if tasks else [_alert("info", "暂无直搜任务，请在插件配置中创建。")]
    return _section("插件任务", cards)


def _task_card(task: Dict[str, Any]) -> Dict[str, Any]:
    target = target_episodes(task)
    downloaded = parse_episodes(task.get("downloaded_episodes"))
    missing = target.difference(downloaded)
    progress = (f"已获取 {len(target) - len(missing)}/{len(target)} · 缺 {episodes_text(missing) or '-'}"
                if target else f"持续追更 · 已记录 {episodes_text(downloaded) or '-'}")
    status = {"active": "活动", "paused": "暂停", "running": "检查中", "completed": "已完成",
              "error": "异常"}.get(str(task.get("status") or ""), str(task.get("status") or "未知"))
    toggle_text = "暂停" if task.get("enabled") else "恢复"
    toggle_icon = "mdi-pause" if task.get("enabled") else "mdi-play"
    auto_text = "关闭自动下载" if task.get("auto_download") else "开启自动下载"
    priority_text = {
        "seeders": "做种数优先", "balanced": "综合优先", "free": "免费优先",
        "latest": "发布时间优先", "smallest": "小体积优先", "largest": "大体积优先",
    }[normalize_priority_mode(task.get("priority_mode"))]
    priority_text += f" · 最低做种 {parse_int(task.get('min_seeders'), 0, minimum=0) or 0}"
    priority_text += " · 下载历史去重" if parse_bool(task.get("dedupe_history"), True) else ""
    priority_text += " · 整包优先" if parse_bool(task.get("prefer_full_pack"), True) else ""
    transfer_status = {
        "waiting": "等待下载完成", "queued": "已加入整理队列", "completed": "整理完成",
        "failed": "整理失败",
    }.get(str(task.get("last_transfer_status") or ""), "尚无整理记录")
    if task.get("last_transfer_message"):
        transfer_status += f" · {task.get('last_transfer_message')}"
    cleanup_pending = task.get("cleanup_pending") or {}
    if _cleanup_pending_active(cleanup_pending):
        cleanup_scope = "下载任务及下载文件" if cleanup_pending.get("delete_files") \
            else "下载任务（保留文件）"
        cleanup_actions = [
            _alert("warning", f"待确认：将清理{cleanup_scope}，重置插件进度后立即重新检查；媒体库成品不会删除。"),
            {"component": "div", "props": {"class": "d-flex flex-wrap ga-2 mt-2"}, "content": [
                _action("确认清理并重处理", "mdi-delete-sweep", "error",
                        f"plugin/{PLUGIN_ID}/tasks/{task['id']}/cleanup/confirm"),
                _action("取消清理", "mdi-close", "secondary",
                        f"plugin/{PLUGIN_ID}/tasks/{task['id']}/cleanup/cancel"),
            ]},
        ]
    else:
        cleanup_actions = [
            {"component": "div", "props": {"class": "d-flex flex-wrap ga-2 mt-2"}, "content": [
                _action("准备清理（保留文件）", "mdi-broom", "secondary",
                        f"plugin/{PLUGIN_ID}/tasks/{task['id']}/cleanup/prepare"),
                _action("准备清理下载文件", "mdi-delete-sweep", "error",
                        f"plugin/{PLUGIN_ID}/tasks/{task['id']}/cleanup/prepare-files"),
            ]},
        ]
    return {
        "component": "VCard", "props": {"variant": "outlined", "class": "mb-3"},
        "content": [
            {"component": "VCardTitle", "content": [
                {"component": "div", "props": {"class": "d-flex align-center flex-wrap ga-2"}, "content": [
                    {"component": "span", "text": str(task.get("name") or "未命名")},
                    _chip(status, _status_color(task.get("status"))), _chip(str(task.get("type") or ""), "secondary"),
                    _chip("自动下载" if task.get("auto_download") else "仅预览",
                          "warning" if task.get("auto_download") else "info"),
                ]}
            ]},
            {"component": "VCardText", "content": [
                _line("进度", progress), _line("搜索词", " / ".join(task_search_keywords(task))),
                _line("站点", ", ".join(str(item) for item in task.get("sites") or []) or "系统活动站点"),
                _line("择优", priority_text),
                _line("最近结果", str(task.get("last_message") or "尚未运行")),
                _line("本轮原因", str(task.get("last_reason_summary") or "尚无详细原因")),
                _line("下载后整理", transfer_status),
                _line("最近检查", str(task.get("last_run_at") or "-")),
                {"component": "div", "props": {"class": "d-flex flex-wrap ga-2 mt-3"}, "content": [
                    _action("立即检查", "mdi-magnify", "primary", f"plugin/{PLUGIN_ID}/tasks/{task['id']}/run"),
                    _action(toggle_text, toggle_icon, "secondary", f"plugin/{PLUGIN_ID}/tasks/{task['id']}/toggle"),
                    _action(auto_text, "mdi-download", "warning", f"plugin/{PLUGIN_ID}/tasks/{task['id']}/auto"),
                    _action("移入回收站", "mdi-delete-outline", "error", f"plugin/{PLUGIN_ID}/tasks/{task['id']}/delete"),
                ]},
                *cleanup_actions,
            ]},
        ],
    }


def _recent_results(tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    rows = []
    for task in tasks:
        for result in (task.get("last_results") or [])[:10]:
            state = "已下载" if result.get("downloaded") else (
                "重复跳过" if result.get("duplicate") or result.get("skip_reason") else "候选"
            )
            rows.append({"节目": task.get("name"), "站点": result.get("site"), "标题": result.get("title"),
                         "集数": result.get("episodes") or "未知", "做种": result.get("seeders") or 0,
                         "促销": "免费" if result.get("free") else "-", "状态": state,
                         "原因": result.get("download_error") or result.get("skip_reason")
                                 or result.get("selection_reason") or result.get("reason") or "-"})
    headers = [{"title": key, "key": key}
               for key in ["节目", "站点", "标题", "集数", "做种", "促销", "状态", "原因"]]
    return {
        "component": "VCard", "props": {"variant": "outlined", "class": "mb-4"},
        "content": [{"component": "VCardTitle", "text": "最近候选"},
                    {"component": "VDataTable", "props": {"headers": headers, "items": rows[:50],
                                                               "items-per-page": 10, "density": "compact",
                                                               "no-data-text": "暂无候选结果"}}],
    }


def _run_log_table(tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    rows = []
    for task in tasks:
        for entry in task.get("run_logs") or []:
            rows.append({
                "时间": entry.get("time"), "节目": task.get("name"),
                "阶段": entry.get("stage"), "动作": entry.get("action"),
                "站点": entry.get("site") or "-", "资源": entry.get("title") or "-",
                "集数": entry.get("episodes") or "-", "做种": entry.get("seeders") or 0,
                "原因": entry.get("reason") or "-",
            })
    rows.sort(key=lambda item: str(item.get("时间") or ""), reverse=True)
    headers = [{"title": key, "key": key}
               for key in ["时间", "节目", "阶段", "动作", "站点", "资源", "集数", "做种", "原因"]]
    return {
        "component": "VCard", "props": {"variant": "outlined", "class": "mb-4"},
        "content": [{"component": "VCardTitle", "text": "详细运行日志与原因"},
                    {"component": "VDataTable", "props": {
                        "headers": headers, "items": rows[:100], "items-per-page": 15,
                        "density": "compact", "no-data-text": "暂无运行日志",
                    }}],
    }


def _trash_collection(tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    return _section("回收站", [
        {"component": "VCard", "props": {"variant": "outlined", "class": "mb-2"}, "content": [
            {"component": "VCardText", "content": [
                _line("任务", str(task.get("name") or task.get("id"))),
                _line("删除时间", str(task.get("deleted_at") or "-")),
                _action("恢复", "mdi-restore", "success", f"plugin/{PLUGIN_ID}/trash/{task['id']}/restore"),
            ]}
        ]} for task in tasks
    ])


def _section(title: str, content: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"component": "VCard", "props": {"variant": "outlined", "class": "mb-4"},
            "content": [{"component": "VCardTitle", "text": title},
                        {"component": "VCardText", "content": content}]}


def _row(content: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"component": "VRow", "content": content}


def _col(cols: int, md: int, child: Dict[str, Any]) -> Dict[str, Any]:
    return {"component": "VCol", "props": {"cols": cols, "md": md}, "content": [child]}


def _field(model: str, label: str, placeholder: str = "", hint: str = "") -> Dict[str, Any]:
    return {"component": "VTextField", "props": {"model": model, "label": label,
                                                      "placeholder": placeholder, "hint": hint}}


def _number(model: str, label: str, minimum: int, maximum: int) -> Dict[str, Any]:
    return {"component": "VTextField", "props": {"model": model, "label": label, "type": "number",
                                                      "min": minimum, "max": maximum}}


def _textarea(model: str, label: str, placeholder: str) -> Dict[str, Any]:
    return {"component": "VTextarea", "props": {"model": model, "label": label,
                                                     "placeholder": placeholder, "rows": 3}}


def _switch(model: str, label: str, hint: str) -> Dict[str, Any]:
    return {"component": "VSwitch", "props": {"model": model, "label": label,
                                                   "hint": hint, "color": "primary"}}


def _alert(alert_type: str, text: str) -> Dict[str, Any]:
    return {"component": "VAlert", "props": {"type": alert_type, "variant": "tonal"}, "text": text}


def _chip(text: str, color: str) -> Dict[str, Any]:
    return {"component": "VChip", "props": {"color": color, "size": "small", "variant": "tonal"},
            "text": text}


def _line(label: str, value: str) -> Dict[str, Any]:
    return {"component": "div", "props": {"class": "text-body-2 mb-1"}, "content": [
        {"component": "span", "props": {"class": "font-weight-medium mr-2"}, "text": f"{label}："},
        {"component": "span", "text": value},
    ]}


def _action(text: str, icon: str, color: str, api: str) -> Dict[str, Any]:
    return {"component": "VBtn", "props": {"variant": "tonal", "color": color,
                                                "prepend-icon": icon, "size": "small"}, "text": text,
            "events": {"click": {"api": api, "method": "post"}}}


def _status_color(status: Any) -> str:
    return {"active": "success", "running": "primary", "completed": "info", "paused": "secondary",
            "error": "error"}.get(str(status or ""), "secondary")
