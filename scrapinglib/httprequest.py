# -*- coding: utf-8 -*-

import mechanicalsoup
import requests
from requests.adapters import HTTPAdapter
from requests.cookies import RequestsCookieJar
from requests.models import Response
from urllib3.util.retry import Retry
from cloudscraper import create_scraper

import config

G_USER_AGENT = r'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'
G_DEFAULT_TIMEOUT = 10


def _debug(msg: str):
    """ debug 模式下打印网络层诊断信息（非 debug 保持安静）"""
    try:
        if config.getInstance().debug():
            print(msg)
    except Exception:
        pass


def _cf_module():
    """ 懒加载 cf_bypass 模块：避免与包初始化形成循环导入，未启用时也零额外开销"""
    from . import cf_bypass
    return cf_bypass


def _solver_cookies_for(url: str) -> dict:
    """ 取 solver 为该站点解出的 cookie（无则空 dict）"""
    try:
        return _cf_module().solver_cookies_for(url)
    except Exception:
        return {}


def _with_solver_cookies(url: str, cookies):
    """ 给直连请求的 cookies 补上 solver 解出的 cookie（调用方显式传入的优先）

    :param url: 目标 URL
    :param cookies: 调用方传入的 cookies（dict / None / CookieJar）
    :return: 合并后的 cookies；无 solver cookie 或类型不认识时原样返回
    """
    extra = _solver_cookies_for(url)
    if not extra:
        return cookies
    if cookies is None:
        return dict(extra)
    if isinstance(cookies, dict):
        merged = dict(extra)
        merged.update(cookies)
        return merged
    # CookieJar 等其它类型保持原样，避免破坏调用方语义
    return cookies


def _merge_cookie_header(header: str, extra: dict) -> str:
    """ 把 solver cookie 并入已有的 Cookie 头字符串（同名以已有值为准）"""
    parts = {}
    for chunk in str(header or "").split(";"):
        if "=" in chunk:
            name, value = chunk.split("=", 1)
            parts[name.strip()] = value.strip()
    for name, value in (extra or {}).items():
        parts.setdefault(str(name), str(value))
    return "; ".join(f"{k}={v}" for k, v in parts.items())


def _absorb_challenge(url: str, response) -> bool:
    """ 检测直连响应是否 CF 挑战页；命中则记账（该站点后续改走 solver）

    :param url: 请求 URL
    :param response: requests / mechanicalsoup 的响应对象
    :return: 命中挑战返回 True
    """
    try:
        cf = _cf_module()
        if not cf.is_cf_challenge_response(response):
            return False
        cf.mark_challenge_hit(url)
        _debug(f"[CF] 命中 Cloudflare 挑战: {url}")
        return True
    except Exception:
        return False


def _cf_value(cf, return_type: str = None, encoding: str = None, request=None):
    """ 把 CFResult 按调用方的 return_type 转换成返回值（content/object/文本三态）"""
    if return_type == "content":
        return (cf.html or "").encode(encoding or "utf-8")
    if return_type == "object":
        return _make_cf_response(cf, request)
    return cf.html or ""


def _try_cf_bypass(url: str, method: str = 'GET', post_data=None, force: bool = False):
    """ 按 solver-first 规则决定是否调用外部 CF 服务，并返回结果

    :param url: 目标 URL
    :param method: 'GET' / 'POST'
    :param post_data: POST 表单数据（dict），GET 时为 None
    :param force: True 时忽略「cookie 复用窗口」，强制走一次 solver
    :return: 命中并成功返回 cf_bypass.CFResult；否则返回 None（表示走原直连逻辑）

    调度规则见 cf_bypass.should_use_solver：白名单站点、或命中过 CF 挑战的站点
    （solver_first=1 时）跳过直连直接交给 solver；solver 刚解过且 cookie 还在
    复用窗口内时先直连带 cookie。solver 成功后开复用窗口并记下 cookie。
    """
    try:
        conf = config.getInstance().cloudflare()
        if not conf.enable:
            return None
        cf = _cf_module()
        if not force and not cf.should_use_solver(url, conf):
            return None
        if force:
            _debug(f"[CF] 挑战后强制走 Solver: {method} {url}")
        else:
            _debug(f"[CF] 站点 {cf.host_of(url)} 走 Solver（solver-first，跳过直连）: {url}")
        result = cf.bypass_url(url, conf=conf, method=method, post_data=post_data)
        # solver 成功：记 cookie + 开复用窗口，窗口内该站点先直连（见 should_use_solver）
        cf.mark_solver_success(url, result.cookies, ttl=conf.solver_reuse_ttl)
        _debug(f"[+]CF bypass: {method} {url}"
               f"（接下来 {int(conf.solver_reuse_ttl)}s 内先直连复用 cookie）")
        return result
    except Exception as e:
        # 任何异常都回退到原直连逻辑；非 debug 模式保持安静
        _debug(f"[-]CF bypass failed（回退直连）: {url} {e}")
        return None


