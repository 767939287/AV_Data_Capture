# -*- coding: utf-8 -*-

import json
import re
from urllib.parse import quote, urljoin

from lxml import etree

from .parser import Parser

# 站内部分来源把来源名写在番号前（caribbeancom_051318-665），比对前先剥除
_SOURCE_LABELS = ('caribbeancom', '1pondo', '10musume', 'pacopacomama')


def fold_number(value):
    """忽略大小写与分隔符的番号形态，用于宽松比对。"""
    return re.sub(r'[^0-9a-z]', '', (value or '').casefold())


class Airav(Parser):
    """AIRAV 刮削器（airav.io）。

    旧版 www.airav.wiki 的 JSON API 已下线，新版为服务端渲染 HTML：
    搜索页 ``/search_result?kw=`` 中每部影片是一个 ``div.oneVideo``；详情页
    ``/video?hid=`` 的番号/女优/标签在 ``ul.list-group`` 里，简介在
    ``div.video-info > p``，发行日期位于时钟图标旁。
    """

    source = 'airav'
    base_url = 'https://airav.io'

    def extraInit(self):
        self.imagecut = 1
        self.originalnum = ''

    def search(self, number):
        self.number = number
        if self.specifiedUrl:
            return self._scrape(self.specifiedUrl)
        # 站内 FC2 恒写 FC2-PPV-<id>，优先用它检索，未命中再按原样检索
        terms = [number]
        article_id = re.sub(r'(?i)^(?:fc2-?ppv-?|fc2-?)', '', number).strip('-')
        if article_id.isdigit():
            terms.insert(0, f'FC2-PPV-{article_id}')
        for term in dict.fromkeys(terms):
            url = self._search_term(term, number)
            if url:
                return self._scrape(url)
        return 404

    def _search_term(self, term, number):
        """检索单个写法，返回与番号严格对应的详情页地址（不取首条）。"""
        html = self.getHtml(f'{self.base_url}/search_result?kw={quote(term)}')
        if html == 404:
            return None
        tree = etree.fromstring(html, etree.HTMLParser())
        target = fold_number(number)
        for item in tree.xpath('//div[contains(@class,"oneVideo")]'):
            href = ''.join(item.xpath('.//a/@href')).strip()
            title = ''.join(item.xpath('.//h5//text()')).strip()
            if not href or not title:
                continue
            token = self._strip_source_label(title.split(' ')[0])
            if fold_number(token) == target:
                return urljoin(self.base_url, href)
        return None

    def _scrape(self, url):
        html = self.getHtml(url)
        if html == 404:
            return 404
        tree = etree.fromstring(html, etree.HTMLParser())

        number = self.getTreeElement(tree, '//li[contains(text(),"番")]/span/text()').strip()
        heading = self.getTreeElement(tree, '//div[contains(@class,"video-title")]//h1/text()').strip()
        if not number or not heading:
            return 404

        token = heading.split(' ')[0]
        if fold_number(self._strip_source_label(token)) == fold_number(number):
            title = heading[len(token):].strip()
        else:
            title = heading

        cover = self.getTreeElement(tree, '//meta[@property="og:image"]/@content').strip()
        if cover and not cover.startswith('http'):
            cover = urljoin(self.base_url, cover)

        release = ''.join(
            tree.xpath('//div[contains(@class,"video-item")]//i[contains(@class,"fa-clock")]/parent::*/text()')
        ).strip()
        matched = re.search(r'\d{4}-\d{2}-\d{2}', release)
        release = matched.group(0) if matched else ''

        actors = [x.strip() for x in tree.xpath('//a[contains(@href,"actor?id=")]/text()') if x.strip()]
        studio = self.getTreeElement(tree, '//a[contains(@href,"tag?id=")]/text()').strip()
        tags = [x.strip() for x in tree.xpath('//a[contains(@href,"tag?tid=")]/text()') if x.strip()]
        outline = self.getTreeElement(tree, '//div[contains(@class,"video-info")]/p/text()').strip()

        dic = {
            'number': number,
            'title': title,
            'studio': studio,
            'release': release,
            'year': release[:4] if release else '',
            'outline': outline,
            'runtime': '',
            'director': '',
            'actor': actors,
            'actor_photo': {},
            'cover': cover,
            'cover_small': '',
            'extrafanart': [],
            'trailer': '',
            'tag': tags,
            'label': '',
            'series': '',
            'userrating': '',
            'uservotes': '',
            'uncensored': False,
            'website': url,
            'source': self.source,
            'imagecut': self.imagecut,
        }
        return json.dumps(dic, ensure_ascii=False, sort_keys=True, separators=(',', ':'))

    @staticmethod
    def _strip_source_label(value):
        """剥除站内来源名前缀，如 ``caribbeancom_051318-665`` → ``051318-665``。"""
        folded = (value or '').casefold()
        for label in _SOURCE_LABELS:
            if not folded.startswith(label):
                continue
            rest = value[len(label):]
            if rest[:1] in ('_', '-'):
                rest = rest[1:]
            elif not rest[:1].isdigit():
                continue
            if rest:
                return rest
        return value

    # def queryNumberUrl(self, number):
    #     queryUrl =  "https://cn.airav.wiki/?search=" + number
    #     queryTree = self.getHtmlTree(queryUrl)
    #     results = self.getTreeAll(queryTree, '//div[contains(@class,"videoList")]/div/a')
    #     for i in results:
    #         num = self.getTreeElement(i, '//div/div[contains(@class,"videoNumber")]/p[1]/text()')
    #         if num.replace('-','') == number.replace('-','').upper():
    #             self.number = num
    #             return "https://cn.airav.wiki" + i.attrib['href']
    #     return 'https://cn.airav.wiki/video/' + number


