from __future__ import annotations

import json
import time
import traceback
import re
from pathlib import Path
from typing import Any, List, Optional, Tuple

from fastapi import Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app import schemas
from app.api.endpoints.search import _stream_search_events
from app.chain.media import MediaChain
from app.chain.search import SearchChain
from app.chain.storage import StorageChain
from app.chain.subscribe import SubscribeChain
from app.chain.transfer import TransferChain
from app.chain.tmdb import TmdbChain
from app.core.config import settings
from app.core.context import MediaInfo
from app.core.event import eventmanager
from app.core.metainfo import MetaInfo
from app.core.security import verify_resource_token, verify_token
from app.db import async_db_query, db_query
from app.db.downloadhistory_oper import DownloadHistoryOper
from app.db.models.downloadhistory import DownloadHistory
from app.db.models.subscribe import Subscribe
from app.db.models.subscribehistory import SubscribeHistory
from app.db.models.transferhistory import TransferHistory
from app.db.subscribe_oper import SubscribeOper
from app.db.transferhistory_oper import TransferHistoryOper
from app.factory import app
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import MediaType, MessageChannel
from app.schemas.types import ContentType, EventType, NotificationType
from app.helper.subscribe import SubscribeHelper
from app.helper.torrent import TorrentHelper


class sourceprioritysubscribefix(_PluginBase):
    plugin_name = "订阅外部源优先"
    plugin_desc = "订阅时有 doubanid/bangumiid 则直接使用对应来源详情，避免强制转 TMDB。"
    plugin_icon = "mdi-heart-cog"
    plugin_version = "1.0.18"
    plugin_author = "local"
    plugin_order = 1
    auth_level = 1

    _enabled = False
    _patched = False
    _originals: dict[str, Any] = {}
    _original_media_routes: list[Any] = []
    _original_media_route_index: Optional[int] = None
    _plugin_route_registered = False
    _original_search_routes: dict[str, list[Any]] = {}
    _original_search_route_indexes: dict[str, int] = {}
    _original_search_endpoints: dict[str, Any] = {}
    _plugin_search_route_registered = False

    def init_plugin(self, config: dict = None):
        config = config or {}
        self._enabled = bool(config.get("enabled", True))
        if self._enabled:
            self._patch()
        else:
            self._unpatch()

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[dict]:
        return []

    def get_api(self) -> List[dict]:
        return [
            {
                "path": "/redo/{history_id}",
                "endpoint": _plugin_redo_transfer_history,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "使用订阅来源重新整理",
                "description": "对 Bangumi-only 订阅下载失败的整理记录，按下载历史中的订阅来源重新整理。",
            }
        ]

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
                                "props": {
                                    "cols": 12,
                                    "md": 6,
                                },
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "enabled",
                                            "label": "启用插件",
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
                                "props": {
                                    "cols": 12,
                                },
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "variant": "tonal",
                                            "text": "启用后，订阅携带 doubanid 或 bangumiid 时会优先使用对应来源详情，避免强制转换到 TMDB。",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                ],
            }
        ], {"enabled": True}

    def get_page(self) -> Optional[List[dict]]:
        return _diagnostic_page(self)

    def stop_service(self):
        self._unpatch()

    @classmethod
    def _patch(cls):
        if cls._patched:
            return
        cls._originals = {
            "subscribe_add": SubscribeChain.add,
            "subscribe_async_add": SubscribeChain.async_add,
            "subscribe_exists": SubscribeChain.__dict__["exists"],
            "subscribe_recognize_media": SubscribeChain.recognize_media,
            "subscribe_async_recognize_media": SubscribeChain.async_recognize_media,
            "search_process": SearchChain.process,
            "search_async_process": SearchChain.async_process,
            "search_async_process_stream": SearchChain.async_process_stream,
            "oper_add": SubscribeOper.add,
            "oper_async_add": SubscribeOper.async_add,
            "oper_exists": SubscribeOper.exists,
            "oper_exist_history": SubscribeOper.exist_history,
            "model_exists": Subscribe.__dict__["exists"],
            "model_async_exists": Subscribe.__dict__["async_exists"],
            "history_exists": SubscribeHistory.__dict__["exists"],
            "history_async_exists": SubscribeHistory.__dict__["async_exists"],
            "torrent_match": TorrentHelper.match_torrent,
            "transfer_do_transfer": TransferChain.do_transfer,
        }
        if hasattr(TransferChain, "redo_transfer_history"):
            cls._originals["transfer_redo_transfer_history"] = TransferChain.redo_transfer_history
        SubscribeChain.add = _patched_subscribe_add
        SubscribeChain.async_add = _patched_subscribe_async_add
        SubscribeChain.exists = staticmethod(_patched_subscribe_exists)
        SubscribeChain.recognize_media = _patched_subscribe_recognize_media
        SubscribeChain.async_recognize_media = _patched_subscribe_async_recognize_media
        SearchChain.process = _patched_search_process
        SearchChain.async_process = _patched_search_async_process
        SearchChain.async_process_stream = _patched_search_async_process_stream
        SubscribeOper.add = _patched_oper_add
        SubscribeOper.async_add = _patched_oper_async_add
        SubscribeOper.exists = _patched_oper_exists
        SubscribeOper.exist_history = _patched_oper_exist_history
        Subscribe.exists = classmethod(db_query(_patched_model_exists))
        Subscribe.async_exists = classmethod(async_db_query(_patched_model_async_exists))
        SubscribeHistory.exists = classmethod(db_query(_patched_model_exists))
        SubscribeHistory.async_exists = classmethod(async_db_query(_patched_model_async_exists))
        TorrentHelper.match_torrent = staticmethod(_patched_match_torrent)
        TransferChain.do_transfer = _patched_transfer_do_transfer
        if "transfer_redo_transfer_history" in cls._originals:
            TransferChain.redo_transfer_history = _patched_transfer_redo_transfer_history
        cls._patch_media_seasons_route()
        cls._patch_search_routes()
        cls._patched = True
        logger.info("订阅外部源优先插件已启用")

    @classmethod
    def _unpatch(cls):
        if not cls._patched:
            return
        SubscribeChain.add = cls._originals["subscribe_add"]
        SubscribeChain.async_add = cls._originals["subscribe_async_add"]
        SubscribeChain.exists = cls._originals["subscribe_exists"]
        SubscribeChain.recognize_media = cls._originals["subscribe_recognize_media"]
        SubscribeChain.async_recognize_media = cls._originals["subscribe_async_recognize_media"]
        SearchChain.process = cls._originals["search_process"]
        SearchChain.async_process = cls._originals["search_async_process"]
        SearchChain.async_process_stream = cls._originals["search_async_process_stream"]
        SubscribeOper.add = cls._originals["oper_add"]
        SubscribeOper.async_add = cls._originals["oper_async_add"]
        SubscribeOper.exists = cls._originals["oper_exists"]
        SubscribeOper.exist_history = cls._originals["oper_exist_history"]
        Subscribe.exists = cls._originals["model_exists"]
        Subscribe.async_exists = cls._originals["model_async_exists"]
        SubscribeHistory.exists = cls._originals["history_exists"]
        SubscribeHistory.async_exists = cls._originals["history_async_exists"]
        TorrentHelper.match_torrent = staticmethod(cls._originals["torrent_match"])
        TransferChain.do_transfer = cls._originals["transfer_do_transfer"]
        if "transfer_redo_transfer_history" in cls._originals:
            TransferChain.redo_transfer_history = cls._originals["transfer_redo_transfer_history"]
        cls._restore_search_routes()
        cls._restore_media_seasons_route()
        cls._originals = {}
        cls._patched = False
        logger.info("订阅外部源优先插件已停用")

    @classmethod
    def _patch_media_seasons_route(cls):
        path = f"{settings.API_V1_STR}/media/seasons"
        if cls._plugin_route_registered:
            return
        remaining_routes = []
        cls._original_media_routes = []
        cls._original_media_route_index = None
        for route in app.routes:
            if getattr(route, "path", None) == path and "GET" in getattr(route, "methods", set()):
                if cls._original_media_route_index is None:
                    cls._original_media_route_index = len(remaining_routes)
                cls._original_media_routes.append(route)
                continue
            remaining_routes.append(route)
        app.routes[:] = remaining_routes
        insert_at = cls._media_seasons_insert_index(app.routes)
        route_count = len(app.routes)
        app.add_api_route(
            path,
            _patched_media_seasons,
            methods=["GET"],
            response_model=List[schemas.MediaSeason],
            summary="查询媒体季信息",
            tags=["media"],
        )
        new_routes = app.routes[route_count:]
        del app.routes[route_count:]
        for offset, route in enumerate(new_routes):
            app.routes.insert(min(insert_at + offset, len(app.routes)), route)
        app.openapi_schema = None
        cls._plugin_route_registered = True

    @classmethod
    def _restore_media_seasons_route(cls):
        if not cls._plugin_route_registered:
            return
        path = f"{settings.API_V1_STR}/media/seasons"
        app.routes[:] = [
            route for route in app.routes
            if not (getattr(route, "path", None) == path and getattr(route, "endpoint", None) is _patched_media_seasons)
        ]
        insert_at = cls._media_seasons_insert_index(app.routes)
        for offset, route in enumerate(cls._original_media_routes):
            app.routes.insert(min(insert_at + offset, len(app.routes)), route)
        app.openapi_schema = None
        cls._original_media_routes = []
        cls._original_media_route_index = None
        cls._plugin_route_registered = False

    @classmethod
    def _media_seasons_insert_index(cls, routes: list[Any]) -> int:
        insert_at = cls._original_media_route_index if cls._original_media_route_index is not None else len(routes)
        detail_path = f"{settings.API_V1_STR}/media/{{mediaid}}"
        for index, route in enumerate(routes):
            if getattr(route, "path", None) == detail_path:
                return min(insert_at, index)
        return insert_at

    @classmethod
    def _search_route_defs(cls) -> dict[str, dict[str, Any]]:
        return {
            f"{settings.API_V1_STR}/search/media/{{mediaid}}": {
                "endpoint": _patched_search_by_id,
                "response_model": schemas.Response,
                "summary": "精确搜索资源",
                "tags": ["search"],
            },
            f"{settings.API_V1_STR}/search/media/{{mediaid}}/stream": {
                "endpoint": _patched_search_by_id_stream,
                "response_model": None,
                "summary": "渐进式精确搜索资源",
                "tags": ["search"],
            },
        }

    @classmethod
    def _patch_search_routes(cls):
        if cls._plugin_search_route_registered:
            return
        route_defs = cls._search_route_defs()
        remaining_routes = []
        cls._original_search_routes = {path: [] for path in route_defs}
        cls._original_search_route_indexes = {}
        cls._original_search_endpoints = {}
        for route in app.routes:
            path = getattr(route, "path", None)
            if path in route_defs and "GET" in getattr(route, "methods", set()):
                if path not in cls._original_search_route_indexes:
                    cls._original_search_route_indexes[path] = len(remaining_routes)
                    cls._original_search_endpoints[path] = getattr(route, "endpoint", None)
                cls._original_search_routes[path].append(route)
                continue
            remaining_routes.append(route)
        app.routes[:] = remaining_routes
        route_count = len(app.routes)
        for path, route_def in route_defs.items():
            app.add_api_route(
                path,
                route_def["endpoint"],
                methods=["GET"],
                response_model=route_def["response_model"],
                summary=route_def["summary"],
                tags=route_def["tags"],
            )
        new_routes = app.routes[route_count:]
        del app.routes[route_count:]
        for route in new_routes:
            insert_at = cls._original_search_route_indexes.get(getattr(route, "path", None), len(app.routes))
            app.routes.insert(min(insert_at, len(app.routes)), route)
        app.openapi_schema = None
        cls._plugin_search_route_registered = True

    @classmethod
    def _restore_search_routes(cls):
        if not cls._plugin_search_route_registered:
            return
        endpoints = {_patched_search_by_id, _patched_search_by_id_stream}
        app.routes[:] = [
            route for route in app.routes
            if getattr(route, "endpoint", None) not in endpoints
        ]
        for path, index in sorted(cls._original_search_route_indexes.items(), key=lambda item: item[1]):
            for offset, route in enumerate(cls._original_search_routes.get(path) or []):
                app.routes.insert(min(index + offset, len(app.routes)), route)
        app.openapi_schema = None
        cls._original_search_routes = {}
        cls._original_search_route_indexes = {}
        cls._original_search_endpoints = {}
        cls._plugin_search_route_registered = False


