#!/usr/bin/env python3
"""run_daily.shの本日分が正常完了したかをlogs/run_daily.logから確認し、
未完了ならDiscordに警告を送る（cronからrun_daily.shの少し後に起動する想定）。

正常完了時は何もしない（通知はしない）。異常時のみ、失敗ログの末尾を
claude -pに渡して原因を日本語で要約させ、Discordに投稿する。claude -p自体が
失敗した場合はログ抜粋をそのままフォールバックとして投稿する。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path

import requests
from dotenv import load_dotenv
import os

ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = ROOT / "logs" / "run_daily.log"
LOG_TAIL_CHARS = 1500
CLAUDE_DIAGNOSIS_TIMEOUT_SEC = 60


def todays_log_section(today: str) -> str:
    if not LOG_FILE.exists():
        return ""
    start_marker = f"=== {today} 開始 ==="
    lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    section: list[str] = []
    capturing = False
    for line in lines:
        if start_marker in line:
            capturing = True
            section = [line]
            continue
        if capturing:
            section.append(line)
    return "\n".join(section)


def completed(section: str, today: str) -> bool:
    return f"=== {today} 完了 ===" in section


def diagnose(section: str) -> str | None:
    tail = section[-LOG_TAIL_CHARS:]
    prompt = f"以下は自動ニュース配信スクリプトの失敗ログです。原因を日本語で2文以内に要約してください。\n\n{tail}"
    try:
        proc = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=CLAUDE_DIAGNOSIS_TIMEOUT_SEC,
        )
    except Exception:
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return proc.stdout.strip()


def post_discord(message: str) -> None:
    load_dotenv(ROOT / ".env")
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook:
        print("[check_daily] DISCORD_WEBHOOK_URL not set, cannot alert", file=sys.stderr)
        return
    resp = requests.post(webhook, json={"content": message}, timeout=15)
    resp.raise_for_status()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Discordに投稿せず内容を標準出力に表示するだけ")
    args = parser.parse_args()

    today = date.today().strftime("%Y-%m-%d")
    section = todays_log_section(today)

    if section and completed(section, today):
        print(f"[check_daily] {today} OK")
        return

    if not section:
        message = (
            f"⚠️ my_news: {today} の実行記録がログに見当たりません。"
            f"cronが起動していない可能性があります（logs/run_daily.logを確認してください）。"
        )
    else:
        summary = diagnose(section)
        if summary:
            message = f"⚠️ my_news: {today} のニュース配信に失敗しました。\n\n{summary}"
        else:
            tail = section[-1000:]
            message = f"⚠️ my_news: {today} のニュース配信に失敗しました。ログ抜粋:\n```\n{tail}\n```"

    print(f"[check_daily] {today} FAILED", file=sys.stderr)
    print(message)
    if not args.dry_run:
        post_discord(message)


if __name__ == "__main__":
    main()
