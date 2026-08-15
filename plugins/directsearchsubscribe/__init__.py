from __future__ import annotations

import re
import time
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from app import schemas
from app.chain.download import DownloadChain
from app.chain.search import SearchChain
from app.chain.subscribe import SubscribeChain
from app.core.config import global_vars
from app.core.context import Context, MediaInfo
from app.core.metainfo import MetaInfo
from app.db.models.subscribe import Subscribe
from app.db.subscribe_oper import SubscribeOper
from app.db.systemconfig_oper import SystemConfigOper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import MediaType, SystemConfigKey


PLUGIN_ID = "directsearchsubscribe"
DIRECT_SUBS_KEY = "direct_subscribes"
HISTORY_KEY = "history"
MAX_HISTORY = 200
DIRECT_DOUBAN_PREFIX = "directsearch:"

_lock = threading.Lock()


class directsearchsubscribe(_PluginBase):
    plugin_name = "直搜订阅"
    plugin_desc = "从插件表单创建原生订阅，并让这些订阅直接按关键词搜索站点，不依赖 TMDB、豆瓣或 Bangumi 识别。"
    plugin_icon = "mdi-magnify-scan"
    plugin_version = "1.1.0"
    plugin_author = "Ellick"
    plugin_order = 30
    auth_level = 1

    _enabled = True
    _config: Dict[str, Any] = {}
    _patched = False
    _original_search = None

    def init_plugin(self, config: dict = None):
        config = config or {}
        self._config = dict(config)
        self._enabled = bool(config.get("enabled", True))

        if self._enabled:
            self._patch()
        else:
            self._unpatch()

        if config.get("add_now"):
            result = self.create_subscribe_from_config(config)
            _append_history(self, result)
            config["add_now"] = False
            self.update_config(config)
            self._config = dict(config)
            if result.get("success") and config.get("run_after_add"):
                sid = result.get("sid")
                if sid:
                    _run_direct_subscription(SubscribeChain(), sid=sid, manual=True)

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {
                "path": "/add",
                "endpoint": self.api_add,
                "methods": ["POST", "GET"],
                "auth": "bear",
                "summary": "添加直搜订阅",
                "description": "使用当前插件表单配置创建一条原生订阅。",
            },
            {
                "path": "/search/{sid}",
                "endpoint": self.api_search,
                "methods": ["POST", "GET"],
                "auth": "bear",
                "summary": "搜索直搜订阅",
                "description": "立即搜索指定直搜订阅。",
            },
        ]

    def get_form(self) -> Tuple[Optional[List[dict]], Dict[str, Any]]:
        return [
            {
                "component": "VForm",
                "content": [
                    _section_card("运行方式", [
                        {
                            "component": "VRow",
                            "content": [
                                _col(12, 4, {
                                    "component": "VSwitch",
                                    "props": {
                                        "model": "enabled",
                                        "label": "启用直搜接管",
                                        "color": "primary",
                                        "hint": "开启后，插件创建的订阅在原生订阅搜索时会直接搜索站点",
                                    },
                                }),
                                _col(12, 4, {
                                    "component": "VSwitch",
                                    "props": {
                                        "model": "add_now",
                                        "label": "添加到订阅",
                                        "color": "success",
                                        "hint": "打开后保存配置，会创建一条原生订阅并自动复位",
                                    },
                                }),
                                _col(12, 4, {
                                    "component": "VSwitch",
                                    "props": {
                                        "model": "run_after_add",
                                        "label": "添加后立即搜索",
                                        "hint": "创建订阅后立即走一次直搜",
                                    },
                                }),
                            ],
                        },
                    ]),
                    _section_card("订阅内容", [
                        {
                            "component": "VRow",
                            "content": [
                                _col(12, 8, {
                                    "component": "VTextField",
                                    "props": {
                                        "model": "title",
                                        "label": "订阅标题",
                                        "placeholder": "Re 从零开始的异世界生活 第四季",
                                        "hint": "会显示在原生订阅列表里",
                                    },
                                }),
                                _col(12, 4, {
                                    "component": "VSelect",
                                    "props": {
                                        "model": "type",
                                        "label": "类型",
                                        "items": [
                                            {"title": "电视剧", "value": "tv"},
                                            {"title": "电影", "value": "movie"},
                                        ],
                                    },
                                }),
                            ],
                        },
                        {
                            "component": "VRow",
                            "content": [
                                _col(12, 8, {
                                    "component": "VTextField",
                                    "props": {
                                        "model": "keyword",
                                        "label": "站点搜索关键词",
                                        "placeholder": "Re Zero S04 / 从零开始的异世界生活 第四季",
                                        "hint": "订阅搜索时直接把这个词送到站点搜索",
                                    },
                                }),
                                _col(12, 4, {
                                    "component": "VTextField",
                                    "props": {
                                        "model": "year",
                                        "label": "年份",
                                        "placeholder": "2026",
                                    },
                                }),
                            ],
                        },
                        {
                            "component": "VRow",
                            "content": [
                                _col(12, 3, {
                                    "component": "VTextField",
                                    "props": {
                                        "model": "season",
                                        "label": "季",
                                        "type": "number",
                                        "placeholder": "4",
                                        "hint": "电影可留空",
                                    },
                                }),
                                _col(12, 3, {
                                    "component": "VTextField",
                                    "props": {
                                        "model": "episodes",
                                        "label": "指定集数",
                                        "placeholder": "1-12,14",
                                        "hint": "留空则按每次新命中的集数追更",
                                    },
                                }),
                                _col(12, 3, {
                                    "component": "VTextField",
                                    "props": {
                                        "model": "total_episode",
                                        "label": "总集数",
                                        "type": "number",
                                        "placeholder": "12",
                                        "hint": "可留空；填了会在完成后移入订阅历史",
                                    },
                                }),
                                _col(12, 3, {
                                    "component": "VTextField",
                                    "props": {
                                        "model": "search_pages",
                                        "label": "搜索页数",
                                        "type": "number",
                                        "min": 1,
                                        "max": 5,
                                    },
                                }),
                            ],
                        },
                    ]),
                    _section_card("筛选与下载", [
                        {
                            "component": "VRow",
                            "content": [
                                _col(12, 6, {
                                    "component": "VTextField",
                                    "props": {
                                        "model": "include",
                                        "label": "必须包含",
                                        "placeholder": "2160p, HEVC",
                                        "hint": "逗号分隔，全部命中才会保留",
                                    },
                                }),
                                _col(12, 6, {
                                    "component": "VTextField",
                                    "props": {
                                        "model": "exclude",
                                        "label": "排除关键词",
                                        "placeholder": "合集, 国语, 试看",
                                        "hint": "逗号分隔，命中任意一个会跳过",
                                    },
                                }),
                            ],
                        },
                        {
                            "component": "VRow",
                            "content": [
                                _col(12, 4, {
                                    "component": "VTextField",
                                    "props": {
                                        "model": "sites",
                                        "label": "站点 ID",
                                        "placeholder": "1, 2, 8",
                                        "hint": "留空使用系统默认订阅站点",
                                    },
                                }),
                                _col(12, 4, {
                                    "component": "VTextField",
                                    "props": {
                                        "model": "downloader",
                                        "label": "下载器",
                                        "placeholder": "留空使用默认",
                                    },
                                }),
                                _col(12, 4, {
                                    "component": "VTextField",
                                    "props": {
                                        "model": "media_category",
                                        "label": "媒体类别",
                                        "placeholder": "动漫 / 剧集",
                                    },
                                }),
                            ],
                        },
                        {
                            "component": "VRow",
                            "content": [
                                _col(12, 8, {
                                    "component": "VTextField",
                                    "props": {
                                        "model": "save_path",
                                        "label": "保存路径",
                                        "placeholder": "留空使用系统规则",
                                    },
                                }),
                                _col(12, 4, {
                                    "component": "VSwitch",
                                    "props": {
                                        "model": "accept_unknown_episode",
                                        "label": "接受无法识别集数",
                                        "hint": "资源站标题解析不出 E01 时，允许下载最佳命中",
                                    },
                                }),
                            ],
                        },
                    ]),
                    {
                        "component": "VAlert",
                        "props": {"type": "info", "variant": "tonal"},
                        "text": "创建后请到原生订阅列表查看；这类订阅的后续搜索、下载、入库仍由 MoviePilot 订阅流程触发，只是搜索阶段改为直接搜站点。",
                    },
                ],
            }
        ], {
            "enabled": True,
            "add_now": False,
            "run_after_add": False,
            "title": "",
            "keyword": "",
            "type": "tv",
            "year": "",
            "season": "",
            "episodes": "",
            "total_episode": "",
            "search_pages": 1,
            "include": "",
            "exclude": "",
            "sites": "",
            "downloader": "",
            "save_path": "",
            "media_category": "",
            "accept_unknown_episode": True,
        }

    def get_page(self) -> Optional[List[dict]]:
        direct_map = _direct_map(self)
        rows = []
        for sid, task in direct_map.items():
            subscribe = SubscribeOper().get(int(sid)) if str(sid).isdigit() else None
            if not subscribe:
                continue
            rows.append({
                "ID": subscribe.id,
                "标题": subscribe.name,
                "类型": "电影" if subscribe.type == MediaType.MOVIE.value else "电视剧",
                "状态": _state_label(subscribe.state),
                "关键词": task.get("keyword") or subscribe.keyword or "",
                "季": subscribe.season or "",
                "集数": task.get("episodes") or "",
                "已下载": ", ".join(str(item) for item in (subscribe.note or [])),
            })
        history = self.get_data(HISTORY_KEY) or []
        history_rows = [
            {
                "时间": row.get("time"),
                "结果": row.get("status"),
                "标题": row.get("title"),
                "说明": row.get("message"),
            }
            for row in history[:20]
        ]
        return [
            {
                "component": "VAlert",
                "props": {"type": "success" if self._enabled else "warning", "variant": "tonal"},
                "text": "直搜接管已启用。" if self._enabled else "直搜接管未启用，已创建订阅不会改用直搜。",
            },
            _table("直搜订阅", rows),
            _table("最近操作", history_rows),
        ]

    def get_service(self) -> List[Dict[str, Any]]:
        return []

    def stop_service(self):
        self._unpatch()

    def api_add(self) -> schemas.Response:
        result = self.create_subscribe_from_config(self._config)
        _append_history(self, result)
        return schemas.Response(success=bool(result.get("success")), message=result.get("message"), data=result)

    def api_search(self, sid: int) -> schemas.Response:
        result = _run_direct_subscription(SubscribeChain(), sid=sid, manual=True)
        return schemas.Response(success=bool(result.get("success")), message=result.get("message"), data=result)

    def create_subscribe_from_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        title = str(config.get("title") or config.get("keyword") or "").strip()
        keyword = str(config.get("keyword") or title).strip()
        media_type = _parse_media_type(config.get("type"))
        if not title:
            return {"success": False, "status": "失败", "title": "", "message": "订阅标题不能为空"}
        if not keyword:
            return {"success": False, "status": "失败", "title": title, "message": "搜索关键词不能为空"}

        season = _parse_int(config.get("season"))
        episodes = sorted(_parse_episodes(config.get("episodes")))
        total_episode = _parse_int(config.get("total_episode"))
        if media_type == MediaType.TV and not total_episode and episodes:
            total_episode = max(episodes)
        start_episode = min(episodes) if episodes else 1
        sites = _parse_sites(config.get("sites")) or []
        task = {
            "title": title,
            "keyword": keyword,
            "type": media_type.value,
            "year": str(config.get("year") or "").strip(),
            "season": season,
            "episodes": str(config.get("episodes") or "").strip(),
            "total_episode": total_episode,
            "search_pages": max(1, min(_parse_int(config.get("search_pages")) or 1, 5)),
            "include": str(config.get("include") or "").strip(),
            "exclude": str(config.get("exclude") or "").strip(),
            "sites": sites,
            "downloader": str(config.get("downloader") or "").strip(),
            "save_path": str(config.get("save_path") or "").strip(),
            "media_category": str(config.get("media_category") or "").strip(),
            "accept_unknown_episode": bool(config.get("accept_unknown_episode", True)),
        }

        direct_map = _direct_map(self)
        for sid, item in direct_map.items():
            subscribe = SubscribeOper().get(int(sid)) if str(sid).isdigit() else None
            if not subscribe:
                continue
            if subscribe.name == title and subscribe.type == media_type.value and subscribe.season == season:
                direct_map[str(subscribe.id)] = task
                self.save_data(DIRECT_SUBS_KEY, direct_map)
                return {
                    "success": True,
                    "status": "已存在",
                    "sid": subscribe.id,
                    "title": title,
                    "message": f"直搜订阅已存在，已更新参数：{subscribe.id}",
                }

        direct_key = f"{DIRECT_DOUBAN_PREFIX}{int(time.time())}"
        subscribe = Subscribe(
            name=title,
            year=task["year"] or None,
            type=media_type.value,
            keyword=keyword,
            doubanid=direct_key,
            season=season,
            filter=None,
            include=task["include"] or None,
            exclude=task["exclude"] or None,
            total_episode=total_episode,
            start_episode=start_episode if media_type == MediaType.TV else None,
            lack_episode=len(episodes) if episodes else total_episode,
            note=[],
            state="N",
            date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            username="直搜订阅",
            sites=sites,
            downloader=task["downloader"] or None,
            save_path=task["save_path"] or None,
            manual_total_episode=1 if total_episode else 0,
            media_category=task["media_category"] or None,
            custom_words="[DirectSearchSubscribe]",
        )
        subscribe.create(SubscribeOper()._db)
        created = Subscribe.get_by_doubanid(SubscribeOper()._db, direct_key)
        if not created:
            return {
                "success": False,
                "status": "失败",
                "title": title,
                "message": "订阅已写入但反查失败，请刷新订阅列表确认",
            }
        direct_map[str(created.id)] = task
        self.save_data(DIRECT_SUBS_KEY, direct_map)
        return {
            "success": True,
            "status": "已添加",
            "sid": created.id,
            "title": title,
            "message": f"已添加到原生订阅：{created.id}",
        }

    @classmethod
    def _patch(cls):
        if cls._patched:
            return
        cls._original_search = SubscribeChain.search

        def patched_search(chain_self, sid: Optional[int] = None, state: Optional[str] = "N",
                           manual: Optional[bool] = False):
            return _patched_subscribe_search(chain_self, sid=sid, state=state, manual=manual)

        SubscribeChain.search = patched_search
        cls._patched = True
        logger.info("直搜订阅已接管原生订阅搜索")

    @classmethod
    def _unpatch(cls):
        if cls._patched and cls._original_search:
            SubscribeChain.search = cls._original_search
        cls._patched = False
        cls._original_search = None


