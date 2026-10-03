# -*- coding: utf-8 -*-
"""
此部分暂未修改

"""

import json
import os
import re
import time
import secrets
import builtins
import multiprocessing
import config

from urllib import parse
from lxml import etree
from multiprocessing.dummy import Pool as ThreadPool

from .airav import Airav
from .xcity import Xcity
from . import httprequest

# 舍弃 Amazon 源
G_registered_storyline_site = {"airavwiki", "airav", "avno1", "xcity", "58avgo", "fanza", "mgstage"}

G_mode_txt = ('顺序执行', '线程池')


def is_japanese(raw: str) -> bool:
    """
    日语简单检测
    """
    return bool(re.search(r'[\u3040-\u309F\u30A0-\u30FF\uFF66-\uFF9F]', raw, re.UNICODE))


class noThread(object):
    def map(self, fn, param):
        return list(builtins.map(fn, param))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


# 获取剧情介绍 从列表中的站点同时查，取值优先级从前到后
def getStoryline(number, title=None, sites: list = None, uncensored=None, proxies=None, verify=None):
    start_time = time.time()
    debug = config.getInstance().debug_storyline()
    print(f'[!]Getting storyline debug : {debug}')
    # 优先级规则（用户可在 config.ini 的 [storyline] 段一眼看懂并掌控）：
    #   1) site 有值：完全按 site 的书写顺序决定优先级；censored_site/uncensored_site
    #      仅作为“额外可用站点”补充到 site 之后（如 58avgo 这类无码专站），且不重复。
    #   2) site 为空：退回原逻辑，用 censored_site/uncensored_site + site 的拼接顺序。
    site_list = [s.strip() for s in config.getInstance().storyline_site().split(",") if s.strip()]
    if uncensored:
        extra_sites = [s.strip() for s in config.getInstance().storyline_uncensored_site().split(",") if s.strip()]
    else:
        extra_sites = [s.strip() for s in config.getInstance().storyline_censored_site().split(",") if s.strip()]
    if site_list:
        # site 有值：site 顺序优先，extra_sites 只补充 site 中未出现的站点（追加到末尾）
        storyine_sites = site_list + [s for s in extra_sites if s not in site_list]
    else:
        # site 为空：保持原行为（extra_sites 在前 + site 在后）
        storyine_sites = extra_sites + site_list
    r_dup = set()
    sort_sites = []
    for s in storyine_sites:
        s = s.strip()
        if s in G_registered_storyline_site and s not in r_dup:
            sort_sites.append(s)
            r_dup.add(s)
    # 各站点「整体超时」(秒)：config [storyline] timeout=15 或 fanza=20,airav=10
    # 语义为该站点「整个刮削流程」的最长耗时(方案2)：超过则放弃该站(结果记空)，由其他站点顶上。
    # 未单独配置的站点用 timeout['default']；都未配置则为 None → 不设整体超时(等其自然跑完)。
    # 注意：该值同时作为该站每次底层 HTTP 请求的超时(见 getStoryline_mp 透传)，双保险。
    timeouts = config.getInstance().storyline_timeout()
    default_timeout = timeouts.get("default")

    def _site_total_timeout(site):
        t = timeouts.get(site, default_timeout)
        return t if isinstance(t, int) and t > 0 else None

    site_total_timeout = {site: _site_total_timeout(site) for site in sort_sites}
    # 用 list 而非生成器传给线程池，避免生成器+线程池潜在的顺序歧义
    mp_args = [(site, number, title, debug, proxies, verify, _site_total_timeout(site))
               for site in sort_sites]
    cores = min(len(sort_sites), os.cpu_count())
    if cores == 0:
        return ''
    run_mode = config.getInstance().storyline_mode()
    if debug:
        # 明确打印实际生效的优先级顺序，便于核对 config 是否按预期生效
        print(f'[!]Storyline sites priority order: {sort_sites}')
        eff = {k: v for k, v in site_total_timeout.items() if v}
        if eff:
            print(f'[!]Storyline site total timeout: {eff}')
    if run_mode > 0:
        results = _run_threadpool_with_total_timeout(cores, mp_args, sort_sites, site_total_timeout, debug)
    else:
        # 顺序执行(单线程)，同样对每个站点应用整站整体超时，避免某站卡死拖住后续站点。
        results = _run_threadpool_with_total_timeout(1, mp_args, sort_sites, site_total_timeout, debug)
    sel = ''

    prefer_jp = config.getInstance().storyline_prefer_jp()
    # 以下debug结果输出会写入日志
    s = f'[!]Storyline{G_mode_txt[run_mode]}模式运行{len(sort_sites)}个任务共耗时(含启动开销){time.time() - start_time:.3f}秒，结束于{time.strftime("%H:%M:%S")}'
    sel_site = ''
    # 严格按 sort_sites 的顺序（即 config 中 censored_site/uncensored_site 在前、site 在后的声明顺序）选择：
    #   prefer_jp=1: 取第一个有结果的站点（按顺序，即使为日文，如 fanza/mgstage）
    #   prefer_jp=0: 优先第一个有结果的中文站点；若全是日文，则取第一个有结果的日文站点兜底
    for site, desc in zip(sort_sites, results):
        if not (isinstance(desc, str) and len(desc)):
            continue
        if prefer_jp:
            sel_site, sel = site, desc
            break
        if not is_japanese(desc):
            sel_site, sel = site, desc
            break
        if not len(sel_site):
            # 记录第一个日文结果作为兜底，继续往后找是否有中文结果
            sel_site, sel = site, desc
    for site, desc in zip(sort_sites, results):
        sl = len(desc) if isinstance(desc, str) else 0
        s += f'，[选中{site}字数:{sl}]' if site == sel_site else f'，{site}字数:{sl}' if sl else f'，{site}:空'
    if debug:
        print(s)
    return sel