_SOURCE_SUBSCRIBE_CACHE = {
    "time": 0.0,
    "items": [],
}


def _clear_source_subscribe_cache():
    _SOURCE_SUBSCRIBE_CACHE["time"] = 0.0
    _SOURCE_SUBSCRIBE_CACHE["items"] = []


def _type_value(value: Any) -> Optional[str]:
    if isinstance(value, MediaType):
        return value.value
    return value


def _normalize_match_text(text: Optional[str]) -> str:
    if not text:
        return ""
    return re.sub(r"\W+", "", str(text).lower())


def _title_candidates_from_meta(meta: Any) -> set[str]:
    if not meta:
        return set()
    candidates = {
        getattr(meta, "name", None),
        getattr(meta, "title", None),
        getattr(meta, "cn_name", None),
        getattr(meta, "org_string", None),
    }
    return {_normalize_match_text(item) for item in candidates if _normalize_match_text(item)}


def _title_candidates_from_subscribe(subscribe: Subscribe) -> set[str]:
    candidates = {subscribe.name}
    try:
        sub_meta = MetaInfo(subscribe.name)
        candidates.add(sub_meta.name)
        candidates.add(sub_meta.title)
    except Exception:
        pass
    return {_normalize_match_text(item) for item in candidates if _normalize_match_text(item)}


def _source_only_subscribes() -> list[Subscribe]:
    now = time.time()
    if now - _SOURCE_SUBSCRIBE_CACHE["time"] < 10:
        return _SOURCE_SUBSCRIBE_CACHE["items"]
    subscribes = [
        subscribe for subscribe in SubscribeOper().list()
        if subscribe.bangumiid and not subscribe.tmdbid and not subscribe.doubanid
    ]
    _SOURCE_SUBSCRIBE_CACHE["time"] = now
    _SOURCE_SUBSCRIBE_CACHE["items"] = subscribes
    return subscribes


def _match_subscribe_by_meta(meta: Any, mtype: Optional[MediaType]) -> Optional[Subscribe]:
    meta_titles = _title_candidates_from_meta(meta)
    if not meta_titles:
        return None
    meta_type = _type_value(mtype) or _type_value(getattr(meta, "type", None))
    meta_season = getattr(meta, "begin_season", None)
    for subscribe in _source_only_subscribes():
        if meta_type and subscribe.type and meta_type != subscribe.type:
            continue
        if meta_season is not None and subscribe.season is not None and meta_season != subscribe.season:
            continue
        sub_titles = _title_candidates_from_subscribe(subscribe)
        if meta_titles.intersection(sub_titles):
            return subscribe
        for meta_title in meta_titles:
            if len(meta_title) >= 4 and any(meta_title in sub_title or sub_title in meta_title for sub_title in sub_titles):
                return subscribe
    return None


def _apply_subscribe_ids(mediainfo: Optional[MediaInfo], subscribe: Optional[Subscribe]) -> Optional[MediaInfo]:
    if not mediainfo or not subscribe:
        return mediainfo
    mediainfo.bangumi_id = subscribe.bangumiid or mediainfo.bangumi_id
    mediainfo.douban_id = subscribe.doubanid or mediainfo.douban_id
    mediainfo.tmdb_id = subscribe.tmdbid or mediainfo.tmdb_id
    return mediainfo


def _bangumi_tag_values(info: dict) -> list[str]:
    values = []
    for key in ("platform",):
        value = info.get(key)
        if value:
            values.append(str(value))
    for item in info.get("meta_tags") or []:
        if item:
            values.append(str(item))
    for item in info.get("tags") or []:
        if isinstance(item, dict):
            name = item.get("name")
            if name:
                values.append(str(name))
        elif item:
            values.append(str(item))
    return values


def _has_any_marker(values: list[str], markers: tuple[str, ...]) -> bool:
    text = "\n".join(values).lower()
    return any(marker.lower() in text for marker in markers)


_BANGUMI_INFOBOX_ALIAS_KEYS = {
    "别名",
    "英文名",
    "英文名称",
    "英文标题",
    "原名",
    "日文名",
    "日文名称",
}

def _iter_text_values(value: Any):
    if value is None:
        return
    if isinstance(value, str):
        text = value.strip()
        if text:
            yield text
        return
    if isinstance(value, dict):
        for key in ("v", "value", "name", "title"):
            if value.get(key):
                yield from _iter_text_values(value.get(key))
                return
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _iter_text_values(item)
        return
    text = str(value).strip()
    if text:
        yield text


def _dedupe_aliases(values: list[Any]) -> list[str]:
    aliases = []
    seen = set()
    for value in values:
        for text in _iter_text_values(value):
            key = _normalize_match_text(text)
            if not key or key in seen:
                continue
            aliases.append(text)
            seen.add(key)
    return aliases


def _bangumi_infobox_aliases(info: dict) -> list[str]:
    values = []
    for item in info.get("infobox") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if key in _BANGUMI_INFOBOX_ALIAS_KEYS:
            values.extend(_iter_text_values(item.get("value")) or [])
    return _dedupe_aliases(values)


def _bangumi_generated_aliases(values: list[Any]) -> list[str]:
    aliases = []
    for value in values:
        for text in _iter_text_values(value):
            aliases.append(text)
            # 常见季标题会把“正片系列名 + 季/篇章名”放在同一个标题里。
            for pattern in (
                r"\s*第[一二三四五六七八九十百千万零〇两0-9]+[季期部].*$",
                r"\s+\d+(?:st|nd|rd|th)\s+season.*$",
                r"\s+season\s*\d+.*$",
            ):
                base = re.sub(pattern, "", text, flags=re.I).strip()
                if base and base != text:
                    aliases.append(base)
            if re.search(r"[\u4e00-\u9fff\u3040-\u30ff]", text):
                base = re.sub(r"\s*[Rr0-9]+$", "", text).strip()
                if base and base != text:
                    aliases.append(base)
    return _dedupe_aliases(aliases)


def _english_alias(aliases: list[str]) -> Optional[str]:
    for alias in aliases:
        if re.search(r"[A-Za-z]", alias) and not re.search(r"[\u4e00-\u9fff\u3040-\u30ff]", alias):
            return alias
    return None


