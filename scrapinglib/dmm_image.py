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

import re
import config

from lxml import etree

from . import httprequest
from urllib.parse import quote, urlsplit


# DMM 相关域名后缀，用于判断是否值得尝试升级
_DMM_HOST_SUFFIX = ("dmm.co.jp", "dmm.com")

# DMM 高清封面 CDN 模板：
#   https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/{cid}/{cid}ps.jpg  (竖版)
#   https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/{cid}/{cid}pl.jpg  (横版)
_AWS_COVER_TMPL = "https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/{cid}/{cid}{suffix}.jpg"


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


# DMM 高清图最小宽度阈值：awsimgsrc 同一 URL 格式下可能返回 147x200 缩略图，
# 仅靠 HEAD/Content-Length 无法区分，需读分辨率过滤（参考 MDCx 的 _is_dmm_hd_image）。
_DMM_HD_MIN_WIDTH = 700


def _parse_image_size(data: bytes):
    """从图片字节流解析 (width, height)，支持 JPEG 与 PNG。解析失败返回 (0, 0)。"""
    if not data or len(data) < 24:
        return 0, 0
    try:
        # PNG: 签名 8 字节后为 IHDR，宽高各 4 字节大端（偏移 16/20）
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            w = int.from_bytes(data[16:20], "big")
            h = int.from_bytes(data[20:24], "big")
            return w, h
        # JPEG: 扫描 SOF 段（0xFFC0-0xFFCF，排除 C4/C8/CC）
        if data[:2] == b"\xff\xd8":
            i = 2
            n = len(data)
            while i + 9 < n:
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                # SOF0..SOF15（不含 DHT=0xC4, JPG=0xC8, DAC=0xCC）
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    h = int.from_bytes(data[i + 5:i + 7], "big")
                    w = int.from_bytes(data[i + 7:i + 9], "big")
                    return w, h
                if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                seg_len = int.from_bytes(data[i + 2:i + 4], "big")
                if seg_len < 2:
                    break
                i += 2 + seg_len
            return 0, 0
    except Exception:
        return 0, 0
    return 0, 0


