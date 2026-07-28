#!/usr/bin/env python3
"""collect.py が集めた固定ソース記事を claude -p に渡し、
   カテゴリごとの explore（WebSearch）・分類・要約をまとめて行わせ、
   data/YYYY-MM-DD.json を生成する。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

import seen_store

ROOT = Path(__file__).resolve().parent.parent
FEEDS_PATH = ROOT / "config" / "feeds.yml"
DATA_DIR = ROOT / "data"
MAX_ARTICLES_PER_CATEGORY = 12
CLAUDE_TIMEOUT_SEC = 300


def load_categories() -> list[dict]:
    with FEEDS_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)["categories"]


def build_schema(category_names: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "categories": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "enum": category_names},
                        "articles": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string"},
                                    "summary": {"type": "string"},
                                    "url": {"type": "string"},
                                    "source": {
                                        "type": "string",
                                        "enum": ["feed", "explore"],
                                    },
                                    "severity": {"type": ["string", "null"]},
                                    "affected_versions": {"type": ["string", "null"]},
                                },
                                "required": ["title", "summary", "url", "source"],
                            },
                        },
                    },
                    "required": ["name", "articles"],
                },
            }
        },
        "required": ["categories"],
    }


def build_prompt(categories: list[dict], already_delivered: list[str]) -> str:
    category_block = "\n\n".join(
        f"### {c['name']}\n"
        f"description: {c['description'].strip()}\n"
        f"explore: {c.get('explore', False)}"
        for c in categories
    )
    delivered_block = ", ".join(already_delivered) if already_delivered else "(なし)"

    return f"""あなたは個人向けニュースダイジェストのキュレーターです。
標準入力に、固定RSSソースから収集した候補記事のJSON配列が渡されます
（各要素: title, url, summary, published, source, suggested_category）。

カテゴリ定義:
{category_block}

手順:
1. explore: true のカテゴリについては、WebSearchを使ってdescriptionを手がかりに
   直近24〜48時間程度の関連記事を追加で探すこと（1カテゴリあたり数件程度で十分）。
2. 標準入力の候補記事とexploreで見つけた記事の両方について、どのカテゴリに
   分類すべきかをdescriptionに基づいて判断すること（suggested_categoryは参考程度で、
   descriptionとの適合性を優先してよい）。
3. 各カテゴリの興味関心に合わない記事は除外してよい（全件羅列しないこと）。
4. 以下のURLは既に配信済みなので、同じURLの記事は絶対に含めないこと:
   {delivered_block}
5. 各記事は「タイトル」「1〜2行の日本語要約」「元URL」を基本とする。
   「自分に関連するIT」カテゴリでは脆弱性情報を最優先し、該当する
   CVE/JVNがあれば深刻度（severity）と対象バージョン（affected_versions）を
   summaryおよび該当フィールドに明記すること。脆弱性でない記事はseverity/
   affected_versionsをnullにしてよい。
6. 各カテゴリの記事は最大{MAX_ARTICLES_PER_CATEGORY}件までとし、
   重要度・関連度が高いものを優先すること。
7. sourceフィールドには、標準入力の候補記事から採用した場合は"feed"、
   WebSearchで新たに見つけた場合は"explore"を設定すること。
8. 該当記事が0件のカテゴリも、articlesを空配列にしてcategoriesに含めること
   （省略しないこと）。

出力は指定されたJSON Schemaに厳密に従うこと。"""


def call_claude(prompt: str, articles_json: str, schema: dict) -> dict:
    cmd = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "json",
        "--tools",
        "WebSearch",
        "--permission-mode",
        "bypassPermissions",
        "--json-schema",
        json.dumps(schema),
    ]
    proc = subprocess.run(
        cmd,
        input=articles_json,
        capture_output=True,
        text=True,
        timeout=CLAUDE_TIMEOUT_SEC,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude -p failed (exit {proc.returncode}): {proc.stderr[:2000]}")

    envelope = json.loads(proc.stdout)
    if envelope.get("is_error"):
        raise RuntimeError(f"claude -p returned error: {envelope.get('result')}")

    return json.loads(envelope["result"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in", dest="in_path", type=Path, required=True, help="collect.pyの出力JSON")
    parser.add_argument("--date", default=None, help="対象日付 YYYY-MM-DD（省略時は今日）")
    parser.add_argument("--out", type=Path, default=None, help="出力先（省略時は data/<date>.json）")
    args = parser.parse_args()

    target_date = args.date or date.today().strftime("%Y-%m-%d")
    out_path = args.out or (DATA_DIR / f"{target_date}.json")

    articles_json = args.in_path.read_text(encoding="utf-8")
    categories = load_categories()
    category_names = [c["name"] for c in categories]

    seen = seen_store.load()
    already_delivered = sorted(seen.keys())

    prompt = build_prompt(categories, already_delivered)
    schema = build_schema(category_names)

    print(f"[curate] invoking claude -p ({len(already_delivered)} delivered URLs known)...", file=sys.stderr)
    digest = call_claude(prompt, articles_json, schema)

    digest_out = {
        "date": target_date,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "categories": digest["categories"],
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(digest_out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"[curate] wrote {out_path}", file=sys.stderr)

    all_urls = [
        a["url"] for c in digest_out["categories"] for a in c["articles"] if a.get("url")
    ]
    seen_store.mark_seen(all_urls)
    print(f"[curate] marked {len(all_urls)} urls as seen", file=sys.stderr)


if __name__ == "__main__":
    main()