def _patched_subscribe_search(chain: SubscribeChain, sid: Optional[int] = None,
                              state: Optional[str] = "N", manual: Optional[bool] = False):
    original = directsearchsubscribe._original_search
    if not original:
        return None

    if sid:
        subscribe = SubscribeOper().get(sid)
        if _is_direct_subscribe(subscribe):
            result = _run_direct_subscription(chain, subscribe=subscribe, manual=manual)
            if manual:
                chain.messagehelper.put(f"{subscribe.name} 搜索完成！", title="订阅搜索", role="system")
            return result
        return original(chain, sid=sid, state=state, manual=manual)

    states = chain.get_states_for_search(state)
    subscribes = SubscribeOper().list(states)
    direct_ids = {item.id for item in subscribes if _is_direct_subscribe(item)}
    if direct_ids:
        for subscribe in subscribes:
            if subscribe.id in direct_ids:
                _run_direct_subscription(chain, subscribe=subscribe, manual=manual)

    regular_ids = {item.id for item in subscribes if item.id not in direct_ids}
    if regular_ids:
        original_list = SubscribeOper.list

        def filtered_list(oper_self, search_state: Optional[str] = None):
            return [item for item in original_list(oper_self, search_state) if item.id in regular_ids]

        SubscribeOper.list = filtered_list
        try:
            return original(chain, sid=None, state=state, manual=manual)
        finally:
            SubscribeOper.list = original_list

    if manual:
        chain.messagehelper.put("所有订阅搜索完成！", title="订阅搜索", role="system")
    return None


