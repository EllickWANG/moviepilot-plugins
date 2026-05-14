import os
import re
from datetime import datetime, timedelta
from threading import Event
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from lxml import etree
from ruamel.yaml import CommentedMap

from app.core.config import settings
from app.core.event import eventmanager
from app.db.site_oper import SiteOper
from app.helper.downloader import DownloaderHelper
from app.helper.sites import SitesHelper
from app.helper.torrent import TorrentHelper
from app.log import logger
from app.plugins import _PluginBase
from app.plugins.iyuuautoseedplus.iyuu_helper import IyuuHelper
from app.schemas import NotificationType, ServiceInfo
from app.schemas.types import EventType
from app.utils.http import RequestUtils
from app.utils.string import StringUtils


def _int_config(value: Any, default: int, min_value: int, max_value: int) -> int:
    """
    读取正整数配置，限制范围，避免错误输入导致辅种任务不可用。
    """
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        number = default
    return max(min_value, min(max_value, number))


def _float_config(value: Any, default: float = 0.0, min_value: float = 0.0) -> float:
    """
    读取浮点数配置，错误输入按默认值处理。
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(min_value, number)


def _bool_config(value: Any) -> bool:
    """
    兼容布尔值和字符串布尔值，避免表单存储差异导致开关误判。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "启用", "开启")
    return False


def _list_config(value: Any) -> list:
    """
    兼容多选配置偶发存成单值的情况。
    """
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    return [value]


def _text_config(value: Any) -> str:
    return str(value or "").strip()


