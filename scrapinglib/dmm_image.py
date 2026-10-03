# -*- coding: utf-8 -*-
"""
DMM/FANZA 高清图升级（轻量版，方案 A）

背景：
    FANZA/DMM 详情页返回的封面常为低清图（域名为 pics.dmm.co.jp），且 og:image 多为竖版。
    DMM 同时把同一张图托管在高清 CDN awsimgsrc.dmm.co.jp/pics_dig 上，并同时提供
    横版 pl.jpg 与竖版 ps.jpg。

策略（轻量替换，不改动任何刮削主逻辑）：
    1. 若图片 URL 来自 pics.dmm.co.jp，尝试替换主机为 awsimgsrc.dmm.co.jp/pics_dig，
       并去掉路径中的 /adult/（DMM 高清 CDN 不含该段）。
    2. 封面（cover）额外尝试横版 pl.jpg 候选（DMM 横版通常更清晰、无黑边）。
    3. 逐个候选发 HEAD 请求校验，命中即替换；全部失败则保留原图，绝不报错中断。

开关（config.ini）：
    [dmm_image]
    switch = 0/1   默认 0（关闭，行为与升级前完全一致）

默认行为（未配置或读取异常时）：
    scope = cover,poster     只升级封面与海报
    aspect = landscape       封面优先横版
    verify = head            用 HEAD 请求校验候选
    cdn = awsimgsrc          升级到 DMM 官方高清 CDN

使用方式（在需要处调用，异常安全）：
    from . import dmm_image
    url = dmm_image.upgrade_cover(url)   # 封面
    url = dmm_image.upgrade_poster(url)  # 海报
"""

import config

from . import httprequest
from urllib.parse import urlsplit


# DMM 相关域名后缀，用于判断是否值得尝试升级
_DMM_HOST_SUFFIX = ("dmm.co.jp", "dmm.com")


def _is_dmm_url(url: str) -> bool:
    """判断 URL 是否属于 DMM 域名。"""
    if not isinstance(url, str) or not url:
        return False
    normalized = url.strip()
    if normalized.startswith("//"):
        normalized = "https:" + normalized
    try:
        host = urlsplit(normalized).netloc.lower()
    except Exception:
        return False
    return any(host.endswith(suffix) for suffix in _DMM_HOST_SUFFIX)


def _with_https(url: str) -> str:
    """统一为 https 协议（DMM 图用 https 更稳）。"""
    url = (url or "").strip()
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://"):
        return "https://" + url[len("http://"):]
    return url


def _to_aws_hd(url: str) -> str:
    """pics.dmm.co.jp 低清图 -> awsimgsrc.dmm.co.jp/pics_dig 高清图。

    已是 awsimgsrc 域名或非 pics.dmm 域名时返回空串（表示无需/无法升级）。
    """
    normalized = _with_https(url)
    if "pics.dmm.co.jp" not in normalized:
        return ""
    return normalized.replace(
        "pics.dmm.co.jp", "awsimgsrc.dmm.co.jp/pics_dig"
    ).replace("/adult/", "/")


def _strip_query(url: str) -> str:
    """去掉 URL 的查询串，避免 ?v=... 之类参数干扰后缀判断。"""
    return url.split("?", 1)[0]


def _to_landscape(url: str) -> str:
    """竖版 ps.jpg -> 横版 pl.jpg；非 ps.jpg 结尾返回空串。"""
    base = _strip_query(url)
    if base.endswith("ps.jpg"):
        return base[:-len("ps.jpg")] + "pl.jpg"
    return ""


def _to_portrait(url: str) -> str:
    """横版 pl.jpg -> 竖版 ps.jpg；非 pl.jpg 结尾返回空串。"""
    base = _strip_query(url)
    if base.endswith("pl.jpg"):
        return base[:-len("pl.jpg")] + "ps.jpg"
    return ""


def _valid_image(url: str, proxies=None, verify=None) -> bool:
    """用 HEAD 请求校验图片是否存在且为图片。

    DMM 对不存在的图通常返回 404；此处同时过滤 Content-Type 非图片、
    以及体积极小的占位图（<4KB）。
    """
    try:
        resp = httprequest.get(url, return_type="object", retry=1, timeout=8,
                               proxies=proxies, verify=verify)
        if resp is None:
            return False
        if resp.status_code >= 400:
            return False
        content_type = resp.headers.get("Content-Type", "")
        if content_type and not content_type.startswith("image/"):
            return False
        length = resp.headers.get("Content-Length")
        if length is not None and str(length).isdigit() and int(length) < 4096:
            return False
        return True
    except Exception:
        return False


def _dedupe(urls):
    """去重并保持顺序。"""
    seen = set()
    result = []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            result.append(u)
    return result


def _switch_on() -> bool:
    """读取开关；任何异常都视为关闭（保持默认行为）。"""
    try:
        return config.getInstance().dmm_image_switch()
    except Exception:
        return False


def upgrade_cover(url: str, proxies=None, verify=None) -> str:
    """尝试把 DMM 封面升级为高清 CDN（横版优先）。

    开关关闭、URL 非 DMM、或候选全部校验失败时，原样返回 url。
    """
    if not _switch_on() or not _is_dmm_url(url):
        return url
    original = _with_https(url)
    aws = _to_aws_hd(original)
    if not aws:
        # 已是 awsimgsrc 高清图，仅尝试统一为横版
        if "awsimgsrc.dmm.co.jp" in original:
            landscape = _to_landscape(original)
            if landscape and _valid_image(landscape, proxies=proxies, verify=verify):
                return landscape
        return url
    candidates = _dedupe([
        _to_landscape(aws),  # 横版高清优先
        aws,                 # 竖版高清
    ])
    for candidate in candidates:
        if _valid_image(candidate, proxies=proxies, verify=verify):
            return candidate
    return url


def upgrade_poster(url: str, proxies=None, verify=None) -> str:
    """尝试把 DMM 海报升级为高清 CDN（竖版）。

    开关关闭、URL 非 DMM、或候选校验失败时，原样返回 url。
    """
    if not _switch_on() or not _is_dmm_url(url):
        return url
    original = _with_https(url)
    aws = _to_aws_hd(original)
    if not aws:
        # 已是 awsimgsrc 高清图，仅确保竖版
        if "awsimgsrc.dmm.co.jp" in original:
            portrait = _to_portrait(original)
            if portrait and _valid_image(portrait, proxies=proxies, verify=verify):
                return portrait
        return url
    if _valid_image(aws, proxies=proxies, verify=verify):
        return aws
    return url