def _run_direct_subscription(chain: SubscribeChain, sid: Optional[int] = None,
                             subscribe: Optional[Subscribe] = None,
                             manual: Optional[bool] = False) -> Dict[str, Any]:
    if not _lock.acquire(blocking=False):
        return {"success": False, "message": "直搜订阅正在运行，本次跳过"}
    try:
        subscribe = subscribe or SubscribeOper().get(sid)
        if not subscribe:
            return {"success": False, "message": "订阅不存在"}
        task = _direct_task(subscribe)
        if not task:
            return {"success": False, "message": "不是直搜订阅"}
        if subscribe.date and not manual:
            try:
                subscribe_time = datetime.strptime(subscribe.date, "%Y-%m-%d %H:%M:%S")
                if (datetime.now() - subscribe_time).total_seconds() < 60:
                    logger.debug(f"直搜订阅 {subscribe.name} 新增小于1分钟，暂不搜索")
                    return {"success": True, "message": "新增小于1分钟，暂不搜索"}
            except Exception:
                pass

        logger.info(f"开始直搜订阅：{subscribe.name}，关键词：{task.get('keyword') or subscribe.keyword}")
        contexts = _search_direct_contexts(chain, subscribe, task)
        if not contexts:
            logger.warn(f"直搜订阅 {subscribe.name} 未搜索到资源")
            if subscribe.state == "N":
                SubscribeOper().update(subscribe.id, {"state": "R"})
            return {"success": True, "message": "未搜索到资源", "matched": 0, "downloaded": 0}

        downloads = _download_direct_contexts(chain, subscribe, task, contexts)
        if subscribe.state == "N":
            SubscribeOper().update(subscribe.id, {"state": "R"})
        return {
            "success": True,
            "message": f"匹配 {len(contexts)}，下载 {len(downloads)}",
            "matched": len(contexts),
            "downloaded": len(downloads),
        }
    except Exception as err:
        logger.error(f"直搜订阅执行失败：{err}")
        return {"success": False, "message": str(err)}
    finally:
        _lock.release()


