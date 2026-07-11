import re

from .filename import build_filename, parse_twitter_date


def extract_media_items(tweets, include_retweets: bool) -> list[dict]:
    """
    Tweet オブジェクトのリストからダウンロード対象のメディア情報を抽出する。
    戻り値: [{"filename": str, "url": str, "tweet_id": str, "type": str, ...}, ...]
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

            items.append(
                {
                    "filename": filename,
                    "url": url,
                    "tweet_id": tweet.id,
                    "type": media_type,
                    "created_at_sort": parse_twitter_date(created_at),
                    "media_index": i,
                }
            )

    return items


def sort_media_items_for_download(items: list[dict]) -> list[dict]:
    """
    差分取得は保存済みファイルの最大日時を境界にするため、
    ダウンロードは古いメディアから順に進める。
    """
    filename_date_re = re.compile(r"\[(\d{14})\]")

    def sort_key(item: dict):
        created_at_sort = item.get("created_at_sort")
        if not created_at_sort:
            m = filename_date_re.search(item.get("filename", ""))
            created_at_sort = m.group(1) if m else ""
        return (created_at_sort, item.get("tweet_id") or "", item.get("media_index") or 0)

    return sorted(items, key=sort_key)
