import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .filename import sanitize_filename
from .settings import DOWNLOADS_DIR, TIMEZONE_OFFSET_HOURS


STATE_DIR_NAME = ".state"
STATE_VERSION = 2
STATE_STATUS_ACTIVE = "active"
STATE_STATUS_UNAVAILABLE = "unavailable"
DOWNLOAD_USER_RE = re.compile(r"\(@([^)]+)\)")


class StateSaveError(RuntimeError):
    """保存できなかった未解決項目も最終レポートへ渡す。"""

    def __init__(self, message, pending_downloads):
        super().__init__(message)
        self.pending_downloads = dict(pending_downloads)


def parse_download_timestamp(timestamp: str):
    """ファイル名や状態ファイルの YYYYMMDDhhmmss を aware datetime に変換する。"""
    try:
        dt_naive = datetime.strptime(timestamp, "%Y%m%d%H%M%S")
    except (ValueError, TypeError):
        return None
    return dt_naive.replace(tzinfo=timezone(timedelta(hours=TIMEZONE_OFFSET_HOURS)))


def get_state_path(username: str, downloads_dir: str = DOWNLOADS_DIR) -> Path:
    """ユーザーごとの差分watermark状態ファイルのパスを返す。"""
    return Path(downloads_dir) / STATE_DIR_NAME / f"{username}.json"


def load_user_state(username: str, downloads_dir: str = DOWNLOADS_DIR):
    """ユーザーごとの状態ファイルを読み込む。存在しない場合は None を返す。"""
    state_path = get_state_path(username, downloads_dir)
    if not state_path.exists():
        return None

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or state.get("version", 1) not in (1, 2):
            raise ValueError("unsupported state format")
        if state.get("watermark") and not parse_download_timestamp(state["watermark"]):
            raise ValueError("invalid watermark")
        pending = state.get("pending_downloads", {})
        if not isinstance(pending, dict):
            raise ValueError("invalid pending_downloads")
        for key, item in pending.items():
            if not isinstance(item, dict) or key != media_key(item):
                raise ValueError("invalid pending media identity")
            for name in [item.get("filename", ""), *item.get("legacy_filenames", [])]:
                if not name or Path(name).name != name or name in (".", ".."):
                    raise ValueError("invalid pending filename")
            if not isinstance(item.get("url"), str) or not isinstance(item.get("attempts", 0), int):
                raise ValueError("invalid pending media")
            if not parse_download_timestamp(item.get("created_at_sort", "")):
                raise ValueError("invalid pending timestamp")
        return state
    except (OSError, ValueError, TypeError, KeyError) as e:
        raise RuntimeError(f"状態ファイルを読み込めません: {state_path} ({e})") from e


def write_user_state(username: str, state: dict, downloads_dir: str = DOWNLOADS_DIR):
    """境界と未解決項目を一緒に置換し、書き込み途中の状態を公開しない。"""
    state_path = get_state_path(username, downloads_dir)
    temporary = None
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=state_path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, state_path)
    except OSError as e:
        raise StateSaveError(f"状態ファイルを保存できません: {state_path} ({e})",
                             state.get('pending_downloads', {})) from e
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def media_key(item: dict) -> str:
    """ファイル名変更に影響されないメディア識別子。"""
    return f"{item['tweet_id']}:{item['media_index']}"


def build_base_state(username: str, status: str):
    """状態ファイルで共通して使うメタ情報を作る。"""
    return {
        "version": STATE_VERSION,
        "username": username,
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def save_active_state(
    username: str,
    watermark: str | None = None,
    *,
    folders: list[str] | None = None,
    pending_downloads: dict | None = None,
    downloads_dir: str = DOWNLOADS_DIR,
):
    """取得可能ユーザーの状態を保存する。"""
    state = load_user_state(username, downloads_dir) or {}
    state.update(build_base_state(username, STATE_STATUS_ACTIVE))
    state.pop("reason", None)
    if watermark:
        state["watermark"] = watermark
    if folders:
        state["folders"] = folders
    if pending_downloads is not None:
        state["pending_downloads"] = pending_downloads
    write_user_state(username, state, downloads_dir)


def load_since_datetime(username: str, downloads_dir: str = DOWNLOADS_DIR):
    """状態ファイルに保存された差分取得の境界日時を読む。"""
    state_path = get_state_path(username, downloads_dir)
    if not state_path.exists():
        legacy_dt, matched_folders = find_latest_downloaded_datetime(username, downloads_dir)
        if legacy_dt:
            print(f"   候補フォルダ確認: {matched_folders}")
            print("   状態ファイル未作成のため、安全確認として全件取得します。")
        return None

    state = load_user_state(username, downloads_dir)
    status = state.get("status", STATE_STATUS_ACTIVE)
    if status == STATE_STATUS_UNAVAILABLE:
        print(f"   状態ファイル確認: {state_path}")
        print("   前回はユーザー取得不能として記録されています。再確認のため全件取得します。")
        return None

    watermark = state.get("watermark")
    if not watermark:
        return None

    since_dt = parse_download_timestamp(watermark)
    if since_dt is None:
        raise RuntimeError(f"状態ファイルのwatermarkが不正です: {state_path}")

    print(f"   状態ファイル確認: {state_path}")
    return since_dt


def save_unavailable_state(
    username: str,
    reason: str,
    *,
    watermark: str | None = None,
    folders: list[str] | None = None,
    downloads_dir: str = DOWNLOADS_DIR,
):
    """取得不能ユーザーの状態を、既存ファイルを残したまま記録する。"""
    state = load_user_state(username, downloads_dir) or {}
    state.update(build_base_state(username, STATE_STATUS_UNAVAILABLE))
    state["reason"] = reason
    if watermark:
        state["watermark"] = watermark
    if folders:
        state["folders"] = folders
    write_user_state(username, state, downloads_dir)


def is_unavailable_state(username: str, downloads_dir: str = DOWNLOADS_DIR) -> bool:
    """状態ファイル上で取得不能ユーザーとして記録されているかを返す。"""
    state = load_user_state(username, downloads_dir)
    if not state:
        return False
    return state.get("status") == STATE_STATUS_UNAVAILABLE


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


def find_downloaded_user_folders(downloads_dir: str = DOWNLOADS_DIR):
    """downloads 配下のユーザー別フォルダを username ごとに集める。"""
    dl_root = Path(downloads_dir)
    users = {}
    if not dl_root.exists():
        return users

    for folder in dl_root.iterdir():
        if not folder.is_dir() or folder.name == STATE_DIR_NAME:
            continue
        m = DOWNLOAD_USER_RE.search(folder.name)
        if not m:
            continue
        users.setdefault(m.group(1), []).append(folder)

    return dict(sorted(users.items()))


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
