# -*- coding: utf-8 -*-

import re
from urllib.parse import urljoin, urlsplit
from lxml import etree

import config
from .httprequest import request_session, get_html_by_scraper
from . import tls_request
from .parser import Parser

# javdb 对「声明要 HTML」的请求会 302 到 /login（登录墙，实测 2026-11）。
# 首选 curl_cffi 做 TLS 指纹伪装（行为等同真浏览器）；未安装/未启用时回退
# cloudscraper + Accept: */*（声明 */* 不触发服务端的 JS 校验路径）。
_ACCEPT_ANY = {"Accept": "*/*"}

# 登录页内容判据（借鉴 amane / mdcz）：登录表单自身的 action 地址。
# 覆盖「状态码 200 但内容就是登录表单」的情况（URL 可能不带 /login）。
# mdcz 原文：JavDB redirects FC2 and uncensored titles to its sign-in page unless
# the cookie belongs to a signed-in account.
_LOGIN_MARKER = 'action="/user_sessions"'

# 落地页判定结果
_C_OK = "OK"
_C_CF = "CF"
_C_LOGIN = "LOGIN"
_C_HIJACK = "HIJACK"
_C_REGION = "REGION_BLOCKED"
_C_IPBAN = "IP_BANNED"
_C_EMPTY = "EMPTY"
_C_NOMATCH = "NOMATCH"
_C_ERROR = "ERROR"

# 屏蔽页判据（借鉴 mdcz 的 classifyBlockedPage）：区分「地区限制」与「IP 被封」，
# 两者都是 200 状态的非正常页，靠文案识别。
_BLOCK_MARKERS = (
    (_C_REGION, "Due to copyright restrictions"),
    (_C_IPBAN, "banned your access"),
)


