# -*- coding: utf-8 -*-

import re
from urllib.parse import quote, urljoin

from lxml import etree
import config
from .httprequest import request_session
from .parser import Parser


# javlibrary 主站默认域名（镜像失效时可在 config.ini 的 [javlibrary] domain 修改）
_DEFAULT_DOMAIN = "https://www.javlibrary.com"


def _get_domain() -> str:
    """读取 javlibrary 域名；配置缺失/异常时回退默认主站。"""
    try:
        return config.getInstance().javlibrary_domain().rstrip("/")
    except Exception:
        return _DEFAULT_DOMAIN


# 详情页字段 XPath：新结构与旧结构并存，使用时按顺序取第一个命中值
_EXPR_NUMBER = (
    '//div[@id="video_id"]//td[@class="text"]/text()',
    '//div[@id="video_id"]/table/tr/td[@class="text"]/text()',
)
_EXPR_TITLE = (
    '//h3[contains(@class,"post-title")]/a/text()',
    '//div[@id="video_title"]/h3/a/text()',
)
_EXPR_ACTOR = (
    '//span[@class="cast"]/span[@class="star"]/a/text()',
    '//div[@id="video_cast"]//td[@class="text"]/span/span[@class="star"]/a/text()',
)
_EXPR_TAGS = (
    '//div[@id="video_genres"]//td[@class="text"]/span[@class="genre"]/a/text()',
    '//div[@id="video_genres"]//td[@class="text"]/span/a/text()',
)
_EXPR_COVER = ('//img[@id="video_jacket_img"]/@src',)
_EXPR_RELEASE = (
    '//div[@id="video_date"]//td[@class="text"]/text()',
    '//div[@id="video_date"]/table/tr/td[@class="text"]/text()',
)
_EXPR_STUDIO = (
    '//div[@id="video_maker"]//td[@class="text"]//a/text()',
    '//div[@id="video_maker"]/table/tr/td[@class="text"]/span/a/text()',
)
_EXPR_LABEL = ('//div[@id="video_label"]//td[@class="text"]//a/text()',)
_EXPR_RUNTIME = ('//div[@id="video_length"]//td/span[@class="text"]/text()',)
_EXPR_SCORE = (
    '//div[@id="video_review"]//span[@class="score"]/text()',
    '//div[@id="video_review"]/table/tr/td/span[@class="score"]/text()',
)
_EXPR_DIRECTOR = (
    '//div[@id="video_director"]//td[@class="text"]//a/text()',
    '//div[@id="video_director"]/table/tr/td[@class="text"]/span/a/text()',
)
_EXPR_CANONICAL = ('//link[@rel="canonical"]/@href',)


