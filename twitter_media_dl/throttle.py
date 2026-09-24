import time

from .settings import ENDPOINT_DELAY, UPDATE_ALL_USER_DELAY, USER_DELAY


def is_rate_limited(error: Exception | str) -> bool:
    """429/rate limit 系のエラーかを、外部ライブラリの例外型に依存せず判定する。"""
    message = str(error).lower()
    return "429" in message or "rate limited" in message or "rate limit" in message


def wait_seconds(seconds: float, reason: str):
    """ユーザーに理由を表示してから待機する。"""
    if seconds <= 0:
        return
    print(f"   {reason}: {seconds:.0f}秒待機します。")
    time.sleep(seconds)


def wait_between_endpoints():
    """初回取得で別エンドポイントへ続けてアクセスする前に待つ。"""
    wait_seconds(ENDPOINT_DELAY, "連続API取得を避けるため")


def wait_between_users():
    """全件取得を伴う複数ユーザー処理で次のユーザーへ進む前に待つ。"""
    wait_seconds(USER_DELAY, "次のユーザー処理まで")


def wait_between_update_all_users():
    """差分一括更新で次のユーザーへ進む前に短く待つ。"""
    wait_seconds(UPDATE_ALL_USER_DELAY, "次のユーザー更新まで")
