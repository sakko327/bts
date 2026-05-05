#!/usr/bin/env python3
"""
BTS 2026 World Tour - Ticketmaster 再販チェッカー (1回実行版)

使い方:
    python check_tickets.py            # チェックして結果をJSON出力
    python check_tickets.py --notify   # MCP通知用フォーマットで出力
"""

import json
import sys
import time
import logging
from datetime import datetime
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('monitor.log', encoding='utf-8'),
        logging.StreamHandler(sys.stderr),
    ]
)
logger = logging.getLogger(__name__)

CONFIG_FILE = Path(__file__).parent / 'config.yaml'
STATE_FILE  = Path(__file__).parent / 'state.json'

# ブラウザに偽装するヘッダー
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': (
        'text/html,application/xhtml+xml,application/xml;'
        'q=0.9,image/avif,image/webp,*/*;q=0.8'
    ),
    'Accept-Language': 'en-US,en;q=0.9,ja;q=0.8',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Cache-Control': 'no-cache',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
}


def load_config():
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def check_via_api(event_id: str, api_key: str):
    """Ticketmaster Discovery API でチケット状況を確認"""
    url = f"https://app.ticketmaster.com/discovery/v2/events/{event_id}"
    try:
        resp = requests.get(url, params={'apikey': api_key}, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            code = data.get('dates', {}).get('status', {}).get('code', '').lower()
            if code in ('onsale', 'rescheduled'):
                return True
            if code in ('offsale', 'cancelled', 'postponed'):
                return False
        elif resp.status_code == 404:
            logger.warning(f"API: イベント {event_id} が見つかりません")
    except Exception as e:
        logger.error(f"API エラー ({event_id}): {e}")
    return None


def check_via_scraping(url: str):
    """HTML スクレイピングでチケット状況を確認"""
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        resp = session.get(url, timeout=20, allow_redirects=True)
        if resp.status_code != 200:
            logger.warning(f"HTTP {resp.status_code}: {url}")
            return None

        soup = BeautifulSoup(resp.text, 'html.parser')

        # 1. JSON-LD 構造化データで確認
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                data = json.loads(script.string or '')
                offers = data.get('offers', {})
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                avail = str(offers.get('availability', ''))
                if 'InStock' in avail:
                    return True
                if 'SoldOut' in avail or 'Discontinued' in avail:
                    return False
            except Exception:
                pass

        # 2. Next.js 埋め込みデータで確認
        next_data_tag = soup.find('script', id='__NEXT_DATA__')
        if next_data_tag:
            try:
                data = json.loads(next_data_tag.string or '')
                event = (
                    data.get('props', {})
                        .get('pageProps', {})
                        .get('event', {})
                )
                code = (
                    event.get('dates', {})
                         .get('status', {})
                         .get('code', '')
                         .lower()
                )
                if code in ('onsale', 'rescheduled'):
                    return True
                if code in ('offsale', 'cancelled', 'postponed'):
                    return False
            except Exception:
                pass

        # 3. テキストパターンで判定 (Ticketmaster / SeatGeek 共通)
        text = soup.get_text().lower()
        available_signals = [
            # Ticketmaster
            'add to cart', 'buy tickets', 'find tickets',
            'resale tickets', 'fan-to-fan', 'ticket resale',
            'get tickets', 'select your tickets',
            # SeatGeek
            'tickets from $', 'from $', 'buy now',
            'deal score', 'listing',
        ]
        sold_out_signals = [
            'sold out', 'this event is sold out',
            'no tickets available', 'not on sale',
            'tickets are not currently available',
            # SeatGeek
            'no tickets found', '0 tickets',
        ]
        for sig in available_signals:
            if sig in text:
                return True
        for sig in sold_out_signals:
            if sig in text:
                return False

        return None

    except Exception as e:
        logger.error(f"スクレイピングエラー ({url}): {e}")
        return None


def check_show(show: dict, config: dict):
    """公演1件のチケット状況をチェック"""
    event_id = show.get('event_id')
    available = None

    api_cfg = config.get('ticketmaster_api', {})
    if api_cfg.get('enabled') and event_id:
        available = check_via_api(event_id, api_cfg['api_key'])

    if available is None:
        available = check_via_scraping(show['url'])

    return available


def main():
    config = load_config()
    shows  = config.get('shows', [])

    if not shows:
        logger.warning("config.yaml に公演 URL が設定されていません")
        print(json.dumps({'newly_available': [], 'error': 'no shows configured'}))
        sys.exit(0)

    state = load_state()
    now   = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    newly_available = []

    for show in shows:
        name = show['name']
        logger.info(f"チェック中: {name}")

        available    = check_show(show, config)
        prev_state   = state.get(name, {})
        prev_avail   = prev_state.get('available')

        if available is True and prev_avail is not True:
            logger.info(f"🎟️  チケット再販検出: {name}")
            newly_available.append({
                'name': name,
                'url': show['url'],
                'detected_at': now,
            })
        elif available is False:
            logger.info(f"完売中: {name}")
        else:
            logger.warning(f"状態不明: {name}")

        state[name] = {
            'available': available,
            'last_checked': now,
        }
        time.sleep(2)  # レートリミット

    save_state(state)

    result = {
        'checked_at': now,
        'total_shows': len(shows),
        'newly_available': newly_available,
        'all_status': {k: v for k, v in state.items()},
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return bool(newly_available)


if __name__ == '__main__':
    found = main()
    sys.exit(0 if found else 1)
