# -*- coding: utf-8 -*-

import json
import re
from urllib.parse import urljoin

from lxml import etree

from . import httprequest
from .parser import Parser

# 商品页链接固定指向 /product/index.php
_PRODUCT_PATH = '/product/index.php'
# 年龄确认入口：带着旧会话访问时只删会话，最多两次才能领到新会话
_AGE_GATE_PATH = '/cookie_set.php'
_SESSION_COOKIE = 'PHPSESSID'
_AGE_GATE_ATTEMPTS = 2


class Giga(Parser):
    """GIGA 刮削器（giga-web.jp）。

    站点对未通过年龄确认的会话一律返回年龄页（状态码仍是 200）。确认入口
    ``/cookie_set.php`` 会下发 ``PHPSESSID``，因此这里用一个持久会话先领取会话，
    再带 ``Referer`` 取检索页与商品页。直接访问在当前网络下会被 403 拦截，需要代理。
    """

    source = 'giga'
    base_url = 'https://www.giga-web.jp'

    def extraInit(self):
        self.imagecut = 1
        self._session = None
        self._session_claimed = False

    # ------------------------------------------------------------------
    # 会话与请求
    # ------------------------------------------------------------------
    def _get_session(self):
        if self._session is None:
            self._session = httprequest.request_session(
                cookies=self.cookies, proxies=self.proxies, verify=self.verify, retry=0,
                timeout=self.timeout or 20)
        if not self._session_claimed:
            self._claim_session()
        return self._session

    def _claim_session(self):
        """访问年龄确认入口领取会话；带着旧会话时第一次只删会话，故最多试两次。"""
        for _ in range(_AGE_GATE_ATTEMPTS):
            try:
                resp = self._session.get(self.base_url + _AGE_GATE_PATH,
                                         headers={'Referer': self.base_url + '/top.php'},
                                         allow_redirects=False, timeout=self.timeout or 20)
            except Exception:
                return
            cookie = resp.headers.get('set-cookie') or ''
            if f'{_SESSION_COOKIE}=' in cookie and f'{_SESSION_COOKIE}=deleted' not in cookie:
                self._session_claimed = True
                return

    def _get_html(self, url):
        session = self._get_session()
        try:
            resp = session.get(url, headers={'Referer': self.base_url + '/top.php'},
                               timeout=self.timeout or 20)
        except Exception:
            return 404
        if resp.status_code != 200 or not resp.text:
            return 404
        # 未通过年龄确认时会整页换成确认表单
        if 'cookie_set.php' in resp.text:
            return 404
        return resp.text

    # ------------------------------------------------------------------
    # 查询与解析
    # ------------------------------------------------------------------
    def search(self, number):
        self.number = number
        if self.specifiedUrl:
            return self._scrape(self.specifiedUrl)
        html_code = self._get_html(f'{self.base_url}/search/?keyword={number}')
        if html_code == 404:
            return 404
        tree = etree.fromstring(html_code, etree.HTMLParser())
        urls = [urljoin(self.base_url, href) for href in
                tree.xpath('//div[contains(@class,"thumBox")]//a/@href')
                if _PRODUCT_PATH in href]
        for url in urls:
            result = self._scrape(url)
            if result != 404 and self._same_number(json.loads(result).get('number'), number):
                return result
        return 404

    @staticmethod
    def _same_number(found, number):
        norm = lambda v: re.sub(r'[^0-9a-z]', '', (v or '').lower())
        return bool(found) and norm(found) == norm(number)

    def _scrape(self, url):
        html_code = self._get_html(url)
        if html_code == 404:
            return 404
        tree = etree.fromstring(html_code, etree.HTMLParser())

        title = ''.join(tree.xpath('//div[@id="works_pic"]//h5//text()')).strip()
        if not title:
            return 404

        def field(label):
            value = tree.xpath(f'//div[@id="works_txt"]//dt[normalize-space(text())="{label}"]'
                               f'/following-sibling::dd[1]//text()')
            return ' '.join(x.strip() for x in value if x.strip()).strip()

        def links(label, href_part):
            return [x.strip() for x in tree.xpath(
                f'//div[@id="works_txt"]//dt[normalize-space(text())="{label}"]'
                f'/following-sibling::dd[1]//a[contains(@href,"{href_part}")]/text()') if x.strip()]

        number = field('作品番号')
        actors = links('出演女優', 'actor_id')
        director = field('監督')
        runtime = re.search(r'\d+', field('収録時間'))
        # 标签写作「DVDリリース日」（无空格，部分页面为「DVD リリース日」）
        release = ''
        for label in ('DVDリリース日', 'DVD リリース日', '発売日'):
            release = field(label)
            if release:
                break
        release_match = re.search(r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})', release)
        release = (f'{release_match.group(1)}-{int(release_match.group(2)):02d}-'
                   f'{int(release_match.group(3)):02d}') if release_match else ''
        series = field('シリーズ')
        # 故事在 #story_list2（展开全文），缺失时用 #story_list1（截断版）
        outline = ''
        for story_id in ('story_list2', 'story_list1'):
            text = tree.xpath(f'//div[@id="{story_id}"]//li[contains(@class,"story_window")]//text()')
            outline = ' '.join(x.strip() for x in text if x.strip()).strip()
            if outline:
                break

        covers = [u for u in tree.xpath('//div[@id="works_pic"]//img/@src') if u]
        cover = urljoin(self.base_url, covers[0].replace('pac_s', 'pac_l')) if covers else ''
        extrafanart = [urljoin(self.base_url, u) for u in
                       tree.xpath('//div[@class="gasatsu_images_pc"]//a/@href') if u]

        dic = {
            'number': number or self.number,
            'title': title,
            'studio': 'GIGA',
            'release': release,
            'year': release[:4] if release else '',
            'outline': outline,
            'runtime': runtime.group(0) if runtime else '',
            'director': director,
            'actor': actors,
            'actor_photo': {},
            'cover': cover,
            'cover_small': '',
            'extrafanart': extrafanart,
            'trailer': '',
            'tag': [],
            'label': 'GIGA',
            'series': series,
            'userrating': '',
            'uservotes': '',
            'uncensored': False,
            'website': url,
            'source': self.source,
            'imagecut': self.imagecut,
        }
        return json.dumps(dic, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
