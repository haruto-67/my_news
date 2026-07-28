#!/usr/bin/env python3
"""data/*.json（日次ダイジェスト）を走査して data/index.json を再生成する。

GitHub Pagesの静的サイトはディレクトリ一覧を取得できないため、
どの日付のファイルが存在するかをこのindexファイルで明示する。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")


def main() -> None:
    entries = []
    for path in DATA_DIR.glob("*.json"):
        if not DATE_RE.match(path.name):
            continue
        digest = json.loads(path.read_text(encoding="utf-8"))
        entries.append(
            {
                "date": digest["date"],
                "file": path.name,
                "article_counts": {
                    c["name"]: len(c["articles"]) for c in digest["categories"]
                },
            }
        )

    entries.sort(key=lambda e: e["date"], reverse=True)
    index_path = DATA_DIR / "index.json"
    index_path.write_text(json.dumps({"dates": entries}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[build_index] {len(entries)} digest(s) indexed -> {index_path}")


if __name__ == "__main__":
    main()
