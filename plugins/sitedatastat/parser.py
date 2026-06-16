# 站点解析器 fork（合并为单文件，便于安装与独立维护）
from __future__ import annotations
from abc import ABCMeta, abstractmethod
from enum import Enum
from lxml import etree
from requests import Session
from typing import Optional
from typing import Optional, Tuple
from urllib.parse import urljoin
from urllib.parse import urljoin, urlencode
from urllib.parse import urljoin, urlsplit
import json
import re
from app.core.config import settings
from app.helper.cloudflare import under_challenge
from app.log import logger
from app.utils.http import RequestUtils
from app.utils.site import SiteUtils
from app.utils.string import StringUtils

# ===== from parser/__init__.py =====
# 站点框架
class SiteSchema(Enum):
    DiscuzX = "DiscuzX"
    Gazelle = "Gazelle"
    Ipt = "IPTorrents"
    NexusPhp = "NexusPhp"
    NexusProject = "NexusProject"
    NexusRabbit = "NexusRabbit"
    NexusHhanclub = "NexusHhanclub"
    NexusAudiences = "NexusAudiences"
    SmallHorse = "Small Horse"
    Unit3d = "Unit3d"
    TorrentLeech = "TorrentLeech"
    FileList = "FileList"
    TNode = "TNode"
    MTorrent = "MTorrent"
    Yema = "Yema"
    HDDolby = "HDDolby"
    Zhixing = "Zhixing"
    Bitpt = "Bitpt"
    RousiPro = "RousiPro"


class SiteParserBase(metaclass=ABCMeta):
    # 站点模版
    schema = None
    # 请求模式 cookie/apikey
    request_mode = "cookie"

    def __init__(self, site_name: str,
                 url: str,
                 site_cookie: str,
                 apikey: str,
                 token: str,
                 session: Session = None,
                 ua: Optional[str] = None,
                 emulate: bool = False,
                 proxy: bool = None):
        super().__init__()

        # 站点信息
        self.apikey = apikey
        self.token = token
        self._site_name = site_name
        self._site_url = url
        __split_url = urlsplit(url)
        self._site_domain = __split_url.netloc
        self._base_url = f"{__split_url.scheme}://{__split_url.netloc}"
        self._site_cookie = site_cookie
        self._session = session if session else None
        self._ua = ua
        self._emulate = emulate
        self._proxy = proxy
        self._index_html = ""
        # 用户信息
        self.username = None
        self.userid = None
        self.user_level = None
        self.join_at = None
        self.bonus = 0.0

        # 流量信息
        self.upload = 0
        self.download = 0
        self.ratio = 0

        # 做种信息
        self.seeding = 0
        self.leeching = 0
        self.seeding_size = 0
        self.leeching_size = 0
        self.uploaded = 0
        self.completed = 0
        self.incomplete = 0
        self.uploaded_size = 0
        self.completed_size = 0
        self.incomplete_size = 0
        # 做种人数, 种子大小
        self.seeding_info = []

        # 未读消息
        self.message_unread = 0
        self.message_unread_contents = []
        self.message_read_force = False

        # 全局附加请求头
        self._addition_headers = None

        # 用户基础信息页面
        self._user_basic_page = None
        # 用户基础信息参数
        self._user_basic_params = None
        # 用户基础信息请求头
        self._user_basic_headers = None

        # 用户详情信息页面
        self._user_detail_page = "userdetails.php?id="
        # 用户详情信息参数
        self._user_detail_params = None
        # 用户详情信息请求头
        self._user_detail_headers = None

        # 用户流量信息页面
        self._user_traffic_page = "index.php"
        # 用户流量信息参数
        self._user_traffic_params = None
        # 用户流量信息请求头
        self._user_traffic_headers = None

        # 用户未读消息页面
        self._user_mail_unread_page = "messages.php?action=viewmailbox&box=1&unread=yes"
        # 系统未读消息页面
        self._sys_mail_unread_page = "messages.php?action=viewmailbox&box=-2&unread=yes"
        # 未读消息数参数
        self._mail_unread_params = None
        # 未读消息数请求头
        self._mail_unread_headers = None
        # 未读消息内容参数
        self._mail_content_params = None
        # 未读消息内容请求头
        self._mail_content_headers = None

        # 用户做种信息页面
        self._torrent_seeding_page = "getusertorrentlistajax.php?userid="
        # 用户做种信息参数
        self._torrent_seeding_params = None
        # 用户做种信息请求头
        self._torrent_seeding_headers = None

        # 错误信息
        self.err_msg = None

    def site_schema(self) -> SiteSchema:
        """
        站点解析模型
        :return: 站点解析模型
        """
        return self.schema

    def parse(self):
        """
        解析站点信息
        :return:
        """
        try:
            # Cookie模式时，获取站点首页html
            if self.request_mode == "apikey":
                if not self.apikey and not self.token:
                    logger.warn(f"{self._site_name} 未设置cookie 或 apikey/token，跳过后续操作")
                    return
                self._index_html = {}
            else:
                # 检查是否已经登录
                self._index_html = self._get_page_content(url=self._site_url)
                if not self._parse_logged_in(self._index_html):
                    return
            # 解析站点页面
            self._parse_site_page(self._index_html)
            # 解析用户基础信息
            if self._user_basic_page:
                self._parse_user_base_info(
                    self._get_page_content(
                        url=urljoin(self._base_url, self._user_basic_page),
                        params=self._user_basic_params,
                        headers=self._user_basic_headers
                    )
                )
            else:
                self._parse_user_base_info(self._index_html)
            # 解析用户详细信息
            if self._user_detail_page:
                self._parse_user_detail_info(
                    self._get_page_content(
                        url=urljoin(self._base_url, self._user_detail_page),
                        params=self._user_detail_params,
                        headers=self._user_detail_headers
                    )
                )
            # 解析用户未读消息
            if settings.SITE_MESSAGE:
                self._pase_unread_msgs()
            # 解析用户上传、下载、分享率等信息
            if self._user_traffic_page:
                self._parse_user_traffic_info(
                    self._get_page_content(
                        url=urljoin(self._base_url, self._user_traffic_page),
                        params=self._user_traffic_params,
                        headers=self._user_traffic_headers
                    )
                )
            # 解析用户做种信息
            self._parse_seeding_pages()
        finally:
            # 关闭连接
            self.close()

    def _pase_unread_msgs(self):
        """
        解析所有未读消息标题和内容
        :return:
        """
        unread_msg_links = []
        if self.message_unread > 0 or self.message_read_force:
            links = {self._user_mail_unread_page, self._sys_mail_unread_page}
            for link in links:
                if not link:
                    continue
                msg_links = []
                next_page = self._parse_message_unread_links(
                    self._get_page_content(
                        url=urljoin(self._base_url, link),
                        params=self._mail_unread_params,
                        headers=self._mail_unread_headers
                    ),
                    msg_links)
                while next_page:
                    next_page = self._parse_message_unread_links(
                        self._get_page_content(
                            url=urljoin(self._base_url, next_page),
                            params=self._mail_unread_params,
                            headers=self._mail_unread_headers
                        ),
                        msg_links
                    )
                unread_msg_links.extend(msg_links)
        # 重新更新未读消息数（99999表示有消息但数量未知）
        if unread_msg_links and not self.message_unread:
            self.message_unread = len(unread_msg_links)
        # 解析未读消息内容
        for msg_link in unread_msg_links:
            logger.debug(f"{self._site_name} 信息链接 {msg_link}")
            head, date, content = self._parse_message_content(
                self._get_page_content(
                    urljoin(self._base_url, msg_link),
                    params=self._mail_content_params,
                    headers=self._mail_content_headers
                )
            )
            logger.debug(f"{self._site_name} 标题 {head} 时间 {date} 内容 {content}")
            self.message_unread_contents.append((head, date, content))

    def _parse_seeding_pages(self):
        """
        解析做种页面
        """
        if self._torrent_seeding_page:
            # 第一页
            next_page = self._parse_user_torrent_seeding_info(
                self._get_page_content(
                    url=urljoin(self._base_url, self._torrent_seeding_page),
                    params=self._torrent_seeding_params,
                    headers=self._torrent_seeding_headers
                )
            )

            # 其他页处理
            while next_page is not None and next_page is not False:
                next_page = self._parse_user_torrent_seeding_info(
                    self._get_page_content(
                        url=urljoin(urljoin(self._base_url, self._torrent_seeding_page), next_page),
                        params=self._torrent_seeding_params,
                        headers=self._torrent_seeding_headers
                    ),
                    multi_page=True)

    @staticmethod
    def _prepare_html_text(html_text):
        """
        处理掉HTML中的干扰部分
        """
        return re.sub(r"#\d+", "", re.sub(r"\d+px", "", html_text))

    @abstractmethod
    def _parse_message_unread_links(self, html_text: str, msg_links: list) -> Optional[str]:
        """
        获取未阅读消息链接
        :param html_text:
        :return:
        """
        pass

    def _get_page_content(self, url: str, params: dict = None, headers: dict = None):
        """
        获取页面内容
        :param url: 网页地址
        :param params: post参数
        :param headers: 额外的请求头
        :return:
        """
        req_headers = None
        proxies = settings.PROXY if self._proxy else None
        if self._ua or headers or self._addition_headers:

            if self.request_mode == "apikey":
                req_headers = {}
            else:
                req_headers = {
                    "User-Agent": f"{self._ua}"
                }

            if headers:
                req_headers.update(headers)
            else:
                req_headers.update({
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                })

            if self._addition_headers:
                req_headers.update(self._addition_headers)

        if self.request_mode == "apikey":
            # 使用apikey请求，通过请求头传递
            cookie = None
            session = None
        else:
            # 使用cookie请求
            cookie = self._site_cookie
            session = self._session

        if params:
            if req_headers.get("Content-Type") == "application/json":
                res = RequestUtils(cookies=cookie,
                                   session=session,
                                   timeout=60,
                                   proxies=proxies,
                                   headers=req_headers).post_res(url=url, json=params)
            else:
                res = RequestUtils(cookies=cookie,
                                   session=session,
                                   timeout=60,
                                   proxies=proxies,
                                   headers=req_headers).post_res(url=url, data=params)
        else:
            res = RequestUtils(cookies=cookie,
                               session=session,
                               timeout=60,
                               proxies=proxies,
                               headers=req_headers).get_res(url=url)
        if res is not None and res.status_code in (200, 500, 403):
            if req_headers and "application/json" in str(req_headers.get("Accept")):
                try:
                    return json.dumps(res.json())
                except (json.JSONDecodeError, ValueError) as e:
                    logger.error(f"{self._site_name} API响应JSON解析失败: {e}")
                    return ""
            else:
                # 如果cloudflare 有防护，尝试使用浏览器仿真
                if under_challenge(res.text):
                    logger.warn(
                        f"{self._site_name} 检测到Cloudflare，请更新Cookie和UA")
                    return ""
                return RequestUtils.get_decoded_html_content(res,
                                                             settings.ENCODING_DETECTION_PERFORMANCE_MODE,
                                                             settings.ENCODING_DETECTION_MIN_CONFIDENCE)

        return ""

    @abstractmethod
    def _parse_site_page(self, html_text: str):
        """
        解析站点相关信息页面
        :param html_text:
        :return:
        """
        pass

    @abstractmethod
    def _parse_user_base_info(self, html_text: str):
        """
        解析用户基础信息
        :param html_text:
        :return:
        """
        pass

    def _parse_logged_in(self, html_text):
        """
        解析用户是否已经登陆
        :param html_text:
        :return: True/False
        """
        logged_in = SiteUtils.is_logged_in(html_text)
        if not logged_in:
            self.err_msg = "未检测到已登陆，请检查cookies是否过期"
            logger.warn(f"{self._site_name} 未登录，跳过后续操作")

        return logged_in

    @abstractmethod
    def _parse_user_traffic_info(self, html_text: str):
        """
        解析用户的上传，下载，分享率等信息
        :param html_text:
        :return:
        """
        pass

    @abstractmethod
    def _parse_user_torrent_seeding_info(self, html_text: str, multi_page: bool = False) -> Optional[str]:
        """
        解析用户的做种相关信息
        :param html_text:
        :param multi_page: 是否多页数据
        :return: 下页地址
        """
        pass

    @abstractmethod
    def _parse_user_detail_info(self, html_text: str):
        """
        解析用户的详细信息
        加入时间/等级/魔力值等
        :param html_text:
        :return:
        """
        pass

    @abstractmethod
    def _parse_message_content(self, html_text):
        """
        解析短消息内容
        :param html_text:
        :return:  head: message, date: time, content: message content
        """
        pass

    def close(self):
        """
        关闭会话
        """
        if self._session:
            self._session.close()
            self._session = None

    def clear(self):
        """
        清除当前解析器的所有信息
        """
        self._index_html = ""
        self.seeding_info.clear()
        self.message_unread_contents.clear()

    def to_dict(self):
        """
        转化为字典
        """
        attributes = [
            attr for attr in dir(self)
            if not callable(getattr(self, attr)) and not attr.startswith("_")
        ]
        return {
            attr: getattr(self, attr).value
            if isinstance(getattr(self, attr), SiteSchema)
            else getattr(self, attr) for attr in attributes
        }


