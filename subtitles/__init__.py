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
        # 依次尝试每个候选字幕页面，直到有任意一个语言版本成功下载。
        # open_download 会尽可能把 zh-CN 与 zh-TW 两个版本都下载下来。
        for subtitle_link in subtitle_links:
            if open_download(subtitle_link,path,number,leak_word,c_word,hack_word):
                return True
        
        return False
        
    except Exception as e:
        print(f"错误: {e}")
        return False
    
def open_download(subtitle_link,path,number,leak_word,c_word,hack_word):
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
        return False
    tree = html.fromstring(subtitle_response.content)

    # zh-CN 和 zh-TW 两个语言版本都收集，若都存在则一起下载。
    languages = (
        ('zh-CN', '//div[@class="sub-single"]/span/a[contains(@href, "zh-CN.srt")]/@href'),
        ('zh-TW', '//div[@class="sub-single"]/span/a[contains(@href, "zh-TW.srt")]/@href'),
    )
    download_targets = []
    for lang, xpath in languages:
        links = tree.xpath(xpath)
        if links:
            download_targets.append((lang, links[0]))
        else:
            print(f"未找到{lang}字幕下载链接")

    if not download_targets:
        return False

    success = False
    for lang, download_link in download_targets:
        if _download_one(subtitle_page_url, download_link, path, number, leak_word, c_word, hack_word,
                         f"{lang}.srt"):
            success = True
    return success


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
    # 检测 404 页面：站点返回的 404 页标题形如 "404找不到"（无空格），
    # 也有 "404 Not Found / 404 未找到" 等变体，不能只比对单一中文字符串，
    # 否则会漏判并把整段 html 当成字幕写入 .srt 文件（即用户看到的“内容显示404”）。
    head = content[:2048].lower()
    is_404 = (
        b"404 not found" in head
        or b"<title>404" in head
        or "404找不到".encode("utf-8") in content
        or "404 未找到".encode("utf-8") in content
        or "404找不到".encode("gbk", errors="ignore") in content
    )
    if is_404:
        print(f"字幕文件下载失败({ext}): 服务器返回 404 页面")
        return False

    sub_targetpath = Path(path) / f"{number}{leak_word}{c_word}{hack_word}.{ext}"
    print(f"保存字幕至: {sub_targetpath}")
    with open(sub_targetpath, "wb") as file:
        file.write(content)
    return True
