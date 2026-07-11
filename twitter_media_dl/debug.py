import hashlib
import json
from datetime import datetime
from pathlib import Path


def write_debug_data(tweets, username: str, anonymize: bool = False) -> Path:
    """ツイートの生データ確認用 JSON を出力する。"""
    debug_data = []
    for t in tweets:
        text_display = hashlib.sha256(t.text.encode()).hexdigest()[:8] if anonymize else t.text
        debug_data.append(
            {
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
            }
        )
    out_path = Path(f"debug_{username}_{datetime.now().strftime('%Y%m%d%H%M%S')}.json")
    out_path.write_text(json.dumps(debug_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path