# ===== from parser/nexus_php.py =====
class NexusPhpSiteUserInfo(SiteParserBase):
    schema = SiteSchema.NexusPhp

    def _parse_site_page(self, html_text: str):
        html_text = self._prepare_html_text(html_text)

        user_detail = re.search(r"userdetails.php\?id=(\d+)", html_text)
        if user_detail and user_detail.group().strip():
            self._user_detail_page = user_detail.group().strip().lstrip('/')
            self.userid = user_detail.group(1)
            self._torrent_seeding_page = f"getusertorrentlistajax.php?userid={self.userid}&type=seeding"
        else:
            user_detail = re.search(r"(userdetails)", html_text)
            if user_detail and user_detail.group().strip():
                self._user_detail_page = user_detail.group().strip().lstrip('/')
                self.userid = None
                self._torrent_seeding_page = None

    def _parse_message_unread(self, html_text):
        """
        解析未读短消息数量
        :param html_text:
        :return:
        """
        html = etree.HTML(html_text)
        try:
            if not StringUtils.is_valid_html_element(html):
                return

            message_labels = html.xpath('//a[@href="messages.php"]/..')
            message_labels.extend(html.xpath('//a[contains(@href, "messages.php")]/..'))
            if message_labels:
                message_text = message_labels[0].xpath("string(.)")

                logger.debug(f"{self._site_name} 消息原始信息 {message_text}")
                message_unread_match = re.findall(r"[^Date](信息箱\s*|\((?![^)]*:)|你有\xa0)(\d+)", message_text)

                if message_unread_match and len(message_unread_match[-1]) == 2:
                    self.message_unread = StringUtils.str_int(message_unread_match[-1][1])
                elif message_text.isdigit():
                    self.message_unread = StringUtils.str_int(message_text)
        finally:
            if html is not None:
                del html

    def _parse_user_base_info(self, html_text: str):
        """
        解析用户基本信息
        """
        # 合并解析，减少额外请求调用
        self._parse_user_traffic_info(html_text)
        self._user_traffic_page = None

        self._parse_message_unread(html_text)

        html = etree.HTML(html_text)
        try:
            if not StringUtils.is_valid_html_element(html):
                return

            ret = html.xpath(f'//a[contains(@href, "userdetails") and contains(@href, "{self.userid}")]//b//text()')
            if ret:
                self.username = str(ret[0])
                return
            ret = html.xpath(f'//a[contains(@href, "userdetails") and contains(@href, "{self.userid}")]//text()')
            if ret:
                self.username = str(ret[0])

            ret = html.xpath('//a[contains(@href, "userdetails")]//strong//text()')
        finally:
            if html is not None:
                del html

        if ret:
            self.username = str(ret[0])
            return

    def _parse_user_traffic_info(self, html_text):
        """
        解析用户流量信息
        """
        html_text = self._prepare_html_text(html_text)
        upload_match = re.search(r"[^总]上[传傳]量?[:：_<>/a-zA-Z-=\"'\s#;]+([\d,.\s]+[KMGTPI]*B)", html_text,
                                 re.IGNORECASE)
        self.upload = StringUtils.num_filesize(upload_match.group(1).strip()) if upload_match else 0
        download_match = re.search(r"[^总子影力]下[载載]量?[:：_<>/a-zA-Z-=\"'\s#;]+([\d,.\s]+[KMGTPI]*B)", html_text,
                                   re.IGNORECASE)
        self.download = StringUtils.num_filesize(download_match.group(1).strip()) if download_match else 0
        ratio_match = re.search(r"分享率[:：_<>/a-zA-Z-=\"'\s#;]+([\d,.\s]+)", html_text)
        # 计算分享率
        calc_ratio = 0.0 if self.download <= 0.0 else round(self.upload / self.download, 3)
        # 优先使用页面上的分享率
        self.ratio = StringUtils.str_float(ratio_match.group(1)) if (
                ratio_match and ratio_match.group(1).strip()) else calc_ratio
        leeching_match = re.search(r"(Torrents leeching|下载中)[\u4E00-\u9FA5\D\s]+(\d+)[\s\S]+<", html_text)
        self.leeching = StringUtils.str_int(leeching_match.group(2)) if leeching_match and leeching_match.group(
            2).strip() else 0
        html = etree.HTML(html_text)
        try:
            has_ucoin, self.bonus = self._parse_ucoin(html)
            if has_ucoin:
                return
            tmps = html.xpath('//a[contains(@href,"mybonus")]/text()') if html else None
            if tmps:
                bonus_text = str(tmps[0]).strip()
                bonus_match = re.search(r"([\d,.]+)", bonus_text)
                if bonus_match and bonus_match.group(1).strip():
                    self.bonus = StringUtils.str_float(bonus_match.group(1))
                    return
            bonus_match = re.search(r"mybonus.[\[\]:：<>/a-zA-Z_\-=\"'\s#;.(使用魔力值豆]+\s*([\d,.]+)[<()&\s]", html_text)
            try:
                if bonus_match and bonus_match.group(1).strip():
                    self.bonus = StringUtils.str_float(bonus_match.group(1))
                    return
                bonus_match = re.search(r"[魔力值|\]][\[\]:：<>/a-zA-Z_\-=\"'\s#;]+\s*([\d,.]+|\"[\d,.]+\")[<>()&\s]",
                                        html_text,
                                        flags=re.S)
                if bonus_match and bonus_match.group(1).strip():
                    self.bonus = StringUtils.str_float(bonus_match.group(1).strip('"'))
            except Exception as err:
                logger.error(f"{self._site_name} 解析魔力值出错, 错误信息: {str(err)}")
        finally:
            if html is not None:
                del html

    @staticmethod
    def _parse_ucoin(html):
        """
        解析ucoin, 统一转换为铜币
        :param html:
        :return:
        """
        if StringUtils.is_valid_html_element(html):
            gold, silver, copper = None, None, None

            golds = html.xpath('//span[@class = "ucoin-symbol ucoin-gold"]//text()')
            if golds:
                gold = StringUtils.str_float(str(golds[-1]))
            silvers = html.xpath('//span[@class = "ucoin-symbol ucoin-silver"]//text()')
            if silvers:
                silver = StringUtils.str_float(str(silvers[-1]))
            coppers = html.xpath('//span[@class = "ucoin-symbol ucoin-copper"]//text()')
            if coppers:
                copper = StringUtils.str_float(str(coppers[-1]))
            if gold or silver or copper:
                gold = gold if gold else 0
                silver = silver if silver else 0
                copper = copper if copper else 0
                return True, gold * 100 * 100 + silver * 100 + copper
        return False, 0.0

    def _parse_user_torrent_seeding_info(self, html_text: str, multi_page: Optional[bool] = False) -> Optional[str]:
        """
        做种相关信息
        :param html_text:
        :param multi_page: 是否多页数据
        :return: 下页地址
        """
        html = etree.HTML(str(html_text).replace(r'\/', '/'))
        try:
            if not StringUtils.is_valid_html_element(html):
                return None

            # 首页存在扩展链接，使用扩展链接
            seeding_url_text = html.xpath('//a[contains(@href,"torrents.php") '
                                          'and contains(@href,"seeding")]/@href')
            if multi_page is False and seeding_url_text and seeding_url_text[0].strip():
                self._torrent_seeding_page = seeding_url_text[0].strip()
                return self._torrent_seeding_page

            size_col = 3
            seeders_col = 4
            # 搜索size列
            size_col_xpath = '//tr[position()=1]/' \
                             'td[(img[@class="size"] and img[@alt="size"])' \
                             ' or (text() = "大小")' \
                             ' or (a/img[@class="size" and @alt="size"])]'
            if html.xpath(size_col_xpath):
                size_col = len(html.xpath(f'{size_col_xpath}/preceding-sibling::td')) + 1
            # 搜索seeders列
            seeders_col_xpath = '//tr[position()=1]/' \
                                'td[(img[@class="seeders"] and img[@alt="seeders"])' \
                                ' or (text() = "在做种")' \
                                ' or (a/img[@class="seeders" and @alt="seeders"])]'
            if html.xpath(seeders_col_xpath):
                seeders_col = len(html.xpath(f'{seeders_col_xpath}/preceding-sibling::td')) + 1

            page_seeding = 0
            page_seeding_size = 0
            page_seeding_info = []
            # 如果 table class="torrents"，则增加table[@class="torrents"]
            table_class = '//table[@class="torrents"]' if html.xpath('//table[@class="torrents"]') else ''
            seeding_sizes = html.xpath(f'{table_class}//tr[position()>1]/td[{size_col}]')
            seeding_seeders = html.xpath(f'{table_class}//tr[position()>1]/td[{seeders_col}]/b/a/text()')
            if not seeding_seeders:
                seeding_seeders = html.xpath(f'{table_class}//tr[position()>1]/td[{seeders_col}]//text()')
            if seeding_sizes and seeding_seeders:
                page_seeding = len(seeding_sizes)

                for i in range(0, len(seeding_sizes)):
                    size = StringUtils.num_filesize(seeding_sizes[i].xpath("string(.)").strip())
                    seeders = StringUtils.str_int(seeding_seeders[i])

                    page_seeding_size += size
                    page_seeding_info.append([seeders, size])

            self.seeding += page_seeding
            self.seeding_size += page_seeding_size
            self.seeding_info.extend(page_seeding_info)

            # 通用做种兜底：部分站点做种列表表格结构与列检测失配，核心数到 0；
            # 这些页面顶部均有规整的 "N条记录 … X TB" 汇总，据此补全做种数/体积。
            if not self.seeding:
                self._apply_seeding_summary(str(html_text))

            # 是否存在下页数据
            next_page = None
            next_page_text = html.xpath(
                '//a[contains(.//text(), "下一页") or contains(.//text(), "下一頁") or contains(.//text(), ">")]/@href')

            # 防止识别到详情页
            while next_page_text:
                next_page = next_page_text.pop().strip()
                if not next_page.startswith('details.php'):
                    break
                next_page = None

            # fix up page url
            if next_page:
                if self.userid not in next_page:
                    next_page = f'{next_page}&userid={self.userid}&type=seeding'
        finally:
            if html is not None:
                del html

        return next_page

    def _apply_seeding_summary(self, html_text: str):
        """
        从做种列表页顶部的 "N条记录 … X TB" 汇总行补全做种数与做种体积。
        仅在常规表格解析得到 0 时调用。真实无做种（“没有记录”）保持 0。
        """
        if not html_text or "条记录" not in html_text:
            return
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html_text))
        count_match = re.search(r"(\d[\d,]*)\s*条\s*记录", text)
        if not count_match:
            return
        count = StringUtils.str_int(count_match.group(1).replace(",", ""))
        if count <= 0:
            return
        self.seeding = count
        if not self.seeding_size:
            for size_re in (r"总大小[：:\s]*([\d.]+\s*[KMGTP]i?B)",
                            r"Total[:：]?\s*([\d.]+\s*[KMGTP]i?B)",
                            r"共计\s*([\d.]+\s*[KMGTP]i?B)",
                            r"资源总量[：:\s]*([\d.]+\s*[KMGTP]i?B)"):
                size_match = re.search(size_re, text, re.IGNORECASE)
                if size_match:
                    self.seeding_size = StringUtils.num_filesize(size_match.group(1).strip())
                    break

    def _parse_user_detail_info(self, html_text: str):
        """
        解析用户额外信息，加入时间，等级
        :param html_text:
        :return:
        """
        html = etree.HTML(html_text)
        try:
            if not StringUtils.is_valid_html_element(html):
                return

            self._get_user_level(html)

            self._fixup_traffic_info(html)

            # 加入日期
            join_at_text = html.xpath(
                '//tr/td[text()="加入日期" or text()="注册日期" or *[text()="加入日期"]]/following-sibling::td[1]//text()'
                '|//div/b[text()="加入日期"]/../text()')
            if join_at_text:
                self.join_at = StringUtils.unify_datetime_str(join_at_text[0].split(' (')[0].strip())

            # 做种体积 & 做种数
            # seeding 页面获取不到的话，此处再获取一次
            seeding_sizes = html.xpath('//tr/td[text()="当前上传"]/following-sibling::td[1]//'
                                       'table[tr[1][td[4 and text()="尺寸"]]]//tr[position()>1]/td[4]')
            seeding_seeders = html.xpath('//tr/td[text()="当前上传"]/following-sibling::td[1]//'
                                         'table[tr[1][td[5 and text()="做种者"]]]//tr[position()>1]/td[5]//text()')
            tmp_seeding = len(seeding_sizes)
            tmp_seeding_size = 0
            tmp_seeding_info = []
            for i in range(0, len(seeding_sizes)):
                size = StringUtils.num_filesize(seeding_sizes[i].xpath("string(.)").strip())
                seeders = StringUtils.str_int(seeding_seeders[i])

                tmp_seeding_size += size
                tmp_seeding_info.append([seeders, size])

            if not self.seeding_size:
                self.seeding_size = tmp_seeding_size
            if not self.seeding:
                self.seeding = tmp_seeding
            if not self.seeding_info:
                self.seeding_info = tmp_seeding_info

            seeding_sizes = html.xpath('//tr/td[text()="做种统计"]/following-sibling::td[1]//text()')
            if seeding_sizes:
                seeding_match = re.search(r"总做种数:\s+(\d+)", seeding_sizes[0], re.IGNORECASE)
                seeding_size_match = re.search(r"总做种体积:\s+([\d,.\s]+[KMGTPI]*B)", seeding_sizes[0], re.IGNORECASE)
                tmp_seeding = StringUtils.str_int(seeding_match.group(1)) if (
                        seeding_match and seeding_match.group(1)) else 0
                tmp_seeding_size = StringUtils.num_filesize(
                    seeding_size_match.group(1).strip()) if seeding_size_match else 0
            if not self.seeding_size:
                self.seeding_size = tmp_seeding_size
            if not self.seeding:
                self.seeding = tmp_seeding

            self._fixup_torrent_seeding_page(html)
        finally:
            if html is not None:
                del html

    def _fixup_torrent_seeding_page(self, html):
        """
        修正种子页面链接
        :param html:
        :return:
        """
        # 单独的种子页面
        seeding_url_text = html.xpath('//a[contains(@href,"getusertorrentlist.php") '
                                      'and contains(@href,"seeding")]/@href')
        if seeding_url_text:
            self._torrent_seeding_page = seeding_url_text[0].strip()
        # 从JS调用种获取用户ID
        seeding_url_text = html.xpath('//a[contains(@href, "javascript: getusertorrentlistajax") '
                                      'and contains(@href,"seeding")]/@href')
        csrf_text = html.xpath('//meta[@name="x-csrf"]/@content')
        if not self._torrent_seeding_page and seeding_url_text:
            user_js = re.search(r"javascript: getusertorrentlistajax\(\s*'(\d+)", seeding_url_text[0])
            if user_js and user_js.group(1).strip():
                self.userid = user_js.group(1).strip()
                self._torrent_seeding_page = f"getusertorrentlistajax.php?userid={self.userid}&type=seeding"
        elif seeding_url_text and csrf_text:
            if csrf_text[0].strip():
                self._torrent_seeding_page \
                    = f"ajax_getusertorrentlist.php"
                self._torrent_seeding_params = {'userid': self.userid, 'type': 'seeding', 'csrf': csrf_text[0].strip()}

        # 分类做种模式
        # 临时屏蔽
        # seeding_url_text = html.xpath('//tr/td[text()="当前做种"]/following-sibling::td[1]'
        #                              '/table//td/a[contains(@href,"seeding")]/@href')
        # if seeding_url_text:
        #    self._torrent_seeding_page = seeding_url_text

    def _get_user_level(self, html):
        # 等级 获取同一行等级数据，图片格式等级，取title信息，否则取文本信息
        user_levels_text = html.xpath('//tr/td[text()="等級" or text()="等级" or *[text()="等级"]]/'
                                      'following-sibling::td[1]/img[1]/@title')
        if user_levels_text:
            self.user_level = user_levels_text[0].strip()
            return

        user_levels_text = html.xpath('//tr/td[text()="等級" or text()="等级"]/'
                                      'following-sibling::td[1 and not(img)]'
                                      '|//tr/td[text()="等級" or text()="等级"]/'
                                      'following-sibling::td[1 and img[not(@title)]]')
        if user_levels_text:
            self.user_level = user_levels_text[0].xpath("string(.)").strip()
            return

        user_levels_text = html.xpath('//tr/td[text()="等級" or text()="等级"]/'
                                      'following-sibling::td[1]')
        if user_levels_text:
            self.user_level = user_levels_text[0].xpath("string(.)").strip()
            return

        user_levels_text = html.xpath('//a[contains(@href, "userdetails")]/text()')
        if not self.user_level and user_levels_text:
            for user_level_text in user_levels_text:
                user_level_match = re.search(r"\[(.*)]", user_level_text)
                if user_level_match and user_level_match.group(1).strip():
                    self.user_level = user_level_match.group(1).strip()
                    break

        # 兜底：部分站点（如 hhanclub）等级仅以等级图标的 title 呈现，没有“等级”行
        if not self.user_level:
            level_img_title = html.xpath(
                '//img[contains(@src, "pic/") and (contains(@title, "User") '
                'or contains(@title, "user"))]/@title')
            if level_img_title and level_img_title[0].strip():
                self.user_level = level_img_title[0].strip()

    def _parse_message_unread_links(self, html_text: str, msg_links: list) -> Optional[str]:
        html = etree.HTML(html_text)
        try:
            if not StringUtils.is_valid_html_element(html):
                return None

            message_links = html.xpath('//tr[not(./td/img[@alt="Read"])]/td/a[contains(@href, "viewmessage")]/@href')
            msg_links.extend(message_links)
            # 是否存在下页数据
            next_page = None
            next_page_text = html.xpath('//a[contains(.//text(), "下一页") or contains(.//text(), "下一頁")]/@href')
            if next_page_text:
                next_page = next_page_text[-1].strip()
        finally:
            if html is not None:
                del html

        return next_page

    def _parse_message_content(self, html_text):
        html = etree.HTML(html_text)
        try:
            if not StringUtils.is_valid_html_element(html):
                return None, None, None
            # 标题
            message_head_text = None
            message_head = html.xpath('//h1/text()'
                                      '|//div[@class="layui-card-header"]/span[1]/text()')
            if message_head:
                message_head_text = message_head[-1].strip()

            # 消息时间
            message_date_text = None
            message_date = html.xpath('//h1/following-sibling::table[.//tr/td[@class="colhead"]]//tr[2]/td[2]'
                                      '|//div[@class="layui-card-header"]/span[2]/span[2]')
            if message_date:
                message_date_text = message_date[0].xpath("string(.)").strip()

            # 消息内容
            message_content_text = None
            message_content = html.xpath('//h1/following-sibling::table[.//tr/td[@class="colhead"]]//tr[3]/td'
                                         '|//div[contains(@class,"layui-card-body")]')
            if message_content:
                message_content_text = message_content[0].xpath("string(.)").strip()
        finally:
            if html is not None:
                del html

        return message_head_text, message_date_text, message_content_text

    def _fixup_traffic_info(self, html):
        # fixup bonus
        if not self.bonus:
            bonus_text = html.xpath('//tr/td[text()="魔力值" or text()="猫粮"]/following-sibling::td[1]/text()')
            if bonus_text:
                self.bonus = StringUtils.str_float(bonus_text[0].strip())


