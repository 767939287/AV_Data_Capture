# -*- coding: utf-8 -*-
"""7MMTV 聚合站刮削器（7mmtv.sx）。

站点把同一番号拆成三个分类页，元数据一致，只有面包屑与标题前缀不同：
    /zh/censored_content/<id>/<番号>.html         有码原版
    /zh/chinese_content/<id>/<番号>.html          中文字幕
    /zh/reducing-mosaic_content/<id>/<番号>.html  無碼破解
因此搜索命中后默认按上述顺序择优（可通过 specifiedUrl 指定具体页面）。

站点直连可访问（实测 200，无 Cloudflare 拦截），不需要 solver。
"""

import json
import re
from urllib.parse import quote, urljoin

from lxml import etree

from number_parser import is_uncensored
from .parser import Parser

_BASE = 'https://7mmtv.sx'

# 详情页分类优先级：数字越小越优先
_KIND_ORDER = {
    'censored': 0,
    'chinese': 1,
    'reducing-mosaic': 2,
}
_KIND_RE = re.compile(r'/zh/([a-z\-]+)_content/\d+/')
# 标题前缀，如 [中字] / [無碼破解]
_TITLE_PREFIX = re.compile(r'^\[[^\]]+\]\s*')
_FOLD_RE = re.compile(r'[^0-9a-z]')
_DATE_RE = re.compile(r'\d{4}-\d{2}-\d{2}')
_COVER_RE = re.compile(r'class="player-cover"\s*><a><img\s+src="([^"]+)')

# 正在执行 storyline 兜底的番号（storyline 的 7mmtv 通道会再次抓同一页，需防递归）
_FALLBACK_NUMBERS = set()


def fold_number(value):
    """番号归一：忽略大小写与分隔符。"""
    return _FOLD_RE.sub('', (value or '').casefold())


