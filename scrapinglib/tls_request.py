# -*- coding: utf-8 -*-
"""基于 curl_cffi 的 TLS 指纹请求（可选依赖）。

为什么需要：
  javdb 对「声明要 HTML」的请求会 302 到 /login（登录墙）。requests / cloudscraper
  的 TLS 指纹不像浏览器，补再多请求头也会被判定为脚本。curl_cffi 能把 TLS(J/A3)
  指纹伪装成指定浏览器版本，行为与真浏览器一致（实测 chrome136 可正常返回搜索页）。

设计：
  - 只给需要它的通道按需调用（目前是 javdb web 通道），不改全局 requests 逻辑；
  - 未安装 curl_cffi 时 get() 返回 None，调用方自动回退原逻辑（cloudscraper + Accept: */*）。
"""


class TlsResponse:
    """最小响应对象：只暴露调用方需要的 .text / .url / .status_code / .headers。"""

    __slots__ = ("text", "url", "status_code", "headers")

    def __init__(self, text, url, status_code, headers=None):
        self.text = text
        self.url = url
        self.status_code = status_code
        self.headers = headers or {}


def available() -> bool:
    """curl_cffi 是否可用（未安装时返回 False，不抛异常）。"""
    try:
        import curl_cffi  # noqa: F401
        return True
    except Exception:
        return False


def get(url, cookies=None, proxies=None, verify=None, timeout=30,
        impersonate="chrome150", headers=None):
    """用 curl_cffi 发起 GET 请求（TLS 指纹伪装成指定浏览器）

    :param url: 目标 URL
    :param cookies: cookie 字典
    :param proxies: requests 风格代理字典，如 {"http": "socks5h://h:p", "https": "socks5h://h:p"}
    :param verify: CA 证书路径或布尔值（None=默认）
    :param timeout: 超时(秒)
    :param impersonate: curl_cffi 浏览器指纹名（chrome136 / chrome131 / chrome124 / firefox133 ...）
    :param headers: 附加/覆盖请求头（默认不传，完全使用指纹自带的浏览器头）
    :return: TlsResponse；curl_cffi 不可用时返回 None（调用方自行回退）
    """
    try:
        from curl_cffi import requests as creq
    except Exception:
        return None

    kwargs = {"impersonate": impersonate or "chrome150", "timeout": timeout}
    if cookies:
        kwargs["cookies"] = cookies
    if proxies:
        kwargs["proxies"] = proxies
    if verify:
        kwargs["verify"] = verify
    if headers:
        kwargs["headers"] = headers

    resp = creq.get(url, **kwargs)
    return TlsResponse(resp.text or "", resp.url or url, resp.status_code,
                       dict(resp.headers or {}))
