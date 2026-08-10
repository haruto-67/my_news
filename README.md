# my_news — 自分専用ニュース配信サービス

毎朝、興味に沿った情報を自動収集し、Claude Code がキュレーション・要約・分類したうえで
Discord（速報）と GitHub Pages（ダッシュボード）に配信する。詳細な要件は
[`IT脆弱性ニュース要件.md`](./IT脆弱性ニュース要件.md) を参照。

**ニュースページ**: https://haruto-67.github.io/my_news/

## 構成

```
config/feeds.yml     カテゴリ定義・購読フィード・explore設定（編集ポイント）
scripts/collect.py    固定RSSソースから直近N時間の新着を収集
scripts/curate.py     claude -p にexplore(WebSearch)・分類・要約をまとめて実行させる
scripts/notify_discord.py  Discord webhookへembed形式で通知
scripts/build_index.py    data/*.json から data/index.json（日付一覧）を再生成
scripts/run_daily.sh   上記を一連で実行するcron用スクリプト
scripts/seen_store.py  重複配信防止用の既配信URLストア（data/state/seen.json）
site/                  GitHub Pages用の静的ダッシュボード（vanilla HTML/CSS/JS）
  article.html/js/article.js  記事詳細ページ（Claudeが生成した本文＋元記事へのリンク）
data/                  生成された日次ダイジェスト（data/YYYY-MM-DD.json）
.github/workflows/pages.yml   push契機でPagesをビルド&デプロイ
deploy/crontab.example cron登録例
```

## セットアップ（本番: Raspberry Pi / Ubuntu）

実際に `/rpi/my_news` へこの手順でデプロイ済み（2026-07-29）。

1. `git clone https://github.com/<user>/my_news.git` し、venvを作成する。
   - `python3 -m venv .venv` が `python3.x-venv` apt packageの不足（`ensurepip is not available`）
     で失敗する場合、sudo権限がなくても以下で代替できる:
     ```bash
     python3 -m venv .venv --without-pip
     curl -sS https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
     .venv/bin/python3 /tmp/get-pip.py
     .venv/bin/pip install -r requirements.txt
     ```
2. `.env.example` を `.env` にコピーし、`DISCORD_WEBHOOK_URL` を設定（専用の1人サーバーの
   チャンネルwebhook）。`SITE_BASE_URL`（GitHub PagesのURL）も設定するとDiscordの
   リンクが記事詳細ページ（`site/article.html`）向けになる（未設定時は元記事URLに直接
   リンクする）。`.env` はgit管理外。
3. `claude` CLI がそのユーザーで認証済みであること（`claude -p "hello"` が動くか確認）。
   サブスク認証はヘッドレスCIでは通せないため、この認証済みPi上でcronを回す設計になっている。
   `claude` が `~/.local/bin` 等PATHの通っていない場所にある場合、cronのPATHにも追加すること
   （下記cron例を参照）。
4. GitHubへのpush用に、Pi上でこのリポジトリ専用のSSHキーを発行する
   （`ssh-keygen -t ed25519 -f ~/.ssh/my_news_deploy -N ""`）。公開鍵をGitHubの
   リポジトリ Settings → Deploy keys に write権限付きで登録し（`gh repo deploy-key add`
   でも可）、リポジトリのgit設定だけをその鍵に向ける:
   ```bash
   git remote set-url origin git@github.com:<user>/my_news.git
   git config core.sshCommand 'ssh -i ~/.ssh/my_news_deploy -o IdentitiesOnly=yes'
   ```
   （`~/.ssh/config` を書き換えず、このリポジトリだけ専用鍵を使う設定）
5. `deploy/crontab.example` を参考に `crontab -e` で毎朝5:00起動を登録する
   （**既存のcrontab行は上書きせず追記すること**）。`claude`のPATHが通っていない環境変数下で
   cronは動くため、crontab行に `PATH=...` を明示するか `run_daily.sh` 側でPATHを補う。
6. GitHubリポジトリの Settings → Pages で「GitHub Actions」をソースに設定する
   （`.github/workflows/pages.yml` が自動でビルド&デプロイする）。**GitHub Pagesはprivate
   リポジトリではFreeプランで使えない**（"Your current plan does not support GitHub Pages
   for this repository" というエラーになる）。private運用したい場合はGitHub Proへの
   アップグレードが必要。

## 手動実行

```bash
.venv/bin/python scripts/collect.py --hours 24 --out /tmp/articles.json
.venv/bin/python scripts/curate.py --in /tmp/articles.json --out data/$(date +%F).json
.venv/bin/python scripts/build_index.py
.venv/bin/python scripts/notify_discord.py data/$(date +%F).json
```

または `scripts/run_daily.sh` を直接実行（collect→curate→git push→Discord通知まで一括）。

## feeds.yml の編集ガイド

- 「こういう記事もほしい」        → 該当カテゴリの `description` に一文追加
- 「この固定ソースも確実に見たい」 → `feeds` にURLを一行追加
- 「カテゴリを増やしたい」        → ブロックをコピーして `name` を変更
- カテゴリごとにDiscordチャンネルを分けたい場合は、`.env` に
  `DISCORD_WEBHOOK_URL_XXX` を追加し、そのカテゴリに `discord_webhook_env: DISCORD_WEBHOOK_URL_XXX`
  を指定する（未指定なら共通の `DISCORD_WEBHOOK_URL` を使用）。

## 既知の制約・今後の課題

- `claude -p` の呼び出しはネットワーク・API利用状況に依存するため、失敗時のリトライは
  行っていない（`run_daily.sh` は `set -e` で即終了する）。代わりに `scripts/check_daily.py`
  を毎朝6:00（`run_daily.sh`の60分後。`curate.py`の`claude -p`タイムアウトが25分あるため
  余裕を持たせている）にcron実行し、当日分が完了していなければ`claude -p`でログを要約して
  Discordに警告するようにしている（`deploy/crontab.example`参照）。
- 各カテゴリの記事上限は `scripts/curate.py` の `MAX_ARTICLES_PER_CATEGORY`（既定12件）。
- 該当0件のカテゴリはdata上は空配列として保持し、Discord/Pages側で「該当記事なし」と表示する。
- 開発はmacOS、本番はRaspberry Pi(Ubuntu)想定。cron/systemd・パス・`claude`認証の保存場所
  などOS差異があるため、Pi実機での動作確認を必ず行うこと。
- `https://www.minecraft.net/en-us/rss` は開発機（Mac）・本番Pi（家庭用回線）の両方から
  接続タイムアウトを確認済み（Akamaiのボット対策等が原因と推測、恒常的な様子）。
  「趣味」カテゴリは `explore: true` のためWebSearchでMinecraft関連記事が補完されており、
  実際の生成結果でも探索経由でminecraft.net記事が取得できている。将来的にはこの固定URLを
  見直すか削除しても実害は小さい。
