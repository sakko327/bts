#!/usr/bin/env python3
"""
BTS 2026 World Tour - Ticketmaster 再販モニター (常駐版)

使い方:
    python monitor.py          # config.yaml の check_interval 分ごとに自動チェック
    python monitor.py --once   # 1回だけチェックして終了

通知方法:
    - Slack Webhook (config.yaml で設定)
    - LINE Notify  (config.yaml で設定)
"""

import json
import sys
import time
import logging
import argparse
from datetime import datetime
from pathlib import Path

import requests
import schedule
import yaml

from check_tickets import (
    load_config,
    load_state,
    save_state,
    check_show,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('monitor.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 通知関数
# ─────────────────────────────────────────────

def notify_ntfy(topic: str, shows: list[dict], server: str = "https://ntfy.sh"):
    """
    ntfy.sh でプッシュ通知 — アカウント不要、完全無料
    スマホに ntfy アプリを入れてトピックを購読するだけ
    """
    title = f"BTS チケット再販！ ({len(shows)}公演)"
    body_lines = []
    for s in shows:
        body_lines.append(f"■ {s['name']}\n{s['url']}")
    body = "\n\n".join(body_lines)

    try:
        resp = requests.post(
            f"{server}/{topic}",
            data=body.encode('utf-8'),
            headers={
                "Title": title.encode('utf-8'),
                "Priority": "urgent",
                "Tags": "ticket,tada",
                "Click": shows[0]['url'],
            },
            timeout=10,
        )
        if resp.status_code == 200:
            logger.info("ntfy 通知送信完了")
        else:
            logger.error(f"ntfy エラー: {resp.status_code} {resp.text}")
    except Exception as e:
        logger.error(f"ntfy 通知エラー: {e}")


def notify_telegram(bot_token: str, chat_id: str, shows: list[dict]):
    """
    Telegram Bot でプッシュ通知
    @BotFather でボット作成 → トークン取得 → 自分とチャットして chat_id 取得
    """
    lines = ["🎟️ *BTS チケット再販検出！*", ""]
    for s in shows:
        lines.append(f"*{s['name']}*")
        lines.append(f"[Ticketmaster で確認]({s['url']})")
        lines.append("")
    lines.append(f"_検出時刻: {shows[0]['detected_at']}_")
    message = "\n".join(lines)

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            logger.info("Telegram 通知送信完了")
        else:
            logger.error(f"Telegram エラー: {resp.status_code} {resp.text}")
    except Exception as e:
        logger.error(f"Telegram 通知エラー: {e}")


def notify_pushover(app_token: str, user_key: str, shows: list[dict]):
    """
    Pushover でプッシュ通知 (iOS/Android)
    https://pushover.net — 月$0.99 or 買い切り$5
    """
    body_lines = [f"■ {s['name']}\n{s['url']}" for s in shows]
    try:
        resp = requests.post(
            "https://api.pushover.net/1/messages.json",
            data={
                "token":    app_token,
                "user":     user_key,
                "title":    f"BTS チケット再販！ ({len(shows)}公演)",
                "message":  "\n\n".join(body_lines),
                "priority": 1,
                "url":      shows[0]['url'],
                "url_title": "Ticketmaster で確認",
                "sound":    "cashregister",
            },
            timeout=10,
        )
        if resp.status_code == 200:
            logger.info("Pushover 通知送信完了")
        else:
            logger.error(f"Pushover エラー: {resp.status_code} {resp.text}")
    except Exception as e:
        logger.error(f"Pushover 通知エラー: {e}")


def notify_line(token: str, shows: list[dict]):
    """LINE Notify で通知"""
    lines = ["", "🎟️  BTS チケット再販！"]
    for s in shows:
        lines += [f"■ {s['name']}", f"  {s['url']}"]
    message = "\n".join(lines)

    try:
        resp = requests.post(
            'https://notify-api.line.me/api/notify',
            headers={'Authorization': f'Bearer {token}'},
            data={'message': message},
            timeout=10,
        )
        if resp.status_code == 200:
            logger.info("LINE 通知送信完了")
        else:
            logger.error(f"LINE Notify エラー: {resp.status_code}")
    except Exception as e:
        logger.error(f"LINE 通知エラー: {e}")


def send_all_notifications(config: dict, newly_available: list[dict]):
    if not newly_available:
        return

    notif = config.get('notifications', {})

    ntfy = notif.get('ntfy', {})
    if ntfy.get('enabled') and ntfy.get('topic'):
        notify_ntfy(ntfy['topic'], newly_available, ntfy.get('server', 'https://ntfy.sh'))

    telegram = notif.get('telegram', {})
    if telegram.get('enabled') and telegram.get('bot_token') and telegram.get('chat_id'):
        notify_telegram(telegram['bot_token'], telegram['chat_id'], newly_available)

    pushover = notif.get('pushover', {})
    if pushover.get('enabled') and pushover.get('app_token') and pushover.get('user_key'):
        notify_pushover(pushover['app_token'], pushover['user_key'], newly_available)

    line = notif.get('line_notify', {})
    if line.get('enabled') and line.get('token'):
        notify_line(line['token'], newly_available)


# ─────────────────────────────────────────────
# チェック実行
# ─────────────────────────────────────────────

def run_check():
    config = load_config()
    shows  = config.get('shows', [])
    if not shows:
        logger.warning("config.yaml に公演 URL が設定されていません")
        return

    state = load_state()
    now   = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    newly_available = []

    logger.info(f"=== チェック開始: {now}  ({len(shows)} 公演) ===")

    for show in shows:
        name = show['name']
        logger.info(f"  チェック中: {name}")

        available  = check_show(show, config)
        prev_avail = state.get(name, {}).get('available')

        if available is True and prev_avail is not True:
            logger.info(f"  🎟️  再販検出: {name}")
            newly_available.append({
                'name': name,
                'url': show['url'],
                'detected_at': now,
            })
        elif available is False:
            logger.info(f"  完売中: {name}")
        else:
            logger.warning(f"  状態不明: {name}")

        state[name] = {'available': available, 'last_checked': now}
        time.sleep(2)

    save_state(state)

    if newly_available:
        send_all_notifications(config, newly_available)
    else:
        logger.info("=== 再販なし ===")


# ─────────────────────────────────────────────
# エントリポイント
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='BTS チケット再販モニター')
    parser.add_argument('--once', action='store_true', help='1回だけチェックして終了')
    args = parser.parse_args()

    if args.once:
        run_check()
        return

    config   = load_config()
    interval = config.get('check_interval_minutes', 60)

    logger.info(f"BTSチケット監視開始 — {interval} 分ごとにチェックします")
    run_check()  # 起動直後に1回実行

    schedule.every(interval).minutes.do(run_check)
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == '__main__':
    main()
