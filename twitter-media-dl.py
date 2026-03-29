"""
twitter-media-dl.py
指定したTwitter/Xユーザーのメディア（画像・動画）を一括ダウンロードするスクリプト。

使い方:
  python twitter-media-dl.py <username>
  python twitter-media-dl.py <username> --max 100
  python twitter-media-dl.py <username> --include-retweets

認証:
  カレントディレクトリの config.yaml に以下を記述:
    auth:
      auth_token: "your_auth_token"
      ct0: "your_ct0"

保存先:
  ./downloads/@<username>/
"""

import argparse
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

# ────────────────────────────────────────────────────────────
# 設定
# ────────────────────────────────────────────────────────────

CONFIG_FILE = "config.yaml"
DOWNLOADS_DIR = "downloads"

DATE_PATTERN = "%Y%m%d%H%M"           # YYYYMMDDhhmm
TWEET_CONTENT_MAX_LEN = 200            # ファイル名内のツイート本文の最大文字数
REQUEST_INTERVAL = 1.0                # ダウンロード間隔（秒）
TIMEZONE_OFFSET_HOURS = 9  # 0=UTC, 9=JST

# Windowsで使えないファイル名文字
INVALID_CHARS_RE = re.compile(r'[\\/:*?"<>|\r\n\t]')


# ────────────────────────────────────────────────────────────
# 設定ファイルの読み込み
# ────────────────────────────────────────────────────────────

def load_config() -> dict:
    """config.yaml を読み込む。yaml ライブラリがない場合は簡易パーサーで対応。"""
    if not os.path.exists(CONFIG_FILE):
        return {}
    try:
        import yaml
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        # pyyaml が入っていない場合の簡易パーサー
        config = {}
        auth = {}
        with open(CONFIG_FILE, encoding="utf-8") as f:
            in_auth = False
            for line in f:
                line = line.rstrip()
                if line.strip() == "auth:":
                    in_auth = True
                    continue
                if in_auth and line.startswith(" "):
                    m = re.match(r'\s+(\w+):\s*["\']?([^"\']+)["\']?', line)
                    if m:
                        auth[m.group(1)] = m.group(2).strip()
                elif in_auth:
                    in_auth = False
        if auth:
            config["auth"] = auth
        return config


# ────────────────────────────────────────────────────────────
# ツイート取得（twitter-cli の Python API を直接使用）
# ────────────────────────────────────────────────────────────

BATCH_SIZE = 40        # 1リクエストあたりの取得件数
REQUEST_DELAY = 3.5   # ページネーション間のウェイト（秒）


