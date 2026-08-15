from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from apscheduler.triggers.cron import CronTrigger

from app import schemas
from app.chain.download import DownloadChain
from app.chain.search import SearchChain
from app.core.context import Context, MediaInfo
from app.core.metainfo import MetaInfo
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import MediaType
from app.utils.string import StringUtils


DEFAULT_TASKS = [
    {
        "id": "rezero-s04",
        "name": "Re 从零开始的异世界生活 第四季",
        "keyword": "Re 从零开始的异世界生活 第四季",
        "type": "tv",
        "season": 4,
        "episodes": "",
        "sites": [],
        "include": [],
        "exclude": ["合集", "国语"],
        "downloader": "",
        "save_path": "",
        "label": "直搜订阅",
        "max_downloads": 1,
        "pages": 1,
        "enabled": False
    }
]

PLUGIN_ID = "directsearchsubscribe"
HISTORY_KEY = "history"
SEEN_KEY = "seen"
RUN_KEY = "last_run"
MAX_HISTORY = 300

_lock = threading.Lock()


class directsearchsubscribe(_PluginBase):
    plugin_name = "直搜订阅"
    plugin_desc = "按自定义关键词直接搜索站点并自动下载，不依赖 TMDB、豆瓣或 Bangumi 识别。"
    plugin_icon = "mdi-magnify-scan"
    plugin_version = "1.0.0"
    plugin_author = "Ellick"
    plugin_order = 30
    auth_level = 1

    _enabled = False
    _cron = "*/30 * * * *"
    _tasks_json = ""
    _dry_run = False
    _notify = False

    def init_plugin(self, config: dict = None):
        config = config or {}
        self._enabled = bool(config.get("enabled", False))
        self._cron = str(config.get("cron") or self._cron).strip()
        self._tasks_json = config.get("tasks_json") or _default_tasks_json()
        self._dry_run = bool(config.get("dry_run", False))
        self._notify = bool(config.get("notify", False))

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {
                "path": "/run",
                "endpoint": self.api_run,
                "methods": ["POST", "GET"],
                "auth": "bear",
                "summary": "运行直搜订阅",
                "description": "立即执行所有启用的直搜订阅任务。",
            },
            {
                "path": "/reset/{task_id}",
                "endpoint": self.api_reset,
                "methods": ["POST", "DELETE"],
                "auth": "bear",
                "summary": "重置直搜订阅去重",
                "description": "清空指定任务的已下载去重记录。",
            },
        ]

    def get_form(self) -> Tuple[Optional[List[dict]], Dict[str, Any]]:
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "enabled", "label": "启用插件"},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "dry_run", "label": "演练模式"},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "notify", "label": "下载成功通知"},
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
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "cron",
                                            "label": "执行周期",
                                            "placeholder": "*/30 * * * *",
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
                                            "model": "tasks_json",
                                            "label": "直搜订阅任务 JSON",
                                            "rows": 18,
                                            "auto-grow": True,
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VAlert",
                        "props": {"type": "info", "variant": "tonal"},
                        "text": "任务字段：id/name/keyword/type(tv|movie)/season/episodes/sites/include/exclude/downloader/save_path/label/max_downloads/pages/enabled。episodes 支持 1-12,14。",
                    },
                ],
            }
        ], {
            "enabled": False,
            "cron": self._cron,
            "dry_run": False,
            "notify": False,
            "tasks_json": _default_tasks_json(),
        }

    def get_page(self) -> Optional[List[dict]]:
        tasks = self._load_tasks()
        history = self.get_data(HISTORY_KEY) or []
        last_run = self.get_data(RUN_KEY) or {}
        task_rows = [
            {
                "任务": task.get("name") or task.get("keyword") or task.get("id"),
                "类型": _media_type_label(task.get("type")),
                "状态": "启用" if task.get("enabled", True) else "停用",
                "关键词": task.get("keyword") or "",
                "集数": task.get("episodes") or "",
            }
            for task in tasks
        ]
        history_rows = [
            {
                "时间": row.get("time"),
                "任务": row.get("task_name"),
                "结果": row.get("status"),
                "资源": row.get("title"),
                "站点": row.get("site"),
                "说明": row.get("message"),
            }
            for row in history[:30]
        ]
        return [
            {
                "component": "VAlert",
                "props": {"type": "info", "variant": "tonal"},
                "text": f"上次运行：{last_run.get('time') or '未运行'}；命中 {last_run.get('matched', 0)}，下载 {last_run.get('downloaded', 0)}，跳过 {last_run.get('skipped', 0)}。",
            },
            _table("任务", task_rows),
            _table("最近历史", history_rows),
        ]

    def get_service(self) -> List[Dict[str, Any]]:
        if not self._enabled or not self._cron:
            return []
        try:
            trigger = CronTrigger.from_crontab(self._cron)
        except ValueError as err:
            logger.error(f"直搜订阅 cron 配置错误：{err}")
            return []
        return [{
            "id": "DirectSearchSubscribe",
            "name": "直搜订阅",
            "trigger": trigger,
            "func": self.run,
            "kwargs": {},
        }]

    def stop_service(self):
        pass

    def api_run(self) -> schemas.Response:
        return schemas.Response(success=True, data=self.run(manual=True))

    def api_reset(self, task_id: str) -> schemas.Response:
        seen = self.get_data(SEEN_KEY) or {}
        if task_id in seen:
            seen.pop(task_id, None)
            self.save_data(SEEN_KEY, seen)
        return schemas.Response(success=True, message=f"已重置任务 {task_id} 的去重记录")

    def run(self, manual: bool = False) -> Dict[str, Any]:
        if not _lock.acquire(blocking=False):
            logger.info("直搜订阅正在运行，本次跳过")
            return {"running": True}
        try:
            tasks = [task for task in self._load_tasks() if task.get("enabled", True)]
            seen = self.get_data(SEEN_KEY) or {}
            history = self.get_data(HISTORY_KEY) or []
            summary = {
                "time": _now(),
                "manual": manual,
                "tasks": len(tasks),
                "matched": 0,
                "downloaded": 0,
                "skipped": 0,
                "errors": 0,
            }
            for task in tasks:
                try:
                    result = self._run_task(task=task, seen=seen, history=history)
                    for key in ("matched", "downloaded", "skipped", "errors"):
                        summary[key] += result.get(key, 0)
                except Exception as err:
                    summary["errors"] += 1
                    logger.error(f"直搜订阅任务执行失败：{task.get('name') or task.get('id')} - {err}")
                    _append_history(history, task, None, "错误", str(err))

            self.save_data(SEEN_KEY, seen)
            self.save_data(HISTORY_KEY, history[:MAX_HISTORY])
            self.save_data(RUN_KEY, summary)
            logger.info(
                f"直搜订阅运行完成：任务 {summary['tasks']}，命中 {summary['matched']}，"
                f"下载 {summary['downloaded']}，跳过 {summary['skipped']}，错误 {summary['errors']}"
            )
            return summary
        finally:
            _lock.release()

    def _run_task(self, task: Dict[str, Any], seen: Dict[str, Dict[str, Any]], history: List[dict]) -> Dict[str, int]:
        task_id = _task_id(task)
        task_seen = seen.setdefault(task_id, {})
        keyword = str(task.get("keyword") or task.get("name") or "").strip()
        if not keyword:
            raise ValueError("keyword 不能为空")

        sites = _parse_sites(task.get("sites"))
        pages = max(1, min(int(task.get("pages") or 1), 5))
        max_downloads = max(1, int(task.get("max_downloads") or 1))
        desired_episodes = _parse_episodes(task.get("episodes"))
        media_type = _parse_media_type(task.get("type"))

        logger.info(f"直搜订阅开始搜索：{task.get('name') or keyword}，关键词：{keyword}")
        candidates: List[Context] = []
        for page in range(pages):
            candidates.extend(SearchChain().search_by_title(title=keyword, page=page, sites=sites) or [])

        matches = self._filter_candidates(
            task=task,
            candidates=candidates,
            media_type=media_type,
            desired_episodes=desired_episodes,
        )
        summary = {"matched": len(matches), "downloaded": 0, "skipped": 0, "errors": 0}
        if not matches:
            _append_history(history, task, None, "未命中", "没有符合条件的资源")
            return summary

        for context in matches:
            fingerprint = _fingerprint(context)
            if fingerprint in task_seen:
                summary["skipped"] += 1
                continue
            if summary["downloaded"] >= max_downloads:
                summary["skipped"] += 1
                continue

            context.media_info = _build_media_info(task, media_type)
            episodes = _download_episodes(context.meta_info, desired_episodes)
            if self._dry_run:
                task_seen[fingerprint] = _seen_record(context, dry_run=True)
                summary["downloaded"] += 1
                _append_history(history, task, context, "演练", "演练模式未下载")
                continue

            download_id, error = DownloadChain().download_single(
                context=context,
                episodes=episodes,
                source="DirectSearchSubscribe",
                downloader=str(task.get("downloader") or "").strip() or None,
                save_path=str(task.get("save_path") or "").strip() or None,
                username=PLUGIN_ID,
                label=str(task.get("label") or "直搜订阅").strip() or None,
                return_detail=True,
            )
            if download_id:
                task_seen[fingerprint] = _seen_record(context, download_id=download_id)
                summary["downloaded"] += 1
                _append_history(history, task, context, "已下载", str(download_id))
                logger.info(f"直搜订阅已添加下载：{context.torrent_info.title}")
            else:
                summary["errors"] += 1
                _append_history(history, task, context, "失败", error or "添加下载失败")

        return summary

    def _filter_candidates(
            self,
            task: Dict[str, Any],
            candidates: List[Context],
            media_type: MediaType,
            desired_episodes: Set[int],
    ) -> List[Context]:
        include_words = _parse_words(task.get("include"))
        exclude_words = _parse_words(task.get("exclude"))
        season = _parse_int(task.get("season"))
        accept_unknown_episode = bool(task.get("accept_unknown_episode", False))
        results: List[Context] = []

        for context in candidates:
            torrent = context.torrent_info
            if not torrent or not torrent.title:
                continue
            text = f"{torrent.title} {torrent.description or ''}".lower()
            if include_words and not all(word.lower() in text for word in include_words):
                continue
            if exclude_words and any(word.lower() in text for word in exclude_words):
                continue

            meta = MetaInfo(title=torrent.title, subtitle=torrent.description)
            if media_type == MediaType.TV:
                if season is not None and meta.begin_season is not None and meta.begin_season != season:
                    continue
                if desired_episodes:
                    parsed_episodes = set(meta.episode_list or [])
                    if not parsed_episodes and not accept_unknown_episode:
                        continue
                    if parsed_episodes and not parsed_episodes.intersection(desired_episodes):
                        continue
            context.meta_info = meta
            context.resource_source = "direct_search_subscribe"
            results.append(context)

        return sorted(
            results,
            key=lambda item: (
                int(getattr(item.torrent_info, "seeders", 0) or 0),
                int(getattr(item.torrent_info, "size", 0) or 0),
            ),
            reverse=True,
        )

    def _load_tasks(self) -> List[Dict[str, Any]]:
        try:
            raw = self._tasks_json or "[]"
            tasks = json.loads(raw)
            if isinstance(tasks, dict):
                tasks = [tasks]
            if not isinstance(tasks, list):
                raise ValueError("任务 JSON 必须是数组或对象")
            return [task for task in tasks if isinstance(task, dict)]
        except Exception as err:
            logger.error(f"直搜订阅任务配置解析失败：{err}")
            return []