# ===== from parser/bitpt.py =====
#
# 极速之星 https://bitpt.cn/
# author: ThedoRap
# time: 2025-10-02
#

from bs4 import BeautifulSoup

class BitptSiteUserInfo(SiteParserBase):
    schema = SiteSchema.Bitpt

    def _parse_site_page(self, html_text: str):
        self._user_basic_page = "userdetails.php?uid={uid}"
        self._user_detail_page = None
        self._user_basic_params = {}
        self._user_traffic_page = None
        self._sys_mail_unread_page = None
        self._user_mail_unread_page = None
        self._mail_unread_params = {}
        self._torrent_seeding_base = "browse.php"
        self._torrent_seeding_params = {"t": "myseed", "st": "2", "d": "desc"}
        self._torrent_seeding_headers = {}
        self._addition_headers = {}

    def _parse_logged_in(self, html_text):
        soup = BeautifulSoup(html_text, 'html.parser')
        return bool(soup.find(id='userinfotop'))

    def _parse_user_base_info(self, html_text: str):
        if not html_text:
            return None
        soup = BeautifulSoup(html_text, 'html.parser')
        table = soup.find('table', class_='frmtable')
        if not table:
            return

        rows = table.find_all('tr')
        info_dict = {}
        for row in rows:
            cells = row.find_all('td')
            if len(cells) == 2:
                key = cells[0].text.strip()
                value = cells[1].text.strip()
                info_dict[key] = value

        self.userid = info_dict.get('UID')
        self.username = info_dict.get('用户名').split('\xa0')[0] if '用户名' in info_dict else None
        self.user_level = info_dict.get('用户级别') if '用户级别' in info_dict else None
        self.join_at = StringUtils.unify_datetime_str(info_dict.get('注册时间')) if '注册时间' in info_dict else None

        self.upload = StringUtils.num_filesize(info_dict.get('上传流量')) if '上传流量' in info_dict else 0
        self.download = StringUtils.num_filesize(info_dict.get('下载流量')) if '下载流量' in info_dict else 0
        self.ratio = float(info_dict.get('共享率')) if '共享率' in info_dict else 0
        bonus_str = info_dict.get('星辰', '')
        self.bonus = float(re.search(r'累计([\d\.]+)', bonus_str).group(1)) if re.search(r'累计([\d\.]+)', bonus_str) else 0
        self.message_unread = 0

        if hasattr(self, '_torrent_seeding_base') and self._torrent_seeding_base:
            self.seeding = 0
            self.seeding_size = 0
        else:
            seeding_info = soup.find('div', style="margin:0 auto;width:90%;font-size:14px;margin-top:10px;margin-bottom:10px;text-align:center;")
            if seeding_info:
                seeding_link = seeding_info.find_all('a')[1].text if len(seeding_info.find_all('a')) > 1 else ''
                match = re.search(r'当前上传的种子\((\d+)个, 共([\d\.]+ [KMGT]B)\)', seeding_link)
                if match:
                    self.seeding = int(match.group(1))
                    self.seeding_size = StringUtils.num_filesize(match.group(2))
                else:
                    self.seeding = 0
                    self.seeding_size = 0

    def _parse_user_traffic_info(self, html_text: str):
        pass

    def _parse_user_detail_info(self, html_text: str):
        pass

    def _parse_user_torrent_seeding_page_info(self, html_text: str) -> Tuple[int, int]:
        if not html_text:
            return 0, 0
        soup = BeautifulSoup(html_text, 'html.parser')
        torrent_table = soup.find('table', class_='torrenttable')
        if not torrent_table:
            return 0, 0
        rows = torrent_table.find_all('tr')
        if len(rows) <= 1:
            return 0, 0
        torrents = [row for row in rows[1:] if 'btr' in row.get('class', [])]
        page_seeding = 0
        page_seeding_size = 0
        for torrent in torrents:
            size_td = torrent.find('td', class_='r')
            if size_td:
                size_a = size_td.find('a')
                size_text = size_a.text.strip() if size_a else size_td.text.strip()
                if size_text:
                    page_seeding += 1
                    page_seeding_size += StringUtils.num_filesize(size_text)
        return page_seeding, page_seeding_size

    def _parse_message_unread_links(self, html_text: str, msg_links: list) -> Optional[str]:
        pass

    def _parse_message_content(self, html_text) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        pass

    def _parse_user_torrent_seeding_info(self, html_text: str, **kwargs):
        pass

    def parse(self):
        super().parse()
        if self._index_html:
            soup = BeautifulSoup(self._index_html, 'html.parser')
            user_link = soup.find('a', href=re.compile(r'userdetails\.php\?uid=\d+'))
            if user_link:
                uid_match = re.search(r'uid=(\d+)', user_link['href'])
                if uid_match:
                    self.userid = uid_match.group(1)

        if self.userid and self._user_basic_page:
            basic_url = self._user_basic_page.format(uid=self.userid)
            basic_html = self._get_page_content(url=urljoin(self._base_url, basic_url))
            self._parse_user_base_info(basic_html)

        if hasattr(self, '_torrent_seeding_base') and self._torrent_seeding_base:
            seeding_base_url = urljoin(self._base_url, self._torrent_seeding_base)
            params = self._torrent_seeding_params.copy()
            page_num = 1
            while True:
                params['p'] = page_num
                query_string = urlencode(params)
                full_url = f"{seeding_base_url}?{query_string}"
                seeding_html = self._get_page_content(url=full_url)
                page_seeding, page_seeding_size = self._parse_user_torrent_seeding_page_info(seeding_html)
                self.seeding += page_seeding
                self.seeding_size += page_seeding_size
                if page_seeding == 0:
                    break
                page_num += 1

        # 🔑 最终对外统一转字符串
        self.userid = str(self.userid or "")
        self.username = str(self.username or "")
        self.user_level = str(self.user_level or "")
        self.join_at = str(self.join_at or "")

        self.upload = str(self.upload or 0)
        self.download = str(self.download or 0)
        self.ratio = str(self.ratio or 0)
        self.bonus = str(self.bonus or 0.0)
        self.message_unread = str(self.message_unread or 0)

        self.seeding = str(self.seeding or 0)
        self.seeding_size = str(self.seeding_size or 0)


# ===== from parser/discuz.py =====
class DiscuzUserInfo(SiteParserBase):
    schema = SiteSchema.DiscuzX

    def _parse_user_base_info(self, html_text: str):
        html_text = self._prepare_html_text(html_text)
        html = etree.HTML(html_text)
        try:
            user_info = html.xpath('//a[contains(@href, "&uid=")]')
            if user_info:
                user_id_match = re.search(r"&uid=(\d+)", user_info[0].attrib['href'])
                if user_id_match and user_id_match.group().strip():
                    self.userid = user_id_match.group(1)
                    self._torrent_seeding_page = f"forum.php?&mod=torrents&cat_5up=on"
                    self._user_detail_page = user_info[0].attrib['href']
                    self.username = user_info[0].text.strip()
        finally:
            if html is not None:
                del html

    def _parse_site_page(self, html_text: str):
        pass

    def _parse_user_detail_info(self, html_text: str):
        """
        解析用户额外信息，加入时间，等级
        :param html_text:
        :return:
        """
        html = etree.HTML(html_text)
        try:
            if not StringUtils.is_valid_html_element(html):
                return None

            # 用户等级
            user_levels_text = html.xpath('//a[contains(@href, "usergroup")]/text()')
            if user_levels_text:
                self.user_level = user_levels_text[-1].strip()

            # 加入日期
            join_at_text = html.xpath('//li[em[text()="注册时间"]]/text()')
            if join_at_text:
                self.join_at = StringUtils.unify_datetime_str(join_at_text[0].strip())

            # 分享率
            ratio_text = html.xpath('//li[contains(.//text(), "分享率")]//text()')
            if ratio_text:
                ratio_match = re.search(r"\(([\d,.]+)\)", ratio_text[0])
                if ratio_match and ratio_match.group(1).strip():
                    self.bonus = StringUtils.str_float(ratio_match.group(1))

            # 积分
            bouns_text = html.xpath('//li[em[text()="积分"]]/text()')
            if bouns_text:
                self.bonus = StringUtils.str_float(bouns_text[0].strip())

            # 上传
            upload_text = html.xpath('//li[em[contains(text(),"上传量")]]/text()')
            if upload_text:
                self.upload = StringUtils.num_filesize(upload_text[0].strip().split('/')[-1])

            # 下载
            download_text = html.xpath('//li[em[contains(text(),"下载量")]]/text()')
            if download_text:
                self.download = StringUtils.num_filesize(download_text[0].strip().split('/')[-1])
        finally:
            if html is not None:
                del html

    def _parse_user_torrent_seeding_info(self, html_text: str, multi_page: bool = False) -> Optional[str]:
        """
        做种相关信息
        :param html_text:
        :param multi_page: 是否多页数据
        :return: 下页地址
        """
        html = etree.HTML(html_text)
        try:
            if not StringUtils.is_valid_html_element(html):
                return None

            size_col = 3
            seeders_col = 4
            # 搜索size列
            if html.xpath('//tr[position()=1]/td[.//img[@class="size"] and .//img[@alt="size"]]'):
                size_col = len(html.xpath('//tr[position()=1]/td[.//img[@class="size"] '
                                          'and .//img[@alt="size"]]/preceding-sibling::td')) + 1
            # 搜索seeders列
            if html.xpath('//tr[position()=1]/td[.//img[@class="seeders"] and .//img[@alt="seeders"]]'):
                seeders_col = len(html.xpath('//tr[position()=1]/td[.//img[@class="seeders"] '
                                             'and .//img[@alt="seeders"]]/preceding-sibling::td')) + 1

            page_seeding = 0
            page_seeding_size = 0
            page_seeding_info = []
            seeding_sizes = html.xpath(f'//tr[position()>1]/td[{size_col}]')
            seeding_seeders = html.xpath(f'//tr[position()>1]/td[{seeders_col}]//text()')
            if seeding_sizes and seeding_seeders:
                page_seeding = len(seeding_sizes)

                for i in range(0, len(seeding_sizes)):
                    size = StringUtils.num_filesize(seeding_sizes[i].xpath("string(.)").strip())
                    seeders = StringUtils.str_int(seeding_seeders[i])

                    page_seeding_size += size
                    page_seeding_info.append([seeders, size])

            self.seeding += page_seeding
            self.seeding_size += page_seeding_size
            self.seeding_info.extend(page_seeding_info)

            # 是否存在下页数据
            next_page = None
            next_page_text = html.xpath('//a[contains(.//text(), "下一页") or contains(.//text(), "下一頁")]/@href')
            if next_page_text:
                next_page = next_page_text[-1].strip()
        finally:
            if html is not None:
                del html

        return next_page

    def _parse_user_traffic_info(self, html_text: str):
        pass

    def _parse_message_unread_links(self, html_text: str, msg_links: list) -> Optional[str]:
        return None

    def _parse_message_content(self, html_text):
        return None, None, None