def fetch_all_tweets_by_UserTweets(username: str, max_count: int | None, auth_token: str, ct0: str, since_dt: datetime | None = None):
    """
    twitter-cli の TwitterClient を直接使い、cursor ベースの
    ページネーションで全件（または max_count 件）取得する。
    Tweet オブジェクトのリストを返す。
    """
    try:
        from twitter_cli.client import TwitterClient, FEATURES
        from twitter_cli.parser import _deep_get
    except ImportError:
        print("❌ twitter-cli が見つかりません。`pip install twitter-cli` でインストールしてください。")
        sys.exit(1)

    client = TwitterClient(
        auth_token=auth_token,
        ct0=ct0,
        rate_limit_config={"requestDelay": REQUEST_DELAY, "maxRetries": 3, "maxCount": 200},
    )

    # ユーザーID取得
    print(f"👤 @{username} のプロフィールを取得中...(UserTweets)")
    try:
        user = client.fetch_user(username)
    except Exception as e:
        print(f"❌ ユーザー取得失敗: {e}")
        sys.exit(1)
    user_id = user.id
    print(f"   ID: {user_id}  ツイート数: {user.tweets_count:,}")

    # ページネーションループ
    all_tweets = []
    seen_ids = set()
    cursor = None
    page = 0

    def get_instructions(data):
        return _deep_get(data, "data", "user", "result", "timeline_v2", "timeline", "instructions")

    print("📡 ツイートを取得中...")

    while True:
        if max_count is not None and len(all_tweets) >= max_count:
            break

        variables = {
            "userId": user_id,
            "count": BATCH_SIZE,
            "includePromotedContent": True,
            "withQuickPromoteEligibilityTweetFields": True,
            "withVoice": True,
            "withV2Timeline": True,
        }
        if cursor:
            variables["cursor"] = cursor

        try:
            data = client._graphql_get("UserTweets", variables, FEATURES)
        except Exception as e:
            print(f"   ⚠️  APIエラー（ページ{page+1}）: {e}")
            break

        new_tweets, next_cursor = client._parse_timeline_response(data, get_instructions)

        added = 0
        stop = False
        for tweet in new_tweets:
            if tweet.id and tweet.id not in seen_ids:
                # RTでない通常ツイートのみ打ち切り判定に使う
                if since_dt is not None and not tweet.is_retweet:
                    try:
                        tweet_dt = datetime.strptime(tweet.created_at, "%a %b %d %H:%M:%S %z %Y")
                        if tweet_dt.replace(tzinfo=None) < since_dt:
                            stop = True
                            break
                    except ValueError:
                        pass
                seen_ids.add(tweet.id)
                all_tweets.append(tweet)
                added += 1
        if stop:
            break

        page += 1
        print(f"   ページ {page}: {added} 件取得（累計 {len(all_tweets)} 件）")

        # 終了条件: 新規ツイートなし、またはcursorが尽きた
        if not next_cursor or added == 0:
            break

        cursor = next_cursor
        time.sleep(REQUEST_DELAY)

    if max_count is not None:
        all_tweets = all_tweets[:max_count]

    return all_tweets, user


def fetch_all_tweets_by_UserMedia(username: str, max_count: int | None, auth_token: str, ct0: str, since_dt: datetime | None = None):
    """
    UserMedia エンドポイントを使いメディア付きツイートを取得する。
    返信ツイートのメディアも含む。
    """
    try:
        from twitter_cli.client import TwitterClient, FEATURES
        from twitter_cli.parser import _deep_get, _extract_cursor, parse_tweet_result
        from twitter_cli.graphql import FALLBACK_QUERY_IDS
        if "UserMedia" not in FALLBACK_QUERY_IDS:
            FALLBACK_QUERY_IDS["UserMedia"] = "U1Zgdsu2qjBi8JF74lTmJQ"
    except ImportError:
        print("❌ twitter-cli が見つかりません。`pip install twitter-cli` でインストールしてください。")
        sys.exit(1)

    client = TwitterClient(
        auth_token=auth_token,
        ct0=ct0,
        rate_limit_config={"requestDelay": REQUEST_DELAY, "maxRetries": 3, "maxCount": 200},
    )

    # ユーザーID取得
    print(f"👤 @{username} のプロフィールを取得中...(UserMedia)")
    try:
        user = client.fetch_user(username)
    except Exception as e:
        print(f"❌ ユーザー取得失敗: {e}")
        sys.exit(1)
    user_id = user.id
    print(f"   ID: {user_id}  ツイート数: {user.tweets_count:,}")

    # ページネーションループ
    all_tweets = []
    seen_ids = set()
    cursor = None
    page = 0

    def get_instructions(data):
        return _deep_get(data, "data", "user", "result", "timeline", "timeline", "instructions")

    def parse_usermedia_response(data):
        """UserMedia専用パーサー。ページ1とページ2以降で構造が異なるため独自実装。"""
        tweets = []
        next_cursor = None
        instructions = get_instructions(data)
        if not instructions:
            return tweets, next_cursor
        for instruction in instructions:
            # ページ1: entries の中に items がある構造
            for entry in instruction.get("entries", []):
                content = entry.get("content", {})
                next_cursor = _extract_cursor(content) or next_cursor
                for nested_item in content.get("items", []):
                    result = _deep_get(nested_item, "item", "itemContent", "tweet_results", "result")
                    if result:
                        tweet = parse_tweet_result(result)
                        if tweet:
                            tweets.append(tweet)
            # ページ2以降: moduleItems が直接ある構造
            for module_item in instruction.get("moduleItems", []):
                result = _deep_get(module_item, "item", "itemContent", "tweet_results", "result")
                if result:
                    tweet = parse_tweet_result(result)
                    if tweet:
                        tweets.append(tweet)
        return tweets, next_cursor
    
    print("📡 ツイートを取得中...")

    while True:
        if max_count is not None and len(all_tweets) >= max_count:
            break

        variables = {
            "userId": user_id,
            "count": BATCH_SIZE,
            "includePromotedContent": False,
            "withClientEventToken": False,
            "withBirdwatchNotes": False,
            "withVoice": True,
        }
        if cursor:
            variables["cursor"] = cursor

        try:
            data = client._graphql_get("UserMedia", variables, FEATURES)
        except Exception as e:
            print(f"   ⚠️  APIエラー（ページ{page+1}）: {e}")
            break

        new_tweets, next_cursor = parse_usermedia_response(data)
        
        added = 0
        stop = False
        for tweet in new_tweets:
            if tweet.id and tweet.id not in seen_ids:
                # RTでない通常ツイートのみ打ち切り判定に使う
                if since_dt is not None and not tweet.is_retweet:
                    try:
                        tweet_dt = datetime.strptime(tweet.created_at, "%a %b %d %H:%M:%S %z %Y")
                        if tweet_dt.replace(tzinfo=None) < since_dt:
                            stop = True
                            break
                    except ValueError:
                        pass
                seen_ids.add(tweet.id)
                all_tweets.append(tweet)
                added += 1
        if stop:
            break

        page += 1
        print(f"   ページ {page}: {added} 件取得（累計 {len(all_tweets)} 件）")

        if not next_cursor or added == 0:
            break

        cursor = next_cursor
        time.sleep(REQUEST_DELAY)

    if max_count is not None:
        all_tweets = all_tweets[:max_count]

    return all_tweets, user