def _run_threadpool_with_total_timeout(cores, mp_args, sort_sites, site_total_timeout, debug):
    """线程池并发执行各站点，支持对每个站点分别设置“整站整体超时”(方案2)。

    参数:
      cores:             线程数
      mp_args:           每个站点的参数元组列表(与 sort_sites 一一对应)
      sort_sites:        站点名列表(保持顺序)
      site_total_timeout: {站点名: 整体超时秒数或 None}。值为正整数时对该站应用整体超时；
                         为 None 时不对该站设整体超时，等其自然返回。
      debug:             是否打印调试信息

    返回:
      与 sort_sites 顺序一致的 results 列表；整体超时的站点其结果为 None。

    语义:
      “整体超时”指该站点从开始到返回的整段刮削流程总时长上限(含多次请求与重试)，
      而非单次 HTTP 请求超时。超时即放弃该站结果(记 None)，由其他站点顶上，
      从而避免某个站点卡死拖慢整体 storyline 获取。

    实现要点:
      用 apply_async 为每个站点单独提交并拿到独立的 AsyncResult，再按站点逐个
      result.get(timeout)。这样“整体超时”才是针对每个站点各自计时的：
        - 配了整体超时的站点：最多等 N 秒，超时则该项记 None，继续处理其它站点；
        - 未配的站点：result.get(timeout=None) 一直等其完成。
      (若用 map_async().get(timeout) 会变成等“全部任务”完成，无法按站点分别计时。)
      注意：超时只是“不再等待”，对应线程仍在后台运行，会在完成后自然结束，
            不影响本次 storyline 结果与程序退出。
    """
    # multiprocessing.pool 的 AsyncResult.get 超时抛 multiprocessing.TimeoutError，
    # 在 Python 3.11+ 其即为内建 TimeoutError，但旧版本两者不同，这里一并兼容捕获。
    _TimeoutErrors = (TimeoutError,) if multiprocessing.TimeoutError is TimeoutError else (
        TimeoutError, multiprocessing.TimeoutError)
    with ThreadPool(cores) as pool:
        async_results = [pool.apply_async(getStoryline_mp, (arg,)) for arg in mp_args]
        results = [None] * len(sort_sites)
        for idx, site in enumerate(sort_sites):
            t = site_total_timeout.get(site)
            try:
                results[idx] = async_results[idx].get(timeout=t)  # t=None 时一直等
            except _TimeoutErrors:
                results[idx] = None
                if debug:
                    print(f"[!]Storyline site '{site}' 整体超时 {t}s，放弃该站结果。")
            except Exception as e:
                results[idx] = None
                if debug:
                    print(f"[-]Storyline site '{site}' 执行异常: {e}")
    return results


