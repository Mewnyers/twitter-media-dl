import sys
import time
from datetime import datetime

from .settings import BATCH_SIZE, REQUEST_DELAY


class TwitterFetchError(RuntimeError):
    """Twitter/X 取得処理を安全に続行できない場合の例外。"""


def create_client(auth_token: str, ct0: str):
    """twitter-cli の TwitterClient を作成する。"""
    try:
        from twitter_cli.client import TwitterClient
    except ImportError:
        print("❌ twitter-cli が見つかりません。`pip install twitter-cli` でインストールしてください。")
        sys.exit(1)

    return TwitterClient(
        auth_token=auth_token,
        ct0=ct0,
        rate_limit_config={"requestDelay": REQUEST_DELAY, "maxRetries": 3, "maxCount": 200},
    )


def should_stop_at_tweet(tweet, since_dt: datetime | None) -> bool:
    """RTでない通常ツイートのみ、差分取得の打ち切り判定に使う。"""
    if since_dt is None or tweet.is_retweet:
        return False
    try:
        tweet_dt = datetime.strptime(tweet.created_at, "%a %b %d %H:%M:%S %z %Y")
    except ValueError:
        return False
    return tweet_dt < since_dt


def fetch_all_tweets_by_UserTweets(
    username: str,
    max_count: int | None,
    auth_token: str,
    ct0: str,
    since_dt: datetime | None = None,
):
    """
    twitter-cli の TwitterClient を直接使い、cursor ベースの
    ページネーションで全件（または max_count 件）取得する。
    Tweet オブジェクトのリストを返す。
    """
    try:
        from twitter_cli.client import FEATURES
        from twitter_cli.parser import _deep_get, parse_timeline_response
    except ImportError:
        print("❌ twitter-cli が見つかりません。`pip install twitter-cli` でインストールしてください。")
        sys.exit(1)

    client = create_client(auth_token, ct0)

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
            raise TwitterFetchError(f"APIエラー（UserTweets ページ{page + 1}）: {e}") from e

        new_tweets, next_cursor = parse_timeline_response(data, get_instructions)

        added = 0
        stop = False
        for tweet in new_tweets:
            if tweet.id and tweet.id not in seen_ids:
                if should_stop_at_tweet(tweet, since_dt):
                    stop = True
                    break
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


def fetch_all_tweets_by_UserMedia(
    username: str,
    max_count: int | None,
    auth_token: str,
    ct0: str,
    since_dt: datetime | None = None,
):
    """
    UserMedia エンドポイントを使いメディア付きツイートを取得する。
    返信ツイートのメディアも含む。
    """
    try:
        from twitter_cli.client import FEATURES
        from twitter_cli.graphql import FALLBACK_QUERY_IDS
        from twitter_cli.parser import _deep_get, _extract_cursor, parse_tweet_result

        if "UserMedia" not in FALLBACK_QUERY_IDS:
            FALLBACK_QUERY_IDS["UserMedia"] = "U1Zgdsu2qjBi8JF74lTmJQ"
    except ImportError:
        print("❌ twitter-cli が見つかりません。`pip install twitter-cli` でインストールしてください。")
        sys.exit(1)

    client = create_client(auth_token, ct0)

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
            raise TwitterFetchError(f"APIエラー（UserMedia ページ{page + 1}）: {e}") from e

        new_tweets, next_cursor = parse_usermedia_response(data)

        added = 0
        stop = False
        for tweet in new_tweets:
            if tweet.id and tweet.id not in seen_ids:
                if should_stop_at_tweet(tweet, since_dt):
                    stop = True
                    break
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