# ────────────────────────────────────────────────────────────
# ファイル名生成
# ────────────────────────────────────────────────────────────

def parse_twitter_date(date_str: str) -> str:
    try:
        from datetime import timezone, timedelta
        dt = datetime.strptime(date_str, "%a %b %d %H:%M:%S %z %Y")
        dt = dt.astimezone(timezone(timedelta(hours=TIMEZONE_OFFSET_HOURS)))
        return dt.strftime(DATE_PATTERN)
    except ValueError:
        # パース失敗時はそのまま返す
        return re.sub(r"[^0-9]", "", date_str)[:12]


def sanitize_filename(text: str) -> str:
    """ファイル名に使えない文字を除去し、空白を整理する。"""
    text = INVALID_CHARS_RE.sub("", text)
    text = text.strip()
    # 連続スペースを1つに
    text = re.sub(r" {2,}", " ", text)
    return text


def anonymize_filename(filename: str) -> str:
    """ファイル名のツイート本文部分をSHA256ハッシュの先頭8文字に置換する。"""
    import hashlib
    return re.sub(
        r"(\[@[^\]]+\]\[\d{12}\] )(.+?)(_\d+)?(\.[^.]+)$",
        lambda m: m.group(1) + hashlib.sha256(m.group(2).encode()).hexdigest()[:8] + (m.group(3) or "") + m.group(4),
        filename
    )


def truncate_content(text: str, max_len: int = TWEET_CONTENT_MAX_LEN) -> str:
    """ツイート本文をmax_len文字に切り詰める。"""
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip()


def build_filename(author: str, created_at: str, text: str, index: int | None, media_url: str) -> str:
    """
    [@author][YYYYMMDDhhmm] tweetContent{index}.ext
    index が None なら index部分は空文字（メディアが1枚のみの場合）
    """
    date_str = parse_twitter_date(created_at)
    content = sanitize_filename(truncate_content(text))

    # 拡張子を決定
    # URL例: https://pbs.twimg.com/media/xxx.jpg?format=jpg&name=orig
    #        https://video.twimg.com/xxx/xxx.mp4
    url_path = media_url.split("?")[0]  # クエリパラメータを除去
    ext = url_path.rsplit(".", 1)[-1] if "." in url_path else "jpg"
    ext = ext.lower()

    index_str = f"_{index}" if index is not None else ""

    filename = f"[@{author}][{date_str}] {content}{index_str}.{ext}"
    return filename