# ===== from parser/file_list.py =====
class FileListSiteUserInfo(SiteParserBase):
    schema = SiteSchema.FileList

    def _parse_site_page(self, html_text: str):
        html_text = self._prepare_html_text(html_text)

        user_detail = re.search(r"userdetails.php\?id=(\d+)", html_text)
        if user_detail and user_detail.group().strip():
            self._user_detail_page = user_detail.group().strip().lstrip('/')
            self.userid = user_detail.group(1)

        self._torrent_seeding_page = f"snatchlist.php?id={self.userid}&action=torrents&type=seeding"

    def _parse_user_base_info(self, html_text: str):
        html_text = self._prepare_html_text(html_text)
        html = etree.HTML(html_text)
        try:
            ret = html.xpath(f'//a[contains(@href, "userdetails") and contains(@href, "{self.userid}")]//text()')
            if ret:
                self.username = str(ret[0])
        finally:
            if html is not None:
                del html

    def _parse_user_traffic_info(self, html_text: str):
        """
        上传/下载/分享率 [做种数/魔力值]
        :param html_text:
        :return:
        """
        return

    def _parse_user_detail_info(self, html_text: str):
        html_text = self._prepare_html_text(html_text)
        html = etree.HTML(html_text)
        try:
            upload_html = html.xpath('//table//tr/td[text()="Uploaded"]/following-sibling::td//text()')
            if upload_html:
                self.upload = StringUtils.num_filesize(upload_html[0])
            download_html = html.xpath('//table//tr/td[text()="Downloaded"]/following-sibling::td//text()')
            if download_html:
                self.download = StringUtils.num_filesize(download_html[0])

            ratio_html = html.xpath('//table//tr/td[text()="Share ratio"]/following-sibling::td//text()')
            if ratio_html:
                share_ratio = StringUtils.str_float(ratio_html[0])
            else:
                share_ratio = 0
            self.ratio = 0 if self.download == 0 else share_ratio

            seed_html = html.xpath('//table//tr/td[text()="Seed bonus"]/following-sibling::td//text()')
            if seed_html:
                self.seeding = StringUtils.str_int(seed_html[1])
                self.seeding_size = StringUtils.num_filesize(seed_html[3])

            user_level_html = html.xpath('//table//tr/td[text()="Class"]/following-sibling::td//text()')
            if user_level_html:
                self.user_level = user_level_html[0].strip()

            join_at_html = html.xpath('//table//tr/td[contains(text(), "Join")]/following-sibling::td//text()')
            if join_at_html:
                join_at = (join_at_html[0].split("("))[0].strip()
                self.join_at = StringUtils.unify_datetime_str(join_at)

            bonus_html = html.xpath('//a[contains(@href, "shop.php")]')
            if bonus_html:
                self.bonus = StringUtils.str_float(bonus_html[0].xpath("string(.)").strip())
        finally:
            if html is not None:
                del html

    def _parse_user_torrent_seeding_info(self, html_text: str, multi_page: Optional[bool] = False) -> Optional[str]:
        """
        做种相关信息
        :param html_text:
        :param multi_page: 是否多页数据
        :return: 下页地址
        """
        html = etree.HTML(html_text)
        try:
            if not StringUtils.is_valid_html_element(html):
                return None

            size_col = 6
            seeders_col = 7

            page_seeding_size = 0
            page_seeding_info = []
            seeding_sizes = html.xpath(f'//table/tr[position()>1]/td[{size_col}]')
            seeding_seeders = html.xpath(f'//table/tr[position()>1]/td[{seeders_col}]')
            if seeding_sizes and seeding_seeders:
                for i in range(0, len(seeding_sizes)):
                    size = StringUtils.num_filesize(seeding_sizes[i].xpath("string(.)").strip())
                    seeders = StringUtils.str_int(seeding_seeders[i].xpath("string(.)").strip())

                    page_seeding_size += size
                    page_seeding_info.append([seeders, size])

            self.seeding_info.extend(page_seeding_info)

            # 是否存在下页数据
            next_page = None
        finally:
            if html is not None:
                del html

        return next_page

    def _parse_message_unread_links(self, html_text: str, msg_links: list) -> Optional[str]:
        return None

    def _parse_message_content(self, html_text):
        return None, None, None


# ===== from parser/gazelle.py =====
class GazelleSiteUserInfo(SiteParserBase):
    schema = SiteSchema.Gazelle

    def _parse_user_base_info(self, html_text: str):
        html_text = self._prepare_html_text(html_text)
        html = etree.HTML(html_text)
        try:
            tmps = html.xpath('//a[contains(@href, "user.php?id=") or contains(@href, "user?id=")]')
            if tmps:
                user_id_match = re.search(r"user(?:\.php)?\?id=(\d+)", tmps[0].attrib['href'])
                if user_id_match and user_id_match.group().strip():
                    self.userid = user_id_match.group(1)
                    self._torrent_seeding_page = f"torrents.php?type=seeding&userid={self.userid}"
                    self._user_detail_page = f"user.php?id={self.userid}"
                    self.username = tmps[0].text.strip()

            tmps = html.xpath('//*[@id="header-uploaded-value"]/@data-value')
            if tmps:
                self.upload = StringUtils.num_filesize(tmps[0])
            else:
                tmps = html.xpath('//li[@id="stats_seeding"]/span/text()')
                if tmps:
                    self.upload = StringUtils.num_filesize(tmps[0])

            tmps = html.xpath('//*[@id="header-downloaded-value"]/@data-value')
            if tmps:
                self.download = StringUtils.num_filesize(tmps[0])
            else:
                tmps = html.xpath('//li[@id="stats_leeching"]/span/text()')
                if tmps:
                    self.download = StringUtils.num_filesize(tmps[0])

            self.ratio = 0.0 if self.download <= 0.0 else round(self.upload / self.download, 3)

            tmps = html.xpath('//a[contains(@href, "bonus")]/@data-tooltip')
            if tmps:
                bonus_match = re.search(r"([\d,.]+)", tmps[0])
                if bonus_match and bonus_match.group(1).strip():
                    self.bonus = StringUtils.str_float(bonus_match.group(1))
            else:
                tmps = html.xpath('//a[contains(@href, "bonus")]')
                if tmps:
                    bonus_text = tmps[0].xpath("string(.)")
                    bonus_match = re.search(r"([\d,.]+)", bonus_text)
                    if bonus_match and bonus_match.group(1).strip():
                        self.bonus = StringUtils.str_float(bonus_match.group(1))
        finally:
            if html is not None:
                del html

    def _parse_site_page(self, html_text: str):
        pass

    def _parse_user_detail_info(self, html_text: str):
        """
        解析用户额外信息，加入时间，等级
        :param html_text:
        :return:
        """
        html = etree.HTML(html_text)
        try:
            if not StringUtils.is_valid_html_element(html):
                return None

            # 用户等级
            user_levels_text = html.xpath('//*[@id="class-value"]/@data-value')
            if user_levels_text:
                self.user_level = user_levels_text[0].strip()
            else:
                user_levels_text = html.xpath('//li[contains(text(), "用户等级")]/text()')
                if user_levels_text:
                    self.user_level = user_levels_text[0].split(':')[1].strip()

            # 加入日期
            join_at_text = html.xpath('//*[@id="join-date-value"]/@data-value')
            if join_at_text:
                self.join_at = StringUtils.unify_datetime_str(join_at_text[0].strip())
            else:
                join_at_text = html.xpath(
                    '//div[contains(@class, "box_userinfo_stats")]//li[contains(text(), "加入时间")]/span/text()')
                if join_at_text:
                    self.join_at = StringUtils.unify_datetime_str(join_at_text[0].strip())
        finally:
            if html is not None:
                del html

    def _parse_user_torrent_seeding_info(self, html_text: str, multi_page: Optional[bool] = False) -> Optional[str]:
        """
        做种相关信息
        :param html_text:
        :param multi_page: 是否多页数据
        :return: 下页地址
        """
        html = etree.HTML(html_text)
        try:
            if not StringUtils.is_valid_html_element(html):
                return None

            size_col = 3
            # 搜索size列
            if html.xpath('//table[contains(@id, "torrent")]//tr[1]/td'):
                size_col = len(html.xpath('//table[contains(@id, "torrent")]//tr[1]/td')) - 3
            # 搜索seeders列
            seeders_col = size_col + 2

            page_seeding = 0
            page_seeding_size = 0
            page_seeding_info = []
            seeding_sizes = html.xpath(f'//table[contains(@id, "torrent")]//tr[position()>1]/td[{size_col}]')
            seeding_seeders = html.xpath(f'//table[contains(@id, "torrent")]//tr[position()>1]/td[{seeders_col}]/text()')
            if seeding_sizes and seeding_seeders:
                page_seeding = len(seeding_sizes)

                for i in range(0, len(seeding_sizes)):
                    size = StringUtils.num_filesize(seeding_sizes[i].xpath("string(.)").strip())
                    seeders = int(seeding_seeders[i])

                    page_seeding_size += size
                    page_seeding_info.append([seeders, size])

            if multi_page:
                self.seeding += page_seeding
                self.seeding_size += page_seeding_size
                self.seeding_info.extend(page_seeding_info)
            else:
                if not self.seeding:
                    self.seeding = page_seeding
                if not self.seeding_size:
                    self.seeding_size = page_seeding_size
                if not self.seeding_info:
                    self.seeding_info = page_seeding_info

            # 是否存在下页数据
            next_page = None
            next_page_text = html.xpath('//a[contains(.//text(), "Next") or contains(.//text(), "下一页") or contains(@title, "下一页") or contains(@title, "Next")]/@href')
            if next_page_text:
                next_page = next_page_text[-1].strip()
        finally:
            if html is not None:
                del html

        return next_page

    def _parse_user_traffic_info(self, html_text: str):
        pass

    def _parse_message_unread_links(self, html_text: str, msg_links: list) -> Optional[str]:
        return None

    def _parse_message_content(self, html_text):
        return None, None, None