def _bangumi_media_aliases(mediainfo: MediaInfo) -> list[str]:
    info = mediainfo.bangumi_info or {}
    values = list(mediainfo.names or [])
    values.extend([
        mediainfo.title,
        mediainfo.original_title,
        mediainfo.en_title,
        mediainfo.hk_title,
        mediainfo.tw_title,
        mediainfo.sg_title,
        info.get("name_cn"),
        info.get("name"),
    ])
    values.extend(_bangumi_infobox_aliases(info))
    values.extend(_bangumi_generated_aliases(values))
    return _dedupe_aliases(values)


def _bangumi_match_text_candidates(mediainfo: MediaInfo) -> set[str]:
    values = list(mediainfo.names or [])
    values.extend([
        mediainfo.title,
        mediainfo.original_title,
        mediainfo.en_title,
        mediainfo.hk_title,
        mediainfo.tw_title,
        mediainfo.sg_title,
    ])
    candidates = set()
    for alias in _bangumi_generated_aliases(values):
        normalized = _normalize_match_text(alias)
        if len(normalized) >= 5:
            candidates.add(normalized)
    return candidates


def _bangumi_match_guard(mediainfo: MediaInfo, torrent_meta: Any, torrent: Any) -> bool:
    if not mediainfo or not mediainfo.bangumi_id or mediainfo.tmdb_id or mediainfo.douban_id:
        return False
    if torrent_meta.type == MediaType.TV and mediainfo.type != MediaType.TV:
        return False
    if torrent.category == MediaType.TV.value and mediainfo.type != MediaType.TV:
        return False
    if not mediainfo.year:
        return True
    if mediainfo.type == MediaType.TV:
        years = {str(year) for year in (mediainfo.season_years or {}).values() if year}
        if not years and mediainfo.year:
            years = {str(mediainfo.year)}
        return not torrent_meta.year or str(torrent_meta.year) in years
    try:
        media_year = int(mediainfo.year)
    except (TypeError, ValueError):
        return True
    return str(torrent_meta.year) in {str(media_year - 1), str(media_year), str(media_year + 1)}


def _bangumi_normalized_content_match(mediainfo: MediaInfo, torrent_meta: Any, torrent: Any) -> bool:
    candidates = _bangumi_match_text_candidates(mediainfo)
    if not candidates:
        return False
    content = _normalize_match_text("\n".join([
        str(torrent.title or ""),
        str(torrent.description or ""),
        str(getattr(torrent_meta, "org_string", "") or ""),
    ]))
    if not content:
        return False
    return any(candidate in content for candidate in candidates)


def _patched_match_torrent(mediainfo: MediaInfo, torrent_meta: Any, torrent: Any) -> bool:
    if sourceprioritysubscribefix._originals["torrent_match"](mediainfo, torrent_meta, torrent):
        return True
    if not _bangumi_match_guard(mediainfo, torrent_meta, torrent):
        return False
    if _bangumi_normalized_content_match(mediainfo, torrent_meta, torrent):
        logger.info(f"{mediainfo.title} 通过Bangumi归一化标题匹配到资源：{torrent.site_name} - {torrent.title}")
        return True
    return False


def _infer_bangumi_media_category(mediainfo: MediaInfo) -> Optional[str]:
    info = mediainfo.bangumi_info or {}
    if info.get("type") != 2:
        return None
    if mediainfo.type == MediaType.MOVIE:
        return "动画电影"
    values = _bangumi_tag_values(info)
    if _has_any_marker(values, ("中国", "大陆", "国产", "国漫", "中漫", "中国大陆", "台湾", "香港")):
        return "国漫"
    if _has_any_marker(values, ("日本", "日漫", "日番")):
        return "日番"
    return None


def _mark_bangumi_media_ready(mediainfo: Optional[MediaInfo]) -> Optional[MediaInfo]:
    if not mediainfo or not mediainfo.bangumi_id or mediainfo.tmdb_id or mediainfo.douban_id:
        return mediainfo
    if mediainfo.type == MediaType.TV and mediainfo.year and not mediainfo.season_years:
        season = mediainfo.season
        if season is None and mediainfo.seasons:
            season = next(iter(mediainfo.seasons.keys()), None)
        if season is not None:
            mediainfo.season_years = {season: str(mediainfo.year)}
    if not mediainfo.category:
        mediainfo.category = _infer_bangumi_media_category(mediainfo) or mediainfo.category
    aliases = _bangumi_media_aliases(mediainfo)
    if aliases:
        mediainfo.names = aliases
        if not mediainfo.en_title:
            mediainfo.en_title = _english_alias(aliases)
    return mediainfo


def _media_from_douban(chain: Any, doubanid: str, mtype: Optional[MediaType]) -> Optional[MediaInfo]:
    info = chain.douban_info(doubanid=doubanid, mtype=mtype)
    if not info:
        return None
    return MediaInfo(douban_info=info)


async def _async_media_from_douban(chain: Any, doubanid: str, mtype: Optional[MediaType]) -> Optional[MediaInfo]:
    info = await chain.async_douban_info(doubanid=doubanid, mtype=mtype)
    if not info:
        return None
    return MediaInfo(douban_info=info)


def _media_from_bangumi(chain: Any, bangumiid: int) -> Optional[MediaInfo]:
    info = chain.bangumi_info(bangumiid=bangumiid)
    if not info:
        return None
    return _mark_bangumi_media_ready(MediaInfo(bangumi_info=info))


async def _async_media_from_bangumi(chain: Any, bangumiid: int) -> Optional[MediaInfo]:
    info = await chain.async_bangumi_info(bangumiid=bangumiid)
    if not info:
        return None
    return _mark_bangumi_media_ready(MediaInfo(bangumi_info=info))


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None and str(value).strip() else None
    except (TypeError, ValueError):
        return None


def _media_type_or_none(value: Any) -> Optional[MediaType]:
    if isinstance(value, MediaType):
        return value
    if not value:
        return None
    try:
        return MediaType(value)
    except ValueError:
        return None


def _download_history_source(download_history: Any) -> Optional[str]:
    note = getattr(download_history, "note", None)
    if isinstance(note, dict):
        return note.get("source")
    if isinstance(note, str):
        if note.startswith("Subscribe|"):
            return note
        try:
            note_data = json.loads(note)
        except (TypeError, ValueError):
            return None
        if isinstance(note_data, dict):
            return note_data.get("source")
    return None


def _subscribe_from_source_keyword(source_keyword: dict) -> Optional[Subscribe]:
    sid = _int_or_none(source_keyword.get("id"))
    if sid:
        subscribe = SubscribeOper().get(sid)
        if subscribe:
            return subscribe
    return SubscribeOper().get_by(
        type=source_keyword.get("type"),
        season=source_keyword.get("season"),
        tmdbid=source_keyword.get("tmdbid"),
        doubanid=source_keyword.get("doubanid"),
        bangumiid=source_keyword.get("bangumiid"),
    )


def _source_media_from_download_history(chain: Any, download_history: Any) -> Optional[MediaInfo]:
    if not download_history or getattr(download_history, "tmdbid", None) or getattr(download_history, "doubanid", None):
        return None
    source_keyword = SubscribeChain.parse_subscribe_source_keyword(_download_history_source(download_history))
    if not source_keyword:
        return None
    subscribe = _subscribe_from_source_keyword(source_keyword)
    bangumiid = _int_or_none(getattr(subscribe, "bangumiid", None) or source_keyword.get("bangumiid"))
    if not bangumiid:
        return None
    mediainfo = _media_from_bangumi(chain, bangumiid)
    if not mediainfo:
        return None
    mtype = _media_type_or_none(getattr(download_history, "type", None)) or _media_type_or_none(source_keyword.get("type"))
    if mtype:
        mediainfo.type = mtype
    season = _int_or_none(source_keyword.get("season"))
    if mediainfo.type == MediaType.TV and season is not None:
        mediainfo.season = season
    if getattr(download_history, "media_category", None):
        mediainfo.category = download_history.media_category
    elif subscribe and getattr(subscribe, "media_category", None):
        mediainfo.category = subscribe.media_category
    return _mark_bangumi_media_ready(_apply_subscribe_ids(mediainfo, subscribe))


def _download_history_by_hash_or_file(download_hash: Optional[str], fileitem: Any) -> Optional[Any]:
    downloadhis = DownloadHistoryOper()
    if download_hash:
        history = downloadhis.get_by_hash(download_hash)
        if history:
            return history
    file_path = getattr(fileitem, "path", None)
    if not file_path:
        return None
    try:
        download_file = downloadhis.get_file_by_fullpath(Path(file_path).as_posix())
    except Exception:
        download_file = None
    if download_file and getattr(download_file, "download_hash", None):
        return downloadhis.get_by_hash(download_file.download_hash)
    return None


def _patched_transfer_do_transfer(self: TransferChain, *args, **kwargs):
    args_list = list(args)
    fileitem = args_list[0] if args_list else kwargs.get("fileitem")
    mediainfo = kwargs.get("mediainfo")
    if mediainfo is None and len(args_list) > 2:
        mediainfo = args_list[2]
    download_hash = kwargs.get("download_hash")
    if download_hash is None and len(args_list) > 14:
        download_hash = args_list[14]

    if not mediainfo:
        download_history = _download_history_by_hash_or_file(download_hash, fileitem)
        source_mediainfo = _source_media_from_download_history(self, download_history)
        if source_mediainfo:
            logger.info(f"{getattr(fileitem, 'name', None) or getattr(fileitem, 'path', '')} 使用订阅来源Bangumi详情补齐整理识别：{source_mediainfo.title_year}")
            if len(args_list) > 2:
                args_list[2] = source_mediainfo
            else:
                kwargs["mediainfo"] = source_mediainfo

    return sourceprioritysubscribefix._originals["transfer_do_transfer"](self, *args_list, **kwargs)


