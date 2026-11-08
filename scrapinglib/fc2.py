# -*- coding: utf-8 -*-

import re
from lxml import etree
from urllib.parse import urljoin

from .parser import Parser


class Fc2(Parser):
    source = 'fc2'

    # 标题取主图 alt：标题行里除标题外还有促销角标与隐藏水印
    expr_title = '//div[@class="items_article_MainitemThumb"]//img/@alt'
    expr_studio = '//div[@class="items_article_headerInfo"]/ul/li[last()]/a/text()'
    # 站点按语言返回「販売日」或「Sale Day」
    expr_release = ('//div[@class="items_article_softDevice"]/p[contains(text(),"販売日")'
                    ' or contains(text(),"Sale Day")]/text()')
    expr_runtime = "//p[@class='items_article_info']/text()"
    expr_director = ''
    expr_actor = '//div[@class="items_article_headerInfo"]/ul/li[last()]/a/text()'
    expr_cover = "//div[@class='items_article_MainitemThumb']/span/img/@src"
    expr_extrafanart = '//ul[@class="items_article_SampleImagesArea"]/li/a/@href'
    expr_tags = "//a[@class='tag tagTag']/text()"
    # 商品页独有的块；商品不在售时站点回 200 但页面无此块
    _ARTICLE_BLOCK = '//div[@data-section="userInfo"]'

    def extraInit(self):
        self.imagecut = 0
        self.allow_number_change = True
        self.uncensored = True

    def search(self, number):
        self.number = number.lower().replace('fc2ppv-', '').replace('fc2-ppv-', '').replace('fc2-', '').replace('fc-', '').replace('fc_', '')
        if self.specifiedUrl:
            self.detailurl = self.specifiedUrl
        else:
            self.detailurl = 'https://adult.contents.fc2.com/article/' + self.number + '/'
            #self.detailurl = 'https://adult.contents.fc2.com/article/' + self.number + '/review'
        self.htmlcode = self.getHtml(self.detailurl)
        if self.htmlcode == 404:
            return 404
        htmltree = etree.HTML(self.htmlcode)
        # 商品已下架时站点仍返回 200 的「お探しの商品が見つかりませんでした」页，按无此番号处理
        if not htmltree.xpath(self._ARTICLE_BLOCK):
            return 404
        result = self.dictformat(htmltree)
        return result

    def getNum(self, htmltree):
        return 'FC2-' + self.number

    def getRelease(self, htmltree):
        text = self.getTreeElement(htmltree, self.expr_release).strip()
        matched = re.search(r'(\d{4})/(\d{1,2})/(\d{1,2})', text)
        return f'{matched.group(1)}-{int(matched.group(2)):02d}-{int(matched.group(3)):02d}' if matched else ''

    def getActors(self, htmltree):
        actors = super().getActors(htmltree)
        if not actors:
            actors = '素人'
        return actors

    def getCover(self, htmltree):
        cover = super().getCover(htmltree).strip()
        if not cover:
            return ''
        return urljoin('https://adult.contents.fc2.com', cover)

    def getOutline(self, htmltree):
        return self.getTreeElement(htmltree, '//meta[@name="description"]/@content').strip()

    def getTrailer(self, htmltree):
        video_pather = re.compile(r'\'[a-zA-Z0-9]{32}\'')
        video = video_pather.findall(self.htmlcode)
        if video:
            try:
                video_url = video[0].replace('\'', '')
                video_url = 'https://adult.contents.fc2.com/api/v2/videos/' + self.number + '/sample?key=' + video_url
                url_json = eval(self.getHtml(video_url))['path'].replace('\\', '')
                return url_json
            except:
                return ''
        else:
            return ''
