# BTS 2026 World Tour - Ticketmaster 再販モニター

Ticketmaster の BTS 公演ページを1時間ごとに自動チェックし、
再販チケットが出たらスマホに即時通知します。

## セットアップ

```bash
pip install -r requirements.txt
```

## 設定

### 1. 公演 URL を追加

`config.yaml` の `shows` セクションに各公演の URL を追加します。

```yaml
shows:
  - name: "BTS World Tour 2026 - Los Angeles Day 1"
    url: "https://www.ticketmaster.com/event/XXXXXXXXXX"
    event_id: "XXXXXXXXXX"   # URL末尾の英数字
```

### 2. スマホ通知を設定（どれか1つでOK）

#### ★ ntfy.sh（最も簡単・無料・アカウント不要）

1. iOS / Android に **ntfy** アプリをインストール
2. アプリで任意のトピック名（例: `bts-tickets-abc123`）を購読
3. `config.yaml` を編集:

```yaml
notifications:
  ntfy:
    enabled: true
    topic: "bts-tickets-abc123"   # アプリで購読したのと同じ名前
```

#### Telegram Bot（無料）

1. Telegram で `@BotFather` に `/newbot` → `bot_token` を取得
2. 作ったボットに何かメッセージを送る
3. `https://api.telegram.org/bot<TOKEN>/getUpdates` で `chat_id` を確認

```yaml
notifications:
  telegram:
    enabled: true
    bot_token: "1234567890:ABCDefgh..."
    chat_id: "123456789"
```

#### LINE Notify（無料）

トークン取得: https://notify-bot.line.me/my/

```yaml
notifications:
  line_notify:
    enabled: true
    token: "YOUR_TOKEN"
```

## 実行

```bash
# 常駐監視（1時間ごと）
python monitor.py

# 1回だけテスト実行
python monitor.py --once

# JSON 出力のみ（Claude Code ループ用）
python check_tickets.py
```

## Ticketmaster Discovery API（オプション）

HTML スクレイピングより確実に状況を取得できます。
無料 API キー: https://developer.ticketmaster.com/products-and-docs/apis/getting-started/

```yaml
ticketmaster_api:
  enabled: true
  api_key: "YOUR_API_KEY"
```

## ログ

`monitor.log` に全チェック結果が記録されます。  
`state.json` に各公演の最終チェック状態が保存されます（重複通知防止用）。
