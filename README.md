# twitter-media-dl

Twitter/X の指定ユーザーのメディア（画像・動画）を一括ダウンロードするPythonスクリプト。

## 特徴

- ユーザーの全ツイートからメディアを漏れなく取得（件数上限なし）
- 画像はオリジナル画質で保存
- 動画（mp4）にも対応
- リツイートのメディアは除外（オプションで含めることも可）
- 既にダウンロード済みのファイルはスキップするため、途中から再開可能
- ファイル名に投稿者・日時・ツイート本文を含む形式で保存

## ファイル名形式

```
[@author][YYYYMMDDhhmm] tweetContent{index}.ext
```

例:
```
[@elonmusk][202603092353] Real picture of @StarbaseTX.jpg
[@elonmusk][202603092353] Multiple images tweet_1.jpg
[@elonmusk][202603092353] Multiple images tweet_2.jpg
[@elonmusk][202603091823] .mp4   ← テキストなしの場合は空文字
```

## 保存先

```
./downloads/@<username>/
```

例: `@elonmusk` を指定した場合 → `./downloads/@elonmusk/`

## 必要なもの

- Python 3.10 以上
- [twitter-cli](https://github.com/jackwener/twitter-cli)

```bash
pip install twitter-cli
```

## セットアップ

### 1. スクリプトの配置

`twitter-media-dl.py` と `config.yaml` を同じフォルダに置く。

### 2. 認証情報の設定

x.com にログイン済みのブラウザで開発者ツールを開き、以下の Cookie の値をコピーする。

- `auth_token`
- `ct0`

`config.yaml` に記入する:

```yaml
auth:
  auth_token: "ここにauth_tokenの値を貼る"
  ct0: "ここにct0の値を貼る"
```

または環境変数でも指定できる:

```cmd
set TWITTER_AUTH_TOKEN=your_auth_token
set TWITTER_CT0=your_ct0
```

> ⚠️ `auth_token` はログインと同等の情報です。他人に共有しないでください。

## 使い方

```bash
# 全件取得
python twitter-media-dl.py <username>

# 件数を指定して取得
python twitter-media-dl.py <username> --max 100

# リツイートのメディアも含める
python twitter-media-dl.py <username> --include-retweets
```

### 実行例

```
📁 保存先: C:\twitter\downloads\@elonmusk
👤 @elonmusk のプロフィールを取得中...
   ID: 44196397  ツイート数: 98,782
📡 ツイートを取得中...
   ページ 1: 21 件取得（累計 21 件）
   ページ 2: 20 件取得（累計 41 件）
   ...
📊 取得ツイート数: 100 件
🖼️  メディア数: 12 件（リツイート除外）

[1/12] ⬇️  [@elonmusk][202603092353] Real picture of @StarbaseTX.jpg
[2/12] ⏭️  スキップ: すでに存在するファイル
...
────────────────────────────────────────────────────────────
✅ 完了: 11 件ダウンロード / 1 件スキップ / 0 件失敗
```

## 注意事項

- Cookie 認証を使用するため、x.com への影響を最小化するためリクエスト間に 4 秒のウェイトを設けています
- Twitter 側の仕様変更により動作しなくなった場合は `pip install --upgrade twitter-cli` でアップデートを試してください
- Cookie の有効期限が切れた場合はブラウザで再ログインし、`config.yaml` の値を更新してください

## 依存関係

| パッケージ | 用途 |
|---|---|
| twitter-cli | Twitter GraphQL API の認証・通信・レスポンスパース |
