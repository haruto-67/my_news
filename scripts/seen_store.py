"""既配信記事のURLを記録し、重複配信を防ぐための小さな永続ストア。

data/state/seen.json に {url: first_seen_date(YYYY-MM-DD)} の形で保持し、
RETENTION_DAYS より古いエントリは読み込み時に間引く。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

RETENTION_DAYS = 10

STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "state" / "seen.json"


def load(today: date | None = None) -> dict[str, str]:
    today = today or date.today()
    if not STATE_PATH.exists():
        return {}
    with STATE_PATH.open(encoding="utf-8") as f:
        raw: dict[str, str] = json.load(f)

    cutoff = today - timedelta(days=RETENTION_DAYS)
    pruned = {}
    for url, first_seen in raw.items():
        try:
            seen_date = datetime.strptime(first_seen, "%Y-%m-%d").date()
        except ValueError:
            continue
        if seen_date >= cutoff:
            pruned[url] = first_seen
    return pruned


def save(seen: dict[str, str]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with STATE_PATH.open("w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def mark_seen(urls: list[str], today: date | None = None) -> None:
    today = today or date.today()
    seen = load(today)
    today_str = today.strftime("%Y-%m-%d")
    for url in urls:
        seen.setdefault(url, today_str)
    save(seen)


def is_seen(url: str, seen: dict[str, str]) -> bool:
    return url in seen