def _search_direct_contexts(chain: SubscribeChain, subscribe: Subscribe, task: Dict[str, Any]) -> List[Context]:
    keyword = str(task.get("keyword") or subscribe.keyword or subscribe.name).strip()
    pages = max(1, min(_parse_int(task.get("search_pages")) or 1, 5))
    sites = task.get("sites")
    if not sites:
        sites = chain.get_sub_sites(subscribe)
    candidates: List[Context] = []
    for page in range(pages):
        candidates.extend(SearchChain().search_by_title(title=keyword, page=page, sites=sites) or [])
    contexts = _filter_contexts(subscribe, task, candidates)
    return contexts


def _filter_contexts(subscribe: Subscribe, task: Dict[str, Any], candidates: List[Context]) -> List[Context]:
    media_type = MediaType(subscribe.type)
    include_words = _parse_words(task.get("include") or subscribe.include)
    exclude_words = _parse_words(task.get("exclude") or subscribe.exclude)
    desired = _parse_episodes(task.get("episodes"))
    season = _parse_int(task.get("season"))
    if season is None:
        season = subscribe.season
    accept_unknown = bool(task.get("accept_unknown_episode", True))
    results = []
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
            if desired:
                parsed = set(meta.episode_list or [])
                if not parsed and not accept_unknown:
                    continue
                if parsed and not parsed.intersection(desired):
                    continue
        context.meta_info = meta
        context.media_info = _build_media_info(subscribe, task)
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