def _redo_transfer_history_with_source(chain: TransferChain, history_id: int) -> Optional[Tuple[bool, str]]:
    history = TransferHistoryOper().get(history_id)
    if history:
        download_history = _download_history_by_hash_or_file(history.download_hash, None)
        mediainfo = _source_media_from_download_history(chain, download_history)
        if mediainfo and history.src_fileitem:
            logger.info(f"{history.src} 使用订阅来源Bangumi详情重新整理：{mediainfo.title_year}")
            if history.dest_fileitem:
                StorageChain().delete_file(schemas.FileItem(**history.dest_fileitem))
            return chain.do_transfer(
                fileitem=schemas.FileItem(**history.src_fileitem),
                mediainfo=mediainfo,
                download_hash=history.download_hash,
                force=True,
                background=False,
                manual=True,
            )
    return None


def _patched_transfer_redo_transfer_history(self: TransferChain, history_id: int) -> Tuple[bool, str]:
    result = _redo_transfer_history_with_source(self, history_id)
    if result:
        return result
    return sourceprioritysubscribefix._originals["transfer_redo_transfer_history"](self, history_id)


def _plugin_redo_transfer_history(history_id: int) -> Any:
    result = _redo_transfer_history_with_source(TransferChain(), history_id)
    if result:
        state, message = result
        return schemas.Response(success=state, message=message)
    return schemas.Response(success=False, message="未找到可用的 Bangumi 订阅来源整理信息")


def _safe_page_text(value: Any) -> str:
    if value is None or value == "":
        return "-"
    return str(value)


def _page_db_items(model: Any, status: Optional[bool] = None, limit: int = 20) -> list[Any]:
    try:
        oper = TransferHistoryOper() if model is TransferHistory else DownloadHistoryOper()
        query = oper._db.query(model)
        if model is TransferHistory and status is not None:
            query = query.filter(TransferHistory.status.is_(status))
        return query.order_by(model.id.desc()).limit(limit).all()
    except Exception as err:
        logger.warn(f"订阅外部源优先插件读取页面数据失败：{err}")
        return []


def _bangumi_source_keyword(download_history: Any) -> Optional[dict]:
    source_keyword = SubscribeChain.parse_subscribe_source_keyword(_download_history_source(download_history))
    if not source_keyword or not _int_or_none(source_keyword.get("bangumiid")):
        return None
    return source_keyword


def _source_downloads(limit: int = 12) -> list[DownloadHistory]:
    result = []
    try:
        histories = DownloadHistoryOper().list_by_page(page=1, count=500) or []
        histories = sorted(histories, key=lambda item: item.id or 0, reverse=True)
    except Exception as err:
        logger.warn(f"订阅外部源优先插件读取下载历史失败：{err}")
        histories = []
    for history in histories:
        if _bangumi_source_keyword(history):
            result.append(history)
        if len(result) >= limit:
            break
    return result


def _bangumi_only_subscribes_for_page(limit: int = 20) -> list[Subscribe]:
    try:
        subscribes = [
            subscribe for subscribe in SubscribeOper().list()
            if subscribe.bangumiid and not subscribe.tmdbid and not subscribe.doubanid
        ]
        return sorted(subscribes, key=lambda item: item.id or 0, reverse=True)[:limit]
    except Exception as err:
        logger.warn(f"订阅外部源优先插件读取订阅数据失败：{err}")
        return []


def _component_text(component: str, text: Any, props: Optional[dict] = None) -> dict:
    item = {
        "component": component,
        "text": _safe_page_text(text),
    }
    if props:
        item["props"] = props
    return item


def _chip(text: Any, color: str = "primary", icon: Optional[str] = None) -> dict:
    props = {
        "size": "small",
        "variant": "tonal",
        "color": color,
        "class": "mr-2 mb-2",
    }
    if icon:
        props["prepend-icon"] = icon
    return {
        "component": "VChip",
        "props": props,
        "text": _safe_page_text(text),
    }


def _detail_line(label: str, value: Any) -> dict:
    return {
        "component": "div",
        "props": {"class": "d-flex align-start py-1"},
        "content": [
            _component_text(
                "div",
                label,
                {
                    "class": "text-caption text-medium-emphasis flex-shrink-0",
                    "style": "width:4.75em",
                },
            ),
            _component_text(
                "div",
                value,
                {
                    "class": "text-body-2 flex-grow-1",
                    "style": "word-break:break-word;min-width:0",
                },
            ),
        ],
    }


def _mobile_item(
        title: Any,
        subtitle: Any,
        chips: list[dict],
        details: list[dict],
        action: Optional[dict] = None) -> dict:
    content = [
        {
            "component": "div",
            "props": {"class": "d-flex align-start justify-space-between ga-2"},
            "content": [
                {
                    "component": "div",
                    "props": {"class": "flex-grow-1", "style": "min-width:0"},
                    "content": [
                        _component_text(
                            "div",
                            title,
                            {
                                "class": "text-body-1 font-weight-medium",
                                "style": "word-break:break-word",
                            },
                        ),
                        _component_text(
                            "div",
                            subtitle,
                            {
                                "class": "text-caption text-medium-emphasis mt-1",
                                "style": "word-break:break-word",
                            },
                        ),
                    ],
                },
            ],
        },
        {
            "component": "div",
            "props": {"class": "d-flex flex-wrap mt-3"},
            "content": chips,
        },
        {
            "component": "div",
            "props": {"class": "mt-1"},
            "content": details,
        },
    ]
    if action:
        content.append({
            "component": "div",
            "props": {"class": "mt-3"},
            "content": [action],
        })
    return {
        "component": "VSheet",
        "props": {
            "border": True,
            "rounded": "lg",
            "class": "pa-3 mb-3",
        },
        "content": content,
    }


