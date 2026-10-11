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
    请求体 {"url","maxTimeout"}
    响应体 {"url","statusCode","html","cookies":[...]}
  TRAWL 同时提供 FlareSolverr v1 兼容端点，故原生解析失败时可回退 /v1。
- 503=浏览器池初始化中，429=池饱和，二者属于可重试错误。
"""

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

# 两次对 CF 服务请求之间的最小间隔(秒)，避免高频冲击服务
_MIN_REQUEST_INTERVAL = 2.0
# 可重试错误的默认最大重试轮数
_DEFAULT_RETRY_ROUNDS = 2


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
            status=int(solution.get("status") or 0),
            url=solution.get("url") or url,
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
                              post_data: Optional[dict]) -> CFResult:
        """向 FlareSolverr v1 兼容端点发起请求并解析。

        :param url: 目标 URL
        :param method_up: 已大写的 HTTP 方法
        :param post_data: POST 表单数据
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
        resp = self._post_json(self._endpoint("/v1"), payload)
        return self._parse_flaresolverr(self._resp_json(resp), url)

    def _request_once(self, url: str, method_up: str,
                      post_data: Optional[dict]) -> CFResult:
        """按配置的服务类型发起一次 CF 服务请求并解析结果。

        :param url: 目标 URL
        :param method_up: 已大写的 HTTP 方法
        :param post_data: POST 表单数据
        :return: CFResult
        :raises CFBypassError: 请求或解析失败
        """
        # TRAWL 原生 /scrape 仅支持 GET/HEAD；POST 或原生解析失败时回退 FlareSolverr v1
        if self.conf.service == "trawl" and method_up == "GET":
            try:
                payload = {"url": url, "maxTimeout": int(self.conf.timeout) * 1000}
                resp = self._post_json(self._endpoint("/scrape"), payload)
                return self._parse_trawl_native(self._resp_json(resp), url)
            except CFBypassError as e:
                # 可重试类错误向上抛出；仅「格式不识别」等非可重试错误才回退 v1
                if e.retryable:
                    raise
                if config.getInstance().debug():
                    print(f"[CF] trawl native fallback to /v1: {e}")
        return self._request_flaresolverr(url, method_up, post_data)

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def bypass(self, url: str, method: str = 'GET',
               post_data: Optional[dict] = None) -> CFResult:
        """调用 CF 服务解析目标 URL 的挑战。

        :param url: 目标 URL
        :param method: 'GET' 或 'POST'
        :param post_data: POST 时的表单数据（dict）
        :return: CFResult
        :raises CFBypassError: 服务不可用/返回非 ok/落地域名校验失败
        """
        method_up = (method or "GET").upper()
        rounds = _DEFAULT_RETRY_ROUNDS
        last_error = None
        for attempt in range(rounds + 1):
            try:
                result = self._request_once(url, method_up, post_data)
                self._verify_landing_url(result)
                if config.getInstance().debug():
                    print(f"[CF] bypass ok: {url} -> {result.url} ({result.status})")
                return result
            except CFBypassError as e:
                last_error = e
                # 非可重试错误，或已达最大轮数，直接抛出
                if not e.retryable or attempt >= rounds:
                    raise
                # 退避递增：2s、4s ...（叠加节流保证最小间隔）
                delay = _MIN_REQUEST_INTERVAL * (attempt + 1)
                if config.getInstance().debug():
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
               post_data: Optional[dict] = None) -> CFResult:
    """模块级便捷入口：内部取配置并缓存客户端实例。

    :param url: 目标 URL
    :param conf: 可选 CloudflareConf，缺省用 config.getInstance().cloudflare()
    :param method: 'GET' / 'POST'
    :param post_data: POST 表单
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
    return _CFBypassClient_cache.bypass(url, method=method, post_data=post_data)
