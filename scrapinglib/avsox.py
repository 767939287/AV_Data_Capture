# -*- coding: utf-8 -*-

import json
import re
from urllib.parse import quote

from . import httprequest
from .parser import Parser

# 名称实体（studio/star/genre/director/label/series）的多语言后缀，按优先级取第一个非空值
_NAME_SUFFIXES = ('', '_ja', '_cn', '_tw', '_en')


class Avsox(Parser):
    """AVSOX 刮削器（avsox.click）。

    站点前端已改为 Vue SPA，壳页 HTML 里没有影片 DOM，元数据全部来自同源 Yii JSON API：
      1. GET  ``/{lang}/`` 取 ``<meta name="csrf-token">``；
      2. POST ``/javu/data/api/search``    body 为 ``[{"search": 番号, "lang": "cn"}, 60, 1]``；
      3. POST ``/javu/data/api/getMovie``  body 为 ``[movieId, "cn"]``。

    站点 HTTPS 证书长期异常（TLS alert internal error），HTTP 可正常服务，故统一走 http。
    """

    source = 'avsox'
    base_url = 'http://avsox.click'
    lang = 'cn'

    def extraInit(self):
        self.imagecut = 0
        self.originalnum = ''
        self._token = None
        self._movie_id = ''

    # ------------------------------------------------------------------
    # 请求层
    # ------------------------------------------------------------------
    def _csrf_token(self):
        """取首页 csrf-token（实例内缓存，避免每次刮削重复请求）。"""
        if self._token:
            return self._token
        try:
            html = httprequest.get(f'{self.base_url}/{self.lang}/', cookies=self.cookies,
                                   proxies=self.proxies, verify=self.verify)
        except Exception:
            return None
        matched = re.search(r'<meta name="csrf-token" content="([^"]+)"', html or '')
        self._token = matched.group(1) if matched else None
        return self._token

    def _api(self, method, payload, referer):
        """调用 javu JSON API，返回 envelope 里的 data；失败返回 None。"""
        headers = {
            'Accept': 'application/json, text/plain, */*',
            'Content-Type': 'application/json',
            'Origin': self.base_url,
            'Referer': referer,
            'X-Requested-With': 'XMLHttpRequest',
        }
        token = self._csrf_token()
        if token:
            headers['X-CSRF-Token'] = token
        url = f'{self.base_url}/javu/data/api/{method}'
        try:
            # httprequest.post 在不指定 return_type 时返回 Response 对象，这里显式取文本
            resp = httprequest.post(url, data=json.dumps(payload, ensure_ascii=False),
                                    cookies=self.cookies, extra_headers=headers,
                                    proxies=self.proxies, verify=self.verify,
                                    return_type='object')
            text = resp.text if hasattr(resp, 'text') else resp
            envelope = json.loads(text)
        except Exception:
            return None
        if not isinstance(envelope, dict) or envelope.get('code') != 200:
            return None
        return envelope.get('data')

    # ------------------------------------------------------------------
    # 查询与解析
    # ------------------------------------------------------------------
    def search(self, number):
        self.number = number
        movie = self._search_movie(number)
        if not movie:
            return 404
        return self._dictformat(movie)

    def _search_movie(self, number):
        referer = f'{self.base_url}/{self.lang}/search/{quote(number, safe="")}'
        results = self._api('search', [{'search': number, 'lang': self.lang}, 60, 1], referer)
        if not isinstance(results, list):
            return None
        movie_id = self._pick_movie_id(results, number)
        if not movie_id:
            return None
        self._movie_id = movie_id
        movie = self._api('getMovie', [movie_id, self.lang],
                          f'{self.base_url}/{self.lang}/movies/{movie_id}')
        if not isinstance(movie, dict) or not movie.get('movieFanHao'):
            return None
        return movie

    @staticmethod
    def _pick_movie_id(movies, number):
        """优先精确匹配番号，其次忽略分隔符（-/_）匹配；都不中则不取首条，避免刮错。"""
        exact = number.casefold()
        folded = re.sub(r'[-_\s]', '', number).casefold()
        folded_hit = None
        for item in movies:
            if not isinstance(item, dict):
                continue
            fanhao = item.get('movieFanHao')
            movie_id = item.get('movieId')
            if not isinstance(fanhao, str) or not isinstance(movie_id, str) or not movie_id:
                continue
            if fanhao.casefold() == exact:
                return movie_id
            if folded_hit is None and re.sub(r'[-_\s]', '', fanhao).casefold() == folded:
                folded_hit = movie_id
        return folded_hit

    @staticmethod
    def _named(entity, prefix):
        """从 studio/star/genre/... 实体取多语言名称。"""
        if not isinstance(entity, dict):
            return ''
        for suffix in _NAME_SUFFIXES:
            value = entity.get(f'{prefix}Name{suffix}')
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ''

    @staticmethod
    def _first(*values):
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ''

    def _dictformat(self, movie):
        """把 API 的 movie 对象映射为项目统一的 JSON 字段。"""
        actors = []
        actor_photo = {}
        for star in movie.get('star') or []:
            name = self._named(star, 'star')
            if not name:
                continue
            actors.append(name)
            avatar = star.get('avatarUrl')
            if isinstance(avatar, str) and avatar.strip():
                actor_photo[name] = avatar.strip()

        release = movie.get('releaseDate') or ''
        runtime = movie.get('length')
        dic = {
            'number': movie.get('movieFanHao') or self.number,
            'title': self._first(movie.get('title'), movie.get('title_ja'), movie.get('title_cn'),
                                 movie.get('title_tw'), movie.get('title_en')),
            'studio': self._named(movie.get('studio'), 'studio'),
            'release': release,
            'year': release[:4] if release else '',
            'outline': self._first(movie.get('description'), movie.get('description_cn'),
                                   movie.get('description_ja'), movie.get('description_en'),
                                   movie.get('description_tw')),
            'runtime': str(runtime) if runtime else '',
            'director': self._named(movie.get('director'), 'director'),
            'actor': actors,
            'actor_photo': actor_photo,
            'cover': movie.get('posterSmall') or '',
            'cover_small': movie.get('posterLarge') or '',
            'extrafanart': [u for u in (movie.get('sampleLarge') or []) if isinstance(u, str) and u],
            'trailer': '',
            'tag': [self._named(genre, 'genre') for genre in (movie.get('genre') or [])
                    if self._named(genre, 'genre')],
            'label': self._named(movie.get('label'), 'label'),
            'series': self._named(movie.get('series'), 'series'),
            'userrating': '',
            'uservotes': '',
            'uncensored': True,
            'website': f'{self.base_url}/{self.lang}/movies/{self._movie_id}' if self._movie_id else '',
            'source': self.source,
            'imagecut': self.imagecut,
        }
        return json.dumps(dic, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
