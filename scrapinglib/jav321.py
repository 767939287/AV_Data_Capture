# -*- coding: utf-8 -*-

import re
from lxml import etree
from . import httprequest
from .parser import Parser


class Jav321(Parser):
    source = 'jav321'

    expr_title = "/html/body/div[2]/div[1]/div[1]/div[1]/h3/text()"
    expr_cover = "/html/body/div[2]/div[2]/div[1]/p/a/img/@src"
    expr_outline = "/html/body/div[2]/div[1]/div[1]/div[2]/div[3]/div/text()"
    expr_number = '//b[contains(text(),"品番")]/following-sibling::node()'
    expr_actor = '//b[contains(text(),"出演者")]/following-sibling::a[starts-with(@href,"/star")]/text()'
    expr_label = '//b[contains(text(),"メーカー")]/following-sibling::a[starts-with(@href,"/company")]/text()'
    expr_tags = '//b[contains(text(),"ジャンル")]/following-sibling::a[starts-with(@href,"/genre")]/text()'
    expr_studio = '//b[contains(text(),"メーカー")]/following-sibling::a[starts-with(@href,"/company")]/text()'
    expr_release = '//b[contains(text(),"配信開始日")]/following-sibling::node()'
    expr_runtime = '//b[contains(text(),"収録時間")]/following-sibling::node()'
    expr_series = '//b[contains(text(),"シリーズ")]/following-sibling::node()'
    expr_extrafanart = '//div[@class="col-md-3"]/div[@class="col-xs-12 col-md-12"]/p/a/img/@src'

    def queryNumberUrl(self, number):
        return 'https://www.jav321.com/search'

    def getHtmlTree(self, url):
        """ 
        特殊处理 仅获取页面调用一次
        """
        if self.specifiedUrl:
            self.detailurl = self.specifiedUrl
            resp = httprequest.get(self.detailurl, cookies=self.cookies, proxies=self.proxies, verify=self.verify)
            self.detailhtml = resp
            return etree.fromstring(resp, etree.HTMLParser())
        resp = httprequest.post(url, data={"sn": self.number}, cookies=self.cookies, proxies=self.proxies, verify=self.verify)
        if "/video/" in resp.url:
            self.detailurl = resp.url
            self.detailhtml = resp.text
            return etree.fromstring(resp.text, etree.HTMLParser())
        return None

    def getNum(self, htmltree):
        return self._after_colon(super().getNum(htmltree))

    @staticmethod
    def _after_colon(value):
        """取「标签: 值」冒号后的内容；没有冒号时原样返回，避免 [1] 越界。"""
        parts = str(value or '').split(': ', 1)
        return parts[1].strip() if len(parts) == 2 else str(value or '').strip()

    def getTrailer(self, htmltree):
        videourl_pather = re.compile(r'<source src=\"(.*?)\"')
        videourl = videourl_pather.findall(getattr(self, 'detailhtml', '') or '')
        if videourl:
            url = videourl[0].replace('awscc3001.r18.com', 'cc3001.dmm.co.jp').replace('cc3001.r18.com', 'cc3001.dmm.co.jp')
            return url
        else:
            return ''

    def getRelease(self, htmltree):
        return self._after_colon(super().getRelease(htmltree))
    
    def getRuntime(self, htmltree):
        return self._after_colon(super().getRuntime(htmltree))
