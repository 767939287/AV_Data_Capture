# -*- coding: utf-8 -*-

import json
import re
from datetime import datetime
from urllib.parse import quote, urljoin

from lxml import etree

from .parser import Parser

# JS Date.toString(): "Thu Feb 18 2021 09:00:00 GMT+0900 (Japan Standard Time)"，星期前缀可选
_JS_DATE = re.compile(r'^(?:[A-Za-z]{3} )?(?P<mon>[A-Za-z]{3}) (?P<day>\d{1,2}) (?P<year>\d{4})')
_CLOCK = re.compile(r'^(?P<h>\d+):(?P<min>\d{2}):(?P<sec>\d{2})$')
_DASHABLE = re.compile(r'^(?P<head>.*[A-Za-z])(?P<digits>\d+)$')
_MONTHS = {m: i for i, m in enumerate(
    ('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'), start=1)}


def work_key(value):
    """番号归一：去分隔符与大小写差异，并消除数字段前导零（SSIS-049 与 SSIS-49 同一部）。"""
    compact = re.sub(r'[^0-9a-z]+', '', (value or '').casefold())
    return ''.join(str(int(p)) if p.isdigit() else p for p in re.findall(r'\d+|[a-z]+', compact))


def query_forms(number):
    """检索候选形态：原样 → 去分隔符 → 补短横线。"""
    forms = []
    for form in (number, number.replace('-', '').replace(' ', '')):
        if form and form not in forms:
            forms.append(form)
    if '-' not in number:
        matched = _DASHABLE.fullmatch(number.replace(' ', ''))
        if matched and (dashed := f'{matched.group("head")}-{matched.group("digits")}') not in forms:
            forms.append(dashed)
    return forms


