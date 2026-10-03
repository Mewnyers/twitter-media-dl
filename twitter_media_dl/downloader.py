from dataclasses import dataclass, field
from datetime import datetime, timezone
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

from .filename import anonymize_filename
from .settings import REQUEST_INTERVAL
from .media import sort_media_items_for_download
from .storage import media_key, load_user_state, save_active_state, parse_download_timestamp
from .throttle import is_rate_limited


@dataclass
class DownloadSummary:
    downloaded: int
    skipped: int
    renamed: int
    failed: int
    watermark: str | None
    pending: dict = field(default_factory=dict)
    rate_limited: bool = False


@dataclass
class UserResult:
    username: str
    pending: dict = field(default_factory=dict)
    error: str | None = None
    rate_limited: bool = False
    skipped: bool = False

    @property
    def success(self):
        return not self.error and not self.pending and not self.skipped


def print_results(results: list[UserResult], total: int, anonymize: bool = False):
    """単体・一括処理で同じ形式の最終エラー一覧を出す。"""
    failed = sum(bool(result.error or result.pending) for result in results)
    print(f"\n処理結果: {sum(r.success for r in results)} ユーザー成功 / {failed} ユーザー未解決 / "
          f"{sum(r.skipped for r in results)} ユーザースキップ / {total - len(results)} ユーザー未処理")
    for result in results:
        if not result.error and not result.pending:
            continue
        print(f"@{result.username}")
        if result.error:
            print(f"  {result.error}")
        for item in result.pending.values():
            name = anonymize_filename(item['filename']) if anonymize else item['filename']
            print(f"  {name} (tweet_id={item['tweet_id']}, media={item['media_index']})")
            print(f"    {item.get('last_error', '未保存')} / 試行回数: {item.get('attempts', 0)}")


def create_ssl_context():
    """certifi が利用できる場合は、更新されたCAバンドルをHTTPS検証に使う。"""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def download_file(url: str, dest_path: Path) -> str | None:
    """
    ファイルをダウンロードして dest_path に保存する。
    成功したら None、失敗したら理由を返す。途中ファイルは完成品として残さない。
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) ",
        "Referer": "https://x.com/",
    }
    temporary = dest_path.with_name(dest_path.name + ".part")
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30, context=create_ssl_context()) as response:
            data = response.read()
        temporary.write_bytes(data)
        temporary.replace(dest_path)
        return None
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return f"{type(e).__name__}: {e.reason}"
    except Exception as e:
        return f"{type(e).__name__}: {e}"
    finally:
        if temporary.exists():
            temporary.unlink()


def get_item_watermark(item: dict) -> str | None:
    """watermark更新に使うメディア日時を返す。"""
    return item.get("created_at_sort")


def rename_legacy_file(output_dir: Path, item: dict) -> bool:
    """旧ルールのファイル名が存在する場合、新ルールのファイル名へ移行する。"""
    legacy_filenames = item.get("legacy_filenames") or []
    legacy_filename = item.get("legacy_filename")
    if legacy_filename:
        legacy_filenames = [legacy_filename, *legacy_filenames]
    if not legacy_filenames:
        return False

    dest = output_dir / item["filename"]
    if dest.exists():
        return False

    for legacy_filename in dict.fromkeys(legacy_filenames):
        legacy_dest = output_dir / legacy_filename
        if legacy_dest.exists():
            legacy_dest.rename(dest)
            return True

    return False


def download_all(items: list[dict], output_dir: Path, initial_watermark: str | None = None,
                 anonymize: bool = False, *, pending_downloads=None, checkpoint=None):
    """全メディアをダウンロードする。"""
    pending = dict(pending_downloads or {})
    merged = {key: dict(item) for key, item in pending.items()}
    for item in items:
        key = media_key(item)
        merged[key] = {**merged.get(key, {}), **item}
    items = sort_media_items_for_download(list(merged.values()))
    # 境界を進める前に全項目の日時を検証する。途中に不正な日時があれば取得漏れを防ぐ。
    for item in items:
        if not parse_download_timestamp(item.get('created_at_sort', '')):
            raise ValueError(f"メディア日時が不正です: {media_key(item)}")
    total = len(items)
    skipped = 0
    renamed = 0
    downloaded = 0
    failed = 0
    watermark = initial_watermark
    rate_limited = False

    for idx, item in enumerate(items, start=1):
        filename = item["filename"]
        url = item["url"]
        dest = output_dir / filename
        display_name = anonymize_filename(filename) if anonymize else filename

        prefix = f"[{idx}/{total}]"

        key = media_key(item)
        error = None
        try:
            if dest.is_file():
                print(f"{prefix} ⏭️  スキップ: {display_name}")
                skipped += 1
            elif rename_legacy_file(output_dir, item):
                print(f"{prefix} 🔁  リネーム: {display_name}")
                renamed += 1
            else:
                print(f"{prefix} ⬇️  {display_name}")
                error = download_file(url, dest)
                if error is None:
                    downloaded += 1
                time.sleep(REQUEST_INTERVAL)
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
        watermark = max(watermark or '', get_item_watermark(item))
        if error is None:
            pending.pop(key, None)
        else:
            failed += 1
            pending[key] = {**item, 'attempts': item.get('attempts', 0) + 1,
                            'last_error': error, 'last_attempt_at': datetime.now(timezone.utc).isoformat()}
            print(f"    [ERROR] {error}")
            if checkpoint:
                checkpoint(watermark, pending)
            if is_rate_limited(error):
                rate_limited = True
                break

    if checkpoint:
        checkpoint(watermark, pending)

    print()
    print("─" * 60)
    status = "中断" if rate_limited else "完了（未解決あり）" if pending else "完了"
    print(f"{status}: {downloaded} 件ダウンロード / {renamed} 件リネーム / {skipped} 件スキップ / {failed} 件失敗")
    return DownloadSummary(downloaded, skipped, renamed, failed, watermark, pending, rate_limited)


def download_user_media(username, items, output_dir, *, anonymize=False):
    """既存pendingを引き継ぎ、境界と未解決項目を一つの状態として保存する。"""
    root = str(output_dir.parent)
    state = load_user_state(username, root) or {}

    def checkpoint(watermark, pending):
        save_active_state(username, watermark, pending_downloads=pending, downloads_dir=root)

    return download_all(items, output_dir, state.get('watermark'), anonymize,
                        pending_downloads=state.get('pending_downloads'), checkpoint=checkpoint)
