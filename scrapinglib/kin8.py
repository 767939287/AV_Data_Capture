# -*- coding: utf-8 -*-

import json
import re

from lxml import etree

from .parser import Parser

# 官网番号形如 KIN8-0001 / KIN8TENGOKU-0001，详情页 id 是其数字段补零到 4 位
_NUMBER_PREFIX = re.compile(r'^KIN8(?:TENGOKU)?[-_ ]?(?P<id>\d+)$')
# 官网 id 恰好 4 位补零：/movie/1、/movie/12345、/movie/0000 都不存在
_ID_DIGITS = 4
_MOVIE_PATH = re.compile(r'/movie/(?P<id>\d+)')
# <title> / og:title 的站点名后缀
_SITE_TITLE_SEPARATOR = '|'


def movie_id(number):
    """番号 → 详情页 id；前缀不符或数字段无效时返回 None。"""
    matched = _NUMBER_PREFIX.match((number or '').strip().upper())
    if matched is None:
        return None
    value = matched['id'].lstrip('0')
    if not value or len(value) > _ID_DIGITS:
        return None
    return value.zfill(_ID_DIGITS)


class Kin8(Parser):
    """KIN8 / 金髪天國 刮削器（kin8tengoku.com）。

    番号直接推导出详情页 ``/movie/{id}``；标题取 ``og:title``（去掉站点名后缀），
    演员 / 类型在 ``Movie_Detail_actor_*`` 区块里。站点偶尔整体维护（返回维护页），此时按无此番号处理。
    """

    source = 'kin8'
    base_url = 'https://www.kin8tengoku.com'

    def extraInit(self):
        self.imagecut = 1

    def search(self, number):
        self.number = (number or '').strip().upper()
        if self.specifiedUrl:
            return self._scrape(self.specifiedUrl)
        detail_id = movie_id(self.number)
        if detail_id is None:
            return 404
        url = f'{self.base_url}/movie/{detail_id}'
        html = self.getHtml(url)
        if html == 404:
            return 404
        return self._scrape(url, html)

    def _scrape(self, url, html=None):
        if html is None:
            html = self.getHtml(url)
        if html == 404:
            return 404
        # 站点维护时整站返回「メンテナンスのお知らせ」页
        if 'メンテナンス' in html or 'maintenance' in html.lower():
            return 404

        tree = etree.fromstring(html, etree.HTMLParser())

        title = self.getTreeElement(tree, '//meta[@property="og:title"]/@content').strip()
        if not title:
            title = self.getTreeElement(tree, '//title/text()').strip()
        title = title.split(_SITE_TITLE_SEPARATOR, 1)[0].strip()
        if not title:
            return 404

        actors = [x.strip() for x in tree.xpath(
            '//div[starts-with(@class,"Movie_Detail_actor_")]//a/text()') if x.strip()]
        tags = [x.strip() for x in tree.xpath(
            '//div[contains(@class,"Movie_Detail_actor-type")]//a/text()') if x.strip()]
        cover = self.getTreeElement(tree, '//meta[@property="og:image"]/@content').strip()

        matched = _MOVIE_PATH.search(url)
        detail_id = matched['id'] if matched else ''
        number = self.number if _NUMBER_PREFIX.match(self.number) else f'KIN8-{detail_id}'

        dic = {
            'number': number,
            'title': title,
            'studio': '',
            'release': '',
            'year': '',
            'outline': '',
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
            'uncensored': True,
            'website': url,
            'source': self.source,
            'imagecut': self.imagecut,
        }
        return json.dumps(dic, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
