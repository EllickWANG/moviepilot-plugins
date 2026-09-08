"""完全由插件维护的站点直搜订阅。"""

from __future__ import annotations

import asyncio
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
from app.chain.mediaserver import MediaServerChain
from app.chain.search import SearchChain
from app.chain.storage import StorageChain
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
from app.schemas.types import EventType, MediaType, ModuleType, NotificationType

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
    task_title_candidates,
    title_matches,
    validate_task,
    word_filter_reason,
)


PLUGIN_ID = "directsearchsubscribe"
TASKS_KEY = "tasks_v2"
TRASH_KEY = "tasks_v2_trash"
REPAIR_STATE_KEY = "repair_state_v1"
LEGACY_TASKS_KEY = "direct_subscribes"
MAX_TRASH = 100
SEARCH_TIMEOUT = 300


class directsearchsubscribe(_PluginBase):
    """自包含的直搜订阅插件。"""

    plugin_name = "直搜订阅"
    plugin_desc = "手工维护节目与集数，定时直搜站点；下载完成后按人工信息整理。"
    plugin_icon = "mdi-magnify-scan"
    plugin_version = "2.5.0"
    plugin_author = "Ellick"
    plugin_order = 30
    auth_level = 1

    _instance: Optional["directsearchsubscribe"] = None
    _enabled = True
    _cron = "*/30 * * * *"
    _notify = True
    _max_downloads = 3
    _task_gap = 2
    _repair_enabled = True
    _repair_cron = "15 */6 * * *"
    _repair_grace_minutes = 30
    _config: Dict[str, Any] = {}
    _data_lock = threading.RLock()
    _running_lock = threading.Lock()
    _download_lock = threading.Lock()
    _running_ids: Set[str] = set()
    _active_stop_event = threading.Event()
    _stop_event: threading.Event
    _transfer_patch_lock = threading.RLock()
    _transfer_retry_lock = threading.Lock()
    _repair_lock = threading.Lock()
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
        self._repair_enabled = parse_bool(config.get("repair_enabled"), True)
        self._repair_cron = str(config.get("repair_cron") or "15 */6 * * *").strip()
        self._repair_grace_minutes = parse_int(
            config.get("repair_grace_minutes"), 30, 5, 1440
        ) or 30
        self.__class__._enabled = self._enabled
        self.__class__._cron = self._cron
        self.__class__._notify = self._notify
        self.__class__._max_downloads = self._max_downloads
        self.__class__._task_gap = self._task_gap
        self.__class__._repair_enabled = self._repair_enabled
        self.__class__._repair_cron = self._repair_cron
        self.__class__._repair_grace_minutes = self._repair_grace_minutes
        self._config = config

        # 下载完成后仍使用 MoviePilot 的转移链，但只为本插件下载注入手工媒体信息，
        # 并阻止电视剧整理阶段再次读取 TMDB 集信息。
        self._patch_transfer_chain()

        # 2.0.0 创建的任务只有插件标签，MoviePilot 下载管理会将其过滤掉。
        # 标签追加是幂等操作，每次加载时顺便修复仍保留在下载器中的历史任务。
        self._repair_download_tags()
        self._reconcile_transfer_records()

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
        if not self._enabled:
            return []
        services = []
        if self._cron:
            try:
                services.append({
                    "id": "directsearchsubscribe_scan",
                    "name": "直搜订阅定时检查",
                    "trigger": CronTrigger.from_crontab(self._cron),
                    "func": self.run_scheduled,
                    "kwargs": {},
                })
            except Exception as err:
                logger.error(f"直搜订阅 cron 无效：{self._cron} - {err}")
        if self._repair_enabled and self._repair_cron:
            try:
                services.append({
                    "id": "directsearchsubscribe_repair",
                    "name": "直搜订阅丢失补偿",
                    "trigger": CronTrigger.from_crontab(self._repair_cron),
                    "func": self.run_repair_scheduled,
                    "kwargs": {},
                })
            except Exception as err:
                logger.error(f"直搜订阅补偿 cron 无效：{self._repair_cron} - {err}")
        return services

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
            _api("/repair/status", self.api_repair_status, ["GET"], "查询丢失补偿状态"),
            _api("/repair/run", self.api_run_repair, ["POST"], "立即执行丢失补偿"),
        ]

    def get_form(self) -> Tuple[Optional[List[dict]], Dict[str, Any]]:
        site_options = _active_site_options()
        return [
            {
                "component": "VForm",
                "content": [
                    _form_hero(self),
                    _form_section(
                        "自动运行",
                        "控制插件何时检查以及每轮最多添加多少下载；通常保持默认值即可。",
                        "mdi-tune-variant",
                        [
                        _row([
                            _col(12, 6, _switch("enabled", "启用定时检查", "关闭后保留任务，但不再自动执行")),
                            _col(12, 6, _switch("notify", "下载结果通知", "有新下载时发送插件通知")),
                        ]),
                        _row([
                            _col(12, 4, _field(
                                "cron", "检查周期", "*/30 * * * *", "Cron 表达式，默认每 30 分钟",
                            )),
                            _col(12, 4, _number(
                                "max_downloads", "每轮最多新增", 1, 20, "每个任务单轮的下载上限",
                            )),
                            _col(12, 4, _number(
                                "task_gap", "任务间隔", 0, 60, "任务之间等待的秒数",
                            )),
                        ]),
                        {"component": "VDivider", "props": {"class": "my-3"}},
                        _row([
                            _col(12, 4, _switch(
                                "repair_enabled", "启用丢失补偿",
                                "核对整理文件和媒体服务器索引；丢失时自动补回",
                            )),
                            _col(12, 4, _field(
                                "repair_cron", "补偿检查周期", "15 */6 * * *",
                                "默认每 6 小时检查一次，避开常规直搜整点",
                            )),
                            _col(12, 4, _number(
                                "repair_grace_minutes", "整理宽限时间", 5, 1440,
                                "刚整理的文件在此时间内不判定为丢失",
                            )),
                        ]),
                        _alert(
                            "info",
                            "补偿会先重扫媒体服务器；成品缺失但下载缓存仍在时重新整理，"
                            "缓存也丢失时才恢复为缺集并重新直搜。",
                        ),
                    ]),
                    _form_section(
                        "1. 节目与追更范围",
                        "先定义要找什么，以及哪些集数还需要下载。",
                        "mdi-television-play",
                        [
                        _row([
                            _col(12, 8, _field("title", "节目名称", "从零开始的异世界生活")),
                            _col(12, 4, _select("type", "类型", [
                                {"title": "电视剧", "value": "电视剧"},
                                {"title": "电影", "value": "电影"},
                            ])),
                        ]),
                        _row([
                            _col(12, 3, _field("year", "年份（可选）", "2026")),
                            _col(12, 3, _number("season", "季（电影留空）", 1, 999)),
                            _col(12, 3, _number("start_episode", "起始集", 1, 99999)),
                            _col(12, 3, _number(
                                "total_episode", "结束集", 1, 99999, "留空表示持续追更",
                            )),
                        ]),
                        _row([
                            _col(12, 6, _field(
                                "episodes", "只追这些集（可选）", "1-12,14", "填写后优先于起始集和结束集",
                            )),
                            _col(12, 6, _field(
                                "owned_episodes", "已经拥有的集数", "1-3", "这些集数不会重复下载",
                            )),
                        ]),
                        _alert(
                            "info",
                            "集数支持 1-12,14 这样的写法。同名、同类型、同季任务会更新原任务并保留下载进度。",
                        ),
                    ]),
                    {
                        "component": "VExpansionPanels",
                        "props": {"variant": "accordion", "class": "mb-4"},
                        "content": [
                            _form_expansion(
                                "2. 搜索与标题匹配",
                                "搜索词、别名、站点与过滤条件",
                                "mdi-magnify-scan",
                                [
                                    _row([
                                        _col(12, 6, _textarea(
                                            "keywords", "站点搜索词（每行一个）",
                                            "Re Zero S04\n从零开始的异世界生活 第四季",
                                            "实际发送给 PT 站的关键词；留空时使用节目名称",
                                        )),
                                        _col(12, 6, _textarea(
                                            "aliases", "标题别名（每行一个）",
                                            "Re:Zero\nリゼロ", "只用于校验候选标题，不会额外发起搜索",
                                        )),
                                    ]),
                                    _row([
                                        _col(12, 6, _field(
                                            "include", "必须包含", "2160p,HEVC", "逗号分隔，全部命中才保留",
                                        )),
                                        _col(12, 6, _field(
                                            "exclude", "排除关键词", "试看,预告", "逗号分隔，命中任意一个就跳过",
                                        )),
                                    ]),
                                    _row([
                                        _col(12, 8, _select(
                                            "sites", "检查站点", site_options,
                                            "留空时使用系统允许搜索的活动站点", multiple=True,
                                        )),
                                        _col(12, 4, _number(
                                            "search_pages", "每个关键词搜索页数", 1, 5,
                                        )),
                                    ]),
                                    _switch(
                                        "strict_title_match", "严格标题匹配",
                                        "要求候选标题命中节目名称、标题别名或搜索词，建议保持开启",
                                    ),
                                ],
                            ),
                            _form_expansion(
                                "3. 下载与整理策略",
                                "候选排序、自动下载、保存位置与安全开关",
                                "mdi-download-box-outline",
                                [
                                    _row([
                                        _col(12, 4, _select("priority_mode", "候选优先规则", [
                                            {"title": "做种数优先", "value": "seeders"},
                                            {"title": "综合优先", "value": "balanced"},
                                            {"title": "免费优先", "value": "free"},
                                            {"title": "发布时间优先", "value": "latest"},
                                            {"title": "小体积优先", "value": "smallest"},
                                            {"title": "大体积优先", "value": "largest"},
                                        ])),
                                        _col(12, 4, _number(
                                            "min_seeders", "最低做种数", 0, 1000000,
                                        )),
                                        _col(12, 4, _switch(
                                            "dedupe_history", "下载历史去重",
                                            "检查插件记录和 MoviePilot 下载历史",
                                        )),
                                    ]),
                                    _row([
                                        _col(12, 4, _field(
                                            "downloader", "指定下载器（可选）", "留空使用站点或系统默认",
                                        )),
                                        _col(12, 4, _field(
                                            "save_path", "下载保存路径（可选）", "/media/downloads",
                                        )),
                                        _col(12, 4, _field(
                                            "media_category", "媒体库二级分类（可选）", "日番",
                                        )),
                                    ]),
                                    _row([
                                        _col(12, 6, _switch(
                                            "prefer_full_pack", "优先整包下载",
                                            "整包覆盖缺集时，优先选择整包并记录其全部集数",
                                        )),
                                        _col(12, 6, _switch(
                                            "accept_unknown_episode", "允许未知集数自动下载",
                                            "高风险：标题无法解析集数时，每轮仍可选择一个候选",
                                            color="warning",
                                        )),
                                    ]),
                                ],
                            ),
                        ],
                    },
                    _save_task_section(),
                ],
            }
        ], {
            "enabled": True,
            "notify": True,
            "cron": "*/30 * * * *",
            "max_downloads": 3,
            "task_gap": 2,
            "repair_enabled": True,
            "repair_cron": "15 */6 * * *",
            "repair_grace_minutes": 30,
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
        trash.sort(key=lambda item: str(item.get("deleted_at") or ""), reverse=True)
        legacy = self.get_data(LEGACY_TASKS_KEY) or {}
        active = sum(1 for task in tasks if task.get("enabled") and task.get("status") != "completed")
        auto = sum(1 for task in tasks if task.get("auto_download"))
        completed = sum(1 for task in tasks if task.get("status") == "completed")
        attention = sum(
            1 for task in tasks
            if task.get("status") == "error" or task.get("last_transfer_status") == "failed"
            or task.get("last_repair_status") == "warning"
        )
        repair_state = self.get_data(REPAIR_STATE_KEY) or {}
        contents = [
            _hero(self, len(tasks), attention),
            _repair_overview(self, repair_state),
            _overview_metrics(len(tasks), active, auto, completed),
            _task_collection(tasks),
            _activity_collection(tasks),
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
            task["repair_missing_episodes"] = []
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

    def api_run_repair(self) -> schemas.Response:
        if not self._start_repair_thread():
            return schemas.Response(success=True, message="丢失补偿正在运行")
        return schemas.Response(success=True, message="已开始在后台核对并补偿丢失内容")

    def api_repair_status(self) -> schemas.Response:
        state = dict(self.get_data(REPAIR_STATE_KEY) or {})
        state["running"] = self.__class__._repair_lock.locked()
        return schemas.Response(success=True, data=state)

    def _start_repair_thread(self) -> bool:
        """启动一次后台补偿，避免接口请求被媒体库扫描阻塞。"""
        if not self.__class__._repair_lock.acquire(blocking=False):
            return False

        def runner():
            try:
                self._run_repair()
            except Exception as err:
                logger.error(f"直搜订阅丢失补偿异常：{err}", exc_info=True)
            finally:
                self.__class__._repair_lock.release()

        threading.Thread(
            target=runner, name="direct-search-repair", daemon=True
        ).start()
        return True

    def run_repair_scheduled(self):
        """定时核对整理文件和媒体服务器索引，并按最小动作补偿。"""
        if not self._enabled or not self._repair_enabled \
                or global_vars.is_system_stopped or self._stop_event.is_set():
            return
        if not self.__class__._repair_lock.acquire(blocking=False):
            logger.info("直搜订阅丢失补偿仍在运行，本轮跳过")
            return
        try:
            self._run_repair()
        finally:
            self.__class__._repair_lock.release()

    def _run_repair(self):
        """执行文件、缓存和媒体服务器三级对账。"""
        self._reconcile_transfer_records()
        storage = StorageChain()
        tasks = self._load_tasks()
        checked_tasks = 0
        checked_files = 0
        refresh_entries: Dict[str, List[Dict[str, Any]]] = {}
        reopened_task_ids: List[str] = []
        requeued = 0
        lost_rows = []
        skipped_running = 0
        errors = []

        for task_id, task in tasks.items():
            if self._stop_event.is_set() or global_vars.is_system_stopped:
                break
            if not task.get("enabled") or task.get("type") != MediaType.TV.value:
                continue
            if not self._claim_task(task_id):
                skipped_running += 1
                continue
            try:
                try:
                    report = self._inspect_task_files(task, storage)
                    checked_tasks += 1
                    checked_files += report["files_checked"]
                    if report["present"]:
                        refresh_entries[task_id] = report["present"]
                    if report["lost"]:
                        self._mark_lost_episodes(task_id, report["lost"])
                        reopened_task_ids.append(task_id)
                        lost_rows.append({
                            "task_id": task_id,
                            "name": task.get("name"),
                            "episodes": sorted(report["lost"]),
                        })
                    for repair in report["retransfer"]:
                        if self._resubmit_missing_transfer(task_id, repair):
                            requeued += 1
                except Exception as err:
                    message = f"{task.get('name') or task_id}：{err}"
                    errors.append(message)
                    logger.warning(f"直搜订阅任务补偿检查失败：{message}", exc_info=True)
            finally:
                self._release_task(task_id)

        media_report = self._repair_media_indexes(refresh_entries)
        state = {
            "last_run_at": now_text(),
            "tasks_checked": checked_tasks,
            "files_checked": checked_files,
            "lost": lost_rows,
            "requeued_transfers": requeued,
            "media_index_gaps": media_report["gaps"],
            "media_refreshes": media_report["refreshes"],
            "skipped_running": skipped_running,
            "errors": errors,
        }
        state["message"] = _repair_state_message(state)
        self.save_data(REPAIR_STATE_KEY, state)
        logger.info(f"直搜订阅丢失补偿完成：{state['message']}")

        # 真正缺失的文件解除去重后立即重新直搜；自动下载开关仍照常生效。
        for index, task_id in enumerate(dict.fromkeys(reopened_task_ids)):
            if self._stop_event.is_set() or global_vars.is_system_stopped:
                break
            latest = self._load_tasks().get(task_id)
            if not latest or not latest.get("enabled") or not self._claim_task(task_id):
                continue
            try:
                self._execute_task(task_id, manual=False, stop_event=self._stop_event)
            finally:
                self._release_task(task_id)
            if index < len(reopened_task_ids) - 1 and self._task_gap:
                self._stop_event.wait(self._task_gap)

    def _inspect_task_files(self, task: Dict[str, Any], storage: StorageChain) -> Dict[str, Any]:
        """按整理历史检查成品与下载缓存，不凭插件进度字段猜测文件状态。"""
        downloaded = parse_episodes(task.get("downloaded_episodes"))
        owned = parse_episodes(task.get("owned_episodes"))
        episode_entries: Dict[int, List[Dict[str, Any]]] = {}
        files_checked = 0
        for record in task.get("download_records") or []:
            record_episodes = parse_episodes(record.get("episodes"))
            if not record_episodes or not record_episodes.intersection(downloaded - owned):
                continue
            if not _repair_record_is_mature(record, self._repair_grace_minutes):
                continue
            download_hash = str(record.get("hash") or "").strip()
            if not download_hash:
                continue
            histories = _latest_record_transfer_histories(record, download_hash)
            successes = [history for history in histories if bool(getattr(history, "status", False))]
            if not successes:
                continue
            for history in successes:
                history_episodes = _transfer_history_episodes(history)
                if not history_episodes and len(record_episodes) == 1:
                    history_episodes = set(record_episodes)
                history_episodes.intersection_update(record_episodes)
                if not history_episodes:
                    continue
                dest_item = _transfer_history_fileitem(history, "dest")
                if not dest_item or not dest_item.path:
                    continue
                files_checked += 1
                dest_exists = bool(storage.get_item(dest_item))
                src_item = _transfer_history_fileitem(history, "src")
                src_exists = bool(src_item and src_item.path and storage.get_item(src_item))
                for episode in history_episodes.intersection(downloaded - owned):
                    episode_entries.setdefault(episode, []).append({
                        "record": record,
                        "history": history,
                        "dest_item": dest_item,
                        "dest_exists": dest_exists,
                        "src_item": src_item,
                        "src_exists": src_exists,
                    })

        present = []
        retransfer = []
        lost: Set[int] = set()
        seen_sources = set()
        for episode, entries in episode_entries.items():
            existing = next((entry for entry in entries if entry["dest_exists"]), None)
            if existing:
                present.append({
                    "episode": episode,
                    "item": _refresh_item_from_transfer(existing["history"]),
                    "path": existing["dest_item"].path,
                })
                continue
            cached = next((entry for entry in entries if entry["src_exists"]), None)
            if cached:
                source_key = (
                    str(cached["record"].get("hash") or "").casefold(),
                    str(cached["src_item"].storage or ""),
                    str(cached["src_item"].path or ""),
                )
                if source_key not in seen_sources:
                    seen_sources.add(source_key)
                    retransfer.append({"episode": episode, **cached})
                continue
            lost.add(episode)
        return {
            "files_checked": files_checked,
            "present": [entry for entry in present if entry.get("item")],
            "retransfer": retransfer,
            "lost": lost,
        }

    def _resubmit_missing_transfer(self, task_id: str, repair: Dict[str, Any]) -> bool:
        """成品丢失但缓存仍在时，直接重走整理，不浪费 PT 下载。"""
        record = repair["record"]
        history = repair["history"]
        src_item = repair["src_item"]
        episode = int(repair["episode"])
        try:
            state, message = TransferChain().do_transfer(
                fileitem=src_item,
                downloader=getattr(history, "downloader", None),
                download_hash=str(record.get("hash") or ""),
                force=True,
                scrape=False,
                background=True,
            )
        except Exception as err:
            state, message = False, str(err)
        reason = (
            f"第 {episode} 集成品丢失，下载缓存仍在，已重新提交整理"
            if state else f"第 {episode} 集重新整理提交失败：{message or '未知错误'}"
        )
        self._append_repair_log(task_id, reason, success=bool(state))
        return bool(state)

    def _mark_lost_episodes(self, task_id: str, lost: Set[int]):
        """成品和缓存都不存在时解除旧历史去重，让缺集重新进入直搜。"""
        if not lost:
            return
        with self.__class__._data_lock:
            tasks = self._load_tasks()
            task = tasks.get(task_id)
            if not task:
                return
            downloaded = parse_episodes(task.get("downloaded_episodes"))
            downloaded.difference_update(lost)
            pending = parse_episodes(task.get("repair_missing_episodes"))
            pending.update(lost)
            ignored_hashes = set(task.get("ignored_history_hashes") or [])
            ignored_identities = set(task.get("ignored_resource_identities") or [])
            affected_fingerprints = set()
            records = list(task.get("download_records") or [])
            for record in records:
                if not parse_episodes(record.get("episodes")).intersection(lost):
                    continue
                download_hash = str(record.get("hash") or "").strip().casefold()
                identity = str(record.get("resource_identity") or "") \
                    or resource_identity(record.get("title"))
                fingerprint = str(record.get("fingerprint") or "")
                if download_hash:
                    ignored_hashes.add(download_hash)
                if identity:
                    ignored_identities.add(identity)
                if fingerprint:
                    affected_fingerprints.add(fingerprint)
                record["repair_status"] = "lost"
                record["repair_message"] = f"成品和下载缓存均不存在：{episodes_text(lost)}"
                record["repair_updated_at"] = now_text()
            task["downloaded_episodes"] = sorted(downloaded)
            task["repair_missing_episodes"] = sorted(pending)
            task["downloaded_fingerprints"] = [
                value for value in task.get("downloaded_fingerprints") or []
                if value not in affected_fingerprints
            ]
            task["ignored_history_hashes"] = sorted(ignored_hashes)[-MAX_RESOURCE_HISTORY:]
            task["ignored_resource_identities"] = sorted(ignored_identities)[-MAX_RESOURCE_HISTORY:]
            task["download_records"] = records[-MAX_RESOURCE_HISTORY:]
            task["status"] = "active" if task.get("enabled") else "paused"
            reason = f"检测到成品和下载缓存均丢失：{episodes_text(lost)}，已恢复为缺集"
            task["last_repair_at"] = now_text()
            task["last_repair_status"] = "warning"
            task["last_repair_message"] = reason
            task["last_message"] = reason
            task["last_reason_summary"] = reason
            task["run_logs"] = [_audit_entry(
                "补偿", "恢复缺集", reason, level="warning", episodes=episodes_text(lost)
            ), *(task.get("run_logs") or [])][:MAX_TASK_LOGS]
            task["updated_at"] = now_text()
            tasks[task_id] = task
            self._save_tasks(tasks)

    def _append_repair_log(self, task_id: str, message: str, success: bool = True):
        with self.__class__._data_lock:
            tasks = self._load_tasks()
            task = tasks.get(task_id)
            if not task:
                return
            task["last_repair_at"] = now_text()
            task["last_repair_status"] = "success" if success else "warning"
            task["last_repair_message"] = message
            task["run_logs"] = [_audit_entry(
                "补偿", "已处理" if success else "失败", message,
                level="info" if success else "warning",
            ), *(task.get("run_logs") or [])][:MAX_TASK_LOGS]
            task["updated_at"] = now_text()
            tasks[task_id] = task
            self._save_tasks(tasks)

    def _repair_media_indexes(self, entries_by_task: Dict[str, List[Dict[str, Any]]]) \
            -> Dict[str, Any]:
        """只在媒体服务器缺集或无法定位节目时触发对应媒体库刷新。"""
        if not entries_by_task:
            return {"gaps": [], "refreshes": 0}
        tasks = self._load_tasks()
        media_chain = MediaServerChain()
        gaps = []
        refreshes = 0
        for module in media_chain.modulemanager.get_running_type_modules(ModuleType.MediaServer):
            if not hasattr(module, "get_instances"):
                continue
            for server_name, server in (module.get_instances() or {}).items():
                refresh_items = []
                server_gaps = []
                for task_id, entries in entries_by_task.items():
                    task = tasks.get(task_id)
                    if not task:
                        continue
                    physical = {int(entry["episode"]) for entry in entries}
                    indexed = _server_indexed_episodes(module, server_name, task, entries)
                    missing = physical if indexed is None else physical.difference(indexed)
                    if not missing:
                        continue
                    server_gaps.append({
                        "task_id": task_id,
                        "name": task.get("name"),
                        "server": server_name,
                        "episodes": sorted(missing),
                        "index_unknown": indexed is None,
                    })
                    refresh_items.extend(
                        entry["item"] for entry in entries if int(entry["episode"]) in missing
                    )
                refresh_items = _dedupe_refresh_items(refresh_items)
                if not refresh_items or not hasattr(server, "refresh_library_by_items"):
                    gaps.extend(server_gaps)
                    continue
                success = _refresh_media_server(server, refresh_items)
                if success:
                    refreshes += 1
                for gap in server_gaps:
                    gaps.append(gap)
                    episode_text = episodes_text(gap["episodes"])
                    reason = (
                        f"媒体服务器 {server_name} 缺少索引 {episode_text}，已触发媒体库刷新"
                        if success else f"媒体服务器 {server_name} 缺少索引 {episode_text}，刷新未成功"
                    )
                    self._append_repair_log(gap["task_id"], reason, success=success)
        return {"gaps": gaps, "refreshes": refreshes}

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
        self._reconcile_transfer_records(task_id=task_id)
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
            repair_missing = parse_episodes(task.get("repair_missing_episodes"))
            # 已确认物理丢失的集数不能再被旧下载历史误恢复为“已获取”。
            history_episodes.difference_update(repair_missing)
            downloaded.difference_update(repair_missing)
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
                    found = _search_site_titles(search_chain, keyword, page, sites)
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
                    candidate_episodes = set(prepared[1].get("episode_numbers") or [])
                    repair_overlap = candidate_episodes.intersection(
                        parse_episodes(task.get("repair_missing_episodes"))
                    )
                    if (fingerprint in downloaded_fingerprints or identity in known_identities) \
                            and not repair_overlap:
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
            candidate_episodes = set(result.get("episode_numbers") or [])
            repair_overlap = candidate_episodes.intersection(
                parse_episodes(current.get("repair_missing_episodes"))
            )
            if (fingerprint in fingerprints or identity in known_identities) and not repair_overlap:
                duplicate_count += 1
                result["skip_reason"] = "运行期间再次命中插件记录或下载历史"
                _audit(audit, run_id, "去重", "跳过", result["skip_reason"], result=result)
                continue
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
                repair_missing = parse_episodes(current.get("repair_missing_episodes"))
                repair_overlap = progress_episodes.intersection(repair_missing)
                result["skip_reason"] = error or "下载任务已存在"
                duplicate_count += 1
                fingerprints.add(fingerprint)
                known_identities.add(identity)
                if repair_overlap:
                    result["skip_reason"] += "；该资源未补回丢失文件，继续保留缺集"
                else:
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
            repair_missing = parse_episodes(current.get("repair_missing_episodes"))
            repair_missing.difference_update(progress_episodes)
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
                repair_missing_episodes=sorted(repair_missing),
                last_transfer_status="waiting",
                last_transfer_message="等待下载完成",
                last_results=[item[1] for item in candidates],
            )
        self._update_runtime(task_id, last_results=[item[1] for item in candidates])
        self._reconcile_transfer_records(task_id=task_id)
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
            latest["repair_missing_episodes"] = []
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

    def _reconcile_transfer_records(self, task_id: Optional[str] = None):
        """按 Hash 对账整理历史，补偿整理事件早于插件记录落库的竞态。"""
        stored_tasks = [*self._load_tasks().values(), *self._load_trash().values()]
        for task in stored_tasks:
            if task_id and str(task.get("id") or "") != str(task_id):
                continue
            for record in task.get("download_records") or []:
                if str(record.get("transfer_status") or "") == "completed":
                    continue
                download_hash = str(record.get("hash") or "").strip()
                if not download_hash:
                    continue
                try:
                    histories = TransferHistoryOper().list_by_hash(download_hash) or []
                except Exception as err:
                    logger.warning(f"直搜订阅整理状态对账失败：{download_hash[:12]} - {err}")
                    continue
                try:
                    record_threshold = datetime.strptime(
                        str(record.get("time") or ""), "%Y-%m-%d %H:%M:%S"
                    ) - timedelta(minutes=2)
                    histories = [history for history in histories if not getattr(history, "date", None)
                                 or datetime.strptime(str(history.date), "%Y-%m-%d %H:%M:%S")
                                 >= record_threshold]
                except (TypeError, ValueError):
                    pass
                latest_by_source: Dict[str, Any] = {}
                for history in histories:
                    source = str(getattr(history, "src", "") or getattr(history, "id", ""))
                    current = latest_by_source.get(source)
                    rank = (str(getattr(history, "date", "") or ""), int(getattr(history, "id", 0) or 0))
                    current_rank = (
                        str(getattr(current, "date", "") or ""),
                        int(getattr(current, "id", 0) or 0),
                    ) if current else ("", 0)
                    if not current or rank >= current_rank:
                        latest_by_source[source] = history
                latest_histories = list(latest_by_source.values())
                successes = [history for history in latest_histories
                             if bool(getattr(history, "status", False))]
                failures = [history for history in latest_histories
                            if not bool(getattr(history, "status", False))]
                if failures:
                    message = str(getattr(failures[-1], "errmsg", "") or "整理历史中存在失败文件")
                    if successes:
                        message = f"已完成 {len(successes)} 个文件，失败 {len(failures)} 个：{message}"
                    self._update_transfer_record(download_hash, "failed", message)
                elif successes:
                    target = str(getattr(successes[-1], "dest", "") or "")
                    message = f"已整理 {len(successes)} 个文件"
                    if target:
                        message += f"，最近目标 {target}"
                    self._update_transfer_record(download_hash, "completed", message, target)

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


def _search_site_titles(search_chain: SearchChain, keyword: str, page: int,
                        sites: Optional[List[int]]) -> List[Context]:
    """取站点搜索结果，优先走核心的异步搜索路径。

    同步的 search_by_title 在部分部署上每个站点都会在 5 秒后返回空结果，而前端和订阅
    使用的 async_search_by_title 用同样的关键词与站点列表能正常拿到数据。这里沿用核心
    自身从工作线程调用协程的做法，把协程交回 app 主事件循环执行；异步 HTTP 客户端绑定
    在主循环上，另起事件循环（asyncio.run）会让它们失效，因此必须复用 global_vars.loop。
    核心没有异步搜索或主循环未运行时回退到同步实现。
    """
    async_search = getattr(search_chain, "async_search_by_title", None)
    loop = getattr(global_vars, "loop", None)
    if async_search and loop is not None and loop.is_running():
        coro = None
        future = None
        try:
            coro = async_search(title=keyword, page=page, sites=sites, cache_local=False)
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            # 协程已交给事件循环，后续由 future 负责，不再需要本地关闭。
            coro = None
            return future.result(timeout=SEARCH_TIMEOUT) or []
        except Exception as err:
            # 等待超时时协程仍在主循环上运行，取消掉避免它继续请求站点并与回退搜索重叠。
            if future is not None:
                future.cancel()
            logger.warning(f"直搜订阅异步搜索失败，回退同步搜索：{err}")
        finally:
            if coro is not None:
                coro.close()
    return search_chain.search_by_title(keyword, page=page, sites=sites) or []


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


def _repair_record_is_mature(record: Dict[str, Any], grace_minutes: int) -> bool:
    """整理完成后保留宽限期，避免扫描到仍在复制的目标文件。"""
    raw = record.get("transfer_updated_at") or record.get("time")
    try:
        updated_at = datetime.strptime(str(raw or ""), "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return False
    return datetime.now() - updated_at >= timedelta(minutes=max(5, grace_minutes))


def _latest_record_transfer_histories(record: Dict[str, Any], download_hash: str) -> List[Any]:
    """读取单次插件下载之后的最新整理历史，排除同 Hash 的旧任务记录。"""
    try:
        histories = TransferHistoryOper().list_by_hash(download_hash) or []
    except Exception as err:
        logger.warning(f"直搜订阅补偿读取整理历史失败：{download_hash[:12]} - {err}")
        return []
    try:
        threshold = datetime.strptime(
            str(record.get("time") or ""), "%Y-%m-%d %H:%M:%S"
        ) - timedelta(minutes=2)
        histories = [
            history for history in histories
            if not getattr(history, "date", None)
            or datetime.strptime(str(history.date), "%Y-%m-%d %H:%M:%S") >= threshold
        ]
    except (TypeError, ValueError):
        pass
    latest_by_source: Dict[str, Any] = {}
    for history in histories:
        source = str(getattr(history, "src", "") or getattr(history, "id", ""))
        current = latest_by_source.get(source)
        rank = (str(getattr(history, "date", "") or ""), int(getattr(history, "id", 0) or 0))
        current_rank = (
            str(getattr(current, "date", "") or ""),
            int(getattr(current, "id", 0) or 0),
        ) if current else ("", 0)
        if not current or rank >= current_rank:
            latest_by_source[source] = history
    return list(latest_by_source.values())


def _transfer_history_episodes(history: Any) -> Set[int]:
    """从整理历史字段和源/目标文件名恢复集数。"""
    return extract_episode_numbers(" ".join(filter(None, (
        str(getattr(history, "seasons", "") or ""),
        str(getattr(history, "episodes", "") or ""),
        str(getattr(history, "src", "") or ""),
        str(getattr(history, "dest", "") or ""),
    ))))


def _transfer_history_fileitem(history: Any, side: str) -> Optional[schemas.FileItem]:
    """兼容历史中的序列化 FileItem 和早期只有路径的记录。"""
    raw = getattr(history, f"{side}_fileitem", None)
    if isinstance(raw, schemas.FileItem):
        return raw
    if isinstance(raw, dict):
        try:
            return schemas.FileItem(**raw)
        except Exception:
            pass
    path = str(getattr(history, side, "") or "").strip()
    if not path:
        return None
    return schemas.FileItem(
        storage=str(getattr(history, f"{side}_storage", "") or "local"),
        type="file",
        path=path,
        name=Path(path).name,
    )


def _refresh_item_from_transfer(history: Any) -> Optional[schemas.RefreshMediaItem]:
    path = str(getattr(history, "dest", "") or "").strip()
    if not path:
        return None
    try:
        media_type = MediaType(getattr(history, "type", None))
    except (TypeError, ValueError):
        media_type = MediaType.TV
    return schemas.RefreshMediaItem(
        title=getattr(history, "title", None),
        year=getattr(history, "year", None),
        type=media_type,
        category=getattr(history, "category", None),
        target_path=Path(path),
    )


def _repair_title_candidates(task: Dict[str, Any], entries: List[Dict[str, Any]]) -> List[str]:
    """生成媒体服务器标题候选，兼容人工任务名中包含季号和年份。"""
    values = list(task_title_candidates(task))
    for entry in entries:
        path = Path(str(entry.get("path") or ""))
        if len(path.parents) >= 2:
            values.append(path.parent.parent.name)
    result = []
    seen = set()

    def add(value: Any):
        text = str(value or "").strip(" .-_()（）[]")
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)

    for value in values:
        text = str(value or "").strip()
        add(text)
        without_year = re.sub(r"\s*[\(（\[]\d{4}[\)）\]]\s*$", "", text).strip()
        add(without_year)
        without_season = re.sub(
            r"\s*第\s*(?:\d+|[零〇一二三四五六七八九十百两]+)\s*季\s*$",
            "", without_year, flags=re.IGNORECASE,
        ).strip()
        without_season = re.sub(
            r"\s+(?:season\s*|S)0*\d{1,3}\s*$", "", without_season,
            flags=re.IGNORECASE,
        ).strip()
        add(without_season)
    return result


def _server_indexed_episodes(module: Any, server_name: str, task: Dict[str, Any],
                             entries: List[Dict[str, Any]]) -> Optional[Set[int]]:
    """查询单个媒体服务器中的已索引集数；无法定位节目时返回 None。"""
    season = parse_int(task.get("season"), minimum=1)
    if season is None or not hasattr(module, "media_exists"):
        return None
    for title in _repair_title_candidates(task, entries):
        media = _manual_media_info(task)
        media.title = title
        # 人工任务年份经常是季度年份，而媒体服务器保存的是整部剧首播年份。
        media.year = None
        try:
            exists = module.media_exists(mediainfo=media, server=server_name)
        except Exception as err:
            logger.warning(f"直搜订阅查询媒体服务器 {server_name} 失败：{title} - {err}")
            continue
        if not exists:
            continue
        seasons = getattr(exists, "seasons", None) or {}
        indexed = seasons.get(season)
        if indexed is None:
            indexed = seasons.get(str(season))
        return parse_episodes(indexed or [])
    return None


def _dedupe_refresh_items(items: List[schemas.RefreshMediaItem]) -> List[schemas.RefreshMediaItem]:
    result = []
    seen = set()
    for item in items:
        path = str(getattr(item, "target_path", "") or "")
        if path and path not in seen:
            seen.add(path)
            result.append(item)
    return result


def _refresh_media_server(server: Any, items: List[schemas.RefreshMediaItem]) -> bool:
    """触发最小范围刷新；服务端不支持路径刷新时回退全库扫描。"""
    try:
        try:
            result = server.refresh_library_by_items(items, scan_mode=3)
        except TypeError:
            result = server.refresh_library_by_items(items)
    except Exception as err:
        logger.warning(f"直搜订阅触发媒体服务器刷新失败：{err}")
        return False
    if result is not False and result is not None:
        return True
    api = getattr(server, "_api", None)
    if result is False and api and hasattr(api, "task_running"):
        try:
            if api.task_running():
                return True
        except Exception:
            pass
    if hasattr(server, "refresh_root_library"):
        try:
            fallback = server.refresh_root_library()
            return fallback is not False and fallback is not None
        except Exception as err:
            logger.warning(f"直搜订阅触发媒体服务器全库刷新失败：{err}")
    return False


def _repair_state_message(state: Dict[str, Any]) -> str:
    lost_count = sum(len(row.get("episodes") or []) for row in state.get("lost") or [])
    index_count = sum(len(row.get("episodes") or []) for row in state.get("media_index_gaps") or [])
    message = (
        f"核对 {state.get('tasks_checked') or 0} 个任务、{state.get('files_checked') or 0} 个文件；"
        f"媒体索引缺 {index_count} 集，触发刷新 {state.get('media_refreshes') or 0} 个服务器；"
        f"重新整理 {state.get('requeued_transfers') or 0} 个文件；"
        f"恢复直搜 {lost_count} 集"
    )
    if state.get("errors"):
        message += f"；异常 {len(state['errors'])} 个"
    if state.get("skipped_running"):
        message += f"；跳过运行中任务 {state['skipped_running']} 个"
    return message


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


def _form_hero(plugin: directsearchsubscribe) -> Dict[str, Any]:
    return {
        "component": "VCard",
        "props": {"variant": "tonal", "color": "primary", "rounded": "xl", "class": "mb-4"},
        "content": [{
            "component": "VCardText",
            "props": {"class": "pa-4 pa-sm-5"},
            "content": [
                {"component": "div", "props": {"class": "d-flex align-center ga-3"}, "content": [
                    _avatar("mdi-magnify-scan", "primary", 52),
                    {"component": "div", "props": {"style": "min-width:0"}, "content": [
                        {"component": "div", "props": {"class": "text-h6 font-weight-bold"},
                         "text": "创建直搜任务"},
                        {"component": "div", "props": {"class": "text-body-2 text-medium-emphasis mt-1"},
                         "text": f"v{plugin.plugin_version} · 人工定义节目，直接搜索 PT 站，不依赖外部媒体信息源"},
                    ]},
                ]},
                {"component": "div", "props": {"class": "d-flex flex-wrap align-center ga-2 mt-4"},
                 "content": [
                     _chip("1  定义节目", "primary", "mdi-television-play"),
                     _icon("mdi-chevron-right", "text-medium-emphasis"),
                     _chip("2  预览候选", "info", "mdi-eye-outline"),
                     _icon("mdi-chevron-right", "text-medium-emphasis"),
                     _chip("3  开启自动下载", "warning", "mdi-download"),
                 ]},
            ],
        }],
    }


def _form_section(title: str, subtitle: str, icon: str,
                  content: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "component": "VCard",
        "props": {"variant": "flat", "rounded": "xl", "border": True, "class": "mb-4"},
        "content": [{
            "component": "VCardText",
            "props": {"class": "pa-4 pa-sm-5"},
            "content": [
                _section_heading(title, subtitle, icon),
                {"component": "div", "props": {"class": "mt-4"}, "content": content,
                 },
            ],
        }],
    }


def _form_expansion(title: str, subtitle: str, icon: str,
                    content: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "component": "VExpansionPanel",
        "content": [
            {"component": "VExpansionPanelTitle", "content": [
                {"component": "div", "props": {"class": "d-flex align-center ga-3 w-100 pr-3"}, "content": [
                    _avatar(icon, "primary", 36),
                    {"component": "div", "props": {"style": "min-width:0"}, "content": [
                        {"component": "div", "props": {"class": "text-subtitle-1 font-weight-medium"},
                         "text": title},
                        {"component": "div", "props": {"class": "text-caption text-medium-emphasis"},
                         "text": subtitle},
                    ]},
                ]},
            ]},
            {"component": "VExpansionPanelText", "content": content},
        ],
    }


def _save_task_section() -> Dict[str, Any]:
    return {
        "component": "VCard",
        "props": {"variant": "tonal", "color": "primary", "rounded": "xl", "class": "mb-4"},
        "content": [{
            "component": "VCardText",
            "props": {"class": "pa-4 pa-sm-5"},
            "content": [
                _section_heading(
                    "4. 创建或更新任务",
                    "最后确认任务状态，然后使用插件配置页自带的保存按钮提交。",
                    "mdi-content-save-check-outline",
                ),
                _row([
                    _col(12, 4, _switch("task_enabled", "创建后立即启用", "关闭时保存为暂停任务")),
                    _col(12, 4, _switch(
                        "auto_download", "允许自动下载", "初次使用建议关闭，先在详情页检查候选",
                        color="warning",
                    )),
                    _col(12, 4, _switch(
                        "run_after_save", "保存后立即检查", "创建或更新成功后立即搜索一次",
                    )),
                ]),
                {"component": "VDivider", "props": {"class": "my-3"}},
                _switch(
                    "save_task_now", "本次保存时创建或更新上面的任务",
                    "请先开启此项，再点击页面右下角的保存按钮；执行完成后此项会自动关闭",
                ),
                _alert("info", "若只是修改插件的自动运行设置，请不要开启“本次保存时创建或更新任务”。"),
            ],
        }],
    }


def _hero(plugin: directsearchsubscribe, total: int, attention: int) -> Dict[str, Any]:
    enabled = plugin._enabled
    return {
        "component": "VCard",
        "props": {"variant": "tonal", "color": "primary", "rounded": "xl", "class": "mb-4"},
        "content": [{
            "component": "VCardText",
            "props": {"class": "pa-4 pa-sm-5"},
            "content": [
                {"component": "div",
                 "props": {"class": "d-flex flex-wrap align-center justify-space-between ga-3"},
                 "content": [
                     {"component": "div", "props": {"class": "d-flex align-center ga-3",
                                                       "style": "min-width:0"}, "content": [
                         _avatar("mdi-magnify-scan", "primary", 52),
                         {"component": "div", "props": {"style": "min-width:0"}, "content": [
                             {"component": "div", "props": {"class": "d-flex flex-wrap align-center ga-2"},
                              "content": [
                                  {"component": "div", "props": {"class": "text-h6 font-weight-bold"},
                                   "text": "直搜订阅"},
                                  _chip("运行中" if enabled else "已停用", "success" if enabled else "error",
                                        "mdi-power" if enabled else "mdi-power-off"),
                                  _chip(f"v{plugin.plugin_version}", "secondary", "mdi-tag-outline"),
                              ]},
                             {"component": "div",
                              "props": {"class": "text-body-2 text-medium-emphasis mt-1"},
                              "text": "手工节目任务 · PT 站直搜 · 下载完成后自动整理"},
                         ]},
                     ]},
                     {"component": "div", "props": {"class": "d-flex flex-wrap ga-2"}, "content": [
                         _action("立即对账补偿", "mdi-database-sync", "primary",
                                 f"plugin/{PLUGIN_ID}/repair/run"),
                         _action("重试失败整理", "mdi-folder-refresh", "secondary",
                                 f"plugin/{PLUGIN_ID}/transfers/retry-failed"),
                     ]},
                 ]},
                {"component": "div", "props": {"class": "d-flex flex-wrap align-center ga-2 mt-4"},
                 "content": [
                     _chip("节目任务", "primary", "mdi-playlist-check"),
                     _icon("mdi-chevron-right", "text-medium-emphasis"),
                     _chip("搜索 PT 站", "info", "mdi-database-search"),
                     _icon("mdi-chevron-right", "text-medium-emphasis"),
                     _chip("发送下载器", "warning", "mdi-download"),
                     _icon("mdi-chevron-right", "text-medium-emphasis"),
                     _chip("媒体库整理", "success", "mdi-folder-move"),
                 ]},
                {"component": "div", "props": {"class": "text-caption text-medium-emphasis mt-3"},
                 "text": f"共 {total} 个任务 · 检查周期 {plugin._cron}"
                         + (f" · 补偿周期 {plugin._repair_cron}" if plugin._repair_enabled else " · 补偿已停用")
                         + (f" · {attention} 个任务需要处理" if attention else " · 当前无异常")},
            ],
        }],
    }


def _overview_metrics(total: int, active: int, auto: int, completed: int) -> Dict[str, Any]:
    return {
        "component": "VRow",
        "props": {"class": "mb-1"},
        "content": [
            _stat_card("全部任务", total, "插件内维护", "mdi-playlist-check", "primary"),
            _stat_card("正在追更", active, "启用且未完成", "mdi-radar", "success"),
            _stat_card("自动下载", auto, "其余任务仅预览", "mdi-download-circle-outline", "warning"),
            _stat_card("已经完成", completed, "有限目标已齐", "mdi-check-decagram-outline", "info"),
        ],
    }


def _repair_overview(plugin: directsearchsubscribe, state: Dict[str, Any]) -> Dict[str, Any]:
    if not plugin._repair_enabled:
        return _alert("warning", "丢失补偿已停用；不会定时核对媒体索引、成品文件和下载缓存。")
    message = str(state.get("message") or "尚未执行补偿对账；系统会按设定周期自动检查。")
    checked_at = str(state.get("last_run_at") or "-")
    return {
        "component": "VAlert",
        "props": {
            "type": "info", "variant": "tonal", "rounded": "xl", "class": "mb-4",
            "title": "丢失补偿",
        },
        "content": [
            {"component": "div", "props": {"class": "text-body-2"}, "text": message},
            {"component": "div", "props": {"class": "text-caption text-medium-emphasis mt-1"},
             "text": f"上次检查 {checked_at} · 宽限 {plugin._repair_grace_minutes} 分钟"},
        ],
    }


def _stat_card(title: str, value: Any, subtitle: str, icon: str, color: str) -> Dict[str, Any]:
    return {
        "component": "VCol", "props": {"cols": 6, "sm": 6, "lg": 3},
        "content": [{
            "component": "VCard",
            "props": {"variant": "flat", "rounded": "xl", "border": True, "class": "h-100"},
            "content": [{"component": "VCardText", "props": {"class": "pa-3 pa-sm-4"}, "content": [
                {"component": "div", "props": {"class": "d-flex align-center ga-3"}, "content": [
                    _avatar(icon, color, 44),
                    {"component": "div", "props": {"style": "min-width:0"}, "content": [
                        {"component": "div", "props": {"class": "text-h5 font-weight-bold lh-1"},
                         "text": str(value)},
                        {"component": "div", "props": {"class": "text-caption font-weight-medium mt-1"},
                         "text": title},
                        {"component": "div", "props": {"class": "text-caption text-medium-emphasis"},
                         "text": subtitle},
                    ]},
                ]},
            ]}],
        }],
    }


def _task_collection(tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not tasks:
        content = [_empty_state(
            "mdi-playlist-plus", "还没有直搜任务", "打开插件配置，填写节目和搜索词后创建第一个任务。",
        )]
    else:
        content = [{
            "component": "VExpansionPanels",
            "props": {"variant": "accordion"},
            "content": [_task_panel(task) for task in tasks],
        }]
    return _page_card(
        "任务",
        "按最近更新时间排序；展开任务可查看规则、运行结果和操作。",
        "mdi-format-list-bulleted",
        content,
        count=len(tasks),
    )


def _task_panel(task: Dict[str, Any]) -> Dict[str, Any]:
    target = target_episodes(task)
    downloaded = parse_episodes(task.get("downloaded_episodes"))
    missing = target.difference(downloaded)
    acquired = len(target) - len(missing)
    status = _task_status_label(task.get("status"))
    status_color = _status_color(task.get("status"))
    toggle_text = "暂停任务" if task.get("enabled") else "恢复任务"
    toggle_icon = "mdi-pause" if task.get("enabled") else "mdi-play"
    auto_text = "关闭自动下载" if task.get("auto_download") else "开启自动下载"
    media_bits = [str(task.get("type") or "节目")]
    if task.get("year"):
        media_bits.append(str(task.get("year")))
    if task.get("type") != "电影" and task.get("season"):
        media_bits.append(f"S{int(task['season']):02d}")
    progress_summary = (f"已获取 {acquired}/{len(target)} · 缺 {episodes_text(missing) or '-'}"
                        if target else f"持续追更 · 已记录 {episodes_text(downloaded) or '-'}")
    priority_text = {
        "seeders": "做种数优先", "balanced": "综合优先", "free": "免费优先",
        "latest": "发布时间优先", "smallest": "小体积优先", "largest": "大体积优先",
    }[normalize_priority_mode(task.get("priority_mode"))]
    priority_text += f" · 最低做种 {parse_int(task.get('min_seeders'), 0, minimum=0) or 0}"
    priority_text += " · 历史去重" if parse_bool(task.get("dedupe_history"), True) else ""
    priority_text += " · 整包优先" if parse_bool(task.get("prefer_full_pack"), True) else ""
    filters = []
    if task.get("include"):
        filters.append("包含 " + ", ".join(str(item) for item in task.get("include") or []))
    if task.get("exclude"):
        filters.append("排除 " + ", ".join(str(item) for item in task.get("exclude") or []))
    if not filters:
        filters.append("未设置额外关键词过滤")
    transfer_status = _transfer_status(task)
    last_message = str(task.get("last_message") or "尚未运行，点击“立即检查”预览候选。")
    message_type = "error" if task.get("status") == "error" else (
        "success" if (task.get("last_download_count") or task.get("status") == "completed") else "info"
    )
    cleanup_pending = task.get("cleanup_pending") or {}
    if _cleanup_pending_active(cleanup_pending):
        cleanup_scope = "下载任务及下载文件" if cleanup_pending.get("delete_files") \
            else "下载任务（保留文件）"
        maintenance = [
            _alert("warning", f"等待确认：将清理{cleanup_scope}，重置进度后立即重新检查；媒体库成品不会删除。"),
            _button_row([
                _action("确认清理并重处理", "mdi-delete-sweep", "error",
                        f"plugin/{PLUGIN_ID}/tasks/{task['id']}/cleanup/confirm"),
                _action("取消", "mdi-close", "secondary",
                        f"plugin/{PLUGIN_ID}/tasks/{task['id']}/cleanup/cancel"),
            ]),
        ]
    else:
        maintenance = [
            {"component": "div", "props": {"class": "text-caption text-medium-emphasis mb-2"},
             "text": "清理仅作用于这个插件明确创建的下载 Hash，不会删除媒体库成品。"},
            _button_row([
                _action("清理任务，保留文件", "mdi-broom", "secondary",
                        f"plugin/{PLUGIN_ID}/tasks/{task['id']}/cleanup/prepare"),
                _action("清理任务和下载文件", "mdi-delete-sweep", "error",
                        f"plugin/{PLUGIN_ID}/tasks/{task['id']}/cleanup/prepare-files"),
                _action("移入回收站", "mdi-delete-outline", "error",
                        f"plugin/{PLUGIN_ID}/tasks/{task['id']}/delete"),
            ]),
        ]
    return {
        "component": "VExpansionPanel",
        "content": [
            {"component": "VExpansionPanelTitle", "content": [
                {"component": "div",
                 "props": {"class": "d-flex flex-wrap align-center ga-3 w-100 pr-3"},
                 "content": [
                     _avatar(_task_icon(task), status_color, 38),
                     {"component": "div", "props": {"class": "flex-grow-1", "style": "min-width:180px"},
                      "content": [
                          {"component": "div", "props": {"class": "text-subtitle-1 font-weight-medium",
                                                            "style": "word-break:break-word"},
                           "text": str(task.get("name") or "未命名")},
                          {"component": "div", "props": {"class": "text-caption text-medium-emphasis"},
                           "text": " · ".join(media_bits) + " · " + progress_summary},
                      ]},
                     {"component": "div", "props": {"class": "d-flex flex-wrap ga-2"}, "content": [
                         _chip(status, status_color),
                         _chip("自动下载" if task.get("auto_download") else "仅预览",
                               "warning" if task.get("auto_download") else "info"),
                     ]},
                 ]},
            ]},
            {"component": "VExpansionPanelText", "content": [
                _task_progress(target, missing, downloaded, status_color),
                _alert(message_type, last_message),
                _button_row([
                    _action("立即检查", "mdi-magnify", "primary",
                            f"plugin/{PLUGIN_ID}/tasks/{task['id']}/run"),
                    _action(toggle_text, toggle_icon, "secondary",
                            f"plugin/{PLUGIN_ID}/tasks/{task['id']}/toggle"),
                    _action(auto_text, "mdi-download", "warning",
                            f"plugin/{PLUGIN_ID}/tasks/{task['id']}/auto"),
                ]),
                {"component": "VRow", "props": {"class": "mt-2"}, "content": [
                    _col(12, 6, _detail_card("搜索规则", "mdi-database-search", [
                        _line("搜索词", " / ".join(task_search_keywords(task)) or "节目名称"),
                        _line("站点", ", ".join(str(item) for item in task.get("sites") or [])
                              or "系统活动站点"),
                        _line("过滤", "；".join(filters)),
                        _line("标题匹配", "严格匹配" if task.get("strict_title_match") else "宽松匹配"),
                    ])),
                    _col(12, 6, _detail_card("下载与整理", "mdi-folder-move", [
                        _line("候选择优", priority_text),
                        _line("下载位置", str(task.get("save_path") or "站点或系统默认")),
                        _line("媒体分类", str(task.get("media_category") or "使用媒体库默认规则")),
                        _line("整理状态", transfer_status),
                        _line("丢失补偿", str(task.get("last_repair_message") or "尚未发现异常")),
                    ])),
                ]},
                _detail_card("最近一次运行", "mdi-history", [
                    _line("检查时间", str(task.get("last_run_at") or "-")),
                    _line("候选 / 下载 / 重复", f"{task.get('last_match_count') or 0} / "
                          f"{task.get('last_download_count') or 0} / {task.get('last_duplicate_count') or 0}"),
                    _line("结果说明", str(task.get("last_reason_summary") or "暂无详细原因")),
                ]),
                {"component": "VDivider", "props": {"class": "my-4"}},
                {"component": "div", "props": {"class": "text-subtitle-2 font-weight-medium mb-2"},
                 "text": "维护与清理"},
                *maintenance,
            ]},
        ],
    }


def _task_progress(target: Set[int], missing: Set[int], downloaded: Set[int],
                   color: str) -> Dict[str, Any]:
    if not target:
        return {
            "component": "VSheet",
            "props": {"rounded": "lg", "class": "pa-3 mb-3", "color": "info"},
            "content": [
                {"component": "div", "props": {"class": "d-flex align-center ga-2"}, "content": [
                    _icon("mdi-infinity", "text-info"),
                    {"component": "div", "props": {"class": "text-body-2 font-weight-medium"},
                     "text": "持续追更模式"},
                ]},
                {"component": "div", "props": {"class": "text-caption text-medium-emphasis mt-1"},
                 "text": f"已记录集数：{episodes_text(downloaded) or '暂无'}"},
            ],
        }
    acquired = len(target) - len(missing)
    percentage = round(acquired * 100 / len(target))
    return {
        "component": "VSheet",
        "props": {"rounded": "lg", "class": "pa-3 mb-3", "border": True},
        "content": [
            {"component": "div", "props": {"class": "d-flex justify-space-between ga-3 mb-2"},
             "content": [
                 {"component": "span", "props": {"class": "text-body-2 font-weight-medium"},
                  "text": f"已获取 {acquired}/{len(target)}"},
                 {"component": "span", "props": {"class": "text-caption text-medium-emphasis"},
                  "text": f"缺少 {episodes_text(missing) or '无'}"},
             ]},
            {"component": "VProgressLinear", "props": {
                "model-value": percentage, "color": color, "height": 8, "rounded": True,
            }},
        ],
    }


def _activity_collection(tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    candidates = _candidate_rows(tasks)
    run_logs = _run_log_rows(tasks)
    return _page_card(
        "最近活动",
        "候选和运行原因默认收起，需要排查时再展开；手机端会自动切换为卡片。",
        "mdi-pulse",
        [{
            "component": "VExpansionPanels",
            "props": {"variant": "accordion"},
            "content": [
                _activity_panel(
                    "最近候选", f"{len(candidates)} 条", "mdi-format-list-checks",
                    _recent_results(candidates),
                ),
                _activity_panel(
                    "详细运行日志", f"{len(run_logs)} 条", "mdi-text-box-search-outline",
                    _run_log_table(run_logs),
                ),
            ],
        }],
    )


def _candidate_rows(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for task in tasks:
        for result in (task.get("last_results") or [])[:10]:
            state = "已下载" if result.get("downloaded") else (
                "重复跳过" if result.get("duplicate") or result.get("skip_reason") else "候选"
            )
            rows.append({
                "节目": task.get("name") or "-", "站点": result.get("site") or "-",
                "标题": result.get("title") or "-", "集数": result.get("episodes") or "未知",
                "做种": result.get("seeders") or 0, "促销": "免费" if result.get("free") else "-",
                "状态": state, "原因": result.get("download_error") or result.get("skip_reason")
                or result.get("selection_reason") or result.get("reason") or "-",
            })
    return rows[:50]


def _run_log_rows(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for task in tasks:
        for entry in task.get("run_logs") or []:
            rows.append({
                "时间": entry.get("time") or "-", "节目": task.get("name") or "-",
                "阶段": entry.get("stage") or "-", "动作": entry.get("action") or "-",
                "站点": entry.get("site") or "-", "资源": entry.get("title") or "-",
                "集数": entry.get("episodes") or "-", "做种": entry.get("seeders") or 0,
                "原因": entry.get("reason") or "-",
            })
    rows.sort(key=lambda item: str(item.get("时间") or ""), reverse=True)
    return rows[:100]


def _recent_results(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return _empty_state("mdi-magnify-close", "暂无候选", "运行一次任务后，候选资源会显示在这里。")
    desktop_rows = []
    mobile_items = []
    for row in rows:
        state_color = {"已下载": "success", "重复跳过": "secondary"}.get(row["状态"], "info")
        desktop_rows.append({"component": "tr", "content": [
            _td(row["节目"], "text-no-wrap"),
            {"component": "td", "content": [
                {"component": "div", "props": {"class": "text-body-2",
                                                  "style": "min-width:260px;word-break:break-word"},
                 "text": str(row["标题"])},
                {"component": "div", "props": {"class": "text-caption text-medium-emphasis mt-1"},
                 "text": str(row["站点"])},
            ]},
            _td(row["集数"], "text-no-wrap"), _td(row["做种"], "text-no-wrap"),
            _chip_td(row["促销"], "success" if row["促销"] == "免费" else "secondary"),
            _chip_td(row["状态"], state_color), _td(row["原因"]),
        ]})
        mobile_items.append(_mobile_record(
            row["标题"], f"{row['节目']} · {row['站点']}",
            [_chip(str(row["集数"]), "primary", "mdi-television-classic"),
             _chip(f"做种 {row['做种']}", "info", "mdi-account-multiple"),
             _chip(str(row["状态"]), state_color)],
            [_line("促销", str(row["促销"])), _line("原因", str(row["原因"]))],
        ))
    return _responsive_table(
        ["节目", "资源 / 站点", "集数", "做种", "促销", "状态", "原因"],
        desktop_rows, mobile_items,
    )


def _run_log_table(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return _empty_state("mdi-text-box-search-outline", "暂无运行日志", "任务运行后的筛选与下载原因会显示在这里。")
    desktop_rows = []
    mobile_items = []
    for row in rows:
        desktop_rows.append({"component": "tr", "content": [
            _td(row["时间"], "text-no-wrap"),
            _td(f"{row['节目']}\n{row['阶段']} · {row['动作']}"),
            _td(f"{row['资源']}\n{row['站点']}"),
            _td(row["集数"], "text-no-wrap"), _td(row["做种"], "text-no-wrap"), _td(row["原因"]),
        ]})
        mobile_items.append(_mobile_record(
            f"{row['阶段']} · {row['动作']}", f"{row['时间']} · {row['节目']}",
            [_chip(str(row["站点"]), "secondary", "mdi-database"),
             _chip(str(row["集数"]), "primary", "mdi-television-classic"),
             _chip(f"做种 {row['做种']}", "info", "mdi-account-multiple")],
            [_line("资源", str(row["资源"])), _line("原因", str(row["原因"]))],
        ))
    return _responsive_table(
        ["时间", "节目 / 阶段", "资源 / 站点", "集数", "做种", "原因"],
        desktop_rows, mobile_items,
    )


def _trash_collection(tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    panels = []
    for task in tasks:
        panels.append({
            "component": "VExpansionPanel",
            "content": [
                {"component": "VExpansionPanelTitle", "content": [
                    {"component": "div", "props": {"class": "d-flex align-center ga-3 w-100 pr-3"},
                     "content": [
                         _avatar("mdi-delete-clock-outline", "secondary", 36),
                         {"component": "div", "props": {"class": "flex-grow-1", "style": "min-width:0"},
                          "content": [
                              {"component": "div", "props": {"class": "text-body-1 font-weight-medium"},
                               "text": str(task.get("name") or task.get("id"))},
                              {"component": "div", "props": {"class": "text-caption text-medium-emphasis"},
                               "text": f"删除于 {task.get('deleted_at') or '-'}"},
                          ]},
                         _chip(str(task.get("type") or "任务"), "secondary"),
                     ]},
                ]},
                {"component": "VExpansionPanelText", "content": [
                    _line("原任务 ID", str(task.get("id") or "-")),
                    _line("已下载集数", episodes_text(parse_episodes(task.get("downloaded_episodes"))) or "-"),
                    _button_row([_action(
                        "恢复任务", "mdi-restore", "success",
                        f"plugin/{PLUGIN_ID}/trash/{task['id']}/restore",
                    )]),
                ]},
            ],
        })
    return _page_card(
        "回收站", "删除的任务可恢复，最多显示最近 20 条。", "mdi-delete-restore",
        [{"component": "VExpansionPanels", "props": {"variant": "accordion"}, "content": panels}],
        count=len(tasks),
    )


def _page_card(title: str, subtitle: str, icon: str, content: List[Dict[str, Any]],
               count: Optional[int] = None) -> Dict[str, Any]:
    heading = _section_heading(title, subtitle, icon)
    if count is not None:
        heading["content"].extend([{"component": "VSpacer"}, _chip(f"{count} 个", "primary")])
    return {
        "component": "VCard",
        "props": {"variant": "flat", "rounded": "xl", "border": True, "class": "mb-4"},
        "content": [{"component": "VCardText", "props": {"class": "pa-3 pa-sm-4"}, "content": [
            heading,
            {"component": "div", "props": {"class": "mt-3"}, "content": content},
        ]}],
    }


def _section_heading(title: str, subtitle: str, icon: str) -> Dict[str, Any]:
    return {
        "component": "div", "props": {"class": "d-flex align-center ga-3"},
        "content": [
            _avatar(icon, "primary", 38),
            {"component": "div", "props": {"style": "min-width:0"}, "content": [
                {"component": "div", "props": {"class": "text-subtitle-1 font-weight-bold"}, "text": title},
                {"component": "div", "props": {"class": "text-caption text-medium-emphasis"},
                 "text": subtitle},
            ]},
        ],
    }


def _activity_panel(title: str, subtitle: str, icon: str,
                    content: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "component": "VExpansionPanel",
        "content": [
            {"component": "VExpansionPanelTitle", "content": [
                {"component": "div", "props": {"class": "d-flex align-center ga-3 w-100 pr-3"},
                 "content": [
                     _avatar(icon, "primary", 34),
                     {"component": "span", "props": {"class": "font-weight-medium"}, "text": title},
                     {"component": "VSpacer"},
                     _chip(subtitle, "secondary"),
                 ]},
            ]},
            {"component": "VExpansionPanelText", "content": [content]},
        ],
    }


def _detail_card(title: str, icon: str, content: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "component": "VSheet",
        "props": {"rounded": "lg", "border": True, "class": "pa-3 mb-3 h-100"},
        "content": [
            {"component": "div", "props": {"class": "d-flex align-center ga-2 mb-2"}, "content": [
                _icon(icon, "text-primary"),
                {"component": "div", "props": {"class": "text-subtitle-2 font-weight-medium"},
                 "text": title},
            ]},
            *content,
        ],
    }


def _button_row(actions: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"component": "div", "props": {"class": "d-flex flex-wrap ga-2 mt-3"}, "content": actions}


def _responsive_table(headers: List[str], desktop_rows: List[Dict[str, Any]],
                      mobile_items: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "component": "div",
        "content": [
            {"component": "div", "props": {"class": "d-none d-md-block overflow-x-auto"}, "content": [{
                "component": "VTable", "props": {"hover": True, "density": "comfortable"}, "content": [
                    _table_header(headers), {"component": "tbody", "content": desktop_rows},
                ],
            }]},
            {"component": "div", "props": {"class": "d-flex d-md-none flex-column ga-3"},
             "content": mobile_items},
        ],
    }


def _table_header(headers: List[str]) -> Dict[str, Any]:
    return {"component": "thead", "content": [{"component": "tr", "content": [
        {"component": "th", "props": {"class": "text-start text-no-wrap"}, "text": header}
        for header in headers
    ]}]}


def _td(text: Any, class_name: str = "") -> Dict[str, Any]:
    item = {"component": "td", "text": str(text if text is not None else "-")}
    if class_name:
        item["props"] = {"class": class_name}
    return item


def _chip_td(text: Any, color: str) -> Dict[str, Any]:
    return {"component": "td", "props": {"class": "text-no-wrap"},
            "content": [_chip(str(text), color)]}


def _mobile_record(title: Any, subtitle: Any, chips: List[Dict[str, Any]],
                   details: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "component": "VSheet", "props": {"rounded": "lg", "border": True, "class": "pa-3"},
        "content": [
            {"component": "div", "props": {"class": "text-body-2 font-weight-medium",
                                              "style": "word-break:break-word"}, "text": str(title)},
            {"component": "div", "props": {"class": "text-caption text-medium-emphasis mt-1"},
             "text": str(subtitle)},
            {"component": "div", "props": {"class": "d-flex flex-wrap ga-2 mt-3"}, "content": chips},
            {"component": "div", "props": {"class": "mt-2"}, "content": details},
        ],
    }


def _empty_state(icon: str, title: str, subtitle: str) -> Dict[str, Any]:
    return {
        "component": "div", "props": {"class": "d-flex flex-column align-center text-center py-8 ga-2"},
        "content": [
            _icon(icon, "text-disabled", 42),
            {"component": "div", "props": {"class": "text-body-1 font-weight-medium"}, "text": title},
            {"component": "div", "props": {"class": "text-caption text-medium-emphasis"},
             "text": subtitle},
        ],
    }


def _avatar(icon: str, color: str, size: int) -> Dict[str, Any]:
    return {
        "component": "VAvatar",
        "props": {"variant": "tonal", "color": color, "rounded": "lg", "size": size,
                  "class": "flex-shrink-0"},
        "content": [_icon(icon, size=max(18, size // 2))],
    }


def _icon(icon: str, class_name: str = "", size: int = 18) -> Dict[str, Any]:
    props: Dict[str, Any] = {"icon": icon, "size": size}
    if class_name:
        props["class"] = class_name
    return {"component": "VIcon", "props": props}


def _task_icon(task: Dict[str, Any]) -> str:
    if task.get("status") == "completed":
        return "mdi-check"
    if task.get("status") == "running":
        return "mdi-radar"
    if task.get("status") == "error" or task.get("last_transfer_status") == "failed":
        return "mdi-alert-circle-outline"
    if not task.get("enabled"):
        return "mdi-pause"
    return "mdi-movie-search-outline" if task.get("type") == "电影" else "mdi-television-play"


def _task_status_label(status: Any) -> str:
    return {"active": "追更中", "paused": "已暂停", "running": "检查中", "completed": "已完成",
            "error": "异常"}.get(str(status or ""), str(status or "未知"))


def _transfer_status(task: Dict[str, Any]) -> str:
    text = {
        "waiting": "等待下载完成", "queued": "已加入整理队列", "completed": "整理完成",
        "failed": "整理失败",
    }.get(str(task.get("last_transfer_status") or ""), "尚无整理记录")
    if task.get("last_transfer_message") and str(task.get("last_transfer_message")) != text:
        text += f" · {task.get('last_transfer_message')}"
    return text


def _row(content: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"component": "VRow", "props": {"dense": True}, "content": content}


def _col(cols: int, md: int, child: Dict[str, Any]) -> Dict[str, Any]:
    return {"component": "VCol", "props": {"cols": cols, "md": md}, "content": [child]}


def _field(model: str, label: str, placeholder: str = "", hint: str = "") -> Dict[str, Any]:
    props: Dict[str, Any] = {
        "model": model, "label": label, "placeholder": placeholder,
        "variant": "outlined", "density": "comfortable", "clearable": True,
    }
    if hint:
        props.update({"hint": hint, "persistent-hint": True})
    return {"component": "VTextField", "props": props}


def _number(model: str, label: str, minimum: int, maximum: int,
            hint: str = "") -> Dict[str, Any]:
    props: Dict[str, Any] = {
        "model": model, "label": label, "type": "number", "min": minimum, "max": maximum,
        "variant": "outlined", "density": "comfortable",
    }
    if hint:
        props.update({"hint": hint, "persistent-hint": True})
    return {"component": "VTextField", "props": props}


def _textarea(model: str, label: str, placeholder: str, hint: str = "") -> Dict[str, Any]:
    props: Dict[str, Any] = {
        "model": model, "label": label, "placeholder": placeholder, "rows": 3,
        "auto-grow": True, "variant": "outlined", "density": "comfortable", "clearable": True,
    }
    if hint:
        props.update({"hint": hint, "persistent-hint": True})
    return {"component": "VTextarea", "props": props}


def _select(model: str, label: str, items: List[Dict[str, Any]], hint: str = "",
            multiple: bool = False) -> Dict[str, Any]:
    props: Dict[str, Any] = {
        "model": model, "label": label, "items": items, "variant": "outlined",
        "density": "comfortable",
    }
    if multiple:
        props.update({"multiple": True, "chips": True, "closable-chips": True, "clearable": True})
    if hint:
        props.update({"hint": hint, "persistent-hint": True})
    return {"component": "VSelect", "props": props}


def _switch(model: str, label: str, hint: str, color: str = "primary") -> Dict[str, Any]:
    return {"component": "VSwitch", "props": {
        "model": model, "label": label, "hint": hint, "persistent-hint": True,
        "color": color, "density": "comfortable", "inset": True,
    }}


def _alert(alert_type: str, text: str) -> Dict[str, Any]:
    return {"component": "VAlert", "props": {
        "type": alert_type, "variant": "tonal", "density": "comfortable", "rounded": "lg",
        "class": "mb-3",
    }, "text": text}


def _chip(text: str, color: str, icon: Optional[str] = None) -> Dict[str, Any]:
    props: Dict[str, Any] = {"color": color, "size": "small", "variant": "tonal"}
    if icon:
        props["prepend-icon"] = icon
    return {"component": "VChip", "props": props, "text": text}


def _line(label: str, value: str) -> Dict[str, Any]:
    return {"component": "div", "props": {"class": "d-flex align-start py-1 ga-2"}, "content": [
        {"component": "div", "props": {
            "class": "text-caption text-medium-emphasis flex-shrink-0", "style": "width:6.5em",
        }, "text": label},
        {"component": "div", "props": {
            "class": "text-body-2 flex-grow-1", "style": "min-width:0;word-break:break-word",
        }, "text": value},
    ]}


def _action(text: str, icon: str, color: str, api: str) -> Dict[str, Any]:
    return {"component": "VBtn", "props": {"variant": "tonal", "color": color,
                                                "prepend-icon": icon, "size": "small", "rounded": "lg",
                                                "class": "text-none"}, "text": text,
            "events": {"click": {"api": api, "method": "post"}}}


def _status_color(status: Any) -> str:
    return {"active": "success", "running": "primary", "completed": "info", "paused": "secondary",
            "error": "error"}.get(str(status or ""), "secondary")