class Avbase(Parser):
    """AVBASE 刮削器（avbase.net）。

    数据在内嵌的 Next.js ``__NEXT_DATA__`` 里（站点无公开 API）。检索 ``/works?q=`` 是模糊
    匹配，因此只接受 ``work_id`` 归一后相等的候选，不取首条；详情页是 ``/works/<work_id>``
    （部分作品带 ``<prefix>:`` 前缀）。站点在部分网络下需要 Cloudflare 绕过服务。
    """

    source = 'avbase'
    base_url = 'https://www.avbase.net'

    def extraInit(self):
        self.imagecut = 1

    def search(self, number):
        self.number = number
        if self.specifiedUrl:
            return self._scrape(self.specifiedUrl)
        key = work_key(number)
        if not key:
            return 404
        for variant in self._number_variants(number):
            for form in query_forms(variant):
                url = self._search_form(form, key)
                if url:
                    return self._scrape(url)
        return 404

    @staticmethod
    def _number_variants(number):
        """站内部分条目省略开头数字段，FC2 则可能带 PPV，几种形态都试。"""
        compact = re.sub(r'[^0-9a-zA-Z]', '', number)
        variants = [number, compact]
        if re.fullmatch(r'\d+[A-Za-z].*', compact):
            variants.append(re.sub(r'^\d+', '', compact))
        article_id = re.sub(r'(?i)^fc2[-_]?(?:ppv[-_]?)?', '', number).strip()
        if article_id.isdigit():
            variants.extend([f'FC2-PPV-{article_id}', f'FC2-{article_id}'])
        return list(dict.fromkeys(v for v in variants if v))

    def _search_form(self, term, key):
        html = self.getHtml(f'{self.base_url}/works?q={quote(term)}')
        if html == 404:
            return None
        props = self._next_props(html)
        works = props.get('works') if props else None
        if not isinstance(works, list):
            return None
        picked = self._pick(works, key)
        if not picked:
            return None
        prefix = (picked.get('prefix') or '').strip()
        work_id = (picked.get('work_id') or '').strip()
        if not work_id:
            return None
        path = f'{quote(prefix, safe="")}:{quote(work_id, safe="")}' if prefix else quote(work_id, safe='')
        return f'{self.base_url}/works/{path}'

    @staticmethod
    def _pick(works, key):
        """精确命中优先；只差开头数字段时该 work_id 必须唯一，避免刮错。"""
        exact, loose = [], {}
        for work in works:
            if not isinstance(work, dict):
                continue
            found = work_key(work.get('work_id'))
            if found == key:
                exact.append(work)
            elif found and key.endswith(found):
                loose.setdefault(found, []).append(work)
        if exact:
            return exact[0]
        return next(iter(loose.values()))[0] if len(loose) == 1 else None

    @staticmethod
    def _next_props(html):
        matched = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
        if not matched:
            return None
        try:
            return json.loads(matched.group(1))['props']['pageProps']
        except Exception:
            return None

    def _scrape(self, url):
        html = self.getHtml(url)
        if html == 404:
            return 404
        props = self._next_props(html)
        work = props.get('work') if props else None
        if not isinstance(work, dict) or not work.get('work_id'):
            return 404

        products = [p for p in (work.get('products') or []) if isinstance(p, dict)]
        product = self._image_product(products)
        iteminfo = (product or {}).get('iteminfo') or {}

        casts = work.get('casts') or work.get('actors') or []
        actors = []
        for cast in casts:
            entry = cast.get('actor') if isinstance(cast, dict) and 'actor' in cast else cast
            if isinstance(entry, dict) and entry.get('name'):
                actors.append(entry['name'])

        genres = work.get('genres') or work.get('tags') or []
        tags = [g.get('name') for g in genres if isinstance(g, dict) and g.get('name')]

        samples = []
        for image in (product or {}).get('sample_image_urls') or []:
            if isinstance(image, dict) and (image.get('l') or image.get('s')):
                samples.append(image.get('l') or image.get('s'))

        release = self._parse_js_date(work.get('min_date'))
        dic = {
            'number': work.get('work_id') or self.number,
            'title': (work.get('title') or '').strip(),
            'studio': self._named(product, 'maker'),
            'release': release,
            'year': release[:4] if release else '',
            'outline': (iteminfo.get('description') or '').strip(),
            'runtime': self._runtime(iteminfo.get('volume')),
            'director': (iteminfo.get('director') or '').strip(),
            'actor': actors,
            'actor_photo': {},
            'cover': (product or {}).get('image_url') or '',
            'cover_small': (product or {}).get('thumbnail_url') or '',
            'extrafanart': samples,
            'trailer': (product or {}).get('sample_movie_url') or '',
            'tag': tags,
            'label': self._named(product, 'label'),
            'series': self._named(product, 'series'),
            'userrating': '',
            'uservotes': '',
            'uncensored': False,
            'website': url,
            'source': self.source,
            'imagecut': self.imagecut,
        }
        return json.dumps(dic, ensure_ascii=False, sort_keys=True, separators=(',', ':'))

    @staticmethod
    def _named(product, field):
        if not isinstance(product, dict):
            return ''
        value = product.get(field)
        if isinstance(value, dict):
            return (value.get('name') or '').strip()
        return ''

    @staticmethod
    def _image_product(products):
        """优先取同时有封面与缩略图的商品，其余按顺序兜底。"""
        for product in products:
            if product.get('image_url') and product.get('thumbnail_url'):
                return product
        return products[0] if products else None

    @staticmethod
    def _parse_js_date(value):
        if not isinstance(value, str):
            return ''
        matched = _JS_DATE.match(value.strip())
        if not matched:
            return ''
        month = _MONTHS.get(matched.group('mon').lower())
        if not month:
            return ''
        try:
            return datetime(int(matched.group('year')), month, int(matched.group('day'))).strftime('%Y-%m-%d')
        except ValueError:
            return ''

    @staticmethod
    def _runtime(volume):
        """iteminfo.volume 有分钟数字串与时长时钟串两种形态。"""
        if not volume:
            return ''
        text = str(volume).strip()
        clock = _CLOCK.match(text)
        if clock:
            return str(int(clock.group('h')) * 60 + int(clock.group('min')))
        matched = re.search(r'\d+', text)
        return matched.group(0) if matched else ''
