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
import config

from urllib import parse
from lxml import etree
from multiprocessing.dummy import Pool as ThreadPool

from .airav import Airav
from .xcity import Xcity
from . import httprequest

# 舍弃 Amazon 源
G_registered_storyline_site = {"airavwiki", "airav", "avno1", "xcity", "58avgo"}

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
    storyine_sites = config.getInstance().storyline_site().split(",")  # "1:airav,4:airavwiki".split(',')
    if uncensored:
        storyine_sites = config.getInstance().storyline_uncensored_site().split(",") + storyine_sites  # "3:58avgo".split(',')
    else:
        storyine_sites = config.getInstance().storyline_censored_site().split(",") + storyine_sites  # "2:airav,5:xcity".split(',')
    r_dup = set()
    sort_sites = []
    for s in storyine_sites:
        if s in G_registered_storyline_site and s not in r_dup:
            sort_sites.append(s)
            r_dup.add(s)
    # sort_sites.sort()
    mp_args = ((site, number, title, debug, proxies, verify) for site in sort_sites)
    cores = min(len(sort_sites), os.cpu_count())
    if cores == 0:
        return ''
    run_mode = 1
    with ThreadPool(cores) if run_mode > 0 else noThread() as pool:
        results = pool.map(getStoryline_mp, mp_args)
    sel = ''

    # 以下debug结果输出会写入日志
    s = f'[!]Storyline{G_mode_txt[run_mode]}模式运行{len(sort_sites)}个任务共耗时(含启动开销){time.time() - start_time:.3f}秒，结束于{time.strftime("%H:%M:%S")}'
    sel_site = ''
    for site, desc in zip(sort_sites, results):
        if isinstance(desc, str) and len(desc):
            if not is_japanese(desc):
                sel_site, sel = site, desc
                break
            if not len(sel_site):
                sel_site, sel = site, desc
    for site, desc in zip(sort_sites, results):
        sl = len(desc) if isinstance(desc, str) else 0
        s += f'，[选中{site}字数:{sl}]' if site == sel_site else f'，{site}字数:{sl}' if sl else f'，{site}:空'
    if debug:
        print(s)
    return sel


def getStoryline_mp(args):
    (site, number, title, debug, proxies, verify) = args
    start_time = time.time()
    storyline = None
    if not isinstance(site, str):
        return storyline
    elif site == "airavwiki":
        storyline = getStoryline_airavwiki(number, debug, proxies, verify)
    elif site == "airav":
        storyline = getStoryline_airav(number, debug, proxies, verify)
    elif site == "avno1":
        storyline = getStoryline_avno1(number, debug, proxies, verify)
    elif site == "xcity":
        storyline = getStoryline_xcity(number, debug, proxies, verify)
    elif site == "58avgo":
        storyline = getStoryline_58avgo(number, debug, proxies, verify)
    if debug:
        print("[!]MP 线程[{}]运行{:.3f}秒，结束于{}返回结果: {}".format(
            site,
            time.time() - start_time,
            time.strftime("%H:%M:%S"),
            storyline if isinstance(storyline, str) and len(storyline) else '[空]')
        )
    return storyline


def getStoryline_airav(number, debug, proxies, verify):
    try:
        site = secrets.choice(('airav.io', 'airair6.co',))
        url = f'https://{site}/searchresults.aspx?Search={number}&Type=0'
        session = httprequest.request_session(proxies=proxies, verify=verify, retry=0)
        res = session.get(url)
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
        detail_data = session.get(detail_url)
        if not detail_data.ok:
            raise ValueError(f"session.get('{detail_url}') failed")
        detail_page = etree.fromstring(detail_data.text, etree.HTMLParser(recover=True))
        titles = detail_page.xpath('//div[@class="video-title my-3"]/h1/text()')
        title = str(titles[0]).strip()
        # 添加排除"破坏版"关键字的逻辑
        if "破坏版" in title or "破壞版" in title:
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


def getStoryline_airavwiki(number, debug, proxies, verify):
    try:
        kwd = number[:6] if re.match(r'\d{6}[\-_]\d{2,3}', number) else number
        airavwiki = Airav()
        airavwiki.init()
        airavwiki.updateCore(core=None)
        airavwiki.addtion_Javbus = False
        airavwiki.proxies = proxies
        airavwiki.verify = verify
        jsons = airavwiki.search(kwd)
        outline = json.loads(jsons).get('outline')
        return outline
    except Exception as e:
        if debug:
            print(f"[-]MP getStoryline_airavwiki Error: {e}, number [{number}].")
        pass
    return ''


def getStoryline_58avgo(number, debug, proxies, verify):
    try:
        url = 'http://58avgo.com/cn/index.aspx' + secrets.choice([
            '', '?status=3', '?status=4', '?status=7', '?status=9', '?status=10', '?status=11', '?status=12',
                '?status=1&Sort=Playon', '?status=1&Sort=dateupload', 'status=1&Sort=dateproduce'
        ])  # 随机选一个，避免网站httpd日志中单个ip的请求太过单一
        kwd = number[:6] if re.match(r'\d{6}[\-_]\d{2,3}', number) else number
        result, browser = httprequest.get_html_by_form(url,
                                                       fields={'ctl00$TextBox_SearchKeyWord': kwd},
                                                       proxies=proxies, verify=verify,
                                                       return_type='browser')
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


def getStoryline_avno1(number, debug, proxies, verify):  # 获取剧情介绍 从avno1.cc取得
    try:
        site = secrets.choice(['avno1.cc', '1768av.club', '2nine.net', 'av999.tv',
                               'hotav.biz', 'javhq.tv',
                               'www.hdsex.cc', 'www.xxx18.cc',])
        url = f'http://{site}/cn/search.php?kw_type=key&kw={number}'
        data = httprequest.get_html_by_scraper(url, proxies=proxies, verify=verify)
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
                 if re.match(f'^{number}$', page_number, re.I) and title.rfind('破坏版') == -1:
                    return desc.strip()
            elif re.search(number, page_number, re.I):
                return desc.strip()
        raise ValueError(f"page number ->[{page_number}] not match")
    except Exception as e:
        if debug:
            print(f"[-]MP getOutline_avno1 Error: {e}, number [{number}].")
        pass
    return ''


def getStoryline_avno1OLD(number, debug, proxies, verify):  # 获取剧情介绍 从avno1.cc取得
    try:
        url = 'http://www.avno1.cc/cn/' + secrets.choice(['usercenter.php?item=' +
                                                          secrets.choice(['pay_support', 'qa', 'contact', 'guide-vpn']),
                                                          '?top=1&cat=hd', '?top=1', '?cat=hd', 'porn', '?cat=jp', '?cat=us', 'recommend_category.php'
                                                          ])  # 随机选一个，避免网站httpd日志中单个ip的请求太过单一
        result, browser = httprequest.get_html_by_form(url,
                                                       form_select='div.wrapper > div.header > div.search > form',
                                                       fields={'kw': number},
                                                       proxies=proxies, verify=verify,
                                                       return_type='browser')
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


def getStoryline_xcity(number, debug, proxies, verify):  # 获取剧情介绍 从xcity取得
    try:
        xcityEngine = Xcity()
        xcityEngine.init()
        xcityEngine.updateCore(core=None)
        xcityEngine.proxies = proxies
        xcityEngine.verify = verify
        jsons = xcityEngine.search(number)
        outline = json.loads(jsons).get('outline')
        return outline
    except Exception as e:
        if debug:
            print(f"[-]MP getOutline_xcity Error: {e}, number [{number}].")
        pass
    return ''