# ===== from parser/hddolby.py =====
class HDDolbySiteUserInfo(SiteParserBase):
    schema = SiteSchema.HDDolby
    request_mode = "apikey"

    # 用户级别字典
    HDDolby_sysRoleList = {
        "0": "Peasant",
        "1": "User",
        "2": "Power User",
        "3": "Elite User",
        "4": "Crazy User",
        "5": "Insane User",
        "6": "Veteran User",
        "7": "Extreme User",
        "8": "Ultimate User",
        "9": "Nexus Master",
        "10": "VIP",
        "11": "Retiree",
        "12": "Helper",
        "13": "Seeder",
        "14": "Transferrer",
        "15": "Uploader",
        "16": "Torrent Manager",
        "17": "Forum Moderator",
        "18": "Coder",
        "19": "Moderator",
        "20": "Administrator",
        "21": "Sysop",
        "22": "Staff Leader",
    }

    def _parse_site_page(self, html_text: str):
        """
        获取站点页面地址
        """
        # 更换api地址
        self._base_url = f"https://api.{StringUtils.get_url_domain(self._base_url)}"
        self._user_traffic_page = None
        self._user_detail_page = None
        self._user_basic_page = "api/v1/user/data"
        self._user_basic_params = {}
        self._user_basic_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*"
        }
        self._sys_mail_unread_page = None
        self._user_mail_unread_page = None
        self._mail_unread_params = {}
        self._torrent_seeding_page = "api/v1/user/peers"
        self._torrent_seeding_params = {}
        self._torrent_seeding_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*"
        }
        self._addition_headers = {
            "x-api-key": self.apikey,
        }

    def _parse_logged_in(self, html_text):
        """
        判断是否登录成功, 通过判断是否存在用户信息
        暂时跳过检测，待后续优化
        :param html_text:
        :return:
        """
        return True

    def _parse_user_base_info(self, html_text: str):
        """
        解析用户基本信息，这里把_parse_user_traffic_info和_parse_user_detail_info合并到这里
        """
        if not html_text:
            return None
        detail = json.loads(html_text)
        if not detail or detail.get("status") != 0:
            return
        user_infos = detail.get("data")
        """
        {
            "id": "1",
            "added": "2019-03-03 15:30:36",
            "last_access": "2025-02-18 19:48:04",
            "class": "22",
            "uploaded": "852071699418375",
            "downloaded": "1885536536176",
            "seedbonus": "99774808.0",
            "sebonus": "3739023.7",
            "unread_messages": "0",
        }
        """
        if not user_infos:
            return
        user_info = user_infos[0]
        self.userid = user_info.get("id")
        self.username = user_info.get("username")
        self.user_level = self.HDDolby_sysRoleList.get(user_info.get("class") or "1")
        self.join_at = user_info.get("added")
        self.upload = int(user_info.get("uploaded") or '0')
        self.download = int(user_info.get("downloaded") or '0')
        self.ratio = round(self.upload / self.download, 2) if self.download else 0
        self.bonus = float(user_info.get("seedbonus") or "0")
        self.message_unread = int(user_info.get("unread_messages") or '0')

    def _parse_user_traffic_info(self, html_text: str):
        """
        解析用户流量信息
        """
        pass

    def _parse_user_detail_info(self, html_text: str):
        """
        解析用户详细信息
        """
        pass

    def _parse_user_torrent_seeding_info(self, html_text: str, multi_page: Optional[bool] = False) -> Optional[str]:
        """
        解析用户做种信息
        """
        if not html_text:
            return None
        seeding_info = json.loads(html_text)
        if not seeding_info or seeding_info.get("status") != 0:
            return None
        torrents = seeding_info.get("data", [])
        page_seeding_size = 0
        page_seeding_info = []
        for info in torrents:
            size = info.get("size")
            seeder = info.get("seeders") or 1
            page_seeding_size += size
            page_seeding_info.append([seeder, size])
        self.seeding += len(torrents)
        self.seeding_size += page_seeding_size
        self.seeding_info.extend(page_seeding_info)

        return None

    def _parse_message_unread_links(self, html_text: str, msg_links: list) -> Optional[str]:
        """
        解析未读消息链接，这里直接读出详情
        """
        pass

    def _parse_message_content(self, html_text) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        解析消息内容
        """
        pass


# ===== from parser/ipt_project.py =====
class IptSiteUserInfo(SiteParserBase):
    schema = SiteSchema.Ipt

    def _parse_user_base_info(self, html_text: str):
        html_text = self._prepare_html_text(html_text)
        html = etree.HTML(html_text)
        try:
            tmps = html.xpath('//a[contains(@href, "/u/")]//text()')
            tmps_id = html.xpath('//a[contains(@href, "/u/")]/@href')
            if tmps:
                self.username = str(tmps[-1])
            if tmps_id:
                user_id_match = re.search(r"/u/(\d+)", tmps_id[0])
                if user_id_match and user_id_match.group().strip():
                    self.userid = user_id_match.group(1)
                    self._user_detail_page = f"user.php?u={self.userid}"
                    self._torrent_seeding_page = f"peers?u={self.userid}"

            tmps = html.xpath('//div[@class = "stats"]/div/div')
            if tmps:
                self.upload = StringUtils.num_filesize(str(tmps[0].xpath('span/text()')[1]).strip())
                self.download = StringUtils.num_filesize(str(tmps[0].xpath('span/text()')[2]).strip())
                self.seeding = StringUtils.str_int(tmps[0].xpath('a')[2].xpath('text()')[0])
                self.leeching = StringUtils.str_int(tmps[0].xpath('a')[2].xpath('text()')[1])
                self.ratio = StringUtils.str_float(str(tmps[0].xpath('span/text()')[0]).strip().replace('-', '0'))
                self.bonus = StringUtils.str_float(tmps[0].xpath('a')[3].xpath('text()')[0])
        finally:
            if html is not None:
                del html

    def _parse_site_page(self, html_text: str):
        pass

    def _parse_user_detail_info(self, html_text: str):
        html = etree.HTML(html_text)
        try:
            if not StringUtils.is_valid_html_element(html):
                return

            user_levels_text = html.xpath('//tr/th[text()="Class"]/following-sibling::td[1]/text()')
            if user_levels_text:
                self.user_level = user_levels_text[0].strip()

            # 加入日期
            join_at_text = html.xpath('//tr/th[text()="Join date"]/following-sibling::td[1]/text()')
            if join_at_text:
                self.join_at = StringUtils.unify_datetime_str(join_at_text[0].split(' (')[0])
        finally:
            if html is not None:
                del html

    def _parse_user_torrent_seeding_info(self, html_text: str, multi_page: bool = False) -> Optional[str]:
        html = etree.HTML(html_text)
        try:
            if not StringUtils.is_valid_html_element(html):
                return None
            # seeding start
            seeding_end_pos = 3
            if html.xpath('//tr/td[text() = "Leechers"]'):
                seeding_end_pos = len(html.xpath('//tr/td[text() = "Leechers"]/../preceding-sibling::tr')) + 1
                seeding_end_pos = seeding_end_pos - 3

            page_seeding = 0
            page_seeding_size = 0
            seeding_torrents = html.xpath('//tr/td[text() = "Seeders"]/../following-sibling::tr/td[position()=6]/text()')
            if seeding_torrents:
                page_seeding = seeding_end_pos
                for per_size in seeding_torrents[:seeding_end_pos]:
                    if '(' in per_size and ')' in per_size:
                        per_size = per_size.split('(')[-1]
                        per_size = per_size.split(')')[0]

                    page_seeding_size += StringUtils.num_filesize(per_size)

            self.seeding = page_seeding
            self.seeding_size = page_seeding_size
        finally:
            if html is not None:
                del html

    def _parse_user_traffic_info(self, html_text: str):
        pass

    def _parse_message_unread_links(self, html_text: str, msg_links: list) -> Optional[str]:
        return None

    def _parse_message_content(self, html_text):
        return None, None, None


# ===== from parser/mtorrent.py =====
class MTorrentSiteUserInfo(SiteParserBase):
    schema = SiteSchema.MTorrent
    request_mode = "apikey"

    # 用户级别字典
    MTeam_sysRoleList = {
        "1": "User",
        "2": "Power User",
        "3": "Elite User",
        "4": "Crazy User",
        "5": "Insane User",
        "6": "Veteran User",
        "7": "Extreme User",
        "8": "Ultimate User",
        "9": "Nexus Master",
        "10": "VIP",
        "11": "Retiree",
        "12": "Uploader",
        "13": "Moderator",
        "14": "Administrator",
        "15": "Sysop",
        "16": "Staff",
        "17": "Offer memberStaff",
        "18": "Bet memberStaff",
    }

    def _parse_site_page(self, html_text: str):
        """
        获取站点页面地址
        """
        # 更换api地址
        self._base_url = f"https://api.{StringUtils.get_url_domain(self._base_url)}"
        self._user_traffic_page = None
        self._user_detail_page = None
        self._user_basic_page = "api/member/profile"
        self._user_basic_params = {
            "uid": self.userid
        }
        self._sys_mail_unread_page = None
        self._user_mail_unread_page = "api/msg/search"
        self._mail_unread_params = {
            "keyword": "",
            "box": "-2",
            "type": "pageNumber",
            "pageSize": 100
        }
        self._torrent_seeding_page = "api/member/getUserTorrentList"
        self._torrent_seeding_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*"
        }
        self._addition_headers = {
            "x-api-key": self.apikey,
        }

    def _parse_logged_in(self, html_text):
        """
        判断是否登录成功, 通过判断是否存在用户信息
        暂时跳过检测，待后续优化
        :param html_text:
        :return:
        """
        return True

    def _parse_user_base_info(self, html_text: str):
        """
        解析用户基本信息，这里把_parse_user_traffic_info和_parse_user_detail_info合并到这里
        """
        if not html_text:
            return None
        detail = json.loads(html_text)
        if not detail or detail.get("code") != "0":
            return
        user_info = detail.get("data", {})
        self.userid = user_info.get("id")
        self.username = user_info.get("username")
        self.user_level = self.MTeam_sysRoleList.get(user_info.get("role") or "1")
        self.join_at = user_info.get("memberStatus", {}).get("createdDate")

        self.upload = int(user_info.get("memberCount", {}).get("uploaded") or '0')
        self.download = int(user_info.get("memberCount", {}).get("downloaded") or '0')
        self.ratio = user_info.get("memberCount", {}).get("shareRate") or 0
        self.bonus = user_info.get("memberCount", {}).get("bonus") or 0
        self.message_read_force = True
        self._torrent_seeding_params = {
            "pageNumber": 1,
            "pageSize": 200,
            "type": "SEEDING",
            "userid": self.userid
        }

    def _parse_user_traffic_info(self, html_text: str):
        """
        解析用户流量信息
        """
        pass

    def _parse_user_detail_info(self, html_text: str):
        """
        解析用户详细信息
        """
        pass

    def _parse_user_torrent_seeding_info(self, html_text: str, multi_page: Optional[bool] = False) -> Optional[str]:
        """
        解析用户做种信息
        """
        if not html_text:
            return None
        seeding_info = json.loads(html_text)
        if not seeding_info or seeding_info.get("code") != "0":
            return None
        torrents = seeding_info.get("data", {}).get("data", [])
        page_seeding_size = 0
        page_seeding_info = []
        for info in torrents:
            torrent = info.get("torrent", {})
            size = int(torrent.get("size") or '0')
            seeders = int(torrent.get("source") or '0')
            page_seeding_size += size
            page_seeding_info.append([seeders, size])
        self.seeding += len(torrents)
        self.seeding_size += page_seeding_size
        self.seeding_info.extend(page_seeding_info)

        # 查询总做种数
        seeder_count = 0
        try:
            result = self._get_page_content(
                url=urljoin(self._base_url, "api/tracker/myPeerStatus"),
                params={"uid": self.userid},
            )
            if result:
                seeder_info = json.loads(result)
                seeder_count = int(seeder_info.get("data", {}).get("seeder") or 0)
        except Exception as e:
            logger.error(f"获取做种数失败: {str(e)}")
        if not seeder_count:
            return None
        if self.seeding >= seeder_count:
            return None
        # 还有下一页
        self._torrent_seeding_params["pageNumber"] += 1
        return ""

    def _parse_message_unread_links(self, html_text: str, msg_links: list) -> Optional[str]:
        """
        解析未读消息链接，这里直接读出详情
        """
        if not html_text:
            return None
        messages_info = json.loads(html_text)
        if not messages_info or messages_info.get("code") != "0":
            return None
        messages = messages_info.get("data", {}).get("data", [])
        for message in messages:
            if not message.get("unread"):
                continue
            head = message.get("title")
            date = message.get("createdDate")
            content = message.get("context")
            if head and date and content:
                self.message_unread_contents.append((head, date, content))
                # 设置已读
                self._get_page_content(
                    url=urljoin(self._base_url, f"api/msg/markRead"),
                    params={"msgId": message.get("id")}
                )
        # 是否存在下页数据
        return None

    def _parse_message_content(self, html_text) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        解析消息内容
        """
        pass


# ===== from parser/nexus_audiences.py =====
class NexusAudiencesSiteUserInfo(NexusPhpSiteUserInfo):
    schema = SiteSchema.NexusAudiences

    def _parse_user_traffic_info(self, html_text):
        """
        解析用户流量信息
        """
        super()._parse_user_traffic_info(html_text)
        self.__parse_userbar_info(html_text)

    def _parse_user_detail_info(self, html_text: str):
        """
        解析用户额外信息
        """
        super()._parse_user_detail_info(html_text)
        self.__parse_userbar_info(html_text)

    def __parse_userbar_info(self, html_text: str):
        """
        解析 Audiences 新版顶部用户栏，覆盖 NexusPHP 通用正则的误判。
        """
        html = etree.HTML(html_text)
        try:
            if not StringUtils.is_valid_html_element(html):
                return

            for user_node in html.xpath('//*[@data-uploader-url or @data-uploader-stats]'):
                self.__parse_user_identity(user_node)
                self.__parse_uploader_stats(user_node.get("data-uploader-stats"))

            # data-uploader-stats 不包含分享率，需从 compact metric 的 class 中读取。
            self.__parse_compact_metric(html, "ratio", "ratio")
            self.__parse_compact_metric(html, "uploaded", "upload")
            self.__parse_compact_metric(html, "downloaded", "download")
            self.__parse_compact_metric(html, "bonus", "bonus")
            self.__parse_compact_metric(html, "active", "active")
        finally:
            if html is not None:
                del html

    def __parse_user_identity(self, user_node):
        """
        从新版用户卡属性中提取用户 ID、用户名和等级。
        """
        user_url = user_node.get("data-uploader-url") or ""
        user_detail = re.search(r"userdetails\.php\?id=(\d+)", user_url)
        if user_detail and user_detail.group(1).strip():
            self.userid = user_detail.group(1).strip()

        username = user_node.get("data-uploader-label")
        if username and username.strip():
            self.username = username.strip()

        user_level = user_node.get("data-uploader-badge")
        if user_level and user_level.strip():
            self.user_level = user_level.strip()

    def __parse_uploader_stats(self, stats_text: str):
        """
        解析 data-uploader-stats 中的结构化流量数据。
        """
        if not stats_text:
            return

        try:
            stats = json.loads(stats_text)
        except (TypeError, ValueError):
            return

        if not isinstance(stats, list):
            return

        for item in stats:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip(" ：:")
            tone = str(item.get("tone") or "").strip()
            value = str(item.get("value") or "").strip()
            self.__set_metric_value(label=label, tone=tone, value=value)

    def __parse_compact_metric(self, html, metric: str, field: str):
        """
        按 compact metric 的 class 读取新版用户栏中的单项数据。
        """
        values = html.xpath(
            f'//*[contains(concat(" ", normalize-space(@class), " "), " site-userbar__compact-metric--{metric} ")]'
            '//span[normalize-space()][last()]/text()'
        )
        if not values:
            values = html.xpath(
                f'//*[contains(concat(" ", normalize-space(@class), " "), " site-userbar__compact-metric--{metric} ")]'
                '/text()'
            )
        if values:
            self.__set_metric_value(field=field, value=values[-1].strip())

    def __set_metric_value(self, value: str, label: str = None, tone: str = None, field: str = None):
        """
        将 Audiences 用户栏指标写入通用用户数据字段。
        """
        if not value:
            return

        metric_key = field or tone or label
        if metric_key in {"uploaded", "上传量", "upload"}:
            self.upload = StringUtils.num_filesize(value)
        elif metric_key in {"downloaded", "下载量", "download"}:
            self.download = StringUtils.num_filesize(value)
        elif metric_key in {"bonus", "爆米花"}:
            self.bonus = StringUtils.str_float(value)
        elif metric_key == "ratio":
            self.ratio = StringUtils.str_float(value)
        elif metric_key in {"active", "活跃"}:
            active_match = re.search(r"↑\s*(\d+)\s*/\s*↓\s*(\d+)", value)
            if active_match:
                self.seeding = StringUtils.str_int(active_match.group(1))
                self.leeching = StringUtils.str_int(active_match.group(2))

    def _parse_seeding_pages(self):
        if not self._torrent_seeding_page:
            return
        self._torrent_seeding_headers = {"Referer": urljoin(self._base_url, self._user_detail_page)}
        html_text = self._get_page_content(
            url=urljoin(self._base_url, self._torrent_seeding_page),
            params=self._torrent_seeding_params,
            headers=self._torrent_seeding_headers
        )
        if not html_text:
            return
        html = etree.HTML(html_text)
        try:
            if not StringUtils.is_valid_html_element(html):
                return
            total_row = html.xpath('//table[@class="table table-bordered"]//tr[td[1][normalize-space()="Total"]]')
            if not total_row:
                return
            seeding_count = total_row[0].xpath('./td[2]/text()')
            seeding_size = total_row[0].xpath('./td[3]/text()')
            self.seeding = StringUtils.str_int(seeding_count[0]) if seeding_count else 0
            self.seeding_size = StringUtils.num_filesize(seeding_size[0].strip()) if seeding_size else 0
        finally:
            if html is not None:
                del html


