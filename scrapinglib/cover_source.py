# -*- coding: utf-8 -*-
"""封面跨源回退（[cover_source]，方案 C）

背景：
    主刮削源的封面常常是低清图、带水印图，或者干脆拿不到。
    本模块按配置顺序依次向各站点取封面直链，第一个通过校验的胜出。

配置（config.ini）：
    [cover_source]
    switch = 0                            ; 总开关，默认关闭
    sources = dmm,javbus,mgstage,javdb    ; 从左到右依次尝试
    watermark_sources = javdb             ; 已知带水印的源
    allow_watermark = 0                   ; 0=不接受水印源（默认），1=前面全失败时接受
    min_width = 700                       ; 最小宽度，<=0 不校验
    debug = 0                             ; 日志；未配置时跟随 [debug_mode]

约束：
    - 只返回 URL，下载仍由 core.py 负责，不改变下载链路。
    - 任何异常都不向上抛，失败一律保留原封面。
    - 校验复用 dmm_image._probe_image（HTTP 状态 / Content-Type / 体积 / 分辨率）。
    - javdb 的 App CDN 地址是 XOR 加密流，下载器解不开，直接判为不可用。
"""

import importlib
import json
import re
from types import SimpleNamespace

import config

from . import dmm_image

# App CDN：返回的是加密流，当前下载链路无法解密，视为不可用
_ENCRYPTED_HOSTS = ("spfcas.com", "cmastd.com")


def _conf():
    return config.getInstance()


def _split(raw):
    """把逗号分隔的源名规整为小写、去空、去重、保持顺序的列表。"""
    seen = set()
    result = []
    for item in str(raw or '').split(','):
        name = item.strip().lower()
        if name and name not in seen:
            seen.add(name)
            result.append(name)
    return result


def _core_stub(proxies=None, verify=None):
    """构造刮削器 updateCore() 需要的轻量 core 对象（只取封面，不需要其它元数据）。"""
    dbsite = 'javdb'
    dbsites = []
    try:
        domains = _conf().javdb_domains()
        if domains:
            # 与 scraper.py 一致：dbsite 用 cookie_key（第二个元素）
            dbsite = domains[0][1]
            dbsites = list(domains)
    except Exception:
        pass
    return SimpleNamespace(proxies=proxies, verify=verify, morestoryline=False,
                           specifiedSource=None, specifiedUrl=None, debug=False,
                           dbsite=dbsite, dbcookies=None, dbsites=dbsites)


def _from_dmm(number, proxies=None, verify=None):
    """DMM 高清 CDN：复用 dmm_image 的前缀表 + GraphQL + 搜索页直连推导。"""
    url, reason = dmm_image.cross_source_cover_ex(number, proxies=proxies, verify=verify)
    return url, reason


def _from_scraper(module_name, class_name, number, proxies=None, verify=None):
    """用现成刮削器取封面（只关心 cover 字段）。"""
    try:
        module = importlib.import_module('.' + module_name, 'scrapinglib')
        parser = getattr(module, class_name)()
        data = parser.scrape(number, _core_stub(proxies, verify))
    except Exception as e:
        return '', f'请求异常({type(e).__name__})'
    if not data or data == 404:
        return '', '未找到'
    try:
        payload = json.loads(data)
    except Exception:
        return '', '返回无法解析'
    url = payload.get('cover') or ''
    return (url, '') if url else ('', '无封面字段')


_PROVIDERS = {
    'dmm': _from_dmm,
    'javbus': lambda n, proxies=None, verify=None: _from_scraper('javbus', 'Javbus', n, proxies, verify),
    'mgstage': lambda n, proxies=None, verify=None: _from_scraper('mgstage', 'Mgstage', n, proxies, verify),
    'javdb': lambda n, proxies=None, verify=None: _from_scraper('javdb', 'Javdb', n, proxies, verify),
}

# 这些源返回的地址已由自身校验过，不再重复请求
_PRE_VERIFIED = {'dmm'}


def _valid(url, proxies, verify, min_width):
    """校验候选封面：必须是 http(s) 直链，且通过 dmm_image 的图片探测。"""
    if not re.match(r'^https?://', url or '', re.IGNORECASE):
        return False
    if any(host in url for host in _ENCRYPTED_HOSTS):
        return False
    try:
        return dmm_image._probe_image(url, proxies=proxies, verify=verify,
                                      check_hd=True, min_width=min_width) == ''
    except Exception:
        return False


def fetch_cover(number, proxies=None, verify=None, exclude=None):
    """按配置顺序取封面。

    :param exclude: 已知不可用的封面地址集合（如下载阶段刚失败的那个），
                    命中即跳过，避免「换源」换回同一个坏地址。
    :return: (url, used_source, reasons)
        url 为空表示全部失败（调用方保留原封面）；
        reasons 为各源失败原因，便于 debug 日志排查。
    """
    conf = _conf()
    sources = _split(conf.cover_source_sources())
    if not sources or not number:
        return '', '', []
    skip_urls = {u for u in (exclude or ()) if u}
    watermark = set(_split(conf.cover_source_watermark_sources()))
    allow_watermark = conf.cover_source_allow_watermark()
    min_width = conf.cover_source_min_width()

    # 水印源降级：allow_watermark=0 时直接丢弃；=1 时排到非水印源之后
    ordered = [s for s in sources if s not in watermark]
    if allow_watermark:
        ordered += [s for s in sources if s in watermark]

    reasons = []
    for name in ordered:
        provider = _PROVIDERS.get(name)
        if provider is None:
            reasons.append(f'{name}: 不支持的源')
            continue
        try:
            url, why = provider(number, proxies=proxies, verify=verify)
        except Exception as e:
            url, why = '', f'异常({type(e).__name__})'
        if not url:
            reasons.append(f'{name}: {why or "未取到"}')
            continue
        if url in skip_urls:
            reasons.append(f'{name}: 与已失败地址相同，跳过')
            continue
        if name not in _PRE_VERIFIED and not _valid(url, proxies, verify, min_width):
            reasons.append(f'{name}: 校验未通过')
            continue
        if conf.cover_source_debug():
            print(f'[+]Cover source candidate: {name} -> {url}')
        return url, name, reasons

    return '', '', reasons


def available_sources():
    """返回当前支持的源名列表（供文档/调试）。"""
    return sorted(_PROVIDERS)