# ────────────────────────────────────────────────────────────
# メディア抽出
# ────────────────────────────────────────────────────────────

def extract_media_items(tweets, include_retweets: bool) -> list[dict]:
    """
    Tweet オブジェクトのリストからダウンロード対象のメディア情報を抽出する。
    戻り値: [{"filename": str, "url": str, "tweet_id": str, "type": str}, ...]
    """
    items = []

    for tweet in tweets:
        # 他ユーザーのRTのみ除外（自己RTは常に通す）
        if tweet.is_retweet:
            is_self_retweet = tweet.retweeted_by == tweet.author.screen_name
            if not is_self_retweet and not include_retweets:
                continue

        if not tweet.media:
            continue

        author = tweet.author.screen_name
        created_at = tweet.created_at
        text = tweet.text

        # RTプレフィックスを除去（"RT @username: " の形式）
        text = re.sub(r"^RT @\w+: ", "", text)

        # t.co短縮リンクをテキストから除去
        text = re.sub(r"https://t\.co/\S+", "", text).strip()

        multiple = len(tweet.media) > 1

        for i, media in enumerate(tweet.media, start=1):
            media_type = media.type
            raw_url = media.url

            if not raw_url:
                continue

            # 画像はオリジナル画質
            if media_type == "photo":
                url = raw_url.split("?")[0] + "?format=jpg&name=orig" if "pbs.twimg.com/media" in raw_url else raw_url
            else:
                url = raw_url

            index = i if multiple else None
            filename = build_filename(author, created_at, text, index, url)

            items.append({
                "filename": filename,
                "url": url,
                "tweet_id": tweet.id,
                "type": media_type,
            })

    return items


# ────────────────────────────────────────────────────────────
# ダウンロード
# ────────────────────────────────────────────────────────────

