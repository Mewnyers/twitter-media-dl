from dataclasses import dataclass
from pathlib import Path

from .client import TwitterFetchError, fetch_tweets
from .downloader import download_all, get_item_watermark, rename_legacy_file
from .media import extract_media_items, sort_media_items_for_download
from .settings import DOWNLOADS_DIR
from .storage import (
    find_downloaded_user_folders,
    find_latest_downloaded_datetime,
    format_download_timestamp,
    is_unavailable_state,
    load_since_datetime,
    prepare_output_dir,
    save_active_state,
    save_since_datetime,
    save_unavailable_state,
)
from .throttle import is_rate_limited, wait_after_rate_limit, wait_between_users


@dataclass
class ScanSummary:
    found: int
    renamed: int
    missing: int
    failed: int
    watermark: str | None


def scan_downloaded_items(
    items: list[dict],
    output_dirs: list[Path],
    *,
    initial_watermark: str | None = None,
    anonymize: bool = False,
) -> ScanSummary:
    """既存ファイルを確認し、旧ファイル名があれば新ファイル名へリネームする。"""
    from .filename import anonymize_filename

    found = 0
    renamed = 0
    missing = 0
    failed = 0
    watermark = initial_watermark
    blocked_by_gap = False
    total = len(items)

    for idx, item in enumerate(items, start=1):
        filename = item["filename"]
        display_name = anonymize_filename(filename) if anonymize else filename
        prefix = f"[{idx}/{total}]"

        try:
            renamed_in_any_dir = False
            for output_dir in output_dirs:
                if rename_legacy_file(output_dir, item):
                    renamed_in_any_dir = True
        except OSError as e:
            print(f"{prefix} [WARN] 旧ファイル名のリネーム失敗: {e}")
            failed += 1
            blocked_by_gap = True
            continue

        if renamed_in_any_dir:
            print(f"{prefix} [RENAME] リネーム: {display_name}")
            renamed += 1
            if not blocked_by_gap:
                watermark = get_item_watermark(item) or watermark
            continue

        if any((output_dir / filename).exists() for output_dir in output_dirs):
            print(f"{prefix} [OK] 確認済み: {display_name}")
            found += 1
            if not blocked_by_gap:
                watermark = get_item_watermark(item) or watermark
            continue

        print(f"{prefix} [MISSING] 未保存: {display_name}")
        missing += 1
        blocked_by_gap = True

    print()
    print("-" * 60)
    print(f"スキャン完了: {found} 件確認 / {renamed} 件リネーム / {missing} 件未保存 / {failed} 件失敗")
    if missing or failed:
        print("[WARN] 未保存または失敗があるため、差分境界はそこより先へ進めません。")

    return ScanSummary(found=found, renamed=renamed, missing=missing, failed=failed, watermark=watermark)


def _save_unavailable(username: str, error: Exception, folders: list[Path], downloads_dir: str):
    legacy_dt, matched_folders = find_latest_downloaded_datetime(username, downloads_dir)
    watermark = format_download_timestamp(legacy_dt) if legacy_dt else None
    folder_names = matched_folders or [folder.name for folder in folders]
    save_unavailable_state(username, str(error), watermark=watermark, folders=folder_names, downloads_dir=downloads_dir)
    print(f"   取得不能として状態ファイルに記録しました: {error}")


def _should_skip_unavailable(username: str, retry_unavailable: bool) -> bool:
    if retry_unavailable:
        return False
    return is_unavailable_state(username, DOWNLOADS_DIR)


def _is_unavailable_user_error(error: Exception) -> bool:
    """ユーザー自体が取得不能なエラーかを判定する。API一時失敗は含めない。"""
    return "ユーザー取得失敗" in str(error)


