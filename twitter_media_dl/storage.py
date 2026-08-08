import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .filename import sanitize_filename
from .settings import DOWNLOADS_DIR, TIMEZONE_OFFSET_HOURS


STATE_DIR_NAME = ".state"
STATE_VERSION = 1


def parse_download_timestamp(timestamp: str):
    """ファイル名や状態ファイルの YYYYMMDDhhmmss を aware datetime に変換する。"""
    try:
        dt_naive = datetime.strptime(timestamp, "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return dt_naive.replace(tzinfo=timezone(timedelta(hours=TIMEZONE_OFFSET_HOURS)))


def format_download_timestamp(dt: datetime) -> str:
    """差分watermark保存用の YYYYMMDDhhmmss を返す。"""
    return dt.astimezone(timezone(timedelta(hours=TIMEZONE_OFFSET_HOURS))).strftime("%Y%m%d%H%M%S")


def get_state_path(username: str, downloads_dir: str = DOWNLOADS_DIR) -> Path:
    """ユーザーごとの差分watermark状態ファイルのパスを返す。"""
    return Path(downloads_dir) / STATE_DIR_NAME / f"{username}.json"


def load_since_datetime(username: str, downloads_dir: str = DOWNLOADS_DIR):
    """状態ファイルに保存された差分取得の境界日時を読む。"""
    state_path = get_state_path(username, downloads_dir)
    if not state_path.exists():
        legacy_dt, matched_folders = find_latest_downloaded_datetime(username, downloads_dir)
        if legacy_dt:
            print(f"   候補フォルダ確認: {matched_folders}")
            print("   状態ファイル未作成のため、安全確認として全件取得します。")
        return None

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise RuntimeError(f"状態ファイルを読み込めません: {state_path} ({e})") from e

    watermark = state.get("watermark")
    if not watermark:
        return None

    since_dt = parse_download_timestamp(watermark)
    if since_dt is None:
        raise RuntimeError(f"状態ファイルのwatermarkが不正です: {state_path}")

    print(f"   状態ファイル確認: {state_path}")
    return since_dt


def save_since_datetime(username: str, watermark: str | None, downloads_dir: str = DOWNLOADS_DIR):
    """連続して処理完了した最新日時を状態ファイルへ保存する。"""
    if not watermark:
        return

    state_path = get_state_path(username, downloads_dir)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "version": STATE_VERSION,
        "username": username,
        "watermark": watermark,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def find_latest_downloaded_datetime(username: str, downloads_dir: str = DOWNLOADS_DIR):
    """既存フォルダ内のファイル名から最新ダウンロード日時を探す。"""
    dl_root = Path(downloads_dir)
    # ファイル名に含まれる [YYYYMMDDhhmmss] を既存ダウンロード日時として扱う
    pattern = re.compile(r"\[(\d{14})\]")
    candidates = []
    matched_folders = []

    for folder in dl_root.iterdir() if dl_root.exists() else []:
        if folder.is_dir() and f"(@{username})" in folder.name:
            matched_folders.append(folder.name)
            for f in folder.iterdir():
                m = pattern.search(f.name)
                if m:
                    dt_aware = parse_download_timestamp(m.group(1))
                    if dt_aware:
                        candidates.append(dt_aware)

    if not candidates:
        return None, matched_folders

    return max(candidates), matched_folders


def prepare_output_dir(user, username: str, downloads_dir: str = DOWNLOADS_DIR) -> Path:
    """保存先ディレクトリを作成する。既存フォルダがあれば当日名へリネームする。"""
    today = datetime.now().strftime("%Y%m%d")
    base_name = sanitize_filename(f"{user.name} (@{username})")
    new_folder_name = f"{base_name} [{today}]"
    new_output_dir = Path(downloads_dir) / new_folder_name

    # 既存フォルダを探してリネーム
    dl_root = Path(downloads_dir)
    existing_dir = None
    if dl_root.exists():
        for folder in dl_root.iterdir():
            if folder.is_dir() and f"(@{username})" in folder.name:
                existing_dir = folder
                break

    if existing_dir and existing_dir != new_output_dir:
        existing_dir.rename(new_output_dir)

    new_output_dir.mkdir(parents=True, exist_ok=True)
    return new_output_dir
