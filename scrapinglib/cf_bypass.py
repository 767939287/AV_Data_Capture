# -*- coding: utf-8 -*-
"""Cloudflare 挑战绕过客户端：统一对接 TRAWL / FlareSolverr 外部服务。

本模块把各站点刮削器的请求统一转换为对「外部 CF 绕过服务」的调用，并解析
其返回的 HTML 与 cookies，供 requests / cloudscraper 回填复用。

服务协议（来自 MDCx 与 TRAWL 官方文档）：
- FlareSolverr v1 兼容：POST {host}/v1
    请求体 {"cmd":"request.get"|"request.post","url","maxTimeout","postData"?}
    响应体 {"status":"ok"|"error","message"?,
            "solution":{"url","status","response","cookies":[...]}}
- TRAWL 原生：POST {host}/scrape（仅 GET/HEAD）
    请求体 {"url","maxTimeout","sessionId"?}
    响应体 {"url","statusCode","html","cookies":[...]}
  TRAWL 同时提供 FlareSolverr v1 兼容端点，故原生解析失败时可回退 /v1。
- 503=浏览器池初始化中，429=池饱和，二者属于可重试错误。

solver-first 调度：
- 白名单站点、或命中过 CF 挑战的站点直接走 solver（``should_use_solver``）；
- solver 成功后开 cookie 复用窗口，窗口内先直连（自动带 solver cookie），
  直连又被拦才清窗口、下次重新进 solver（见 mark_solver_success / mark_challenge_hit）。
"""

import re
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional
from urllib.parse import urlsplit, urlencode

import requests

import config

# CF 挑战响应特征标记，供诊断/可能的探测使用
CF_CHALLENGE_MARKERS = (
    'just a moment', 'cf-chl', 'cdn-cgi/challenge-platform', 'cf-challenge',
)

# 只有这些状态码 + 特征标记同时成立才判定为挑战页。
# 刻意不收 200：CF 也会给正常页面注入 /cdn-cgi/challenge-platform 脚本，
# 只按内容判断会把正常响应误判成挑战。
_CF_CHALLENGE_STATUS = (403, 503)

# CF 服务返回 5xx 时，正文里出现这些标记说明「目标被拦 / IP 被 ban」，重试无意义
_BLOCKED_MARKERS = (
    'has blocked this request', 'access denied', 'ip is banned', 'ip has been banned',
)

# 两次对 CF 服务请求之间的最小间隔(秒)，避免高频冲击服务
_MIN_REQUEST_INTERVAL = 2.0
# 可重试错误的默认最大重试轮数
_DEFAULT_RETRY_ROUNDS = 2
# solver 成功后 cookie 复用窗口的默认秒数（0 = 不复用，每次请求都走 solver）
DEFAULT_REUSE_TTL = 600.0

# ---------------------------------------------------------------------------
# solver-first 状态（进程内共享）
#
# 设计对齐 MDCx v20261108：
#   1. 白名单站点、或曾经命中过 Cloudflare 挑战的站点，后续请求不再先打直连，
#      直接交给外部 CF 服务；
#   2. solver 成功后开一个 cookie 复用窗口：窗口内该站点先直连（自动带上 solver
#      解出的 cf_clearance 等 cookie），直连又被拦才清窗口、下一次重新进 solver——
#      这是 FlareSolverr/TRAWL 的常规用法，不必每个请求都开浏览器解挑战；
#   3. solver 失败时由调用方回退直连，不判死。
#
# 状态随进程生命周期存在；加锁是因为刮削器可能多线程调度。
# ---------------------------------------------------------------------------
_state_lock = threading.Lock()
# host -> 命中 CF 挑战的次数（>0 即视为「该站后续走 solver」）
_challenge_hits: Dict[str, int] = {}
# host -> 复用窗口截止时刻（time.monotonic）
_reuse_until: Dict[str, float] = {}
# host -> solver 解出的 cookie（name -> value）
_solver_cookies: Dict[str, Dict[str, str]] = {}
# host -> solver 浏览器的 UA（与 cookie 配对使用，见 CFResult.user_agent）
_solver_ua: Dict[str, str] = {}


def host_of(url: str) -> str:
    """取 URL 的小写 hostname（解析失败返回空串）。

    :param url: 目标 URL
    :return: hostname，如 javdb.com
    """
    try:
        return (urlsplit(str(url or "")).hostname or "").strip().lower()
    except Exception:
        return ""


