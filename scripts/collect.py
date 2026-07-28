#!/usr/bin/env python3
"""feeds.yml の固定ソース(RSS/Atom)から直近N時間の新着記事を収集する。

explore（Claude Code の WebSearch によるネット探索）はここでは行わない。
curate.py 側で claude -p 実行時にまとめて行う。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import requests
import yaml

import seen_store

FEEDS_PATH = Path(__file__).resolve().parent.parent / "config" / "feeds.yml"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 my-news-bot/1.0"
)
FETCH_TIMEOUT_SEC = 20
TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text: str) -> str:
    return TAG_RE.sub("", text or "").strip()


def load_categories() -> list[dict]:
    with FEEDS_PATH.open(encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config["categories"]


def entry_published(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        value = getattr(entry, key, None)
        if value:
            return datetime(*value[:6], tzinfo=timezone.utc)
    return None


def fetch_feed(url: str) -> feedparser.FeedParserDict:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=FETCH_TIMEOUT_SEC)
    resp.raise_for_status()
    return feedparser.parse(resp.content)


def collect(hours: int, seen: dict[str, str]) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    articles: list[dict] = []
    seen_in_run: set[str] = set()

    for category in load_categories():
        category_name = category["name"]
        for feed_url in category.get("feeds") or []:
            try:
                parsed = fetch_feed(feed_url)
            except Exception as exc:  # noqa: BLE001 - フィード1件の失敗で全体を止めない
                print(f"[collect] WARN: failed to fetch {feed_url}: {exc}", file=sys.stderr)
                continue

            source_name = strip_html(getattr(parsed.feed, "title", "")) or urlparse(feed_url).netloc

            for entry in parsed.entries:
                link = getattr(entry, "link", None)
                if not link or link in seen or link in seen_in_run:
                    continue

                published = entry_published(entry)
                if published is None or published < cutoff:
                    continue

                articles.append(
                    {
                        "title": strip_html(getattr(entry, "title", "")),
                        "url": link,
                        "summary": strip_html(getattr(entry, "summary", "")),
                        "published": published.isoformat(),
                        "source": source_name,
                        "suggested_category": category_name,
                    }
                )
                seen_in_run.add(link)

    return articles


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=int, default=24, help="収集対象とする直近N時間（既定: 24）")
    parser.add_argument("--out", type=Path, default=None, help="出力先ファイル（省略時は標準出力）")
    parser.add_argument(
        "--no-mark-seen",
        action="store_true",
        help="収集した記事をseenストアに記録しない（動作確認用）",
    )
    args = parser.parse_args()

    seen = seen_store.load()
    articles = collect(args.hours, seen)

    if not args.no_mark_seen and articles:
        seen_store.mark_seen([a["url"] for a in articles])

    output = json.dumps(articles, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(output, encoding="utf-8")
        print(f"[collect] {len(articles)} articles -> {args.out}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