def _valid_image(url: str, proxies=None, verify=None, check_hd: bool = False) -> bool:
    """校验图片是否存在且有效。

    DMM 对不存在的图通常返回 404；此处同时过滤 Content-Type 非图片、
    以及体积极小的占位图（<4KB）。

    check_hd=True 时额外读取图片分辨率，宽 < 700 视为缩略图/占位图拒收
    （参考 MDCx：awsimgsrc 同一 URL 可能返回 147x200 缩略图，需按尺寸过滤）。
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
        if check_hd:
            # 读取前若干字节解析分辨率（JPEG 的 SOF 段通常在前几 KB 内）
            data = httprequest.get(url, return_type="content", retry=1, timeout=8,
                                   proxies=proxies, verify=verify)
            width, _height = _parse_image_size(data or b"")
            if width and width < _DMM_HD_MIN_WIDTH:
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
            if landscape and _valid_image(landscape, proxies=proxies, verify=verify, check_hd=True):
                return landscape
        return url
    candidates = _dedupe([
        _to_landscape(aws),  # 横版高清优先
        aws,                 # 竖版高清
    ])
    for candidate in candidates:
        if _valid_image(candidate, proxies=proxies, verify=verify, check_hd=True):
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
            if portrait and _valid_image(portrait, proxies=proxies, verify=verify, check_hd=True):
                return portrait
        return url
    if _valid_image(aws, proxies=proxies, verify=verify, check_hd=True):
        return aws
    return url


# ============================================================================
# 路线 2：跨源升级
#   即使用别的刮削器（javbus/javdb 等），也按番号去 DMM 取高清封面来替换。
#   流程：番号 -> DMM 搜索页 -> 解析详情页 cid -> 构造 awsimgsrc 高清直链 -> 校验
# ============================================================================

# DMM 详情页 URL 中以 cid= 携带真实站内编号（如 ssis00200 / h_1240milk00123）
_CID_RE = re.compile(r"cid=([0-9a-zA-Z_]+)", re.IGNORECASE)
# DMM 搜索结果中详情页链接的通用形态
_DETAIL_HREF_RE = re.compile(r"/(?:digital|mono|rental|prime|monthly|anime)/[^\"'#?]*/-/detail/=/cid=([0-9a-zA-Z_]+)",
                             re.IGNORECASE)


def _normalize_number(number: str) -> str:
    """把番号规整为 DMM 搜索更易命中的形态（保留字母数字与横杠，小写）。

    例：'SSIS-200' -> 'ssis-200'，'ssis 200' -> 'ssis200'。
    """
    if not isinstance(number, str):
        return ""
    number = number.strip().lower()
    number = re.sub(r"[^0-9a-z\-]", "", number)
    return number


def _cid_candidates_from_number(number: str):
    """由番号直接推导可能的 DMM cid 候选（无需搜索即可命中的常见情况）。

    优先使用 dmm_prefix 前缀表（可处理带厂牌前缀的 cid，如 abf123 -> 436abf00123），
    再保留「前缀+补零 / 无横杠」兜底形态。最终仍以搜索页解析出的 cid 为准。
    """
    normalized = _normalize_number(number)
    if not normalized:
        return []
    candidates = []
    # 1) 前缀表候选（覆盖特殊厂牌前缀）
    try:
        from .dmm_prefix import cid_candidates
        candidates.extend(cid_candidates(normalized))
    except Exception:
        pass
    # 2) 兜底：前缀+数字补零到5位 / 无横杠形态
    m = re.match(r"^([a-z]+)-?(\d+)$", normalized)
    if m:
        prefix, digits = m.group(1), m.group(2)
        candidates.append(f"{prefix}{int(digits):05d}")  # ssis + 00200
    candidates.append(normalized.replace("-", ""))
    # 去重保序
    seen = set()
    result = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            result.append(c)
    return result


def get_dmm_cid(number: str, proxies=None, verify=None) -> str:
    """通过 DMM 搜索页解析出该番号对应的站内 cid。

    优先解析搜索结果里分值最高的详情页链接；解析不到则返回空串。
    """
    normalized = _normalize_number(number)
    if not normalized:
        return ""
    # DMM 搜索对 00 补零/无补零两种形态都有结果，这里两种都试
    search_numbers = [normalized]
    if re.match(r"^[a-z]+\d+$", normalized):
        m = re.match(r"^([a-z]+)(\d+)$", normalized)
        prefix, digits = m.group(1), m.group(2)
        if len(digits) < 5:
            search_numbers.append(f"{prefix}-{int(digits):05d}")
        search_numbers.append(f"{prefix}-{digits}")
    search_numbers = _dedupe(search_numbers)

    for s_num in search_numbers:
        url = "https://www.dmm.co.jp/search/=/searchstr=" + quote(s_num) + "/"
        try:
            html = httprequest.get(url, retry=1, timeout=12,
                                   proxies=proxies, verify=verify)
        except Exception:
            continue
        if not html or html == 404:
            continue
        # 从 HTML 中直接提取详情页 cid（比 xpath 更稳，覆盖脚本内嵌链接）
        cids = _DETAIL_HREF_RE.findall(html)
        if not cids:
            # 兜底：任意 cid= 形态
            cids = _CID_RE.findall(html)
        cids = _dedupe([c for c in cids if c])
        if not cids:
            continue
        # 选与番号数字部分最匹配的 cid（例如 ssis-200 优先匹配 ssis00200）
        normalized_digits = re.sub(r"\D", "", normalized)
        for cid in cids:
            if normalized_digits and normalized_digits.lstrip("0") in cid.lstrip("0"):
                return cid
        return cids[0]
    return ""


def build_aws_cover_url(cid: str) -> str:
    """由 cid 构造 DMM 高清封面直链（竖版 ps.jpg）。"""
    if not cid:
        return ""
    return _AWS_COVER_TMPL.format(cid=cid, suffix="ps")


def cross_source_cover(number: str, proxies=None, verify=None) -> str:
    """跨源高清封面：按番号从 DMM 取高清封面直链，取不到返回空串。

    仅返回通过 HEAD 校验的地址；任何失败都返回空串，由调用方保留原源封面。
    """
    if not number:
        return ""
    # 1) 直接由番号推导 cid 候选（快，命中常见系列）
    for cid in _cid_candidates_from_number(number):
        for suffix in ("pl", "ps"):  # 横版优先
            candidate = _AWS_COVER_TMPL.format(cid=cid, suffix=suffix)
            if _valid_image(candidate, proxies=proxies, verify=verify, check_hd=True):
                return candidate
    # 2) 走 DMM 搜索页拿到真实 cid（覆盖特殊前缀/组合系列）
    cid = get_dmm_cid(number, proxies=proxies, verify=verify)
    if cid:
        for suffix in ("pl", "ps"):
            candidate = _AWS_COVER_TMPL.format(cid=cid, suffix=suffix)
            if _valid_image(candidate, proxies=proxies, verify=verify, check_hd=True):
                return candidate
    return ""