def _download_direct_contexts(
        chain: SubscribeChain,
        subscribe: Subscribe,
        task: Dict[str, Any],
        contexts: List[Context],
) -> List[Context]:
    media_type = MediaType(subscribe.type)
    source = chain.get_subscribe_source_keyword(subscribe)
    downloader = task.get("downloader") or subscribe.downloader
    save_path = task.get("save_path") or subscribe.save_path
    if media_type == MediaType.MOVIE:
        downloads, _ = DownloadChain().batch_download(
            contexts=contexts[:1],
            no_exists=None,
            username=subscribe.username,
            save_path=save_path,
            downloader=downloader,
            source=source,
        )
        if downloads:
            _native_update_note(chain, subscribe, downloads)
            subscribe = SubscribeOper().get(subscribe.id)
            getattr(chain, "_SubscribeChain__finish_subscribe")(subscribe=subscribe,
                                                                 meta=contexts[0].meta_info,
                                                                 mediainfo=contexts[0].media_info)
        return downloads

    note = set(int(item) for item in (subscribe.note or []) if str(item).isdigit())
    desired = _parse_episodes(task.get("episodes"))
    total_episode = _parse_int(task.get("total_episode")) or subscribe.total_episode
    known_goal = bool(desired or total_episode)
    if desired:
        target_episodes = sorted(desired.difference(note))
    elif total_episode:
        start = subscribe.start_episode or 1
        target_episodes = sorted(set(range(start, total_episode + 1)).difference(note))
    else:
        target_episodes = sorted(_episodes_from_contexts(contexts).difference(note))

    if target_episodes:
        media_key = subscribe.doubanid or f"{DIRECT_DOUBAN_PREFIX}{subscribe.id}"
        season = subscribe.season or _parse_int(task.get("season")) or 1
        no_exists = {
            media_key: {
                season: schemas.NotExistMediaInfo(
                    season=season,
                    episodes=target_episodes,
                    total_episode=total_episode or max(target_episodes),
                    start_episode=min(target_episodes),
                )
            }
        }
        downloads, lefts = DownloadChain().batch_download(
            contexts=contexts,
            no_exists=no_exists,
            username=subscribe.username,
            save_path=save_path,
            downloader=downloader,
            source=source,
        )
        if downloads:
            _native_update_note(chain, subscribe, downloads)
        _finish_or_update_direct(chain, subscribe.id, task, contexts, known_goal, lefts)
        return downloads

    if task.get("accept_unknown_episode", True):
        for context in contexts[:1]:
            download_id = DownloadChain().download_single(
                context=context,
                save_path=save_path,
                source=source,
                downloader=downloader,
                username=subscribe.username,
            )
            if download_id:
                SubscribeOper().update(subscribe.id, {
                    "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "state": "R",
                })
                return [context]
    return []


