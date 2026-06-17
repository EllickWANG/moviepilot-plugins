import inspect
import warnings
from datetime import datetime, timedelta
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.helper.sites import SitesHelper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import NotificationType
from app.utils.string import StringUtils

from .parser import SiteParserBase

warnings.filterwarnings("ignore", category=FutureWarning)

lock = Lock()

# 自主采集字段
DATA_FIELDS = [
    "name", "domain", "username", "userid", "user_level", "join_at",
    "upload", "download", "ratio", "bonus",
    "seeding", "leeching", "seeding_size", "err_msg", "updated_at",
]


class sitedatastat(_PluginBase):
    # 插件名称
    plugin_name = "站点数据统计"
    # 插件描述
    plugin_desc = "完全自主抓取并解析各站点用户数据（上传/下载/分享率/魔力/做种），不依赖核心解析流程。"
    # 插件图标
    plugin_icon = "statistic.png"
    # 插件版本
    plugin_version = "1.1.5"
    # 插件作者
    plugin_author = "Nyxara"
    # 作者主页
    author_url = "https://github.com/EllickWANG"
    # 插件配置项ID前缀
    plugin_config_prefix = "sitedatastat_"
    # 加载顺序
    plugin_order = 1
    # 可使用的用户级别
    auth_level = 2

    # 配置
    _enabled = False
    _onlyonce = False
    _cron = "30 8 * * *"
    _site_ids: List[int] = []
    _retry = True
    _notify_type = ""
    _scheduler = None
    # 解析器类（按 schema 索引）
    _parser_classes: Dict[str, type] = {}

    def init_plugin(self, config: dict = None):
        self.stop_service()
        if config:
            self._enabled = bool(config.get("enabled"))
            self._onlyonce = bool(config.get("onlyonce"))
            self._cron = config.get("cron") or "30 8 * * *"
            self._site_ids = config.get("site_ids") or []
            self._retry = bool(config.get("retry", True))
            self._notify_type = config.get("notify_type") or ""

        # 构建 schema -> 解析器类映射（来自插件内置 fork，独立于核心）
        self._parser_classes = self._load_parser_classes()

        if self._onlyonce:
            logger.info("站点数据统计：立即运行一次采集")
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            self._scheduler.add_job(self.collect, "date",
                                    run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                                    name="站点数据统计")
            self._onlyonce = False
            config = config or {}
            config["onlyonce"] = False
            self.update_config(config)
            if not self._scheduler.running:
                self._scheduler.start()

    # ---------------- 框架接口 ----------------

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return []

    def get_service(self) -> List[Dict[str, Any]]:
        if self._enabled and self._cron:
            return [{
                "id": "sitedatastat",
                "name": "站点数据统计采集",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.collect,
                "kwargs": {}
            }]
        return []

    @staticmethod
    def _load_parser_classes() -> Dict[str, type]:
        """
        加载插件内置 fork（单文件 parser 模块）的全部站点解析器，按 schema 值索引。
        """
        classes: Dict[str, type] = {}
        from . import parser as parser_mod
        for _, obj in inspect.getmembers(parser_mod, inspect.isclass):
            if obj is SiteParserBase:
                continue
            try:
                if issubclass(obj, SiteParserBase) and getattr(obj, "schema", None):
                    classes[obj.schema.value] = obj
            except TypeError:
                continue
        logger.info(f"站点数据统计：加载内置解析器 {len(classes)} 个 schema")
        return classes

    # ---------------- 采集 ----------------

    def collect(self):
        """
        自主采集全部（或所选）站点用户数据并落库到插件自有存储。
        """
        with lock:
            indexers = SitesHelper().get_indexers() or []
            # 站点过滤
            targets = []
            for site in indexers:
                if not site.get("is_active"):
                    continue
                if self._site_ids and site.get("id") not in self._site_ids:
                    continue
                targets.append(site)

            if not targets:
                logger.warning("站点数据统计：没有可采集的站点")
                return

            today = datetime.now().strftime("%Y-%m-%d")
            # 读取上一次快照用于抗抖动（失败/归零时沿用好值）
            prev_snapshot = self._get_latest_snapshot()
            result: Dict[str, dict] = {}

            ok, fail = 0, 0
            for site in targets:
                domain = StringUtils.get_url_domain(site.get("url") or site.get("domain") or "")
                data = self._parse_site(site)
                prev = (prev_snapshot or {}).get(domain)
                data = self._stabilize(data, prev)
                if data.get("err_msg") and prev:
                    # 本次失败但有历史：沿用历史，仅标记
                    merged = dict(prev)
                    merged["err_msg"] = data.get("err_msg")
                    merged["updated_at"] = prev.get("updated_at")
                    result[domain] = merged
                    fail += 1
                else:
                    result[domain] = data
                    if data.get("err_msg"):
                        fail += 1
                    else:
                        ok += 1

            # calculate_ratio 站点：用最终（含沿用历史）的上传/下载补算分享率，
            # 避免站点流量页格式不匹配导致分享率为 0（如天雪）
            from .parser import SITE_RULES
            calc_domains = [d for d, r in SITE_RULES.items() if r.get("calculate_ratio")]
            for dom, rec in result.items():
                if not any(dom == cd or dom.endswith("." + cd) for cd in calc_domains):
                    continue
                if not rec.get("ratio") and rec.get("download"):
                    try:
                        rec["ratio"] = round(float(rec["upload"]) / float(rec["download"]), 3)
                    except (TypeError, ValueError, ZeroDivisionError):
                        pass

            # 落库（插件自有存储，不写核心 SiteUserData）
            self.save_data(today, result)
            dates = self.get_data("_dates") or []
            if today not in dates:
                dates.append(today)
                dates = sorted(dates)[-90:]  # 最多保留 90 天
                self.save_data("_dates", dates)
            logger.info(f"站点数据统计：采集完成，成功 {ok}，失败/沿用 {fail}，共 {len(result)} 站")

            # 通知
            self._notify(today, result, prev_snapshot)

    def _parse_site(self, site: dict) -> dict:
        """
        用插件内置 fork 解析器抓取并解析单站，带一次失败重试。
        """
        schema = site.get("schema")
        parser_cls = self._parser_classes.get(schema)
        base = {f: None for f in DATA_FIELDS}
        base.update({
            "name": site.get("name"),
            "domain": StringUtils.get_url_domain(site.get("url") or site.get("domain") or ""),
            "upload": 0, "download": 0, "ratio": 0.0, "bonus": 0.0,
            "seeding": 0, "leeching": 0, "seeding_size": 0,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        if not parser_cls:
            base["err_msg"] = f"未找到解析器 schema={schema}"
            return base

        attempts = 2 if self._retry else 1
        last_err = None
        for i in range(attempts):
            try:
                parser = parser_cls(
                    site_name=site.get("name"),
                    url=site.get("url"),
                    site_cookie=site.get("cookie"),
                    apikey=site.get("apikey"),
                    token=site.get("token"),
                    ua=site.get("ua") or settings.USER_AGENT,
                    proxy=site.get("proxy"),
                )
                parser.parse()
                if parser.err_msg and i + 1 < attempts:
                    last_err = parser.err_msg
                    continue
                base.update({
                    "username": parser.username,
                    "userid": parser.userid,
                    "user_level": parser.user_level,
                    "join_at": parser.join_at,
                    "upload": parser.upload or 0,
                    "download": parser.download or 0,
                    "ratio": parser.ratio or 0.0,
                    "bonus": parser.bonus or 0.0,
                    "seeding": parser.seeding or 0,
                    "leeching": parser.leeching or 0,
                    "seeding_size": parser.seeding_size or 0,
                    "err_msg": parser.err_msg or None,
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
                return base
            except Exception as err:
                last_err = str(err)
                logger.debug(f"站点数据统计：{site.get('name')} 解析异常({i + 1}/{attempts}) - {err}")
        base["err_msg"] = last_err or "解析失败"
        return base

    @staticmethod
    def _stabilize(data: dict, prev: Optional[dict]) -> dict:
        """
        抗抖动：本次已登录但做种为 0（多为做种子页抓取失败），且历史有好值时沿用，避免归零跳变。
        """
        if not prev:
            return data
        if data.get("err_msg"):
            return data
        # 本次上传/下载全为 0 但历史有上传：多为静默抓取失败（如 m-team apikey 偶发），整条沿用历史
        if not data.get("upload") and not data.get("download") and prev.get("upload"):
            carried = dict(prev)
            carried["err_msg"] = None
            return carried
        if not data.get("upload"):
            return data
        if not data.get("seeding") and prev.get("seeding"):
            data["seeding"] = prev.get("seeding")
            if not data.get("seeding_size") and prev.get("seeding_size"):
                data["seeding_size"] = prev.get("seeding_size")
        # 等级偶发抓不到时沿用历史
        if not data.get("user_level") and prev.get("user_level"):
            data["user_level"] = prev.get("user_level")
        return data

    # ---------------- 存储读取 ----------------

    def _get_latest_snapshot(self) -> Optional[Dict[str, dict]]:
        dates = self.get_data("_dates") or []
        for d in reversed(dates):
            snap = self.get_data(d)
            if snap:
                return snap
        return None

    def _get_snapshot_by_date(self, date: str) -> Optional[Dict[str, dict]]:
        return self.get_data(date)

    def _get_recent_two_snapshots(self) -> Tuple[Optional[Dict[str, dict]], Optional[Dict[str, dict]]]:
        """
        返回最近两份非空快照 (最新, 上一份)，用于增量对比。
        不足两份时上一份为 None。
        """
        dates = self.get_data("_dates") or []
        found: List[Dict[str, dict]] = []
        for d in reversed(dates):
            snap = self.get_data(d)
            if snap:
                found.append(snap)
            if len(found) >= 2:
                break
        latest = found[0] if found else None
        prev = found[1] if len(found) >= 2 else None
        return latest, prev

    def _get_history_series(self) -> Tuple[List[str], List[float], List[float], List[float]]:
        """
        汇总历史快照，返回 (日期列表, 总上传GB, 总下载GB, 总做种体积GB)。
        仅取最近 30 个有数据的快照，避免图表过密。
        """
        dates = self.get_data("_dates") or []
        days: List[str] = []
        up_gb: List[float] = []
        dn_gb: List[float] = []
        seed_gb: List[float] = []
        gib = 1024 ** 3
        for d in dates[-30:]:
            snap = self.get_data(d)
            if not snap:
                continue
            total_up = sum(int(r.get("upload") or 0) for r in snap.values())
            total_dn = sum(int(r.get("download") or 0) for r in snap.values())
            total_seed = sum(int(r.get("seeding_size") or 0) for r in snap.values())
            days.append(d)
            up_gb.append(round(total_up / gib, 2))
            dn_gb.append(round(total_dn / gib, 2))
            seed_gb.append(round(total_seed / gib, 2))
        return days, up_gb, dn_gb, seed_gb

    # ---------------- 通知 ----------------

    def _notify(self, today: str, current: Dict[str, dict], prev: Optional[Dict[str, dict]]):
        if not self._notify_type or not prev:
            return
        inc_up = inc_dn = 0
        for dom, cur in current.items():
            p = prev.get(dom)
            if not p:
                continue
            up = int(cur.get("upload") or 0) - int(p.get("upload") or 0)
            dn = int(cur.get("download") or 0) - int(p.get("download") or 0)
            inc_up += max(0, up)
            inc_dn += max(0, dn)
        if self._notify_type == "all":
            tot_up = sum(int(c.get("upload") or 0) for c in current.values())
            tot_dn = sum(int(c.get("download") or 0) for c in current.values())
            text = (f"累计上传：{StringUtils.str_filesize(tot_up)}\n"
                    f"累计下载：{StringUtils.str_filesize(tot_dn)}")
        else:
            text = (f"今日上传：{StringUtils.str_filesize(inc_up)}\n"
                    f"今日下载：{StringUtils.str_filesize(inc_dn)}")
        self.post_message(mtype=NotificationType.SiteMessage,
                          title="站点数据统计", text=text)

    # ---------------- 配置表单 ----------------

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        site_options = [{"title": s.get("name"), "value": s.get("id")}
                        for s in (SitesHelper().get_indexers() or [])]
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {"component": "VCol", "props": {"cols": 12, "md": 3},
                             "content": [{"component": "VSwitch",
                                          "props": {"model": "enabled", "label": "启用插件"}}]},
                            {"component": "VCol", "props": {"cols": 12, "md": 3},
                             "content": [{"component": "VSwitch",
                                          "props": {"model": "onlyonce", "label": "立即运行一次"}}]},
                            {"component": "VCol", "props": {"cols": 12, "md": 3},
                             "content": [{"component": "VSwitch",
                                          "props": {"model": "retry", "label": "失败重试一次"}}]},
                            {"component": "VCol", "props": {"cols": 12, "md": 3},
                             "content": [{"component": "VTextField",
                                          "props": {"model": "cron", "label": "采集周期(cron)"}}]},
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {"component": "VCol", "props": {"cols": 12, "md": 8},
                             "content": [{"component": "VSelect",
                                          "props": {"model": "site_ids", "label": "采集站点(留空=全部)",
                                                    "multiple": True, "chips": True, "clearable": True,
                                                    "items": site_options}}]},
                            {"component": "VCol", "props": {"cols": 12, "md": 4},
                             "content": [{"component": "VSelect",
                                          "props": {"model": "notify_type", "label": "采集后通知",
                                                    "items": [{"title": "不发送", "value": ""},
                                                              {"title": "今日增量", "value": "inc"},
                                                              {"title": "累计全量", "value": "all"}]}}]},
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {"component": "VCol", "props": {"cols": 12},
                             "content": [{"component": "VAlert",
                                          "props": {"type": "info", "variant": "tonal",
                                                    "text": "本插件完全自主抓取解析，不依赖核心 SiteUserData。数据存于插件自有存储。"}}]},
                        ],
                    },
                ],
            }
        ], {
            "enabled": False,
            "onlyonce": False,
            "retry": True,
            "cron": "30 8 * * *",
            "site_ids": [],
            "notify_type": "",
        }

    # ---------------- 详情展示页 ----------------

    def get_page(self) -> List[dict]:
        snap = self._get_latest_snapshot()
        if not snap:
            return [{"component": "div", "props": {"class": "text-center"}, "text": "暂无数据，请先运行一次采集"}]

        rows = sorted(snap.values(), key=lambda x: x.get("upload") or 0, reverse=True)
        total_up = sum(int(r.get("upload") or 0) for r in rows)
        total_dn = sum(int(r.get("download") or 0) for r in rows)
        total_seed = sum(int(r.get("seeding") or 0) for r in rows)
        total_seed_size = sum(int(r.get("seeding_size") or 0) for r in rows)

        # 与上一份快照对比的增量（按 domain 匹配；无上一份则全部为 0）
        _, prev_snap = self._get_recent_two_snapshots()
        prev_snap = prev_snap or {}
        inc_up_total = inc_dn_total = 0
        for r in rows:
            p = prev_snap.get(r.get("domain"))
            if not p:
                continue
            inc_up_total += max(0, int(r.get("upload") or 0) - int(p.get("upload") or 0))
            inc_dn_total += max(0, int(r.get("download") or 0) - int(p.get("download") or 0))

        def card(title, value, icon):
            return {
                "component": "VCol", "props": {"cols": 6, "md": 3},
                "content": [{
                    "component": "VCard", "props": {"variant": "tonal"},
                    "content": [{
                        "component": "VCardText", "props": {"class": "d-flex align-center"},
                        "content": [
                            {"component": "VAvatar", "props": {"rounded": True, "variant": "text", "class": "me-3"},
                             "content": [{"component": "VImg", "props": {"src": icon}}]},
                            {"component": "div", "content": [
                                {"component": "span", "props": {"class": "text-caption"}, "text": title},
                                {"component": "div", "props": {"class": "d-flex align-center flex-wrap"},
                                 "content": [{"component": "span", "props": {"class": "text-h6"}, "text": value}]},
                            ]},
                        ],
                    }],
                }],
            }

        totals = [
            card("总上传量", StringUtils.str_filesize(total_up), "/plugin_icon/upload.png"),
            card("总下载量", StringUtils.str_filesize(total_dn), "/plugin_icon/download.png"),
            card("总做种数", f"{total_seed:,}", "/plugin_icon/seed.png"),
            card("总做种体积", StringUtils.str_filesize(total_seed_size), "/plugin_icon/database.png"),
        ]
        if prev_snap:
            totals += [
                card("较上次上传增量", f"+{StringUtils.str_filesize(inc_up_total)}", "/plugin_icon/upload.png"),
                card("较上次下载增量", f"+{StringUtils.str_filesize(inc_dn_total)}", "/plugin_icon/download.png"),
            ]

        charts = self._build_charts(rows)

        headers = ["站点", "用户名", "用户等级", "上传量", "上传增量", "下载量", "下载增量",
                   "分享率", "魔力值", "做种数", "做种体积"]
        header_row = {"component": "thead", "content": [
            {"component": "th", "props": {"class": "text-start ps-4"}, "text": h} for h in headers]}

        def fmt_bonus(b):
            try:
                return f"{float(b):,.1f}"
            except (ValueError, TypeError):
                return "0.0"

        def fmt_delta(cur, base, key):
            """相对上一份快照的增量；无上一份或未增长显示 '-'。"""
            if not prev_snap or not base:
                return "-"
            d = int(cur or 0) - int(base.get(key) or 0)
            return f"+{StringUtils.str_filesize(d)}" if d > 0 else "-"

        table_rows = []
        for r in rows:
            base = prev_snap.get(r.get("domain"))
            cells = [
                {"text": r.get("name"), "cls": "whitespace-nowrap break-keep text-high-emphasis"},
                {"text": r.get("username") or "-", "cls": ""},
                {"text": r.get("user_level") or "-", "cls": ""},
                {"text": StringUtils.str_filesize(r.get("upload") or 0), "cls": "text-success"},
                {"text": fmt_delta(r.get("upload"), base, "upload"), "cls": "text-success"},
                {"text": StringUtils.str_filesize(r.get("download") or 0), "cls": "text-error"},
                {"text": fmt_delta(r.get("download"), base, "download"), "cls": "text-error"},
                {"text": str(r.get("ratio") or 0), "cls": ""},
                {"text": fmt_bonus(r.get("bonus") or 0), "cls": ""},
                {"text": str(r.get("seeding") or 0), "cls": ""},
                {"text": StringUtils.str_filesize(r.get("seeding_size") or 0), "cls": ""},
            ]
            content = []
            for c in cells:
                cell = {"component": "td", "text": c["text"]}
                if c["cls"]:
                    cell["props"] = {"class": c["cls"]}
                content.append(cell)
            table_rows.append({"component": "tr", "props": {"class": "text-sm"}, "content": content})

        return [{
            "component": "VRow",
            "content": totals + charts + [{
                "component": "VCol", "props": {"cols": 12},
                "content": [{
                    "component": "VTable", "props": {"hover": True},
                    "content": [header_row, {"component": "tbody", "content": table_rows}],
                }],
            }],
        }]

    def _build_charts(self, rows: List[dict]) -> List[dict]:
        """
        构建插件详情页内嵌图表（VApexChart）：
        - 流量历史趋势：总上传/总下载（GB）随采集日期变化的面积图。
        - 每日增量：相邻快照之间总上传/总下载的增量柱状图。
        - 各站点上传占比：最新快照各站上传量的环形图。
        数据来自插件自有历史快照，无历史时不渲染对应图表。
        """
        charts: List[dict] = []
        bar_chart: Optional[dict] = None

        # 1) 流量历史趋势（面积图）
        days, up_gb, dn_gb, _ = self._get_history_series()
        if len(days) >= 2:
            trend = {
                "component": "VCol", "props": {"cols": 12, "md": 8},
                "content": [{
                    "component": "VCard", "props": {"variant": "tonal"},
                    "content": [
                        {"component": "VCardTitle", "props": {"class": "text-subtitle-1"}, "text": "流量历史趋势"},
                        {"component": "VCardText", "content": [{
                            "component": "VApexChart",
                            "props": {
                                "type": "area",
                                "height": 300,
                                "options": {
                                    "chart": {"toolbar": {"show": False}, "zoom": {"enabled": False}},
                                    "dataLabels": {"enabled": False},
                                    "stroke": {"width": 2, "curve": "smooth"},
                                    "colors": ["#28a745", "#dc3545"],
                                    "xaxis": {"type": "category", "categories": days},
                                    "yaxis": {"title": {"text": "GB"}},
                                    "tooltip": {"y": {"formatter": None}},
                                    "legend": {"position": "top"},
                                },
                                "series": [
                                    {"name": "总上传", "data": up_gb},
                                    {"name": "总下载", "data": dn_gb},
                                ],
                            },
                        }]},
                    ],
                }],
            }
            charts.append(trend)

            # 2) 每日增量（柱状图）：相邻快照差值，第一天无前值故从第二天起
            inc_days = days[1:]
            inc_up = [round(max(0.0, up_gb[i] - up_gb[i - 1]), 2) for i in range(1, len(up_gb))]
            inc_dn = [round(max(0.0, dn_gb[i] - dn_gb[i - 1]), 2) for i in range(1, len(dn_gb))]
            bar = {
                "component": "VCol", "props": {"cols": 12},
                "content": [{
                    "component": "VCard", "props": {"variant": "tonal"},
                    "content": [
                        {"component": "VCardTitle", "props": {"class": "text-subtitle-1"}, "text": "每日增量"},
                        {"component": "VCardText", "content": [{
                            "component": "VApexChart",
                            "props": {
                                "type": "bar",
                                "height": 280,
                                "options": {
                                    "chart": {"toolbar": {"show": False}, "stacked": False},
                                    "plotOptions": {"bar": {"columnWidth": "55%"}},
                                    "dataLabels": {"enabled": False},
                                    "colors": ["#28a745", "#dc3545"],
                                    "xaxis": {"type": "category", "categories": inc_days},
                                    "yaxis": {"title": {"text": "GB"}},
                                    "legend": {"position": "top"},
                                },
                                "series": [
                                    {"name": "上传增量", "data": inc_up},
                                    {"name": "下载增量", "data": inc_dn},
                                ],
                            },
                        }]},
                    ],
                }],
            }
            bar_chart = bar

        # 3) 各站点上传占比（环形图），取上传量前 10 站，其余归为「其它」
        up_rows = [(r.get("name") or r.get("domain") or "-", int(r.get("upload") or 0))
                   for r in rows if int(r.get("upload") or 0) > 0]
        if up_rows:
            up_rows.sort(key=lambda x: x[1], reverse=True)
            top = up_rows[:10]
            other = sum(v for _, v in up_rows[10:])
            labels = [n for n, _ in top]
            series = [round(v / (1024 ** 3), 2) for _, v in top]
            if other > 0:
                labels.append("其它")
                series.append(round(other / (1024 ** 3), 2))
            pie = {
                "component": "VCol", "props": {"cols": 12, "md": 4},
                "content": [{
                    "component": "VCard", "props": {"variant": "tonal"},
                    "content": [
                        {"component": "VCardTitle", "props": {"class": "text-subtitle-1"}, "text": "各站上传占比"},
                        {"component": "VCardText", "content": [{
                            "component": "VApexChart",
                            "props": {
                                "type": "donut",
                                "height": 300,
                                "options": {
                                    "labels": labels,
                                    "legend": {"position": "bottom"},
                                    "dataLabels": {"enabled": True},
                                    "tooltip": {"y": {}},
                                },
                                "series": series,
                            },
                        }]},
                    ],
                }],
            }
            charts.append(pie)

        # 每日增量柱状图放到趋势/占比之后，独占整行
        if bar_chart:
            charts.append(bar_chart)

        return charts

    def stop_service(self):
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
        except Exception as err:
            logger.debug(f"站点数据统计：停止服务异常 - {err}")
