# -*- coding: utf-8 -*-

import re
from urllib.parse import urljoin

from lxml import etree

from .parser import Parser


class Xcity(Parser):
    """XCITY 刮削器（xcity.jp）。

    站点对缺少 Referer 的检索请求会 302 到默认检索词（XCITYオリジナル），因此请求必须带
    ``Referer: https://xcity.jp/``；检索页 ``/result_published/?q=<番号去连字符>`` 的
    结果链接形如 ``/avod/detail/?id=197249``。详情页沿用 ``#avodDetails`` 内的
    ``span.koumoku`` 标签布局。
    """

    source = 'xcity'
    base_url = 'https://xcity.jp'

    expr_number = '//*[@id="hinban"]/text()'
    expr_title = '//*[@id="program_detail_title"]/text()'
    expr_actor = '//li[span[@class="koumoku" and text()="出演"]]/a/text()'
    expr_cover = '//meta[@property="og:image"]/@content'
    expr_release = '//span[@class="koumoku" and text()="発売日"]/following-sibling::text()'
    expr_runtime = '//span[@class="koumoku" and text()="収録時間"]/following-sibling::text()'
    expr_studio = '//*[@id="program_detail_maker_name"]/text()'
    expr_label = '//*[@id="program_detail_label_name"]/text()'
    expr_director = '//*[@id="program_detail_director"]/text()'
    expr_series = "//a[contains(@href,'series_id=')]/span/text()"
    expr_tags = '//a[@class="genre"]/text()'
    expr_extrafanart = '//div[@id="sample_images"]//a/@href'
    expr_outline = '//head/meta[@property="og:description"]/@content'

    def extraInit(self):
        # xcity 对无 Referer 的检索请求一律重定向到默认词，必须显式带上站内 Referer
        self.extraheader = {'Referer': 'https://xcity.jp/'}
        self.imagecut = 1

    def queryNumberUrl(self, number):
        """站内检索番号，返回第一条详情页地址；无结果返回 None。"""
        term = number.replace('-', '')
        html = self.getHtml(f'{self.base_url}/result_published/?q={term}&num=30')
        if html == 404:
            return None
        tree = etree.fromstring(html, etree.HTMLParser())
        links = tree.xpath('//a[starts-with(@href,"/avod/detail/")]/@href')
        if not links:
            return None
        return urljoin(self.base_url, links[0])

    def getRuntime(self, htmltree):
        text = ''.join(htmltree.xpath(self.expr_runtime)).strip()
        matched = re.search(r'\d+', text)
        return matched.group(0) if matched else ''

    def getRelease(self, htmltree):
        text = ''.join(htmltree.xpath(self.expr_release)).strip()
        matched = re.search(r'\d{4}[/-]\d{1,2}[/-]\d{1,2}', text)
        return matched.group(0).replace('/', '-') if matched else ''

    def getCover(self, htmltree):
        cover = super().getCover(htmltree).strip()
        if not cover:
            return ''
        return urljoin(self.base_url, cover)

    def getExtrafanart(self, htmltree):
        return [urljoin(self.base_url, url) for url in super().getExtrafanart(htmltree) if url]