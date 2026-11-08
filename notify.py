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
import json
import os
import time

import requests

import config

_API_URL = 'https://api.telegram.org/bot{token}/sendMessage'

# 状态文件名（与 config.ini 同目录），用于跨轮次累计
_STATE_FILE = 'notify_state.json'

# 本次运行的统计（由 Movie_Data_Capture 在结束时汇总）
_stats = {'success': 0, 'failed': 0, 'unknown': 0}
# 跨轮次累计状态：{started_at, updated_at, success, failed, unknown, rounds}
_batch = {}


def reset():
    """只清空本轮计数，不影响跨轮次累计。"""
    for key in _stats:
        _stats[key] = 0


def _state_path():
    """状态文件放在 config.ini 同目录，便于随项目一起迁移。"""
    try:
        ini_path = getattr(config.getInstance(), 'ini_path', '') or ''
        base = os.path.dirname(os.path.abspath(ini_path)) if ini_path else os.getcwd()
    except Exception:
        base = os.getcwd()
    return os.path.join(base, _STATE_FILE)


def _load_state():
    try:
        with open(_state_path(), 'r', encoding='utf-8') as fp:
            data = json.load(fp)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state():
    try:
        _batch['updated_at'] = time.time()
        with open(_state_path(), 'w', encoding='utf-8') as fp:
            json.dump(_batch, fp, ensure_ascii=False)
    except Exception:
        pass


def begin_run():
    """开始一次运行。

    清空本轮计数；若上一次运行距现在超过 [telegram] cumulative_reset_hours，
    则视为新任务、累计从零开始，否则延续上一批继续累加轮次与总数。
    """
    reset()
    state = _load_state()
    now = time.time()
    try:
        reset_hours = config.getInstance().telegram_cumulative_reset_hours()
    except Exception:
        reset_hours = 12
    last = float(state.get('updated_at') or 0)
    if not state or (reset_hours > 0 and now - last > reset_hours * 3600):
        state = {'started_at': now, 'updated_at': now,
                 'success': 0, 'failed': 0, 'unknown': 0, 'rounds': 0}
    state['rounds'] = int(state.get('rounds') or 0) + 1
    _batch.clear()
    _batch.update(state)


def _add(key, delta=1):
    _stats[key] += delta
    if _batch:
        _batch[key] = int(_batch.get(key) or 0) + delta


def record(success: bool):
    """记录一部影片的处理结果。"""
    _add('success' if success else 'failed')


def record_unknown():
    """记录一个无法识别番号的文件。"""
    _add('unknown')


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
    """整轮结束后的汇总通知（mode=summary/both 时生效）。

    开启 [telegram] cumulative 时，消息同时给出「本轮」与「累计」两行，
    累计跨轮次保留（超过 cumulative_reset_hours 未再运行则视为新任务）。
    """
    conf = _conf()
    round_total = sum(int(_stats[k]) for k in ('success', 'failed', 'unknown'))
    try:
        cumulative = bool(_batch) and config.getInstance().telegram_cumulative()
    except Exception:
        cumulative = False

    if not conf['enabled'] or conf['mode'] not in ('summary', 'both'):
        # 即使本轮不发送，也把累计落盘，避免中途关闭开关丢进度
        if cumulative:
            _save_state()
        return False

    lines = ['📊 <b>刮削完成</b>']
    if cumulative:
        rounds = int(_batch.get('rounds') or 1)
        batch_total = sum(int(_batch.get(k) or 0) for k in ('success', 'failed', 'unknown'))
        lines = [f'📊 <b>刮削完成</b>（第 {rounds} 轮）',
                 f"本轮：成功 {_stats['success']} / 失败 {_stats['failed']} / 未识别 {_stats['unknown']} / 合计 {round_total}",
                 f"累计：成功 {_batch.get('success', 0)} / 失败 {_batch.get('failed', 0)} / "
                 f"未识别 {_batch.get('unknown', 0)} / 合计 {batch_total}"]
        started = _batch.get('started_at')
        if started:
            lines.append(f"开始：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(float(started)))}")
    else:
        lines += [f"成功：{_stats['success']}",
                  f"失败：{_stats['failed']}",
                  f"未识别：{_stats['unknown']}",
                  f"合计：{round_total}"]

    if duration:
        lines.append(f"耗时：{html.escape(duration)}")
    lines.append(time.strftime('%Y-%m-%d %H:%M:%S'))
    ok = _send('\n'.join(lines))
    if cumulative:
        _save_state()
    return ok
