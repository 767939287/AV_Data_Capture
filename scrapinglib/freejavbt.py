# -*- coding: utf-8 -*-

import json
import re
from urllib.parse import quote, unquote, urljoin

from lxml import etree

from .parser import Parser

# 详情页 h1 的尾注，各语言不同，不属于标题
_TITLE_TAIL = re.compile(r'\s*(?:免費AV在線看|免费AV在线看|Watch for free|Смотреть онлайн)\s*$', re.IGNORECASE)
_DATE = re.compile(r'\d{4}-\d{2}-\d{2}')
_LEADING_DIGITS = re.compile(r'^\d+')


def fold_number(value):
    """忽略大小写与分隔符的番号形态，用于宽松比对。"""
    return re.sub(r'[^0-9a-z]', '', (value or '').casefold())


class Freejavbt(Parser):
    """FREE JAV BT 刮削器（freejavbt.com）。

    检索页 ``/search/<番号>`` 的条目为 ``div.category-page.video-list-item``，条目链接末段
    即番号；详情页 ``/<语言>/<番号>`` 的字段都在 ``div.single-video-meta`` 信息行里。
    站点在部分网络下会被 Cloudflare 拦截，需启用 ``[cloudflare]`` 走外部服务。
    """

    source = 'freejavbt'
    base_url = 'https://freejavbt.com'

    def extraInit(self):
        self.imagecut = 1

    def search(self, number):
        self.number = number
        if self.specifiedUrl:
            return self._scrape(self.specifiedUrl)
        # 站内链接末段可能是去短横线或去 PPV 的写法，逐个形态尝试
        for variant in self._variants(number):
            url = self._search_number(variant, number)
            if url:
                return self._scrape(url)
        return 404

    @staticmethod
    def _variants(number):
        num = number.strip()
        forms = [num, num.replace('-', ''), num.replace('_', '')]
        stripped = re.sub(r'(?i)^fc2[-_]?(?:ppv[-_]?)?', '', num).strip()
        if stripped and stripped.isdigit():
            forms.extend([f'FC2-{stripped}', f'FC2PPV-{stripped}', stripped])
        return list(dict.fromkeys(f for f in forms if f))

    def _search_number(self, term, number):
        html = self.getHtml(f'{self.base_url}/search/{quote(term)}')
        if html == 404:
            return None
        tree = etree.fromstring(html, etree.HTMLParser())
        target = fold_number(number)
        for item in tree.xpath('//div[contains(@class,"category-page") and contains(@class,"video-list-item")]'):
            # 一个卡片里有封面图与标题两个 a，取第一个且只认站内链接（跳过广告）
            for href in item.xpath('.//a/@href'):
                href = (href or '').strip()
                if 'freejavbt.com' not in href:
                    continue
                found = self._number_from_url(href)
                if found and fold_number(found) == target:
                    return urljoin(self.base_url, href)
        return None

    @staticmethod
    def _number_from_url(url):
        path = url.split('?', 1)[0].rstrip('/')
        return unquote(path.rsplit('/', 1)[-1]) if path else ''

    def _scrape(self, url):
        html = self.getHtml(url)
        if html == 404:
            return 404
        tree = etree.fromstring(html, etree.HTMLParser())

        number = self._number_from_url(url)
        heading = re.sub(r'\s+', ' ', ''.join(tree.xpath('//h1//text()'))).strip()
        title = _TITLE_TAIL.sub('', heading).strip()
        if number and title.casefold().startswith(number.casefold()):
            title = title[len(number):].strip()
        if not title:
            return 404

        release, runtime = self._release_and_runtime(tree)
        cover = ''.join(tree.xpath('//meta[@property="og:image"]/@content')).strip()

        actors = [x.strip() for x in tree.xpath('//a[contains(@class,"actress")]/text()') if x.strip()]
        directors = [x.strip() for x in tree.xpath('//div[contains(@class,"director")]//a/text()') if x.strip()]
        series = ''.join(tree.xpath('//div[contains(@class,"series")]//a/text()')).strip()
        tags = [x.strip() for x in tree.xpath(
            '//div[contains(@class,"single-video-meta")]//a[contains(@class,"btn-danger")]/text()') if x.strip()]
        extrafanart = [urljoin(self.base_url, u) for u in tree.xpath(
            '//div[contains(@class,"preview")]//a[contains(@class,"tile-item")]/@href') if u]

        dic = {
            'number': number,
            'title': title,
            'studio': '',
            'release': release,
            'year': release[:4] if release else '',
            'outline': '',
            'runtime': str(runtime) if runtime else '',
            'director': directors[0] if directors else '',
            'actor': actors,
            'actor_photo': {},
            'cover': cover,
            'cover_small': '',
            'extrafanart': extrafanart,
            'trailer': '',
            'tag': tags,
            'label': '',
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
    def _release_and_runtime(tree):
        """无链接的信息行里，日期形为发布日期，其余前导整数视为片长（分钟）。"""
        release = ''
        runtime = 0
        for row in tree.xpath('//div[contains(@class,"single-video-meta")][not(.//a)]'):
            text = re.sub(r'\s+', ' ', ''.join(row.xpath('.//text()'))).strip()
            value = text.split(':', 1)[1].strip() if ':' in text else text
            if _DATE.fullmatch(value):
                release = release or value
            else:
                matched = _LEADING_DIGITS.match(value)
                if matched and not runtime:
                    runtime = int(matched.group())
        return release, runtime