class Mmtv(Parser):
    """7MMTV 刮削实现（有码 + 中字 + 無碼破解，聚合站）。"""

    source = '7mmtv'
    base_url = _BASE

    def extraInit(self):
        # 封面本身已是完整横图，直接使用不裁剪
        self.imagecut = 0
        self.uncensored = False

    # ------------------------------------------------------------------
    # 搜索
    # ------------------------------------------------------------------
    def search(self, number):
        self.number = number
        if self.specifiedUrl:
            return self._scrape(urljoin(self.base_url, self.specifiedUrl))
        urls = self._search_urls(number)
        if not urls:
            return 404
        return self._scrape(urls[0])

    @staticmethod
    def _keyword(number):
        """FC2 只取数字段检索（站点不认 FC2-PPV 前缀）。"""
        if number.upper().startswith('FC2'):
            digits = re.findall(r'\d{3,}', number)
            return digits[0] if digits else number
        return number

    def _search_urls(self, number):
        url = ('%s/zh/searchform_search/all/index.html?search_keyword=%s'
               '&search_type=searchall&op=search' % (self.base_url, quote(self._keyword(number))))
        html = self.getHtml(url)
        if not html or html == 404:
            return []
        tree = etree.fromstring(html, etree.HTMLParser())
        target = fold_number(number)
        hits = []
        for figure in tree.xpath('//figure[contains(@class,"video-preview")]'):
            for link in figure.xpath('.//a[@href]'):
                href = (link.get('href') or '').strip()
                alt = link.xpath('.//img/@alt')
                alt = alt[0].strip() if alt else ''
                if not href or not alt:
                    continue
                if not self._alt_match(alt, number, target):
                    continue
                hits.append(urljoin(self.base_url, href))
                break
        return sorted(dict.fromkeys(hits), key=self._kind_rank)

    @staticmethod
    def _alt_match(alt, number, target):
        """用结果卡片的 alt 首段匹配目标番号，避免误取无关结果。"""
        token = alt.split(' ')[0] if alt else ''
        folded = fold_number(token)
        if number.upper().startswith('FC2'):
            digits = re.findall(r'\d{3,}', number)
            return bool(digits) and digits[0] in fold_number(alt)
        return bool(folded) and folded == target

    @staticmethod
    def _kind_rank(url):
        matched = _KIND_RE.search(url)
        return _KIND_ORDER.get(matched.group(1), 9) if matched else 9

    # ------------------------------------------------------------------
    # 详情页
    # ------------------------------------------------------------------
    def _scrape(self, url):
        html = self.getHtml(url)
        if not html or html == 404:
            return 404
        tree = etree.fromstring(html, etree.HTMLParser())

        panel = [x.strip() for x in tree.xpath('//div[@class="d-flex mb-4"]/span/text()')]
        web_number = panel[0] if panel else self.number
        number = web_number.replace('FC2-PPV ', 'FC2-').strip()

        heading = self._collapsed(tree, '//h1[contains(@class,"fullvideo-title")]//text()')
        if not heading:
            return 404
        title = _TITLE_PREFIX.sub('', heading).strip()
        for candidate in (web_number, number):
            if candidate and title.upper().startswith(candidate.upper()):
                title = title[len(candidate):].strip()
                break

        release = ''
        if len(panel) >= 2:
            matched = _DATE_RE.search(panel[1])
            release = matched.group(0) if matched else ''
        runtime = self._runtime(panel[2]) if len(panel) >= 3 else ''

        actors = []
        for name in tree.xpath('//div[@class="fullvideo-idol"]/span/a/text()'):
            cleaned = re.sub(r'（.+?）', '', name or '').strip().split(' ')[0].strip()
            if cleaned:
                actors.append(cleaned)

        tags = [t.strip() for t in tree.xpath(
            '//div[contains(@class,"d-flex") and contains(@class,"flex-wrap")'
            ' and contains(@class,"categories")]/a/text()') if t and t.strip()]

        uncensored = self._uncensored(tree, number)
        self.uncensored = uncensored
        # 本站详情页无简介时（如 HEYZO 系列部分作品），回退 storyline 多源抓取
        outline = self._outline(tree) or self._storyline_fallback(number)

        dic = {
            'number': number,
            'title': title,
            'studio': self._by_href(tree, 'makersr'),
            'release': release,
            'year': release[:4] if release else '',
            'outline': outline,
            'runtime': runtime,
            'director': self._by_href(tree, 'director'),
            'actor': actors,
            'actor_photo': {},
            'cover': self._cover(html),
            'cover_small': '',
            'extrafanart': self._extrafanart(tree),
            'trailer': '',
            'tag': tags,
            'label': self._by_href(tree, 'issuer'),
            'series': '',
            'userrating': '',
            'uservotes': '',
            'uncensored': uncensored,
            'website': url,
            'source': self.source,
            'imagecut': self.imagecut,
        }
        return json.dumps(dic, ensure_ascii=False, sort_keys=True, separators=(',', ':'))

    def _storyline_fallback(self, number):
        """详情页本站无简介时，用 storyline 多源抓取兜底。

        用番号集合防递归：storyline 的 7mmtv 通道会再次进入 _scrape。
        """
        if number in _FALLBACK_NUMBERS:
            return ''
        _FALLBACK_NUMBERS.add(number)
        try:
            from .storyline import getStoryline
            return getStoryline(number, uncensored=self.uncensored,
                                proxies=self.proxies, verify=self.verify) or ''
        except Exception:
            return ''
        finally:
            _FALLBACK_NUMBERS.discard(number)

    # ------------------------------------------------------------------
    # 字段工具
    # ------------------------------------------------------------------
    @staticmethod
    def _collapsed(tree, xpath):
        return re.sub(r'\s+', ' ', ''.join(tree.xpath(xpath))).strip()

    @staticmethod
    def _by_href(tree, marker):
        """取详情面板里指定链接类型（director/makersr/issuer）的文本。"""
        values = tree.xpath(
            '//div[@class="col-auto flex-shrink-1 flex-grow-1"]'
            '/a[contains(@href,"%s")]/text()' % marker)
        for value in values:
            value = (value or '').strip()
            if value and value not in ('N/A', '----'):
                return value
        return ''

    @staticmethod
    def _cover(html):
        matched = _COVER_RE.search(html)
        if not matched:
            return ''
        url = matched.group(1).strip()
        if url and not url.startswith('http'):
            url = urljoin(_BASE, url)
        return url

    @staticmethod
    def _extrafanart(tree):
        lazy = [u.strip() for u in tree.xpath('//span/img[contains(@class,"lazyload")]/@data-src')
                if u and u.strip()]
        script = ''.join(tree.xpath(
            '//div[contains(@class,"fullvideo")]/script[@language="javascript"]/text()'))
        more = re.findall(r'https?://[^\s"\']+?\.jpe?g', script) if script else []
        return list(dict.fromkeys(lazy + more))

    @staticmethod
    def _runtime(text):
        """时长：支持「01:01:49」「147分」「90 min」等写法，输出分钟数。"""
        text = (text or '').strip()
        if ':' in text:
            parts = text.split(':')
            try:
                if len(parts) == 3:
                    return str(int(parts[0] or 0) * 60 + int(parts[1] or 0))
                if len(parts) == 2:
                    return str(int(parts[0] or 0))
            except ValueError:
                return ''
        matched = re.search(r'(\d+)\s*(?:分|min)', text)
        if matched:
            return matched.group(1)
        matched = re.search(r'\d+', text)
        return matched.group(0) if matched else ''

    @staticmethod
    def _outline(tree):
        nodes = tree.xpath('//div[contains(@class,"video-introduction-images-text")]')
        if not nodes:
            return ''
        parts = [re.sub(r'\s+', ' ', t).strip() for t in nodes[0].xpath('.//text()')]
        return '\n'.join([p for p in parts if p])

    @staticmethod
    def _uncensored(tree, number):
        """面包屑明确标记的优先；「中字/素人」类按番号规则兜底判定。"""
        crumb = ''
        nodes = tree.xpath('//ol[@class="breadcrumb"]')
        if nodes:
            crumb = nodes[0].xpath('string(.)')
        if any(k in crumb for k in ('無碼破解', '无码破解', '無碼AV', '无码AV', '國產影片', '国产影片')):
            return True
        if any(k in crumb for k in ('有碼AV', '有码AV')):
            return False
        try:
            return bool(is_uncensored(number))
        except Exception:
            return False
