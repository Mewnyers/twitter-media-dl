from dataclasses import dataclass, field
from pathlib import Path

from .client import TwitterFetchError, fetch_tweets
from .downloader import download_user_media, get_item_watermark, rename_legacy_file, UserResult, print_results
from .filename import anonymize_filename
from .media import extract_media_items, sort_media_items_for_download
from .settings import DOWNLOADS_DIR
from .storage import (
    find_downloaded_user_folders, is_unavailable_state, load_since_datetime, load_user_state,
    media_key, parse_download_timestamp, prepare_output_dir, save_active_state, save_unavailable_state, StateSaveError,
)
from .throttle import is_rate_limited, wait_between_update_all_users, wait_between_users


@dataclass
class ScanSummary:
    found: int
    renamed: int
    missing: int
    failed: int
    watermark: str | None
    pending: dict = field(default_factory=dict)


def scan_downloaded_items(items: list[dict], output_dirs: list[Path], *,
                          initial_watermark=None, anonymize=False, pending_downloads=None,
                          checkpoint=None) -> ScanSummary:
    """既存ファイルを確認し、旧ファイル名を移行して未保存項目を記録する。"""
    pending = dict(pending_downloads or {})
    merged = {key: dict(item) for key, item in pending.items()}
    for item in items:
        key = media_key(item)
        merged[key] = {**merged.get(key, {}), **item}
    items = sort_media_items_for_download(list(merged.values()))
    for item in items:
        if not parse_download_timestamp(item.get('created_at_sort', '')):
            raise ValueError(f"メディア日時が不正です: {media_key(item)}")
    found = renamed = missing = failed = 0
    watermark = initial_watermark
    for idx, item in enumerate(items, start=1):
        filename = item['filename']
        display_name = anonymize_filename(filename) if anonymize else filename
        prefix = f"[{idx}/{len(items)}]"
        error = None
        try:
            # 複数フォルダ内の旧ファイルも従来どおり移行する。
            renamed_in_any_dir = False
            for output_dir in output_dirs:
                if rename_legacy_file(output_dir, item):
                    renamed_in_any_dir = True
            if renamed_in_any_dir:
                renamed += 1
                print(f"{prefix} [RENAME] {display_name}")
            elif any((directory / filename).is_file() for directory in output_dirs):
                found += 1
                print(f"{prefix} [OK] {display_name}")
            else:
                missing += 1
                error = '未保存（スキャンのみ、ダウンロード未実行）'
        except Exception as e:
            failed += 1
            error = f"{type(e).__name__}: {e}"
        key = media_key(item)
        watermark = max(watermark or '', get_item_watermark(item))
        if error:
            # スキャンではダウンロード試行回数を増やさない。
            pending[key] = {**item, 'attempts': item.get('attempts', 0), 'last_error': error}
            print(f"{prefix} [WARN] {display_name}: {error}")
            if checkpoint:
                checkpoint(watermark, pending)
        else:
            pending.pop(key, None)
    if checkpoint:
        checkpoint(watermark, pending)
    print(f"スキャン完了: {found} 件確認 / {renamed} 件リネーム / {missing} 件未保存 / {failed} 件失敗")
    return ScanSummary(found, renamed, missing, failed, watermark, pending)


def _should_skip_unavailable(username: str, retry_unavailable: bool) -> bool:
    return not retry_unavailable and is_unavailable_state(username, DOWNLOADS_DIR)


def _is_unavailable_user_error(error: Exception) -> bool:
    """取得不能と断定できない通信・解析エラーは永続スキップしない。"""
    message = str(error).lower()
    return any(marker in message for marker in ('user not found', 'user has been suspended', 'user is suspended'))


def _run_bulk(auth_token, ct0, *, scan, include_retweets, full=False, anonymize=False, retry_unavailable=False):
    """一括処理の結果収集とエラー境界を共通化する。取得範囲と待機は維持する。"""
    users = find_downloaded_user_folders(DOWNLOADS_DIR)
    if not users:
        print('downloads 配下にユーザーフォルダが見つかりませんでした。')
        return True
    results = []
    print(f"一括{'スキャン' if scan else '更新'}対象: {len(users)} ユーザー")
    for index, (username, folders) in enumerate(users.items(), start=1):
        print(f"\n{'=' * 60}\n[{index}/{len(users)}] @{username}")
        result = UserResult(username)
        results.append(result)
        since_dt = None
        requested = False
        try:
            state = load_user_state(username, DOWNLOADS_DIR) or {}
            result.pending = state.get('pending_downloads', {})
            if _should_skip_unavailable(username, retry_unavailable):
                result.skipped = True
                print('取得不能として記録済みのためスキップします。再確認には --retry-unavailable を指定してください。')
                continue
            if not scan and not full:
                since_dt = load_since_datetime(username, DOWNLOADS_DIR)
            print(f"差分モード: {since_dt}" if since_dt else '全件モード')
            requested = True
            tweets, user = fetch_tweets(username, None, auth_token, ct0, since_dt)
            items = sort_media_items_for_download(extract_media_items(tweets, include_retweets))
            if scan:
                def checkpoint(watermark, pending):
                    save_active_state(username, watermark, folders=[folder.name for folder in folders],
                                      pending_downloads=pending, downloads_dir=DOWNLOADS_DIR)

                summary = scan_downloaded_items(items, folders, initial_watermark=state.get('watermark'),
                                               anonymize=anonymize, pending_downloads=result.pending,
                                               checkpoint=checkpoint)
            else:
                output_dir = prepare_output_dir(user, username, DOWNLOADS_DIR)
                print(f"保存先: {output_dir.resolve()}\n取得ツイート数: {len(tweets)} 件")
                summary = download_user_media(username, items, output_dir, anonymize=anonymize)
                result.rate_limited = summary.rate_limited
            result.pending = summary.pending
        except Exception as e:
            result.error = f"{type(e).__name__}: {e}"
            result.rate_limited = is_rate_limited(e)
            try:
                if isinstance(e, TwitterFetchError) and not result.rate_limited and _is_unavailable_user_error(e):
                    save_unavailable_state(username, str(e), folders=[folder.name for folder in folders],
                                           downloads_dir=DOWNLOADS_DIR)
                result.pending = (load_user_state(username, DOWNLOADS_DIR) or {}).get('pending_downloads', {})
            except Exception as state_error:
                result.error += f"; 状態処理: {state_error}"
            if isinstance(e, StateSaveError):
                result.pending = e.pending_downloads
            print(f"[ERROR] {result.error}")
        if result.rate_limited:
            print('[ERROR] rate limitのため一括処理を停止します。')
            break
        if requested and index < len(users):
            if since_dt is None:
                wait_between_users()
            else:
                wait_between_update_all_users()
    print_results(results, len(users), anonymize)
    return len(results) == len(users) and all(r.success or (r.skipped and not r.pending) for r in results)


def scan_downloads(auth_token, ct0, *, include_retweets=False, anonymize=False, retry_unavailable=False):
    """downloads 配下を一括スキャンし、リネームと状態作成だけを行う。"""
    return _run_bulk(auth_token, ct0, scan=True, include_retweets=include_retweets,
                     anonymize=anonymize, retry_unavailable=retry_unavailable)


def update_all_downloads(auth_token, ct0, *, include_retweets=False, full=False,
                         anonymize=False, retry_unavailable=False):
    """downloads 配下のユーザーを一括更新する。"""
    return _run_bulk(auth_token, ct0, scan=False, include_retweets=include_retweets, full=full,
                     anonymize=anonymize, retry_unavailable=retry_unavailable)
