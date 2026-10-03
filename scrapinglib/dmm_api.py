# -*- coding: utf-8 -*-
"""
DMM 官方 Affiliate API 客户端（参考 MDCx 的 dmm_api.py）。

背景：
    FANZA 是 DMM 的成人内容品牌（原 DMM.R18）。DMM 提供官方 Affiliate API：
        https://api.dmm.com/affiliate/v3/ItemList
    该 API 稳定、结构化（JSON），避免了爬详情页时页面结构变化的脆弱性。
    本模块用官方 API 按番号检索，作为 fanza 数据源的「稳定回退/增强」。

与 fanza.py 的关系：
    fanza.py 直接爬 www.dmm.co.jp/digital/videoa/ 详情页（即 FANZA 页面）；
    本模块走官方 API。二者内容同源（DMM/FANZA），互为补充：
    - fanza.py 解析的字段更贴近详情页（简介、样图等）
    - dmm_api.py 更稳、更全（有 review 评分、actress/director 结构化字段）

API 返回的关键字段（对照 MDCx dmm_digital_payload 响应字段补全）：
    result.items[].content_id     站内 cid（如 ssis00200 / 436abf00123）
    result.items[].title          标题
    result.items[].date           发售/配信日
    result.items[].volume         收录时间（分钟）
    result.items[].review.average 评分；review.count 评分人数
    result.items[].imageURL.large 封面大图（ps.jpg -> 升级 pl.jpg 横版）
    result.items[].sampleImageURL.sample_l.image[] 剧照（-N.jpg -> jp-N.jpg 高清）
    result.items[].sampleMovieURL 预告片直链（size_720_480 等分辨率）
    result.items[].iteminfo.*     结构化：actress/director/genre/maker/label/series
    result.items[].affiliateURL   推广链接

字段映射（API -> dictformat），参考 MDCx：
    packageImage.largeUrl -> cover
    sample2DMovie.url     -> trailer
    duration              -> runtime
    makerReleasedAt       -> release
    reviewSummary.average -> userrating

用法：
    from .dmm_api import search_by_number
    data = search_by_number("SSIS-200")   # 返回 dict 或 None
"""

import re
import json

import config

from . import httprequest


# 默认 API 凭据（来自 MDCx 的公开测试凭据；可在 config.ini 用 [dmm_api] 覆盖）
_DEFAULT_API_ID = "UrwskPfkqQ0DuVry2gYL"
_DEFAULT_AFFILIATE_ID = "10278-996"
_API_HOST = "https://api.dmm.com"
_API_PATH = "/affiliate/v3/ItemList"


def _api_id() -> str:
    try:
        v = config.getInstance().dmm_api_id()
        return v.strip() if v and v.strip() else _DEFAULT_API_ID
    except Exception:
        return _DEFAULT_API_ID


def _affiliate_id() -> str:
    try:
        v = config.getInstance().dmm_affiliate_id()
        return v.strip() if v and v.strip() else _DEFAULT_AFFILIATE_ID
    except Exception:
        return _DEFAULT_AFFILIATE_ID


def _build_api_url(**params) -> str:
    """构造 API URL。"""
    from urllib.parse import urlencode
    query = urlencode({
        "api_id": _api_id(),
        "affiliate_id": _affiliate_id(),
        "output": "json",
        **params,
    })
    return f"{_API_HOST}{_API_PATH}?{query}"


def _search_keywords(number: str):
    """DMM keyword 全文检索的候选序列。

    带横杠格式（SSIS-200）实测返回 0 结果；content_id 形态
    （小写前缀 + 编号补零到 5 位，如 ssis00200）可精确命中。
    特殊站内前缀番号（如 T28 系列 cid=55t2800645）转换后可能落空，
    回退小写厂牌词模糊搜索，交由 _find_best_item 打分挑选。
    """
    stripped = (number or "").strip()
    m = re.fullmatch(r"([A-Za-z0-9_]+)-(\d{1,5})", stripped)
    if not m:
        # 已是无横杠形态，直接作为关键词
        return [stripped.lower()]
    prefix, digits = m.group(1).lower(), m.group(2)
    return [f"{prefix}{digits.zfill(5)}", prefix]


