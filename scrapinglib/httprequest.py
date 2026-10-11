# -*- coding: utf-8 -*-

import mechanicalsoup
import requests
from requests.adapters import HTTPAdapter
from requests.cookies import RequestsCookieJar
from requests.models import Response
from urllib3.util.retry import Retry
from cloudscraper import create_scraper

import config

G_USER_AGENT = r'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.133 Safari/537.36'
G_DEFAULT_TIMEOUT = 10


def _try_cf_bypass(url: str, method: str = 'GET', post_data=None):
    """ 若 URL 命中 CF 白名单且服务已启用，则调用外部 CF 服务返回结果

    :param url: 目标 URL
    :param method: 'GET' / 'POST'
    :param post_data: POST 表单数据（dict），GET 时为 None
    :return: 命中并成功返回 cf_bypass.CFResult；未命中或异常回退返回 None（表示走原直连逻辑）

    仅在 [cloudflare] switch=1 且 URL host 命中 sites 白名单时生效；
    其余情况（未启用 / 未命中 / 调用异常）一律返回 None，交由调用方走原有逻辑。
    """
    try:
        conf = config.getInstance().cloudflare()
        if not conf.enable or not conf.is_whitelisted(url):
            return None
        # lazy import：避免与包初始化形成循环导入，也保证未启用时零额外开销
        from . import cf_bypass
        result = cf_bypass.bypass_url(url, conf=conf, method=method, post_data=post_data)
        if config.getInstance().debug():
            print(f"[+]CF bypass: {method} {url}")
        return result
    except Exception as e:
        # 任何异常都回退到原直连逻辑；非 debug 模式保持安静
        if config.getInstance().debug():
            print(f"[-]CF bypass failed: {url} {e}")
        return None


def _make_cf_response(cf, request=None):
    """ 把 CF 服务结果合成为 requests.Response，供依赖 response 接口的调用方复用

    :param cf: cf_bypass.CFResult
    :param request: 原始 PreparedRequest（可选，仅用于回填 resp.request）
    :return: 合成的 requests.Response（.text/.content/.url/.status_code/.cookies 可用）
    """
    resp = Response()
    resp.status_code = cf.status or 200
    resp.url = cf.url
    resp.request = request
    resp.headers = {}
    resp.encoding = "utf-8"
    resp._content = (cf.html or "").encode("utf-8")
    jar = RequestsCookieJar()
    for k, v in (cf.cookies or {}).items():
        jar.set(k, v)
    resp.cookies = jar
    return resp


def get(url: str, cookies=None, ua: str = None, extra_headers=None, return_type: str = None, encoding: str = None,
        retry: int = 3, timeout: int = G_DEFAULT_TIMEOUT, proxies=None, verify=None):
    """
    网页请求核心函数

    是否使用代理应由上层处理
    """
    errors = ""
    headers = {"User-Agent": ua or G_USER_AGENT}
    if extra_headers != None:
        headers.update(extra_headers)
    # 命中 CF 白名单时优先走外部 CF 服务（未启用/未命中返回 None，原逻辑保持不变）
    cf = _try_cf_bypass(url, 'GET')
    if cf is not None:
        if return_type == "content":
            return (cf.html or "").encode(encoding or "utf-8")
        elif return_type == "object":
            return _make_cf_response(cf)
        else:
            return cf.html or ""
    for i in range(retry):
        try:
            allowRedirects = False
            if 'www.dmm.co.jp' in url:
                allowRedirects = True
            result = requests.get(url, headers=headers, timeout=timeout, proxies=proxies,
                                  verify=verify, cookies=cookies, allow_redirects=allowRedirects)
            if return_type == "object":
                return result
            elif return_type == "content":
                return result.content
            else:
                result.encoding = encoding or result.apparent_encoding
                return result.text
        except Exception as e:
            if config.getInstance().debug():
                print(f"[-]Connect: {url} retry {i + 1}/{retry}")
            errors = str(e)
    if config.getInstance().debug():
        if "getaddrinfo failed" in errors:
            print("[-]Connect Failed! Please Check your proxy config")
            print("[-]" + errors)
        else:
            print("[-]" + errors)
            print('[-]Connect Failed! Please check your Proxy or Network!')
    raise Exception('Connect Failed')