def _site_id_config(value: Any) -> Any:
    """
    站点 ID 可能来自内置站点或自定义站点，数字字符串按数字匹配，其余保持原值。
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _normalize_domain(value: Any) -> str:
    """
    归一化域名，用于匹配 IYUU 站点与 MoviePilot 统一站点配置。
    """
    value = str(value or "").strip().lower()
    if not value:
        return ""
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = parsed.netloc or parsed.path.split("/", 1)[0]
    return host.split("@")[-1].split(":", 1)[0].strip()


def _without_www(domain: str) -> str:
    return domain[4:] if domain.startswith("www.") else domain


def _join_url(base_url: Any, path: Any) -> Optional[str]:
    """
    安全拼接站点 URL 和相对下载路径，避免 example.comdownload.php 这类错误。
    """
    path = str(path or "").strip()
    if not path:
        return None
    if re.match(r"(?i)^https?://", path):
        return path
    base_url = str(base_url or "").strip()
    if not base_url:
        return path
    if not base_url.endswith("/"):
        base_url = f"{base_url}/"
    return urljoin(base_url, path)


def _parse_site_aliases(value: Any) -> dict[str, str]:
    """
    解析一行一个的域名别名配置：IYUU域名=MoviePilot站点域名。
    """
    aliases: dict[str, str] = {}
    for line in str(value or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        separator = next((sep for sep in ("=>", "=", ",") if sep in line), None)
        if not separator:
            continue
        source, target = line.split(separator, 1)
        source_domain = _normalize_domain(source)
        target_domain = _normalize_domain(target)
        if source_domain and target_domain:
            aliases[source_domain] = target_domain
    return aliases


class IYUUAutoSeedPlus(_PluginBase):
    # 插件名称
    plugin_name = "IYUU自动辅种增强"
    # 插件描述
    plugin_desc = "复刻官方IYUU自动辅种，增加IYUU/下载器请求超时和每批Hash数量配置。"
    # 插件图标
    plugin_icon = "mdi-seed-plus"
    # 插件版本
    plugin_version = "2.15.11"
    # 插件作者
    plugin_author = "Ellick"
    # 作者主页
    author_url = "https://github.com/EllickWANG"
    # 插件配置项ID前缀
    plugin_config_prefix = "iyuuautoseedplus_"
    # 加载顺序
    plugin_order = 17
    # 可使用的用户级别
    auth_level = 2

    # 私有属性
    _scheduler = None
    iyuu_helper = None
    # 开关
    _enabled = False
    _cron = None
    _skipverify = False
    _onlyonce = False
    _token = None
    _downloaders = []
    # 辅种下载器
    _auto_downloader = None
    # 自动分类
    _sites = []
    _notify = False
    _nolabels = None
    _nopaths = None
    _labelsafterseed = None
    _categoryafterseed = None
    _fixed_category = False
    _addhosttotag = False
    _size = None
    _clearcache = False
    _auto_start = False
    _request_timeout = 60
    _downloader_timeout = 15
    _chunk_size = 50
    _site_aliases_text = ""
    _site_aliases = {}
    _applied_downloader_timeouts = {}
    # 退出事件
    _event = Event()
    # 种子链接xpaths
    _torrent_xpaths = [
        "//form[contains(@action, 'download.php?id=')]/@action",
        "//a[contains(@href, 'download.php?hash=')]/@href",
        "//a[contains(@href, 'download.php?id=')]/@href",
        "//a[@class='index'][contains(@href, '/dl/')]/@href",
    ]
    # 待校全种子hash清单
    _recheck_torrents = {}
    _is_recheck_running = False
    _is_auto_seed_running = False
    # 辅种缓存，出错的种子不再重复辅种，可清除
    _error_caches = []
    # 辅种缓存，辅种成功的种子，可清除
    _success_caches = []
    # 辅种缓存，出错的种子不再重复辅种，且无法清除。种子被删除404等情况
    _permanent_error_caches = []
    # 辅种计数
    total = 0
    realtotal = 0
    success = 0
    exist = 0
    fail = 0
    cached = 0

    def init_plugin(self, config: dict = None):

        # 读取配置
        if config:
            self.__load_config(config)
            self.__update_config()

        # 停止现有任务
        self.stop_service()
        self._event = Event()
        self._applied_downloader_timeouts = {}

        # 启动定时任务 & 立即运行一次
        if self.get_state() or self._onlyonce:
            self.iyuu_helper = IyuuHelper(token=self._token, timeout=self._request_timeout)
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            logger.info(
                f"IYUU自动辅种增强配置：IYUU请求超时 {self._request_timeout} 秒，"
                f"下载器请求超时 {self._downloader_timeout} 秒，每批 {self._chunk_size} 个Hash"
            )

            if self._onlyonce:
                logger.info(f"辅种服务启动，立即运行一次")
                self._scheduler.add_job(self.auto_seed, 'date',
                                        run_date=datetime.now(
                                            tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3)
                                        )
                # 关闭一次性开关
                self._onlyonce = False

            if self._clearcache:
                # 关闭清除缓存开关
                self._clearcache = False
            # 保存配置
            self.__update_config()

            # 追加种子校验服务
            self._scheduler.add_job(self.check_recheck, 'interval', minutes=3)
            # 启动服务
            self._scheduler.print_jobs()
            self._scheduler.start()

    def __load_config(self, config: dict) -> None:
        """
        读取并标准化插件配置。
        """
        self._enabled = _bool_config(config.get("enabled"))
        self._skipverify = _bool_config(config.get("skipverify"))
        self._onlyonce = _bool_config(config.get("onlyonce"))
        self._cron = _text_config(config.get("cron"))
        self._token = _text_config(config.get("token"))
        self._downloaders = _list_config(config.get("downloaders"))
        self._auto_downloader = _text_config(config.get("auto_downloader"))
        self._sites = [_site_id_config(site_id) for site_id in _list_config(config.get("sites"))]
        self._notify = _bool_config(config.get("notify"))
        self._nolabels = _text_config(config.get("nolabels"))
        self._nopaths = _text_config(config.get("nopaths"))
        self._labelsafterseed = _text_config(config.get("labelsafterseed")) or "已整理,辅种"
        self._categoryafterseed = _text_config(config.get("categoryafterseed"))
        self._fixed_category = _bool_config(config.get("fixed_category"))
        if "fixed_category" not in config and self._categoryafterseed:
            self._fixed_category = True
        self._auto_start = _bool_config(config.get("auto_start"))
        self._addhosttotag = _bool_config(config.get("addhosttotag"))
        self._size = _float_config(config.get("size"))
        self._request_timeout = _int_config(config.get("request_timeout"), 60, 5, 300)
        self._downloader_timeout = _int_config(config.get("downloader_timeout"), 15, 3, 300)
        self._chunk_size = _int_config(config.get("chunk_size"), 50, 1, 200)
        self._site_aliases_text = _text_config(config.get("site_aliases"))
        self._site_aliases = _parse_site_aliases(self._site_aliases_text)
        self._clearcache = _bool_config(config.get("clearcache"))
        self._permanent_error_caches = [] if self._clearcache else config.get("permanent_error_caches") or []
        self._error_caches = [] if self._clearcache else config.get("error_caches") or []
        self._success_caches = [] if self._clearcache else config.get("success_caches") or []

        # 过滤掉已删除的站点
        all_sites = [_site_id_config(site.id) for site in SiteOper().list_order_by_pri()] + [
            _site_id_config(site.get("id")) for site in self.__custom_sites()
        ]
        self._sites = [site_id for site_id in all_sites if site_id in self._sites]

    def __reload_runtime_config(self) -> None:
        """
        每轮任务开始前重新读取配置，让超时、批量、筛选项等配置即时生效。
        """
        config = self.get_config() or {}
        if not config:
            return
        old_token = self._token
        old_request_timeout = self._request_timeout
        old_downloader_timeout = self._downloader_timeout
        old_chunk_size = self._chunk_size
        self.__load_config(config)
        if (
                not self.iyuu_helper
                or old_token != self._token
                or old_request_timeout != self._request_timeout
        ):
            self.iyuu_helper = IyuuHelper(token=self._token, timeout=self._request_timeout)
        if (
                old_request_timeout != self._request_timeout
                or old_downloader_timeout != self._downloader_timeout
                or old_chunk_size != self._chunk_size
        ):
            logger.info(
                f"IYUU自动辅种增强运行配置已刷新：IYUU请求超时 {self._request_timeout} 秒，"
                f"下载器请求超时 {self._downloader_timeout} 秒，每批 {self._chunk_size} 个Hash"
            )

    @property
    def service_infos(self) -> Optional[Dict[str, ServiceInfo]]:
        """
        服务信息
        """
        if not self._downloaders:
            logger.warning("尚未配置下载器，请检查配置")
            return None

        services = DownloaderHelper().get_services(name_filters=self._downloaders)
        if not services:
            logger.warning("获取下载器实例失败，请检查配置")
            return None

        active_services = {}
        for service_name, service_info in services.items():
            if service_info.instance.is_inactive():
                logger.warning(f"下载器 {service_name} 未连接，请检查配置")
            else:
                self.__apply_downloader_timeout(service_info)
                active_services[service_name] = service_info

        if not active_services:
            logger.warning("没有已连接的下载器，请检查配置")
            return None

        return active_services

    @property
    def auto_service_info(self) -> ServiceInfo | None:
        """
        服务信息
        """
        if not self._auto_downloader:
            logger.debug("尚未配置主辅分离下载器，辅种不分离")
            return None

        service = DownloaderHelper().get_service(name=self._auto_downloader)
        if not service:
            logger.warning("获取主辅分离下载器实例失败，请检查配置")
            return None

        if service.instance.is_inactive():
            logger.warning(f"下载器 {service.name} 未连接，请检查配置")
            return None
        self.__apply_downloader_timeout(service)
        return service

    def get_state(self) -> bool:
        return True if self._enabled and self._cron and self._token and self._downloaders else False

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    def get_service(self) -> List[Dict[str, Any]]:
        """
        注册插件公共服务
        [{
            "id": "服务ID",
            "name": "服务名称",
            "trigger": "触发器：cron/interval/date/CronTrigger.from_crontab()",
            "func": self.xxx,
            "kwargs": {} # 定时器参数
        }]
        """
        if self.get_state():
            return [{
                "id": "IYUUAutoSeedPlus",
                "name": "IYUU自动辅种增强服务",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.auto_seed,
                "kwargs": {}
            }]
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
        # 站点的可选项（内置站点 + 自定义站点）
        customSites = self.__custom_sites()

        # 站点的可选项
        site_options = ([{"title": site.name, "value": site.id}
                         for site in SiteOper().list_order_by_pri()]
                        + [{"title": site.get("name"), "value": site.get("id")}
                           for site in customSites])
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'notify',
                                            'label': '发送通知',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'clearcache',
                                            'label': '清除缓存后运行',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'onlyonce',
                                            'label': '立即运行一次',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VTextarea',
                                        'props': {
                                            'model': 'site_aliases',
                                            'label': '站点域名别名',
                                            'rows': 3,
                                            'placeholder': '一行一个：IYUU返回域名=MoviePilot站点域名，例如 www.example.com=example.com'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'request_timeout',
                                            'label': 'IYUU请求超时(秒)',
                                            'type': 'number',
                                            'min': 5,
                                            'max': 300,
                                            'placeholder': '默认60秒'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'downloader_timeout',
                                            'label': '下载器请求超时(秒)',
                                            'type': 'number',
                                            'min': 3,
                                            'max': 300,
                                            'placeholder': '默认15秒'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'chunk_size',
                                            'label': '每批查询Hash数',
                                            'type': 'number',
                                            'min': 1,
                                            'max': 200,
                                            'placeholder': '默认50，官方为200'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'token',
                                            'label': 'IYUU Token',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VCronField',
                                        'props': {
                                            'model': 'cron',
                                            'label': '执行周期',
                                            'placeholder': '0 0 0 ? *'
                                        }
                                    }
                                ]
                            },
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'chips': True,
                                            'multiple': True,
                                            'clearable': True,
                                            'model': 'downloaders',
                                            'label': '下载器',
                                            'items': [{"title": config.name, "value": config.name}
                                                      for config in DownloaderHelper().get_configs().values()]
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'chips': True,
                                            'clearable': True,
                                            'model': 'auto_downloader',
                                            'label': '主辅分离',
                                            'items': [{"title": config.name, "value": config.name}
                                                      for config in DownloaderHelper().get_configs().values()]
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'size',
                                            'label': '辅种体积大于(GB)',
                                            'placeholder': '只有大于该值的才辅种'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'chips': True,
                                            'multiple': True,
                                            'model': 'sites',
                                            'label': '辅种站点',
                                            'items': site_options
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'nolabels',
                                            'label': '不辅种标签',
                                            'placeholder': '使用,分隔多个标签'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'labelsafterseed',
                                            'label': '辅种后增加标签',
                                            'placeholder': '使用,分隔多个标签,不填写则默认为(已整理,辅种)'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'categoryafterseed',
                                            'label': '辅种后增加分类',
                                            'placeholder': '设置辅种的种子分类'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VTextarea',
                                        'props': {
                                            'model': 'nopaths',
                                            'label': '不辅种数据文件目录',
                                            'rows': 3,
                                            'placeholder': '每一行一个目录'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'addhosttotag',
                                            'label': '将站点名添加到标签中',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'skipverify',
                                            'label': '跳过校验(仅QB有效)',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'auto_start',
                                            'label': '自动开始(跳过校验有效)',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'fixed_category',
                                            'label': '固定辅种分类',
                                        }
                                    }
                                ]
                            },
                        ]
                    },
                    {
                        'component': 'VRow',
                        'props': {
                            'style': {
                                'margin-top': '12px'
                            },
                        },
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                },
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'error',
                                            'variant': 'tonal'
                                        },
                                        'content': [
                                            {
                                                'component': 'span',
                                                'text': '注意：详细配置说明和注意事项请参考：'
                                            },
                                            {
                                                'component': 'a',
                                                'props': {
                                                    'href': 'https://github.com/EllickWANG/moviepilot-plugins/tree/main/plugins.v2/iyuuautoseedplus/README.md',
                                                    'target': '_blank'
                                                },
                                                'content': [
                                                    {
                                                        'component': 'u',
                                                        'text': 'README'
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "skipverify": False,
            "onlyonce": False,
            "notify": False,
            "clearcache": False,
            "addhosttotag": False,
            "auto_start": False,
            "fixed_category": False,
            "cron": "",
            "token": "",
            "downloaders": [],
            "auto_downloader": "",
            "sites": [],
            "nopaths": "",
            "nolabels": "",
            "labelsafterseed": "",
            "categoryafterseed": "",
            "size": "",
            "request_timeout": 60,
            "downloader_timeout": 15,
            "chunk_size": 50,
            "site_aliases": ""
        }

    def get_page(self) -> List[dict]:
        pass

    def __update_config(self):
        self.update_config({
            "enabled": self._enabled,
            "skipverify": self._skipverify,
            "onlyonce": self._onlyonce,
            "clearcache": self._clearcache,
            "cron": self._cron,
            "token": self._token,
            "downloaders": self._downloaders,
            "auto_downloader": self._auto_downloader,
            "sites": self._sites,
            "notify": self._notify,
            "nolabels": self._nolabels,
            "nopaths": self._nopaths,
            "labelsafterseed": self._labelsafterseed,
            "categoryafterseed": self._categoryafterseed,
            "fixed_category": self._fixed_category,
            "addhosttotag": self._addhosttotag,
            "auto_start": self._auto_start,
            "size": self._size,
            "request_timeout": self._request_timeout,
            "downloader_timeout": self._downloader_timeout,
            "chunk_size": self._chunk_size,
            "site_aliases": self._site_aliases_text,
            "success_caches": self._success_caches,
            "error_caches": self._error_caches,
            "permanent_error_caches": self._permanent_error_caches
        })

    def auto_seed(self):
        """
        开始辅种
        """
        stop_event = self._event
        if stop_event.is_set():
            logger.info("辅种服务已停止，跳过本次任务")
            return
        if self._is_auto_seed_running:
            logger.warn("已有辅种任务正在执行，跳过本次触发")
            return
        self._is_auto_seed_running = True
        try:
            self.__reload_runtime_config()
            if not self.get_state():
                logger.info("辅种配置不完整或插件已禁用，跳过本次任务")
                return
            services = self.service_infos
            if not self.iyuu_helper or not services:
                return
            logger.info("开始辅种任务 ...")

            # 计数器初始化
            self.total = 0
            self.realtotal = 0
            self.success = 0
            self.exist = 0
            self.fail = 0
            self.cached = 0
            # 扫描下载器辅种
            for service in services.values():
                if stop_event.is_set():
                    logger.info(f"辅种服务停止")
                    return
                downloader = service.name
                downloader_obj = service.instance
                logger.info(f"开始扫描下载器 {downloader} ...")
                # 获取下载器中已完成的种子
                torrents = downloader_obj.get_completed_torrents()
                if torrents:
                    logger.info(f"下载器 {downloader} 已完成种子数：{len(torrents)}")
                else:
                    logger.info(f"下载器 {downloader} 没有已完成种子")
                    continue
                hash_strs = []
                for torrent in torrents:
                    if stop_event.is_set():
                        logger.info(f"辅种服务停止")
                        return
                    # 获取种子hash
                    hash_str = self.__get_hash(torrent=torrent, dl_type=service.type)
                    if hash_str in self._error_caches or hash_str in self._permanent_error_caches:
                        logger.info(f"种子 {hash_str} 辅种失败且已缓存，跳过 ...")
                        continue
                    save_path = self.__get_save_path(torrent=torrent, dl_type=service.type)

                    if self._nopaths and save_path:
                        # 过滤不需要转移的路径
                        nopath_skip = False
                        for nopath in [item.strip() for item in self._nopaths.split('\n') if item.strip()]:
                            if os.path.normpath(save_path).startswith(os.path.normpath(nopath)):
                                logger.info(f"种子 {hash_str} 保存路径 {save_path} 不需要辅种，跳过 ...")
                                nopath_skip = True
                                break
                        if nopath_skip:
                            continue

                    # 获取种子标签
                    torrent_labels = self.__get_label(torrent=torrent, dl_type=service.type)
                    if torrent_labels and self._nolabels:
                        is_skip = False
                        for label in [item.strip() for item in self._nolabels.split(',') if item.strip()]:
                            if label in torrent_labels:
                                logger.info(f"种子 {hash_str} 含有不辅种标签 {label}，跳过 ...")
                                is_skip = True
                                break
                        if is_skip:
                            continue
                    # 体积排除辅种
                    torrent_size = self.__get_torrent_size(torrent=torrent, dl_type=service.type) / 1024 / 1024 / 1024
                    if self._size and torrent_size < self._size:
                        logger.info(f"种子 {hash_str} 大小:{torrent_size:.2f}GB，小于设定 {self._size}GB，跳过 ...")
                        continue
                    hash_strs.append({
                        "hash": hash_str,
                        "save_path": save_path,
                        "category": self._categoryafterseed if self._fixed_category else None
                    })
                if hash_strs:
                    chunk_size = self._chunk_size or 50
                    total_batches = (len(hash_strs) + chunk_size - 1) // chunk_size
                    logger.info(f"总共需要辅种的种子数：{len(hash_strs)}，每批查询：{chunk_size}")
                    # 分组处理，避免单次Hash过多导致IYUU接口超时。
                    for batch_index, i in enumerate(range(0, len(hash_strs), chunk_size), start=1):
                        if stop_event.is_set():
                            logger.info(f"辅种服务停止")
                            return
                        # 切片操作
                        chunk = hash_strs[i:i + chunk_size]
                        logger.info(
                            f"下载器 {downloader} 第 {batch_index}/{total_batches} 批辅种查询，"
                            f"配置每批：{chunk_size}，本批：{len(chunk)}"
                        )
                        # 处理分组
                        self.__seed_torrents(hash_strs=chunk,
                                             service=service,
                                             stop_event=stop_event)
                    # 触发校验检查
                    self.check_recheck()
                else:
                    logger.info(f"没有需要辅种的种子")

            # 保存缓存
            self.__update_config()
            # 发送消息
            if self._notify:
                if self.success or self.fail:
                    self.post_message(
                        mtype=NotificationType.SiteMessage,
                        title="【IYUU自动辅种任务完成】",
                        text=f"服务器返回可辅种总数：{self.total}\n"
                             f"实际可辅种数：{self.realtotal}\n"
                             f"已存在：{self.exist}\n"
                             f"成功：{self.success}\n"
                             f"失败：{self.fail}\n"
                             f"{self.cached} 条失败记录已加入缓存"
                    )
            logger.info("辅种任务执行完成")
        finally:
            self._is_auto_seed_running = False

    def check_recheck(self):
        """
        定时检查下载器中种子是否校验完成，校验完成且完整的自动开始辅种
        """
        if not self._recheck_torrents:
            return
        if self._is_recheck_running:
            return
        self._is_recheck_running = True
        if self.auto_service_info:
            # 检查指定下载器
            self.check_recheck_service(self.auto_service_info)
            self._is_recheck_running = False
            return
        if not self.service_infos:
            self._is_recheck_running = False
            return
        for service in self.service_infos.values():
            # 需要检查的种子
            self.check_recheck_service(service)
        self._is_recheck_running = False

    def check_recheck_service(self, service: ServiceInfo):
        """
        检查指定下载器中种子是否校验完成，校验完成且完整的自动开始辅种
        """
        # 需要检查的种子
        downloader = service.name
        downloader_obj = service.instance
        recheck_torrents = self._recheck_torrents.get(downloader) or []
        if not recheck_torrents:
            return
        logger.info(f"开始检查下载器 {downloader} 的校验任务 ...")
        # 获取下载器中的种子状态
        torrents, _ = downloader_obj.get_torrents(ids=recheck_torrents)
        if torrents:
            can_seeding_torrents = []
            for torrent in torrents:
                # 获取种子hash
                hash_str = self.__get_hash(torrent=torrent, dl_type=service.type)
                if self.__can_seeding(torrent=torrent, dl_type=service.type):
                    can_seeding_torrents.append(hash_str)
            if can_seeding_torrents:
                logger.info(f"共 {len(can_seeding_torrents)} 个任务校验完成，开始辅种 ...")
                # 开始任务
                downloader_obj.start_torrents(ids=can_seeding_torrents)
                # 去除已经处理过的种子
                self._recheck_torrents[downloader] = list(
                    set(recheck_torrents).difference(set(can_seeding_torrents)))
        elif torrents is None:
            logger.info(f"下载器 {downloader} 查询校验任务失败，将在下次继续查询 ...")
            return
        else:
            logger.info(f"下载器 {downloader} 中没有需要检查的校验任务，清空待处理列表 ...")
            self._recheck_torrents[downloader] = []

    def __seed_torrents(self, hash_strs: list, service: ServiceInfo, stop_event: Optional[Event] = None):
        """
        执行一批种子的辅种
        """
        if not hash_strs:
            return
        if stop_event and stop_event.is_set():
            logger.info(f"辅种服务停止")
            return
        logger.info(f"下载器 {service.name} 开始查询辅种，数量：{len(hash_strs)} ...")
        # 下载器中的Hashs
        hashs = [item.get("hash") for item in hash_strs]
        # 每个Hash的保存目录
        save_paths = {}
        save_category = {}
        for item in hash_strs:
            save_paths[item.get("hash")] = item.get("save_path")
            save_category[item.get("hash")] = item.get("category")
        # 查询可辅种数据
        seed_list, msg = self.iyuu_helper.get_seed_info(hashs)
        if stop_event and stop_event.is_set():
            logger.info(f"辅种服务停止")
            return
        if not isinstance(seed_list, dict):
            # 判断辅种异常是否是由于Token未认证导致的，由于没有解决接口，只能从返回值来判断
            if self._token and msg == '请求缺少token':
                logger.warn(f'IYUU辅种失败，疑似站点未绑定插件配置不完整，请先检查是否完成站点绑定！{msg}')
            elif "超时" in msg or "未获取到返回信息" in msg or "未获取到站点哈希" in msg:
                logger.warn(f"IYUU查询超时或网络异常，本批 {len(hash_strs)} 个Hash未完成：{msg}")
            else:
                logger.warn(f"当前种子列表没有可辅种的站点：{msg}")
            return
        else:
            logger.info(f"IYUU返回可辅种数：{len(seed_list)}")
        # 遍历
        for current_hash, seed_info in seed_list.items():
            if stop_event and stop_event.is_set():
                logger.info(f"辅种服务停止")
                return
            if not seed_info:
                continue
            seed_torrents = seed_info.get("torrent")
            if not isinstance(seed_torrents, list):
                seed_torrents = [seed_torrents]

            # 本次辅种成功的种子
            success_torrents = []

            for seed in seed_torrents:
                if stop_event and stop_event.is_set():
                    logger.info(f"辅种服务停止")
                    return
                if not seed:
                    continue
                if not isinstance(seed, dict):
                    continue
                if not seed.get("sid") or not seed.get("info_hash"):
                    continue
                if seed.get("info_hash") in hashs:
                    logger.info(f"{seed.get('info_hash')} 已在下载器中，跳过 ...")
                    continue
                if seed.get("info_hash") in self._success_caches:
                    logger.info(f"{seed.get('info_hash')} 已处理过辅种，跳过 ...")
                    continue
                if seed.get("info_hash") in self._error_caches or seed.get("info_hash") in self._permanent_error_caches:
                    logger.info(f"种子 {seed.get('info_hash')} 辅种失败且已缓存，跳过 ...")
                    continue
                # 添加任务 如果配置了主辅分离使用辅种下载器
                if self._auto_downloader:
                    success = self.__download_torrent(seed=seed,
                                                      service=self.auto_service_info,
                                                      save_path=save_paths.get(current_hash),
                                                      save_category=save_category.get(current_hash))
                else:
                    success = self.__download_torrent(seed=seed,
                                                      service=service,
                                                      save_path=save_paths.get(current_hash),
                                                      save_category=save_category.get(current_hash))
                if success:
                    success_torrents.append(seed.get("info_hash"))

            # 辅种成功的去重放入历史
            if len(success_torrents) > 0:
                self.__save_history(current_hash=current_hash,
                                    downloader=service.name,
                                    success_torrents=success_torrents)

        logger.info(f"下载器 {service.name} 辅种完成")

    def __save_history(self, current_hash: str, downloader: str, success_torrents: []):
        """
        [
            {
                "downloader":"2",
                "torrents":[
                    "248103a801762a66c201f39df7ea325f8eda521b",
                    "bd13835c16a5865b01490962a90b3ec48889c1f0"
                ]
            },
            {
                "downloader":"3",
                "torrents":[
                    "248103a801762a66c201f39df7ea325f8eda521b",
                    "bd13835c16a5865b01490962a90b3ec48889c1f0"
                ]
            }
        ]
        """
        try:
            # 查询当前Hash的辅种历史
            seed_history = self.get_data(key=current_hash) or []

            new_history = True
            if len(seed_history) > 0:
                for history in seed_history:
                    if not history:
                        continue
                    if not isinstance(history, dict):
                        continue
                    if not history.get("downloader"):
                        continue
                    # 如果本次辅种下载器之前有过记录则继续添加
                    if str(history.get("downloader")) == downloader:
                        history_torrents = history.get("torrents") or []
                        history["torrents"] = list(set(history_torrents + success_torrents))
                        new_history = False
                        break

            # 本次辅种下载器之前没有成功记录则新增
            if new_history:
                seed_history.append({
                    "downloader": downloader,
                    "torrents": list(set(success_torrents))
                })

            # 保存历史
            self.save_data(key=current_hash,
                           value=seed_history)
        except Exception as e:
            print(str(e))

    def __download(self, service: ServiceInfo, content: bytes,
                   save_path: str, save_category: str, site_name: str) -> Optional[str]:

        torrent_tags = [tag.strip() for tag in self._labelsafterseed.split(',') if tag.strip()]

        # 辅种 tag 叠加站点名
        if self._addhosttotag:
            torrent_tags.append(site_name)

        """
        添加下载任务
        """
        if service.type == "qbittorrent":
            # 生成随机Tag
            tag = StringUtils.generate_random_str(10)

            torrent_tags.append(tag)

            # qB 开启自动管理时，添加时传入 category 会让 qB 按分类路径重写 save_path。
            # 这里先固定真实路径添加，再单独设置分类，避免辅种路径被分类默认路径覆盖。
            state, added_torrent_ids = self.__parse_add_torrent_result(
                service.instance.add_torrent(content=content,
                                             download_dir=save_path,
                                             is_paused=True,
                                             tag=torrent_tags,
                                             category=None,
                                             ignore_category_check=False,
                                             is_skip_checking=self._skipverify)
            )
            if not state:
                return None
            else:
                # 获取种子Hash
                torrent_hash = next(iter(added_torrent_ids), None)
                if torrent_hash:
                    service.instance.delete_torrents_tag(torrent_hash, tag)
                else:
                    torrent_hash = service.instance.get_torrent_id_by_tag(tags=tag)
                if not torrent_hash:
                    logger.error(f"{service.name} 下载任务添加成功，但获取任务信息失败！")
                    return None
                if save_category:
                    self.__set_qb_category(service=service, torrent_hash=torrent_hash, category=save_category)
            return torrent_hash
        elif service.type == "transmission":
            # 添加任务
            torrent = service.instance.add_torrent(content=content,
                                                   download_dir=save_path,
                                                   is_paused=True,
                                                   labels=torrent_tags)
            if not torrent:
                return None
            else:
                return torrent.hashString

        logger.error(f"不支持的下载器：{service.type}")
        return None

    @staticmethod
    def __set_qb_category(service: ServiceInfo, torrent_hash: str, category: str) -> None:
        """
        qB 辅种需要保留真实 save_path，分类在添加完成后再设置。
        """
        try:
            qbc = getattr(service.instance, "qbc", None)
            if not qbc:
                logger.warn(f"{service.name} 未获取到 qB 客户端，无法设置分类 {category}")
                return
            qbc.torrents_set_category(category=category, torrent_hashes=torrent_hash)
        except Exception as err:
            logger.error(f"{service.name} 设置辅种分类 {category} 失败：{err}")

    @staticmethod
    def __parse_add_torrent_result(result: Any) -> tuple[bool, list[str]]:
        """
        兼容不同 MoviePilot 版本的下载器添加返回值。
        """
        if isinstance(result, tuple):
            state = bool(result[0]) if result else False
            added_ids = result[1] if len(result) > 1 else []
            if not isinstance(added_ids, list):
                added_ids = list(added_ids) if added_ids else []
            return state, [str(item) for item in added_ids if item]
        return bool(result), []

    def __apply_downloader_timeout(self, service: Optional[ServiceInfo]) -> None:
        """
        调整 qBittorrent API 请求超时，避免辅种批量任务长时间阻塞在下载器接口。
        """
        if not service or service.type != "qbittorrent" or not service.instance:
            return
        qbc = getattr(service.instance, "qbc", None)
        if not qbc:
            return
        request_args = getattr(qbc, "_REQUESTS_ARGS", None)
        if not isinstance(request_args, dict):
            try:
                setattr(qbc, "_REQUESTS_ARGS", {})
                request_args = getattr(qbc, "_REQUESTS_ARGS")
            except Exception as err:
                logger.debug(f"设置下载器请求超时失败：{service.name} - {err}")
                return
        service_key = f"{service.name}:{id(qbc)}"
        if self._applied_downloader_timeouts.get(service_key) == self._downloader_timeout:
            return
        request_args["timeout"] = self._downloader_timeout
        self._applied_downloader_timeouts[service_key] = self._downloader_timeout
        logger.info(f"下载器 {service.name} 请求超时设置为 {self._downloader_timeout} 秒")

    def __site_candidates(self, site_url: str) -> list[str]:
        """
        生成 IYUU 站点域名到 MoviePilot 站点域名的候选列表。
        """
        domain = _normalize_domain(site_url)
        candidates = []

        def add(candidate: str):
            candidate = _normalize_domain(candidate)
            if candidate and candidate not in candidates:
                candidates.append(candidate)

        add(domain)
        add(_without_www(domain))
        alias = self._site_aliases.get(domain) or self._site_aliases.get(_without_www(domain))
        if alias:
            add(alias)
            add(_without_www(alias))
        return candidates

    def __get_site_info(self, sites_helper: SitesHelper, site_url: str) -> tuple[Optional[CommentedMap], str]:
        """
        按 IYUU 域名查找 MoviePilot 统一站点配置，支持 www 归一化、别名和子域名兜底。
        """
        source_domain = _normalize_domain(site_url)
        for candidate in self.__site_candidates(site_url):
            site_info = sites_helper.get_indexer(candidate)
            if site_info and site_info.get("url"):
                if candidate != source_domain:
                    logger.info(f"IYUU站点域名映射：{source_domain} -> {candidate}")
                return site_info, candidate

        try:
            for site in SiteOper().list_order_by_pri():
                configured_domain = _normalize_domain(getattr(site, "domain", None) or getattr(site, "url", None))
                if not configured_domain:
                    continue
                if source_domain.endswith(f".{configured_domain}") or _without_www(source_domain) == configured_domain:
                    site_info = sites_helper.get_indexer(configured_domain)
                    if site_info and site_info.get("url"):
                        logger.info(f"IYUU站点域名按子域名匹配：{source_domain} -> {configured_domain}")
                        return site_info, configured_domain
        except Exception as err:
            logger.debug(f"IYUU站点域名兜底匹配失败：{source_domain} - {err}")

        return None, source_domain

    @staticmethod
    def __append_https_param(torrent_url: str) -> str:
        if not torrent_url or "https=1" in torrent_url:
            return torrent_url
        return f"{torrent_url}{'&' if '?' in torrent_url else '?'}https=1"

    def __download_torrent(self, seed: dict, service: ServiceInfo, save_path: str, save_category: str):
        """
        下载种子
        torrent: {
                    "sid": 3,
                    "torrent_id": 377467,
                    "info_hash": "a444850638e7a6f6220e2efdde94099c53358159"
                }
        """

        def __is_special_site(url):
            """
            判断是否为特殊站点（是否需要添加https）
            """
            if "hdsky.me" in url:
                return False
            return True

        self.total += 1
        # 获取种子站点及下载地址模板
        site_url, download_page = self.iyuu_helper.get_torrent_url(seed.get("sid"))
        if not site_url or not download_page:
            # 加入缓存
            self._error_caches.append(seed.get("info_hash"))
            self.fail += 1
            self.cached += 1
            return False
        # 站点信息
        sites_helper = SitesHelper()
        site_info, site_domain = self.__get_site_info(sites_helper, site_url)
        if not site_info or not site_info.get('url'):
            logger.debug(f"没有维护种子对应的站点：{site_url}")
            return False
        if self._sites and site_info.get('id') not in self._sites:
            logger.info("当前站点不在选择的辅种站点范围，跳过 ...")
            return False
        self.realtotal += 1
        # 查询hash值是否已经在下载器中
        downloader_obj = service.instance
        torrent_info, query_error = downloader_obj.get_torrents(ids=[seed.get("info_hash")])
        if query_error:
            logger.warn(f"下载器 {service.name} 查询种子失败，跳过本次辅种添加，避免重复添加：{seed.get('info_hash')}")
            self.fail += 1
            return False
        if torrent_info:
            logger.info(f"{seed.get('info_hash')} 已在下载器中，跳过 ...")
            self.exist += 1
            return False
        # 站点流控
        check, checkmsg = sites_helper.check(site_domain)
        if check:
            logger.warn(checkmsg)
            self.fail += 1
            return False
        # 下载种子
        torrent_url = self.__get_download_url(seed=seed,
                                              site=site_info,
                                              base_url=download_page)
        if not torrent_url:
            # 加入失败缓存
            self._error_caches.append(seed.get("info_hash"))
            self.fail += 1
            self.cached += 1
            return False
        # 强制使用Https
        if __is_special_site(torrent_url):
            torrent_url = self.__append_https_param(torrent_url)
        # 下载种子文件
        _, content, _, _, error_msg = TorrentHelper().download_torrent(
            url=torrent_url,
            cookie=site_info.get("cookie"),
            ua=site_info.get("ua") or settings.USER_AGENT,
            proxy=site_info.get("proxy"))
        if not content:
            # 下载失败
            self.fail += 1
            # 加入失败缓存
            if error_msg and ('无法打开链接' in error_msg or '触发站点流控' in error_msg):
                self._error_caches.append(seed.get("info_hash"))
            else:
                # 种子不存在的情况
                self._permanent_error_caches.append(seed.get("info_hash"))
            logger.error(f"下载种子文件失败：{torrent_url}")
            return False
        # 添加下载，辅种任务默认暂停
        logger.info(f"添加下载任务：{torrent_url} ...")
        download_id = self.__download(service=service,
                                      content=content,
                                      save_path=save_path,
                                      save_category=save_category,
                                      site_name=site_info.get("name"))
        if not download_id:
            # 下载失败
            self.fail += 1
            # 加入失败缓存
            self._error_caches.append(seed.get("info_hash"))
            return False
        else:
            self.success += 1
            if service.type == "qbittorrent":
                if self._skipverify:
                    if self._auto_start:
                        logger.info(f"{download_id} 跳过校验，开启自动开始，注意观察种子的完整性")
                        self.__add_recheck_torrents(service, download_id)
                    else:
                        # 跳过校验
                        logger.info(f"{download_id} 跳过校验，请自行检查手动开始任务...")
                else:
                    # 开始校验种子
                    downloader_obj.recheck_torrents(ids=[download_id])
                    self.__add_recheck_torrents(service, download_id)
            else:
                self.__add_recheck_torrents(service, download_id)
            # 下载成功
            logger.info(f"成功添加辅种下载，站点：{site_info.get('name')}，种子链接：{torrent_url}")
            # 成功也加入缓存，有一些改了路径校验不通过的，手动删除后，下一次又会辅上
            self._success_caches.append(seed.get("info_hash"))
            return True

    def __add_recheck_torrents(self, service: ServiceInfo, download_id: str):
        # 追加校验任务
        logger.info(f"添加校验检查任务：{download_id} ...")
        if not self._recheck_torrents.get(service.name):
            self._recheck_torrents[service.name] = []
        self._recheck_torrents[service.name].append(download_id)

    @staticmethod
    def __get_hash(torrent: Any, dl_type: str):
        """
        获取种子hash
        """
        try:
            return torrent.get("hash") if dl_type == "qbittorrent" else torrent.hashString
        except Exception as e:
            print(str(e))
            return ""

    @staticmethod
    def __get_label(torrent: Any, dl_type: str):
        """
        获取种子标签
        """
        try:
            return [str(tag).strip() for tag in torrent.get("tags").split(',')] \
                if dl_type == "qbittorrent" else torrent.labels or []
        except Exception as e:
            print(str(e))
            return []

    @staticmethod
    def __can_seeding(torrent: Any, dl_type: str):
        """
        判断种子是否可以做种并处于暂停状态
        """
        try:
            return torrent.get("state") in ["pausedUP", "stoppedUP"] if dl_type == "qbittorrent" \
                else (torrent.status.stopped and torrent.percent_done == 1)
        except Exception as e:
            print(str(e))
            return False

    @staticmethod
    def __get_save_path(torrent: Any, dl_type: str):
        """
        获取种子保存路径
        """
        try:
            return torrent.get("save_path") if dl_type == "qbittorrent" else torrent.download_dir
        except Exception as e:
            print(str(e))
            return ""

    @staticmethod
    def __get_torrent_size(torrent: Any, dl_type: str):
        """
        获取种子大小 int bytes
        """
        try:
            return torrent.get("total_size") if dl_type == "qbittorrent" else torrent.total_size
        except Exception as e:
            print(str(e))
            return ""

    def __get_download_url(self, seed: dict, site: CommentedMap, base_url: str):
        """
        拼装种子下载链接
        """

        def __is_mteam(url: str):
            """
            判断是否为mteam站点
            """
            return True if "m-team." in url else False

        def __is_monika(url: str):
            """
            判断是否为monika站点
            """
            return True if "monikadesign." in url else False

        def __is_gpw(url: str):
            """
            判断是否为gpw站点
            """
            return True if "greatposterwall." in url else False
        
        def __get_mteam_enclosure(tid: str, apikey: str):
            """
            获取mteam种子下载链接
            """
            if not apikey:
                logger.error("m-team站点的apikey未配置")
                return None

            """
            将mteam种子下载链接域名替换为使用API
            """
            api_url = re.sub(r'//[^/]+\.m-team', '//api.m-team', site.get('url'))
            ua = site.get("ua") or settings.USER_AGENT
            res = RequestUtils(
                headers={
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'User-Agent': f'{ua}',
                    'Accept': 'application/json, text/plain, */*',
                    'x-api-key': apikey
                },
                timeout=self._request_timeout
            ).post_res(_join_url(api_url, "api/torrent/genDlToken"), params={
                'id': tid
            })
            if not res:
                logger.warn(f"m-team 获取种子下载链接失败：{tid}")
                return None
            return res.json().get("data")

        def __get_monika_torrent(tid: str, rssurl: str):
            """
            Monika下载需要使用rsskey从站点配置中获取并拼接下载链接
            """
            if not rssurl:
                logger.error("Monika站点的rss链接未配置")
                return None

            rss_match = re.search(r'/rss/\d+\.(\w+)', rssurl)
            rsskey = rss_match.group(1)
            return _join_url(site.get('url'), f"torrents/download/{tid}.{rsskey}")

        def __get_gpw_torrent_url_from_page(seed: dict, site: dict):
            """
            从详情页面获取下载链接
            """
            if not site.get('url'):
                logger.warn(f"站点 {site.get('name')} 未获取站点地址，无法获取种子下载链接")
                return None
            
            try:
                page_url = _join_url(site.get('url'), f"torrents.php?torrentid={seed.get('torrent_id')}&hit=1")
                logger.info(f"正在获取种子下载链接：{page_url} ...")

                res = RequestUtils(
                    cookies=site.get("cookie"),
                    ua=site.get("ua") or settings.USER_AGENT,
                    proxies=settings.PROXY if site.get("proxy") else None,
                    timeout=self._request_timeout
                ).get_res(url=page_url)


                if res is None or res.status_code not in (200, 500):
                    logger.error(f"获取种子下载链接失败，请求失败：{page_url}，{res.status_code if res else ''}")
                    return None
                # Fix encoding
                if "charset=utf-8" in res.text or "charset=UTF-8" in res.text:
                    res.encoding = "UTF-8"
                else:
                    res.encoding = res.apparent_encoding

                if not res.text:
                    logger.warn(f"获取种子下载链接失败，页面内容为空：{page_url}")
                    return None
                    # 使用xpath从页面中获取下载链接
                html = etree.HTML(res.text)
                if html is None:
                    logger.warning(f"解析页面失败：{page_url}")
                    return None            
                    
                xpath = "//a[contains(@href, 'torrents.php?action=download')]/@href"
                urls = html.xpath(xpath)
            
                if not urls:
                    logger.warning(f"获取种子下载链接失败，未找到下载链接：{page_url}")
                    return None
                
                torrent_id = str(seed.get("torrent_id"))
                matched_url = None
                # Strict match using regex id=xxxx
                for u in urls:
                    if re.search(rf"id={torrent_id}(?:&|$)", u):
                        matched_url = u
                        break
                if not matched_url:
                    logger.warning(f"未找到与 torrent_id={torrent_id} 对应的下载链接")
                    return None    
                
                final_url = _join_url(site.get('url'), matched_url)

                logger.info(f"获取种子下载链接成功：{final_url}")
                return final_url
            except Exception as e:
                logger.warn(f"获取种子下载链接失败：{str(e)}")
                return None
            
        def __is_special_site(url: str):
            """
            判断是否为特殊站点
            """
            spec_params = ["hash=", "authkey="]
            if any(field in base_url for field in spec_params):
                return True
            if "hdchina.org" in url:
                return True
            if "hdsky.me" in url:
                return True
            if "hdcity.in" in url:
                return True
            if "totheglory.im" in url:
                return True
            return False

        try:
            if __is_mteam(site.get('url')):
                # 调用mteam接口获取下载链接
                return __get_mteam_enclosure(tid=seed.get("torrent_id"), apikey=site.get("apikey"))
            if __is_monika(site.get('url')):
                # 返回种子id和站点配置中所Monika的rss链接
                return __get_monika_torrent(tid=seed.get("torrent_id"), rssurl=site.get("rss"))
            if __is_gpw(site.get('url')):
                # 从详情页面获取下载链接
                return __get_gpw_torrent_url_from_page(seed=seed, site=site)
            
            elif __is_special_site(site.get('url')):
                # 从详情页面获取下载链接
                return self.__get_torrent_url_from_page(seed=seed, site=site)
            else:
                download_url = base_url.replace(
                    "id={}",
                    "id={id}"
                ).replace(
                    "/{}",
                    "/{id}"
                ).replace(
                    "/{torrent_key}",
                    ""
                ).format(
                    **{
                        "id": seed.get("torrent_id"),
                        "passkey": site.get("passkey") or '',
                        "uid": site.get("uid") or '',
                    }
                )
                if download_url.count("{"):
                    logger.warn(f"当前不支持该站点的辅助任务，Url转换失败：{seed}")
                    return None
                download_url = re.sub(r"[&?]passkey=", "",
                                      re.sub(r"[&?]uid=", "",
                                             download_url,
                                             flags=re.IGNORECASE),
                                      flags=re.IGNORECASE)
                return _join_url(site.get('url'), download_url)
        except Exception as e:
            logger.warn(
                f"{site.get('name')} Url转换失败，{str(e)}：site_url={site.get('url')}，base_url={base_url}, seed={seed}")
            return self.__get_torrent_url_from_page(seed=seed, site=site)

    def __get_torrent_url_from_page(self, seed: dict, site: dict):
        """
        从详情页面获取下载链接
        """
        if not site.get('url'):
            logger.warn(f"站点 {site.get('name')} 未获取站点地址，无法获取种子下载链接")
            return None
        try:
            page_url = _join_url(site.get('url'), f"details.php?id={seed.get('torrent_id')}&hit=1")
            logger.info(f"正在获取种子下载链接：{page_url} ...")
            res = RequestUtils(
                cookies=site.get("cookie"),
                ua=site.get("ua") or settings.USER_AGENT,
                proxies=settings.PROXY if site.get("proxy") else None,
                timeout=self._request_timeout
            ).get_res(url=page_url)
            if res is not None and res.status_code in (200, 500):
                if "charset=utf-8" in res.text or "charset=UTF-8" in res.text:
                    res.encoding = "UTF-8"
                else:
                    res.encoding = res.apparent_encoding
                if not res.text:
                    logger.warn(f"获取种子下载链接失败，页面内容为空：{page_url}")
                    return None
                # 使用xpath从页面中获取下载链接
                html = etree.HTML(res.text)
                for xpath in self._torrent_xpaths:
                    download_url = html.xpath(xpath)
                    if download_url:
                        download_url = download_url[0]
                        logger.info(f"获取种子下载链接成功：{download_url}")
                        if not download_url.startswith("http"):
                            download_url = _join_url(site.get('url'), download_url)
                        return download_url
                logger.warn(f"获取种子下载链接失败，未找到下载链接：{page_url}")
                return None
            else:
                logger.error(f"获取种子下载链接失败，请求失败：{page_url}，{res.status_code if res else ''}")
                return None
        except Exception as e:
            logger.warn(f"获取种子下载链接失败：{str(e)}")
            return None

    def stop_service(self):
        """
        退出插件
        """
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._event.set()
                    # 热更新/重载时不要等待正在执行的任务结束，避免安装接口被长任务阻塞。
                    self._scheduler.shutdown(wait=False)
                self._scheduler = None
        except Exception as e:
            print(str(e))

    def __custom_sites(self) -> List[Any]:
        custom_sites = []
        custom_sites_config = self.get_config("CustomSites")
        if custom_sites_config and custom_sites_config.get("enabled"):
            custom_sites = custom_sites_config.get("sites")
        return custom_sites

    @eventmanager.register(EventType.SiteDeleted)
    def site_deleted(self, event):
        """
        删除对应站点选中
        """
        site_id = event.event_data.get("site_id")
        config = self.get_config()
        if config:
            sites = config.get("sites")
            if sites:
                if isinstance(sites, str):
                    sites = [sites]

                # 删除对应站点
                if site_id:
                    sites = [site for site in sites if int(site) != int(site_id)]
                else:
                    # 清空
                    sites = []

                # 若无站点，则停止
                if len(sites) == 0:
                    self._enabled = False

                self._sites = sites
                # 保存配置
                self.__update_config()