def normalize_proxy(proxy) -> str:
    """把调用方的代理配置归一化成 solver 可用的单个代理 URL。

    solver（FlareSolverr / TRAWL）只接受单个代理 URL，而 requests 的 proxies 是
    ``{"http": ..., "https": ...}`` 字典，且常写成 ``socks5h://``（远程 DNS）。
    solver 侧没有 socks5h/socks4a 语义，统一降级为 socks5:// / socks4://。

    透传代理的目的是让 solver 与直连**同出口**：出口 IP 不一致时，clearance 在
    严格站点上会被重新判定为挑战。

    :param proxy: requests 风格代理（dict / str / None）
    :return: 形如 socks5://host:port 的 URL；无可用代理返回空串
    """
    if not proxy:
        return ""
    if isinstance(proxy, dict):
        value = proxy.get("https") or proxy.get("http") or ""
    else:
        value = proxy
    value = str(value or "").strip()
    if not value:
        return ""
    if value.startswith("socks5h://"):
        value = "socks5://" + value[len("socks5h://"):]
    elif value.startswith("socks4a://"):
        value = "socks4://" + value[len("socks4a://"):]
    return value


def session_id_for(url: str) -> str:
    """按目标站点生成 solver 会话标识：``adc-{host}``（host 里的非法字符换成 ``-``）。

    当前未在请求路径上使用：TRAWL 的 ``sessionId`` 要求会话必须已通过
    ``POST /sessions`` 创建，临时拼接的 id 会直接 404，所以 /scrape 不再传它。

    :param url: 目标 URL
    :return: 会话标识；url 无 host 时返回空串（调用方据此不写该字段）
    """
    host = host_of(url)
    if not host:
        return ""
    safe = re.sub(r"[^a-z0-9.-]+", "-", host).strip("-")
    return f"adc-{safe}" if safe else ""


def solver_reuse_active(url: str) -> bool:
    """该站点是否还在 solver cookie 复用窗口内（窗口内优先直连带 cookie）。

    :param url: 目标 URL
    :return: 窗口有效返回 True；过期会顺手清理
    """
    host = host_of(url)
    if not host:
        return False
    with _state_lock:
        deadline = _reuse_until.get(host, 0.0)
        if deadline <= 0:
            return False
        if time.monotonic() >= deadline:
            _reuse_until.pop(host, None)
            return False
        return True


def mark_solver_success(url: str, cookies: Optional[Dict[str, str]] = None,
                        ttl: Optional[float] = None,
                        user_agent: Optional[str] = None) -> None:
    """solver 成功：记录该站点的 cookie/UA 并开（或续）复用窗口。

    :param url: 刚被 solver 解出的目标 URL
    :param cookies: solver 返回的 cookies（name -> value）
    :param ttl: 复用窗口秒数，缺省用 DEFAULT_REUSE_TTL；<=0 表示不复用
    :param user_agent: solver 浏览器的 UA；复用 cookie 时必须配套使用
    """
    host = host_of(url)
    if not host:
        return
    try:
        window = float(DEFAULT_REUSE_TTL if ttl is None else ttl)
    except (TypeError, ValueError):
        window = DEFAULT_REUSE_TTL
    if window <= 0:
        return
    with _state_lock:
        if cookies:
            _solver_cookies[host] = dict(cookies)
        if user_agent:
            _solver_ua[host] = str(user_agent)
        _reuse_until[host] = time.monotonic() + window


def mark_challenge_hit(url: str) -> None:
    """直连命中 CF 挑战：记一次账，并清掉复用窗口（解出的 cookie 已失效）。

    :param url: 命中挑战的请求 URL
    """
    host = host_of(url)
    if not host:
        return
    with _state_lock:
        _challenge_hits[host] = _challenge_hits.get(host, 0) + 1
        _reuse_until.pop(host, None)
        # 窗口失效时 cookie 与 UA 必须**成对丢弃**：留着单边的数据只会让下一次请求
        # 「带 cookie 但 UA 不匹配」，反而多一次 403（MDCx 铁律 #2）
        _solver_cookies.pop(host, None)
        _solver_ua.pop(host, None)


def solver_cookies_for(url: str) -> Dict[str, str]:
    """取 solver 为该站点解出的 cookie（副本；无则空 dict）。

    只在 cookie 复用窗口内返回：窗口过期后 clearance 基本失效，继续带上反而可能
    拿到「半失效」页面，所以直接视为没有。

    :param url: 目标 URL
    :return: cookie 字典
    """
    host = host_of(url)
    if not host or not solver_reuse_active(url):
        return {}
    with _state_lock:
        return dict(_solver_cookies.get(host) or {})