def getStoryline_mp(args):
    (site, number, title, debug, proxies, verify, timeout) = args
    start_time = time.time()
    storyline = None
    if not isinstance(site, str):
        return storyline
    elif site == "airavwiki":
        storyline = getStoryline_airavwiki(number, debug, proxies, verify, timeout)
    elif site == "airav":
        storyline = getStoryline_airav(number, debug, proxies, verify, timeout)
    elif site == "avno1":
        storyline = getStoryline_avno1(number, debug, proxies, verify, timeout)
    elif site == "xcity":
        storyline = getStoryline_xcity(number, debug, proxies, verify, timeout)
    elif site == "58avgo":
        storyline = getStoryline_58avgo(number, debug, proxies, verify, timeout)
    elif site == "fanza":
        storyline = getStoryline_fanza(number, debug, proxies, verify, timeout)
    elif site == "mgstage":
        storyline = getStoryline_mgstage(number, debug, proxies, verify, timeout)
    if debug:
        print("[!]MP 线程[{}]运行{:.3f}秒，结束于{}返回结果: {}".format(
            site,
            time.time() - start_time,
            time.strftime("%H:%M:%S"),
            storyline if isinstance(storyline, str) and len(storyline) else '[空]')
        )
    return storyline


def getStoryline_airav(number, debug, proxies, verify, timeout=None):
    url = ''
    try:
        site = secrets.choice(('airav.io', 'airair6.co',))
        url = f'https://{site}/searchresults.aspx?Search={number}&Type=0'
        session = httprequest.request_session(proxies=proxies, verify=verify, retry=0, timeout=timeout)
        res = session.get(url, timeout=timeout)
        if not res:
            raise ValueError(f"get_html_by_session('{url}') failed")
        lx = etree.fromstring(res.text, etree.HTMLParser(recover=True))
        urls = lx.xpath('//div[@class="oneVideo-top"]/a/@href')
        txts = lx.xpath('//div[@class="oneVideo-body"]/h5/text()')
        detail_url = None
        for txt, url in zip(txts, urls):
            if re.search(number, txt, re.I):
                detail_url = parse.urljoin(res.url, url)
                break
        if detail_url is None:
            raise ValueError("number not found")
        detail_data = session.get(detail_url, timeout=timeout)
        if not detail_data.ok:
            raise ValueError(f"session.get('{detail_url}') failed")
        detail_page = etree.fromstring(detail_data.text, etree.HTMLParser(recover=True))
        titles = detail_page.xpath('//div[@class="video-title my-3"]/h1/text()')
        title = str(titles[0]).strip()
        # 添加排除"破坏版"关键字的逻辑
        if "破坏版" in title or "破壞版" in title or "本站独家影片" in title or "破解版" in title or "马赛克破解版" in title or "馬賽克破解版" in title :
            raise ValueError(f"title contains excluded keyword: {title}")
        if number not in title:
            raise ValueError(f"page number ->[{number}] not match")
        desc_list = detail_page.xpath('//div[@class="video-info"]/p[@class="my-3"]/text()')
        desc = str(desc_list[0]).strip()
        return desc
    except Exception as e:
        if debug:
            print(f"[-]MP getStoryline_airav {url} Error: {e}, number [{number}].")
        pass
    return None