def _clean(s: str) -> str:
    """去除非字母数字后小写，用于宽松比较。"""
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _match_score(item: dict, number_clean: str) -> int:
    """按匹配质量打分，数字段忽略前导零。

    DMM content_id 的编号段 5 位补零（如 sone00244），直接与去横杠后的番号
    （sone244）比较永远不等，故这里对数字段做整数比较。
    """
    cid = _clean(item.get("content_id", ""))
    pid = _clean(item.get("product_id", ""))
    nnum = _clean(number_clean)
    if cid == nnum:
        return 100
    m = re.search(r"([a-z]+)(?:\.)?(\d+)$", cid)
    num_m = re.search(r"([a-z]+)(\d+)$", number_clean)
    if m and num_m and m.group(1) == num_m.group(1) and int(m.group(2)) == int(num_m.group(2)):
        return 90
    if nnum and nnum in cid:
        score = 50
        if cid.startswith("9"):
            score -= 10
        return score
    if pid and pid == nnum:
        return 80
    return -1


def _find_best_item(items: list, number: str):
    """从候选中选出匹配分数最高的一条。"""
    number_clean = number.replace("-", "").lower()
    best = None
    best_score = -1
    for item in items:
        score = _match_score(item, number_clean)
        if score > best_score:
            best_score = score
            best = item
    return best if best_score >= 0 else None


def _first(values):
    return values[0] if values else ""


def _info_names(item: dict, key: str):
    """从 iteminfo 中提取某类字段的 name 列表。"""
    entries = (item.get("iteminfo") or {}).get(key, []) or []
    names = []
    for e in entries:
        if isinstance(e, dict):
            name = str(e.get("name", "")).strip()
            if name:
                names.append(name)
    return names


def _runtime(value) -> str:
    if value is None:
        return ""
    if isinstance(value, int):
        return str(value) if value > 0 else ""
    m = re.search(r"\d+", str(value))
    return m.group() if m else ""


def _score(review) -> str:
    if not isinstance(review, dict):
        return ""
    average = str(review.get("average", "") or "").strip()
    if average:
        return average
    m = re.search(r"[\d.]+", str(review))
    return m.group() if m else ""


def _review_count(review) -> str:
    """评分人数（review.count）。"""
    if not isinstance(review, dict):
        return ""
    for key in ("count", "reviewer_count"):
        v = review.get(key)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _thumb_url(item: dict) -> str:
    """取封面大图 URL。

    参考 MDCx DigitalParser.thumb：DMM 的图以 ps.jpg(竖版) 提供，
    升级为 pl.jpg(横版大图) 可获得更完整封面。这里优先 imageURL.large 并做 ps->pl。
    """
    img = item.get("imageURL") or {}
    for key in ("large", "small", "list"):
        url = img.get(key)
        if url:
            return url.replace("ps.jpg", "pl.jpg")
    return ""


def _sample_images(item: dict):
    """取样图 URL 列表（sample_l 优先，已升级到 jp- 高清）。"""
    for key in ("sample_l", "sample_s"):
        block = (item.get("sampleImageURL") or {}).get(key)
        if isinstance(block, dict):
            imgs = block.get("image", [])
            if imgs:
                # DMM 剧照高清规则: -N.jpg -> jp-N.jpg
                return [re.sub(r"-(\d+)\.jpg", r"jp-\1.jpg", u) for u in imgs]
    return []


