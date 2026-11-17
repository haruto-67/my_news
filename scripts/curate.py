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
# 2026-11の作り直しで1カテゴリ5〜8件を目安に増やしたため、既定を2400秒に延ばした
# （check_daily.py は run_daily.sh の60分後に起動するので、その範囲に収める）。
CLAUDE_TIMEOUT_SEC = int(os.environ.get("MY_NEWS_CURATE_TIMEOUT_SEC", "2400"))


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
                                    "my_action": {
                                        "type": ["string", "null"],
                                        "enum": ["要対応", "確認推奨", "影響なし", None],
                                    },
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
読み手は日本の20歳のエンジニアで、Minecraftサーバー(にわとりMC)を運営しています。
「Xのタイムラインで初めて知った」ということが無いように、開発者界隈・Minecraft界隈で
いま話題になっていることを、毎朝ここで先に知れる状態を目指します。

標準入力に、固定RSSソースから収集した候補記事のJSON配列が渡されます
（各要素: title, url, summary, published, source, suggested_category）。

カテゴリ定義:
{category_block}

手順:
1. 候補記事を全部見て、各カテゴリのdescriptionに合うものを選ぶこと。
   suggested_categoryは参考程度で、descriptionとの適合性を優先してよい。
   はてなブックマークの人気エントリなど「話題度」が分かるソースに載っている記事は、
   多くの人が反応している話題として優先すること。
2. explore: true のカテゴリについては、WebSearchで直近24〜48時間の関連記事を追加で探すこと。
   - 公式発表だけでなく「いま話題になっていること」を重視する。日本語(「話題」「炎上」
     「トレンド」等)と英語の両方で検索し、日本語の技術メディア(ITmedia、GIGAZINE、
     Publickey、窓の杜、はてなブックマーク、Zenn/Qiitaのトレンド等)やコミュニティで
     取り上げられている話も拾うこと。
   - 新しいAIモデル・開発ツールの公開、大手サービスの障害や仕様変更、セキュリティ事故、
     大きな買収・提携、マイクラの新情報や界隈の出来事など、SNSで一斉に話題になりやすい
     ものを取りこぼさないこと。
   - 1カテゴリあたり、候補記事と合わせて5〜8件程度になるまで探すこと。
3. 興味に合わない記事は除外してよい（全件羅列しないこと）。
4. 同じ話題・出来事を報じている記事が複数ある場合（同じCVE、同じ製品リリース等）は、
   最も詳しく信頼できる1件だけを採用すること。カテゴリをまたいだ重複も避けること。
5. 以下のURLは過去に配信済みなので、同じURLの記事は絶対に含めないこと:
   {delivered_block}
6. 各記事は次の内容にすること:
   - title: 日本語の見出し。何が起きたかが一目で分かるように。
   - summary: 1〜2文の日本語要約。「【簡単な説明】」のような見出し・ラベルは付けない。
   - body: 数分(800〜1200字前後)で読める独立した日本語のニュース記事。背景・詳細・影響を含め、
     要約の引き伸ばしではなく読み物として成立させること。段落は空行で区切る。
     技術に詳しくなくても分かるよう、最初の段落で「何の話か」を平易に説明すること。
   - url: 元記事のURL。
7. 「自分に関連するIT」カテゴリでは脆弱性情報を最優先し、該当するCVE/JVNがあれば
   深刻度（severity。例: "CVSS 9.8 緊急"）と対象バージョン（affected_versions）を
   フィールドとbodyに明記すること。
   さらに my_action に、読み手のサーバー（Raspberry Pi上のUbuntu、PaperMC、Java、MySQL、
   nginx、Rust製ツール）で対応が必要かを「要対応」「確認推奨」「影響なし」のいずれかで入れ、
   bodyの最後の段落で理由と具体的な対応（アップデートするパッケージ等）を書くこと。
   脆弱性でない記事は severity / affected_versions / my_action を null にしてよい。
8. 各カテゴリの記事は最大{MAX_ARTICLES_PER_CATEGORY}件までとし、重要度・話題度が高い順に並べること。
9. sourceフィールドには、標準入力の候補記事から採用した場合は"feed"、
   WebSearchで新たに見つけた場合は"explore"を設定すること。
10. 該当記事が0件のカテゴリも、articlesを空配列にしてcategoriesに含めること（省略しないこと）。

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

    # 配信した記事に加えて、今回の候補記事も既読にする（採用しなかった候補を翌日また
    # 候補に出さないため）。collect.py では記録しない（記録すると上の配信済みリストに
    # 当日の候補が入り、全部除外されてしまう）。
    try:
        candidate_urls = [a["url"] for a in json.loads(articles_json) if a.get("url")]
    except (json.JSONDecodeError, TypeError):
        candidate_urls = []
    all_urls = [
        a["url"] for c in digest_out["categories"] for a in c["articles"] if a.get("url")
    ] + candidate_urls
    if args.no_mark_seen:
        print(f"[curate] --no-mark-seen: skipped marking {len(all_urls)} urls as seen", file=sys.stderr)
    else:
        seen_store.mark_seen(all_urls)
        print(f"[curate] marked {len(all_urls)} urls as seen", file=sys.stderr)


if __name__ == "__main__":
    main()
