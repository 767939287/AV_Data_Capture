# -*- coding: utf-8 -*-
"""
DMM/FANZA 番号 -> cid 前缀表（移植自 MDCx 的 dmm_direct.py，精简版）。

背景：
    DMM 的 cid 并非总是「番号前缀 + 编号补零到 5 位」这一种形态。
    不同厂牌（series）的 cid 前缀不同，例如：
        SSIS-200  ->  ssis00200     (前缀为空)
        ABF-123   ->  436abf00123   (前缀 '436')
        MILK-123  ->  h_1240milk00123 (前缀 'h_1240')
        T28-645   ->  55t2800645    (前缀 '55')
    当前项目原先只会「前缀 + 补零」，遇到带特殊前缀的厂牌必然 404。
    本模块提供静态前缀表 + 番号解析，供 fanza.py 生成更完整的 cid 候选。

数据来源：
    MDCx (https://github.com/z291173301/MDCx) mdcx/crawlers/dmm_direct.py
    的 _PREFIX_GROUPS / _EXTRA_PREFIXES / _SPECIAL_THRESHOLDS / _COMMON_PREFIXES，
    （由 libredmm 全站 58.9 万番号↔cid 对归纳，覆盖 9627 系列）。

用法：
    from .dmm_prefix import parse_number, prefixes_for, digit_series
    for series, num, padded in parse_number("abf-123"):
        for prefix in prefixes_for(series, num):
            cid = f"{prefix}{series}{padded}"
"""

import re


# series(小写) -> 该分组共享的前缀（组键即前缀），覆盖 9627 系列 96.5% 直构命中。
# 组键 "" 表示前缀为空；组键 "1"/"13"/... 表示数字前缀；"h_113" 形式表示特殊站内前缀。
_PREFIX_GROUPS = {
    "": [
        "adn", "bf", "cawd", "cnd", "dasd", "dvdms", "ebod", "eyan", "gdhh", "hibl",
        "hmn", "hnd", "hntd", "ipit", "ipvr", "ipx", "ipzz", "jue", "jufd", "juk",
        "jul", "jux", "juy", "juq", "kawd", "meyd", "miab", "miad", "mibd", "mide",
        "midv", "mifd", "mtsp", "mudr", "mukd", "mvsd", "mymd", "nima", "ofje", "onsd",
        "pred", "rki", "sone", "sora", "ssis", "ssni", "waaa",
    ],
    "1": [
        "dandy", "dism", "dldss", "dvdes", "fcdss", "fset", "fsdss", "gs", "hunt", "kmhrs",
        "mmgh", "rct", "rctd", "sdab", "sdam", "sdde", "sdjs", "sdmf", "sdmm", "sdms",
        "sdmt", "sdmu", "sdfk", "sdnm", "star", "stars", "start", "svdvd", "sw", "vandr",
    ],
    "3": ["wanz"],
    "13": ["ayb", "gg", "gvg", "gvh", "ovg"],
    "17": ["bkd"],
    "18": ["momj", "ntrd"],
    "41": ["dok"],
    "42": ["sma"],
    "49": ["avop", "madm"],
    "55": ["t28"],
    "77": ["cre"],
    "118": ["onez"],
    "143": ["ppd", "umd"],
    "433": ["mbd"],
    "436": ["abf"],
    "5642": ["hodv"],
    "h_068": ["mxgs"],
    "h_113": ["ggg"],
    "h_205": ["ssnd"],
    "h_491": ["fone"],
    "h_1100": ["hzgd"],
    "h_1240": ["milk"],
    "h_1324": ["skmj"],
    "h_1371": ["zmen"],
    "h_1374": ["ksvr"],
    "h_1454": ["bdsr", "husr"],
    "h_189": ["ymd"],
    "h_237": ["nact"],
    "h_910": ["vrtm"],
    "h_995": ["bokd"],
}

# 同名系列跨厂商/跨编号段前缀不同，附加候选前缀兜底
_EXTRA_PREFIXES = {
    "sw": ["h_113"],
    "bdsr": ["57"],
    "husr": ["57"],
    "sma": ["83"],
}

# 特殊阈值：series -> (阈值, 小于等于用前缀, 大于用前缀)
_SPECIAL_THRESHOLDS = {
    "avop": (168, "", "1"),
    "gigl": (643, "h_860", ""),
    "ekdv": (655, "49", ""),
}

# 兜底常见前缀（当 series 不在任何分组时尝试）
_COMMON_PREFIXES = ["", "1", "13", "49", "436", "118", "55", "57", "83", "5642"]

# 含数字的 series（如 t28），解析时需要优先长匹配
_DIGIT_SERIES = sorted(
    {s for members in _PREFIX_GROUPS.values() for s in members if any(ch.isdigit() for ch in s)},
    key=len,
    reverse=True,
)


def parse_number(number: str):
    """把番号解析为 [(series, num:int, padded:str), ...]。

    通常返回单个元组；解析失败返回空列表。
        'abf-123'  -> [('abf', 123, '00123')]
        't28-645'  -> [('t28', 645, '00645')]
        'sone-244' -> [('sone', 244, '00244')]
    """
    if not isinstance(number, str):
        return []
    cleaned = number.lower().strip().replace("-", "").replace(" ", "")
    # 含数字的 series（如 t28）先长匹配
    for series in _DIGIT_SERIES:
        if cleaned.startswith(series):
            rest = cleaned[len(series):]
            if rest.isdigit() and rest:
                return [(series, int(rest), f"{int(rest):05d}")]
    m = re.match(r"^([a-z]+)(\d+)$", cleaned)
    if not m:
        return []
    series, digits = m.group(1), m.group(2)
    return [(series, int(digits), f"{int(digits):05d}")]


def prefixes_for(series: str, num: int):
    """返回该 series+编号 可能使用的前缀候选（保持优先级顺序，去重）。"""
    extra = _EXTRA_PREFIXES.get(series, [])
    if series in _SPECIAL_THRESHOLDS:
        threshold, small_prefix, large_prefix = _SPECIAL_THRESHOLDS[series]
        prefix = small_prefix if num <= threshold else large_prefix
        return list(dict.fromkeys([prefix] + extra))
    for group_prefix, members in _PREFIX_GROUPS.items():
        if series in members:
            # 组前缀优先，空串（无前缀）兜底
            return list(dict.fromkeys([group_prefix, ""] + extra))
    return list(dict.fromkeys(_COMMON_PREFIXES + extra))


def cid_candidates(number: str):
    """由番号生成 DMM/FANZA 可能的 cid 候选（保持优先级顺序，去重）。

    生成规则：对每个解析出的 (series, num, padded)，枚举其前缀候选，
    组合成 f"{prefix}{series}{padded}"。同时保留番号本身（去横杠）作为兜底。
    """
    seen = set()
    result = []

    def _add(cid):
        if cid and cid not in seen:
            seen.add(cid)
            result.append(cid)

    for series, num, padded in parse_number(number):
        for prefix in prefixes_for(series, num):
            _add(f"{prefix}{series}{padded}")
    # 兜底：原始规范化形态（去除非字母数字后的小写）
    fallback = re.sub(r"[^0-9a-z]", "", str(number or "").lower())
    _add(fallback)
    return result
