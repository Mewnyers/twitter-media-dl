from dataclasses import dataclass
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

from .filename import anonymize_filename
from .settings import REQUEST_INTERVAL


@dataclass
class DownloadSummary:
    downloaded: int
    skipped: int
    renamed: int
    failed: int
    watermark: str | None


def create_ssl_context():
    """certifi が利用できる場合は、更新されたCAバンドルをHTTPS検証に使う。"""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


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
        with urllib.request.urlopen(req, timeout=30, context=create_ssl_context()) as response:
            data = response.read()
        dest_path.write_bytes(data)
        return True
    except urllib.error.HTTPError as e:
        print(f"    ⚠️  HTTP {e.code}: {url}")
        return False
    except urllib.error.URLError as e:
        print(f"    ⚠️  ダウンロード失敗: {e}")
        if isinstance(e.reason, ssl.SSLError):
            print("       HTTPS証明書エラーです。`pip install -r requirements.txt` で certifi を導入してください。")
        return False
    except Exception as e:
        print(f"    ⚠️  ダウンロード失敗: {e}")
        return False


def get_item_watermark(item: dict) -> str | None:
    """watermark更新に使うメディア日時を返す。"""
    return item.get("created_at_sort")


def rename_legacy_file(output_dir: Path, item: dict) -> bool:
    """旧ルールのファイル名が存在する場合、新ルールのファイル名へ移行する。"""
    legacy_filename = item.get("legacy_filename")
    if not legacy_filename:
        return False

    legacy_dest = output_dir / legacy_filename
    dest = output_dir / item["filename"]
    if not legacy_dest.exists() or dest.exists():
        return False

    legacy_dest.rename(dest)
    return True


def download_all(items: list[dict], output_dir: Path, initial_watermark: str | None = None, anonymize: bool = False):
    """全メディアをダウンロードする。"""
    total = len(items)
    skipped = 0
    renamed = 0
    downloaded = 0
    failed = 0
    watermark = initial_watermark
    blocked_by_failure = False

    for idx, item in enumerate(items, start=1):
        filename = item["filename"]
        url = item["url"]
        dest = output_dir / filename
        display_name = anonymize_filename(filename) if anonymize else filename

        prefix = f"[{idx}/{total}]"

        if dest.exists():
            print(f"{prefix} ⏭️  スキップ: {display_name}")
            skipped += 1
            if not blocked_by_failure:
                watermark = get_item_watermark(item) or watermark
            continue

        try:
            legacy_renamed = rename_legacy_file(output_dir, item)
        except OSError as e:
            print(f"{prefix} ⚠️  旧ファイル名のリネーム失敗: {e}")
            failed += 1
            blocked_by_failure = True
            time.sleep(REQUEST_INTERVAL)
            continue

        if legacy_renamed:
            print(f"{prefix} 🔁  リネーム: {display_name}")
            renamed += 1
            if not blocked_by_failure:
                watermark = get_item_watermark(item) or watermark
            continue

        print(f"{prefix} ⬇️  {display_name}")
        success = download_file(url, dest)

        if success:
            downloaded += 1
            if not blocked_by_failure:
                watermark = get_item_watermark(item) or watermark
        else:
            failed += 1
            blocked_by_failure = True

        time.sleep(REQUEST_INTERVAL)

    print()
    print("─" * 60)
    print(f"✅ 完了: {downloaded} 件ダウンロード / {renamed} 件リネーム / {skipped} 件スキップ / {failed} 件失敗")
    if failed:
        print("⚠️  失敗があるため、差分境界は失敗前の日時までしか進めません。")

    return DownloadSummary(downloaded=downloaded, skipped=skipped, renamed=renamed, failed=failed, watermark=watermark)
