import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .filename import sanitize_filename
from .settings import DOWNLOADS_DIR, TIMEZONE_OFFSET_HOURS


def find_since_datetime(username: str, downloads_dir: str = DOWNLOADS_DIR):
    """既存フォルダ内のファイル名から差分取得の境界日時を探す。"""
    dl_root = Path(downloads_dir)
    # ファイル名に含まれる [YYYYMMDDhhmmss] を既存ダウンロード日時として扱う
    pattern = re.compile(r"\[(\d{14})\]")
    candidates = []

    for folder in dl_root.iterdir() if dl_root.exists() else []:
        if folder.is_dir() and f"(@{username})" in folder.name:
            for f in folder.iterdir():
                m = pattern.search(f.name)
                if m:
                    try:
                        dt_naive = datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
                        dt_aware = dt_naive.replace(tzinfo=timezone(timedelta(hours=TIMEZONE_OFFSET_HOURS)))
                        candidates.append(dt_aware)
                    except ValueError:
                        pass

    if not candidates:
        return None

    print(f"   候補フォルダ確認: {[f.name for f in dl_root.iterdir() if f.is_dir() and f.name.endswith(f'(@{username})')]}")
    return max(candidates)


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
