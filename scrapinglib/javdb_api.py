# -*- coding: utf-8 -*-
"""JavDB 免 CF 镜像站通道（参考 MDCx 的 mdcx/crawlers/javdb_api.py 移植）。

MDCx 的 javdb_api 并非 JSON API，而是 javdb573/574/575 等镜像站的 HTML 直连刮削
（XPath 解析）。镜像站通常不受 Cloudflare 拦截，可作为主站的免 CF 替代通道。

本模块保留其选择器常量与「镜像轮换」思路，改写为本项目一致的同步 requests + lxml
实现，并继承 Parser 以复用 dictformat 输出结构，使上层（core.py / api.py）无需感知差异。
"""

import re
from urllib.parse import urlencode, urljoin

from lxml import etree

import config
from .parser import Parser

# 默认镜像站列表（可通过 config.ini 的 [javdb_channel] api_mirrors 覆盖）
DEFAULT_MIRRORS = (
    "https://javdb573.com",
    "https://javdb574.com",
    "https://javdb575.com",
)

# —— 搜索结果列表页 XPath ——
_XPATH_LIST_ITEM = "//a[@class='box']"
_XPATH_LIST_CODE = ".//div[@class='video-title']/strong/text()"
_XPATH_LIST_TITLE = ".//div[@class='video-title']/text()"

# —— 详情页 XPath ——
_XPATH_NUMBER = "//a[@class='button is-white copy-to-clipboard']/@data-clipboard-text"
_XPATH_TITLE = "//h2[@class='title is-4']/strong[@class='current-title']/text()"
_XPATH_ORIGIN_TITLE = "//h2[@class='title is-4']/span[@class='origin-title']/text()"
_XPATH_COVER = "//img[@class='video-cover']/@src"
_XPATH_EXTRAFANART = "//div[@class='tile-images preview-images']/a[@class='tile-item']/@href"
_XPATH_TRAILER = "//video[@id='preview-video']/source/@src"
_XPATH_SCORE = "//span[@class='score-stars']/.."

# 详情页信息面板字段（中/英双 locale 选择器，按序尝试）
_XPATH_PANEL = {
    "director": ('//strong[contains(text(),"導演:")]/../span/a/text()',
                 '//strong[contains(text(),"Director:")]/../span/a/text()'),
    "maker": ('//strong[contains(text(),"片商:")]/../span/a/text()',
              '//strong[contains(text(),"Maker:")]/../span/a/text()'),
    "publisher": ('//strong[contains(text(),"發行:")]/../span/a/text()',
                  '//strong[contains(text(),"Publisher:")]/../span/a/text()'),
    "series": ('//strong[contains(text(),"系列:")]/../span/a/text()',
               '//strong[contains(text(),"Series:")]/../span/a/text()'),
    "date": ('//strong[contains(text(),"日期:")]/../span/text()',
             '//strong[contains(text(),"Released Date:")]/../span/text()'),
    "duration": ('//strong[contains(text(),"時長")]/../span/text()',
                 '//strong[contains(text(),"Duration:")]/../span/text()'),
    "tags": ('//strong[contains(text(),"類別:")]/../span/a/text()',
             '//strong[contains(text(),"Tags:")]/../span/a/text()'),
}

# 演员：仅取女性（与本项目 [common] actor_gender 默认 female 对齐）
_XPATH_ACTOR_FEMALE = "//strong[contains(@class, 'female')]/preceding-sibling::a/text()"

# 镜像站 Cloudflare 拦截页特征
_CF_MARKERS = ("Just a moment", "ray-id", "cdn-cgi/challenge-platform")