# ===== from parser/nexus_hhanclub.py =====
class NexusHhanclubSiteUserInfo(NexusPhpSiteUserInfo):
    schema = SiteSchema.NexusHhanclub

    def _parse_user_traffic_info(self, html_text):
        super()._parse_user_traffic_info(html_text)

        html_text = self._prepare_html_text(html_text)
        html = etree.HTML(html_text)

        try:
            # 上传、下载、分享率
            upload_match = re.search(r"[_<>/a-zA-Z-=\"'\s#;]+([\d,.\s]+[KMGTPI]*B)",
                                     html.xpath('//*[@id="user-info-panel"]/div[2]/div[2]/div[4]/text()')[0])
            download_match = re.search(r"[_<>/a-zA-Z-=\"'\s#;]+([\d,.\s]+[KMGTPI]*B)",
                                       html.xpath('//*[@id="user-info-panel"]/div[2]/div[2]/div[5]/text()')[0])
            ratio_match = re.search(r"分享率][:：_<>/a-zA-Z-=\"'\s#;]+([\d,.\s]+)",
                                    html.xpath('//*[@id="user-info-panel"]/div[2]/div[1]/div[1]/div/text()')[0])

            # 计算分享率
            self.upload = StringUtils.num_filesize(upload_match.group(1).strip()) if upload_match else 0
            self.download = StringUtils.num_filesize(download_match.group(1).strip()) if download_match else 0
            # 优先使用页面上的分享率
            calc_ratio = 0.0 if self.download <= 0.0 else round(self.upload / self.download, 3)
            self.ratio = StringUtils.str_float(ratio_match.group(1)) if (
                    ratio_match and ratio_match.group(1).strip()) else calc_ratio
        finally:
            if html is not None:
                del html

    def _parse_user_detail_info(self, html_text: str):
        """
        解析用户额外信息，加入时间，等级
        :param html_text:
        :return:
        """
        super()._parse_user_detail_info(html_text)

        html = etree.HTML(html_text)
        try:
            if not StringUtils.is_valid_html_element(html):
                return
            # 加入时间
            join_at_text = html.xpath('//span[contains(text(), "加入日期")]/following-sibling::span/span/@title')
            if join_at_text:
                self.join_at = StringUtils.unify_datetime_str(join_at_text[0].strip())
        finally:
            if html is not None:
                del html

    def _get_user_level(self, html):
        super()._get_user_level(html)
        user_level_path = html.xpath('//b[contains(@class, "_Name")]/text()')
        if user_level_path:
            self.user_level = user_level_path[0]


# ===== from parser/nexus_project.py =====
class NexusProjectSiteUserInfo(NexusPhpSiteUserInfo):
    schema = SiteSchema.NexusProject

    def _parse_site_page(self, html_text: str):
        html_text = self._prepare_html_text(html_text)

        user_detail = re.search(r"userdetails.php\?id=(\d+)", html_text)
        if user_detail and user_detail.group().strip():
            self._user_detail_page = user_detail.group().strip().lstrip('/')
            self.userid = user_detail.group(1)

        self._torrent_seeding_page = f"viewusertorrents.php?id={self.userid}&show=seeding"


# ===== from parser/nexus_rabbit.py =====
class NexusRabbitSiteUserInfo(SiteParserBase):
    schema = SiteSchema.NexusRabbit

    def _parse_site_page(self, html_text: str):
        html_text = self._prepare_html_text(html_text)

        user_detail = re.search(r"user.php\?id=(\d+)", html_text)

        if not (user_detail and user_detail.group().strip()):
            return

        self.userid = user_detail.group(1)
        self._user_detail_page = f"user.php?id={self.userid}"

        self._user_traffic_page = None

        self._torrent_seeding_page = "api/general"
        self._torrent_seeding_params = {
            "page": 1,
            "limit": 5000000,
            "action": "userTorrentsList",
            "data": {"type": "seeding", "id": int(self.userid)},
        }
        self._torrent_seeding_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",  # 必须要加上这一条，不然返回的是空数据
        }

        self._user_mail_unread_page = None
        self._sys_mail_unread_page = "api/general"
        self._mail_unread_params = {
            "page": 1,
            "limit": 5000000,
            "action": "getMessageIn",
        }
        self._mail_unread_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
        }

    def _parse_user_torrent_seeding_info(
        self, html_text: str, multi_page: bool = False
    ) -> Optional[str]:
        """
        做种相关信息
        :param html_text:
        :param multi_page: 是否多页数据
        :return: 下页地址
        """

        try:
            torrents = json.loads(html_text).get("data", [])
        except Exception as e:
            logger.error(f"解析做种信息失败: {str(e)}")
            return None

        seeding_size = 0
        seeding_info = []

        for torrent in torrents:
            seeders = int(torrent.get("seeders", 0))
            size = StringUtils.num_filesize(torrent.get("size"))
            seeding_size += size
            seeding_info.append([seeders, size])

        self.seeding = len(torrents)
        self.seeding_size = seeding_size
        self.seeding_info = seeding_info

    def _parse_message_unread_links(
        self, html_text: str, msg_links: list
    ) -> str | None:
        unread_ids = []
        try:
            messages = json.loads(html_text).get("data", [])
        except Exception as e:
            logger.error(f"解析未读消息失败: {e}")
            return None
        for msg in messages:
            msg_id, msg_unread = msg.get("id"), msg.get("unread")
            if not (msg_id and msg_unread) or msg_unread == "no":
                continue
            unread_ids.append(msg_id)
            head, date, content = msg.get("subject"), msg.get("added"), msg.get("msg")
            if head and date and content:
                self.message_unread_contents.append((head, date, content))
        self.message_unread = len(unread_ids)
        if unread_ids:
            self._get_page_content(
                url=urljoin(self._base_url, "api/general?loading=true"),
                params={"action": "readMessage", "data": {"ids": unread_ids}},
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/plain, */*",
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
        return None

    def _parse_user_base_info(self, html_text: str):
        """只有奶糖余额才需要在 base 中获取，其它均可以在详情页拿到"""
        html = etree.HTML(html_text)
        try:
            if not StringUtils.is_valid_html_element(html):
                return
            bonus = html.xpath(
                '//div[contains(text(), "奶糖余额")]/following-sibling::div[1]/text()'
            )
            if bonus:
                self.bonus = StringUtils.str_float(bonus[0].strip())
        finally:
            if html is not None:
                del html

    def _parse_user_detail_info(self, html_text: str):
        html = etree.HTML(html_text)
        try:
            if not StringUtils.is_valid_html_element(html):
                return
            # 缩小一下查找范围，所有的信息都在这个 div 里
            user_info = html.xpath('//div[contains(@class, "layui-hares-user-info-right")]')
            if not user_info:
                return
            user_info = user_info[0]
            # 用户名
            if username := user_info.xpath(
                './/span[contains(text(), "用户名")]/a/span/text()'
            ):
                self.username = username[0].strip()
            # 等级
            if user_level := user_info.xpath('.//span[contains(text(), "等级")]/b/text()'):
                self.user_level = user_level[0].strip()
            # 加入日期
            if join_date := user_info.xpath('.//span[contains(text(), "注册日期")]/text()'):
                join_date = join_date[0].strip().split("\r")[0].removeprefix("注册日期：")
                self.join_at = StringUtils.unify_datetime_str(join_date)
            # 上传量
            if upload := user_info.xpath('.//span[contains(text(), "上传量")]/text()'):
                self.upload = StringUtils.num_filesize(
                    upload[0].strip().removeprefix("上传量：")
                )
            # 下载量
            if download := user_info.xpath('.//span[contains(text(), "下载量")]/text()'):
                self.download = StringUtils.num_filesize(
                    download[0].strip().removeprefix("下载量：")
                )
            # 分享率
            if ratio := user_info.xpath('.//span[contains(text(), "分享率")]/em/text()'):
                self.ratio = StringUtils.str_float(ratio[0].strip())
        finally:
            if html is not None:
                del html

    def _parse_message_content(self, html_text):
        """
        解析短消息内容，已经在 _parse_message_unread_links 内实现，重载防止 abstractmethod 报错
        :param html_text:
        :return:  head: message, date: time, content: message content
        """
        pass

    def _parse_user_traffic_info(self, html_text: str):
        """
        解析用户的上传，下载，分享率等信息，已经在 _parse_user_detail_info 内实现，重载防止 abstractmethod 报错
        :param html_text:
        :return:
        """
        pass


# ===== from parser/rousi.py =====
class RousiSiteUserInfo(SiteParserBase):
    """
    Rousi.pro 站点解析器
    使用 API v1 接口，通过 Passkey (Bearer Token) 进行认证
    """
    schema = SiteSchema.RousiPro
    request_mode = "apikey"

    def _parse_site_page(self, html_text: str):
        """
        配置 API 请求地址和请求头
        使用 API v1 的 /profile 接口获取用户信息
        """
        self._base_url = f"https://{StringUtils.get_url_domain(self._site_url)}"
        self._user_basic_page = "api/v1/profile?include_fields[user]=seeding_leeching_data"
        self._user_basic_params = {}
        self._user_basic_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {self.apikey}"
        }

        # Rousi.pro API v1 在单个接口返回所有信息，无需额外页面
        self._user_traffic_page = None
        self._user_detail_page = None
        self._torrent_seeding_page = None
        self._user_mail_unread_page = None
        self._sys_mail_unread_page = None

    def _parse_logged_in(self, html_text):
        """
        判断是否登录成功
        API 认证模式下，通过 HTTP 状态码判断，此处始终返回 True
        """
        return True

    def _parse_user_base_info(self, html_text: str):
        """
        解析用户基本信息
        通过 API v1 接口获取用户完整信息，包括上传下载量、做种数据等

        API 响应示例：
        {
            "code": 0,
            "message": "success",
            "data": {
                "id": 1,
                "username": "example",
                "level_text": "Lv.5",
                "registered_at": "2024-01-01T00:00:00Z",
                "uploaded": 1073741824,
                "downloaded": 536870912,
                "ratio": 2.0,
                "karma": 1000.5,
                "seeding_leeching_data": {
                    "seeding_count": 10,
                    "seeding_size": 10737418240,
                    "leeching_count": 2,
                    "leeching_size": 2147483648
                }
            }
        }
        """
        if not html_text:
            return

        try:
            data = json.loads(html_text)
        except json.JSONDecodeError:
            logger.error(f"{self._site_name} JSON 解析失败")
            return

        if not data or data.get("code") != 0:
            self.err_msg = data.get("message", "未知错误")
            logger.warn(f"{self._site_name} API 错误: {self.err_msg}")
            return

        user_info = data.get("data")
        if not user_info:
            return

        # 基本信息
        self.userid = user_info.get("id")
        self.username = user_info.get("username")
        self.user_level = user_info.get("level_text") or user_info.get("role_text")

        # 注册时间：统一格式为 YYYY-MM-DD HH:MM:SS
        join_at = StringUtils.unify_datetime_str(user_info.get("registered_at"))
        if join_at:
            # 确保格式为 YYYY-MM-DD HH:MM:SS (19位)
            if len(join_at) >= 19:
                self.join_at = join_at[:19]
            else:
                self.join_at = join_at

        # 流量信息
        self.upload = int(user_info.get("uploaded") or 0)
        self.download = int(user_info.get("downloaded") or 0)
        self.ratio = round(float(user_info.get("ratio") or 0), 2)

        # 魔力值（站点称为 karma）
        self.bonus = float(user_info.get("karma") or 0)

        # 做种/下载中数据
        sl_data = user_info.get("seeding_leeching_data", {})
        self.seeding = int(sl_data.get("seeding_count") or 0)
        self.seeding_size = int(sl_data.get("seeding_size") or 0)
        self.leeching = int(sl_data.get("leeching_count") or 0)
        self.leeching_size = int(sl_data.get("leeching_size") or 0)

    def _parse_user_traffic_info(self, html_text: str):
        """
        解析用户流量信息
        Rousi.pro API v1 在 _parse_user_base_info 中已完成所有解析，此方法无需实现
        """
        pass

    def _parse_user_detail_info(self, html_text: str):
        """
        解析用户详细信息
        Rousi.pro API v1 在 _parse_user_base_info 中已完成所有解析，此方法无需实现
        """
        pass

    def _parse_user_torrent_seeding_info(self, html_text: str, multi_page: Optional[bool] = False) -> Optional[str]:
        """
        解析用户做种信息
        Rousi.pro API v1 在 _parse_user_base_info 中已通过 seeding_leeching_data 获取做种数据

        :param html_text: 页面内容
        :param multi_page: 是否多页数据
        :return: 下页地址（无下页返回 None）
        """
        return None

    def _parse_message_unread_links(self, html_text: str, msg_links: list) -> Optional[str]:
        """
        解析未读消息链接
        Rousi.pro API v1 暂未提供消息相关接口

        :param html_text: 页面内容
        :param msg_links: 消息链接列表
        :return: 下页地址（无下页返回 None）
        """
        return None

    def _parse_message_content(self, html_text) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        解析消息内容
        Rousi.pro API v1 暂未提供消息相关接口

        :param html_text: 页面内容
        :return: (标题, 日期, 内容)
        """
        return None, None, None

    def _pase_unread_msgs(self):
        """
        解析所有未读消息标题和内容
        Rousi.pro API v1 暂未提供消息相关接口，暂时以网页接口实现
        
        :return:
        """
        if not self.token:
            logger.warn(f"{self._site_name} 站点未配置 Authorization 请求头，跳过消息解析")
            return
        
        headers = {
            "User-Agent": self._ua,
            "Accept": "application/json, text/plain, */*",
            "Authorization": self.token if self.token.startswith("Bearer ") else f"Bearer {self.token}"
        }
        
        def __get_message_list(page: int):
            params = {
                "page": page,
                "page_size": 100,
                "unread_only": "true"
            }
            res = RequestUtils(
                headers=headers,
                timeout=60,
                proxies=settings.PROXY if self._proxy else None
            ).get_res(
                url=urljoin(self._base_url, "api/messages"),
                params=params
            )
            if not res or res.status_code != 200 or res.json().get("code", -1) != 0:
                logger.warn(f"{self._site_name} 站点解析消息失败，状态码: {res.status_code if res else '无响应'}")
                return {
                    "messages": [],
                    "total_pages": 0
                }
            return res.json().get("data")
        
        # 分页获取所有未读消息
        page = 0
        res = __get_message_list(page)
        page += 1
        messages = res.get("messages", [])
        total_pages = res.get("total_pages", 0)
        while page < total_pages:
            res = __get_message_list(page)
            messages.extend(res.get("messages", []))
            page += 1
        
        self.message_unread = len(messages)
        for messsage in messages:
            head = messsage.get("title")
            date = StringUtils.unify_datetime_str(messsage.get("created_at"))
            content = messsage.get("content")
            logger.debug(f"{self._site_name} 标题 {head} 时间 {date} 内容 {content}")
            self.message_unread_contents.append((head, date, content))
            
        # 更新消息为已读
        RequestUtils(
            headers=headers,
            timeout=60,
            proxies=settings.PROXY if self._proxy else None
        ).post_res(
            url=urljoin(self._base_url, "api/messages/read-all")
        )


