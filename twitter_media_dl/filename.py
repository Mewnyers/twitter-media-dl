import hashlib
import re
from datetime import datetime, timedelta, timezone

from .settings import DATE_PATTERN, INVALID_CHARS_RE, TIMEZONE_OFFSET_HOURS, TWEET_CONTENT_MAX_LEN


def parse_twitter_date(date_str: str) -> str:
    try:
        dt = datetime.strptime(date_str, "%a %b %d %H:%M:%S %z %Y")
        dt = dt.astimezone(timezone(timedelta(hours=TIMEZONE_OFFSET_HOURS)))
        return dt.strftime(DATE_PATTERN)
    except ValueError:
        return re.sub(r"[^0-9]", "", date_str)[:12]


def sanitize_filename(text: str) -> str:
    """ファイル名に使えない文字を除去し、空白を整理する。"""
    text = INVALID_CHARS_RE.sub("", text)
    text = text.strip()
    return re.sub(r" {2,}", " ", text)


def anonymize_filename(filename: str) -> str:
    """ファイル名のツイート本文部分をSHA256ハッシュの先頭8文字に置換する。"""
    return re.sub(
        r"(\[@[^\]]+\]\[\d{12}\] )(.+?)(_\d+)?(\.[^.]+)$",
        lambda m: m.group(1)
        + hashlib.sha256(m.group(2).encode()).hexdigest()[:8]
        + (m.group(3) or "")
        + m.group(4),
        filename,
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

    # URL例: https://pbs.twimg.com/media/xxx.jpg?format=jpg&name=orig
    #        https://video.twimg.com/xxx/xxx.mp4
    url_path = media_url.split("?")[0]
    ext = url_path.rsplit(".", 1)[-1] if "." in url_path else "jpg"
    ext = ext.lower()

    index_str = f"_{index}" if index is not None else ""
    return f"[@{author}][{date_str}] {content}{index_str}.{ext}"