def _sample_movie_url(item: dict) -> str:
    """取预告片直链。

    DMM Affiliate API 在 sampleMovieURL 下按分辨率提供多个 URL，
    如 size_720_480 / size_560_360 / size_476_306，优先取最高分辨率。
    """
    mv = item.get("sampleMovieURL")
    if not isinstance(mv, dict):
        return ""
    # 常见分辨率键，按从高到低优先级
    for key in ("size_720_480", "size_560_360", "size_476_306", "size_640_360"):
        url = mv.get(key)
        if url:
            return str(url).strip()
    # 兜底：取第一个 http 开头的值
    for v in mv.values():
        if isinstance(v, str) and v.startswith("http"):
            return v.strip()
    return ""


def _to_dict(item: dict, fallback_number: str) -> dict:
    """把 API item 映射为当前项目 dictformat 的字段结构。

    字段参考 MDCx 的 dmm_digital_payload / dmm_api 响应：
      packageImage.largeUrl -> cover（ps->pl 横版大图）
      sample2DMovie         -> trailer
      duration              -> runtime
      makerReleasedAt/deliveryStartDate -> release
      reviewSummary.average -> userrating
    """
    title = str(item.get("title") or "").strip()

    # 封面：imageURL.large 并升级 ps->pl（横版大图），保证有图且更完整。
    cover = _thumb_url(item)

    # 样图（已升级 jp- 高清）
    samples = _sample_images(item)

    release_raw = str(item.get("date") or "").strip()
    release = release_raw.split(" ")[0] if release_raw else ""

    actors = _info_names(item, "actress")
    directors = _info_names(item, "director")
    makers = _info_names(item, "maker")
    labels = _info_names(item, "label")
    series = _info_names(item, "series")

    return {
        "number": fallback_number,
        "title": title,
        "studio": _first(makers),
        "release": release,
        "outline": "",  # API 无简介，交由 storyline 补充
        "runtime": _runtime(item.get("volume")),
        "director": _first(directors),
        "actor": actors,
        "actor_photo": {},
        "cover": cover,
        "cover_small": "",
        "extrafanart": samples,
        "trailer": _sample_movie_url(item),
        "tag": _info_names(item, "genre"),
        "label": _first(labels),
        "series": _first(series),
        "userrating": _score(item.get("review")),
        "uservotes": _review_count(item.get("review")),
        "uncensored": False,
        "website": str(item.get("affiliateURL") or item.get("URL") or ""),
        "source": "fanza",
        "imagecut": 1,
    }


def search_by_number(number: str, proxies=None, verify=None, force=False):
    """用 DMM 官方 API 按番号检索，返回 dictformat 结构的 dict；失败返回 None。

    任何异常（网络/凭据/无匹配）都返回 None，由调用方回退到其它源。
    force=True 时忽略 [dmm_api] switch 开关强制尝试（用于详情页失败后的兜底）。
    """
    if not number:
        return None
    # 开关关闭时直接返回 None，保证默认行为与升级前一致（fanza 仍只爬详情页）；
    # force=True（详情页已失败）时跳过开关检查。
    if not force:
        try:
            if not config.getInstance().dmm_api_switch():
                return None
        except Exception:
            return None
    number = number.strip()

    for keyword in _search_keywords(number):
        api_url = _build_api_url(keyword=keyword, sort="match", hits="20")
        if config.getInstance().debug():
            print(f"[DmmApi] API URL: {api_url}")
        try:
            resp = httprequest.get(api_url, retry=1, timeout=12,
                                   proxies=proxies, verify=verify,
                                   extra_headers={"Accept": "application/json"})
        except Exception:
            continue
        if not resp or resp == 404:
            continue
        try:
            payload = json.loads(resp)
        except Exception:
            continue
        result = payload.get("result") or {}
        if result.get("status") != 200:
            if config.getInstance().debug():
                print(f"[DmmApi] API 错误: status={result.get('status')} msg={result.get('message')}")
            continue
        raw_items = result.get("items") or []
        if not raw_items:
            continue
        best = _find_best_item(raw_items, number)
        if not best:
            continue
        data = _to_dict(best, fallback_number=number)
        if not data.get("title") and not data.get("cover"):
            continue
        return data
    return None