def scan_downloads(
    auth_token: str,
    ct0: str,
    *,
    include_retweets: bool = False,
    anonymize: bool = False,
    retry_unavailable: bool = False,
):
    """downloads 配下のユーザーを一括スキャンし、リネームと状態作成を行う。"""
    users = find_downloaded_user_folders(DOWNLOADS_DIR)
    if not users:
        print("downloads 配下にユーザーフォルダが見つかりませんでした。")
        return

    print(f"一括スキャン対象: {len(users)} ユーザー")

    for index, (username, folders) in enumerate(users.items(), start=1):
        rate_limited = False
        print()
        print("=" * 60)
        print(f"[{index}/{len(users)}] @{username}")
        print(f"   対象フォルダ: {[folder.name for folder in folders]}")

        try:
            if _should_skip_unavailable(username, retry_unavailable):
                print("   取得不能として記録済みのためスキップします。再確認する場合は --retry-unavailable を指定してください。")
                continue
        except RuntimeError as e:
            print(f"[ERROR] {e}")
            continue

        try:
            tweets, _ = fetch_tweets(username, None, auth_token, ct0, None)
        except TwitterFetchError as e:
            rate_limited = is_rate_limited(e)
            if rate_limited:
                print(f"[ERROR] rate limit のため取得を中断しました: {e}")
            elif _is_unavailable_user_error(e):
                _save_unavailable(username, e, folders, DOWNLOADS_DIR)
            else:
                print(f"[ERROR] ツイート取得に失敗しました。状態ファイルは更新しません: {e}")
        else:
            items = sort_media_items_for_download(extract_media_items(tweets, include_retweets))
            if not items:
                save_active_state(username, folders=[folder.name for folder in folders])
                print("メディア付きツイートが見つかりませんでした。")
            else:
                summary = scan_downloaded_items(items, folders, anonymize=anonymize)
                save_active_state(username, summary.watermark, folders=[folder.name for folder in folders])
                if summary.watermark:
                    print(f"差分境界を保存: {summary.watermark}")

        if index < len(users):
            if rate_limited:
                wait_after_rate_limit()
            wait_between_users()


def update_all_downloads(
    auth_token: str,
    ct0: str,
    *,
    include_retweets: bool = False,
    full: bool = False,
    anonymize: bool = False,
    retry_unavailable: bool = False,
):
    """downloads 配下のユーザーを一括更新する。"""
    users = find_downloaded_user_folders(DOWNLOADS_DIR)
    if not users:
        print("downloads 配下にユーザーフォルダが見つかりませんでした。")
        return

    print(f"一括更新対象: {len(users)} ユーザー")

    for index, (username, folders) in enumerate(users.items(), start=1):
        rate_limited = False
        print()
        print("=" * 60)
        print(f"[{index}/{len(users)}] @{username}")

        try:
            if _should_skip_unavailable(username, retry_unavailable):
                print("   取得不能として記録済みのためスキップします。再確認する場合は --retry-unavailable を指定してください。")
                continue
        except RuntimeError as e:
            print(f"[ERROR] {e}")
            continue

        since_dt = None
        if not full:
            try:
                since_dt = load_since_datetime(username)
            except RuntimeError as e:
                print(f"[ERROR] {e}")
                continue
            if since_dt:
                print(f"差分モード: {since_dt.strftime('%Y/%m/%d %H:%M')} 以降を取得")
            else:
                print("全件モード: 状態ファイルがないため全件取得")
        else:
            print("全件モード: --full 指定")

        try:
            tweets, user = fetch_tweets(username, None, auth_token, ct0, since_dt)
        except TwitterFetchError as e:
            rate_limited = is_rate_limited(e)
            if rate_limited:
                print(f"[ERROR] rate limit のため取得を中断しました: {e}")
            elif _is_unavailable_user_error(e):
                _save_unavailable(username, e, folders, DOWNLOADS_DIR)
            else:
                print(f"[ERROR] ツイート取得に失敗しました。状態ファイルは更新しません: {e}")
        else:
            output_dir = prepare_output_dir(user, username)
            print(f"保存先: {output_dir.resolve()}")
            print(f"取得ツイート数: {len(tweets)} 件")

            items = sort_media_items_for_download(extract_media_items(tweets, include_retweets))
            if not items:
                print("ダウンロード対象のメディアが見つかりませんでした。")
            else:
                rt_msg = "（リツイート含む）" if include_retweets else "（リツイート除外）"
                print(f"メディア数: {len(items)} 件 {rt_msg}")
                print()

                initial_watermark = format_download_timestamp(since_dt) if since_dt else None
                summary = download_all(items, output_dir, initial_watermark=initial_watermark, anonymize=anonymize)
                save_since_datetime(username, summary.watermark)
                if summary.watermark:
                    print(f"差分境界を保存: {summary.watermark}")

        if index < len(users):
            if rate_limited:
                wait_after_rate_limit()
            wait_between_users()
