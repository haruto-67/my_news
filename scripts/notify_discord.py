#!/usr/bin/env python3
"""curate.pyが生成した data/YYYY-MM-DD.json を Discord webhook に通知する。

カテゴリごとに embed を分け、記事ごとに1フィールド（タイトル＋要約＋リンク）を表示する。
feeds.yml の各カテゴリに discord_webhook_env を指定すると、そのカテゴリだけ
別のwebhook（環境変数名）宛に送信できる（未指定ならDISCORD_WEBHOOK_URLを使用）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
FEEDS_PATH = ROOT / "config" / "feeds.yml"

EMBED_COLOR = 0x5865F2
FIELD_VALUE_LIMIT = 1000
FIELD_NAME_LIMIT = 250
TOTAL_CHAR_BUDGET = 5500  # Discordの1メッセージ合計6000文字制限に対する安全マージン

REQUEST_TIMEOUT_SEC = 15
MAX_ATTEMPTS = 4
BACKOFF_BASE_SEC = 3  # 待機は 3s -> 6s -> 12s
MAX_RETRY_AFTER_SEC = 60  # Discordが極端に長いretry_afterを返した場合の上限


def load_webhook_map() -> dict[str, str]:
    load_dotenv(ROOT / ".env")
    with FEEDS_PATH.open(encoding="utf-8") as f:
        categories = yaml.safe_load(f)["categories"]

    default_webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    mapping = {}
    for cat in categories:
        env_name = cat.get("discord_webhook_env")
        url = os.environ.get(env_name) if env_name else None
        mapping[cat["name"]] = url or default_webhook
    return mapping


def build_embed(category_name: str, articles: list[dict]) -> dict:
    if not articles:
        return {
            "title": category_name,
            "color": EMBED_COLOR,
            "fields": [{"name": "該当記事なし", "value": "本日は該当する記事がありませんでした。", "inline": False}],
        }

    fields = []
    for article in articles:
        name = article["title"][:FIELD_NAME_LIMIT]
        value_parts = [article["summary"]]
        extra = []
        if article.get("severity"):
            extra.append(f"深刻度: {article['severity']}")
        if article.get("affected_versions"):
            extra.append(f"対象バージョン: {article['affected_versions']}")
        if extra:
            value_parts.append(" / ".join(extra))
        value_parts.append(f"[記事を読む]({article['url']})")
        value = "\n".join(value_parts)[:FIELD_VALUE_LIMIT]
        fields.append({"name": name, "value": value, "inline": False})

    return {"title": category_name, "color": EMBED_COLOR, "fields": fields}


def fit_to_budget(embeds: list[dict]) -> list[dict]:
    def embed_len(e: dict) -> int:
        return len(e.get("title", "")) + sum(len(f["name"]) + len(f["value"]) for f in e.get("fields", []))

    total = sum(embed_len(e) for e in embeds)
    while total > TOTAL_CHAR_BUDGET:
        # 最も長いembedのフィールドを末尾から間引く
        longest = max(embeds, key=embed_len)
        if len(longest["fields"]) <= 1:
            break
        removed = longest["fields"].pop()
        total -= len(removed["name"]) + len(removed["value"])
        print("[notify_discord] WARN: truncated a field to fit Discord's message size limit", file=sys.stderr)
    return embeds


def group_embeds_by_webhook(digest: dict, webhook_map: dict[str, str]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for category in digest["categories"]:
        webhook_url = webhook_map.get(category["name"])
        if not webhook_url:
            print(f"[notify_discord] WARN: no webhook configured for '{category['name']}', skipping", file=sys.stderr)
            continue
        embed = build_embed(category["name"], category["articles"])
        grouped.setdefault(webhook_url, []).append(embed)
    return grouped


def retry_after_sec(resp: requests.Response) -> float:
    """429レスポンスから待機秒数を取り出す（JSONボディ優先、なければヘッダ）。"""
    try:
        value = float(resp.json().get("retry_after"))
    except Exception:
        try:
            value = float(resp.headers.get("Retry-After", ""))
        except ValueError:
            value = BACKOFF_BASE_SEC
    return min(max(value, 1.0), MAX_RETRY_AFTER_SEC)


def post_with_retry(webhook_url: str, payload: dict) -> None:
    """ネットワーク断・レート制限・Discord側の一時障害をリトライする。

    4xx（429を除く）はペイロード側の問題でリトライしても直らないため即座に送出する。
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        last_attempt = attempt == MAX_ATTEMPTS
        try:
            resp = requests.post(webhook_url, json=payload, timeout=REQUEST_TIMEOUT_SEC)
        except requests.exceptions.RequestException as exc:
            if last_attempt:
                raise
            wait = BACKOFF_BASE_SEC * 2 ** (attempt - 1)
            reason = f"{type(exc).__name__}: {exc}"
        else:
            if resp.status_code == 429:
                if last_attempt:
                    resp.raise_for_status()
                wait = retry_after_sec(resp)
                reason = "429 rate limited"
            elif resp.status_code >= 500:
                if last_attempt:
                    resp.raise_for_status()
                wait = BACKOFF_BASE_SEC * 2 ** (attempt - 1)
                reason = f"HTTP {resp.status_code}"
            else:
                resp.raise_for_status()
                return

        print(
            f"[notify_discord] WARN: attempt {attempt}/{MAX_ATTEMPTS} failed ({reason}), "
            f"retrying in {wait:.0f}s",
            file=sys.stderr,
        )
        time.sleep(wait)


def send(webhook_url: str, embeds: list[dict], date_str: str) -> None:
    embeds = fit_to_budget(embeds)
    # Discordは1メッセージにつきembed最大10件
    for i in range(0, len(embeds), 10):
        chunk = embeds[i : i + 10]
        payload = {"content": f"**{date_str} のニュースダイジェスト**" if i == 0 else None, "embeds": chunk}
        post_with_retry(webhook_url, payload)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("digest_path", type=Path, help="data/YYYY-MM-DD.json のパス")
    args = parser.parse_args()

    digest = json.loads(args.digest_path.read_text(encoding="utf-8"))
    webhook_map = load_webhook_map()
    grouped = group_embeds_by_webhook(digest, webhook_map)

    if not grouped:
        print("[notify_discord] ERROR: no webhook URLs resolved, nothing sent", file=sys.stderr)
        sys.exit(1)

    for webhook_url, embeds in grouped.items():
        send(webhook_url, embeds, digest["date"])
        print(f"[notify_discord] sent {len(embeds)} embed(s) to webhook ending in ...{webhook_url[-6:]}", file=sys.stderr)


if __name__ == "__main__":
    main()