def download_file(url: str, dest_path: Path) -> bool:
    """
    ファイルをダウンロードして dest_path に保存する。
    成功したら True、失敗したら False を返す。
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) ",
        "Referer": "https://x.com/",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            data = response.read()
        dest_path.write_bytes(data)
        return True
    except urllib.error.HTTPError as e:
        print(f"    ⚠️  HTTP {e.code}: {url}")
        return False
    except Exception as e:
        print(f"    ⚠️  ダウンロード失敗: {e}")
        return False


def download_all(items: list[dict], output_dir: Path, anonymize: bool = False):
    """全メディアをダウンロードする。"""
    total = len(items)
    skipped = 0
    downloaded = 0
    failed = 0

    for idx, item in enumerate(items, start=1):
        filename = item["filename"]
        url = item["url"]
        dest = output_dir / filename
        display_name = anonymize_filename(filename) if anonymize else filename

        prefix = f"[{idx}/{total}]"

        if dest.exists():
            print(f"{prefix} ⏭️  スキップ: {display_name}")
            skipped += 1
            continue

        print(f"{prefix} ⬇️  {display_name}")
        success = download_file(url, dest)

        if success:
            downloaded += 1
        else:
            failed += 1

        time.sleep(REQUEST_INTERVAL)

    print()
    print("─" * 60)
    print(f"✅ 完了: {downloaded} 件ダウンロード / {skipped} 件スキップ / {failed} 件失敗")


# ────────────────────────────────────────────────────────────
# エントリポイント
# ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Twitter/X ユーザーのメディアを一括ダウンロードします。"
    )
    parser.add_argument("username", help="ダウンロード対象のユーザーID（@なし）")
    parser.add_argument("--max", type=int, default=None, dest="max_count",
                        help="取得するツイートの最大件数（省略時は全件取得）")
    parser.add_argument("--include-retweets", action="store_true",
                        help="リツイートのメディアも含める（デフォルト: 除外）")
    parser.add_argument("--full", action="store_true",
                        help="差分モードを無視して全件取得する")
    parser.add_argument("--anonymize", action="store_true",
                        help="デバッグ出力のツイート本文をハッシュ化する")
    parser.add_argument("--debug", action="store_true",
                        help="ツイートの生データをJSONに出力してデバッグ（ダウンロードは行わない）")
    args = parser.parse_args()

    username = args.username.lstrip("@")

    # 設定ファイル読み込み
    config = load_config()
    auth = config.get("auth", {})

    # 認証情報: config.yaml → 環境変数の順で取得
    auth_token = auth.get("auth_token") or os.environ.get("TWITTER_AUTH_TOKEN", "")
    ct0 = auth.get("ct0") or os.environ.get("TWITTER_CT0", "")

    if not auth_token or not ct0:
        print("❌ 認証情報が見つかりません。")
        print("   config.yaml に auth_token と ct0 を記述するか、")
        print("   環境変数 TWITTER_AUTH_TOKEN / TWITTER_CT0 を設定してください。")
        sys.exit(1)
    
    # 差分モード: 既存フォルダのファイル名から最新日時を取得
    since_dt = None
    if not args.full:
        dl_root = Path(DOWNLOADS_DIR)
        # フォルダ名が "* (@username)" にマッチするものを探す
        pattern = re.compile(r"\[(\d{12})\]")
        candidates = []
        for folder in dl_root.iterdir() if dl_root.exists() else []:
            if folder.is_dir() and f"(@{username})" in folder.name:
                for f in folder.iterdir():
                    m = pattern.search(f.name)
                    if m:
                        try:
                            candidates.append(datetime.strptime(m.group(1), "%Y%m%d%H%M"))
                        except ValueError:
                            pass
        if candidates:
            since_dt = max(candidates)
            print(f"   候補フォルダ確認: {[f.name for f in dl_root.iterdir() if f.is_dir() and f.name.endswith(f'(@{username})')]}")
            print(f"📅 差分モード: {since_dt.strftime('%Y/%m/%d %H:%M')} 以降を取得")
        else:
            print("📅 全件モード: 既存ファイルが見つからないため全件取得")

    # ツイート取得
    tweets, user = fetch_all_tweets_by_UserMedia(username, args.max_count, auth_token, ct0, since_dt)

    # 保存先ディレクトリを作成
    today = datetime.now().strftime("%Y%m%d")
    folder_name = sanitize_filename(f"{user.name} (@{username})") + f" [{today}]"
    output_dir = Path(DOWNLOADS_DIR) / folder_name
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"📁 保存先: {output_dir.resolve()}")
    print(f"📊 取得ツイート数: {len(tweets)} 件")

    # デバッグモード: 生データをJSONに出力して終了
    if args.debug:
        import json
        debug_data = []
        for t in tweets:
            import hashlib
            text_display = hashlib.sha256(t.text.encode()).hexdigest()[:8] if args.anonymize else t.text
            debug_data.append({
                "id": t.id,
                "text": text_display,
                "author": t.author.screen_name,
                "is_retweet": t.is_retweet,
                "retweeted_by": t.retweeted_by,
                "has_media": bool(t.media),
                "media_count": len(t.media),
                "has_quoted_tweet": t.quoted_tweet is not None,
                "quoted_tweet_author": t.quoted_tweet.author.screen_name if t.quoted_tweet else None,
                "quoted_tweet_has_media": bool(t.quoted_tweet and t.quoted_tweet.media),
            })
        out_path = Path(f"debug_{username}_{datetime.now().strftime('%Y%m%d%H%M%S')}.json")
        out_path.write_text(json.dumps(debug_data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"📄 デバッグデータを出力しました: {out_path}")
        return

    # メディア抽出
    items = extract_media_items(tweets, args.include_retweets)
    if not items:
        print("ℹ️  ダウンロード対象のメディアが見つかりませんでした。")
        return

    rt_msg = "（リツイート含む）" if args.include_retweets else "（リツイート除外）"
    print(f"🖼️  メディア数: {len(items)} 件 {rt_msg}")
    print()

    # ダウンロード
    download_all(items, output_dir, anonymize=args.anonymize)


if __name__ == "__main__":
    main()
