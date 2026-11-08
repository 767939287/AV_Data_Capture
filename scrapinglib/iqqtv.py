# -*- coding: utf-8 -*-

import json
import re
from urllib.parse import quote, urljoin

from lxml import etree

from .parser import Parser

# 排除马赛克破坏版等无有效元数据的条目
_EXCLUDED_KEYWORDS = ('克破', '无码破解', '無碼破解', '无码流出', '無碼流出')


def fold_number(value):
    """忽略大小写与分隔符的番号形态，用于宽松比对。"""
    return re.sub(r'[^0-9a-z]', '', (value or '').casefold())


class Iqqtv(Parser):
    """IQQTV 刮削器（iqqah.com）。

    站点域名从 iqq5.xyz 301 到 iqqah.com，这里直接用落地域名，避免依赖跳转。
    检索页 ``/cn/search.php?kw=<番号>`` 的条目标题在 ``span.title > a`` 的 ``title`` 属性里；
    详情页为 ``/cn/player.php?uuid=...``，标题在 ``h1``，演员 / 片商 / 标签分别用
    ``s_type=actor`` / ``s_type=fac`` / ``s_type=tag`` 的链接定位。
    """

    source = 'iqqtv'
    base_url = 'https://iqqah.com'
    lang_prefix = '/cn'

    def extraInit(self):
        self.imagecut = 1

    def search(self, number):
        self.number = number
        if self.specifiedUrl:
            return self._scrape(self.specifiedUrl)
        # n 开头的素人番号站内保持小写，其余统一大写
        term = number if re.match(r'n\d{4}', number) else number.upper()
        url = self._search_term(term, number)
        if not url:
            return 404
        return self._scrape(url)

    def _search_term(self, term, number):
        search_url = f'{self.base_url}{self.lang_prefix}/search.php?kw={quote(term)}'
        html = self.getHtml(search_url)
        if html == 404:
            return None
        tree = etree.fromstring(html, etree.HTMLParser())
        target = fold_number(number)
        for item in tree.xpath('//span[@class="title"]/a'):
            href = (item.get('href') or '').strip()
            title = (item.get('title') or '').strip()
            if not href or any(kw in title for kw in _EXCLUDED_KEYWORDS):
                continue
            if any(fold_number(token) == target for token in title.split()):
                path = re.sub(r'^/(cn|jp)/', '', href).lstrip('/')
                return urljoin(self.base_url, f'{self.lang_prefix}/{path}')
        return None

    def _scrape(self, url):
        html = self.getHtml(url)
        if html == 404:
            return 404
        tree = etree.fromstring(html, etree.HTMLParser())

        heading = ''.join(tree.xpath('//h1//text()')).strip()
        if not heading or '克破' in heading:
            return 404

        number = self._extract_number(heading)
        title = self._clean_title(heading)

        actors = [x.strip() for x in tree.xpath('//a[contains(@href,"s_type=actor")]/span/text()') if x.strip()]
        makers = [re.sub(r'\s+', ' ', x).strip() for x in tree.xpath('//a[contains(@href,"s_type=fac")]/text()')]
        studio = next((x for x in makers if x), '')
        release = ''.join(tree.xpath('//div[class="date"]/text()')).strip()
        if not release:
            release = ''.join(tree.xpath('//div[contains(@class,"date")]/text()')).strip()
        release = release.replace('/', '-')
        tags = list(dict.fromkeys(x.strip() for x in tree.xpath('//a[contains(@href,"s_type=tag")]/text()') if x.strip()))
        series = ''.join(tree.xpath('//a[contains(@href,"s_type=series")]/text()')).strip()
        cover = ''.join(tree.xpath('//meta[@property="og:image"]/@content')).strip()
        plot = ''.join(tree.xpath('//div[contains(@class,"intro")]//p//text()')).strip()
        # 简介段以「简介：」开头，标签不属于正文
        plot = re.sub(r'^(简介|簡介|介绍|介紹|Introduction)\s*[:：]\s*', '', plot).strip()
        plot = re.sub(r'[ \t\r\f\v]+', ' ', plot)
        extrafanart = [urljoin(self.base_url, u) for u in
                       tree.xpath('//div[@class="cover"]//img/@data-src') if u]

        dic = {
            'number': number,
            'title': title,
            'studio': studio,
            'release': release,
            'year': release[:4] if release else '',
            'outline': plot,
            'runtime': '',
            'director': '',
            'actor': actors,
            'actor_photo': {},
            'cover': cover,
            'cover_small': '',
            'extrafanart': extrafanart,
            'trailer': '',
            'tag': tags,
            'label': studio,
            'series': series,
            'userrating': '',
            'uservotes': '',
            'uncensored': False,
            'website': url,
            'source': self.source,
            'imagecut': self.imagecut,
        }
        return json.dumps(dic, ensure_ascii=False, sort_keys=True, separators=(',', ':'))

    @staticmethod
    def _extract_number(title):
        parts = title.split(' ')
        number = parts[-1] if len(parts) > 1 else title
        return (number.replace('_1pondo_', '').replace('1pondo_', '')
                .replace('caribbeancom-', '').replace('caribbeancom', '')
                .replace('-PPV', '').strip(' _-'))

    @staticmethod
    def _clean_title(title):
        """标题末尾是番号（可能还有更短的后缀词），把它们去掉。"""
        parts = title.strip().split(' ')
        if len(parts) > 1 and len(parts[-1]) < 5:
            parts.pop()
        if len(parts) > 1:
            parts.pop()
        return ' '.join(parts).strip()
