# -*- coding: utf-8 -*-
"""JavDB App API 通道（参考 MDCx 的 mdcx/crawlers/javdb_app.py 移植）。

通过 JavDB 移动端 API 获取结构化 JSON 数据，并使用 App 专用图片 CDN
（tp.spfcas.com）返回的无水印原图。该 CDN 返回单字节 XOR 加密流，解密后即为原始 JPEG。

签名算法：jdsignature = "{ts}.{suffix}.{md5(ts+prefix)}"，
常量由 MDCx 对 JavDB 移动端 APK 逆向得到（与 javdb-cli 项目一致）。
"""

import hashlib
import json
import re
import time
import uuid
from urllib.parse import urlencode, urlsplit

import config
from .httprequest import get as http_get
from .parser import Parser

# —— 签名常量（App 更新轮换时需同步更新）——
_SIG_PREFIX = "71cf27bb3c0bcdf207b64abecddc970098c7421ee7203b9cdae54478478a199e7d5a6e1a57691123c1a931c057842fb73ba3b3c83bcd69c17ccf174081e3d8aa"
_SIG_SUFFIX = "lpw6vgqzsp"

# App API 主机与回退主机
_API_BASE = "https://apidd.czssdgz.com"
_API_FALLBACKS = ("https://apidd.spthgb.com", "https://jdforrepam.com")

# App 图片 CDN（返回加密流，需 XOR 解密为无水印原图）
_IMAGE_HOST_LEGACY = "https://tp.cmastd.com/"
_IMAGE_HOST_APP = "https://tp.spfcas.com/"
_SPFCAS_HOST = "tp.spfcas.com"

# App 客户端伪装参数（保持与 MDCx 一致，避免服务端风控识别）
_PLATFORM = "android"
_APP_CHANNEL = "official"
_APP_VERSION = "official"
_APP_VERSION_NUMBER = "1.9.35"
_SYSTEM_VERSION = "13"
_DEVICE_MODEL = "Pixel 6"
_DEVICE_NAME = "Pixel"
_SYSTEM_VERSION_FALLBACK = "13"
# 固定 device_uuid：模拟真实设备的稳定标识，跨运行保持一致
_DEVICE_UUID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "mdcx-javdb-app"))

# 同一实例两次 API 请求的最小间隔（秒），规避服务端风控
_MIN_REQUEST_INTERVAL = 3.0

# 签名/参数校验失败特征（HTTP 状态或响应体关键字）
_SIGNATURE_ERROR_MARKERS = ("parameterinvalid", "invalidsignature", "unauthorized")


def make_signature() -> str:
    """ 生成 JavDB App 接口签名

    :return: 形如 "{ts}.{suffix}.{md5(ts+prefix)}" 的签名字符串

    签名公式来自对 JavDB 移动端 APK 的逆向结果；服务端据此校验请求合法性。
    """
    ts = int(time.time())
    md5_hash = hashlib.md5(f"{ts}{_SIG_PREFIX}".encode()).hexdigest()
    return f"{ts}.{_SIG_SUFFIX}.{md5_hash}"


def decrypt_spfcas_image(data: bytes):
    """ 解密 App CDN 加密图片流

    :param data: CDN 返回的原始字节
    :return: 解密后的图片字节；格式不符（含空输入）返回 None

    加密格式：首字节为 XOR 密钥，其后每个字节与密钥异或；明文应为 JPEG（FF D8 起始）。
    使用 256 字节查表 + bytes.translate 实现整段异或，避免逐字节 Python 循环。
    """
    if not data or len(data) < 2:
        return None
    key = data[0]
    table = bytes(i ^ key for i in range(256))
    plain = data[1:].translate(table)
    return plain if plain.startswith(b"\xff\xd8") else None


def is_spfcas_image_url(url: str) -> bool:
    """ 判断 URL 是否属于需要解密的 App CDN

    :param url: 图片 URL
    :return: 属于 App CDN 返回 True
    """
    if not url:
        return False
    try:
        return urlsplit(str(url)).netloc.lower() == _SPFCAS_HOST
    except Exception:
        return False