def solver_user_agent_for(url: str) -> str:
    """取 solver 为该站点解出的浏览器 UA（仅在 cookie 复用窗口内有效）。

    cf_clearance 与 UA 绑定：窗口内直连必须沿用 solver 的 UA，否则仍会被挑战。

    :param url: 目标 URL
    :return: UA 字符串；无则空串
    """
    host = host_of(url)
    if not host or not solver_reuse_active(url):
        return ""
    with _state_lock:
        return _solver_ua.get(host) or ""


def should_use_solver(url: str, conf) -> bool:
    """该请求是否跳过直连、直接交给外部 CF 服务。

    成立条件（任一，且总开关开启、不在 cookie 复用窗口内）：
      - 命中 ``[cloudflare] sites`` 白名单（首次也不试直连）；

      - 该 host 之前命中过 CF 挑战（``solver_first=1`` 时才启用自动升级）。

    例外：solver 刚解过、cookie 还在复用窗口内时先走直连——解一次用一段时间，
    直连又被拦会清掉窗口，下一次重新进 solver。

    :param url: 目标 URL
    :param conf: CloudflareConf 实例
    :return: 需要走 solver 返回 True
    """
    if not url or conf is None or not getattr(conf, "enable", False):
        return False
    if solver_reuse_active(url):
        return False
    if conf.is_whitelisted(url):
        return True
    if not getattr(conf, "solver_first", True):
        return False
    host = host_of(url)
    if not host:
        return False
    with _state_lock:
        return _challenge_hits.get(host, 0) > 0


def is_cf_challenge_response(response, text: Optional[str] = None) -> bool:
    """判断直连响应是否为 Cloudflare 挑战页。

    判据（避免误伤正常页面）：
      - 响应头 ``cf-mitigated: challenge``；或
      - 状态码 403/503 且正文含 CF 挑战特征标记。

    :param response: requests.Response（可为 mechanicalsoup 返回的响应）
    :param text: 可选的正文（缺省取 response.text）
    :return: 命中挑战返回 True
    """
    if response is None:
        return False
    headers = getattr(response, "headers", None) or {}
    try:
        if str(headers.get("cf-mitigated", "")).strip().lower() == "challenge":
            return True
    except Exception:
        pass
    try:
        status = int(getattr(response, "status_code", 0) or 0)
    except (TypeError, ValueError):
        status = 0
    if status not in _CF_CHALLENGE_STATUS:
        return False
    if text is None:
        try:
            text = response.text
        except Exception:
            text = ""
    lowered = str(text or "").lower()
    return any(marker in lowered for marker in CF_CHALLENGE_MARKERS)


# 直连「连接层失败」的异常类名（按 MRO 匹配，兼容 requests / urllib3 各版本）
_CONNECT_FAILURE_TYPES = ("ConnectionError", "ConnectTimeout", "SSLError")

# 本地环境类故障：代理挂掉/不可达不代表目标站点不可达，不能据此把站点判死
_LOCAL_FAILURE_TYPES = ("ProxyError",)


def is_connect_failure(error) -> bool:
    """判断异常是否为「直连在连接层就失败」（DNS 失败 / 握手被重置 / TLS 失败）。

    javdb 这类被 IP/SNI 层封锁的站点不会返回 CF 挑战页，而是 TLS 握手阶段就被
    RST，requests 只抛 ConnectionError。这种情况同样说明「该站直连不可用」，应
    当和命中 CF 挑战一样升级到 solver，否则 solver_first 永远触发不了。

    ProxyError 明确排除：代理故障是本机环境问题，不代表目标站点不可达。

    :param error: 捕获到的异常对象
    :return: 属于连接层失败返回 True
    """
    if error is None:
        return False
    names = {cls.__name__ for cls in type(error).__mro__}
    if any(name in names for name in _LOCAL_FAILURE_TYPES):
        return False
    return any(name in names for name in _CONNECT_FAILURE_TYPES)


def reset_state() -> None:
    """清空 solver-first 状态（供测试/诊断使用）。"""
    with _state_lock:
        _challenge_hits.clear()
        _reuse_until.clear()
        _solver_cookies.clear()
        _solver_ua.clear()


