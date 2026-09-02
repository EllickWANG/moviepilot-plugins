"""完全由插件维护的站点直搜订阅。"""

from __future__ import annotations

import copy
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

from apscheduler.triggers.cron import CronTrigger
from fastapi import Body

from app import schemas
from app.chain.download import DownloadChain
from app.chain.search import SearchChain
from app.core.config import global_vars, settings
from app.core.context import Context, MediaInfo
from app.core.metainfo import MetaInfo
from app.db.downloadhistory_oper import DownloadHistoryOper
from app.db.site_oper import SiteOper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import MediaType, NotificationType

from .core import (
    MAX_RESOURCE_HISTORY,
    candidate_score,
    candidate_sort_key,
    episodes_text,
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
    words_match,
)


PLUGIN_ID = "directsearchsubscribe"
TASKS_KEY = "tasks_v2"
TRASH_KEY = "tasks_v2_trash"
LEGACY_TASKS_KEY = "direct_subscribes"
MAX_TRASH = 100


class directsearchsubscribe(_PluginBase):
    """自包含的直搜订阅插件。"""

    plugin_name = "直搜订阅"
    plugin_desc = "手工维护节目与集数，定时直搜站点；不创建系统订阅，也不访问媒体信息源。"
    plugin_icon = "mdi-magnify-scan"
    plugin_version = "2.1.0"
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

        # 2.0.0 创建的任务只有插件标签，MoviePilot 下载管理会将其过滤掉。
        # 标签追加是幂等操作，每次加载时顺便修复仍保留在下载器中的历史任务。
        self._repair_download_tags()

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
            _api("/tasks/{task_id}/delete", self.api_delete_task, ["POST", "DELETE"], "移入回收站"),
            _api("/trash/{task_id}/restore", self.api_restore_task, ["POST"], "恢复直搜任务"),
            _api("/tasks/{task_id}/results", self.api_task_results, ["GET"], "查询最近候选"),
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
                            _col(12, 4, _switch(
                                "accept_unknown_episode", "允许未知集数下载",
                                "高风险：标题解析不出集数时也可自动下载；每个任务最多选择一个",
                            )),
                            _col(12, 4, _switch("save_task_now", "保存为插件任务", "保存配置时执行一次并自动复位")),
                            _col(12, 4, _switch("run_after_save", "保存后立即检查", "创建或更新成功后启动后台检查")),
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
        }

    def get_page(self) -> Optional[List[dict]]:
        tasks = list(self._load_tasks().values())
        tasks.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        trash = list(self._load_trash().values())
        legacy = self.get_data(LEGACY_TASKS_KEY) or {}
        active = sum(1 for task in tasks if task.get("enabled") and task.get("status") != "completed")
        auto = sum(1 for task in tasks if task.get("auto_download"))
        contents = [_hero(self, len(tasks), active, auto), _task_collection(tasks), _recent_results(tasks)]
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

        known_identities = self._known_resource_identities()
        if parse_bool(task.get("dedupe_history"), True):
            history_identities, history_episodes, history_has_movie = self._history_snapshot(task)
            known_identities.update(history_identities)
            downloaded = parse_episodes(task.get("downloaded_episodes"))
            recovered = history_episodes.difference(downloaded)
            if recovered:
                downloaded.update(recovered)
                self._update_runtime(task_id, downloaded_episodes=sorted(downloaded))
                task = self._load_tasks().get(task_id) or task
            target = target_episodes(task)
            if history_has_movie or target and target.issubset(downloaded):
                message = "下载历史已覆盖全部目标，未重复搜索下载"
                self._update_runtime(
                    task_id, status="completed", last_status="success", last_run_at=now_text(),
                    last_message=message, last_download_count=0, last_duplicate_count=len(recovered),
                )
                return

        self._update_runtime(task_id, status="running", last_status="running",
                             last_run_at=now_text(), last_message="正在直接搜索站点")
        try:
            candidates, search_errors, duplicate_count = self._search_task(
                task, stop_event, known_identities
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
                    task, candidates, stop_event, known_identities
                )
                duplicate_count += runtime_duplicates
            else:
                downloads = []
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
            self._update_runtime(task_id, status=status,
                                 last_status="success" if not search_errors or results else "warning",
                                 last_message=message, last_download_count=len(downloads),
                                 last_duplicate_count=duplicate_count)
            if downloads and self._notify:
                self.post_message(mtype=NotificationType.Plugin,
                                  title=f"直搜订阅：{latest.get('name')}", text=message)
            logger.info(f"直搜订阅 {latest.get('name')} 完成：{message}")
        except Exception as err:
            logger.error(f"直搜订阅 {task.get('name')} 执行失败：{err}", exc_info=True)
            self._update_runtime(task_id, status="error", last_status="error", last_message=str(err))

    def _search_task(self, task: Dict[str, Any], stop_event: threading.Event,
                     known_identities: Set[str]) \
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
                    continue
                for context in found:
                    prepared = _prepare_candidate(task, context, missing, downloaded)
                    if not prepared:
                        continue
                    fingerprint = prepared[1]["fingerprint"]
                    identity = prepared[1]["resource_identity"] or fingerprint
                    if fingerprint in downloaded_fingerprints or identity in known_identities:
                        duplicate_count += 1
                        continue
                    existing = contexts.get(identity)
                    if existing:
                        duplicate_count += 1
                        if _candidate_priority_key(task, prepared[1], missing) \
                                > _candidate_priority_key(task, existing[1], missing):
                            contexts[identity] = prepared
                        continue
                    contexts[identity] = prepared
        ordered = sorted(
            contexts.values(),
            key=lambda item: _candidate_priority_key(task, item[1], missing),
            reverse=True,
        )
        return ordered[:50], errors, duplicate_count

    def _download_candidates(self, task: Dict[str, Any],
                             candidates: List[Tuple[Context, Dict[str, Any]]],
                             stop_event: threading.Event,
                             known_identities: Set[str]) -> Tuple[List[Dict[str, Any]], int]:
        """串行执行跨任务下载，避免两个手动任务竞态添加同一资源。"""
        with self.__class__._download_lock:
            known_identities.update(self._known_resource_identities())
            return self._download_candidates_locked(
                task, candidates, stop_event, known_identities
            )

    def _download_candidates_locked(self, task: Dict[str, Any],
                                    candidates: List[Tuple[Context, Dict[str, Any]]],
                                    stop_event: threading.Event,
                                    known_identities: Set[str]) -> Tuple[List[Dict[str, Any]], int]:
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
                break
            if task.get("type") == MediaType.MOVIE.value and downloads:
                break
            if stop_event.is_set() or global_vars.is_system_stopped:
                break
            current = self._load_tasks().get(task_id)
            if not current or not current.get("auto_download"):
                break
            fingerprint = result["fingerprint"]
            identity = result.get("resource_identity") or fingerprint
            if fingerprint in fingerprints or identity in known_identities:
                duplicate_count += 1
                continue
            candidate_episodes = set(result.get("episode_numbers") or [])
            selected: Optional[Set[int]] = None
            if task.get("type") == MediaType.TV.value:
                selected = candidate_episodes.difference(downloaded)
                if target:
                    selected.intersection_update(target)
                if candidate_episodes and not selected:
                    continue
                if not candidate_episodes and not task.get("accept_unknown_episode"):
                    continue
                if not candidate_episodes:
                    if unknown_downloaded:
                        result["skip_reason"] = "已选择过未知集数资源"
                        duplicate_count += 1
                        continue
                    selected = None
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
                continue
            if is_duplicate_download_message(error):
                result["duplicate"] = True
                result["skip_reason"] = error or "下载任务已存在"
                duplicate_count += 1
                fingerprints.add(fingerprint)
                known_identities.add(identity)
                if selected:
                    downloaded.update(selected)
                if not candidate_episodes and task.get("type") == MediaType.TV.value:
                    unknown_downloaded = True
                self._update_runtime(
                    task_id,
                    downloaded_episodes=sorted(downloaded),
                    downloaded_fingerprints=list(fingerprints)[-MAX_RESOURCE_HISTORY:],
                    last_results=[item[1] for item in candidates],
                )
                continue
            if not download_hash:
                result["download_error"] = error or "添加下载失败"
                continue
            if selected:
                downloaded.update(selected)
            fingerprints.add(fingerprint)
            known_identities.add(identity)
            record = {
                "time": now_text(), "fingerprint": fingerprint, "hash": download_hash,
                "site": result.get("site"), "title": result.get("title"),
                "episodes": episodes_text(selected or []), "size": result.get("size") or 0,
                "resource_identity": identity,
            }
            records.append(record)
            if not candidate_episodes and task.get("type") == MediaType.TV.value:
                unknown_downloaded = True
            result["downloaded"] = True
            downloads.append(record)
            self._update_runtime(
                task_id,
                downloaded_episodes=sorted(downloaded),
                downloaded_fingerprints=list(fingerprints)[-MAX_RESOURCE_HISTORY:],
                download_records=records[-MAX_RESOURCE_HISTORY:],
                last_results=[item[1] for item in candidates],
            )
        self._update_runtime(task_id, last_results=[item[1] for item in candidates])
        return downloads, duplicate_count

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
        for history in histories:
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
        "save_path": config.get("save_path"), "media_category": config.get("media_category"),
        "enabled": config.get("task_enabled"), "auto_download": config.get("auto_download"),
        "strict_title_match": config.get("strict_title_match"),
        "accept_unknown_episode": config.get("accept_unknown_episode"),
    }