class JavdbApi(Parser):
    """JavDB 镜像站通道刮削实现

    继承 Parser 以复用 dictformat 输出结构与请求封装（getHtml）；
    覆盖各字段方法以适配镜像站页面结构（与主站 javdb.py 的 XPath 不同）。
    """

    source = 'javdb'

    def extraInit(self):
        """ 自定义初始化：记录最近一次成功的镜像站

        为什么记录：镜像站常有个别域名失效，记住最近可用者可避免每次请求
        都从列表首个镜像开始重试，显著降低无谓等待。
        """
        self._successful_mirror = ""

    # ------------------------------------------------------------------
    # 配置与请求
    # ------------------------------------------------------------------
    def _channel_conf(self):
        """ 读取 [javdb_channel] 配置

        :return: JavdbChannelConf 实例；读取失败返回 None（调用方回退默认值）
        """
        try:
            return config.getInstance().javdb_channel()
        except Exception:
            return None

    def _mirrors(self):
        """ 返回可用镜像站列表（优先取配置，缺省用内置默认值）

        :return: 镜像站 base url 列表
        """
        conf = self._channel_conf()
        if conf and conf.mirrors:
            return list(conf.mirrors)
        return list(DEFAULT_MIRRORS)

    def updateCore(self, core):
        """ 从 core 同步请求参数，并补齐镜像站所需的 cookie

        :param core: 上层刮削上下文（含 proxies/verify/dbcookies 等）
        """
        super().updateCore(core)
        # 镜像站同样需要 over18 同意 cookie 才能正常返回搜索结果
        if core is not None and getattr(core, "dbcookies", None):
            self.cookies = core.dbcookies.get(self.source, {'over18': '1', 'locale': 'zh'})
        else:
            self.cookies = {'over18': '1', 'locale': 'zh'}
        # [javdb_channel] cookie 字段按 k=v;k=v 形式解析后合并进 cookies
        conf = self._channel_conf()
        if conf and conf.cookie:
            merged = dict(self.cookies or {})
            for pair in conf.cookie.split(';'):
                if '=' in pair:
                    k, v = pair.split('=', 1)
                    merged[k.strip()] = v.strip()
            self.cookies = merged

    def _fetch(self, url):
        """ 获取页面文本

        :param url: 目标 URL
        :return: 页面 HTML 文本；失败或 404 返回 None
        """
        try:
            page = self.getHtml(url)
        except Exception as e:
            if config.getInstance().debug():
                print(f"[-]javdb_api fetch failed: {url} {e}")
            return None
        if not page or page == 404:
            return None
        return page

    # ------------------------------------------------------------------
    # 搜索与 URL 解析
    # ------------------------------------------------------------------
    @staticmethod
    def _search_candidates(number: str) -> list:
        """ 生成搜索候选番号

        :param number: 原始番号
        :return: 去重保序的候选列表（原样 + 紧凑形式 + FC2 变体）
        """
        raw = str(number or "").strip()
        variants = [raw] if raw else []
        compact = re.sub(r"[\s\-_]+", "", raw).upper()
        if compact and compact not in variants:
            variants.append(compact)
        if compact.startswith("FC2"):
            digits = re.sub(r"\D", "", compact.replace("FC2PPV", ""))
            if digits:
                for v in ("FC2-" + digits, digits):
                    if v not in variants:
                        variants.append(v)
        return [v for v in variants if v]

    @staticmethod
    def _number_key(value: str) -> str:
        """ 番号归一化：大写并去除空白/横杠/下划线

        :param value: 原始番号或标题
        :return: 归一化后的 key
        """
        return re.sub(r"[\s\-_]+", "", str(value or "").upper())

    def _match_number(self, candidate: str, number: str) -> bool:
        """ 判断候选项是否与目标番号等价（归一化后相等或以目标为前缀）

        :param candidate: 候选项（番号或标题）
        :param number: 目标番号
        :return: 匹配返回 True
        """
        a = self._number_key(candidate)
        b = self._number_key(number)
        return bool(a) and bool(b) and (a == b or a.startswith(b))

    def _match_detail_url(self, page: str, number: str, base: str) -> str:
        """ 从搜索结果页匹配目标番号并返回详情页绝对 URL

        :param page: 搜索结果页 HTML
        :param number: 目标番号
        :param base: 当前镜像站 base url（用于补全相对链接）
        :return: 详情页 URL；未匹配返回空串
        """
        try:
            tree = etree.fromstring(page, etree.HTMLParser())
        except Exception:
            return ""
        items = tree.xpath(_XPATH_LIST_ITEM)
        if not items:
            return ""
        info = []
        for each in items:
            href = each.get("href") or ""
            code = "".join(each.xpath(_XPATH_LIST_CODE)).strip()
            title = "".join(each.xpath(_XPATH_LIST_TITLE)).strip()
            if href:
                info.append((href, code, title))
        # 第一轮：番号/标题精确匹配（与原项目 javdb 的匹配策略一致，避免误取无关结果）
        for href, code, title in info:
            if self._match_number(code, number) or self._match_number(title, number):
                return urljoin(base, href)
        # 第二轮：宽松匹配，允许目标番号出现在「番号+标题」组合串中
        target = self._number_key(number)
        for href, code, title in info:
            if target and target in self._number_key(code + title):
                return urljoin(base, href)
        return ""

    def queryNumberUrl(self, number):
        """ 在镜像站搜索番号并返回详情页 URL

        :param number: 番号
        :return: 详情页 URL；全部镜像均失败返回空串

        轮换策略：优先从最近成功的镜像开始，逐个镜像尝试；每个镜像下遍历候选番号。
        """
        mirrors = self._mirrors()
        if self._successful_mirror and self._successful_mirror in mirrors:
            mirrors.remove(self._successful_mirror)
            mirrors.insert(0, self._successful_mirror)
        for base in mirrors:
            for candidate in self._search_candidates(number):
                search_url = f"{base}/search?{urlencode({'f': 'all', 'q': candidate, 'page': '1'})}"
                page = self._fetch(search_url)
                if not page:
                    continue
                # 命中 Cloudflare 拦截页说明该镜像不可用，换下一个镜像（镜像站一般不会走 CF）
                if any(marker in page for marker in _CF_MARKERS):
                    if config.getInstance().debug():
                        print(f"[-]javdb_api mirror blocked by CF: {base}")
                    break
                detail = self._match_detail_url(page, number, base)
                if detail:
                    self._successful_mirror = base
                    return detail
        return ""

    # ------------------------------------------------------------------
    # 详情页字段解析
    # ------------------------------------------------------------------
    def getNum(self, htmltree):
        """ 番号：取详情页复制按钮的文本，缺失时回退到传入番号
        """
        try:
            value = self.getTreeElement(htmltree, _XPATH_NUMBER)
        except Exception:
            value = ""
        return (value or "").strip() or self.number

    def getTitle(self, htmltree):
        """ 标题：优先中文标题，缺失时取原文（日文）标题
        """
        return self.getTreeElementbyExprs(htmltree, _XPATH_TITLE, _XPATH_ORIGIN_TITLE).strip()

    def getCover(self, htmltree):
        """ 封面：详情页主图
        """
        return self.getTreeElementbyExprs(htmltree, _XPATH_COVER).strip()

    def getSmallCover(self, htmltree):
        """ 小封面：镜像站无独立小图，返回空串（同时避免空 XPath 触发解析异常）
        """
        return ''

    def getExtrafanart(self, htmltree) -> list:
        """ 剧照：详情页预览图链接列表
        """
        return [x.strip() for x in self.getTreeAll(htmltree, _XPATH_EXTRAFANART) if x and x.strip()]

    def getTrailer(self, htmltree):
        """ 预告片：协议相对地址（// 开头）补全为 https
        """
        url = self.getTreeElementbyExprs(htmltree, _XPATH_TRAILER).strip()
        if url and not url.startswith("http"):
            url = "https:" + url
        return url

    def getDirector(self, htmltree):
        """ 导演
        """
        return self.getTreeElementbyExprs(htmltree, *_XPATH_PANEL["director"]).strip()

    def getStudio(self, htmltree):
        """ 片商
        """
        return self.getTreeElementbyExprs(htmltree, *_XPATH_PANEL["maker"]).strip()

    def getSeries(self, htmltree):
        """ 系列
        """
        return self.getTreeElementbyExprs(htmltree, *_XPATH_PANEL["series"]).strip()

    def getLabel(self, htmltree):
        """ 厂牌：镜像站以「發行」字段作为 label
        """
        return self.getTreeElementbyExprs(htmltree, *_XPATH_PANEL["publisher"]).strip()

    def getRelease(self, htmltree):
        """ 发行日期
        """
        return self.getTreeElementbyExprs(htmltree, *_XPATH_PANEL["date"]).strip().replace("/", "-")

    def getRuntime(self, htmltree):
        """ 时长（分钟）：移除中/英单位后缀
        """
        text = self.getTreeElementbyExprs(htmltree, *_XPATH_PANEL["duration"])
        for unit in ("分鍾", "分鐘", " 分", " minute(s)", "min"):
            text = text.replace(unit, "")
        return text.strip()

    def getTags(self, htmltree) -> list:
        """ 标签：中/英 locale 结果合并去重并保序
        """
        result = []
        for expr in _XPATH_PANEL["tags"]:
            for t in self.getTreeAll(htmltree, expr):
                t = (t or "").strip()
                if t and t not in result:
                    result.append(t)
        return result

    def getActors(self, htmltree) -> list:
        """ 演员：默认仅女性（与项目 [common] actor_gender 默认 female 对齐）
        """
        return [x.strip() for x in self.getTreeAll(htmltree, _XPATH_ACTOR_FEMALE) if x and x.strip()]

    def getUserRating(self, htmltree):
        """ 评分：从 score-stars 容器的拼接文本中提取首个数字

        为什么用 string(...)：详情页为 pretty-printed HTML，容器首个文本子节点是换行空白，
        直接取首节点会拿到空串导致评分漏抓。
        """
        try:
            text = htmltree.xpath("string(" + _XPATH_SCORE + ")")
            match = re.search(r"(\d+(?:\.\d+)?)", text or "")
            return float(match.group(1)) if match else ''
        except Exception:
            return ''

    def getOutline(self, htmltree):
        """ 简介：镜像站详情页通常无简介，统一交由 storyline 机制补全
        """
        return ''