def getStoryline_airavwiki(number, debug, proxies, verify, timeout=None):
    try:
        kwd = number[:6] if re.match(r'\d{6}[\-_]\d{2,3}', number) else number
        airavwiki = Airav()
        airavwiki.init()
        airavwiki.updateCore(core=None)
        airavwiki.addtion_Javbus = False
        airavwiki.proxies = proxies
        airavwiki.verify = verify
        airavwiki.timeout = timeout
        jsons = airavwiki.search(kwd)
        outline = json.loads(jsons).get('outline')
        return outline
    except Exception as e:
        if debug:
            print(f"[-]MP getStoryline_airavwiki Error: {e}, number [{number}].")
        pass
    return ''


def getStoryline_58avgo(number, debug, proxies, verify, timeout=None):
    try:
        url = 'http://58avgo.com/cn/index.aspx' + secrets.choice([
            '', '?status=3', '?status=4', '?status=7', '?status=9', '?status=10', '?status=11', '?status=12',
                '?status=1&Sort=Playon', '?status=1&Sort=dateupload', 'status=1&Sort=dateproduce'
        ])  # 随机选一个，避免网站httpd日志中单个ip的请求太过单一
        kwd = number[:6] if re.match(r'\d{6}[\-_]\d{2,3}', number) else number
        form_kwargs = {}
        if timeout is not None:
            form_kwargs["timeout"] = timeout
        result, browser = httprequest.get_html_by_form(url,
                                                       fields={'ctl00$TextBox_SearchKeyWord': kwd},
                                                       proxies=proxies, verify=verify,
                                                       return_type='browser', **form_kwargs)
        if not result:
            raise ValueError(f"get_html_by_form('{url}','{number}') failed")
        if f'searchresults.aspx?Search={kwd}' not in browser.url:
            raise ValueError("number not found")
        s = browser.page.select('div.resultcontent > ul > li.listItem > div.one-info-panel.one > a.ga_click')
        link = None
        for a in s:
            title = a.h3.text.strip()
            list_number = title[title.rfind(' ')+1:].strip()
            if re.search(number, list_number, re.I):
                link = a
                break
        if link is None:
            raise ValueError("number not found")
        result = browser.follow_link(link)
        if not result.ok or 'playon.aspx' not in browser.url:
            raise ValueError("detail page not found")
        title = browser.page.select_one('head > title').text.strip()
        detail_number = str(re.findall(r'\[(.*?)]', title)[0])
        if not re.search(number, detail_number, re.I):
            raise ValueError(f"detail page number not match, got ->[{detail_number}]")
        return browser.page.select_one('#ContentPlaceHolder1_Label2').text.strip()
    except Exception as e:
        if debug:
            print(f"[-]MP getOutline_58avgo Error: {e}, number [{number}].")
        pass
    return ''


def getStoryline_avno1(number, debug, proxies, verify, timeout=None):  # 获取剧情介绍 从avno1.cc取得
    try:
        site = secrets.choice(['avno1.cc', '1768av.club', '2nine.net', 'av999.tv',
                               'hotav.biz', 'javhq.tv',
                               'www.hdsex.cc', 'www.xxx18.cc',])
        url = f'http://{site}/cn/search.php?kw_type=key&kw={number}'
        data = httprequest.get_html_by_scraper(url, proxies=proxies, verify=verify, timeout=timeout)
        lx = etree.fromstring(data, etree.HTMLParser(recover=True))
        descs = lx.xpath('//@data-description')
        titles = lx.xpath('//a[@class="ga_name"]/text()')
        if not descs or not len(descs):
            print(f"number not found")
        partial_num = bool(re.match(r'\d{6}[\-_]\d{2,3}', number))
        for title, desc in zip(titles, descs):
            page_number = title[title.rfind(' ')+1:].strip()
            if not partial_num:
                # 不选择title中带破坏版和破坏版的简介
                 if re.match(f'^{number}$', page_number, re.I) and title.rfind('破解版') == -1 and title.rfind('本站独家影片') == -1 and title.rfind('破坏版') == -1 and title.rfind('破解版') == -1:
                    return desc.strip()
            elif re.search(number, page_number, re.I):
                return desc.strip()
        raise ValueError(f"page number ->[{page_number}] not match")
    except Exception as e:
        if debug:
            print(f"[-]MP getOutline_avno1 Error: {e}, number [{number}].")
        pass
    return ''