class CFBypassError(Exception):
    """CF 绕过服务调用异常。

    属性 retryable 标记该错误是否可重试（如 503 池预热 / 429 池饱和 / 连接失败）。
    """

    def __init__(self, message: str, retryable: bool = False):
        """构造异常。

        :param message: 错误描述
        :param retryable: 是否可重试
        """
        super().__init__(message)
        self.retryable = retryable


@dataclass
class CFResult:
    """CF 服务返回结果。"""

    html: str                 # 解出的页面 HTML
    cookies: Dict[str, str]   # 回填用 cookies（name->value）
    status: int               # 目标站点返回的 HTTP 状态码
    url: str                  # 最终落地 URL
    # solver 浏览器的 UA。cf_clearance 与 UA 强绑定：复用 cookie 直连时必须用同一个
    # UA，否则依旧 403 Just a moment（实测：项目 UA 403 / solver UA 200）
    user_agent: str = ""


class CFBypassClient:
    """CF 服务客户端，按 CloudflareConf 构造，复用一个 requests 会话。"""

    def __init__(self, conf):
        """构造客户端。

        :param conf: CloudflareConf 实例（来自 config.getInstance().cloudflare()）
        """
        self.conf = conf
        self.session = requests.Session()
        # 客户端超时略大于服务端 maxTimeout，避免服务端仍在解挑战时客户端先断开
        self.http_timeout = max(5, int(conf.timeout) + 5)
        # 上次请求时间戳，用于节流（保证最小间隔）
        self._last_request_ts = 0.0

    # ------------------------------------------------------------------
    # 内部工具方法
    # ------------------------------------------------------------------
    def _endpoint(self, path: str) -> str:
        """拼接服务端点 URL。

        :param path: 端点路径（如 '/v1'、'/scrape'）
        :return: 完整端点 URL
        """
        return self.conf.host.rstrip('/') + path

    def _throttle(self):
        """确保两次对 CF 服务的请求间隔不小于 _MIN_REQUEST_INTERVAL。

        通过 time.monotonic 计算距离上次请求的间隔，不足则补 sleep，避免服务被高频冲击。
        """
        now = time.monotonic()
        wait = _MIN_REQUEST_INTERVAL - (now - self._last_request_ts)
        if wait > 0:
            time.sleep(wait)
        self._last_request_ts = time.monotonic()

    def _post_json(self, url: str, payload: dict):
        """向 CF 服务发送 POST 请求并返回响应对象。

        :param url: 端点 URL
        :param payload: 请求体（会被序列化为 JSON）
        :return: requests.Response
        :raises CFBypassError: 传输层失败或返回 4xx/5xx（503/429 标记为可重试）
        """
        self._throttle()
        try:
            resp = self.session.post(url, json=payload, timeout=self.http_timeout)
        except requests.RequestException as e:
            # 连接失败/超时属于可重试错误
            raise CFBypassError(f"CF service request failed: {e}", retryable=True)
        if resp.status_code in (429, 503):
            # 429=池饱和，503=池预热中，交给上层退避重试
            raise CFBypassError(f"CF service busy ({resp.status_code})", retryable=True)
        if resp.status_code >= 500:
            # 服务端明确告知「目标被拦 / IP 被 ban」属于确定性失败，重试只是白等：
            # FlareSolverr 会抛 "Cloudflare has blocked this request"，TRAWL 的 500 也带同类信息。
            try:
                body = (resp.text or "")[:400]
            except Exception:
                body = ""
            if any(m in body.lower() for m in _BLOCKED_MARKERS):
                raise CFBypassError(f"CF service blocked target ({resp.status_code}): {body}")
            raise CFBypassError(f"CF service error ({resp.status_code})", retryable=True)
        if resp.status_code >= 400:
            raise CFBypassError(f"CF service bad request ({resp.status_code})")
        return resp

    @staticmethod
    def _resp_json(resp) -> dict:
        """安全解析响应 JSON 为 dict。

        :param resp: requests.Response
        :return: 解析后的 dict
        :raises CFBypassError: 响应体不是合法 JSON 对象
        """
        try:
            data = resp.json()
        except ValueError as e:
            raise CFBypassError(f"CF service returned invalid JSON: {e}")
        if not isinstance(data, dict):
            raise CFBypassError("CF service returned unexpected JSON type")
        return data

    @staticmethod
    def _extract_cookies(raw_cookies) -> Dict[str, str]:
        """把 CF 服务返回的 cookies 数组规范化为 {name: value} 字典。

        :param raw_cookies: CF 服务返回的 cookies 列表（形如 [{"name":..,"value":..}]）
        :return: cookies 字典（忽略缺失 name 的条目）
        """
        cookies: Dict[str, str] = {}
        if isinstance(raw_cookies, list):
            for item in raw_cookies:
                if isinstance(item, dict) and item.get("name") is not None:
                    cookies[str(item["name"])] = str(item.get("value", ""))
        return cookies

    def _parse_flaresolverr(self, data: dict, url: str) -> CFResult:
        """解析 FlareSolverr v1 兼容响应。

        :param data: 响应 JSON
        :param url: 请求的目标 URL（作为落地 URL 的兜底）
        :return: CFResult
        :raises CFBypassError: 响应状态非 ok 或缺少 solution
        """
        status = data.get("status")
        if status != "ok":
            message = data.get("message") or status or "unknown"
            raise CFBypassError(f"CF service returned error: {message}")
        solution = data.get("solution") or {}
        return CFResult(
            html=solution.get("response") or "",
            cookies=self._extract_cookies(solution.get("cookies")),
            # 注意：FlareSolverr 的 solution.status 恒为 200（Selenium 取不到真实状态码，
            # 源码内标注 # todo: fix），不能当作目标站点的真实 HTTP 状态使用。
            status=int(solution.get("status") or 0),
            url=solution.get("url") or url,
            user_agent=str(solution.get("userAgent") or ""),
        )

    def _parse_trawl_native(self, data: dict, url: str) -> CFResult:
        """解析 TRAWL 原生 /scrape 响应。

        :param data: 响应 JSON
        :param url: 请求的目标 URL（作为落地 URL 的兜底）
        :return: CFResult
        :raises CFBypassError: 响应不符合原生格式（交由上层回退 /v1）
        """
        if "html" not in data and "statusCode" not in data:
            raise CFBypassError("TRAWL native response unrecognized")
        return CFResult(
            html=data.get("html") or "",
            cookies=self._extract_cookies(data.get("cookies")),
            status=int(data.get("statusCode") or 0),
            url=data.get("url") or url,
            user_agent=str(data.get("userAgent") or ""),
        )

    def _verify_landing_url(self, result: CFResult):
        """校验 CF 返回的落地 URL 是否在 trusted_hosts 白名单内（防劫持）。

        :param result: CF 服务结果
        :raises CFBypassError: trusted_hosts 非空且落地域名不在白名单内
        """
        if not self.conf.trusted_hosts:
            return
        try:
            hostname = (urlsplit(result.url).hostname or "").lower()
        except Exception:
            hostname = ""
        for allowed in self.conf.trusted_hosts:
            allowed_host = allowed.split(":")[0].strip().lower()
            if allowed_host and (hostname == allowed_host or hostname.endswith("." + allowed_host)):
                return
        raise CFBypassError(f"Landing host '{hostname}' is not in cf_trusted_hosts")

    def _request_flaresolverr(self, url: str, method_up: str,
                              post_data: Optional[dict],
                              proxy_url: str = "") -> CFResult:
        """向 FlareSolverr v1 兼容端点发起请求并解析。

        :param url: 目标 URL
        :param method_up: 已大写的 HTTP 方法
        :param post_data: POST 表单数据
        :param proxy_url: 归一化后的代理 URL（空串=不让 solver 走代理）
        :return: CFResult
        :raises CFBypassError: 请求或解析失败
        """
        payload = {
            "cmd": "request.post" if method_up == "POST" else "request.get",
            "url": url,
            "maxTimeout": int(self.conf.timeout) * 1000,
        }
        if post_data:
            # FlareSolverr 的 postData 需要 form-encoded 字符串
            payload["postData"] = urlencode(post_data, doseq=True)
        if proxy_url:
            # FlareSolverr v1 的 proxy 形如 {"url": "socks5://host:port"}，
            # 让 solver 与直连走同一出口，避免 clearance 因 IP 不一致失效。
            payload["proxy"] = {"url": proxy_url}
        resp = self._post_json(self._endpoint("/v1"), payload)
        return self._parse_flaresolverr(self._resp_json(resp), url)

    def _request_once(self, url: str, method_up: str,
                      post_data: Optional[dict],
                      proxy_url: str = "") -> CFResult:
        """按配置的服务类型发起一次 CF 服务请求并解析结果。

        :param url: 目标 URL
        :param method_up: 已大写的 HTTP 方法
        :param post_data: POST 表单数据
        :param proxy_url: 归一化后的代理 URL（空串=不让 solver 走代理）
        :return: CFResult
        :raises CFBypassError: 请求或解析失败
        """
        # TRAWL 原生 /scrape 仅用于 GET；POST 或原生解析失败时回退 FlareSolverr v1
        if self.conf.service == "trawl" and method_up == "GET":
            try:
                payload = {"url": url, "maxTimeout": int(self.conf.timeout) * 1000}
                if proxy_url:
                    # TRAWL 原生 /scrape 的 proxy 是字符串，且为严格路由（不走直连层）
                    payload["proxy"] = proxy_url
                # 不要传 sessionId：TRAWL 要求该会话必须已通过 POST /sessions 创建，
                # 传一个临时拼出来的 id 会直接 404（白跑一次请求且触发节流）。
                resp = self._post_json(self._endpoint("/scrape"), payload)
                return self._parse_trawl_native(self._resp_json(resp), url)
            except CFBypassError as e:
                # 可重试类错误向上抛出；仅「格式不识别」等非可重试错误才回退 v1
                if e.retryable:
                    raise
                if self.conf.debug:
                    print(f"[CF] trawl native fallback to /v1: {e}")
        return self._request_flaresolverr(url, method_up, post_data, proxy_url)

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def bypass(self, url: str, method: str = 'GET',
               post_data: Optional[dict] = None, proxy=None) -> CFResult:
        """调用 CF 服务解析目标 URL 的挑战。

        :param url: 目标 URL
        :param method: 'GET' 或 'POST'
        :param post_data: POST 时的表单数据（dict）
        :param proxy: 本次请求使用的代理（requests 风格 dict/str），透传给 solver 对齐出口
        :return: CFResult
        :raises CFBypassError: 服务不可用/返回非 ok/落地域名校验失败
        """
        method_up = (method or "GET").upper()
        proxy_url = normalize_proxy(proxy)
        rounds = _DEFAULT_RETRY_ROUNDS
        last_error = None
        for attempt in range(rounds + 1):
            try:
                result = self._request_once(url, method_up, post_data, proxy_url)
                self._verify_landing_url(result)
                if self.conf.debug:
                    print(f"[CF] bypass ok: {url} -> {result.url} ({result.status})")
                return result
            except CFBypassError as e:
                last_error = e
                # 非可重试错误，或已达最大轮数，直接抛出
                if not e.retryable or attempt >= rounds:
                    raise
                # 退避递增：2s、4s ...（叠加节流保证最小间隔）
                delay = _MIN_REQUEST_INTERVAL * (attempt + 1)
                if self.conf.debug:
                    print(f"[CF] retry {attempt + 1}/{rounds}: {e} (wait {delay}s)")
                time.sleep(delay)
        raise last_error or CFBypassError("CF bypass failed")

    def to_dict_cookies(self, result: CFResult) -> Dict[str, str]:
        """规范化 CF 返回的 cookies 为 {name: value} dict。

        :param result: CF 服务结果
        :return: cookies 字典，供 requests.utils.add_dict_to_cookiejar 使用
        """
        return dict(result.cookies or {})