def post(url: str, data: dict = None, files=None, cookies=None, ua: str = None, extra_headers=None, return_type: str = None, encoding: str = None,
         retry: int = 3, timeout: int = G_DEFAULT_TIMEOUT, proxies=None, verify=None):
    """
    是否使用代理应由上层处理
    """
    errors = ""
    headers = {"User-Agent": ua or G_USER_AGENT}
    if extra_headers != None:
        headers.update(extra_headers)
    # 命中 CF 白名单时优先走外部 CF 服务（覆盖 jav321 的搜索 POST）
    cf = _try_cf_bypass(url, 'POST', post_data=data)
    if cf is not None:
        if return_type == "content":
            return (cf.html or "").encode(encoding or "utf-8")
        else:
            return _make_cf_response(cf)
    for i in range(retry):
        try:
            result = requests.post(url, data=data, files=files, headers=headers, timeout=timeout, proxies=proxies,
                                   verify=verify, cookies=cookies)
            if return_type == "object":
                return result
            elif return_type == "content":
                return result.content
            else:
                result.encoding = encoding or result.apparent_encoding
                return result
        except Exception as e:
            if config.getInstance().debug():
                print(f"[-]Connect: {url} retry {i + 1}/{retry}")
            errors = str(e)
    if config.getInstance().debug():
        if "getaddrinfo failed" in errors:
            print("[-]Connect Failed! Please check your proxy config")
            print("[-]" + errors)
        else:
            print("[-]" + errors)
            print('[-]Connect Failed! Please check your Proxy or Network!')
    raise Exception('Connect Failed')


class TimeoutHTTPAdapter(HTTPAdapter):
    def __init__(self, *args, **kwargs):
        self.timeout = G_DEFAULT_TIMEOUT
        if "timeout" in kwargs:
            self.timeout = kwargs["timeout"]
            del kwargs["timeout"]
        super().__init__(*args, **kwargs)

    def send(self, request, **kwargs):
        timeout = kwargs.get("timeout")
        if timeout is None:
            kwargs["timeout"] = self.timeout
        return super().send(request, **kwargs)


class _CFAwareTimeoutAdapter(TimeoutHTTPAdapter):
    """ 在保留原有超时/重试行为的基础上，命中 CF 白名单时改走 CF 服务

    用于 session 型站点（如 javlibrary）：send() 内先判断请求 URL 是否命中白名单，
    命中则由外部 CF 服务返回合成响应，否则调用父类原逻辑。
    """

    def send(self, request, **kwargs):
        cf = _try_cf_bypass(request.url, request.method or 'GET')
        if cf is not None:
            return _make_cf_response(cf, request)
        return super().send(request, **kwargs)


def request_session(cookies=None, ua: str = None, retry: int = 3, timeout: int = G_DEFAULT_TIMEOUT, proxies=None, verify=None):
    """
    keep-alive
    """
    if timeout is None:  # 未指定超时则回退默认，避免 TimeoutHTTPAdapter 收到 None
        timeout = G_DEFAULT_TIMEOUT
    session = create_scraper(browser={'custom': ua or G_USER_AGENT, })
    retries = 0
    if retry > 0:
        retries = Retry(total=retry, connect=retry, backoff_factor=1,
                        status_forcelist=[429, 500, 502, 503, 504])
    # 统一挂载 CF 感知适配器：session 型站点（如 javlibrary）命中白名单时透明改走 CF 服务
    session.mount("https://", _CFAwareTimeoutAdapter(max_retries=retries, timeout=timeout))
    session.mount("http://", _CFAwareTimeoutAdapter(max_retries=retries, timeout=timeout))
    if isinstance(cookies, dict) and len(cookies):
        requests.utils.add_dict_to_cookiejar(session.cookies, cookies)
    if verify:
        session.verify = verify
    if proxies:
        session.proxies = proxies
    session.headers = {"User-Agent": ua or G_USER_AGENT}
    return session


