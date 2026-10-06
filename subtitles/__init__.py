import requests
import re
from lxml import html
from pathlib import Path
import config

headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        }


def download_subtitles(filepath, path, multi_part, number, part, leak_word, c_word, hack_word) -> bool:
    number = re.sub(r'-CD\d+$', '', number)
    try:
        print(f"开始搜索{number}字幕...")
        search_url = f"https://subtitlecat.com/index.php?search={number}"
        config_proxy = config.getInstance().proxy()
        if config_proxy.enable:
            proxies = config_proxy.proxies()
            response = requests.get(search_url, headers=headers, proxies=proxies)
        else:
            response = requests.get(search_url, headers=headers)
        print(f"搜索URL: {search_url}, 状态码: {response.status_code}")
        if response.status_code != 200:
            return False
        
        tree = html.fromstring(response.content)
        subtitle_links = tree.xpath('//table[@class="table sub-table"]//tr/td[contains(translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "' + number.lower() + '")]/a/@href')
        if not subtitle_links:
            print("未找到字幕链接")
            return False
        # 遍历所有候选字幕页面，尽量把简繁两个版本都下全：
        # 某些候选页只有繁中、另一些只有简中，因此不能遇到第一个能下的页就停止，
        # 而应记录已下载的语言，继续在后续候选页中补齐尚未拿到的语言。
        done_langs = set()
        for subtitle_link in subtitle_links:
            done_langs |= open_download(subtitle_link, path, number, leak_word, c_word, hack_word, done_langs)
            # 简繁都已经拿到，无需再继续尝试其它候选页
            if {'zh-CN', 'zh-TW'} <= done_langs:
                break

        return len(done_langs) > 0
        
    except Exception as e:
        print(f"错误: {e}")
        return False
    
def open_download(subtitle_link,path,number,leak_word,c_word,hack_word, done_langs=None):
    """访问单个候选字幕页，下载其中尚未获取到的 zh-CN / zh-TW 版本。

    返回本次成功下载的语言集合（set），供调用方累计去重。
    """
    if done_langs is None:
        done_langs = set()
    print(f"找到字幕链接: {subtitle_link}")
    subtitle_page_url = f"https://subtitlecat.com/{subtitle_link}"
    config_proxy = config.getInstance().proxy()
    if config_proxy.enable:
        proxies = config_proxy.proxies()

        subtitle_response = requests.get(subtitle_page_url, headers=headers, proxies=proxies,verify=False)
    else:
        subtitle_response = requests.get(subtitle_page_url, headers=headers,verify=False)

    print(f"访问字幕页面: {subtitle_page_url}, 状态码: {subtitle_response.status_code}")
    if subtitle_response.status_code != 200:
        return set()
    tree = html.fromstring(subtitle_response.content)

    # zh-CN 和 zh-TW 两个语言版本都收集，若都存在则一起下载（已下过的语言跳过）。
    languages = (
        ('zh-CN', '//div[@class="sub-single"]/span/a[contains(@href, "zh-CN.srt")]/@href'),
        ('zh-TW', '//div[@class="sub-single"]/span/a[contains(@href, "zh-TW.srt")]/@href'),
    )
    download_targets = []
    for lang, xpath in languages:
        if lang in done_langs:
            continue
        links = tree.xpath(xpath)
        if links:
            download_targets.append((lang, links[0]))
        else:
            print(f"未找到{lang}字幕下载链接")

    if not download_targets:
        return set()

    success_langs = set()
    for lang, download_link in download_targets:
        if _download_one(subtitle_page_url, download_link, path, number, leak_word, c_word, hack_word,
                         f"{lang}.srt"):
            success_langs.add(lang)
    return success_langs


def _is_valid_subtitle(content: bytes) -> bool:
    """校验下载到的内容是否为真正的字幕，而不是 404 错误页 / HTML / nginx 报错页。

    判定规则：
    1) 不能包含 HTML 页面特征（<html>、<head>、<body>、404 Not Found、nginx 等）；
    2) 必须包含 SRT/VTT 的时间轴标记 "-->".
    """
    # 统一按 utf-8 解码，忽略无法解码的字节，兼容 gbk 等编码的报错页
    try:
        text = content.decode("utf-8", errors="ignore")
    except Exception:
        text = ""
    head = text[:2048].lower()
    body = text.lower()

    # 1) HTML / 报错页特征：只看开头，避免字幕正文偶尔出现的单词被误判。
    #    不使用裸 "404"，因为正文台词里可能恰好含 "404"，只认明确的 404 报错文案。
    if any(m in head for m in ("<html", "<head", "<body", "<title", "nginx",
                               "404 not found", "404找不到", "404 未找到")):
        return False
    # 正文中兜底检测明显的 404 提示
    if any(m in body for m in ("404 not found", "404找不到", "404 未找到")):
        return False

    # 2) 必须含有字幕时间轴标记
    if "-->" not in text:
        return False

    return True


def _download_one(subtitle_page_url, download_link, path, number, leak_word, c_word, hack_word, ext) -> bool:
    config_proxy = config.getInstance().proxy()
    subtitle_download_url = f"https://subtitlecat.com/{download_link}"
    # 带上来源页，部分字幕站会做防盗链校验，缺失 Referer 时直接返回 404 内容。
    dl_headers = dict(headers)
    dl_headers["Referer"] = subtitle_page_url
    try:
        if config_proxy.enable:
            proxies = config_proxy.proxies()
            subtitle_response = requests.get(subtitle_download_url, headers=dl_headers, proxies=proxies,
                                             verify=False)
        else:
            # 修复：原先此处误用了 subtitle_page_url，导致请求到的是详情页而非下载链接，
            # 加上站点防盗链时就会返回 404 页面内容。
            subtitle_response = requests.get(subtitle_download_url, headers=dl_headers, verify=False)
    except Exception as e:
        print(f"下载字幕出错: {subtitle_download_url}, 错误: {e}")
        return False

    print(f"下载字幕: {subtitle_download_url}, 状态码: {subtitle_response.status_code}")
    if subtitle_response.status_code != 200:
        return False

    content = subtitle_response.content
    # 下载完毕后检查：确认拿到的是真正的字幕，而不是 404 / HTML / nginx 报错页。
    if not _is_valid_subtitle(content):
        print(f"字幕文件下载失败({ext}): 内容不是有效字幕（可能是 404 或 HTML 错误页）")
        return False

    sub_targetpath = Path(path) / f"{number}{leak_word}{c_word}{hack_word}.{ext}"
    print(f"保存字幕至: {sub_targetpath}")
    with open(sub_targetpath, "wb") as file:
        file.write(content)
    return True
