# -*- coding: utf-8 -*-

import re
from html import unescape

from lxml.html import HtmlElement

def html_to_text(raw: str) -> str:
    """把 HTML 片段转成带真实换行的纯文本（用于 FANZA/DMM 简介）。

    FANZA 简介的段落分隔完全依赖 <br>：单 <br> 是句内换行，<br><br> 是段落分隔。
    实测 PIYO-242 的 GraphQL description 共 899 字、含 40 个 <br>、0 个 <p>。
    若用 re.sub(r'<[^>]+>', '') 直接剃标签，<br> 会一起消失，整段简介挤成一行。
    这里按「<br> -> \\n，<br><br> -> \\n\\n」还原，与 FANZA 网页观感一致（连续空行
    最多保留 1 个），开头的空行会被丢弃。
    """
    if not raw:
        return ''
    s = str(raw).replace('\r\n', '\n').replace('\r', '\n')
    # <br> 以及块级标签的结束位置都视为换行
    s = re.sub(r'<\s*br\s*/?\s*>', '\n', s, flags=re.IGNORECASE)
    s = re.sub(r'<\s*/\s*(?:p|div|li|h[1-6]|tr)\s*>', '\n', s, flags=re.IGNORECASE)
    # 其余标签直接去掉，再解 HTML 实体（&nbsp; 解出的是不换行空格 \xa0，
    # 某些前端会因此不折行，统一归一成普通空格）
    s = re.sub(r'<[^>]+>', '', s)
    s = unescape(s).replace('\xa0', ' ')
    out = []
    blank = 0
    for ln in s.split('\n'):
        ln = ln.strip()
        if ln:
            out.append(ln)
            blank = 0
        elif out and not blank:
            # 连续空行最多保留 1 个，即段落之间最多一个空行
            out.append('')
            blank = 1
    return '\n'.join(out).strip()

def getTreeElement(tree: HtmlElement, expr='', index=0):
    """ 根据表达式从`xmltree`中获取匹配值,默认 index 为 0
    :param tree (html.HtmlElement)
    :param expr 
    :param index
    """
    if expr == '':
        return ''
    result = tree.xpath(expr)
    try:
        return result[index]
    except:
        return ''

def getTreeAll(tree: HtmlElement, expr=''):
    """ 根据表达式从`xmltree`中获取全部匹配值
    :param tree (html.HtmlElement)
    :param expr 
    :param index
    """
    if expr == '':
        return []
    result = tree.xpath(expr)
    try:
        return result
    except:
        return []
