# -*- coding: utf-8 -*-
"""刮削完成后的通知（Telegram）。

配置见 config.ini 的 [telegram] 段：
  switch      0=关闭（默认） 1=开启
  bot_token   BotFather 给的 token
  chat_id     会话/群组 id
  mode        movie=每部一条 | summary=全部结束后一条汇总（默认） | both=两者都发
  notify_failed  失败时是否也推送（mode 含 movie 时有效）
  proxy       可选，形如 socks5h://127.0.0.1:1080；留空则跟随 [proxy] 配置

设计约束：通知属于旁路功能，任何异常都必须被吞掉，绝不能影响刮削主流程。
"""

import html
import time

import requests

import config

_API_URL = 'https://api.telegram.org/bot{token}/sendMessage'

# 本次运行的统计（由 Movie_Data_Capture 在结束时汇总）
_stats = {'success': 0, 'failed': 0, 'unknown': 0}


def reset():
    """开始新一次运行前清空统计。"""
    for key in _stats:
        _stats[key] = 0


def record(success: bool):
    """记录一部影片的处理结果。"""
    _stats['success' if success else 'failed'] += 1


def record_unknown():
    """记录一个无法识别番号的文件。"""
    _stats['unknown'] += 1


def _conf():
    """读取通知配置；配置缺失时返回全默认值。"""
    c = config.getInstance()
    return {
        'enabled': c.telegram_switch(),
        'token': c.telegram_bot_token(),
        'chat_id': c.telegram_chat_id(),
        'mode': c.telegram_mode(),
        'notify_failed': c.telegram_notify_failed(),
        'proxy': c.telegram_proxy(),
    }


def _proxies(conf):
    """通知用代理：优先 [telegram] proxy，其次跟随 [proxy]。"""
    if conf['proxy']:
        return {'http': conf['proxy'], 'https': conf['proxy']}
    try:
        ini_proxy = config.getInstance().proxy()
        if ini_proxy.switch:
            return ini_proxy.proxies() or None
    except Exception:
        pass
    return None


def _send(text: str) -> bool:
    """发送一条 HTML 消息；未启用/未配置/失败都返回 False，不抛异常。"""
    conf = _conf()
    if not conf['enabled'] or not conf['token'] or not conf['chat_id']:
        return False
    payload = {
        'chat_id': conf['chat_id'],
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True,
    }
    try:
        resp = requests.post(_API_URL.format(token=conf['token']), json=payload,
                             proxies=_proxies(conf), timeout=15)
        if not resp.ok and config.getInstance().debug():
            print(f"[-] Telegram 通知失败: HTTP {resp.status_code} {resp.text[:120]}")
        return resp.ok
    except Exception as e:
        if config.getInstance().debug():
            print(f"[-] Telegram 通知异常: {type(e).__name__}: {e}")
        return False


def notify_movie(number, filename, success=True, reason=''):
    """单部影片通知（mode=movie/both 时生效）。"""
    conf = _conf()
    if not conf['enabled'] or conf['mode'] not in ('movie', 'both'):
        return False
    if not success and not conf['notify_failed']:
        return False

    icon = '✅' if success else '❌'
    lines = [f"{icon} <b>{html.escape(str(number) or '未识别')}</b>",
             f"文件：<code>{html.escape(str(filename))}</code>"]
    if not success and reason:
        lines.append(f"原因：{html.escape(str(reason)[:200])}")
    lines.append(time.strftime('%Y-%m-%d %H:%M:%S'))
    return _send('\n'.join(lines))


def notify_summary(duration=''):
    """整批结束后的汇总通知（mode=summary/both 时生效）。"""
    conf = _conf()
    if not conf['enabled'] or conf['mode'] not in ('summary', 'both'):
        return False

    total = _stats['success'] + _stats['failed'] + _stats['unknown']
    lines = ['📊 <b>刮削完成</b>',
             f"成功：{_stats['success']}",
             f"失败：{_stats['failed']}",
             f"未识别：{_stats['unknown']}",
             f"合计：{total}"]
    if duration:
        lines.append(f"耗时：{html.escape(duration)}")
    lines.append(time.strftime('%Y-%m-%d %H:%M:%S'))
    return _send('\n'.join(lines))
