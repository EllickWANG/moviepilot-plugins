from __future__ import annotations

import json
import subprocess
import time
import traceback
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, List, Optional, Tuple
from xml.dom import minidom

from fastapi import Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app import schemas
from app.api.endpoints.search import _stream_search_events
from app.chain.download import DownloadChain
from app.chain.mediaserver import MediaServerChain
from app.chain.media import MediaChain
from app.chain.search import SearchChain
from app.chain.storage import StorageChain
from app.chain.subscribe import SubscribeChain
from app.chain.transfer import TransferChain
from app.chain.tmdb import TmdbChain
from app.core.config import settings
from app.core.context import Context, MediaInfo
from app.core.event import eventmanager
from app.core.metainfo import MetaInfo, MetaInfoPath
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
from app.schemas.types import ContentType, EventType, ModuleType, NotificationType, ScrapingTarget
from app.helper.subscribe import SubscribeHelper
from app.helper.torrent import TorrentHelper
from app.utils.dom import DomUtils


class sourceprioritysubscribefix(_PluginBase):
    plugin_name = "订阅外部源优先"
    plugin_desc = "订阅时有 doubanid/bangumiid 则直接使用对应来源详情，避免强制转 TMDB。"
    plugin_icon = "mdi-heart-cog"
    plugin_version = "1.0.34"
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
            },
            {
                "path": "/refresh",
                "endpoint": _plugin_refresh_bangumi_media,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "刷新Bangumi媒体库",
                "description": "重新触发最近 Bangumi-only 整理记录所在媒体库扫描，用于修复媒体服务器旧缓存。",
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

    def get_module(self) -> dict[str, Any]:
        return {
            "metadata_nfo": _bangumi_metadata_nfo,
            "metadata_img": _bangumi_metadata_img,
        }

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
            "download_single": DownloadChain.download_single,
            "media_recognize_by_meta": MediaChain.recognize_by_meta,
            "media_recognize_by_path": MediaChain.recognize_by_path,
            "media_handle_tv_episode_file": MediaChain._handle_tv_episode_file,
            "media_initialize_tv_directory_metadata": MediaChain._initialize_tv_directory_metadata,
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
        DownloadChain.download_single = _patched_download_single
        MediaChain.recognize_by_meta = _patched_media_recognize_by_meta
        MediaChain.recognize_by_path = _patched_media_recognize_by_path
        MediaChain._handle_tv_episode_file = _patched_media_handle_tv_episode_file
        MediaChain._initialize_tv_directory_metadata = _patched_media_initialize_tv_directory_metadata
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
        DownloadChain.download_single = cls._originals["download_single"]
        MediaChain.recognize_by_meta = cls._originals["media_recognize_by_meta"]
        MediaChain.recognize_by_path = cls._originals["media_recognize_by_path"]
        MediaChain._handle_tv_episode_file = cls._originals["media_handle_tv_episode_file"]
        MediaChain._initialize_tv_directory_metadata = cls._originals["media_initialize_tv_directory_metadata"]
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

_MEDIA_SERVER_REFRESH_CACHE: dict[str, float] = {}
_MEDIA_SERVER_COMPAT_ID_CACHE: dict[str, tuple[float, Optional[int]]] = {}
_TMDB_EPISODE_STILL_CACHE: dict[str, tuple[float, dict[int, str]]] = {}


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


def _title_candidates_from_text(text: Any) -> set[str]:
    candidates = {text}
    try:
        meta = MetaInfo(str(text))
        candidates.add(meta.name)
        candidates.add(meta.title)
        candidates.add(meta.cn_name)
        candidates.add(meta.org_string)
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
        subscribe_type = _type_value(subscribe.type)
        if meta_type and subscribe_type and meta_type != subscribe_type:
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


def _season_number(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    match = re.search(r"(\d+)", text)
    if not match:
        return None
    return _int_or_none(match.group(1))


def _match_subscribe_by_download_history(download_history: Any) -> Optional[Subscribe]:
    if not download_history:
        return None
    history_titles = _title_candidates_from_text(getattr(download_history, "title", None))
    torrent_name = getattr(download_history, "torrent_name", None)
    if torrent_name:
        history_titles.update(_title_candidates_from_text(torrent_name))
    if not history_titles:
        return None

    history_type = _type_value(_media_type_or_none(getattr(download_history, "type", None)))
    history_season = _season_number(getattr(download_history, "seasons", None))
    history_year = str(getattr(download_history, "year", "") or "").strip()

    for subscribe in _source_only_subscribes():
        subscribe_type = _type_value(subscribe.type)
        if history_type and subscribe_type and history_type != subscribe_type:
            continue
        if history_season is not None and subscribe.season is not None and history_season != subscribe.season:
            continue
        if history_year and subscribe.year and str(subscribe.year) != history_year:
            continue
        sub_titles = _title_candidates_from_subscribe(subscribe)
        if history_titles.intersection(sub_titles):
            return subscribe
        for history_title in history_titles:
            if len(history_title) >= 4 and any(
                    history_title in sub_title or sub_title in history_title
                    for sub_title in sub_titles
            ):
                return subscribe
    return None


def _match_subscribe_by_transfer_history(history: Any) -> Optional[Subscribe]:
    if not history:
        return None
    history_titles = _title_candidates_from_text(getattr(history, "title", None))
    history_dest = getattr(history, "dest", None)
    if history_dest:
        history_titles.update(_title_candidates_from_text(Path(history_dest).name))
    if not history_titles:
        return None

    history_type = _type_value(_media_type_or_none(getattr(history, "type", None)))
    history_season = _season_number(getattr(history, "seasons", None))
    history_year = str(getattr(history, "year", "") or "").strip()

    for subscribe in _source_only_subscribes():
        subscribe_type = _type_value(subscribe.type)
        if history_type and subscribe_type and history_type != subscribe_type:
            continue
        if history_season is not None and subscribe.season is not None and history_season != subscribe.season:
            continue
        if history_year and subscribe.year and str(subscribe.year) != history_year:
            continue
        sub_titles = _title_candidates_from_subscribe(subscribe)
        if history_titles.intersection(sub_titles):
            return subscribe
        for history_title in history_titles:
            if len(history_title) >= 4 and any(
                    history_title in sub_title or sub_title in history_title
                    for sub_title in sub_titles
            ):
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
    if not mediainfo.genre_ids and (mediainfo.bangumi_info or {}).get("type") == 2:
        mediainfo.genre_ids = settings.ANIME_GENREIDS or [16]
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
    if not download_history:
        return None
    source_keyword = SubscribeChain.parse_subscribe_source_keyword(_download_history_source(download_history))
    subscribe = _subscribe_from_source_keyword(source_keyword) if source_keyword else None
    if not subscribe:
        subscribe = _match_subscribe_by_download_history(download_history)
        if subscribe:
            logger.info(
                f"{getattr(download_history, 'title', '')} 通过下载历史匹配到Bangumi订阅："
                f"{subscribe.name} bangumi:{subscribe.bangumiid}"
            )
    bangumiid = _int_or_none(
        getattr(subscribe, "bangumiid", None)
        or (source_keyword or {}).get("bangumiid")
    )
    if not bangumiid:
        return None
    try:
        mediainfo = _media_from_bangumi(chain, bangumiid)
    except Exception as err:
        logger.warn(f"订阅外部源优先插件获取Bangumi详情失败：{bangumiid} - {err}")
        return None
    if not mediainfo:
        return None
    mtype = (
        _media_type_or_none(getattr(download_history, "type", None))
        or _media_type_or_none((source_keyword or {}).get("type"))
        or _media_type_or_none(getattr(subscribe, "type", None))
    )
    if mtype:
        mediainfo.type = mtype
    season = (
        _int_or_none((source_keyword or {}).get("season"))
        or _season_number(getattr(download_history, "seasons", None))
        or _season_number(getattr(subscribe, "season", None))
    )
    if mediainfo.type == MediaType.TV and season is not None:
        mediainfo.season = season
    if subscribe and getattr(subscribe, "media_category", None):
        mediainfo.category = subscribe.media_category
    elif not mediainfo.category and getattr(download_history, "media_category", None):
        mediainfo.category = download_history.media_category
    return _mark_bangumi_media_ready(_apply_subscribe_ids(mediainfo, subscribe))


def _source_media_from_source(
        chain: Any,
        source: Optional[str],
        current_media: Optional[MediaInfo] = None,
        meta: Any = None) -> tuple[Optional[MediaInfo], Optional[Subscribe]]:
    source_keyword = SubscribeChain.parse_subscribe_source_keyword(source)
    subscribe = _subscribe_from_source_keyword(source_keyword) if source_keyword else None
    if not subscribe and current_media and current_media.bangumi_id:
        subscribe = SubscribeOper().get_by(bangumiid=current_media.bangumi_id)
    if not subscribe and meta:
        subscribe = _match_subscribe_by_meta(meta, getattr(current_media, "type", None))

    bangumiid = _int_or_none(
        getattr(subscribe, "bangumiid", None)
        or (source_keyword or {}).get("bangumiid")
        or getattr(current_media, "bangumi_id", None)
    )
    doubanid = (
        getattr(subscribe, "doubanid", None)
        or (source_keyword or {}).get("doubanid")
        or getattr(current_media, "douban_id", None)
    )
    mtype = (
        _media_type_or_none(getattr(current_media, "type", None))
        or _media_type_or_none((source_keyword or {}).get("type"))
        or _media_type_or_none(getattr(subscribe, "type", None))
    )
    if bangumiid:
        mediainfo = _media_from_bangumi(chain, bangumiid)
    elif doubanid:
        mediainfo = _media_from_douban(chain, doubanid, mtype)
    else:
        return None, subscribe
    if not mediainfo:
        return None, subscribe
    if mtype:
        mediainfo.type = mtype
    season = (
        _int_or_none((source_keyword or {}).get("season"))
        or _season_number(getattr(subscribe, "season", None))
        or _season_number(getattr(meta, "begin_season", None))
    )
    if mediainfo.type == MediaType.TV and season is not None:
        mediainfo.season = season
    if subscribe and getattr(subscribe, "media_category", None):
        mediainfo.category = subscribe.media_category
    return _mark_bangumi_media_ready(_apply_subscribe_ids(mediainfo, subscribe)), subscribe


def _apply_download_source_category(chain: Any, context: Any, source: Optional[str]) -> None:
    if not context or not getattr(context, "media_info", None):
        return
    current_media: MediaInfo = context.media_info
    source_media, subscribe = _source_media_from_source(
        chain=chain,
        source=source,
        current_media=current_media,
        meta=getattr(context, "meta_info", None),
    )
    source_category = (
        getattr(subscribe, "media_category", None)
        or getattr(source_media, "category", None)
    )
    if not source_category:
        return
    old_category = current_media.category
    if old_category == source_category:
        return
    current_media.category = source_category
    if source_media:
        source_media = _mark_bangumi_media_ready(source_media)
        current_media.bangumi_id = source_media.bangumi_id or current_media.bangumi_id
        current_media.douban_id = source_media.douban_id or current_media.douban_id
        current_media.episode_group = source_media.episode_group or current_media.episode_group
        current_media.genre_ids = current_media.genre_ids or source_media.genre_ids
        current_media.names = current_media.names or source_media.names
        current_media.bangumi_info = current_media.bangumi_info or source_media.bangumi_info
        current_media.seasons = current_media.seasons or source_media.seasons
        current_media.season_years = current_media.season_years or source_media.season_years
    logger.info(
        f"{getattr(current_media, 'title_year', None) or getattr(current_media, 'title', '')} "
        f"下载二级分类按订阅来源修正：{old_category or '-'} -> {source_category}"
    )


def _patched_download_single(self: DownloadChain, *args, **kwargs):
    args_list = list(args)
    context = kwargs.get("context") or (args_list[0] if args_list else None)
    source = kwargs.get("source")
    if source is None and len(args_list) > 5:
        source = args_list[5]
    try:
        _apply_download_source_category(self, context, source)
    except Exception as err:
        logger.warn(f"订阅外部源优先插件修正下载二级分类失败：{err}")
    return sourceprioritysubscribefix._originals["download_single"](self, *args_list, **kwargs)


def _source_media_from_meta(
        chain: Any,
        meta: Any,
        mtype: Optional[MediaType] = None) -> Optional[MediaInfo]:
    subscribe = _match_subscribe_by_meta(meta, mtype)
    if not subscribe or not subscribe.bangumiid:
        return None
    try:
        mediainfo = _media_from_bangumi(chain, subscribe.bangumiid)
    except Exception as err:
        logger.warn(f"{getattr(meta, 'title', '')} 获取Bangumi订阅来源失败：{err}")
        return None
    if not mediainfo:
        return None
    media_type = mtype or _media_type_or_none(getattr(subscribe, "type", None))
    if media_type:
        mediainfo.type = media_type
    season = (
        _season_number(getattr(meta, "begin_season", None))
        or _season_number(getattr(subscribe, "season", None))
    )
    if mediainfo.type == MediaType.TV and season is not None:
        mediainfo.season = season
    if getattr(subscribe, "media_category", None):
        mediainfo.category = subscribe.media_category
    return _mark_bangumi_media_ready(_apply_subscribe_ids(mediainfo, subscribe))


def _patched_media_recognize_by_meta(
        self: MediaChain,
        metainfo: Any,
        episode_group: Optional[str] = None) -> Optional[MediaInfo]:
    source_mediainfo = _source_media_from_meta(self, metainfo, getattr(metainfo, "type", None))
    if source_mediainfo:
        source_mediainfo.episode_group = episode_group or source_mediainfo.episode_group
        logger.info(
            f"{getattr(metainfo, 'title', '')} 使用Bangumi订阅来源识别："
            f"{source_mediainfo.title_year}"
        )
        return source_mediainfo
    return sourceprioritysubscribefix._originals["media_recognize_by_meta"](
        self, metainfo, episode_group=episode_group
    )


def _patched_media_recognize_by_path(
        self: MediaChain,
        path: str,
        episode_group: Optional[str] = None) -> Optional[Context]:
    file_path = Path(path)
    file_meta = MetaInfoPath(file_path)
    source_mediainfo = _source_media_from_meta(self, file_meta, getattr(file_meta, "type", None))
    if source_mediainfo:
        source_mediainfo.episode_group = episode_group or source_mediainfo.episode_group
        logger.info(f"{path} 使用Bangumi订阅来源识别：{source_mediainfo.title_year}")
        return Context(meta_info=file_meta, media_info=source_mediainfo)
    return sourceprioritysubscribefix._originals["media_recognize_by_path"](
        self, path, episode_group=episode_group
    )


def _patched_media_handle_tv_episode_file(
        self: MediaChain,
        fileitem: schemas.FileItem,
        filepath: Path,
        mediainfo: MediaInfo,
        parent: schemas.FileItem,
        overwrite: bool):
    if not _is_bangumi_metadata_media(mediainfo):
        return sourceprioritysubscribefix._originals["media_handle_tv_episode_file"](
            self, fileitem, filepath, mediainfo, parent, overwrite
        )

    file_meta = MetaInfoPath(filepath)
    if not file_meta.begin_episode:
        logger.warn(f"{filepath.name} 无法识别文件集数！")
        return

    file_mediainfo = mediainfo
    if not getattr(file_mediainfo, "bangumi_info", None):
        try:
            source_mediainfo = _media_from_bangumi(self, file_mediainfo.bangumi_id)
        except Exception as err:
            logger.warn(
                f"{filepath.name} 获取Bangumi详情失败："
                f"{file_mediainfo.bangumi_id} - {err}"
            )
            source_mediainfo = None
        if source_mediainfo:
            source_mediainfo.type = file_mediainfo.type or source_mediainfo.type
            source_mediainfo.season = (
                file_mediainfo.season
                if file_mediainfo.season is not None
                else source_mediainfo.season
            )
            source_mediainfo.category = file_mediainfo.category or source_mediainfo.category
            file_mediainfo = source_mediainfo

    season_number = (
        file_meta.begin_season
        if file_meta.begin_season is not None
        else file_mediainfo.season if file_mediainfo.season is not None
        else 1
    )
    episode_number = file_meta.begin_episode
    logger.info(
        f"{filepath.name} 使用Bangumi本地NFO刮削单集："
        f"S{int(season_number):02d}E{int(episode_number):02d}"
    )

    self._scrape_nfo_generic(
        current_fileitem=fileitem,
        meta=file_meta,
        mediainfo=_mark_bangumi_media_ready(file_mediainfo),
        item_type=ScrapingTarget.EPISODE,
        parent_fileitem=parent,
        overwrite=overwrite,
        season_number=season_number,
        episode_number=episode_number,
    )
    self._scrape_images_generic(
        current_fileitem=fileitem,
        mediainfo=file_mediainfo,
        item_type=ScrapingTarget.EPISODE,
        parent_fileitem=parent,
        overwrite=overwrite,
        season_number=season_number,
        episode_number=episode_number,
    )


def _bangumi_tv_root_candidates(mediainfo: Optional[MediaInfo]) -> set[str]:
    if not mediainfo:
        return set()
    raw_titles = {
        getattr(mediainfo, "title", None),
        getattr(mediainfo, "original_title", None),
        getattr(mediainfo, "en_title", None),
    }
    for name in getattr(mediainfo, "names", None) or []:
        raw_titles.add(name)

    candidates = set()
    year = str(getattr(mediainfo, "year", "") or "").strip()
    for title in raw_titles:
        normalized = _normalize_match_text(title)
        if not normalized:
            continue
        candidates.add(normalized)
        if year:
            candidates.add(_normalize_match_text(f"{title}{year}"))
            candidates.add(_normalize_match_text(f"{title} ({year})"))
    title_year = getattr(mediainfo, "title_year", None)
    if title_year:
        candidates.add(_normalize_match_text(title_year))
    return candidates


def _is_bangumi_tv_root_directory(filepath: Path, mediainfo: Optional[MediaInfo]) -> bool:
    if not _is_bangumi_metadata_media(mediainfo) or mediainfo.type != MediaType.TV:
        return False
    return _normalize_match_text(filepath.name) in _bangumi_tv_root_candidates(mediainfo)


def _delete_wrong_bangumi_root_season_nfo(
        storagechain: StorageChain,
        fileitem: schemas.FileItem,
        filepath: Path) -> None:
    try:
        wrong_nfo = storagechain.get_file_item(
            storage=fileitem.storage,
            path=filepath / "season.nfo",
        )
        if wrong_nfo and wrong_nfo.type == "file":
            storagechain.delete_file(wrong_nfo)
            logger.info(f"已清理Bangumi剧集根目录错误季NFO：{wrong_nfo.path}")
    except Exception as err:
        logger.warn(f"清理Bangumi剧集根目录错误季NFO失败：{filepath} - {err}")


def _patched_media_initialize_tv_directory_metadata(
        self: MediaChain,
        fileitem: schemas.FileItem,
        filepath: Path,
        meta: Any,
        mediainfo: MediaInfo,
        parent: schemas.FileItem,
        overwrite: bool):
    if not _is_bangumi_tv_root_directory(filepath, mediainfo):
        return sourceprioritysubscribefix._originals["media_initialize_tv_directory_metadata"](
            self,
            fileitem=fileitem,
            filepath=filepath,
            meta=meta,
            mediainfo=mediainfo,
            parent=parent,
            overwrite=overwrite,
        )

    # Bangumi 条目的标题常自带“第 N 季”，不能让根目录被当成季目录。
    logger.info(f"{filepath.name} 作为Bangumi剧集根目录刮削")
    _delete_wrong_bangumi_root_season_nfo(self.storagechain, fileitem, filepath)
    self._scrape_nfo_generic(
        current_fileitem=fileitem,
        meta=meta,
        mediainfo=_mark_bangumi_media_ready(mediainfo),
        item_type=ScrapingTarget.TV,
        overwrite=overwrite,
    )
    self._scrape_images_generic(
        current_fileitem=fileitem,
        mediainfo=mediainfo,
        item_type=ScrapingTarget.TV,
        overwrite=overwrite,
    )


def _is_bangumi_metadata_media(mediainfo: Optional[MediaInfo]) -> bool:
    return bool(mediainfo and mediainfo.bangumi_id)


def _add_cdata(doc: minidom.Document, root: minidom.Node, name: str, value: Any) -> None:
    node = DomUtils.add_node(doc, root, name)
    node.appendChild(doc.createCDATASection(str(value or "")))


def _add_text_node(doc: minidom.Document, root: minidom.Node, name: str, value: Any) -> None:
    DomUtils.add_node(doc, root, name, str(value or ""))


def _bangumi_unique_id(
        doc: minidom.Document,
        root: minidom.Node,
        mediainfo: MediaInfo,
        suffix: Optional[str] = None) -> None:
    bid = str(mediainfo.bangumi_id)
    value = f"{bid}-{suffix}" if suffix else bid
    uniqueid = DomUtils.add_node(doc, root, "uniqueid", value)
    uniqueid.setAttribute("type", "bangumi")
    uniqueid.setAttribute("default", "true")
    DomUtils.add_node(doc, root, "bangumiid", bid)


def _bangumi_genres(mediainfo: MediaInfo) -> list[str]:
    genres = []
    seen = set()
    for value in _bangumi_tag_values(mediainfo.bangumi_info or {}):
        value = str(value).strip()
        key = _normalize_match_text(value)
        if not key or key in seen:
            continue
        genres.append(value)
        seen.add(key)
        if len(genres) >= 12:
            break
    return genres


_BANGUMI_TV_SEASON_SUFFIX_PATTERNS = (
    re.compile(r"\s+第\s*[0-9一二三四五六七八九十百零〇两]+\s*(?:季|期|部|章)\s*.*$", re.I),
    re.compile(r"\s+\d{1,2}(?:st|nd|rd|th)\s+season\s*.*$", re.I),
    re.compile(r"\s+season\s*0*\d{1,2}\s*.*$", re.I),
    re.compile(r"\s+s\s*0*\d{1,2}\s*.*$", re.I),
    re.compile(r"\s+\d{1,2}\s*(?:季|期|部|章)\s*.*$", re.I),
)


def _normalize_bangumi_title_text(title: Optional[str]) -> str:
    text = re.sub(r"\s+", " ", str(title or "")).strip()
    return re.sub(r"\s*[-_·:：]+\s*$", "", text).strip()


def _strip_bangumi_tv_season_suffix(title: Optional[str]) -> str:
    text = _normalize_bangumi_title_text(title)
    if not text:
        return ""
    for pattern in _BANGUMI_TV_SEASON_SUFFIX_PATTERNS:
        candidate = pattern.sub("", text).strip()
        candidate = _normalize_bangumi_title_text(candidate)
        if candidate and candidate != text and len(_normalize_match_text(candidate)) >= 2:
            return candidate
    return text


def _has_bangumi_tv_season_suffix(title: Optional[str]) -> bool:
    text = _normalize_bangumi_title_text(title)
    return bool(text and _strip_bangumi_tv_season_suffix(text) != text)


def _bangumi_tv_show_title_candidates(mediainfo: MediaInfo, original: bool = False) -> list[str]:
    info = mediainfo.bangumi_info or {}
    localized_values = [
        info.get("name_cn"),
        mediainfo.title,
        mediainfo.hk_title,
        mediainfo.tw_title,
        mediainfo.sg_title,
    ]
    original_values = [
        info.get("name"),
        mediainfo.original_title,
        mediainfo.en_title,
    ]
    if original:
        values = original_values + _bangumi_infobox_aliases(info) + list(mediainfo.names or []) + localized_values
    else:
        values = localized_values + _bangumi_infobox_aliases(info) + list(mediainfo.names or []) + original_values
    return _dedupe_aliases(values)


def _bangumi_tv_show_title(mediainfo: MediaInfo, original: bool = False) -> str:
    # 媒体服务器用 show title 聚合分集。优先使用 Bangumi 数据里已经存在的系列名，
    # 避免把“第 N 季/篇章名”写进 showtitle 后被识别成另一部剧。
    candidates = _bangumi_tv_show_title_candidates(mediainfo, original=original)
    for candidate in candidates:
        title = _normalize_bangumi_title_text(candidate)
        if title and not _has_bangumi_tv_season_suffix(title):
            return title
    fallback = mediainfo.original_title if original else mediainfo.title
    fallback = fallback or mediainfo.title or mediainfo.original_title or ""
    return _strip_bangumi_tv_season_suffix(fallback)


def _media_server_compat_cache_key(mediainfo: MediaInfo, title: str) -> str:
    return "|".join([
        _type_value(getattr(mediainfo, "type", None)) or "",
        _normalize_match_text(title),
        str(getattr(mediainfo, "year", None) or ""),
    ])


def _tmdb_id_from_media_server_item(item: Any) -> Optional[int]:
    tmdbid = getattr(item, "tmdbid", None)
    if not tmdbid and getattr(item, "trim_id", None):
        trim_id = str(getattr(item, "trim_id"))
        if trim_id.startswith(("tt", "tm")) and trim_id[2:].isdigit():
            tmdbid = trim_id[2:]
    return _int_or_none(tmdbid)


def _lookup_existing_tv_tmdb_id(server: Any, title: str, season: Optional[int]) -> Optional[int]:
    title_key = _normalize_match_text(title)
    api = getattr(server, "_api", None)
    if api and hasattr(api, "search_list"):
        try:
            for item in api.search_list(title) or []:
                item_type = _type_value(getattr(getattr(item, "type", None), "value", getattr(item, "type", None)))
                if item_type and item_type not in ("TV", MediaType.TV.value):
                    continue
                item_title = getattr(item, "title", None)
                if _normalize_match_text(item_title) != title_key:
                    continue
                tmdbid = _tmdb_id_from_media_server_item(item)
                if tmdbid:
                    return tmdbid
        except Exception:
            pass
    if not (hasattr(server, "get_tv_episodes") and hasattr(server, "get_iteminfo")):
        return None
    try:
        item_id, _ = server.get_tv_episodes(title=title, year=None, season=season)
    except Exception:
        return None
    if not item_id:
        return None
    try:
        return _tmdb_id_from_media_server_item(server.get_iteminfo(item_id))
    except Exception:
        return None


def _existing_media_server_tmdb_id(mediainfo: MediaInfo) -> Optional[int]:
    tmdbid = _int_or_none(getattr(mediainfo, "tmdb_id", None))
    if tmdbid:
        return tmdbid
    media_type = _type_value(getattr(mediainfo, "type", None))
    if media_type not in (MediaType.TV.value, "TV"):
        return None
    title = _bangumi_tv_show_title(mediainfo)
    if not title:
        return None
    cache_key = _media_server_compat_cache_key(mediainfo, title)
    now = time.time()
    cached = _MEDIA_SERVER_COMPAT_ID_CACHE.get(cache_key)
    if cached and now - cached[0] < 600:
        return cached[1]
    result = None
    try:
        media_chain = MediaServerChain()
        for module in media_chain.modulemanager.get_running_type_modules(ModuleType.MediaServer):
            if not hasattr(module, "get_instances"):
                continue
            for _, server in (module.get_instances() or {}).items():
                if not server:
                    continue
                result = _lookup_existing_tv_tmdb_id(server, title, getattr(mediainfo, "season", None))
                if result:
                    break
            if result:
                break
    except Exception as err:
        logger.debug(f"Bangumi媒体服务器兼容ID查询失败：{title} - {err}")
    _MEDIA_SERVER_COMPAT_ID_CACHE[cache_key] = (now, result)
    if result:
        logger.info(f"{mediainfo.title_year} 使用现有媒体服务器TMDBID兼容聚合：{result}")
    return result


def _add_tmdb_unique_id(
        doc: minidom.Document,
        root: minidom.Node,
        mediainfo: MediaInfo) -> None:
    tmdbid = _existing_media_server_tmdb_id(mediainfo)
    if not tmdbid:
        return
    uniqueid = DomUtils.add_node(doc, root, "uniqueid", str(tmdbid))
    uniqueid.setAttribute("type", "tmdb")
    uniqueid.setAttribute("default", "false")
    DomUtils.add_node(doc, root, "tmdbid", str(tmdbid))


def _bangumi_add_common_nfo(
        doc: minidom.Document,
        root: minidom.Node,
        mediainfo: MediaInfo,
        unique_suffix: Optional[str] = None) -> None:
    _bangumi_unique_id(doc, root, mediainfo, unique_suffix)
    _add_cdata(doc, root, "plot", mediainfo.overview or "")
    _add_cdata(doc, root, "outline", mediainfo.overview or "")
    _add_text_node(doc, root, "rating", mediainfo.vote_average or "0")
    _add_text_node(doc, root, "premiered", mediainfo.release_date or "")
    _add_text_node(doc, root, "year", mediainfo.year or "")
    for genre in _bangumi_genres(mediainfo):
        _add_text_node(doc, root, "genre", genre)
    for actor in mediainfo.actors or []:
        name = actor.get("name") or actor.get("name_cn") or actor.get("subject_name")
        if not name:
            continue
        xactor = DomUtils.add_node(doc, root, "actor")
        _add_text_node(doc, xactor, "name", name)
        _add_text_node(doc, xactor, "type", "Actor")
        role = actor.get("character") or actor.get("role") or actor.get("career")
        if isinstance(role, list):
            role = "、".join(str(item) for item in role if item)
        _add_text_node(doc, xactor, "role", role or "")
        images = actor.get("images") or {}
        thumb = images.get("medium") or images.get("large") or images.get("small")
        if thumb:
            _add_text_node(doc, xactor, "thumb", thumb)
        if actor.get("id"):
            _add_text_node(doc, xactor, "profile", f"https://bgm.tv/person/{actor.get('id')}")


def _bangumi_tv_nfo(mediainfo: MediaInfo) -> minidom.Document:
    doc = minidom.Document()
    root = DomUtils.add_node(doc, doc, "tvshow")
    _bangumi_add_common_nfo(doc, root, mediainfo)
    _add_tmdb_unique_id(doc, root, mediainfo)
    _add_text_node(doc, root, "title", _bangumi_tv_show_title(mediainfo))
    _add_text_node(doc, root, "originaltitle", _bangumi_tv_show_title(mediainfo, original=True))
    _add_text_node(doc, root, "season", "-1")
    _add_text_node(doc, root, "episode", "-1")
    return doc


def _bangumi_movie_nfo(mediainfo: MediaInfo) -> minidom.Document:
    doc = minidom.Document()
    root = DomUtils.add_node(doc, doc, "movie")
    _bangumi_add_common_nfo(doc, root, mediainfo)
    _add_text_node(doc, root, "title", mediainfo.title or "")
    _add_text_node(doc, root, "originaltitle", mediainfo.original_title or "")
    return doc


def _bangumi_season_nfo(mediainfo: MediaInfo, season: int) -> minidom.Document:
    doc = minidom.Document()
    root = DomUtils.add_node(doc, doc, "season")
    _bangumi_add_common_nfo(doc, root, mediainfo, f"s{season}")
    _add_text_node(doc, root, "title", f"第 {season} 季")
    _add_text_node(doc, root, "seasonnumber", season)
    return doc


def _bangumi_episode_title(episode: int) -> str:
    return f"第 {episode} 集"


def _bangumi_episode_nfo(mediainfo: MediaInfo, season: int, episode: int) -> minidom.Document:
    doc = minidom.Document()
    root = DomUtils.add_node(doc, doc, "episodedetails")
    _bangumi_add_common_nfo(doc, root, mediainfo, f"s{season}e{episode}")
    title = _bangumi_episode_title(episode)
    episode_id = f"bangumi-{mediainfo.bangumi_id}-s{season}e{episode}"
    _add_text_node(doc, root, "id", episode_id)
    _add_text_node(doc, root, "episodeid", episode_id)
    _add_text_node(doc, root, "title", title)
    _add_text_node(doc, root, "originaltitle", title)
    _add_text_node(doc, root, "showtitle", _bangumi_tv_show_title(mediainfo))
    _add_text_node(doc, root, "sorttitle", f"S{int(season):02d}E{int(episode):02d}")
    _add_text_node(doc, root, "season", season)
    _add_text_node(doc, root, "episode", episode)
    _add_text_node(doc, root, "seasonnumber", season)
    _add_text_node(doc, root, "episodenumber", episode)
    _add_text_node(doc, root, "displayseason", season)
    _add_text_node(doc, root, "displayepisode", episode)
    _add_text_node(doc, root, "aired", mediainfo.release_date or "")
    _add_text_node(doc, root, "lockdata", "true")
    return doc


def _bangumi_metadata_nfo(
        meta: Any,
        mediainfo: MediaInfo,
        season: Optional[int] = None,
        episode: Optional[int] = None) -> Optional[str]:
    if not _is_bangumi_metadata_media(mediainfo):
        return None
    if mediainfo.type == MediaType.MOVIE:
        doc = _bangumi_movie_nfo(mediainfo)
    elif season is not None and episode is not None:
        doc = _bangumi_episode_nfo(mediainfo, season, episode)
    elif season is not None:
        doc = _bangumi_season_nfo(mediainfo, season)
    else:
        doc = _bangumi_tv_nfo(mediainfo)
    logger.info(f"{mediainfo.title_year} 使用Bangumi生成NFO元数据")
    return doc.toprettyxml(indent="  ", encoding="utf-8")


def _image_ext(url: Optional[str]) -> str:
    if not url:
        return ".jpg"
    suffix = Path(str(url).split("?", 1)[0]).suffix
    return suffix or ".jpg"


def _tmdb_episode_still_url(mediainfo: MediaInfo, season: Optional[int], episode: Optional[int]) -> Optional[str]:
    if season is None or episode is None:
        return None
    tmdbid = _existing_media_server_tmdb_id(mediainfo)
    if not tmdbid:
        return None
    episode_group = getattr(mediainfo, "episode_group", None)
    cache_key = f"{tmdbid}|{season}|{episode_group or ''}"
    now = time.time()
    cached = _TMDB_EPISODE_STILL_CACHE.get(cache_key)
    if cached and now - cached[0] < 3600:
        return cached[1].get(int(episode))
    stills: dict[int, str] = {}
    try:
        episodes = TmdbChain().tmdb_episodes(
            tmdbid=int(tmdbid),
            season=int(season),
            episode_group=episode_group,
        ) or []
        for item in episodes:
            episode_number = _int_or_none(getattr(item, "episode_number", None))
            still_path = getattr(item, "still_path", None)
            if episode_number is None or not still_path:
                continue
            stills[episode_number] = settings.TMDB_IMAGE_URL(still_path)
    except Exception as err:
        logger.debug(f"Bangumi分集缩略图查询TMDB失败：{tmdbid} S{season} - {err}")
    _TMDB_EPISODE_STILL_CACHE[cache_key] = (now, stills)
    return stills.get(int(episode))


def _bangumi_metadata_img(
        mediainfo: MediaInfo,
        season: Optional[int] = None,
        episode: Optional[int] = None) -> Optional[dict]:
    if not _is_bangumi_metadata_media(mediainfo):
        return None
    if episode is not None:
        thumb = (
            _tmdb_episode_still_url(mediainfo, season, episode)
            or mediainfo.backdrop_path
            or mediainfo.poster_path
        )
        if not thumb:
            return {}
        return {f"episode-thumb{_image_ext(thumb)}": thumb}
    poster = mediainfo.poster_path
    if season is not None:
        if not poster:
            return {}
        image_name = (
            "season-specials-poster"
            if season == 0
            else f"season{str(season).rjust(2, '0')}-poster"
        )
        return {f"{image_name}{_image_ext(poster)}": poster}
    images = {}
    if poster:
        images[f"poster{_image_ext(poster)}"] = poster
    if mediainfo.backdrop_path:
        images[f"backdrop{_image_ext(mediainfo.backdrop_path)}"] = mediainfo.backdrop_path
    return images


def _transfer_result_success(result: Any) -> bool:
    if isinstance(result, tuple) and result:
        return bool(result[0])
    return bool(result)


def _history_has_bangumi_source(history: Any) -> bool:
    if not history or not getattr(history, "dest", None):
        return False
    download_history = _download_history_by_hash_or_file(getattr(history, "download_hash", None), None)
    if download_history and _bangumi_source_keyword(download_history):
        return True
    if download_history and _match_subscribe_by_download_history(download_history):
        return True
    return bool(_match_subscribe_by_transfer_history(history))


def _refresh_item_from_history(history: Any) -> Optional[schemas.RefreshMediaItem]:
    if not history or not getattr(history, "dest", None):
        return None
    return schemas.RefreshMediaItem(
        title=getattr(history, "title", None),
        year=getattr(history, "year", None),
        type=_media_type_or_none(getattr(history, "type", None)),
        category=getattr(history, "category", None),
        target_path=Path(history.dest),
    )


def _dedupe_refresh_items(items: list[schemas.RefreshMediaItem]) -> list[schemas.RefreshMediaItem]:
    deduped = []
    seen = set()
    for item in items:
        if not item or not item.target_path:
            continue
        key = item.target_path.as_posix()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _media_server_scan_running(server: Any) -> bool:
    api = getattr(server, "_api", None)
    if not api or not hasattr(api, "task_running"):
        return False
    try:
        return bool(api.task_running())
    except Exception:
        return False


def _media_server_refresh_diag(server: Any) -> str:
    try:
        authenticated = server.is_authenticated() if hasattr(server, "is_authenticated") else None
    except Exception:
        authenticated = None
    userinfo = getattr(server, "_userinfo", None)
    admin = getattr(userinfo, "is_admin", None)
    libraries = getattr(server, "_libraries", None) or {}
    library_desc = []
    for lib in list(libraries.values())[:5]:
        library_desc.append(
            f"{getattr(lib, 'name', '-')}:"
            f"{getattr(lib, 'category', '-')}:"
            f"{getattr(lib, 'dir_list', None)}"
        )
    return (
        f"authenticated={authenticated}, admin={admin}, "
        f"libraries={library_desc or '[]'}"
    )


def _call_media_server_refresh(server: Any, items: list[schemas.RefreshMediaItem]) -> Optional[bool]:
    try:
        try:
            return server.refresh_library_by_items(items, scan_mode=3)
        except TypeError:
            return server.refresh_library_by_items(items)
    except Exception:
        raise


def _refresh_media_server_for_items(
        items: list[schemas.RefreshMediaItem],
        force: bool = False) -> tuple[int, list[str]]:
    items = _dedupe_refresh_items(items)
    if not items:
        return 0, ["没有可刷新的媒体库路径"]

    cache_key = "|".join(sorted(item.target_path.as_posix() for item in items if item.target_path))
    now = time.time()
    if not force and now - _MEDIA_SERVER_REFRESH_CACHE.get(cache_key, 0) < 120:
        return 0, ["近期已触发过同一批媒体库刷新，跳过重复请求"]
    _MEDIA_SERVER_REFRESH_CACHE[cache_key] = now

    refreshed = 0
    messages = []
    media_chain = MediaServerChain()
    for module in media_chain.modulemanager.get_running_type_modules(ModuleType.MediaServer):
        if not hasattr(module, "get_instances"):
            continue
        try:
            module_name = module.get_name()
        except Exception:
            module_name = module.__class__.__name__
        for server_name, server in (module.get_instances() or {}).items():
            if not server or not hasattr(server, "refresh_library_by_items"):
                continue
            try:
                result = _call_media_server_refresh(server, items)
            except Exception as err:
                logger.warn(f"Bangumi媒体库刷新失败：{module_name} {server_name} - {err}")
                messages.append(f"{module_name} {server_name}: 失败")
                continue
            if result is None:
                logger.warn(
                    f"Bangumi媒体库刷新未触发：{module_name} {server_name} - "
                    f"{_media_server_refresh_diag(server)}"
                )
                messages.append(f"{module_name} {server_name}: 未连接")
                continue
            if result is False:
                if _media_server_scan_running(server):
                    refreshed += 1
                    messages.append(f"{module_name} {server_name}: 已有扫描任务")
                    continue
                fallback = None
                if hasattr(server, "refresh_root_library"):
                    try:
                        fallback = server.refresh_root_library()
                    except Exception as err:
                        logger.warn(f"Bangumi媒体库全库刷新失败：{module_name} {server_name} - {err}")
                if fallback is not None and fallback is not False:
                    refreshed += 1
                    messages.append(f"{module_name} {server_name}: 已触发全库")
                    continue
                logger.warn(
                    f"Bangumi媒体库刷新返回失败：{module_name} {server_name} - "
                    f"{_media_server_refresh_diag(server)}"
                )
                messages.append(f"{module_name} {server_name}: 失败")
                continue
            refreshed += 1
            messages.append(f"{module_name} {server_name}: 已触发")
    if refreshed:
        logger.info(
            "Bangumi媒体库刷新已触发："
            + "，".join(item.target_path.as_posix() for item in items if item.target_path)
        )
    return refreshed, messages or ["没有可用的媒体服务器刷新能力"]


def _refresh_bangumi_histories_media_server(
        histories: list[Any],
        force: bool = False) -> tuple[int, int, list[str]]:
    items = []
    matched = 0
    for history in histories:
        if not _history_has_bangumi_source(history):
            continue
        matched += 1
        item = _refresh_item_from_history(history)
        if item:
            items.append(item)
    refreshed, messages = _refresh_media_server_for_items(items, force=force)
    return matched, refreshed, messages


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


def _source_media_from_transfer_history(chain: Any, history: Any) -> Optional[MediaInfo]:
    download_history = _download_history_by_hash_or_file(
        getattr(history, "download_hash", None),
        None,
    )
    mediainfo = _source_media_from_download_history(chain, download_history)
    if mediainfo:
        return mediainfo

    subscribe = _match_subscribe_by_transfer_history(history)
    if not subscribe or not getattr(subscribe, "bangumiid", None):
        return None
    try:
        mediainfo = _media_from_bangumi(chain, subscribe.bangumiid)
    except Exception as err:
        logger.warn(f"整理记录 {getattr(history, 'id', '')} 获取Bangumi详情失败：{subscribe.bangumiid} - {err}")
        return None
    if not mediainfo:
        return None
    mtype = (
        _media_type_or_none(getattr(history, "type", None))
        or _media_type_or_none(getattr(subscribe, "type", None))
    )
    if mtype:
        mediainfo.type = mtype
    season = (
        _season_number(getattr(history, "seasons", None))
        or _season_number(getattr(subscribe, "season", None))
    )
    if mediainfo.type == MediaType.TV and season is not None:
        mediainfo.season = season
    if getattr(subscribe, "media_category", None):
        mediainfo.category = subscribe.media_category
    elif not mediainfo.category and getattr(history, "category", None):
        mediainfo.category = history.category
    return _mark_bangumi_media_ready(_apply_subscribe_ids(mediainfo, subscribe))


def _history_storage(history: Any) -> str:
    dest_fileitem = getattr(history, "dest_fileitem", None) or {}
    if getattr(history, "dest_storage", None):
        return history.dest_storage
    if isinstance(dest_fileitem, dict) and dest_fileitem.get("storage"):
        return dest_fileitem["storage"]
    if getattr(dest_fileitem, "storage", None):
        return dest_fileitem.storage
    return "local"


def _is_standalone_season_directory(name: str) -> bool:
    normalized = str(name or "").strip()
    if not normalized:
        return False
    if normalized in settings.RENAME_FORMAT_S0_NAMES:
        return True
    return bool(
        re.fullmatch(r"(?i)(season|s)\s*0*\d+", normalized)
        or re.fullmatch(r"第\s*[0-9一二三四五六七八九十零〇]+\s*季", normalized)
    )


def _history_media_root_path(history: Any) -> Optional[Path]:
    dest = getattr(history, "dest", None)
    if not dest:
        return None
    parent = Path(dest).parent
    if _is_standalone_season_directory(parent.name):
        return parent.parent
    return parent


def _history_media_root_item(storagechain: StorageChain, history: Any) -> Optional[schemas.FileItem]:
    root_path = _history_media_root_path(history)
    if not root_path:
        return None
    storage = _history_storage(history)
    item = storagechain.get_file_item(storage=storage, path=root_path)
    if item:
        return item
    return schemas.FileItem(
        storage=storage,
        path=root_path.as_posix(),
        name=root_path.name,
        type="dir",
    )


def _episode_thumb_path(history: Any) -> Optional[Path]:
    dest = getattr(history, "dest", None)
    if not dest:
        return None
    path = Path(dest)
    if not path.suffix:
        return None
    return path.with_suffix(".jpg")


def _image_is_landscape(path: Path) -> bool:
    try:
        from PIL import Image

        with Image.open(path) as image:
            width, height = image.size
        return width > height and width / max(height, 1) >= 1.4
    except Exception:
        return False


def _should_generate_episode_thumb(history: Any, target: Path) -> bool:
    if _history_storage(history) != "local":
        return False
    video = Path(getattr(history, "dest", "") or "")
    if not video.exists() or not video.is_file():
        return False
    if not target.exists() or target.stat().st_size == 0:
        return True
    return not _image_is_landscape(target)


def _video_seek_seconds(video: Path) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        duration = float((result.stdout or "").strip() or 0)
    except Exception:
        duration = 0
    if duration <= 0:
        return 120
    if duration < 120:
        return max(5, duration / 2)
    return min(max(duration * 0.18, 90), max(duration - 30, 5))


def _generate_episode_frame(video: Path, output: Path) -> bool:
    seek = _video_seek_seconds(video)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-ss", f"{seek:.3f}",
        "-i", str(video),
        "-frames:v", "1",
        "-vf", "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720",
        "-q:v", "3",
        str(output),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    except Exception as err:
        logger.warn(f"Bangumi分集缩略图截帧失败：{video.name} - {err}")
        return False
    if result.returncode != 0 or not output.exists() or output.stat().st_size == 0:
        logger.warn(f"Bangumi分集缩略图截帧失败：{video.name} - {(result.stderr or '').strip()}")
        return False
    return True


def _save_episode_frame_thumb(storagechain: StorageChain, history: Any, temp_path: Path, target: Path) -> bool:
    parent_item = schemas.FileItem(
        storage=_history_storage(history),
        path=target.parent.as_posix(),
        name=target.parent.name,
        type="dir",
    )
    item = storagechain.upload_file(fileitem=parent_item, path=temp_path, new_name=target.name)
    return bool(item)


def _generate_bangumi_episode_frame_thumbs(histories: list[Any]) -> tuple[int, list[str]]:
    generated = 0
    messages = []
    seen = set()
    storagechain = StorageChain()
    for history in histories:
        if not _history_has_bangumi_source(history):
            continue
        target = _episode_thumb_path(history)
        if not target:
            continue
        key = (_history_storage(history), target.as_posix())
        if key in seen:
            continue
        seen.add(key)
        if not _should_generate_episode_thumb(history, target):
            continue
        video = Path(getattr(history, "dest", "") or "")
        temp_path = None
        try:
            with NamedTemporaryFile(delete=False, suffix=".jpg") as temp_file:
                temp_path = Path(temp_file.name)
            if not _generate_episode_frame(video, temp_path):
                messages.append(f"{video.name}: 截帧失败")
                continue
            if _save_episode_frame_thumb(storagechain, history, temp_path, target):
                generated += 1
                messages.append(f"{target.name}: 已生成分集图")
            else:
                messages.append(f"{target.name}: 保存分集图失败")
        except Exception as err:
            logger.warn(f"Bangumi分集缩略图生成失败：{target} - {err}")
            messages.append(f"{target.name}: 生成分集图失败")
        finally:
            if temp_path:
                try:
                    temp_path.unlink(missing_ok=True)
                except Exception:
                    pass
    return generated, messages


def _scrape_bangumi_histories_metadata(
        histories: list[Any],
        overwrite: bool = True) -> tuple[int, list[str]]:
    scraped = 0
    messages = []
    seen = set()
    media_chain = MediaChain()
    storagechain = StorageChain()
    for history in histories:
        if not _history_has_bangumi_source(history):
            continue
        root_item = _history_media_root_item(storagechain, history)
        if not root_item:
            messages.append(f"整理记录 {getattr(history, 'id', '')}: 未找到媒体目录")
            continue
        mediainfo = _source_media_from_transfer_history(media_chain, history)
        if not mediainfo:
            messages.append(f"整理记录 {getattr(history, 'id', '')}: 未找到Bangumi详情")
            continue
        key = (
            getattr(root_item, "storage", None),
            getattr(root_item, "path", None),
            getattr(mediainfo, "bangumi_id", None),
        )
        if key in seen:
            continue
        seen.add(key)
        try:
            media_chain.scrape_metadata(
                fileitem=root_item,
                mediainfo=mediainfo,
                overwrite=overwrite,
                recursive=True,
            )
            scraped += 1
            messages.append(f"{root_item.name}: 已重刮")
        except Exception as err:
            logger.warn(f"Bangumi媒体库元数据重刮失败：{root_item.path} - {err}")
            messages.append(f"{root_item.name}: 重刮失败")
    return scraped, messages


def _refresh_recent_bangumi_media_by_hash(download_hash: Optional[str]) -> None:
    if not download_hash:
        return
    try:
        histories = TransferHistoryOper().list_by_hash(download_hash) or []
        _scrape_bangumi_histories_metadata(histories=histories, overwrite=True)
        _generate_bangumi_episode_frame_thumbs(histories=histories)
        _refresh_bangumi_histories_media_server(histories=histories, force=False)
    except Exception as err:
        logger.warn(f"Bangumi媒体库自动刷新失败：{err}")


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
        try:
            source_mediainfo = _source_media_from_download_history(self, download_history)
        except Exception as err:
            logger.warn(f"订阅外部源优先插件补齐整理识别失败：{err}")
            source_mediainfo = None
        if source_mediainfo:
            logger.info(f"{getattr(fileitem, 'name', None) or getattr(fileitem, 'path', '')} 使用订阅来源Bangumi详情补齐整理识别：{source_mediainfo.title_year}")
            if len(args_list) > 2:
                args_list[2] = source_mediainfo
            else:
                kwargs["mediainfo"] = source_mediainfo

    result = sourceprioritysubscribefix._originals["transfer_do_transfer"](self, *args_list, **kwargs)
    if _transfer_result_success(result):
        _refresh_recent_bangumi_media_by_hash(download_hash)
    return result


def _redo_transfer_history_with_source(chain: TransferChain, history_id: int) -> Optional[Tuple[bool, str]]:
    history = TransferHistoryOper().get(history_id)
    if history:
        try:
            mediainfo = _source_media_from_transfer_history(chain, history)
        except Exception as err:
            logger.warn(f"订阅外部源优先插件重新整理识别失败：{err}")
            mediainfo = None
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


def _plugin_refresh_bangumi_media(
        history_id: Optional[int] = None,
        title: Optional[str] = None,
        limit: int = 30) -> Any:
    oper = TransferHistoryOper()
    if history_id:
        histories = [oper.get(history_id)]
    elif title:
        histories = oper.get_by_title(title) or []
        histories = histories[:limit]
    else:
        histories = TransferHistory.list_by_page(oper._db, page=1, count=limit, status=True) or []
    histories = [history for history in histories if history]
    scraped, scrape_messages = _scrape_bangumi_histories_metadata(histories, overwrite=True)
    thumbs, thumb_messages = _generate_bangumi_episode_frame_thumbs(histories=histories)
    matched, refreshed, messages = _refresh_bangumi_histories_media_server(histories, force=True)
    details = scrape_messages + thumb_messages + messages
    if refreshed:
        return schemas.Response(
            success=True,
            message=(
                f"已重刮 {scraped} 个Bangumi媒体目录，"
                f"已生成 {thumbs} 张分集图，"
                f"已触发 {refreshed} 个媒体服务器刷新，匹配 Bangumi 记录 {matched} 条"
            ),
            data={"matched": matched, "scraped": scraped, "thumbs": thumbs, "refreshed": refreshed, "details": details},
        )
    if scraped:
        return schemas.Response(
            success=True,
            message=(
                f"已重刮 {scraped} 个Bangumi媒体目录，已生成 {thumbs} 张分集图，"
                f"但未触发媒体服务器刷新；"
                + "；".join(messages)
            ),
            data={"matched": matched, "scraped": scraped, "thumbs": thumbs, "refreshed": refreshed, "details": details},
        )
    return schemas.Response(
        success=False,
        message=f"未触发媒体库刷新，匹配 Bangumi 记录 {matched} 条；" + "；".join(messages),
        data={"matched": matched, "scraped": scraped, "thumbs": thumbs, "refreshed": refreshed, "details": details},
    )


def _safe_page_text(value: Any) -> str:
    if value is None or value == "":
        return "-"
    return str(value)


def _page_db_items(model: Any, status: Optional[bool] = None, limit: int = 20) -> list[Any]:
    try:
        oper = TransferHistoryOper() if model is TransferHistory else DownloadHistoryOper()
        if model is TransferHistory and status is not None:
            return TransferHistory.list_by_page(oper._db, page=1, count=limit, status=status) or []
        return oper._db.query(model).order_by(model.id.desc()).limit(limit).all()
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
        download_history = _download_history_by_hash_or_file(history.download_hash, None)
        source_keyword = _bangumi_source_keyword(download_history)
        inferred_subscribe = None if source_keyword else _match_subscribe_by_download_history(download_history)
        bangumiid = (
            source_keyword.get("bangumiid")
            if source_keyword
            else getattr(inferred_subscribe, "bangumiid", None)
        )
        source_text = (
            f"bangumi:{bangumiid}"
            if bangumiid
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
                    "content": [_redo_button(plugin_id, history.id)] if bangumiid else [],
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
            action=_redo_button(plugin_id, history.id, block=True) if bangumiid else None,
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
                        {
                            "component": "VBtn",
                            "props": {
                                "variant": "tonal",
                                "color": "primary",
                                "prepend-icon": "mdi-sync",
                            },
                            "text": "刷新最近 Bangumi 媒体库",
                            "events": {
                                "click": {
                                    "api": f"plugin/{plugin_id}/refresh",
                                    "method": "post",
                                }
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