# ===== from parser/small_horse.py =====
class SmallHorseSiteUserInfo(SiteParserBase):
    schema = SiteSchema.SmallHorse

    def _parse_site_page(self, html_text: str):
        html_text = self._prepare_html_text(html_text)

        user_detail = re.search(r"user.php\?id=(\d+)", html_text)
        if user_detail and user_detail.group().strip():
            self._user_detail_page = user_detail.group().strip().lstrip('/')
            self.userid = user_detail.group(1)
            self._torrent_seeding_page = f"torrents.php?type=seeding&userid={self.userid}"
        self._user_traffic_page = f"user.php?id={self.userid}"

    def _parse_user_base_info(self, html_text: str):
        html_text = self._prepare_html_text(html_text)
        html = etree.HTML(html_text)
        try:
            ret = html.xpath('//a[contains(@href, "user.php")]//text()')
            if ret:
                self.username = str(ret[0])
        finally:
            if html is not None:
                del html

    def _parse_user_traffic_info(self, html_text: str):
        """
        上传/下载/分享率 [做种数/魔力值]
        :param html_text:
        :return:
        """
        html_text = self._prepare_html_text(html_text)
        html = etree.HTML(html_text)
        try:
            tmps = html.xpath('//ul[@class = "stats nobullet"]')
            if tmps:
                if tmps[1].xpath("li") and tmps[1].xpath("li")[0].xpath("span//text()"):
                    self.join_at = StringUtils.unify_datetime_str(tmps[1].xpath("li")[0].xpath("span//text()")[0])
                self.upload = StringUtils.num_filesize(str(tmps[1].xpath("li")[2].xpath("text()")[0]).split(":")[1].strip())
                self.download = StringUtils.num_filesize(
                    str(tmps[1].xpath("li")[3].xpath("text()")[0]).split(":")[1].strip())
                if tmps[1].xpath("li")[4].xpath("span//text()"):
                    self.ratio = StringUtils.str_float(str(tmps[1].xpath("li")[4].xpath("span//text()")[0]).replace('∞', '0'))
                else:
                    self.ratio = StringUtils.str_float(str(tmps[1].xpath("li")[5].xpath("text()")[0]).split(":")[1])
                self.bonus = StringUtils.str_float(str(tmps[1].xpath("li")[5].xpath("text()")[0]).split(":")[1])
                self.user_level = str(tmps[3].xpath("li")[0].xpath("text()")[0]).split(":")[1].strip()
                self.leeching = StringUtils.str_int(
                    (tmps[4].xpath("li")[6].xpath("text()")[0]).split(":")[1].replace("[", ""))
        finally:
            if html is not None:
                del html

    def _parse_user_detail_info(self, html_text: str):
        pass

    def _parse_user_torrent_seeding_info(self, html_text: str, multi_page: Optional[bool] = False) -> Optional[str]:
        """
         做种相关信息
         :param html_text:
         :param multi_page: 是否多页数据
         :return: 下页地址
         """
        html = etree.HTML(html_text)
        try:
            if not StringUtils.is_valid_html_element(html):
                return None

            size_col = 6
            seeders_col = 8

            page_seeding = 0
            page_seeding_size = 0
            page_seeding_info = []
            seeding_sizes = html.xpath(f'//table[@id="torrent_table"]//tr[position()>1]/td[{size_col}]')
            seeding_seeders = html.xpath(f'//table[@id="torrent_table"]//tr[position()>1]/td[{seeders_col}]')
            if seeding_sizes and seeding_seeders:
                page_seeding = len(seeding_sizes)

                for i in range(0, len(seeding_sizes)):
                    size = StringUtils.num_filesize(seeding_sizes[i].xpath("string(.)").strip())
                    seeders = StringUtils.str_int(seeding_seeders[i].xpath("string(.)").strip())

                    page_seeding_size += size
                    page_seeding_info.append([seeders, size])

            self.seeding += page_seeding
            self.seeding_size += page_seeding_size
            self.seeding_info.extend(page_seeding_info)

            # 是否存在下页数据
            next_page = None
            next_pages = html.xpath('//ul[@class="pagination"]/li[contains(@class,"active")]/following-sibling::li')
            if next_pages and len(next_pages) > 1:
                page_num = next_pages[0].xpath("string(.)").strip()
                if page_num.isdigit():
                    next_page = f"{self._torrent_seeding_page}&page={page_num}"
        finally:
            if html is not None:
                del html
        return next_page

    def _parse_message_unread_links(self, html_text: str, msg_links: list) -> Optional[str]:
        return None

    def _parse_message_content(self, html_text):
        return None, None, None


# ===== from parser/tnode.py =====
class TNodeSiteUserInfo(SiteParserBase):
    schema = SiteSchema.TNode

    def _parse_site_page(self, html_text: str):
        html_text = self._prepare_html_text(html_text)

        # <meta name="x-csrf-token" content="fd169876a7b4846f3a7a16fcd5cccf8d">
        csrf_token = re.search(r'<meta name="x-csrf-token" content="(.+?)">', html_text)
        if csrf_token:
            self._addition_headers = {'X-CSRF-TOKEN': csrf_token.group(1)}
            self._user_detail_page = "api/user/getMainInfo"
            self._torrent_seeding_page = "api/user/listTorrentActivity?id=&type=seeding&page=1&size=20000"

    def _parse_logged_in(self, html_text):
        """
        判断是否登录成功, 通过判断是否存在用户信息
        暂时跳过检测，待后续优化
        :param html_text:
        :return:
        """
        return True

    def _parse_user_base_info(self, html_text: str):
        self.username = self.userid

    def _parse_user_traffic_info(self, html_text: str):
        pass

    def _parse_user_detail_info(self, html_text: str):
        try:
            detail = json.loads(html_text)
        except json.JSONDecodeError:
            return
        if detail.get("status") != 200:
            return

        user_info = detail.get("data", {})
        self.userid = user_info.get("id")
        self.username = user_info.get("username")
        self.user_level = user_info.get("class", {}).get("name")
        self.join_at = user_info.get("regTime", 0)
        self.join_at = StringUtils.unify_datetime_str(str(self.join_at))

        self.upload = user_info.get("upload")
        self.download = user_info.get("download")
        self.ratio = 0 if self.download <= 0 else round(self.upload / self.download, 3)
        self.bonus = user_info.get("bonus")

        self.message_unread = user_info.get("unreadAdmin", 0) + user_info.get("unreadInbox", 0) + user_info.get(
            "unreadSystem", 0)
        pass

    def _parse_user_torrent_seeding_info(self, html_text: str, multi_page: Optional[bool] = False) -> Optional[str]:
        """
        解析用户做种信息
        """
        try:
            seeding_info = json.loads(html_text)
        except json.JSONDecodeError as e:
            logger.warning(f"{self._site_name}: Failed to decode seeding info JSON: {e}")
            return None

        if not isinstance(seeding_info, dict):
            logger.warning(f"{self._site_name}: Seeding info payload is not a dictionary")
            return None

        if seeding_info.get("status") != 200:
            return None

        torrents = seeding_info.get("data", {}).get("torrents", [])

        page_seeding_size = 0
        page_seeding_info = []
        for torrent in torrents:
            size = torrent.get("size", 0)
            seeders = torrent.get("seeding", 0)

            page_seeding_size += size
            page_seeding_info.append([seeders, size])

        self.seeding += len(torrents)
        self.seeding_size += page_seeding_size
        self.seeding_info.extend(page_seeding_info)

        # 是否存在下页数据
        next_page = None

        return next_page

    def _parse_message_unread_links(self, html_text: str, msg_links: list) -> Optional[str]:
        return None

    def _parse_message_content(self, html_text):
        """
        系统信息 api/message/listSystem?page=1&size=20
        收件箱信息 api/message/listInbox?page=1&size=20
        管理员信息 api/message/listAdmin?page=1&size=20
        :param html_text:
        :return:
        """
        return None, None, None


# ===== from parser/torrent_leech.py =====
class TorrentLeechSiteUserInfo(SiteParserBase):
    schema = SiteSchema.TorrentLeech

    def _parse_site_page(self, html_text: str):
        html_text = self._prepare_html_text(html_text)

        user_detail = re.search(r"/profile/([^/]+)/", html_text)
        if user_detail and user_detail.group().strip():
            self._user_detail_page = user_detail.group().strip().lstrip('/')
            self.userid = user_detail.group(1)
        self._user_traffic_page = f"profile/{self.userid}/view"
        self._torrent_seeding_page = f"profile/{self.userid}/seeding"

    def _parse_user_base_info(self, html_text: str):
        self.username = self.userid

    def _parse_user_traffic_info(self, html_text: str):
        """
        上传/下载/分享率 [做种数/魔力值]
        :param html_text:
        :return:
        """
        html_text = self._prepare_html_text(html_text)
        html = etree.HTML(html_text)
        try:
            upload_html = html.xpath('//div[contains(@class,"profile-uploaded")]//span/text()')
            if upload_html:
                self.upload = StringUtils.num_filesize(upload_html[0])
            download_html = html.xpath('//div[contains(@class,"profile-downloaded")]//span/text()')
            if download_html:
                self.download = StringUtils.num_filesize(download_html[0])
            ratio_html = html.xpath('//div[contains(@class,"profile-ratio")]//span/text()')
            if ratio_html:
                self.ratio = StringUtils.str_float(ratio_html[0].replace('∞', '0'))

            user_level_html = html.xpath('//table[contains(@class, "profileViewTable")]'
                                         '//tr/td[text()="Class"]/following-sibling::td/text()')
            if user_level_html:
                self.user_level = user_level_html[0].strip()

            join_at_html = html.xpath('//table[contains(@class, "profileViewTable")]'
                                      '//tr/td[text()="Registration date"]/following-sibling::td/text()')
            if join_at_html:
                self.join_at = StringUtils.unify_datetime_str(join_at_html[0].strip())

            bonus_html = html.xpath('//span[contains(@class, "total-TL-points")]/text()')
            if bonus_html:
                self.bonus = StringUtils.str_float(bonus_html[0].strip())
        finally:
            if html is not None:
                del html

    def _parse_user_detail_info(self, html_text: str):
        pass

    def _parse_user_torrent_seeding_info(self, html_text: str, multi_page: Optional[bool] = False) -> Optional[str]:
        """
        做种相关信息
        :param html_text:
        :param multi_page: 是否多页数据
        :return: 下页地址
        """
        html = etree.HTML(html_text)
        try:
            if not StringUtils.is_valid_html_element(html):
                return None

            size_col = 2
            seeders_col = 7

            page_seeding = 0
            page_seeding_size = 0
            page_seeding_info = []
            seeding_sizes = html.xpath(f'//tbody/tr/td[{size_col}]')
            seeding_seeders = html.xpath(f'//tbody/tr/td[{seeders_col}]/text()')
            if seeding_sizes and seeding_seeders:
                page_seeding = len(seeding_sizes)

                for i in range(0, len(seeding_sizes)):
                    size = StringUtils.num_filesize(seeding_sizes[i].xpath("string(.)").strip())
                    seeders = StringUtils.str_int(seeding_seeders[i])

                    page_seeding_size += size
                    page_seeding_info.append([seeders, size])

            self.seeding += page_seeding
            self.seeding_size += page_seeding_size
            self.seeding_info.extend(page_seeding_info)

            # 是否存在下页数据
            next_page = None
        finally:
            if html is not None:
                del html

        return next_page

    def _parse_message_unread_links(self, html_text: str, msg_links: list) -> Optional[str]:
        return None

    def _parse_message_content(self, html_text):
        return None, None, None