def _stat_card(title: str, value: Any, subtitle: str, icon: str, color: str) -> dict:
    return {
        "component": "VCol",
        "props": {"cols": 6, "sm": 6, "lg": 3},
        "content": [
            {
                "component": "VCard",
                "props": {"variant": "tonal", "color": color, "class": "h-100"},
                "content": [
                    {
                        "component": "VCardText",
                        "props": {"class": "pa-3 pa-sm-4"},
                        "content": [
                            {
                                "component": "div",
                                "props": {"class": "d-flex align-start justify-space-between ga-2"},
                                "content": [
                                    {
                                        "component": "div",
                                        "props": {"style": "min-width:0"},
                                        "content": [
                                            _component_text("div", title, {"class": "text-caption text-medium-emphasis"}),
                                            _component_text("div", value, {"class": "text-h6 text-sm-h5 font-weight-bold mt-1"}),
                                            _component_text(
                                                "div",
                                                subtitle,
                                                {
                                                    "class": "text-caption mt-1",
                                                    "style": "word-break:break-word",
                                                },
                                            ),
                                        ],
                                    },
                                    {
                                        "component": "VIcon",
                                        "props": {"icon": icon, "size": 28, "class": "flex-shrink-0"},
                                    },
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
    }


def _table_header(headers: list[str]) -> dict:
    return {
        "component": "thead",
        "content": [
            {
                "component": "tr",
                "content": [
                    {
                        "component": "th",
                        "props": {"class": "text-start ps-4"},
                        "text": header,
                    }
                    for header in headers
                ],
            }
        ],
    }


def _td(text: Any, class_name: str = "") -> dict:
    item = {
        "component": "td",
        "text": _safe_page_text(text),
    }
    if class_name:
        item["props"] = {"class": class_name}
    return item


def _table_card(
        title: str,
        icon: str,
        headers: list[str],
        rows: list[dict],
        mobile_items: list[dict],
        empty_text: str) -> dict:
    content = [
        {
            "component": "div",
            "props": {"class": "d-flex align-center mb-3"},
            "content": [
                {"component": "VIcon", "props": {"icon": icon, "class": "mr-2"}},
                _component_text("div", title, {"class": "text-subtitle-1 font-weight-medium"}),
            ],
        }
    ]
    if rows:
        content.append({
            "component": "div",
            "props": {"class": "d-none d-md-block overflow-x-auto"},
            "content": [
                {
                    "component": "VTable",
                    "props": {"hover": True, "density": "compact"},
                    "content": [
                        _table_header(headers),
                        {
                            "component": "tbody",
                            "content": rows,
                        },
                    ],
                }
            ],
        })
        content.append({
            "component": "div",
            "props": {"class": "d-flex d-md-none flex-column ga-3"},
            "content": mobile_items,
        })
    else:
        content.append({
            "component": "VAlert",
            "props": {
                "type": "info",
                "variant": "tonal",
                "text": empty_text,
            },
        })
    return {
        "component": "VCard",
        "props": {"variant": "outlined"},
        "content": [
            {
                "component": "VCardText",
                "props": {"class": "pa-3 pa-sm-4"},
                "content": content,
            }
        ],
    }


def _redo_button(plugin_id: str, history_id: int, block: bool = False) -> dict:
    props = {
        "size": "small",
        "variant": "tonal",
        "color": "primary",
        "prepend-icon": "mdi-restore",
    }
    if block:
        props["block"] = True
    return {
        "component": "VBtn",
        "props": props,
        "text": "重整",
        "events": {
            "click": {
                "api": f"plugin/{plugin_id}/redo/{history_id}",
                "method": "post",
            }
        },
    }


def _failed_transfer_entries(plugin_id: str, histories: list[TransferHistory]) -> tuple[list[dict], list[dict]]:
    rows = []
    mobile_items = []
    for history in histories:
        source_keyword = _bangumi_source_keyword(_download_history_by_hash_or_file(history.download_hash, None))
        source_text = (
            f"bangumi:{source_keyword.get('bangumiid')}"
            if source_keyword
            else "无 Bangumi 订阅来源"
        )
        rows.append({
            "component": "tr",
            "content": [
                _td(history.id, "text-no-wrap"),
                _td(history.title, "text-no-wrap"),
                _td(source_text, "text-no-wrap"),
                _td(history.errmsg),
                _td(history.date, "text-no-wrap"),
                {
                    "component": "td",
                    "content": [_redo_button(plugin_id, history.id)] if source_keyword else [],
                },
            ],
        })
        mobile_items.append(_mobile_item(
            title=history.title,
            subtitle=source_text,
            chips=[
                _chip(f"ID {history.id}", "primary", "mdi-pound"),
                _chip(history.date, "secondary", "mdi-clock-outline"),
            ],
            details=[
                _detail_line("错误", history.errmsg),
                _detail_line("来源", source_text),
            ],
            action=_redo_button(plugin_id, history.id, block=True) if source_keyword else None,
        ))
    return rows, mobile_items


def _subscribe_entries(subscribes: list[Subscribe]) -> tuple[list[dict], list[dict]]:
    rows = []
    mobile_items = []
    for subscribe in subscribes:
        progress = f"{(subscribe.total_episode or 0) - (subscribe.lack_episode or 0)} / {subscribe.total_episode or 0}"
        season_text = f"S{subscribe.season:02d}" if subscribe.season else "-"
        rows.append({
            "component": "tr",
            "content": [
                _td(subscribe.id, "text-no-wrap"),
                _td(subscribe.name, "text-no-wrap"),
                _td(subscribe.year, "text-no-wrap"),
                _td(season_text, "text-no-wrap"),
                _td(f"bangumi:{subscribe.bangumiid}", "text-no-wrap"),
                _td(progress, "text-no-wrap"),
            ],
        })
        mobile_items.append(_mobile_item(
            title=subscribe.name,
            subtitle=f"{subscribe.year or '-'} · {season_text} · {progress}",
            chips=[
                _chip(f"ID {subscribe.id}", "primary", "mdi-pound"),
                _chip(f"bangumi:{subscribe.bangumiid}", "info", "mdi-book-open-page-variant"),
            ],
            details=[
                _detail_line("年份", subscribe.year),
                _detail_line("季", season_text),
                _detail_line("进度", progress),
            ],
        ))
    return rows, mobile_items


def _download_entries(downloads: list[DownloadHistory]) -> tuple[list[dict], list[dict]]:
    rows = []
    mobile_items = []
    for download in downloads:
        source_keyword = _bangumi_source_keyword(download) or {}
        source_text = f"bangumi:{source_keyword.get('bangumiid')}"
        rows.append({
            "component": "tr",
            "content": [
                _td(download.id, "text-no-wrap"),
                _td(download.title, "text-no-wrap"),
                _td(source_text, "text-no-wrap"),
                _td(download.torrent_name),
                _td(download.date, "text-no-wrap"),
            ],
        })
        mobile_items.append(_mobile_item(
            title=download.title,
            subtitle=source_text,
            chips=[
                _chip(f"ID {download.id}", "primary", "mdi-pound"),
                _chip(download.date, "secondary", "mdi-clock-outline"),
            ],
            details=[
                _detail_line("资源", download.torrent_name),
                _detail_line("来源", source_text),
            ],
        ))
    return rows, mobile_items


def _diagnostic_page(plugin: sourceprioritysubscribefix) -> List[dict]:
    plugin_id = plugin.__class__.__name__
    failed_histories = _page_db_items(TransferHistory, status=False, limit=20)
    source_download_items = _source_downloads(limit=12)
    bangumi_subscribes = _bangumi_only_subscribes_for_page(limit=20)
    enabled_text = "已启用" if plugin.get_state() else "已停用"
    failed_rows, failed_mobile_items = _failed_transfer_entries(plugin_id, failed_histories)
    subscribe_rows, subscribe_mobile_items = _subscribe_entries(bangumi_subscribes)
    download_rows, download_mobile_items = _download_entries(source_download_items)

    return [
        {
            "component": "VRow",
            "props": {"dense": True},
            "content": [
                _stat_card("插件状态", enabled_text, f"版本 {plugin.plugin_version}", "mdi-heart-cog", "primary"),
                _stat_card("Bangumi 订阅", len(bangumi_subscribes), "未绑定 TMDB/豆瓣", "mdi-book-heart", "info"),
                _stat_card("失败整理", len(failed_histories), "最多显示最近 20 条", "mdi-alert-circle", "error"),
                _stat_card("来源下载", len(source_download_items), "最近 Bangumi 来源下载", "mdi-download-circle", "success"),
            ],
        },
        {
            "component": "VRow",
            "content": [
                {
                    "component": "VCol",
                    "props": {"cols": 12},
                    "content": [
                        _table_card(
                            title="失败整理记录",
                            icon="mdi-alert-circle-outline",
                            headers=["ID", "标题", "订阅来源", "错误", "时间", "操作"],
                            rows=failed_rows,
                            mobile_items=failed_mobile_items,
                            empty_text="当前没有失败整理记录。",
                        )
                    ],
                }
            ],
        },
        {
            "component": "VRow",
            "content": [
                {
                    "component": "VCol",
                    "props": {"cols": 12, "lg": 6},
                    "content": [
                        _table_card(
                            title="Bangumi-only 订阅",
                            icon="mdi-book-open-page-variant",
                            headers=["ID", "标题", "年份", "季", "Bangumi", "进度"],
                            rows=subscribe_rows,
                            mobile_items=subscribe_mobile_items,
                            empty_text="暂无 Bangumi-only 订阅。",
                        )
                    ],
                },
                {
                    "component": "VCol",
                    "props": {"cols": 12, "lg": 6},
                    "content": [
                        _table_card(
                            title="最近来源下载",
                            icon="mdi-download",
                            headers=["ID", "标题", "Bangumi", "资源名", "时间"],
                            rows=download_rows,
                            mobile_items=download_mobile_items,
                            empty_text="暂无 Bangumi 来源下载记录。",
                        )
                    ],
                },
            ],
        },
    ]


def _patched_subscribe_recognize_media(self: SubscribeChain, meta: Any = None, mtype: Optional[MediaType] = None,
                                       tmdbid: Optional[int] = None, doubanid: Optional[str] = None,
                                       bangumiid: Optional[int] = None, episode_group: Optional[str] = None,
                                       cache: bool = True) -> Optional[MediaInfo]:
    if not tmdbid and doubanid:
        mediainfo = _media_from_douban(self, doubanid, mtype)
        if mediainfo:
            return mediainfo
    if not tmdbid and not doubanid and bangumiid:
        mediainfo = _media_from_bangumi(self, bangumiid)
        if mediainfo:
            return mediainfo
    if not tmdbid and not doubanid and not bangumiid:
        subscribe = _match_subscribe_by_meta(meta, mtype)
        if subscribe:
            mediainfo = _media_from_bangumi(self, subscribe.bangumiid)
            if mediainfo:
                return _mark_bangumi_media_ready(_apply_subscribe_ids(mediainfo, subscribe))
    return _mark_bangumi_media_ready(sourceprioritysubscribefix._originals["subscribe_recognize_media"](
        self,
        meta=meta,
        mtype=mtype,
        tmdbid=tmdbid,
        doubanid=doubanid,
        bangumiid=bangumiid,
        episode_group=episode_group,
        cache=cache,
    ))


async def _patched_subscribe_async_recognize_media(self: SubscribeChain, meta: Any = None,
                                                   mtype: Optional[MediaType] = None,
                                                   tmdbid: Optional[int] = None,
                                                   doubanid: Optional[str] = None,
                                                   bangumiid: Optional[int] = None,
                                                   episode_group: Optional[str] = None,
                                                   cache: bool = True) -> Optional[MediaInfo]:
    if not tmdbid and doubanid:
        mediainfo = await _async_media_from_douban(self, doubanid, mtype)
        if mediainfo:
            return mediainfo
    if not tmdbid and not doubanid and bangumiid:
        mediainfo = await _async_media_from_bangumi(self, bangumiid)
        if mediainfo:
            return mediainfo
    if not tmdbid and not doubanid and not bangumiid:
        subscribe = _match_subscribe_by_meta(meta, mtype)
        if subscribe:
            mediainfo = await _async_media_from_bangumi(self, subscribe.bangumiid)
            if mediainfo:
                return _mark_bangumi_media_ready(_apply_subscribe_ids(mediainfo, subscribe))
    return _mark_bangumi_media_ready(await sourceprioritysubscribefix._originals["subscribe_async_recognize_media"](
        self,
        meta=meta,
        mtype=mtype,
        tmdbid=tmdbid,
        doubanid=doubanid,
        bangumiid=bangumiid,
        episode_group=episode_group,
        cache=cache,
    ))


def _ensure_bangumi_search_media(chain: SearchChain, mediainfo: MediaInfo) -> MediaInfo:
    if not mediainfo or not mediainfo.bangumi_id or mediainfo.tmdb_id or mediainfo.douban_id:
        return mediainfo
    mediainfo = _mark_bangumi_media_ready(mediainfo)
    if mediainfo.names and mediainfo.seasons:
        return mediainfo
    refreshed = _media_from_bangumi(chain, mediainfo.bangumi_id)
    return _mark_bangumi_media_ready(refreshed or mediainfo)


async def _async_ensure_bangumi_search_media(chain: SearchChain, mediainfo: MediaInfo) -> MediaInfo:
    if not mediainfo or not mediainfo.bangumi_id or mediainfo.tmdb_id or mediainfo.douban_id:
        return mediainfo
    mediainfo = _mark_bangumi_media_ready(mediainfo)
    if mediainfo.names and mediainfo.seasons:
        return mediainfo
    refreshed = await _async_media_from_bangumi(chain, mediainfo.bangumi_id)
    return _mark_bangumi_media_ready(refreshed or mediainfo)


def _patched_search_process(self: SearchChain, mediainfo: MediaInfo, *args, **kwargs):
    mediainfo = _ensure_bangumi_search_media(self, mediainfo)
    return sourceprioritysubscribefix._originals["search_process"](self, mediainfo, *args, **kwargs)


async def _patched_search_async_process(self: SearchChain, mediainfo: MediaInfo, *args, **kwargs):
    mediainfo = await _async_ensure_bangumi_search_media(self, mediainfo)
    return await sourceprioritysubscribefix._originals["search_async_process"](self, mediainfo, *args, **kwargs)


async def _patched_search_async_process_stream(self: SearchChain, mediainfo: MediaInfo, *args, **kwargs):
    mediainfo = await _async_ensure_bangumi_search_media(self, mediainfo)
    async for event in sourceprioritysubscribefix._originals["search_async_process_stream"](self, mediainfo, *args, **kwargs):
        yield event


def _parse_site_list(sites: Optional[str]) -> Optional[List[int]]:
    return [int(site) for site in sites.split(",") if site] if sites else None


def _parse_media_type(mtype: Optional[str]) -> Optional[MediaType]:
    return MediaType(mtype) if mtype else None


def _parse_media_season(season: Optional[str]) -> Optional[int]:
    return int(season) if season else None


def _search_no_exists_for_season(media_season: Optional[int]) -> Optional[dict]:
    if media_season is None:
        return None
    # 原搜索链只用 tmdbid/doubanid 作缺失信息 key；Bangumi-only 用 None 才能复用季过滤。
    return {
        None: {
            media_season: schemas.NotExistMediaInfo(episodes=[])
        }
    }


async def _bangumi_search_media(search_chain: SearchChain, mediaid: str, mtype: Optional[str],
                                season: Optional[str]) -> tuple[Optional[MediaInfo], Optional[int], Optional[str]]:
    bangumiid_text = mediaid.replace("bangumi:", "", 1)
    if not bangumiid_text.isdigit():
        return None, None, "BangumiID无效"
    media_type = _parse_media_type(mtype)
    media_season = _parse_media_season(season)
    mediainfo = await _async_media_from_bangumi(search_chain, int(bangumiid_text))
    if not mediainfo:
        return None, media_season, "未识别到Bangumi媒体信息"
    if media_type:
        mediainfo.type = media_type
    if media_season is not None:
        mediainfo.season = media_season
    logger.info(f"订阅外部源优先插件使用Bangumi详情搜索：{bangumiid_text}")
    return mediainfo, media_season, None


async def _patched_search_by_id(mediaid: str,
                                mtype: Optional[str] = None,
                                area: Optional[str] = "title",
                                title: Optional[str] = None,
                                year: Optional[str] = None,
                                season: Optional[str] = None,
                                sites: Optional[str] = None,
                                _: schemas.TokenPayload = Depends(verify_token)) -> Any:
    path = f"{settings.API_V1_STR}/search/media/{{mediaid}}"
    if not mediaid.startswith("bangumi:"):
        original = sourceprioritysubscribefix._original_search_endpoints.get(path)
        return await original(mediaid=mediaid, mtype=mtype, area=area, title=title, year=year, season=season,
                              sites=sites, _=_)

    search_chain = SearchChain()
    mediainfo, media_season, error = await _bangumi_search_media(search_chain, mediaid, mtype, season)
    if error:
        return schemas.Response(success=False, message=error)
    contexts = await search_chain.async_process(
        mediainfo=mediainfo,
        sites=_parse_site_list(sites),
        area=area,
        no_exists=_search_no_exists_for_season(media_season),
    )
    await search_chain.async_save_cache(contexts, "__search_result__")
    if not contexts:
        return schemas.Response(success=False, message="未搜索到任何资源")
    return schemas.Response(success=True, data=[context.to_dict() for context in contexts])


async def _patched_search_by_id_stream(request: Request,
                                       mediaid: str,
                                       mtype: Optional[str] = None,
                                       area: Optional[str] = "title",
                                       title: Optional[str] = None,
                                       year: Optional[str] = None,
                                       season: Optional[str] = None,
                                       sites: Optional[str] = None,
                                       _: schemas.TokenPayload = Depends(verify_resource_token)) -> Any:
    path = f"{settings.API_V1_STR}/search/media/{{mediaid}}/stream"
    if not mediaid.startswith("bangumi:"):
        original = sourceprioritysubscribefix._original_search_endpoints.get(path)
        return await original(request=request, mediaid=mediaid, mtype=mtype, area=area, title=title, year=year,
                              season=season, sites=sites, _=_)

    async def event_source():
        search_chain = SearchChain()
        mediainfo, media_season, error = await _bangumi_search_media(search_chain, mediaid, mtype, season)
        if error:
            yield {"type": "error", "success": False, "message": error}
            return

        contexts = []
        async for event in search_chain.async_process_stream(
                mediainfo=mediainfo,
                sites=_parse_site_list(sites),
                area=area,
                no_exists=_search_no_exists_for_season(media_season)):
            if event.get("type") == "done":
                contexts = event.get("contexts") or []
                event = {
                    key: value
                    for key, value in event.items()
                    if key != "contexts"
                }
            yield event
        await search_chain.async_save_cache(contexts, "__search_result__")

    return StreamingResponse(_stream_search_events(request, event_source()), media_type="text/event-stream")


def _explicit_source_media(chain: SubscribeChain, doubanid: Optional[str], bangumiid: Optional[int],
                           mtype: Optional[MediaType]) -> Optional[MediaInfo]:
    if doubanid:
        return _media_from_douban(chain, doubanid, mtype)
    if bangumiid:
        return _media_from_bangumi(chain, bangumiid)
    return None


async def _async_explicit_source_media(chain: SubscribeChain, doubanid: Optional[str], bangumiid: Optional[int],
                                       mtype: Optional[MediaType]) -> Optional[MediaInfo]:
    if doubanid:
        return await _async_media_from_douban(chain, doubanid, mtype)
    if bangumiid:
        return await _async_media_from_bangumi(chain, bangumiid)
    return None


def _normalize_title_and_season(mediainfo: MediaInfo, season: Optional[int]) -> Optional[int]:
    meta = MetaInfo(mediainfo.title)
    mediainfo.title = meta.name
    return meta.begin_season if season is None else season


def _fill_total_episode(chain: SubscribeChain, mediainfo: MediaInfo, title: str, tmdbid: Optional[int],
                        doubanid: Optional[str], bangumiid: Optional[int], episode_group: Optional[str],
                        season: Optional[int], kwargs: dict) -> tuple[Optional[MediaInfo], Optional[int], Optional[str]]:
    if mediainfo.type != MediaType.TV:
        return mediainfo, None, None
    season = 1 if season is None else season
    if not kwargs.get("total_episode"):
        if not mediainfo.seasons or episode_group:
            mediainfo = chain.recognize_media(
                mtype=mediainfo.type,
                tmdbid=None,
                doubanid=mediainfo.douban_id or doubanid,
                bangumiid=mediainfo.bangumi_id or bangumiid,
                episode_group=episode_group,
                cache=False,
            )
            if not mediainfo:
                logger.error("媒体信息识别失败！")
                return None, season, "媒体信息识别失败"
            if not mediainfo.seasons:
                logger.error(f"媒体信息中没有季集信息，标题：{title}，tmdbid：{tmdbid}，doubanid：{doubanid}")
                return None, season, "媒体信息中没有季集信息"
        total_episode = len(mediainfo.seasons.get(season) or [])
        if not total_episode:
            logger.error(f"未获取到总集数，标题：{title}，tmdbid：{tmdbid}, doubanid：{doubanid}")
            return None, season, f"未获取到第 {season} 季的总集数"
        kwargs["total_episode"] = total_episode
    if not kwargs.get("lack_episode"):
        kwargs["lack_episode"] = kwargs.get("total_episode")
    return mediainfo, season, None


async def _async_fill_total_episode(chain: SubscribeChain, mediainfo: MediaInfo, title: str, tmdbid: Optional[int],
                                    doubanid: Optional[str], bangumiid: Optional[int], episode_group: Optional[str],
                                    season: Optional[int], kwargs: dict) -> tuple[Optional[MediaInfo], Optional[int], Optional[str]]:
    if mediainfo.type != MediaType.TV:
        return mediainfo, None, None
    season = 1 if season is None else season
    if not kwargs.get("total_episode"):
        if not mediainfo.seasons or episode_group:
            mediainfo = await chain.async_recognize_media(
                mtype=mediainfo.type,
                tmdbid=None,
                doubanid=mediainfo.douban_id or doubanid,
                bangumiid=mediainfo.bangumi_id or bangumiid,
                episode_group=episode_group,
                cache=False,
            )
            if not mediainfo:
                logger.error("媒体信息识别失败！")
                return None, season, "媒体信息识别失败"
            if not mediainfo.seasons:
                logger.error(f"媒体信息中没有季集信息，标题：{title}，tmdbid：{tmdbid}，doubanid：{doubanid}")
                return None, season, "媒体信息中没有季集信息"
        total_episode = len(mediainfo.seasons.get(season) or [])
        if not total_episode:
            logger.error(f"未获取到总集数，标题：{title}，tmdbid：{tmdbid}, doubanid：{doubanid}")
            return None, season, f"未获取到第 {season} 季的总集数"
        kwargs["total_episode"] = total_episode
    if not kwargs.get("lack_episode"):
        kwargs["lack_episode"] = kwargs.get("total_episode")
    return mediainfo, season, None


def _patched_subscribe_add(self: SubscribeChain, title: str, year: str, mtype: MediaType = None,
                           tmdbid: Optional[int] = None, doubanid: Optional[str] = None,
                           bangumiid: Optional[int] = None, mediaid: Optional[str] = None,
                           episode_group: Optional[str] = None, season: Optional[int] = None,
                           channel: MessageChannel = None, source: Optional[str] = None,
                           userid: Optional[str] = None, username: Optional[str] = None,
                           message: Optional[bool] = True, exist_ok: Optional[bool] = False,
                           **kwargs) -> Tuple[Optional[int], str]:
    try:
        if not doubanid and not bangumiid:
            return sourceprioritysubscribefix._originals["subscribe_add"](
                self, title, year, mtype, tmdbid, doubanid, bangumiid, mediaid, episode_group,
                season, channel, source, userid, username, message, exist_ok, **kwargs
            )
        logger.info(f"开始添加订阅，标题：{title} ...")
        metainfo = MetaInfo(title)
        if year:
            metainfo.year = year
        if mtype:
            metainfo.type = mtype
        if season is not None:
            metainfo.type = MediaType.TV
            metainfo.begin_season = season

        mediainfo = _explicit_source_media(self, doubanid, bangumiid, mtype)
        if not mediainfo:
            logger.warn(f"未识别到媒体信息，标题：{title}，doubanid：{doubanid}，bangumiid：{bangumiid}")
            return None, "未识别到媒体信息"
        season = _normalize_title_and_season(mediainfo, season)
        mediainfo, season, error = _fill_total_episode(self, mediainfo, title, tmdbid, doubanid, bangumiid, episode_group, season, kwargs)
        if error:
            return None, error
        if not bangumiid:
            self.obtain_images(mediainfo=mediainfo)
        if doubanid:
            mediainfo.douban_id = doubanid
        if bangumiid:
            mediainfo.bangumi_id = bangumiid
        kwargs.update(self._SubscribeChain__get_default_kwargs(mediainfo.type, **kwargs))
        return _create_subscription(self, mediainfo, metainfo, title, year, season, channel, source, userid, username, message, exist_ok, kwargs)
    except Exception as err:
        logger.error(f"订阅外部源优先插件添加订阅异常：{err}\n{traceback.format_exc()}")
        raise


async def _patched_subscribe_async_add(self: SubscribeChain, title: str, year: str, mtype: MediaType = None,
                                       tmdbid: Optional[int] = None, doubanid: Optional[str] = None,
                                       bangumiid: Optional[int] = None, mediaid: Optional[str] = None,
                                       episode_group: Optional[str] = None, season: Optional[int] = None,
                                       channel: MessageChannel = None, source: Optional[str] = None,
                                       userid: Optional[str] = None, username: Optional[str] = None,
                                       message: Optional[bool] = True, exist_ok: Optional[bool] = False,
                                       **kwargs) -> Tuple[Optional[int], str]:
    try:
        if not doubanid and not bangumiid:
            return await sourceprioritysubscribefix._originals["subscribe_async_add"](
                self, title, year, mtype, tmdbid, doubanid, bangumiid, mediaid, episode_group,
                season, channel, source, userid, username, message, exist_ok, **kwargs
            )
        logger.info(f"开始添加订阅，标题：{title} ...")
        metainfo = MetaInfo(title)
        if year:
            metainfo.year = year
        if mtype:
            metainfo.type = mtype
        if season is not None:
            metainfo.type = MediaType.TV
            metainfo.begin_season = season

        mediainfo = await _async_explicit_source_media(self, doubanid, bangumiid, mtype)
        if not mediainfo:
            logger.warn(f"未识别到媒体信息，标题：{title}，doubanid：{doubanid}，bangumiid：{bangumiid}")
            return None, "未识别到媒体信息"
        season = _normalize_title_and_season(mediainfo, season)
        mediainfo, season, error = await _async_fill_total_episode(self, mediainfo, title, tmdbid, doubanid, bangumiid, episode_group, season, kwargs)
        if error:
            return None, error
        if not bangumiid:
            await self.async_obtain_images(mediainfo=mediainfo)
        if doubanid:
            mediainfo.douban_id = doubanid
        if bangumiid:
            mediainfo.bangumi_id = bangumiid
        kwargs.update(self._SubscribeChain__get_default_kwargs(mediainfo.type, **kwargs))
        return await _async_create_subscription(self, mediainfo, metainfo, title, year, season, channel, source, userid, username, message, exist_ok, kwargs)
    except Exception as err:
        logger.error(f"订阅外部源优先插件添加订阅异常：{err}\n{traceback.format_exc()}")
        raise


def _create_subscription(chain: SubscribeChain, mediainfo: MediaInfo, metainfo: MetaInfo, title: str, year: str,
                         season: Optional[int], channel: MessageChannel, source: Optional[str],
                         userid: Optional[str], username: Optional[str], message: bool, exist_ok: bool,
                         kwargs: dict) -> Tuple[Optional[int], str]:
    sid, err_msg = SubscribeOper().add(mediainfo=mediainfo, season=season, username=username, **kwargs)
    if not sid:
        logger.error(f"{mediainfo.title_year} {err_msg}")
        if not exist_ok and message:
            chain.post_message(schemas.Notification(
                channel=channel,
                source=source,
                mtype=NotificationType.Subscribe,
                title=f"{mediainfo.title_year} {metainfo.season} 添加订阅失败！",
                text=f"{err_msg}",
                image=mediainfo.get_message_image(),
                userid=userid,
            ))
        return None, err_msg
    if message:
        link = settings.MP_DOMAIN("#/subscribe/tv?tab=mysub" if mediainfo.type == MediaType.TV else "#/subscribe/movie?tab=mysub")
        chain.post_message(
            schemas.Notification(
                channel=channel,
                source=source,
                mtype=NotificationType.Subscribe,
                ctype=ContentType.SubscribeAdded,
                image=mediainfo.get_message_image(),
                link=link,
                userid=userid,
                username=username,
            ),
            meta=metainfo,
            mediainfo=mediainfo,
            username=username,
        )
    eventmanager.send_event(EventType.SubscribeAdded, {
        "subscribe_id": sid,
        "username": username,
        "mediainfo": mediainfo.to_dict(),
    })
    SubscribeHelper().sub_reg_async(_subscribe_stat_payload(mediainfo, metainfo, title, year))
    _clear_source_subscribe_cache()
    return sid, err_msg


async def _async_create_subscription(chain: SubscribeChain, mediainfo: MediaInfo, metainfo: MetaInfo, title: str, year: str,
                                     season: Optional[int], channel: MessageChannel, source: Optional[str],
                                     userid: Optional[str], username: Optional[str], message: bool, exist_ok: bool,
                                     kwargs: dict) -> Tuple[Optional[int], str]:
    sid, err_msg = await SubscribeOper().async_add(mediainfo=mediainfo, season=season, username=username, **kwargs)
    if not sid:
        logger.error(f"{mediainfo.title_year} {err_msg}")
        if not exist_ok and message:
            await chain.async_post_message(schemas.Notification(
                channel=channel,
                source=source,
                mtype=NotificationType.Subscribe,
                title=f"{mediainfo.title_year} {metainfo.season} 添加订阅失败！",
                text=f"{err_msg}",
                image=mediainfo.get_message_image(),
                userid=userid,
            ))
        return None, err_msg
    if message:
        link = settings.MP_DOMAIN("#/subscribe/tv?tab=mysub" if mediainfo.type == MediaType.TV else "#/subscribe/movie?tab=mysub")
        await chain.async_post_message(
            schemas.Notification(
                channel=channel,
                source=source,
                mtype=NotificationType.Subscribe,
                ctype=ContentType.SubscribeAdded,
                image=mediainfo.get_message_image(),
                link=link,
                userid=userid,
                username=username,
            ),
            meta=metainfo,
            mediainfo=mediainfo,
            username=username,
        )
    await eventmanager.async_send_event(EventType.SubscribeAdded, {
        "subscribe_id": sid,
        "username": username,
        "mediainfo": mediainfo.to_dict(),
    })
    await SubscribeHelper().async_sub_reg(_subscribe_stat_payload(mediainfo, metainfo, title, year))
    _clear_source_subscribe_cache()
    return sid, err_msg


def _subscribe_stat_payload(mediainfo: MediaInfo, metainfo: MetaInfo, title: str, year: str) -> dict:
    return {
        "name": title,
        "year": year,
        "type": mediainfo.type.value,
        "tmdbid": mediainfo.tmdb_id,
        "imdbid": mediainfo.imdb_id,
        "tvdbid": mediainfo.tvdb_id,
        "doubanid": mediainfo.douban_id,
        "bangumiid": mediainfo.bangumi_id,
        "season": metainfo.begin_season,
        "poster": mediainfo.get_poster_image(),
        "backdrop": mediainfo.get_backdrop_image(),
        "vote": mediainfo.vote_average,
        "description": mediainfo.overview,
    }


def _patched_subscribe_exists(mediainfo: MediaInfo, meta: Any = None):
    return SubscribeOper().exists(
        tmdbid=mediainfo.tmdb_id,
        doubanid=mediainfo.douban_id,
        bangumiid=mediainfo.bangumi_id,
        season=meta.begin_season if meta else None,
    )


def _patched_model_exists(cls, db, tmdbid: Optional[int] = None, doubanid: Optional[str] = None,
                          bangumiid: Optional[int] = None, season: Optional[int] = None):
    if tmdbid:
        query = db.query(cls).filter(cls.tmdbid == tmdbid)
        if season is not None:
            query = query.filter(cls.season == season)
        return query.first()
    if doubanid:
        return db.query(cls).filter(cls.doubanid == doubanid).first()
    if bangumiid:
        return db.query(cls).filter(cls.bangumiid == bangumiid).first()
    return None


async def _patched_model_async_exists(cls, db, tmdbid: Optional[int] = None, doubanid: Optional[str] = None,
                                      bangumiid: Optional[int] = None, season: Optional[int] = None):
    if tmdbid:
        stmt = select(cls).filter(cls.tmdbid == tmdbid)
        if season is not None:
            stmt = stmt.filter(cls.season == season)
    elif doubanid:
        stmt = select(cls).filter(cls.doubanid == doubanid)
    elif bangumiid:
        stmt = select(cls).filter(cls.bangumiid == bangumiid)
    else:
        return None
    result = await db.execute(stmt)
    return result.scalars().first()


def _patched_oper_add(self: SubscribeOper, mediainfo: MediaInfo, **kwargs) -> Tuple[int, str]:
    subscribe = Subscribe.exists(
        self._db,
        tmdbid=mediainfo.tmdb_id,
        doubanid=mediainfo.douban_id,
        bangumiid=mediainfo.bangumi_id,
        season=kwargs.get("season"),
    )
    kwargs.update(_subscribe_db_payload(mediainfo, kwargs))
    if not subscribe:
        subscribe = Subscribe(**kwargs)
        subscribe.create(self._db)
        subscribe = Subscribe.exists(
            self._db,
            tmdbid=mediainfo.tmdb_id,
            doubanid=mediainfo.douban_id,
            bangumiid=mediainfo.bangumi_id,
            season=kwargs.get("season"),
        )
        return subscribe.id, "新增订阅成功"
    return subscribe.id, "订阅已存在"


async def _patched_oper_async_add(self: SubscribeOper, mediainfo: MediaInfo, **kwargs) -> Tuple[int, str]:
    subscribe = await Subscribe.async_exists(
        self._db,
        tmdbid=mediainfo.tmdb_id,
        doubanid=mediainfo.douban_id,
        bangumiid=mediainfo.bangumi_id,
        season=kwargs.get("season"),
    )
    kwargs.update(_subscribe_db_payload(mediainfo, kwargs))
    if not subscribe:
        subscribe = Subscribe(**kwargs)
        await subscribe.async_create(self._db)
        subscribe = await Subscribe.async_exists(
            self._db,
            tmdbid=mediainfo.tmdb_id,
            doubanid=mediainfo.douban_id,
            bangumiid=mediainfo.bangumi_id,
            season=kwargs.get("season"),
        )
        return subscribe.id, "新增订阅成功"
    return subscribe.id, "订阅已存在"


def _subscribe_db_payload(mediainfo: MediaInfo, kwargs: dict) -> dict:
    return {
        "name": mediainfo.title,
        "year": mediainfo.year,
        "type": mediainfo.type.value,
        "tmdbid": mediainfo.tmdb_id,
        "imdbid": mediainfo.imdb_id,
        "tvdbid": mediainfo.tvdb_id,
        "doubanid": mediainfo.douban_id,
        "bangumiid": mediainfo.bangumi_id,
        "episode_group": mediainfo.episode_group,
        "poster": mediainfo.get_poster_image(),
        "backdrop": mediainfo.get_backdrop_image(),
        "vote": mediainfo.vote_average,
        "description": mediainfo.overview,
        "media_category": kwargs.get("media_category") or mediainfo.category,
        "search_imdbid": 1 if kwargs.get("search_imdbid") else 0,
        "date": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
    }


def _patched_oper_exists(self: SubscribeOper, tmdbid: Optional[int] = None, doubanid: Optional[str] = None,
                         bangumiid: Optional[int] = None, season: Optional[int] = None) -> bool:
    return bool(Subscribe.exists(self._db, tmdbid=tmdbid, doubanid=doubanid, bangumiid=bangumiid, season=season))


def _patched_oper_exist_history(self: SubscribeOper, tmdbid: Optional[int] = None, doubanid: Optional[str] = None,
                                bangumiid: Optional[int] = None, season: Optional[int] = None):
    return bool(SubscribeHistory.exists(self._db, tmdbid=tmdbid, doubanid=doubanid, bangumiid=bangumiid, season=season))


def _season_poster_path(poster_path: Optional[str]) -> Optional[str]:
    if poster_path and poster_path.startswith("/"):
        return poster_path
    return None


def _media_seasons_from_info(mediainfo: MediaInfo, season: Optional[int] = None) -> List[schemas.MediaSeason]:
    if not mediainfo or mediainfo.type != MediaType.TV:
        return []
    if getattr(mediainfo, "season_info", None):
        seasons_info = [
            schemas.MediaSeason(**{**info, "poster_path": _season_poster_path(info.get("poster_path"))})
            for info in mediainfo.season_info
            if season is None or info.get("season_number") == season
        ]
        if seasons_info:
            return seasons_info
    season_numbers = sorted((mediainfo.seasons or {}).keys())
    if not season_numbers and (mediainfo.number_of_episodes or mediainfo.season is not None):
        season_numbers = [mediainfo.season if mediainfo.season is not None else 1]
    seasons_info = []
    for season_number in season_numbers:
        if season is not None and season_number != season:
            continue
        episodes = (mediainfo.seasons or {}).get(season_number) or []
        seasons_info.append(schemas.MediaSeason(
            season_number=season_number,
            poster_path=_season_poster_path(mediainfo.poster_path),
            name=f"第 {season_number} 季",
            air_date=mediainfo.release_date,
            overview=mediainfo.overview,
            vote_average=mediainfo.vote_average,
            episode_count=len(episodes) or mediainfo.number_of_episodes,
        ))
    return seasons_info


async def _patched_media_seasons(mediaid: Optional[str] = None,
                                 title: Optional[str] = None,
                                 year: str = None,
                                 season: int = None,
                                 _: schemas.TokenPayload = Depends(verify_token)) -> Any:
    mediachain = MediaChain()
    if mediaid:
        if mediaid.startswith("tmdb:"):
            seasons_info = await TmdbChain().async_tmdb_seasons(tmdbid=int(mediaid[5:]))
            if seasons_info:
                return [sea for sea in seasons_info if sea.season_number == season] if season is not None else seasons_info
        elif mediaid.startswith("douban:"):
            mediainfo = await mediachain.async_recognize_media(doubanid=mediaid[7:], mtype=MediaType.TV)
            seasons_info = _media_seasons_from_info(mediainfo, season=season)
            if seasons_info:
                return seasons_info
        elif mediaid.startswith("bangumi:") and mediaid[8:].isdigit():
            mediainfo = await mediachain.async_recognize_media(bangumiid=int(mediaid[8:]), mtype=MediaType.TV)
            seasons_info = _media_seasons_from_info(mediainfo, season=season)
            if seasons_info:
                return seasons_info
    if title:
        meta = MetaInfo(title)
        if year:
            meta.year = year
        mediainfo = await mediachain.async_recognize_media(meta, mtype=MediaType.TV)
        if mediainfo:
            if settings.RECOGNIZE_SOURCE == "themoviedb" and mediainfo.tmdb_id:
                seasons_info = await TmdbChain().async_tmdb_seasons(tmdbid=mediainfo.tmdb_id)
                if seasons_info:
                    return [sea for sea in seasons_info if sea.season_number == season] if season is not None else seasons_info
            seasons_info = _media_seasons_from_info(mediainfo, season=season)
            if seasons_info:
                return seasons_info
            if mediainfo.number_of_episodes:
                sea = season if season is not None else 1
                return [schemas.MediaSeason(
                    season_number=sea,
                    poster_path=_season_poster_path(mediainfo.poster_path),
                    name=f"第 {sea} 季",
                    air_date=mediainfo.release_date,
                    overview=mediainfo.overview,
                    vote_average=mediainfo.vote_average,
                    episode_count=mediainfo.number_of_episodes,
                )]
    return []