class Javlibrary(Parser):
    source = 'javlibrary'

    # 供 Parser.dictformat / getUncensored 使用；对应 get* 方法会再按新旧结构依次尝试
    expr_number = _EXPR_NUMBER[0]
    expr_title = _EXPR_TITLE[0]
    expr_actor = _EXPR_ACTOR[0]
    expr_tags = _EXPR_TAGS[0]
    expr_cover = _EXPR_COVER[0]
    expr_release = _EXPR_RELEASE[0]
    expr_studio = _EXPR_STUDIO[0]
    expr_runtime = _EXPR_RUNTIME[0]
    expr_userrating = _EXPR_SCORE[0]
    expr_director = _EXPR_DIRECTOR[0]
    # 实时页面中样图 <img> 嵌在 <a> 内，必须用 // 取后代
    expr_extrafanart = '//div[@class="previewthumbs"]//img/@src'

    # 影片详情页链接特征："?v=javxxxx"（主站）或 "/javxxxx.html"（CDN/镜像）
    _re_detail_href = re.compile(r'(?:[?&]v=jav|/jav[a-z0-9]+\.html)', re.I)

    def extraInit(self):
        self.htmltree = None

    def updateCore(self, core):
        if not core:
            return
        if core.proxies:
            self.proxies = core.proxies
        if core.verify:
            self.verify = core.verify
        if core.morestoryline:
            self.morestoryline = True
        if core.specifiedSource == self.source:
            self.specifiedUrl = core.specifiedUrl
        self.cookies = {'over18': '1'}

    def search(self, number):
        self.number = number.upper()
        self.session = request_session(cookies=self.cookies, proxies=self.proxies, verify=self.verify)
        if self.specifiedUrl:
            self.detailurl = self.specifiedUrl
        else:
            self.detailurl = self.queryNumberUrl(self.number)
        if not self.detailurl:
            return 404
        if self.htmltree is None:
            details = self.session.get(self.detailurl)
            if details is None or not (details.text or '').strip():
                return 404
            self.htmltree = etree.fromstring(details.text, etree.HTMLParser())
        # 详情页必有 video_id 区块；没有则说明是拦截页 / 404 / 其它提示页
        if not self.htmltree.xpath('//div[@id="video_id"]'):
            return 404
        result = self.dictformat(self.htmltree)
        return result

    def queryNumberUrl(self, number: str):
        domain = _get_domain()
        queryUrl = domain + "/cn/vl_searchbyid.php?keyword=" + quote(number, safe="")
        queryResult = self.session.get(queryUrl)
        if queryResult is None or not (queryResult.text or "").strip():
            return None

        queryTree = etree.fromstring(queryResult.text, etree.HTMLParser())

        # ① 唯一命中：站点会把检索请求跳转到影片详情页（CF 服务也会跟随跳转）。
        #    详情页地址形式随域名/镜像而变（"?v=javxxxx" 或 "javxxxx.html"），
        #    因此按页面结构判断，并复用已下载的 HTML，省掉一次请求。
        if queryTree.xpath('//div[@id="video_id"]'):
            self.htmltree = queryTree
            canonical = self._first(queryTree, _EXPR_CANONICAL)
            if canonical:
                detailurl = urljoin(queryResult.url or queryUrl, canonical)
            else:
                detailurl = queryResult.url or queryUrl
            detail_number = self._first(queryTree, _EXPR_NUMBER)
            if not self._same_number(detail_number, number) and config.getInstance().debug():
                # 相近番号也会被跳转到详情页；是否采用交由上层 scraper 的番号校验决定
                print(f'[!]Javlibrary: search {number} -> page {detail_number}')
            return detailurl

        target = number.strip().upper()

        # ② 结果列表（当前结构）：每个条目只有一个带 href 的链接，按标题里的番号匹配。
        #    链接相对检索页的 /cn/ 目录，必须按检索 URL 解析，按站点根解析会 404。
        fallback = None
        for item in queryTree.xpath('//div[@class="video"]'):
            hrefs = item.xpath('.//a[@href]/@href')
            if not hrefs or not hrefs[0].strip():
                continue
            full_url = urljoin(queryUrl, hrefs[0].strip())
            if fallback is None:
                fallback = full_url
            titles = item.xpath('.//a[@href]/@title')
            if titles and self._title_matches(titles[0], target):
                return full_url

        # ③ 结果列表（旧结构）兜底：//div[@class="id"] 内是番号
        numbers = [n.strip().upper() for n in queryTree.xpath('//div[@class="id"]/text()')]
        if target in numbers:
            hrefs = queryTree.xpath('//div[@class="id"]/../@href')
            idx = numbers.index(target)
            if idx < len(hrefs) and hrefs[idx].strip():
                return urljoin(queryUrl, hrefs[idx].strip())

        # ④ 单条命中的跳转页：页面内只有一个影片链接时直接跟随
        detail_hrefs = [h for h in queryTree.xpath('//a/@href') if self._re_detail_href.search(h)]
        if len(detail_hrefs) == 1:
            return urljoin(queryUrl, detail_hrefs[0].strip())

        return fallback

    @staticmethod
    def _fold_number(number: str) -> str:
        """番号比较键：忽略大小写、短横线与空白（下划线视为不同番号的连接符，保留）。"""
        return (number or '').strip().casefold().replace('-', '').replace(' ', '')

    @classmethod
    def _same_number(cls, left: str, right: str) -> bool:
        return bool(left) and bool(right) and cls._fold_number(left) == cls._fold_number(right)

    @staticmethod
    def _title_matches(title, target: str) -> bool:
        """条目标题是否对应该番号；尾部加负向断言，避免 SPSF-8 误命中 SPSF-81。"""
        if not title:
            return False
        return re.search(re.escape(target) + r'(?![0-9A-Z])', title.upper()) is not None

    def _first(self, tree, exprs):
        """按顺序尝试多个 XPath，返回第一个非空字符串值。"""
        for expr in exprs:
            value = self.getTreeElement(tree, expr)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ''

    def _all(self, tree, exprs) -> list:
        """按顺序尝试多个 XPath，返回第一个非空的结果列表。"""
        for expr in exprs:
            values = [v.strip() for v in self.getTreeAll(tree, expr) if isinstance(v, str) and v.strip()]
            if values:
                return values
        return []

    def getNum(self, htmltree):
        return self._first(htmltree, _EXPR_NUMBER)

    def getTitle(self, htmltree):
        title = self._first(htmltree, _EXPR_TITLE)
        number = self.getNum(htmltree)
        # 标题通常以番号开头，去掉番号部分
        if number and title.upper().startswith(number.upper()):
            title = title[len(number):]
        return title.strip()

    def getActors(self, htmltree) -> list:
        return self._all(htmltree, _EXPR_ACTOR)

    def getTags(self, htmltree) -> list:
        return self._all(htmltree, _EXPR_TAGS)

    def getStudio(self, htmltree):
        return self._first(htmltree, _EXPR_STUDIO)

    def getLabel(self, htmltree):
        return self._first(htmltree, _EXPR_LABEL)

    def getDirector(self, htmltree):
        return self._first(htmltree, _EXPR_DIRECTOR)

    def getRelease(self, htmltree):
        return self._first(htmltree, _EXPR_RELEASE).replace('/', '-')

    def getRuntime(self, htmltree):
        """页面只给"120 分钟"这类文本，取出纯数字。"""
        match = re.search(r'\d+', self._first(htmltree, _EXPR_RUNTIME))
        return match.group() if match else ''

    def getUserRating(self, htmltree):
        """评分为 "(4.5)" 形式，取出括号内数值。"""
        score = self._first(htmltree, _EXPR_SCORE)
        nums = re.findall(r'[0-9.]+', score)
        if len(nums) == 1:
            return float(nums[0])
        return ''

    def getCover(self, htmltree):
        url = self._first(htmltree, _EXPR_COVER)
        if not url:
            return ''
        if url.startswith('//'):
            return 'https:' + url
        if not url.startswith('http'):
            return urljoin(_get_domain() + '/', url)
        return url

    def getOutline(self, htmltree):
        if self.morestoryline:
            from .storyline import getStoryline
            return getStoryline(self.number, self.getUncensored(htmltree),
                                proxies=self.proxies, verify=self.verify)
        return ''