def _retry_by_solver(url: str, method: str = 'GET', post_data=None,
                     return_type: str = None, encoding: str = None, request=None):
    """ 直连被 CF 拦下后的补救：强制走一次 solver 并返回已转换的结果

    :return: 转换后的响应（str/bytes/Response）；solver 不可用时返回 None
    """
    cf = _try_cf_bypass(url, method, post_data=post_data, force=True)
    if cf is None:
        return None
    return _cf_value(cf, return_type, encoding, request)



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
    # 命中 CF 白名单 / 命中过挑战的站点优先走外部 CF 服务（未启用/未命中返回 None，原逻辑保持不变）
    cf = _try_cf_bypass(url, 'GET')
    if cf is not None:
        if return_type == "content":
            return (cf.html or "").encode(encoding or "utf-8")
        elif return_type == "object":
            return _make_cf_response(cf)
        else:
            return cf.html or ""
    # 直连前补上 solver 解出的 cookie（该站点在复用窗口内时有值）
    cookies = _with_solver_cookies(url, cookies)
    for i in range(retry):
        try:
            allowRedirects = False
            if 'www.dmm.co.jp' in url:
                allowRedirects = True
            result = requests.get(url, headers=headers, timeout=timeout, proxies=proxies,
                                  verify=verify, cookies=cookies, allow_redirects=allowRedirects)
            if _absorb_challenge(url, result):
                # 直连被拦：同一次调用内改走 solver，失败则保持旧行为（把原响应交回调用方）
                retried = _retry_by_solver(url, 'GET', return_type=return_type,
                                           encoding=encoding, request=getattr(result, "request", None))
                if retried is not None:
                    return retried
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
    # 命中 CF 白名单 / 命中过挑战的站点优先走外部 CF 服务（覆盖 jav321 的搜索 POST）
    cf = _try_cf_bypass(url, 'POST', post_data=data)
    if cf is not None:
        if return_type == "content":
            return (cf.html or "").encode(encoding or "utf-8")
        else:
            return _make_cf_response(cf)
    # 直连前补上 solver 解出的 cookie（该站点在复用窗口内时有值）
    cookies = _with_solver_cookies(url, cookies)
    for i in range(retry):
        try:
            result = requests.post(url, data=data, files=files, headers=headers, timeout=timeout, proxies=proxies,
                                   verify=verify, cookies=cookies)
            if _absorb_challenge(url, result):
                retried = _retry_by_solver(url, 'POST', post_data=data, return_type=return_type,
                                           encoding=encoding, request=getattr(result, "request", None))
                if retried is not None:
                    return retried
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
    命中则由外部 CF 服务返回合成响应，否则补上 solver cookie 后调用父类原逻辑。
    直连被 CF 拦下时记一笔，并在同一次调用内改走 solver。
    """

    def send(self, request, **kwargs):
        cf = _try_cf_bypass(request.url, request.method or 'GET')
        if cf is not None:
            return _make_cf_response(cf, request)
        # solver 复用窗口内：直连请求补上解出的 cookie（同名以 session 已有值为准）
        extra = _solver_cookies_for(request.url)
        if extra:
            request.headers["Cookie"] = _merge_cookie_header(request.headers.get("Cookie", ""), extra)
        response = super().send(request, **kwargs)
        if _absorb_challenge(request.url, response):
            retried = _try_cf_bypass(request.url, request.method or 'GET', force=True)
            if retried is not None:
                try:
                    response.close()
                except Exception:
                    pass
                return _make_cf_response(retried, request)
        return response


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
    # 直连前补上 solver 解出的 cookie（该站点在复用窗口内时有值）
    cookies = _with_solver_cookies(url, cookies)
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
        if _absorb_challenge(url, result):
            # 直连被拦：改走 solver；solver 不可用时保持旧行为（下面 result.ok 为 False 则返回 None）
            retried = _try_cf_bypass(url, 'GET', force=True)
            if retried is not None:
                if return_type in ("browser", "scraper"):
                    return _make_cf_response(retried), None
                return _cf_value(retried, return_type, encoding)
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
    # 直连前补上 solver 解出的 cookie（该站点在复用窗口内时有值）
    cookies = _with_solver_cookies(url, cookies)
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
        if _absorb_challenge(url, result):
            # 直连被拦：改走 solver；失败则把原响应交回调用方（保持旧行为）
            retried = _try_cf_bypass(url, 'GET', force=True)
            if retried is not None:
                if return_type == "scraper":
                    return _make_cf_response(retried), requests.Session()
                return _cf_value(retried, return_type, encoding)
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