def _default_tasks_json() -> str:
    return json.dumps(DEFAULT_TASKS, ensure_ascii=False, indent=2)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _parse_media_type(value: Any) -> MediaType:
    text = str(value or "tv").strip().lower()
    if text in {"movie", "电影", "m"}:
        return MediaType.MOVIE
    return MediaType.TV


def _media_type_label(value: Any) -> str:
    return "电影" if _parse_media_type(value) == MediaType.MOVIE else "电视剧"


def _parse_int(value: Any) -> Optional[int]:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_sites(value: Any) -> Optional[List[int]]:
    if value in (None, "", []):
        return None
    if isinstance(value, str):
        items = re.split(r"[,，\\s]+", value.strip())
    elif isinstance(value, list):
        items = value
    else:
        items = [value]
    sites = []
    for item in items:
        try:
            if item not in (None, ""):
                sites.append(int(item))
        except (TypeError, ValueError):
            continue
    return sites or None


def _parse_words(value: Any) -> List[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in re.split(r"[,，\\n]+", str(value)) if item.strip()]


def _parse_episodes(value: Any) -> Set[int]:
    if value in (None, "", []):
        return set()
    if isinstance(value, list):
        raw_parts = [str(item) for item in value]
    else:
        raw_parts = re.split(r"[,，\\s]+", str(value))
    episodes: Set[int] = set()
    for part in raw_parts:
        part = part.strip()
        if not part:
            continue
        match = re.match(r"^(\\d+)\\s*-\\s*(\\d+)$", part)
        if match:
            start, end = int(match.group(1)), int(match.group(2))
            if start > end:
                start, end = end, start
            episodes.update(range(start, end + 1))
            continue
        if part.isdigit():
            episodes.add(int(part))
    return episodes