def _prepare_candidate(task: Dict[str, Any], context: Context, missing: Set[int],
                       downloaded: Set[int]) -> Optional[Tuple[Context, Dict[str, Any]]]:
    torrent = context.torrent_info
    if not torrent or not torrent.title:
        return None
    if not title_matches(task, torrent.title, torrent.description or ""):
        return None
    if not words_match(task, torrent.title, torrent.description or ""):
        return None
    media_type = MediaType(task.get("type") or MediaType.TV.value)
    meta = MetaInfo(title=torrent.title, subtitle=torrent.description)
    meta.type = media_type
    task_year = str(task.get("year") or "").strip()
    parsed_year = str(getattr(meta, "year", None) or "").strip()
    if task_year and parsed_year and task_year != parsed_year:
        return None
    if media_type == MediaType.TV:
        task_season = parse_int(task.get("season"), minimum=1)
        parsed_season = meta.begin_season
        if task_season and parsed_season and task_season != parsed_season:
            return None
        if task_season and not parsed_season:
            meta.begin_season = task_season
        elif not meta.begin_season:
            meta.begin_season = 1
    episodes = set(meta.episode_list or [])
    target = target_episodes(task)
    if media_type == MediaType.TV and episodes:
        needed = missing if target else episodes.difference(downloaded)
        if not episodes.intersection(needed):
            return None
    seeders = int(torrent.seeders or 0)
    if seeders < (parse_int(task.get("min_seeders"), 0, minimum=0) or 0):
        return None
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
    return context, {
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
    }


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
            {"component": "VCardTitle", "text": f"直搜订阅 2.0 · {state}"},
            {"component": "VCardSubtitle", "text": "插件独立维护 · 站点直搜 · 不使用外部媒体信息源"},
            {"component": "VCardText", "content": [
                {"component": "div", "props": {"class": "d-flex flex-wrap ga-2"}, "content": [
                    _chip(f"任务 {total}", "primary"), _chip(f"活动 {active}", "success"),
                    _chip(f"自动下载 {auto}", "warning"), _chip(f"周期 {plugin._cron}", "info"),
                ]},
                {"component": "div", "props": {"class": "text-caption mt-3"},
                 "text": "新建或更新节目请打开插件配置；本页可立即检查、暂停、切换自动下载或移入回收站。"},
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
                _line("最近检查", str(task.get("last_run_at") or "-")),
                {"component": "div", "props": {"class": "d-flex flex-wrap ga-2 mt-3"}, "content": [
                    _action("立即检查", "mdi-magnify", "primary", f"plugin/{PLUGIN_ID}/tasks/{task['id']}/run"),
                    _action(toggle_text, toggle_icon, "secondary", f"plugin/{PLUGIN_ID}/tasks/{task['id']}/toggle"),
                    _action(auto_text, "mdi-download", "warning", f"plugin/{PLUGIN_ID}/tasks/{task['id']}/auto"),
                    _action("移入回收站", "mdi-delete-outline", "error", f"plugin/{PLUGIN_ID}/tasks/{task['id']}/delete"),
                ]},
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
                         "促销": "免费" if result.get("free") else "-", "状态": state})
    headers = [{"title": key, "key": key}
               for key in ["节目", "站点", "标题", "集数", "做种", "促销", "状态"]]
    return {
        "component": "VCard", "props": {"variant": "outlined", "class": "mb-4"},
        "content": [{"component": "VCardTitle", "text": "最近候选"},
                    {"component": "VDataTable", "props": {"headers": headers, "items": rows[:50],
                                                               "items-per-page": 10, "density": "compact",
                                                               "no-data-text": "暂无候选结果"}}],
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