_CFBypassClient_cache = None  # 模块级客户端缓存，避免每次请求重建会话


def bypass_url(url: str, conf=None, method: str = 'GET',
               post_data: Optional[dict] = None, proxy=None) -> CFResult:
    """模块级便捷入口：内部取配置并缓存客户端实例。

    :param url: 目标 URL
    :param conf: 可选 CloudflareConf，缺省用 config.getInstance().cloudflare()
    :param method: 'GET' / 'POST'
    :param post_data: POST 表单
    :param proxy: 本次请求的代理（dict/str），透传给 solver 以对齐出口
    :return: CFResult
    :raises CFBypassError: 调用失败（含未启用时）
    """
    global _CFBypassClient_cache
    if conf is None:
        conf = config.getInstance().cloudflare()
    if not conf.enable:
        raise CFBypassError("cloudflare bypass is disabled")
    # 关键字段变化时重建客户端；否则复用，避免每次请求重建会话
    if (_CFBypassClient_cache is None
            or _CFBypassClient_cache.conf.host != conf.host
            or _CFBypassClient_cache.conf.service != conf.service
            or _CFBypassClient_cache.conf.timeout != conf.timeout):
        _CFBypassClient_cache = CFBypassClient(conf)
    return _CFBypassClient_cache.bypass(url, method=method, post_data=post_data, proxy=proxy)