def _finish_or_update_direct(chain: SubscribeChain, sid: int, task: Dict[str, Any],
                             contexts: List[Context], known_goal: bool, lefts: Dict[str, Any]):
    subscribe = SubscribeOper().get(sid)
    if not subscribe:
        return
    if not known_goal:
        SubscribeOper().update(sid, {
            "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "state": "R",
        })
        return
    desired = _parse_episodes(task.get("episodes"))
    if not desired and subscribe.total_episode:
        desired = set(range(subscribe.start_episode or 1, subscribe.total_episode + 1))
    downloaded = set(int(item) for item in (subscribe.note or []) if str(item).isdigit())
    lack = len(desired.difference(downloaded)) if desired else 0
    SubscribeOper().update(sid, {
        "lack_episode": lack,
        "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "state": "R",
    })
    if lack == 0 and contexts and subscribe.state != "P":
        subscribe = SubscribeOper().get(sid)
        getattr(chain, "_SubscribeChain__finish_subscribe")(subscribe=subscribe,
                                                             meta=contexts[0].meta_info,
                                                             mediainfo=contexts[0].media_info)


def _native_update_note(chain: SubscribeChain, subscribe: Subscribe, downloads: List[Context]):
    getattr(chain, "_SubscribeChain__update_subscribe_note")(subscribe=subscribe, downloads=downloads)


