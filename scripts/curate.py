#!/usr/bin/env python3
"""collect.py が集めた固定ソース記事を claude -p に渡し、
   カテゴリごとの explore（WebSearch）・分類・要約をまとめて行わせ、
   data/YYYY-MM-DD.json を生成する。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

import seen_store

ROOT = Path(__file__).resolve().parent.parent
FEEDS_PATH = ROOT / "config" / "feeds.yml"
DATA_DIR = ROOT / "data"
MAX_ARTICLES_PER_CATEGORY = 12

# collect.pyは収集候補記事も含めてseen_storeに記録するため、フィード数増加に伴い
# 配信済みURL件数が際限なく増える。全件をprompt引数に埋め込むとLinuxの単一
# コマンドライン引数長の上限（MAX_ARG_STRLEN, 128KiB）を超えて
# `OSError: [Errno 7] Argument list too long` でclaude -p自体が起動できなくなる
# （実際に2026-08-26に発生）ため、新しいものを優先して上限件数までに絞る。
MAX_DELIVERED_URLS = 1000

# 3カテゴリ分のWebSearch探索＋要約は元々150〜200秒程度だったが、各記事に
# body（数分で読める本文記事）を生成させるようになり生成トークン量が増えたため
# 実測で最大400秒弱かかるようになった（記事数の多い日はさらに伸びうる）。
# cronはcheck_daily.pyをrun_daily.shの60分後に起動する想定で、そこまでの
# 余裕を見て設定している。
CLAUDE_TIMEOUT_SEC = int(os.environ.get("MY_NEWS_CURATE_TIMEOUT_SEC", "1200"))


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
                                    "body": {"type": "string"},
                                    "url": {"type": "string"},
                                    "source": {
                                        "type": "string",
                                        "enum": ["feed", "explore"],
                                    },
                                    "severity": {"type": ["string", "null"]},
                                    "affected_versions": {"type": ["string", "null"]},
                                },
                                "required": ["title", "summary", "body", "url", "source"],
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
4. 同一カテゴリ内で同じ話題・出来事を報じている記事が複数見つかった場合
   （例: 同じCVE、同じ製品リリースを複数ソースが報じている等）は、最も
   詳しい・信頼できる1件だけを採用し、似たような記事を重複して含めないこと。
5. 以下のURLは既に配信済みなので、同じURLの記事は絶対に含めないこと:
   {delivered_block}
6. 各記事は「タイトル」「1〜2行の日本語要約（summary）」「本文記事（body）」
   「元URL」を基本とする。bodyはsummaryとは別に、その話題について数分程度
   （800〜1200字前後）で読める独立したニュース記事として日本語でまとめること。
   背景・詳細・影響などを含め、元記事や関連情報を踏まえて書くこと（要約の
   引き伸ばしではなく、読み物として成立する記事にすること）。
   「自分に関連するIT」カテゴリでは脆弱性情報を最優先し、該当する
   CVE/JVNがあれば深刻度（severity）と対象バージョン（affected_versions）を
   summary・body双方および該当フィールドに明記すること。脆弱性でない記事は
   severity/affected_versionsをnullにしてよい。
7. 各カテゴリの記事は最大{MAX_ARTICLES_PER_CATEGORY}件までとし、
   重要度・関連度が高いものを優先すること。
8. sourceフィールドには、標準入力の候補記事から採用した場合は"feed"、
   WebSearchで新たに見つけた場合は"explore"を設定すること。
9. 該当記事が0件のカテゴリも、articlesを空配列にしてcategoriesに含めること
   （省略しないこと）。

出力は指定されたJSON Schemaに厳密に従うこと。"""


def call_claude(prompt: str, articles_json: str, schema: dict) -> dict:
    # promptは配信済みURLリストを含み日々長さが変わるため、コマンドライン引数では
    # なくstdin経由で渡す（argv経由だとLinuxの単一引数長上限 MAX_ARG_STRLEN=128KiB
    # を超えたときに OSError: [Errno 7] Argument list too long でclaude -p自体が
    # 起動できなくなる。実際に2026-08-26に発生した）。
    full_input = f"{prompt}\n\n---\n以下が標準入力の候補記事一覧（JSON配列）です:\n{articles_json}"
    cmd = [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--tools",
        "WebSearch",
        "--permission-mode",
        "bypassPermissions",
        "--json-schema",
        json.dumps(schema),
    ]
    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            input=full_input,
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"claude -p が {CLAUDE_TIMEOUT_SEC} 秒以内に完了しませんでした。"
            f"MY_NEWS_CURATE_TIMEOUT_SEC で制限時間を延ばすか、"
            f"MAX_ARTICLES_PER_CATEGORY（現在{MAX_ARTICLES_PER_CATEGORY}）や"
            f"explore対象カテゴリを減らしてください。"
        ) from None

    print(f"[curate] claude -p finished in {time.monotonic() - started:.0f}s", file=sys.stderr)
    if proc.returncode != 0:
        # exit != 0 でも --output-format json はJSON封筒(is_error/result)を
        # stdoutに吐くことがある（例: OAuthセッション期限切れ）。stderrが空だと
        # 原因不明のまま握りつぶされていたため、stdoutからも理由を拾う。
        detail = proc.stderr.strip()
        if not detail and proc.stdout.strip():
            try:
                envelope = json.loads(proc.stdout)
                detail = str(envelope.get("result") or envelope)
            except json.JSONDecodeError:
                detail = proc.stdout[:2000]
        raise RuntimeError(f"claude -p failed (exit {proc.returncode}): {detail[:2000]}")

    envelope = json.loads(proc.stdout)
    if envelope.get("is_error"):
        raise RuntimeError(f"claude -p returned error: {envelope.get('result')}")

    return json.loads(envelope["result"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in", dest="in_path", type=Path, required=True, help="collect.pyの出力JSON")
    parser.add_argument("--date", default=None, help="対象日付 YYYY-MM-DD（省略時は今日）")
    parser.add_argument("--out", type=Path, default=None, help="出力先（省略時は data/<date>.json）")
    parser.add_argument(
        "--no-mark-seen", action="store_true", help="テスト用: seenストアを更新しない"
    )
    args = parser.parse_args()

    target_date = args.date or date.today().strftime("%Y-%m-%d")
    out_path = args.out or (DATA_DIR / f"{target_date}.json")

    articles_json = args.in_path.read_text(encoding="utf-8")
    categories = load_categories()
    category_names = [c["name"] for c in categories]

    seen = seen_store.load()
    newest_first = sorted(seen.items(), key=lambda kv: kv[1], reverse=True)
    already_delivered = sorted(url for url, _ in newest_first[:MAX_DELIVERED_URLS])

    prompt = build_prompt(categories, already_delivered)
    schema = build_schema(category_names)

    print(f"[curate] invoking claude -p ({len(already_delivered)} delivered URLs known)...", file=sys.stderr)
    digest = call_claude(prompt, articles_json, schema)

    for category in digest["categories"]:
        for article in category["articles"]:
            article["id"] = hashlib.sha256(article["url"].encode("utf-8")).hexdigest()[:12]

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
    if args.no_mark_seen:
        print(f"[curate] --no-mark-seen: skipped marking {len(all_urls)} urls as seen", file=sys.stderr)
    else:
        seen_store.mark_seen(all_urls)
        print(f"[curate] marked {len(all_urls)} urls as seen", file=sys.stderr)


if __name__ == "__main__":
    main()