def _download_episodes(meta: MetaInfo, desired: Set[int]) -> Optional[Set[int]]:
    if not desired:
        return None
    parsed = set(meta.episode_list or [])
    return parsed.intersection(desired) or desired


def _build_media_info(task: Dict[str, Any], media_type: MediaType) -> MediaInfo:
    media = MediaInfo()
    media.type = media_type
    media.title = str(task.get("name") or task.get("keyword") or "").strip()
    media.season = _parse_int(task.get("season"))
    media.category = str(task.get("category") or "").strip()
    return media


def _task_id(task: Dict[str, Any]) -> str:
    return str(task.get("id") or task.get("name") or task.get("keyword") or "default").strip()


def _fingerprint(context: Context) -> str:
    torrent = context.torrent_info
    return "|".join([
        str(torrent.site_name or ""),
        str(torrent.title or ""),
        str(torrent.description or ""),
        str(torrent.enclosure or torrent.page_url or ""),
    ])


def _seen_record(context: Context, download_id: Optional[str] = None, dry_run: bool = False) -> Dict[str, Any]:
    torrent = context.torrent_info
    return {
        "time": _now(),
        "title": torrent.title,
        "site": torrent.site_name,
        "download_id": download_id,
        "dry_run": dry_run,
    }


def _append_history(
        history: List[dict],
        task: Dict[str, Any],
        context: Optional[Context],
        status: str,
        message: str = "",
):
    torrent = context.torrent_info if context else None
    history.insert(0, {
        "time": _now(),
        "task_id": _task_id(task),
        "task_name": task.get("name") or task.get("keyword") or _task_id(task),
        "status": status,
        "message": message,
        "title": torrent.title if torrent else "",
        "site": torrent.site_name if torrent else "",
        "size": StringUtils.str_filesize(torrent.size) if torrent and torrent.size else "",
    })
    del history[MAX_HISTORY:]


def _table(title: str, rows: List[dict]) -> dict:
    headers = [{"title": key, "key": key} for key in (rows[0].keys() if rows else ["提示"])]
    items = rows or [{"提示": "暂无数据"}]
    return {
        "component": "VCard",
        "props": {"variant": "outlined", "class": "mb-3"},
        "content": [
            {"component": "VCardTitle", "text": title},
            {
                "component": "VDataTable",
                "props": {
                    "headers": headers,
                    "items": items,
                    "items-per-page": 10,
                    "density": "compact",
                },
            },
        ],
    }