# ===== from parser/unit3d.py =====
class Unit3dSiteUserInfo(SiteParserBase):
    schema = SiteSchema.Unit3d

    def _parse_user_base_info(self, html_text: str):
        html_text = self._prepare_html_text(html_text)
        html = etree.HTML(html_text)
        try:
            tmps = html.xpath('//a[contains(@href, "/users/") and contains(@href, "settings")]/@href')
            if tmps:
                user_name_match = re.search(r"/users/(.+)/settings", tmps[0])
                if user_name_match and user_name_match.group().strip():
                    self.username = user_name_match.group(1)
                    self._torrent_seeding_page = f"/users/{self.username}/active?perPage=100&client=&seeding=include"
                    self._user_detail_page = f"/users/{self.username}"

            tmps = html.xpath('//a[contains(@href, "bonus/earnings")]')
            if tmps:
                bonus_text = tmps[0].xpath("string(.)")
                bonus_match = re.search(r"([\d,.]+)", bonus_text)
                if bonus_match and bonus_match.group(1).strip():
                    self.bonus = StringUtils.str_float(bonus_match.group(1))
        finally:
            if html is not None:
                del html

    def _parse_site_page(self, html_text: str):
        pass

    def _parse_user_detail_info(self, html_text: str):
        """
        解析用户额外信息，加入时间，等级
        :param html_text:
        :return:
        """
        html = etree.HTML(html_text)
        try:
            if not StringUtils.is_valid_html_element(html):
                return None

            # 用户等级
            user_levels_text = html.xpath('//div[contains(@class, "content")]//span[contains(@class, "badge-user")]/text()')
            if user_levels_text:
                self.user_level = user_levels_text[0].strip()

            # 加入日期
            join_at_text = html.xpath('//div[contains(@class, "content")]//h4[contains(text(), "注册日期") '
                                      'or contains(text(), "註冊日期") '
                                      'or contains(text(), "Registration date")]/text()')
            if join_at_text:
                self.join_at = StringUtils.unify_datetime_str(
                    join_at_text[0].replace('注册日期', '').replace('註冊日期', '').replace('Registration date', ''))
        finally:
            if html is not None:
                del html

    def _parse_user_torrent_seeding_info(self, html_text: str, multi_page: Optional[bool] = False) -> Optional[str]:
        """
        做种相关信息
        :param html_text:
        :param multi_page: 是否多页数据
        :return: 下页地址
        """
        html = etree.HTML(html_text)
        try:
            if not StringUtils.is_valid_html_element(html):
                return None

            size_col = 9
            seeders_col = 2
            # 搜索size列
            if html.xpath('//thead//th[contains(@class,"size")]'):
                size_col = len(html.xpath('//thead//th[contains(@class,"size")][1]/preceding-sibling::th')) + 1
            # 搜索seeders列
            if html.xpath('//thead//th[contains(@class,"seeders")]'):
                seeders_col = len(html.xpath('//thead//th[contains(@class,"seeders")]/preceding-sibling::th')) + 1

            page_seeding = 0
            page_seeding_size = 0
            page_seeding_info = []
            seeding_sizes = html.xpath(f'//tr[position()]/td[{size_col}]')
            seeding_seeders = html.xpath(f'//tr[position()]/td[{seeders_col}]')
            if seeding_sizes and seeding_seeders:
                page_seeding = len(seeding_sizes)

                for i in range(0, len(seeding_sizes)):
                    size = StringUtils.num_filesize(seeding_sizes[i].xpath("string(.)").strip())
                    seeders = StringUtils.str_int(seeding_seeders[i].xpath("string(.)").strip())

                    page_seeding_size += size
                    page_seeding_info.append([seeders, size])

            self.seeding += page_seeding
            self.seeding_size += page_seeding_size
            self.seeding_info.extend(page_seeding_info)

            # 是否存在下页数据
            next_page = None
            next_pages = html.xpath('//ul[@class="pagination"]/li[contains(@class,"active")]/following-sibling::li')
            if next_pages and len(next_pages) > 1:
                page_num = next_pages[0].xpath("string(.)").strip()
                if page_num.isdigit():
                    next_page = f"{self._torrent_seeding_page}&page={page_num}"
        finally:
            if html is not None:
                del html

        return next_page

    def _parse_user_traffic_info(self, html_text: str):
        html_text = self._prepare_html_text(html_text)
        upload_match = re.search(r"[^总]上[传傳]量?[:：_<>/a-zA-Z-=\"'\s#;]+([\d,.\s]+[KMGTPI]*B)", html_text,
                                 re.IGNORECASE)
        self.upload = StringUtils.num_filesize(upload_match.group(1).strip()) if upload_match else 0
        download_match = re.search(r"[^总子影力]下[载載]量?[:：_<>/a-zA-Z-=\"'\s#;]+([\d,.\s]+[KMGTPI]*B)", html_text,
                                   re.IGNORECASE)
        self.download = StringUtils.num_filesize(download_match.group(1).strip()) if download_match else 0
        ratio_match = re.search(r"分享率[:：_<>/a-zA-Z-=\"'\s#;]+([\d,.\s]+)", html_text)
        self.ratio = StringUtils.str_float(ratio_match.group(1)) if (
                ratio_match and ratio_match.group(1).strip()) else 0.0

    def _parse_message_unread_links(self, html_text: str, msg_links: list) -> Optional[str]:
        return None

    def _parse_message_content(self, html_text):
        return None, None, None


# ===== from parser/yema.py =====
class TYemaSiteUserInfo(SiteParserBase):
    schema = SiteSchema.Yema

    def _parse_site_page(self, html_text: str):
        """
        获取站点页面地址
        """
        self._user_traffic_page = None
        self._user_detail_page = None
        self._user_basic_page = "api/consumer/fetchSelfDetail"
        self._user_basic_params = {}
        self._sys_mail_unread_page = None
        self._user_mail_unread_page = None
        self._mail_unread_params = {}
        self._torrent_seeding_page = "/api/userTorrent/fetchSeedTorrentInfo"
        self._torrent_seeding_params = {
            # 虽然这个参数是无意义的，但这个 API 必须用 POST
            "status": "seeding"
        }
        self._torrent_seeding_headers = {}
        self._addition_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
        }

    def _parse_logged_in(self, html_text):
        """
        判断是否登录成功, 通过判断是否存在用户信息
        暂时跳过检测，待后续优化
        :param html_text:
        :return:
        """
        return True

    def _parse_user_base_info(self, html_text: str):
        """
        解析用户基本信息，这里把_parse_user_traffic_info和_parse_user_detail_info合并到这里
        """
        if not html_text:
            return None
        detail = json.loads(html_text)
        if not detail or not detail.get("success"):
            return
        user_info = detail.get("data", {})
        self.userid = user_info.get("id")
        self.username = user_info.get("name")
        self.user_level = str(user_info.get("level")) if user_info.get("level") is not None else None
        self.join_at = StringUtils.unify_datetime_str(user_info.get("registerTime"))

        self.upload = user_info.get('uploadSize')
        # 使用 promotionDownloadSize 获取真实下载量（考虑促销因素）
        if "promotionDownloadSize" in user_info:
            self.download = user_info.get('promotionDownloadSize')
        else:
            self.download = user_info.get('downloadSize')
        self.ratio = round(self.upload / (self.download or 1), 2)
        self.bonus = user_info.get("bonus")
        self.message_unread = 0

    def _parse_user_traffic_info(self, html_text: str):
        """
        解析用户流量信息
        """
        pass

    def _parse_user_detail_info(self, html_text: str):
        """
        解析用户详细信息
        """
        pass

    def _parse_user_torrent_seeding_info(self, html_text: str, multi_page: Optional[bool] = False) -> Optional[str]:
        """
        解析用户做种信息
        """
        if not html_text:
            return None
        seeding_info = json.loads(html_text)
        if not seeding_info or not seeding_info.get("success") or not seeding_info.get("data"):
            return None

        torrents = seeding_info.get("data")

        self.seeding += torrents.get("num")
        self.seeding_size += torrents.get("fileSize")

        # 是否存在下页数据
        next_page = None

        return next_page

    def _parse_message_unread_links(self, html_text: str, msg_links: list) -> Optional[str]:
        """
        解析未读消息链接，这里直接读出详情
        """
        pass

    def _parse_message_content(self, html_text) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        解析消息内容
        """
        pass


# ===== from parser/zhixing.py =====
#
# 知行 http://pt.zhixing.bjtu.edu.cn/
# author: ThedoRap
# time: 2025-10-02
#

from bs4 import BeautifulSoup


class ZhixingSiteUserInfo(SiteParserBase):
    schema = SiteSchema.Zhixing

    def _parse_site_page(self, html_text: str):
        """
        获取站点页面地址
        """
        self._user_basic_page = "user/{uid}/"
        self._user_detail_page = None
        self._user_basic_params = {}
        self._user_traffic_page = None
        self._sys_mail_unread_page = None
        self._user_mail_unread_page = None
        self._mail_unread_params = {}
        self._torrent_seeding_base = "user/{uid}/seeding"
        self._torrent_seeding_params = {}
        self._torrent_seeding_headers = {}
        self._addition_headers = {}

    def _parse_logged_in(self, html_text):
        """
        判断是否登录成功, 通过判断是否存在用户信息
        """
        soup = BeautifulSoup(html_text, 'html.parser')
        return bool(soup.find(id='um'))

    def _parse_user_base_info(self, html_text: str):
        """
        解析用户基本信息，这里把_parse_user_traffic_info和_parse_user_detail_info合并到这里
        """
        if not html_text:
            return None
        soup = BeautifulSoup(html_text, 'html.parser')
        details_tabs = soup.find_all('div', class_='user-details-tabs')
        info_dict = {}
        for tab in details_tabs:
            for p in tab.find_all('p'):
                text = p.text.strip()
                if '：' in text:
                    parts = text.split('：', 1)
                elif ':' in text:
                    parts = text.split(':', 1)
                else:
                    continue
                if len(parts) == 2:
                    key = parts[0].strip()
                    value_text = parts[1].strip()
                    value = re.split(r'\s*\(', value_text)[0].strip().split('查看')[0].strip()
                    info_dict[key] = value

        self._basic_info = info_dict  # Save for fallback

        self.userid = info_dict.get('UID')
        self.username = info_dict.get('用户名')
        self.user_level = info_dict.get('用户组')
        self.join_at = StringUtils.unify_datetime_str(info_dict.get('注册时间')) if '注册时间' in info_dict else None

        def num_filesize_safe(s: str):
            if s:
                s = s.strip()
                if re.match(r'^\d+(\.\d+)?$', s):
                    s += ' B'
            return StringUtils.num_filesize(s) if s else 0

        self.upload = num_filesize_safe(info_dict.get('上传流量')) if '上传流量' in info_dict else 0
        self.download = num_filesize_safe(info_dict.get('下载流量')) if '下载流量' in info_dict else 0
        self.ratio = float(info_dict.get('共享率')) if '共享率' in info_dict else 0
        self.bonus = float(info_dict.get('保种积分')) if '保种积分' in info_dict else 0.0
        self.message_unread = 0  # 暂无消息解析

        # Temporarily set seeding from basic, will override or fallback later
        self.seeding = int(info_dict.get('当前保种数量')) if '当前保种数量' in info_dict else 0
        self.seeding_size = num_filesize_safe(info_dict.get('当前保种容量')) if '当前保种容量' in info_dict else 0

    def _parse_user_traffic_info(self, html_text: str):
        pass

    def _parse_user_detail_info(self, html_text: str):
        pass

    def _parse_user_torrent_seeding_page_info(self, html_text: str) -> Tuple[int, int]:
        """
        解析用户做种信息单页，返回本页数量和大小
        """
        if not html_text:
            return 0, 0
        soup = BeautifulSoup(html_text, 'html.parser')
        torrents = soup.find_all('tr', id=re.compile(r'^t\d+'))
        page_seeding = 0
        page_seeding_size = 0
        for torrent in torrents:
            size_td = torrent.find('td', class_='r')
            if size_td:
                size_text = size_td.find('a').text if size_td.find('a') else size_td.text.strip()
                page_seeding += 1
                page_seeding_size += StringUtils.num_filesize(size_text)
        return page_seeding, page_seeding_size

    def _parse_message_unread_links(self, html_text: str, msg_links: list) -> Optional[str]:
        pass

    def _parse_message_content(self, html_text) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        pass

    def _parse_user_torrent_seeding_info(self, html_text: str, multi_page: bool = False):
        """
        占位，避免抽象类报错
        """
        pass

    def parse(self):
        """
        解析站点信息
        """
        super().parse()
        # 先从首页解析userid
        if self._index_html:
            soup = BeautifulSoup(self._index_html, 'html.parser')
            user_link = soup.find('a', href=re.compile(r'/user/\d+/'))
            if user_link:
                uid_match = re.search(r'/user/(\d+)/', user_link['href'])
                if uid_match:
                    self.userid = uid_match.group(1)
        # 如果有userid，则格式化页面
        if self.userid:
            if self._user_basic_page:
                basic_url = self._user_basic_page.format(uid=self.userid)
                basic_html = self._get_page_content(url=urljoin(self._base_url, basic_url))
                self._parse_user_base_info(basic_html)
            if hasattr(self, '_torrent_seeding_base') and self._torrent_seeding_base:
                self.seeding = 0  # Reset to sum from pages
                self.seeding_size = 0
                seeding_base = self._torrent_seeding_base.format(uid=self.userid)
                seeding_base_url = urljoin(self._base_url, seeding_base)
                page_num = 1
                while True:
                    seeding_url = f"{seeding_base_url}/p{page_num}"
                    seeding_html = self._get_page_content(url=seeding_url)
                    page_seeding, page_seeding_size = self._parse_user_torrent_seeding_page_info(seeding_html)
                    self.seeding += page_seeding
                    self.seeding_size += page_seeding_size
                    if page_seeding == 0:
                        break
                    page_num += 1
                # Fallback to basic if no seeding found from pages
                if self.seeding == 0 and hasattr(self, '_basic_info'):
                    def num_filesize_safe(s: str):
                        if s:
                            s = s.strip()
                            if re.match(r'^\d+(\.\d+)?$', s):
                                s += ' B'
                        return StringUtils.num_filesize(s) if s else 0
                    self.seeding = int(self._basic_info.get('当前保种数量', 0))
                    self.seeding_size = num_filesize_safe(self._basic_info.get('当前保种容量', ''))

        # 🔑 最终对外统一转字符串，避免 join 报错
        self.userid = str(self.userid or "")
        self.username = str(self.username or "")
        self.user_level = str(self.user_level or "")
        self.join_at = str(self.join_at or "")

        self.upload = str(self.upload or 0)
        self.download = str(self.download or 0)
        self.ratio = str(self.ratio or 0)
        self.bonus = str(self.bonus or 0.0)
        self.message_unread = str(self.message_unread or 0)

        self.seeding = str(self.seeding or 0)
        self.seeding_size = str(self.seeding_size or 0)