class Javdb(Parser):
    source = 'javdb'

    # 新版详情页把番号放在「复制」按钮的 data 属性上；保留旧版文本节点作为兜底
    expr_number = '//a[contains(@class,"copy-to-clipboard")]/@data-clipboard-text'
    expr_number2 = '//strong[contains(text(),"番號")]/../span/text()'
    expr_title = "/html/head/title/text()"
    expr_title_no = '//*[contains(@class,"movie-list")]/div/a/div[contains(@class, "video-title")]/strong/text()'
    expr_runtime = '//strong[contains(text(),"時長")]/../span/text()'
    expr_runtime2 = '//strong[contains(text(),"時長")]/../span/a/text()'
    expr_uncensored = '//strong[contains(text(),"類別")]/../span/a[contains(@href,"/tags/uncensored?") or contains(@href,"/tags/western?")]'
    expr_actor = '//span[@class="value"]/a[contains(@href,"/actors/")]/text()'
    # 女优链接带 actor-female，男优为普通 a（旧版则用 strong 的 symbol 图标）
    expr_actor2 = '//span[@class="value"]/a[contains(@href,"/actors/")]/@class'
    expr_release = '//strong[contains(text(),"日期")]/../span/text()'
    expr_release_no = '//*[contains(@class,"movie-list")]/div/a/div[contains(@class, "meta")]/text()'
    expr_studio = '//strong[contains(text(),"片商")]/../span/a/text()'
    expr_studio2 = '//strong[contains(text(),"賣家:")]/../span/a/text()'
    expr_director = '//strong[contains(text(),"導演")]/../span/text()'
    expr_director2 = '//strong[contains(text(),"導演")]/../span/a/text()'
    expr_cover = "//div[contains(@class, 'column-video-cover')]/a/img/@src"
    expr_cover2 = "//div[contains(@class, 'column-video-cover')]/img/@src"
    expr_cover_no = '//*[contains(@class,"movie-list")]/div/a/div[contains(@class, "cover")]/img/@src'
    expr_trailer = '//span[contains(text(),"預告片")]/../../video/source/@src'
    expr_extrafanart = "//article[@class='message video-panel']/div[@class='message-body']/div[@class='tile-images preview-images']/a[contains(@href,'/samples/')]/@href"
    expr_tags = '//strong[contains(text(),"類別")]/../span/a/text()'
    expr_tags2 = '//strong[contains(text(),"類別")]/../span/text()'
    expr_series = '//strong[contains(text(),"系列")]/../span/text()'
    expr_series2 = '//strong[contains(text(),"系列")]/../span/a/text()'
    expr_label = '//strong[contains(text(),"系列")]/../span/text()'
    expr_label2 = '//strong[contains(text(),"系列")]/../span/a/text()'
    expr_userrating = '//span[@class="score-stars"]/../text()'
    expr_uservotes = '//span[@class="score-stars"]/../text()'
    expr_actorphoto = '//strong[contains(text(),"演員:")]/../span/a[starts-with(@href,"/actors/")]'

    def extraInit(self):
        self.fixstudio = False
        self.noauth = False
        # 三通道门面：缓存子通道实现实例，避免每次 search 重复构造
        self._impl_cache = {}
        self._core = None
        # web 通道的站点候选 [(host, cookie_key), ...]，由 core.dbsites 注入
        self.dbsites = []
        # 本次是否由「去除纯数字前缀」的宽松匹配命中（命中时 nfo 仍写原始番号）
        self._number_loose = False

    def updateCore(self, core):
        # 保存 core 引用，供 api/app 通道在分派时重新初始化子通道使用
        self._core = core
        # 直接调用 scrape(number, None) 时没有 core，使用默认 cookie/站点即可
        if core is None:
            self.cookies = {'over18': '1', 'theme': 'auto', 'locale': 'zh'}
            self.dbsite = 'javdb'
            self._merge_channel_cookie()
            return
        if core.proxies:
            self.proxies = core.proxies
        if core.verify:
            self.verify = core.verify
        if core.morestoryline:
            self.morestoryline = True
        if core.specifiedSource == self.source:
            self.specifiedUrl = core.specifiedUrl
        # special
        if core.dbcookies:
            self.cookies = core.dbcookies.get(self.source, {'over18':'1', 'theme':'auto', 'locale':'zh'})
        else:
            self.cookies =  {'over18':'1', 'theme':'auto', 'locale':'zh'}
        if core.dbsite:
            self.dbsite = core.dbsite
        else:
            self.dbsite = 'javdb'
        # 站点候选顺序（原站优先），供失败时自动换站
        self.dbsites = list(getattr(core, 'dbsites', None) or [])
        self._merge_channel_cookie()

    def _merge_channel_cookie(self):
        """把 [javdb_channel] cookie 合并进当前 cookies（web/api/app 三通道统一）"""
        conf = self._channel_conf()
        if not conf or not conf.cookie:
            return
        merged = dict(self.cookies or {})
        for pair in conf.cookie.split(';'):
            if '=' in pair:
                k, v = pair.split('=', 1)
                merged[k.strip()] = v.strip()
        self.cookies = merged

    # ------------------------------------------------------------------
    # 三通道门面（参考 MDCx 的 web / api / app 三通道设计）
    # ------------------------------------------------------------------
    def _channel_conf(self):
        """ 读取 [javdb_channel] 配置

        :return: JavdbChannelConf 实例；读取失败返回 None（回退单通道 web 行为）
        """
        try:
            return config.getInstance().javdb_channel()
        except Exception:
            return None

    def _get_impl(self, name):
        """ 按需构造并缓存通道实现实例

        :param name: 通道名（api / app）
        :return: 通道实现实例；不支持的通道返回 None
        """
        if name in self._impl_cache:
            return self._impl_cache[name]
        impl = None
        if name == 'api':
            from .javdb_api import JavdbApi
            impl = JavdbApi()
        elif name == 'app':
            from .javdb_app import JavdbApp
            impl = JavdbApp()
        self._impl_cache[name] = impl
        return impl

    def _run_channel(self, name, number):
        """ 执行指定通道的刮削

        :param name: 通道名（web / api / app）
        :param number: 番号
        :return: 统一字段字典（JSON 字符串）或 404
        """
        if name == 'web':
            return self._search_web(number)
        impl = self._get_impl(name)
        if impl is None:
            return 404
        # 子通道需要与门面一致的请求参数（cookies/proxies/verify 等）
        impl.init()
        impl.updateCore(self._core)
        return impl.search(number)

    def search(self, number: str):
        """ 按 [javdb_channel] 配置选择数据通道执行刮削

        :param number: 番号
        :return: 统一字段字典（JSON 字符串）

        分派与降级策略（参考 MDCx 三通道设计）：
          - service=web       → 仅走原有 HTML 刮削，行为与升级前逐行等价
          - service=api/app   → 走对应通道，失败时追加 web 兜底
          - service=auto      → 按 fallback_order 依次尝试，全部失败再回退 web
        """
        conf = self._channel_conf()
        channels = conf.channel_list() if conf else ['web']
        # 非 web 通道失败时追加 web 兜底，避免外部接口变更导致整站不可用
        if 'web' not in channels:
            channels = channels + ['web']
        last_result = 404
        for name in channels:
            try:
                result = self._run_channel(name, number)
            except Exception as e:
                result = None
                if config.getInstance().javdb_debug():
                    print(f"[-]javdb {name} channel failed: {e}")
            if result and result != 404:
                return result
            last_result = result or 404
        return last_result

    # ------------------------------------------------------------------
    # web 通道：站点轮换 + 落地判定（原站优先，失败自动换镜像）
    # ------------------------------------------------------------------
    def _site_candidates(self):
        """按顺序返回要尝试的站点 host 列表（当前 cookie 对应站点排第一）

        顺序来源：[javdb] domains（原站优先）→ [javdb] sites（镜像兜底），
        由 scraper.py 归一化后经 core.dbsites 传入；未传入时按 self.dbsite 反推。
        """
        candidates = []
        seen = set()

        def _add(host):
            host = (host or "").strip().lower()
            if host and host not in seen:
                seen.add(host)
                candidates.append(host)

        pairs = list(getattr(self, "dbsites", None) or [])
        for host, key in pairs:
            if key == self.dbsite:
                _add(host)
        if not candidates:
            d = (self.dbsite or "javdb").strip().lower()
            _add(d if "." in d else f"{d}.com")
        for host, _key in pairs:
            _add(host)
        return candidates

    def _fetch(self, url):
        """请求页面

        优先用 curl_cffi 做 TLS 指纹伪装（行为等同真浏览器，不会触发 javdb
        「声明 HTML 就跳 /login」的判定）；未安装/未启用时回退 cloudscraper + Accept: */*。

        :param url: 目标 URL
        :return: (text, final_url, status)；请求失败返回 (None, url, None)
        """
        conf = config.getInstance()
        try:
            impersonate = conf.javdb_tls_impersonate()
        except Exception:
            impersonate = ""
        if impersonate:
            try:
                resp = tls_request.get(
                    url, cookies=self.cookies, proxies=self.proxies, verify=self.verify,
                    timeout=30, impersonate=impersonate)
                if resp is not None:
                    return (resp.text or ""), (resp.url or url), resp.status_code
            except Exception as e:
                if conf.javdb_debug():
                    print(f'[-]javdb tls fetch failed: {url} {type(e).__name__}: {e}')
        # 回退：cloudscraper + Accept: */*（旧行为）
        try:
            resp = get_html_by_scraper(
                url, cookies=self.cookies, proxies=self.proxies, verify=self.verify,
                return_type="object", extra_headers=_ACCEPT_ANY)
        except Exception as e:
            if conf.javdb_debug():
                print(f'[-]javdb request failed: {url} {type(e).__name__}: {e}')
            return None, url, None
        if resp is None:
            return None, url, None
        return (resp.text or ""), (resp.url or url), resp.status_code

    @staticmethod
    def _classify(host, text, final_url, status):
        """判定落地页类型

        :param host: 本次请求期望的站点 host
        :param text: 页面文本
        :param final_url: 跟随重定向后的最终 URL
        :param status: HTTP 状态码
        :return: OK / CF / LOGIN / HIJACK / ERROR
        """
        if status is None or text is None:
            return _C_ERROR
        try:
            fhost = (urlsplit(final_url).hostname or "").lower()
        except Exception:
            fhost = ""
        # 落地域名必须是 javdb 系（防广告劫持，如 xml-v4.pushub.net）
        if fhost and not (fhost == host or (fhost.startswith("javdb") and fhost.endswith(".com"))):
            return _C_HIJACK
        try:
            path = urlsplit(final_url).path or ""
        except Exception:
            path = ""
        if "/login" in path:
            return _C_LOGIN
        # 借鉴 amane / mdcz：状态码 200 但内容是登录表单
        if _LOGIN_MARKER in text:
            return _C_LOGIN
        # 借鉴 mdcz：地区限制 / IP 被封（也都是 200 状态的非正常页）
        for kind, marker in _BLOCK_MARKERS:
            if marker in text:
                return kind
        if status == 403 or "Just a moment" in text or "cf-browser-verification" in text:
            return _C_CF
        if status >= 400:
            return _C_ERROR
        return _C_OK

    @staticmethod
    def _drop_numeric_prefix(number):
        """去掉番号前面的纯数字前缀（如 300NTK-718 -> NTK-718）

        javdb 上部分作品不带纯数字前缀（300NTK-718 在站上写作 NTK-718），
        严格匹配会一直 NOMATCH。只在剩余部分仍像番号时才返回，避免误伤：
          - 要求前缀至少 2 位数字，且其后以字母开头并含数字
          - 1234-5678 这类纯数字番号不会命中（去掉前缀后不以字母开头）

        :param number: 原始番号
        :return: 去前缀后的番号；不符合时返回空串
        """
        m = re.match(r'^\d{2,}[-_ ]?([A-Za-z][A-Za-z0-9]*[-_]?\d+.*)$', str(number or '').strip())
        return m.group(1) if m else ''

    def _pick_detail_url(self, number, urls, ids):
        """按番号从搜索结果中挑出详情页 URL

        :return: (url, matched)；未匹配返回 ('', False)
        """
        if not urls:
            return "", False
        # 欧美影片（形如 blacked.20.01.01）直接取第一条
        if re.search(r'[a-zA-Z]+\.\d{2}\.\d{2}\.\d{2}', number):
            self.queryid = 0
            return urls[0], True
        # 第一轮：只接受与番号严格对应的结果（先精确，再忽略分隔符），避免刮错
        folded_target = re.sub(r'[-_\s]', '', number).upper()
        for index, found in enumerate(ids):
            if found.upper() == number.upper() or \
                    re.sub(r'[-_\s]', '', found).upper() == folded_target:
                self.queryid = index
                self._number_loose = False
                return urls[index], True
        # 第二轮：宽松匹配——去掉纯数字前缀后再比一次（300NTK-718 -> NTK-718）
        stripped = self._drop_numeric_prefix(number)
        if stripped:
            folded_strip = re.sub(r'[-_\s]', '', stripped).upper()
            for index, found in enumerate(ids):
                if re.sub(r'[-_\s]', '', found).upper() == folded_strip:
                    self.queryid = index
                    self._number_loose = True
                    return urls[index], True
        return "", False

    def _resolve_detail(self, number):
        """逐站点搜索并校验详情页

        :return: (detail_url, search_tree, detail_page, diag)
                 任一站点「搜索 + 详情页」都通过判定即返回；
                 搜索能开但详情页被跳登录/广告时换下一个站点重试
                 （对应「能搜到但打不开」的场景）。
        """
        diag = []
        for host in self._site_candidates():
            search_url = f"https://{host}/search?q={number}&f=all"
            text, final_url, status = self._fetch(search_url)
            kind = self._classify(host, text, final_url, status)
            if kind != _C_OK:
                diag.append((host, kind, final_url, status))
                continue
            tree = etree.fromstring(text, etree.HTMLParser())
            candidates = self._search_candidates(tree, final_url)
            if not candidates:
                diag.append((host, _C_EMPTY, final_url, status))
                continue
            urls = [url for url, _found in candidates]
            ids = [found for _url, found in candidates]
            detail_url, matched = self._pick_detail_url(number, urls, ids)
            if not matched:
                diag.append((host, _C_NOMATCH, f"{final_url} (found: {ids[:5]})", status))
                continue
            dtext, dfinal, dstatus = self._fetch(detail_url)
            dkind = self._classify(host, dtext, dfinal, dstatus)
            if dkind != _C_OK:
                diag.append((host, f"detail-{dkind}", dfinal, dstatus))
                continue
            return detail_url, tree, dtext, diag
        return "", None, "", diag

    def _format_detail(self, page):
        """把详情页 HTML 解析为统一字段字典

        :param page: 详情页 HTML 文本
        :return: dictformat 输出的 JSON 字符串；无法处理时返回 404
        """
        if '此內容需要登入才能查看或操作' in page or '需要VIP權限才能訪問此內容' in page:
            self.noauth = True
            self.imagecut = 0
            tree = getattr(self, "querytree", None)
            if tree is not None:
                return self.dictformat(tree)
            return 404
        htmltree = etree.fromstring(page, etree.HTMLParser())
        return self.dictformat(htmltree)

    @staticmethod
    def _print_diag(diag):
        """一次性打印所有站点的失败明细（替代旧实现里重复且含糊的报错）"""
        if not diag:
            return
        print('[!]javdb web channel: 所有候选站点均不可用，明细如下：')
        for host, kind, url, status in diag:
            print(f'    - {host or "-":<18} {kind:<14} status={status} url={url}')
        kinds = [kind for _host, kind, _url, _status in diag]
        if any('LOGIN' in k for k in kinds):
            print('[!]提示：LOGIN 表示请求被跳转到登录页/返回登录表单（实测：无码条目与无码演员档案需要登录态，')
            print('           搜索页与有码详情页一般不需要）。可检查 [javdb] cookie，或确认 tls_impersonate 已启用。')
        if any('HIJACK' in k for k in kinds):
            print('[!]提示：HIJACK 表示被跳到非 javdb 域名（广告劫持），请从 [javdb] domains 移除该站。')
        if any('CF' in k for k in kinds):
            print('[!]提示：CF 表示命中 Cloudflare 拦截，可临时开启 [cloudflare] switch=1。')
        if any('REGION_BLOCKED' in k for k in kinds):
            print('[!]提示：REGION_BLOCKED 表示 javdb 对你当前出口的地区限制访问（版权限制），建议换对应地区代理。')
        if any('IP_BANNED' in k for k in kinds):
            print('[!]提示：IP_BANNED 表示该出口 IP 已被 javdb 封禁，请换代理出口或降低请求频率。')

    def _search_web(self, number: str):
        """原有 HTML 刮削通道：按站点顺序尝试（原站优先），失败自动换站

        :param number: 番号
        :return: 统一字段字典（JSON 字符串）
        """
        self.number = number
        # 指定 URL：直接用该页面，不再搜索
        if self.specifiedUrl:
            host = ""
            try:
                host = (urlsplit(self.specifiedUrl).hostname or "").lower()
            except Exception:
                pass
            text, final_url, status = self._fetch(self.specifiedUrl)
            kind = self._classify(host, text, final_url, status)
            if kind != _C_OK:
                self._print_diag([(host or self.specifiedUrl, kind, final_url, status)])
                raise Exception(f'[!] {number}: specified url unavailable ({kind})')
            self.detailurl = self.specifiedUrl
            return self._format_detail(text)

        detail_url, tree, page, diag = self._resolve_detail(number)
        if not detail_url:
            self._print_diag(diag)
            # 区分「完全没搜到」与「搜到了但番号对不上」，避免误报成 no search results
            kinds = {kind for _host, kind, _url, _status in diag}
            if kinds and kinds.issubset({_C_NOMATCH}):
                raise Exception(f'[!] {number}: javdb 搜到结果但番号不匹配（明细见上）')
            raise Exception(f'[!] {number}: No search results found in javdb')
        self.detailurl = detail_url
        self.querytree = tree
        self.deatilpage = page
        return self._format_detail(page)

    def queryNumberUrl(self, number):
        """返回详情页 URL（Parser 接口要求）

        与 _search_web 共用逐站点搜索逻辑；全部站点失败时打印诊断明细并抛出
        一句摘要，避免旧实现里「同一条错误被打印两遍」的问题。
        """
        self.number = number
        detail_url, tree, page, diag = self._resolve_detail(number)
        if not detail_url:
            self._print_diag(diag)
            raise Exception(f"No search results found in javdb ({len(diag)} site(s) tried)")
        self.querytree = tree
        self.deatilpage = page
        return detail_url

    def _deprecated_query_number_url(self, number):
        """【已废弃】旧版 queryNumberUrl 实现，仅作对照保留，实际不再被调用。

        保留原因：便于对比升级前的匹配规则；确认新实现稳定后可整段删除。
        """
        javdb_url = f'https://{self.dbsite}.com/search?q={number}&f=all'
        try:
            resp = get_html_by_scraper(javdb_url, cookies=self.cookies, proxies=self.proxies, verify=self.verify, return_type="object")
        # except Exception as e:
        #     #print(e)
        #     raise Exception(f'[!] {self.number}: page not fond in javdb')

        # self.querytree = etree.fromstring(resp.text, etree.HTMLParser()) 
        # # javdb sometime returns multiple results,
        # # and the first elememt maybe not the one we are looking for
        # # iterate all candidates and find the match one
        # urls = self.getTreeAll(self.querytree, '//*[contains(@class,"movie-list")]/div/a/@href')
        # # 记录一下欧美的ids  ['Blacked','Blacked']
        # if re.search(r'[a-zA-Z]+\.\d{2}\.\d{2}\.\d{2}', number):
        #     correct_url = urls[0]
        # else:
        #     ids = self.getTreeAll(self.querytree, '//*[contains(@class,"movie-list")]/div/a/div[contains(@class, "video-title")]/strong/text()')
        #     try:
        #         self.queryid = ids.index(number)
        #         correct_url = urls[self.queryid]
        #     except:
        #         # 为避免获得错误番号，只要精确对应的结果
        #         if ids[0].upper() != number.upper():
        #             raise ValueError("number not found in javdb")
            self.querytree = etree.fromstring(resp.text, etree.HTMLParser()) 

            # 搜索结果条目：新版为 <a class="box">，旧版为 div.movie-list > div > a，两种都收
            candidates = self._search_candidates(self.querytree, resp.url)
            if not candidates:
                print(f'[!] {self.number}: No search results found in javdb')
                raise Exception(f'[!] {self.number}: No search results found in javdb')

            urls = [url for url, _ in candidates]
            ids = [found for _, found in candidates]
            print(f'[+] {self.number}: Found {len(ids)} results: {ids}')

            # 欧美影片（形如 blacked.20.01.01）直接取第一条
            if re.search(r'[a-zA-Z]+\.\d{2}\.\d{2}\.\d{2}', number):
                return urls[0]

            # 只接受与番号严格对应的结果：先精确匹配，再忽略分隔符匹配，避免刮错
            folded_target = re.sub(r'[-_\s]', '', number).upper()
            for index, found in enumerate(ids):
                if found.upper() == number.upper() or \
                        re.sub(r'[-_\s]', '', found).upper() == folded_target:
                    self.queryid = index
                    return urls[index]

            raise ValueError(f"number not found in javdb (found: {ids[0]})")
            
        except Exception as e:
            print(f'[!] {self.number}: Error in queryNumberUrl: {str(e)}')
            print(f'[!] Response status: {resp.status_code if "resp" in locals() else "N/A"}')
            print(f'[!] Response URL: {resp.url if "resp" in locals() else "N/A"}')
            raise
            
    @staticmethod
    def _search_candidates(querytree, base_url):
        """从搜索结果页提取 (详情页 URL, 结果番号) 列表。

        新版站点条目为 ``<a class="box">``，番号在 ``div.video-title > strong``；
        旧版为 ``div.movie-list > div > a``，结构相同。只收两者都有的条目。
        """
        candidates = []
        for item in querytree.xpath('//a[contains(@class,"box")]'):
            href = ''.join(item.xpath('./@href')).strip()
            found = ''.join(item.xpath('.//div[contains(@class,"video-title")]/strong/text()')).strip()
            if href and found:
                candidates.append((urljoin(base_url, href), found))
        if candidates:
            return candidates
        for item in querytree.xpath('//*[contains(@class,"movie-list")]/div/a'):
            href = ''.join(item.xpath('./@href')).strip()
            found = ''.join(item.xpath('.//div[contains(@class,"video-title")]/strong/text()')).strip()
            if href and found:
                candidates.append((urljoin(base_url, href), found))
        return candidates

    def getNum(self, htmltree):
        """番号优先取复制按钮的 data 属性，旧版页面回退到 info 面板文本。"""
        number = self.getTreeElement(htmltree, self.expr_number).strip()
        if not number:
            number = self.getTreeElement(htmltree, self.expr_number2).strip()
        return number

    def getTitle(self, htmltree):
        if self.noauth:
            return self.getTreeElement(htmltree, self.expr_title_no, self.queryid)
        # 新版：h2.title > strong.current-title 为显示标题，span.origin-title 为原标题
        title = self.getTreeElement(
            htmltree, '//h2[contains(@class,"title")]/strong[contains(@class,"current-title")]/text()').strip()
        if not title:
            title = self.getTreeElement(
                htmltree, '//h2[contains(@class,"title")]/span[contains(@class,"origin-title")]/text()').strip()
        if not title:
            browser_title = super().getTitle(htmltree)
            end = browser_title.find(' | JavDB')
            title = (browser_title[:end] if end > 0 else browser_title).strip()
        return title.replace(self.number, '').strip()

    def getCover(self, htmltree):
        if self.noauth:
            return self.getTreeElement(htmltree, self.expr_cover_no, self.queryid)
        return super().getCover(htmltree)

    def getRelease(self, htmltree):
        if self.noauth:
            return self.getTreeElement(htmltree, self.expr_release_no, self.queryid).strip()
        return super().getRelease(htmltree)

    def getDirector(self, htmltree):
        return self.getTreeElementbyExprs(htmltree, self.expr_director, self.expr_director2)
    
    def getSeries(self, htmltree):
        # NOTE 不清楚javdb是否有一部影片多个series的情况，暂时保留
        results = self.getTreeAllbyExprs(htmltree, self.expr_series, self.expr_series2)
        result = ''.join(results)
        if not result and self.fixstudio:
            result = self.getStudio(htmltree)
        return result

    def getLabel(self, htmltree):
        results = self.getTreeAllbyExprs(htmltree, self.expr_label, self.expr_label2)
        result = ''.join(results)
        if not result and self.fixstudio:
            result = self.getStudio(htmltree)
        return result

    def getActors(self, htmltree):
        actors = self.getTreeAll(htmltree, self.expr_actor)
        genders = self.getTreeAll(htmltree, self.expr_actor2)
        r = []
        # NOTE only female, we dont care others
        actor_gendor = 'female'
        for idx, act in enumerate(actors):
            # 新版用 actor-female class 标记女优；旧版用 strong 的 symbol 图标
            gender = genders[idx] if idx < len(genders) else ''
            is_female = 'actor-female' in gender or gender == 'symbol female'
            is_male = 'actor-male' in gender or gender == 'symbol male'
            if (actor_gendor == 'all'
                    or (actor_gendor == 'both' and (is_female or is_male))
                    or (actor_gendor == 'female' and is_female)
                    or (actor_gendor == 'male' and is_male)):
                r.append(act)
        if re.match(r'FC2-[\d]+', self.number, re.A) and not r:
            r = '素人'
            self.fixstudio = True
        return r

    def extradict(self, dic: dict):
        """番号以用户文件名为准

        宽松匹配（如 300NTK-718 在站上为 NTK-718）命中时，把详情页解析出的
        站上番号改回原始番号，保证写进 .nfo / 文件名 / 目录名的番号是完整形式。
        """
        if getattr(self, '_number_loose', False) and getattr(self, 'number', ''):
            dic['number'] = self.number
        return dic

    def getOutline(self, htmltree):
        if self.morestoryline:
            from .storyline import getStoryline
            return getStoryline(self.number, uncensored=self.getUncensored(htmltree),
                                proxies=self.proxies, verify=self.verify)
        return ''

    def getTrailer(self, htmltree):
        video = super().getTrailer(htmltree)
        # 加上数组判空
        if video:
            if not 'https:' in video:
                video_url = 'https:' + video
            else:
                video_url = video
        else:
            video_url = ''
        return video_url

    def getTags(self, htmltree):
        return self.getTreeAllbyExprs(htmltree, self.expr_tags, self.expr_tags2)

    def getUserRating(self, htmltree):
        try:
            numstrs = self.getTreeElement(htmltree, self.expr_userrating)
            nums = re.findall('[0-9.]+', numstrs)
            return float(nums[0])
        except:
            return ''

    def getUserVotes(self, htmltree):
        try:
            result = self.getTreeElement(htmltree, self.expr_uservotes)
            v = re.findall('[0-9.]+', result)
            return int(v[1])
        except:
            return ''

    def getaphoto(self, url, session):
        """ 访问演员页并提取其头像图片地址

        :param url: 演员详情页 URL
        :param session: 已构造的请求会话（由 getActorPhoto 复用，避免每张头像都新建连接）
        :return: 头像图片 URL；未找到返回空串
        """
        html_page = session.get(url).text
        img_url = re.findall(r'<span class\=\"avatar\" style\=\"background\-image\: url\((.*?)\)', html_page)
        return img_url[0] if img_url else ''

    def getActorPhoto(self, htmltree):
        """ 演员头像字典 {演员名: 头像URL}

        :param htmltree: 详情页 etree
        :return: 演员名到头像地址的映射；无演员或获取失败返回空 dict

        注意：原先直接使用 self.session，但门面类从未定义该属性，导致所有头像请求
        抛出 AttributeError 并被 except 静默吞掉（头像永远为空）。此处改为就地构造
        一个 keep-alive 会话并复用。
        """
        actorall = self.getTreeAll(htmltree, self.expr_actorphoto)
        if not actorall:
            return {}
        actors = self.getActors(htmltree)
        actor_photo = {}
        # 复用一个会话：头像页需要保持 cookie，逐个新建连接会被站点限流
        session = request_session(cookies=self.cookies, proxies=self.proxies, verify=self.verify)
        for i in actorall:
            x = re.findall(r'/actors/(.*)', i.attrib['href'], re.A)
            if not len(x) or not len(x[0]) or i.text not in actors:
                continue
            try:
                pic_url = self.getaphoto(urljoin('https://javdb.com', i.attrib['href']), session)
                if len(pic_url):
                    actor_photo[i.text] = pic_url
            except Exception:
                # 单个演员头像失败不影响其它演员
                pass
        return actor_photo