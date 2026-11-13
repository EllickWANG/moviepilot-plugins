import warnings
from datetime import datetime, timedelta
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.chain.site import SiteChain
from app.core.config import settings
from app.core.event import eventmanager
from app.db.site_oper import SiteOper
from app.helper.sites import SitesHelper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import EventType, NotificationType
from app.utils.string import StringUtils

warnings.filterwarnings("ignore", category=FutureWarning)

lock = Lock()

# 兼容已有历史快照的统计字段
DATA_FIELDS = [
    "name", "domain", "username", "userid", "user_level", "join_at",
    "upload", "download", "ratio", "bonus",
    "seeding", "leeching", "seeding_size", "err_msg", "updated_at",
]


class sitedatastat(_PluginBase):
    # 插件名称
    plugin_name = "站点数据统计"
    # 插件描述
    plugin_desc = "跟随站点管理采集用户数据，提供历史趋势、上传下载增量和做种统计。"
    # 插件图标
    plugin_icon = "statistic.png"
    # 插件版本
    plugin_version = "1.2.0"
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

    def init_plugin(self, config: dict = None):
        self.stop_service()
        if config:
            self._enabled = bool(config.get("enabled"))
            self._onlyonce = bool(config.get("onlyonce"))
            self._cron = config.get("cron") or "30 8 * * *"
            self._site_ids = config.get("site_ids") or []
            self._retry = bool(config.get("retry", True))
            self._notify_type = config.get("notify_type") or ""

        # 升级后直接接入站点管理已有结果，不重抓站点、不改写历史。
        if self._enabled:
            self.sync_core_data()

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

    # ---------------- 核心数据同步与采集 ----------------

    def _targets(self) -> List[dict]:
        return [site for site in (SitesHelper().get_indexers() or [])
                if site.get("is_active")
                and (not self._site_ids or site.get("id") in self._site_ids)]

    @staticmethod
    def _domain(site: dict) -> str:
        return StringUtils.get_url_domain(site.get("domain") or site.get("url") or "")

    @classmethod
    def _empty_data(cls, site: dict) -> dict:
        data = {field: None for field in DATA_FIELDS}
        data.update({
            "name": site.get("name"), "domain": cls._domain(site),
            "upload": 0, "download": 0, "ratio": 0.0, "bonus": 0.0,
            "seeding": 0, "leeching": 0, "seeding_size": 0,
        })
        return data

    @classmethod
    def _normalize_userdata(cls, site: dict, payload: dict) -> dict:
        if payload.get("err_msg"):
            raise ValueError(payload["err_msg"])
        # 核心也可能返回未解析的默认对象，不能把它当作真实的全零账户。
        if not payload.get("userid") and not payload.get("username"):
            raise ValueError("核心未返回有效用户信息")
        data = cls._empty_data(site)
        data.update({field: payload[field] for field in DATA_FIELDS if field in payload})
        data.update(name=site.get("name"), domain=cls._domain(site), err_msg=None)
        day, time = payload.get("updated_day"), payload.get("updated_time")
        data["updated_at"] = (f"{day} {time}" if day and time
                              else datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        return data

    def _get_core_snapshot(self, targets: List[dict]) -> Dict[str, dict]:
        sites = {self._domain(site): site for site in targets}
        result = {}
        for row in SiteOper().get_userdata_latest() or []:
            payload = row.to_dict()
            domain = StringUtils.get_url_domain(payload.get("domain") or "")
            if domain not in sites:
                continue
            try:
                data = self._normalize_userdata(sites[domain], payload)
            except ValueError:
                continue
            # 兼容核心返回同日多条记录的版本，始终取最后成功采集的结果。
            previous = result.get(domain)
            if not previous or data["updated_at"] > previous["updated_at"]:
                result[domain] = data
        return result

    def _save_snapshot(self, today: str, result: Dict[str, dict]):
        self.save_data(today, result)
        dates = sorted(set((self.get_data("_dates") or []) + [today]))[-90:]
        self.save_data("_dates", dates)

    @eventmanager.register(EventType.SiteRefreshed)
    def on_site_refreshed(self, event):
        """站点管理手动或定时刷新后，同步结果，不再发起第二次抓取。"""
        if self._enabled and event:
            self.sync_core_data()

    def sync_core_data(self):
        """用核心最新成功数据更新今日快照，保留已有历史。"""
        # 插件主动采集会触发同一事件，由 collect 统一保存，避免递归或等待自己。
        if not lock.acquire(blocking=False):
            return
        try:
            targets = self._targets()
            current = self._get_core_snapshot(targets)
            if not current:
                return
            previous = self._get_latest_snapshot() or {}
            result = {}
            for site in targets:
                domain = self._domain(site)
                if domain in current:
                    data = current[domain]
                    last = previous.get(domain) or {}
                    # 失败采集也可能触发核心事件；旧缓存不能清除本次失败标记。
                    if last.get("err_msg") and data["updated_at"] <= (last.get("updated_at") or ""):
                        data = dict(data, err_msg=last["err_msg"])
                    result[domain] = data
                elif domain in previous:
                    result[domain] = previous[domain]
            self._save_snapshot(datetime.now().strftime("%Y-%m-%d"), result)
        except Exception as err:
            logger.warning(f"站点数据统计：同步核心数据失败 - {err}")
        finally:
            lock.release()

    def collect(self):
        """通过站点管理的采集流程刷新数据，并保存统计快照。"""
        with lock:
            targets = self._targets()
            if not targets:
                logger.warning("站点数据统计：没有可采集的站点")
                return

            today = datetime.now().strftime("%Y-%m-%d")
            previous = self._get_latest_snapshot() or {}
            core_snapshot = self._get_core_snapshot(targets)
            result: Dict[str, dict] = {}
            ok = fail = 0
            for site in targets:
                domain = self._domain(site)
                data = self._parse_site(site)
                if data.get("err_msg"):
                    # 优先沿用核心成功数据；升级前的插件历史仅作最后兜底。
                    fallback = core_snapshot.get(domain) or previous.get(domain)
                    if fallback:
                        data = dict(fallback, err_msg=data["err_msg"])
                    fail += 1
                else:
                    ok += 1
                result[domain] = data

            self._save_snapshot(today, result)
            logger.info(f"站点数据统计：核心采集完成，成功 {ok}，失败/沿用 {fail}，共 {len(result)} 站")
            self._notify(today, result, previous)

    def _parse_site(self, site: dict) -> dict:
        """复用站点管理的核心采集和适配规则，失败时重试一次。"""
        data = self._empty_data(site)
        attempts = 2 if self._retry else 1
        for attempt in range(attempts):
            try:
                userdata = SiteChain().refresh_userdata(site=site)
                if userdata is None:
                    raise ValueError("核心未返回站点用户数据")
                return self._normalize_userdata(site, userdata.model_dump())
            except Exception as err:
                data["err_msg"] = str(err) or "核心采集失败"
                logger.warning(f"站点数据统计：{site.get('name')} 核心采集失败"
                               f"({attempt + 1}/{attempts}) - {err}")
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

    def _get_recent_two_snapshots(self):
        """
        返回最近两份非空快照及其日期 (最新日期, 最新快照, 上一日期, 上一快照)，
        用于"当前 vs 前一天"的增量对比。不足两份时上一份为 None。
        注意：取的是上一份"可用"快照，若某天漏采则会落到再往前最近的一天。
        """
        dates = self.get_data("_dates") or []
        found: List[Tuple[str, Dict[str, dict]]] = []
        for d in reversed(dates):
            snap = self.get_data(d)
            if snap:
                found.append((d, snap))
            if len(found) >= 2:
                break
        latest_date, latest_snap = found[0] if found else (None, None)
        prev_date, prev_snap = found[1] if len(found) >= 2 else (None, None)
        return latest_date, latest_snap, prev_date, prev_snap

    @staticmethod
    def _short_date(d: Optional[str]) -> str:
        """2026-06-16 -> 06-16；空值返回 ''。"""
        return d[5:] if d and len(d) >= 10 else (d or "")

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
                                                    "text": "采集与站点管理保持一致，站点管理刷新后自动同步。历史快照和图表保存在插件中；采集失败时保留上次成功数据。"}}]},
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

        # 与上一份快照（正常即前一天）对比的增量（按 domain 匹配；无上一份则全部为 0）
        latest_date, _, prev_date, prev_snap = self._get_recent_two_snapshots()
        prev_snap = prev_snap or {}
        prev_md = self._short_date(prev_date)
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
            up_title = f"较前一天上传增量（{prev_md}→{self._short_date(latest_date)}）" if prev_md else "较前一天上传增量"
            dn_title = f"较前一天下载增量（{prev_md}→{self._short_date(latest_date)}）" if prev_md else "较前一天下载增量"
            totals += [
                card(up_title, f"+{StringUtils.str_filesize(inc_up_total)}", "/plugin_icon/upload.png"),
                card(dn_title, f"+{StringUtils.str_filesize(inc_dn_total)}", "/plugin_icon/download.png"),
            ]

        charts = self._build_charts()

        inc_suffix = f"(较{prev_md})" if prev_md else ""
        headers = ["站点", "用户名", "用户等级", "上传量", f"上传增量{inc_suffix}",
                   "下载量", f"下载增量{inc_suffix}", "分享率", "魔力值", "做种数", "做种体积"]
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
                if c is cells[0]:
                    cell.setdefault("props", {})["title"] = (
                        f"最近成功采集：{r.get('updated_at') or '暂无'}"
                        + (f"；本次失败：{r['err_msg']}" if r.get("err_msg") else "")
                    )
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

    def _build_charts(self) -> List[dict]:
        """
        构建插件详情页内嵌图表（VApexChart）：
        - 流量历史趋势：总上传/总下载（GB）随采集日期变化的面积图。
        - 每日增量：相邻快照之间总上传/总下载的增量柱状图（较前一天）。
        数据来自插件自有历史快照，无历史时不渲染对应图表。
        """
        charts: List[dict] = []
        bar_chart: Optional[dict] = None

        # 1) 流量历史趋势（面积图）
        days, up_gb, dn_gb, _ = self._get_history_series()
        if len(days) >= 2:
            trend = {
                "component": "VCol", "props": {"cols": 12},
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
                        {"component": "VCardTitle", "props": {"class": "text-subtitle-1"}, "text": "每日增量（较前一天）"},
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

        # 每日增量柱状图放到趋势图之后，独占整行
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
