# -*- coding: utf-8 -*-

import os
import re
import json
import inspect
from lxml import etree
from urllib.parse import urlencode
from .parser import Parser


class Fanza(Parser):
    source = 'fanza'

    expr_title = '//*[starts-with(@id, "title")]/text()'
    expr_actor = "//td[contains(text(),'出演者')]/following-sibling::td/span/a/text()"
    # expr_cover = './/head/meta[@property="og:image"]/@content'
    # expr_extrafanart = '//a[@name="sample-image"]/img/@src'
    expr_outline = "//div[@class='mg-b20 lh4']/text()"
    expr_outline2 = "//div[@class='mg-b20 lh4']//p/text()"
    expr_outline3 = "//div[contains(@class,'wrapper-detailContents')]/following-sibling::div/p[contains(@class,'mg-b20')]/text()"
    expr_outline4 = ".//div[@class='clear']/p/text()"
    expr_outline_og = '//head/meta[@property="og:description"]/@content'
    expr_runtime = "//td[contains(text(),'収録時間')]/following-sibling::td/text()"

    def search(self, number):
        self.number = number
        if self.specifiedUrl:
            self.detailurl = self.specifiedUrl
            durl = "https://www.dmm.co.jp/age_check/=/declared=yes/?"+ urlencode({"rurl": self.detailurl})
            self.htmltree = self.getHtmlTree(durl)
            result = self.dictformat(self.htmltree)
            return result

        # 1) 优先走 DMM 官方 Affiliate API（稳定、结构化；FANZA 即 DMM 成人品牌，内容同源）。
        #    失败（无凭据/无匹配/网络异常）时回退到下面的详情页爬取。
        try:
            from .dmm_api import search_by_number as _dmm_api_search_by_number
            api_data = _dmm_api_search_by_number(number, proxies=self.proxies, verify=self.verify)
            if api_data and (api_data.get("title") or api_data.get("cover")):
                # API 无简介；需要更多剧情时由 storyline 在 getOutline 阶段补充
                if getattr(self, "morestoryline", False):
                    try:
                        from .storyline import getStoryline
                        more = getStoryline(api_data.get("number", number),
                                            uncensored=getattr(self, "uncensored", False),
                                            proxies=self.proxies, verify=self.verify)
                        if isinstance(more, str) and len(more):
                            api_data["outline"] = more
                    except Exception:
                        pass
                return json.dumps(api_data, ensure_ascii=False, sort_keys=True,
                                  separators=(',', ':'))
        except Exception:
            pass

        # fanza allow letter + number + underscore, normalize the input here
        # @note: I only find the usage of underscore as h_test123456789
        fanza_search_number = number
        # AV_Data_Capture.py.getNumber() over format the input, restore the h_ prefix
        if fanza_search_number.startswith("h-"):
            fanza_search_number = fanza_search_number.replace("h-", "h_")

        fanza_search_number = re.sub(r"[^0-9a-zA-Z_]", "", fanza_search_number).lower()

        fanza_urls = [
            "https://www.dmm.co.jp/digital/videoa/-/detail/=/cid=",
            "https://www.dmm.co.jp/mono/dvd/-/detail/=/cid=",
            "https://www.dmm.co.jp/digital/anime/-/detail/=/cid=",
            "https://www.dmm.co.jp/mono/anime/-/detail/=/cid=",
            "https://www.dmm.co.jp/digital/videoc/-/detail/=/cid=",
            "https://www.dmm.co.jp/digital/nikkatsu/-/detail/=/cid=",
            "https://www.dmm.co.jp/rental/-/detail/=/cid=",
        ]

        # DMM/FANZA 的 cid 是「字母前缀 + 数字补零到5位」，例如：
        #   ABF-123  -> abf00123
        #   SSIS-001 -> ssis00001
        #   STARS-1  -> stars00001
        # 而 AV_Data_Capture 传入的番号多为去掉连字符的短格式(如 abf123)，
        # 直接拼 cid 会 404，因此这里生成候选 cid 依次尝试。
        for fanza_cid in self._cid_candidates(fanza_search_number):
            for url in fanza_urls:
                self.detailurl = url + fanza_cid
                req_url = "https://www.dmm.co.jp/age_check/=/declared=yes/?"+ urlencode({"rurl": self.detailurl})
                self.htmlcode = self.getHtml(req_url)
                if self.htmlcode != 404 \
                        and 'Sorry! This content is not available in your region.' not in self.htmlcode:
                    self.htmltree = etree.HTML(self.htmlcode)
                    if self.htmltree is not None:
                        result = self.dictformat(self.htmltree)
                        return result
        return 404

    @staticmethod
    def _cid_candidates(fanza_search_number):
        """根据番号生成 DMM/FANZA 可能的 cid 候选（保持优先级顺序，去重）。

        参考 MDCx 的做法：DMM 的 cid 不仅「前缀+编号补零到5位」一种形态，
        不同厂牌有各自的前缀，例如：
            ssis200 -> ssis00200
            abf123  -> 436abf00123   (前缀 '436')
            milk123 -> h_1240milk00123 (前缀 'h_1240')
            t28_645 -> 55t2800645    (前缀 '55')
        因此优先用 dmm_prefix 的前缀表生成候选，并保留「原样 / 补零」兜底。
        """
        candidates = [fanza_search_number]
        # 1) 前缀表候选（覆盖特殊厂牌前缀，如 436abf00123 / h_1240milk00123）
        try:
            from .dmm_prefix import cid_candidates
            for cid in cid_candidates(fanza_search_number):
                if cid not in candidates:
                    candidates.append(cid)
        except Exception:
            pass
        # 2) 兜底：原样 -> 前缀+数字补零到5位
        m = re.match(r'^([a-z]+)(\d+)$', fanza_search_number)
        if m:
            prefix, digits = m.groups()
            if len(digits) < 5:
                padded = prefix + digits.zfill(5)
                if padded not in candidates:
                    candidates.append(padded)
        return candidates

    def getNum(self, htmltree):
        # for some old page, the input number does not match the page
        # for example, the url will be cid=test012
        # but the hinban on the page is test00012
        # so get the hinban first, and then pass it to following functions
        self.fanza_hinban = self.getFanzaString('品番：')
        self.number = self.fanza_hinban
        number_lo = self.number.lower()
        if (re.sub('-|_', '', number_lo) == self.fanza_hinban or
            number_lo.replace('-', '00') == self.fanza_hinban or
            number_lo.replace('-', '') + 'so' == self.fanza_hinban
        ):
            self.number = self.number
        return self.number

    def getStudio(self, htmltree):
        return self.getFanzaString('メーカー')

    def getOutline(self, htmltree):
        result = self._getFanzaOutline(htmltree)
        # 关闭 storyline 时直接返回 FANZA 自身的（日文）简介
        if not self.morestoryline:
            return result
        # 从 storyline.py 出发时不可再回调 storyline.py 中的 fanza 源，避免无限递归
        if any(
            caller
            for caller in inspect.stack()
            if os.path.basename(caller.filename) == "storyline.py"
        ):
            return result
        # 优先使用 FANZA 自身的简介，仅当其缺失时才回退到中文剧情简介站点
        if isinstance(result, str) and len(result.strip()):
            return result
        from .storyline import getStoryline
        try:
            more = getStoryline(
                self.number,
                uncensored=self.uncensored,
                proxies=self.proxies,
                verify=self.verify,
            )
            if isinstance(more, str) and len(more):
                return more
        except Exception:
            pass
        return result

    def _getFanzaOutline(self, htmltree):
        """从 FANZA/DMM 详情页解析（日文）剧情简介，多选择器依次回退。"""
        # 1) digital/video 页面优先从 JSON-LD 里取 description
        try:
            json_text = self.getTreeElement(htmltree, '//script[@type="application/ld+json"]/text()')
            if json_text:
                data = json.loads(json_text)
                desc = data.get("description") if isinstance(data, dict) else None
                if isinstance(desc, str) and desc.strip():
                    return desc.strip()
        except Exception:
            pass
        # 2) 依次尝试各站点布局的详情页简介选择器
        for expr in (self.expr_outline, self.expr_outline2, self.expr_outline3, self.expr_outline4):
            try:
                result = self.getTreeElement(htmltree, expr).replace("\n", "").strip()
            except Exception:
                result = ''
            if result and "※ 配信方法によって収録内容が異なる場合があります。" != result:
                return result
        # 3) 回退到 og:description
        try:
            result = self.getTreeElement(htmltree, self.expr_outline_og).replace("\n", "").strip()
        except Exception:
            result = ''
        return result

    def getRuntime(self, htmltree):
        # 収録時間 字段可能缺失或其结构变化，此时 super().getRuntime() 返回空串，
        # re.search(r'\d+', '') 会得到 None，直接 .group() 会抛
        # AttributeError: 'NoneType' object has no attribute 'group'，
        # 导致整个 dictformat 失败、说明与简介全丢。这里做判空保护。
        raw = super().getRuntime(htmltree)
        m = re.search(r'\d+', raw or '')
        if not m:
            return ''
        return str(m.group()).strip(" ['']")

    def getDirector(self, htmltree):
        if "anime" not in self.detailurl:
            return self.getFanzaString('監督：')
        return ''

    def getActors(self, htmltree):
        if "anime" not in self.detailurl:
            return super().getActors(htmltree)
        return ''

    def getRelease(self, htmltree):
        result = self.getFanzaString('発売日：')
        if result == '' or result == '----':
            result = self.getFanzaString('配信開始日：')
        return result.replace("/", "-").strip('\\n')

    def getTags(self, htmltree):
        return self.getFanzaStrings('ジャンル：')

    def getLabel(self, htmltree):
        ret = self.getFanzaString('レーベル')
        if ret == "----":
            return ''
        return ret

    def getSeries(self, htmltree):
        ret = self.getFanzaString('シリーズ：')
        if ret == "----":
            return ''
        return ret
    
    def getCover(self, htmltree):
        cover_number = self.number
        try:
            result = htmltree.xpath('//*[@id="sample-image1"]/img/@src')[0]
        except:
            # sometimes fanza modify _ to \u0005f for image id
            if "_" in cover_number:
                cover_number = cover_number.replace("_", r"\u005f")
            try:
                result = htmltree.xpath('//*[@id="' + cover_number + '"]/@href')[0]
            except:
                # (TODO) handle more edge case
                # print(html)
                # raise exception here, same behavior as before
                # people's major requirement is fetching the picture
                raise ValueError("can not find image")
        return result

    def getExtrafanart(self, htmltree):
        htmltext = re.search(r'<div id=\"sample-image-block\"[\s\S]*?<br></div>\s*?</div>', self.htmlcode)
        if htmltext:
            htmltext = htmltext.group()
            extrafanart_images = re.findall(r'<img.*?src=\"(.*?)\"', htmltext)
            if extrafanart_images:
                sheet = []
                for img_url in extrafanart_images[1:]:
                    url_cuts = img_url.rsplit('-', 1)
                    sheet.append(url_cuts[0] + 'jp-' + url_cuts[1])
                return sheet
        return ''

    def getTrailer(self, htmltree):
        htmltext = re.search(r'<script type=\"application/ld\+json\">[\s\S].*}\s*?</script>', self.htmlcode)
        if htmltext:
            htmltext = htmltext.group()
            url = re.search(r'\"contentUrl\":\"(.*?)\"', htmltext)
            if url:
                url = url.group(1)
                url = url.rsplit('_', 2)[0] + '_mhb_w.mp4'
                return url
        return ''

    def getFanzaString(self, expr):
        result1 = str(self.htmltree.xpath("//td[contains(text(),'"+expr+"')]/following-sibling::td/a/text()")).strip(" ['']")
        result2 = str(self.htmltree.xpath("//td[contains(text(),'"+expr+"')]/following-sibling::td/text()")).strip(" ['']")
        return result1+result2

    def getFanzaStrings(self, string):
        result1 = self.htmltree.xpath("//td[contains(text(),'" + string + "')]/following-sibling::td/a/text()")
        if len(result1) > 0:
            return result1
        result2 = self.htmltree.xpath("//td[contains(text(),'" + string + "')]/following-sibling::td/text()")
        return result2