def getStoryline_avno1OLD(number, debug, proxies, verify, timeout=None):  # 获取剧情介绍 从avno1.cc取得
    try:
        url = 'http://www.avno1.cc/cn/' + secrets.choice(['usercenter.php?item=' +
                                                          secrets.choice(['pay_support', 'qa', 'contact', 'guide-vpn']),
                                                          '?top=1&cat=hd', '?top=1', '?cat=hd', 'porn', '?cat=jp', '?cat=us', 'recommend_category.php'
                                                          ])  # 随机选一个，避免网站httpd日志中单个ip的请求太过单一
        form_kwargs = {}
        if timeout is not None:
            form_kwargs["timeout"] = timeout
        result, browser = httprequest.get_html_by_form(url,
                                                       form_select='div.wrapper > div.header > div.search > form',
                                                       fields={'kw': number},
                                                       proxies=proxies, verify=verify,
                                                       return_type='browser', **form_kwargs)
        if not result:
            raise ValueError(f"get_html_by_form('{url}','{number}') failed")
        s = browser.page.select('div.type_movie > div > ul > li > div')
        for div in s:
            title = div.a.h3.text.strip()
            page_number = title[title.rfind(' ')+1:].strip()
            if re.search(number, page_number, re.I):
                return div['data-description'].strip()
        raise ValueError(f"page number ->[{page_number}] not match")
    except Exception as e:
        if debug:
            print(f"[-]MP getOutline_avno1 OLD Error: {e}, number [{number}].")
        pass
    return ''


def getStoryline_xcity(number, debug, proxies, verify, timeout=None):  # 获取剧情介绍 从xcity取得
    try:
        xcityEngine = Xcity()
        xcityEngine.init()
        xcityEngine.updateCore(core=None)
        xcityEngine.proxies = proxies
        xcityEngine.verify = verify
        xcityEngine.timeout = timeout
        jsons = xcityEngine.search(number)
        outline = json.loads(jsons).get('outline')
        return outline
    except Exception as e:
        if debug:
            print(f"[-]MP getOutline_xcity Error: {e}, number [{number}].")
        pass
    return ''


def getStoryline_fanza(number, debug, proxies, verify, timeout=None):  # 获取剧情介绍 从 FANZA/DMM 取得(日文)
    from .fanza import Fanza
    try:
        fanzaEngine = Fanza()
        fanzaEngine.init()
        fanzaEngine.updateCore(core=None)
        fanzaEngine.proxies = proxies
        fanzaEngine.verify = verify
        fanzaEngine.timeout = timeout
        jsons = fanzaEngine.search(number)
        if not jsons or jsons == 404:
            raise ValueError("number not found on FANZA")
        outline = json.loads(jsons).get('outline')
        return outline
    except Exception as e:
        if debug:
            print(f"[-]MP getStoryline_fanza Error: {e}, number [{number}].")
        pass
    return ''


def getStoryline_mgstage(number, debug, proxies, verify, timeout=None):  # 获取剧情介绍 从 MGStage 取得(日文)
    from .mgstage import Mgstage
    try:
        mgstageEngine = Mgstage()
        mgstageEngine.init()
        mgstageEngine.updateCore(core=None)
        mgstageEngine.proxies = proxies
        mgstageEngine.verify = verify
        mgstageEngine.timeout = timeout
        jsons = mgstageEngine.search(number)
        if not jsons or jsons == 404:
            raise ValueError("number not found on MGStage")
        outline = json.loads(jsons).get('outline')
        return outline
    except Exception as e:
        if debug:
            print(f"[-]MP getStoryline_mgstage Error: {e}, number [{number}].")
        pass
    return ''
