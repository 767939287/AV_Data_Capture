# -*- coding: utf-8 -*-

import re
import json
from .parser import Parser


def remove_number_leading_zero(number: str) -> str:
    """去掉连字符后数字部分的前导零，例如 ABC-00001 -> ABC-1。"""
    if not number:
        return ""
    normalized = number.upper().strip()
    matched = re.fullmatch(r"([A-Z0-9]+)-0+(\d+)", normalized)
    if not matched:
        return normalized
    return f"{matched[1]}-{matched[2]}"


def build_candidate_numbers(number: str, short_number: str) -> list:
    """生成 mgstage 详情页可能的番号候选（参考 mdcx-diy 实现，按优先级去重）。"""
    candidates = []
    for each in [
        remove_number_leading_zero(number),
        remove_number_leading_zero(short_number),
        (number or "").upper().strip(),
        (short_number or "").upper().strip(),
    ]:
        if each and each not in candidates:
            candidates.append(each)
    return candidates


class Mgstage(Parser):
    source = 'mgstage'

    expr_number = '//th[contains(text(),"品番：")]/../td/a/text()'
    expr_title = '//*[@id="center_column"]/div[1]/h1/text()'
    expr_studio = '//th[contains(text(),"メーカー：")]/../td/a/text()'
    expr_outline = '//dl[@id="introduction"]/dd/p/text()'
    expr_outline2 = '//*[@id="introduction"]/dd/p[1]/text()'
    # expr_outline3 = '//*[@id="introduction"]/dd'  # 备用：整个 dd 节点(改用 xpath string(.) 直接取，不再经 getTreeElement)
    expr_runtime = '//th[contains(text(),"収録時間：")]/../td/a/text()'
    expr_director = '//th[contains(text(),"シリーズ")]/../td/a/text()'
    expr_actor = '//th[contains(text(),"出演：")]/../td/a/text()'
    expr_release = '//th[contains(text(),"配信開始日：")]/../td/a/text()'
    expr_cover = '//*[@id="EnlargeImage"]/@href'
    expr_label = '//th[contains(text(),"レーベル：")]/../td/a/text()'
    expr_tags = '//th[contains(text(),"ジャンル：")]/../td/a/text()'
    expr_tags2 = '//th[contains(text(),"ジャンル：")]/../td/text()'
    expr_series = '//th[contains(text(),"シリーズ")]/../td/a/text()'
    expr_extrafanart = '//a[@class="sample_image"]/@href'

    def extraInit(self):
        self.imagecut = 4

    def search(self, number):
        self.number = number.upper()
        self.cookies = {'adc': '1'}
        if self.specifiedUrl:
            self.detailurl = self.specifiedUrl
            htmltree = self.getHtmlTree(self.detailurl)
            result = self.dictformat(htmltree)
            return result
        # 参考 mdcx-diy：MGStage 番号可能带前导零(如 ABC-00001)或短号(ABC-1)，
        # 依次尝试各候选详情页，命中即返回。
        base = 'https://www.mgstage.com/product/product_detail/{}/'
        for candidate in build_candidate_numbers(number, number):
            self.number = candidate
            self.detailurl = base.format(candidate)
            htmltree = self.getHtmlTree(self.detailurl)
            if htmltree is None or htmltree == 404:
                continue
            result = self.dictformat(htmltree)
            try:
                if result and result != 404 and json.loads(result).get('title'):
                    return result
            except Exception:
                continue
        return 404

    def getTitle(self, htmltree):
        return super().getTitle(htmltree).replace('/', ',').strip()

    def getOutline(self, htmltree):
        # 参考 mdcx-diy：优先取 introduction/dd/p[1]，缺失时回退整个 dd 的文本
        for expr in (self.expr_outline2, self.expr_outline):
            try:
                result = self.getTreeElement(htmltree, expr)
            except Exception:
                result = ''
            if isinstance(result, str) and len(result.strip()):
                return result.replace('\n', '').strip()
        # 回退：取整个 introduction/dd 节点的文本(string(.))
        try:
            dds = htmltree.xpath('//*[@id="introduction"]/dd')
            if dds and len(dds):
                result = dds[0].xpath('string(.)')
                if isinstance(result, str) and len(result.strip()):
                    return result.replace('\n', '').replace(' ', '').strip()
        except Exception:
            pass
        return ''

    def getTags(self, htmltree):
        return self.getTreeAllbyExprs(htmltree, self.expr_tags, self.expr_tags2)

    def getTreeAll(self, tree, expr):
        alls = super().getTreeAll(tree, expr)
        return [ x.strip() for x in alls if x.strip()]

    def getTreeElement(self, tree, expr, index=0):
        if expr == '':
            return ''
        result1 = ''.join(self.getTreeAll(tree, expr))
        result2 = ''.join(self.getTreeAll(tree, expr.replace('td/a/','td/')))
        if result1 == result2:
            return result1
        return result1 + result2