class JavdbApp(Parser):
    """JavDB App API 通道刮削实现

    与 web/api 通道共享项目统一的字段契约（dictformat 的输出结构），
    因此可被 javdb.py 门面无差别调用。
    """

    source = 'javdb'

    def extraInit(self):
        """ 自定义初始化：请求限速时间戳与图片解密开关
        """
        self._last_request_at = 0.0
        self._decrypt_image = True

    # ------------------------------------------------------------------
    # 配置与工具
    # ------------------------------------------------------------------
    def _channel_conf(self):
        """ 读取 [javdb_channel] 配置

        :return: JavdbChannelConf 实例；失败返回 None
        """
        try:
            return config.getInstance().javdb_channel()
        except Exception:
            return None

    @staticmethod
    def _clean(value) -> str:
        """ 清理字符串：None 安全并去除首尾空白
        """
        return "" if value is None else str(value).strip()

    @staticmethod
    def _clean_int(value) -> str:
        """ 转正整数为字符串

        :param value: 原始时长（秒或分钟）
        :return: 正整数文本；None/非法/<=0 返回空串
        """
        try:
            iv = int(value)
        except (TypeError, ValueError):
            return ""
        return str(iv) if iv > 0 else ""

    @staticmethod
    def _ensure_https(url: str) -> str:
        """ 补全协议相对 URL（// 开头）
        """
        if url and not url.startswith("http"):
            return "https:" + url
        return url or ""

    @staticmethod
    def _normalize_image_url(url: str) -> str:
        """ 把旧 App CDN 域名归一到当前 App CDN（路径保持不变）
        """
        url = JavdbApp._ensure_https(url)
        if url.startswith(_IMAGE_HOST_LEGACY):
            url = _IMAGE_HOST_APP + url[len(_IMAGE_HOST_LEGACY):]
        return url

    @staticmethod
    def _is_female(actor: dict) -> bool:
        """ 判断演员性别是否为女；缺省视为女（与 MDCx 行为一致）
        """
        gender = actor.get("gender")
        if gender is None:
            return True
        if isinstance(gender, str):
            return gender.lower() in ("female", "女", "女优", "女優")
        if isinstance(gender, bool):
            return gender
        if isinstance(gender, int):
            return gender == 1
        return True

    @staticmethod
    def _female_actors(actors) -> list:
        """ 过滤出女性演员姓名（与项目默认 actor_gender=female 对齐）
        """
        result = []
        for a in actors or []:
            if not isinstance(a, dict):
                continue
            name = JavdbApp._clean(a.get("name"))
            if name and JavdbApp._is_female(a):
                result.append(name)
        return result

    @staticmethod
    def _number_key(value: str) -> str:
        """ 番号归一化：大写、去分隔符，并把 FC2PPV 归一为 FC2
        """
        key = re.sub(r"[\s\-_]+", "", str(value or "").upper())
        return key.replace("FC2PPV", "FC2")

    def _loose_match(self, candidate: str, number: str) -> bool:
        """ 宽松匹配：候选番号归一化后与目标相等或以目标为前缀
        """
        a = self._number_key(candidate)
        b = self._number_key(number)
        return bool(a) and bool(b) and (a == b or a.startswith(b))

    @staticmethod
    def _search_candidates(number: str) -> list:
        """ 生成搜索候选番号（原样 + 紧凑形式 + FC2 变体），去重保序
        """
        raw = str(number or "").strip()
        variants = [raw] if raw else []
        compact = re.sub(r"[\s\-_]+", "", raw).upper()
        if compact and compact not in variants:
            variants.append(compact)
        if compact.startswith("FC2"):
            digits = re.sub(r"\D", "", compact.replace("FC2PPV", ""))
            if digits:
                for v in ("FC2-" + digits, digits):
                    if v not in variants:
                        variants.append(v)
        return [v for v in variants if v]

    # ------------------------------------------------------------------
    # API 调用
    # ------------------------------------------------------------------
    @staticmethod
    def _api_params() -> dict:
        """ 构造 App 接口公共查询参数（设备伪装）
        """
        return {
            "platform": _PLATFORM,
            "app_channel": _APP_CHANNEL,
            "app_version": _APP_VERSION,
            "app_version_number": _APP_VERSION_NUMBER,
            "system_version": _SYSTEM_VERSION or _SYSTEM_VERSION_FALLBACK,
            "device_model": _DEVICE_MODEL,
            "device_name": _DEVICE_NAME,
            "device_uuid": _DEVICE_UUID,
        }

    def _request_api(self, path: str, params: dict = None):
        """ 调用 App API 并返回解析后的 JSON

        :param path: 接口路径（如 /api/v2/search）
        :param params: 附加查询参数
        :return: JSON dict；全部主机失败返回 None

        多主机回退：主域名失败时依次尝试备用域名，任意一个成功即返回。
        """
        # 限速：同一实例两次请求间隔不足时补 sleep，规避服务端风控
        now = time.monotonic()
        if self._last_request_at and now - self._last_request_at < _MIN_REQUEST_INTERVAL:
            time.sleep(_MIN_REQUEST_INTERVAL - (now - self._last_request_at))
        self._last_request_at = time.monotonic()

        query = self._api_params()
        if params:
            query.update(params)
        headers = {
            "jdsignature": make_signature(),
            "accept-language": "zh",
            "User-Agent": "Dart/3.5 (dart:io)",
        }
        for host in (_API_BASE,) + _API_FALLBACKS:
            url = f"{host}{path}?{urlencode(query)}"
            try:
                resp = http_get(url, extra_headers=headers, cookies=self.cookies,
                                proxies=self.proxies, verify=self.verify, return_type="object")
            except Exception as e:
                if config.getInstance().debug():
                    print(f"[-]javdb_app api failed: {url} {e}")
                continue
            if resp is None or resp.status_code != 200:
                continue
            try:
                return resp.json()
            except Exception:
                continue
        return None

    # ------------------------------------------------------------------
    # 查询与字段归一化
    # ------------------------------------------------------------------
    def queryNumberUrl(self, number):
        """ 通过 App API 搜索番号并返回影片 ID

        :param number: 番号
        :return: 影片 ID 字符串；未找到返回空串

        为什么返回 ID：App 通道用 ID 直接请求详情接口，不依赖网页地址。
        """
        for candidate in self._search_candidates(number):
            resp = self._request_api("/api/v2/search",
                                     {"q": candidate, "page": "1", "type": "movie", "limit": "50"})
            if not resp:
                continue
            movies = (resp.get("data") or {}).get("movies") or []
            # 第一轮：番号精确匹配
            for m in movies:
                if isinstance(m, dict) and self._number_key(m.get("number", "")) == self._number_key(number):
                    return str(m.get("id") or "")
            # 第二轮：前缀/包含匹配（兼容 BF-002 与 ABF-002 之类的歧义场景）
            for m in movies:
                if isinstance(m, dict) and self._loose_match(m.get("number", ""), number):
                    return str(m.get("id") or "")
        return ""

    def search(self, number: str):
        """ 查询番号并返回统一字段字典（JSON 字符串）

        :param number: 番号
        :return: 与 Parser.dictformat 输出一致的 JSON 字符串；未找到返回 404
        """
        self.number = number
        conf = self._channel_conf()
        if conf is not None:
            self._decrypt_image = bool(conf.decrypt_app_image)
        movie_id = self.queryNumberUrl(number)
        if not movie_id:
            return 404
        detail = self._request_api(f"/api/v4/movies/{movie_id}")
        movie = ((detail or {}).get("data") or {}).get("movie") or {}
        if not movie:
            return 404
        self.detailurl = f"https://javdb573.com/v/{movie_id}"
        return self._build_result(movie)

    def _build_result(self, movie: dict) -> str:
        """ 把 App 详情 JSON 归一为本项目统一的字段字典

        :param movie: App 详情接口返回的 movie 对象
        :return: 与 Parser.dictformat 结构一致的 JSON 字符串
        """
        number = self._clean(movie.get("number")) or self.number
        title = self._clean(movie.get("title"))
        origin = self._clean(movie.get("origin_title")) or title
        release = self._clean(movie.get("release_date"))
        extra = [self._normalize_image_url(self._clean(i.get("thumb_url") or i.get("large_url")))
                 for i in (movie.get("preview_images") or []) if isinstance(i, dict)]
        tags = [self._clean(t.get("name")) for t in (movie.get("tags") or [])
                if isinstance(t, dict) and self._clean(t.get("name"))]
        dic = {
            'number': number,
            'title': title,
            'studio': self._clean(movie.get("maker_name")),
            'release': release,
            'year': release[:4],
            'outline': self._clean(movie.get("summary")),
            'runtime': self._clean_int(movie.get("duration")),
            'director': self._clean(movie.get("director_name")),
            'actor': self._female_actors(movie.get("actors")),
            'actor_photo': {},
            'cover': self._normalize_image_url(self._clean(movie.get("cover_url"))),
            'cover_small': '',
            'extrafanart': [x for x in extra if x],
            'trailer': self._ensure_https(self._clean(movie.get("preview_video_url"))),
            'tag': tags,
            'label': self._clean(movie.get("publisher_name")),
            'series': self._clean(movie.get("series_name")),
            'userrating': self._clean(movie.get("score")),
            'uservotes': '',
            'uncensored': self._number_key(number).startswith("FC2"),
            'website': self.detailurl,
            'source': self.source,
            'imagecut': self.imagecut,
        }
        return json.dumps(dic, ensure_ascii=False, sort_keys=True, separators=(',', ':'))

    def fetch_image(self, url: str):
        """ 下载图片；属于 App CDN 且启用解密时返回无水印原图字节

        :param url: 图片 URL（可能为加密流地址）
        :return: 可直接写盘的图片字节；失败返回 None
        """
        try:
            content = http_get(url, cookies=self.cookies, proxies=self.proxies,
                               verify=self.verify, return_type="content")
        except Exception:
            return None
        if not content:
            return None
        if self._decrypt_image and is_spfcas_image_url(url):
            plain = decrypt_spfcas_image(content)
            if plain is not None:
                return plain
            # 解密失败（格式不符）时回退原始字节，避免整张图丢失
            if config.getInstance().debug():
                print(f"[-]javdb_app decrypt failed, fallback raw: {url}")
        return content
