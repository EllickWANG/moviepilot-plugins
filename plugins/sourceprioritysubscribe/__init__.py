from __future__ import annotations

import time
import traceback
import re
from typing import Any, List, Optional, Tuple

from fastapi import Depends
from sqlalchemy import select

from app import schemas
from app.chain.media import MediaChain
from app.chain.search import SearchChain
from app.chain.subscribe import SubscribeChain
from app.chain.tmdb import TmdbChain
from app.core.config import settings
from app.core.context import MediaInfo
from app.core.event import eventmanager
from app.core.metainfo import MetaInfo
from app.core.security import verify_token
from app.db import async_db_query, db_query
from app.db.models.subscribe import Subscribe
from app.db.models.subscribehistory import SubscribeHistory
from app.db.subscribe_oper import SubscribeOper
from app.factory import app
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import MediaType, MessageChannel
from app.schemas.types import ContentType, EventType, NotificationType
from app.helper.subscribe import SubscribeHelper


class sourceprioritysubscribe(_PluginBase):
    plugin_name = "订阅外部源优先"
    plugin_desc = "订阅时有 doubanid/bangumiid 则直接使用对应来源详情，避免强制转 TMDB。"
    plugin_icon = "mdi-heart-cog"
    plugin_version = "1.0.6"
    plugin_author = "local"
    plugin_order = 1
    auth_level = 1

    _enabled = False
    _patched = False
    _originals: dict[str, Any] = {}
    _original_media_routes: list[Any] = []
    _original_media_route_index: Optional[int] = None
    _plugin_route_registered = False

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
        return None

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
        }
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
        cls._patch_media_seasons_route()
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


def _mark_bangumi_media_ready(mediainfo: Optional[MediaInfo]) -> Optional[MediaInfo]:
    if not mediainfo or not mediainfo.bangumi_id or mediainfo.tmdb_id or mediainfo.douban_id:
        return mediainfo
    if not mediainfo.names:
        mediainfo.names = list(dict.fromkeys([
            item for item in [
                mediainfo.title,
                mediainfo.original_title,
                mediainfo.en_title,
                mediainfo.hk_title,
                mediainfo.tw_title,
                mediainfo.sg_title,
            ] if item
        ]))
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
    return _mark_bangumi_media_ready(sourceprioritysubscribe._originals["subscribe_recognize_media"](
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
    return _mark_bangumi_media_ready(await sourceprioritysubscribe._originals["subscribe_async_recognize_media"](
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
    if mediainfo.names and mediainfo.seasons:
        return mediainfo
    refreshed = _media_from_bangumi(chain, mediainfo.bangumi_id)
    return _mark_bangumi_media_ready(refreshed or mediainfo)


async def _async_ensure_bangumi_search_media(chain: SearchChain, mediainfo: MediaInfo) -> MediaInfo:
    if not mediainfo or not mediainfo.bangumi_id or mediainfo.tmdb_id or mediainfo.douban_id:
        return mediainfo
    if mediainfo.names and mediainfo.seasons:
        return mediainfo
    refreshed = await _async_media_from_bangumi(chain, mediainfo.bangumi_id)
    return _mark_bangumi_media_ready(refreshed or mediainfo)


def _patched_search_process(self: SearchChain, mediainfo: MediaInfo, *args, **kwargs):
    mediainfo = _ensure_bangumi_search_media(self, mediainfo)
    return sourceprioritysubscribe._originals["search_process"](self, mediainfo, *args, **kwargs)


async def _patched_search_async_process(self: SearchChain, mediainfo: MediaInfo, *args, **kwargs):
    mediainfo = await _async_ensure_bangumi_search_media(self, mediainfo)
    return await sourceprioritysubscribe._originals["search_async_process"](self, mediainfo, *args, **kwargs)


async def _patched_search_async_process_stream(self: SearchChain, mediainfo: MediaInfo, *args, **kwargs):
    mediainfo = await _async_ensure_bangumi_search_media(self, mediainfo)
    async for event in sourceprioritysubscribe._originals["search_async_process_stream"](self, mediainfo, *args, **kwargs):
        yield event


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
            return sourceprioritysubscribe._originals["subscribe_add"](
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
            return await sourceprioritysubscribe._originals["subscribe_async_add"](
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
