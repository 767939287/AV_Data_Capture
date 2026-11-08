# -*- coding: utf-8 -*-

import json
import re

from . import httprequest
from .parser import Parser

# ``出演女優`` 字段可能用 / 、空白分隔
_ACTOR_SPLIT = re.compile(r'[/、\s]+')


def slugify(number):
    """番号 → 站点 slug：只保留字母数字并转小写（``FNS-257`` → ``fns257``）。"""
    return re.sub(r'[^0-9a-z]', '', (number or '').lower())


def number_from_slug(slug):
    """slug → 展示用番号：前导字母段后补横线（``dldss532`` → ``DLDSS-532``）。"""
    matched = re.match(r'^([a-z]+)(\d+)([a-z]*)$', (slug or '').lower())
    if not matched:
        return (slug or '').upper()
    return f'{matched.group(1).upper()}-{matched.group(2)}{matched.group(3).upper()}'


def _first_key(mapping, *keywords):
    """在 ACF 字典里找第一个非空字符串，兼容键名带空格/全角括号的变体。

    先精确匹配键名，再按包含匹配；``Amazon発売日`` / ``予約日`` 这类同后缀键必须排除，
    否则会取到它们（真实数据里是 2126 这种占位年份）。
    """
    if not isinstance(mapping, dict):
        return ''
    for keyword in keywords:
        value = mapping.get(keyword)
        if isinstance(value, str) and value.strip():
            return value.strip()
        for key, value in mapping.items():
            if keyword in key and not key.startswith(('Amazon', '予約')):
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return ''


def _parse_minutes(value):
    matched = re.search(r'\d+', value or '')
    return matched.group(0) if matched else ''


def _normalize_date(value):
    """``2026/11/5``、``2026-11-05``、``20261105`` 三种写法 → ``2026-11-05``。"""
    text = (value or '').strip()
    matched = re.search(r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})', text)
    if not matched:
        matched = re.fullmatch(r'(\d{4})(\d{2})(\d{2})', text)
    if not matched:
        return ''
    return f'{matched.group(1)}-{int(matched.group(2)):02d}-{int(matched.group(3)):02d}'


class WordPressWorks(Parser):
    """``works`` 主题厂牌官站的公共实现（dahlia-av.jp / faleno.jp）。

    检索与详情都用 WP REST ``/wp-json/wp/v2/works?slug=``：slug 精确匹配，不存在时返回空数组。
    不要改用 HTML 详情页判定存在性——站点对前缀命中但未命中的 slug 会 302 到别的作品。
    """

    rest_prefix = ''
    studio = ''
    default_publisher = ''
    publisher_prefixes = {}

    def extraInit(self):
        self.imagecut = 1

    def _api_url(self, slug):
        return f'{self.base_url}{self.rest_prefix}/wp-json/wp/v2/works?slug={slug}'

    def _page_url(self, slug):
        return f'{self.base_url}{self.rest_prefix}/works/{slug}/'

    def _publisher(self, slug):
        matched = re.match(r'^[a-z]+', (slug or '').lower())
        prefix = matched.group(0) if matched else ''
        return self.publisher_prefixes.get(prefix, self.default_publisher) or ''

    def _fetch_json(self, url):
        try:
            resp = httprequest.get(url, cookies=self.cookies, proxies=self.proxies,
                                   verify=self.verify, timeout=self.timeout or 20,
                                   extra_headers={'Accept': 'application/json'},
                                   return_type='object')
            if getattr(resp, 'status_code', 200) != 200:
                return None
            resp.encoding = 'utf-8'
            return json.loads(resp.text)
        except Exception:
            return None

    def search(self, number):
        self.number = number
        slug = slugify(number)
        if not slug:
            return 404
        posts = self._fetch_json(self._api_url(slug))
        if not isinstance(posts, list):
            return 404
        post = next((p for p in posts
                     if isinstance(p, dict) and (p.get('slug') or '').lower() == slug), None)
        if post is None:
            return 404
        return self._dictformat(post)

    def _dictformat(self, post):
        acf = post.get('acf') or {}
        images = post.get('acf_custom_images') or {}
        slug = (post.get('slug') or '').lower()

        actors_raw = _first_key(acf, '出演女優', '出演')
        actors = [x.strip() for x in _ACTOR_SPLIT.split(actors_raw) if x.strip()]

        title = _first_key(acf, '作品名')
        if not title:
            rendered = (post.get('title') or {}).get('rendered') or ''
            title = re.sub(r'<[^>]+>', '', rendered).strip()
        # 标题里常用全角空格分段，统一成半角并压缩空白
        title = re.sub(r'[\s\u3000]+', ' ', title).strip()
        # 站点把出演者拼在作品名末尾，与实际出演者一致时剥掉
        for name in actors:
            if title.endswith(name):
                title = title[: -len(name)].strip()
                break

        release = _normalize_date(_first_key(
            acf, '発売日（表示用）', '発売日 （表示用）', '配信開始日（表示用）', '配信開始日 （表示用）',
            '発売日', '配信開始日'))
        gallery = [u for u in (images.get('gallery_urls') or []) if isinstance(u, str) and u]

        dic = {
            'number': number_from_slug(slug),
            'title': title,
            'studio': self.studio,
            'release': release,
            'year': release[:4] if release else '',
            'outline': _first_key(acf, '作品紹介文'),
            'runtime': _parse_minutes(_first_key(acf, '収録時間')),
            'director': '',
            'actor': actors,
            'actor_photo': {},
            'cover': images.get('sub_photo_url') or '',
            'cover_small': images.get('main_photo_url') or '',
            'extrafanart': gallery,
            'trailer': '',
            'tag': [],
            'label': self._publisher(slug),
            'series': '',
            'userrating': '',
            'uservotes': '',
            'uncensored': False,
            'website': self._page_url(slug),
            'source': self.source,
            'imagecut': self.imagecut,
        }
        return json.dumps(dic, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
