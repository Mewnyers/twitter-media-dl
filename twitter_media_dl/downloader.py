import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

from .filename import anonymize_filename
from .settings import REQUEST_INTERVAL


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
