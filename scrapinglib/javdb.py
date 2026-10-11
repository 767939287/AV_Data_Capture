# -*- coding: utf-8 -*-

import re
from urllib.parse import urljoin
from lxml import etree

import config
from .httprequest import request_session, get_html_by_scraper
from .parser import Parser


class Javdb(Parser):
    source = 'javdb'

    expr_number = '//strong[contains(text(),"番號")]/../span/text()'
    expr_number2 = '//strong[contains(text(),"番號")]/../span/a/text()'
    expr_title = "/html/head/title/text()"
    expr_title_no = '//*[contains(@class,"movie-list")]/div/a/div[contains(@class, "video-title")]/strong/text()'
    expr_runtime = '//strong[contains(text(),"時長")]/../span/text()'
    expr_runtime2 = '//strong[contains(text(),"時長")]/../span/a/text()'
    expr_uncensored = '//strong[contains(text(),"類別")]/../span/a[contains(@href,"/tags/uncensored?") or contains(@href,"/tags/western?")]'
    expr_actor = '//span[@class="value"]/a[contains(@href,"/actors/")]/text()'
    expr_actor2 = '//span[@class="value"]/a[contains(@href,"/actors/")]/../strong/@class'
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

    def updateCore(self, core):
        # 保存 core 引用，供 api/app 通道在分派时重新初始化子通道使用
        self._core = core
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
                if config.getInstance().debug():
                    print(f"[-]javdb {name} channel failed: {e}")
            if result and result != 404:
                return result
            last_result = result or 404
        return last_result

    def _search_web(self, number: str):
        """ 原有 HTML 刮削通道（保持与升级前逐行等价）

        :param number: 番号
        :return: 统一字段字典（JSON 字符串）
        """
        self.number = number
        if self.specifiedUrl:
            self.detailurl = self.specifiedUrl
        else:
            self.detailurl = self.queryNumberUrl(number)
        self.deatilpage = get_html_by_scraper(self.detailurl, cookies=self.cookies, proxies=self.proxies, verify=self.verify)
        if '此內容需要登入才能查看或操作' in self.deatilpage or '需要VIP權限才能訪問此內容' in self.deatilpage:
            self.noauth = True
            self.imagecut = 0
            result = self.dictformat(self.querytree)
        else:
            htmltree = etree.fromstring(self.deatilpage, etree.HTMLParser())
            result = self.dictformat(htmltree)
        return result

    def queryNumberUrl(self, number):
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
            
            # 获取所有可能的URL和ID，添加长度检查和日志
            urls = self.getTreeAll(self.querytree, '//*[contains(@class,"movie-list")]/div/a/@href')
            if not urls:
                print(f'[!] {self.number}: No search results found in javdb')
                raise Exception(f'[!] {self.number}: No search results found in javdb')
            
            # 欧美影片的特殊处理
            if re.search(r'[a-zA-Z]+\.\d{2}\.\d{2}\.\d{2}', number):
                print(f'[+] {self.number}: Western video detected')        
                correct_url = urls[0]
        # return urljoin(resp.url, correct_url)
            else:
                ids = self.getTreeAll(self.querytree, '//*[contains(@class,"movie-list")]/div/a/div[contains(@class, "video-title")]/strong/text()')
                print(f'[+] {self.number}: Found {len(ids)} results: {ids}')
                
                try:
                    self.queryid = ids.index(number)
                    correct_url = urls[self.queryid]
                except ValueError:
                    # 检查第一个结果是否匹配
                    if not ids:
                        print(f'[!] {self.number}: No IDs found in search results')
                        raise ValueError("No IDs found in javdb search results")
                        
                    if ids[0].upper() != number.upper():
                        print(f'[!] {self.number}: Expected {number}, but found {ids[0]}')
                        raise ValueError(f"number not found in javdb (found: {ids[0]})")
                        
                    correct_url = urls[0]
                except IndexError as e:
                    print(f'[!] {self.number}: Index error - urls:{len(urls)}, ids:{len(ids)}')
                    raise IndexError(f"Index mismatch: urls({len(urls)}), ids({len(ids)})") from e
                
            return urljoin(resp.url, correct_url)
            
        except Exception as e:
            print(f'[!] {self.number}: Error in queryNumberUrl: {str(e)}')
            print(f'[!] Response status: {resp.status_code if "resp" in locals() else "N/A"}')
            print(f'[!] Response URL: {resp.url if "resp" in locals() else "N/A"}')
            raise
            
    def getTitle(self, htmltree):
        if self.noauth:
            return self.getTreeElement(htmltree, self.expr_title_no, self.queryid)
        browser_title = super().getTitle(htmltree)
        title = browser_title[:browser_title.find(' | JavDB')].strip()
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
        idx = 0
        # NOTE only female, we dont care others
        actor_gendor = 'female'
        for act in actors:
            if((actor_gendor == 'all')
            or (actor_gendor == 'both' and genders[idx] in ['symbol female', 'symbol male'])
            or (actor_gendor == 'female' and genders[idx] == 'symbol female')
            or (actor_gendor == 'male' and genders[idx] == 'symbol male')):
                r.append(act)
            idx = idx + 1
        if re.match(r'FC2-[\d]+', self.number, re.A) and not r:
            r = '素人'
            self.fixstudio = True
        return r

    def getOutline(self, htmltree):
        if self.morestoryline:
            from .storyline import getStoryline
            return getStoryline(self.number, self.getUncensored(htmltree),
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