def _build_media_info(subscribe: Subscribe, task: Dict[str, Any]) -> MediaInfo:
    media = MediaInfo()
    media.type = MediaType(subscribe.type)
    media.title = subscribe.name
    media.year = subscribe.year
    media.season = subscribe.season
    media.douban_id = subscribe.doubanid or f"{DIRECT_DOUBAN_PREFIX}{subscribe.id}"
    media.category = task.get("media_category") or subscribe.media_category or ""
    media.source = PLUGIN_ID
    return media


def _direct_task(subscribe: Subscribe) -> Optional[Dict[str, Any]]:
    if not subscribe:
        return None
    direct_map = _direct_map()
    task = direct_map.get(str(subscribe.id))
    if task:
        return task
    if str(subscribe.doubanid or "").startswith(DIRECT_DOUBAN_PREFIX):
        return {
            "title": subscribe.name,
            "keyword": subscribe.keyword or subscribe.name,
            "type": subscribe.type,
            "year": subscribe.year,
            "season": subscribe.season,
            "episodes": "",
            "total_episode": subscribe.total_episode,
            "search_pages": 1,
            "include": subscribe.include or "",
            "exclude": subscribe.exclude or "",
            "sites": subscribe.sites or [],
            "downloader": subscribe.downloader or "",
            "save_path": subscribe.save_path or "",
            "media_category": subscribe.media_category or "",
            "accept_unknown_episode": True,
        }
    return None


def _is_direct_subscribe(subscribe: Optional[Subscribe]) -> bool:
    return bool(_direct_task(subscribe))


def _direct_map(plugin: Optional[directsearchsubscribe] = None) -> Dict[str, Dict[str, Any]]:
    if plugin:
        data = plugin.get_data(DIRECT_SUBS_KEY) or {}
    else:
        data = directsearchsubscribe().get_data(DIRECT_SUBS_KEY) or {}
    return data if isinstance(data, dict) else {}


def _append_history(plugin: directsearchsubscribe, result: Dict[str, Any]):
    history = plugin.get_data(HISTORY_KEY) or []
    history.insert(0, {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": result.get("status") or ("成功" if result.get("success") else "失败"),
        "title": result.get("title"),
        "message": result.get("message"),
    })
    plugin.save_data(HISTORY_KEY, history[:MAX_HISTORY])


def _parse_media_type(value: Any) -> MediaType:
    text = str(value or "tv").strip().lower()
    if text in {"movie", "电影", "m", MediaType.MOVIE.value}:
        return MediaType.MOVIE
    return MediaType.TV


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
        items = re.split(r"[,，\s]+", value.strip())
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
    return [item.strip() for item in re.split(r"[,，\n]+", str(value)) if item.strip()]


def _parse_episodes(value: Any) -> Set[int]:
    if value in (None, "", []):
        return set()
    if isinstance(value, list):
        raw_parts = [str(item) for item in value]
    else:
        raw_parts = re.split(r"[,，\s]+", str(value))
    episodes: Set[int] = set()
    for part in raw_parts:
        part = part.strip()
        if not part:
            continue
        match = re.match(r"^(\d+)\s*-\s*(\d+)$", part)
        if match:
            start, end = int(match.group(1)), int(match.group(2))
            if start > end:
                start, end = end, start
            episodes.update(range(start, end + 1))
            continue
        if part.isdigit():
            episodes.add(int(part))
    return episodes


def _episodes_from_contexts(contexts: List[Context]) -> Set[int]:
    episodes = set()
    for context in contexts:
        episodes.update(context.meta_info.episode_list or [])
    return episodes


def _col(cols: int, md: int, child: Dict[str, Any]) -> Dict[str, Any]:
    return {"component": "VCol", "props": {"cols": cols, "md": md}, "content": [child]}


def _section_card(title: str, content: List[dict]) -> Dict[str, Any]:
    return {
        "component": "VCard",
        "props": {"variant": "outlined", "class": "mb-4"},
        "content": [
            {"component": "VCardTitle", "text": title},
            {"component": "VCardText", "content": content},
        ],
    }


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


def _state_label(state: str) -> str:
    return {
        "N": "新建",
        "R": "订阅中",
        "P": "待定",
        "S": "暂停",
    }.get(state or "", state or "")
