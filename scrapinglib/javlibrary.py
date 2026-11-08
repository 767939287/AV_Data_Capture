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


class Javlibrary(Parser):
    source = ' javlib.com'

    expr_number = '//div[@id="video_id"]/table/tr/td[@class="text"]/text()'
    expr_title = '//div[@id="video_title"]/h3/a/text()'
    expr_actor = '//div[@id="video_cast"]/table/tr/td[@class="text"]/span/span[@class="star"]/a/text()'
    expr_tags = '//div[@id="video_genres"]/table/tr/td[@class="text"]/span/a/text()'
    expr_cover = '//img[@id="video_jacket_img"]/@src'
    expr_release = '//div[@id="video_date"]/table/tr/td[@class="text"]/text()'
    expr_studio = '//div[@id="video_maker"]/table/tr/td[@class="text"]/span/a/text()'
    expr_runtime = '//div[@id="video_length"]/table/tr/td/span[@class="text"]/text()'
    expr_userrating = '//div[@id="video_review"]/table/tr/td/span[@class="score"]/text()'
    expr_director = '//div[@id="video_director"]/table/tr/td[@class="text"]/span/a/text()'
    expr_extrafanart = '//div[@class="previewthumbs"]/img/@src'

    # 影片详情页链接特征："?v=javxxxx"（主站）或 "/javxxxx.html"（CDN/镜像）
    _re_detail_href = re.compile(r'(?:[?&]v=jav|/jav[a-z0-9]+\.html)', re.I)

    def extraInit(self):
        self.htmltree = None

    def updateCore(self, core):
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
            deatils = self.session.get(self.detailurl)
            self.htmltree = etree.fromstring(deatils.text, etree.HTMLParser())
        result = self.dictformat(self.htmltree)
        return result

    def queryNumberUrl(self, number: str):
        domain = _get_domain()
        base = domain + "/tw/"
        queryUrl = base + "vl_searchbyid.php?keyword=" + quote(number, safe="")
        queryResult = self.session.get(queryUrl)
        if not queryResult or not (queryResult.text or "").strip():
            return None

        queryTree = etree.fromstring(queryResult.text, etree.HTMLParser())

        # 1) 单条命中：站点会跳转到影片详情页。不同域名/镜像的详情页 URL 形式不同
        #    （"?v=javxxxx" 或 "javxxxx.html"），因此用页面结构判断，
        #    命中后直接复用已下载的页面，避免重复请求。
        if queryTree.xpath('//div[@id="video_id"]'):
            self.htmltree = queryTree
            return queryResult.url

        # 2) 结果列表页：按番号匹配列表项（忽略大小写与空白）
        numbers = [n.strip().upper() for n in queryTree.xpath('//div[@class="id"]/text()')]
        target = number.strip().upper()
        if target in numbers:
            urls = queryTree.xpath('//div[@class="id"]/../@href')
            idx = numbers.index(target)
            if idx < len(urls):
                href = urls[idx].strip()
                if href:
                    return urljoin(base, href)

        # 3) 单条命中的跳转页：页面内只有一个影片链接时直接跟随
        detail_hrefs = [h for h in queryTree.xpath('//a/@href') if self._re_detail_href.search(h)]
        if len(detail_hrefs) == 1:
            return urljoin(base, detail_hrefs[0].strip())

        return None

    def getTitle(self, htmltree):
        title = super().getTitle(htmltree)
        title = title.replace(self.getNum(htmltree), '').strip()
        return title

    def getCover(self, htmltree):
        url = super().getCover(htmltree)
        if not url.startswith('http'):
            url = 'https:' + url
        return url

    def getOutline(self, htmltree):
        if self.morestoryline:
            from .storyline import getStoryline
            return getStoryline(self.number, self.getUncensored(htmltree),
                                proxies=self.proxies, verify=self.verify)
        return ''
