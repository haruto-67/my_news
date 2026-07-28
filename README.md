# my_news — 自分専用ニュース配信サービス

毎朝、興味に沿った情報を自動収集し、Claude Code がキュレーション・要約・分類したうえで
Discord（速報）と GitHub Pages（ダッシュボード）に配信する。詳細な要件は
[`IT脆弱性ニュース要件.md`](./IT脆弱性ニュース要件.md) を参照。

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
data/                  生成された日次ダイジェスト（data/YYYY-MM-DD.json）
.github/workflows/pages.yml   push契機でPagesをビルド&デプロイ
deploy/crontab.example cron登録例
```

## セットアップ（本番: Raspberry Pi / Ubuntu）

1. リポジトリをclone し、`python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
2. `.env.example` を `.env` にコピーし、`DISCORD_WEBHOOK_URL` を設定（専用の1人サーバーの
   チャンネルwebhook）。`.env` はgit管理外。
3. `claude` CLI がそのユーザーで認証済みであること（`claude -p "hello"` が動くか確認）。
   サブスク認証はヘッドレスCIでは通せないため、この認証済みPi上でcronを回す設計になっている。
4. GitHubへのpush用に、Pi上でこのリポジトリ専用のSSHキーを発行し、GitHubアカウントの
   Deploy Key（またはSSH key）として登録する。`~/.ssh/config` に `Host github` のような
   エイリアスを作り、`git remote set-url origin git@github:<user>/my_news.git` としておくと
   鍵の使い分けがしやすい。
5. `deploy/crontab.example` を参考に `crontab -e` で毎朝5:00起動を登録する。
6. GitHubリポジトリの Settings → Pages で「GitHub Actions」をソースに設定する
   （`.github/workflows/pages.yml` が自動でビルド&デプロイする）。

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
  行っていない（`run_daily.sh` は `set -e` で即終了する）。
- 各カテゴリの記事上限は `scripts/curate.py` の `MAX_ARTICLES_PER_CATEGORY`（既定12件）。
- 該当0件のカテゴリはdata上は空配列として保持し、Discord/Pages側で「該当記事なし」と表示する。
- 開発はmacOS、本番はRaspberry Pi(Ubuntu)想定。cron/systemd・パス・`claude`認証の保存場所
  などOS差異があるため、Pi実機での動作確認を必ず行うこと。
- `https://www.minecraft.net/en-us/rss` はAkamaiのボット対策と思われる挙動で、開発機（Mac/
  クラウド由来IP）からは接続がタイムアウトすることを確認済み。家庭用回線のPiからは問題なく
  取得できる可能性があるため、本番での疎通を確認すること。取得できない場合も「趣味」カテゴリは
  `explore: true` のためWebSearchで補完される。
