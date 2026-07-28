#!/usr/bin/env bash
# 毎朝cronから起動される一連の処理:
#   collect -> curate(claude -p) -> data/*.json -> git push -> Discord通知
#
# 失敗時はその場で終了する（set -e）。リトライは行わない
# （未確定事項: cron実行失敗時のリトライ/通知は将来の改善課題）。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

VENV_PYTHON="$ROOT_DIR/.venv/bin/python"
DATE="$(date +%F)"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

ARTICLES_JSON="$WORK_DIR/articles.json"
DIGEST_JSON="$ROOT_DIR/data/$DATE.json"

echo "[run_daily] === $DATE 開始 ==="

echo "[run_daily] collect..."
"$VENV_PYTHON" scripts/collect.py --hours 24 --out "$ARTICLES_JSON"

echo "[run_daily] curate (claude -p)..."
"$VENV_PYTHON" scripts/curate.py --in "$ARTICLES_JSON" --date "$DATE" --out "$DIGEST_JSON"

echo "[run_daily] update data/index.json..."
"$VENV_PYTHON" scripts/build_index.py

echo "[run_daily] git commit & push..."
git add "data/$DATE.json" data/index.json data/state/seen.json
if git diff --cached --quiet; then
  echo "[run_daily] no changes to commit"
else
  git commit -m "Add digest for $DATE"
  git push origin main
fi

echo "[run_daily] Discord通知..."
"$VENV_PYTHON" scripts/notify_discord.py "$DIGEST_JSON"

echo "[run_daily] === $DATE 完了 ==="