# storyline xcity only
def get_html_by_form(url, form_select: str = None, fields: dict = None, cookies: dict = None, ua: str = None,
                     return_type: str = None, encoding: str = None,
                     retry: int = 3, timeout: int = G_DEFAULT_TIMEOUT, proxies=None, verify=None):
    if timeout is None:
        timeout = G_DEFAULT_TIMEOUT
    # 命中 CF 白名单时优先走外部 CF 服务（主要目标站 xcity 通常不在白名单，默认行为不变）
    cf = _try_cf_bypass(url, 'GET')
    if cf is not None:
        if return_type == "content":
            return (cf.html or "").encode(encoding or "utf-8")
        elif return_type == "object":
            return _make_cf_response(cf)
        elif return_type in ("browser", "scraper"):
            return _make_cf_response(cf), None
        else:
            return cf.html or ""
    session = requests.Session()
    if isinstance(cookies, dict) and len(cookies):
        requests.utils.add_dict_to_cookiejar(session.cookies, cookies)
    if retry > 0:
        retries = Retry(total=retry, connect=retry, backoff_factor=1,
                        status_forcelist=[429, 500, 502, 503, 504])
        session.mount("https://", TimeoutHTTPAdapter(max_retries=retries, timeout=timeout))
        session.mount("http://", TimeoutHTTPAdapter(max_retries=retries, timeout=timeout))
    if verify:
        session.verify = verify
    if proxies:
        session.proxies = proxies
    try:
        browser = mechanicalsoup.StatefulBrowser(user_agent=ua or G_USER_AGENT, session=session)
        result = browser.open(url)
        if not result.ok:
            return None
        form = browser.select_form() if form_select is None else browser.select_form(form_select)
        if isinstance(fields, dict):
            for k, v in fields.items():
                browser[k] = v
        response = browser.submit_selected()

        if return_type == "object":
            return response
        elif return_type == "content":
            return response.content
        elif return_type == "browser":
            return response, browser
        else:
            result.encoding = encoding or "utf-8"
            return response.text
    except requests.exceptions.ProxyError:
        print("[-]get_html_by_form() Proxy error! Please check your Proxy")
    except Exception as e:
        print(f'[-]get_html_by_form() Failed! {e}')
    return None

# storyline javdb only


def get_html_by_scraper(url: str = None, cookies: dict = None, ua: str = None, return_type: str = None,
                        encoding: str = None, retry: int = 3, proxies=None, extra_headers: dict = None, timeout: int = G_DEFAULT_TIMEOUT, verify=None):

    if timeout is None:
        timeout = G_DEFAULT_TIMEOUT
    # 命中 CF 白名单时优先走外部 CF 服务（javdb 等站点）
    cf = _try_cf_bypass(url, 'GET')
    if cf is not None:
        if return_type == "content":
            return (cf.html or "").encode(encoding or "utf-8")
        elif return_type == "object":
            return _make_cf_response(cf)
        elif return_type == "scraper":
            # CF 场景下无 cloudscraper 会话可复用，返回空会话占位避免调用方解包失败
            return _make_cf_response(cf), requests.Session()
        else:
            return cf.html or ""
    session = create_scraper(browser={'custom': ua or G_USER_AGENT, })
    headers = {"User-Agent": ua or G_USER_AGENT}
    if extra_headers != None:
        headers.update(extra_headers)
    session.headers.update(headers)
    if isinstance(cookies, dict) and len(cookies):
        requests.utils.add_dict_to_cookiejar(session.cookies, cookies)
    if retry > 0:
        retries = Retry(total=retry, connect=retry, backoff_factor=1,
                        status_forcelist=[429, 500, 502, 503, 504])
        session.mount("https://", TimeoutHTTPAdapter(max_retries=retries, timeout=timeout))
        session.mount("http://", TimeoutHTTPAdapter(max_retries=retries, timeout=timeout))
    if verify:
        session.verify = verify
    if proxies:
        session.proxies = proxies
    try:
        if isinstance(url, str) and len(url):
            result = session.get(str(url))
        else:  # 空url参数直接返回可重用scraper对象，无需设置return_type
            return session
        # if not result.ok:
        #     return None
        if return_type == "object":
            return result
        elif return_type == "content":
            return result.content
        elif return_type == "scraper":
            return result, session
        else:
            result.encoding = encoding or "utf-8"
            return result.text
    except requests.exceptions.ProxyError:
        print("[-]get_html_by_scraper() Proxy error! Please check your Proxy")
    except Exception as e:
        print(f"[-]get_html_by_scraper() failed {url}. {e}")
    return None
