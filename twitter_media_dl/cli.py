import argparse
import os
import re
import sys
from pathlib import Path

from .client import TwitterFetchError, fetch_tweets
from .config import load_config
from .debug import write_debug_data
from .downloader import download_all
from .maintenance import scan_downloads, update_all_downloads
from .media import extract_media_items, sort_media_items_for_download
from .storage import (
    format_download_timestamp,
    load_since_datetime,
    prepare_output_dir,
    save_since_datetime,
)
from .throttle import is_rate_limited, wait_after_rate_limit, wait_between_users


USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
LIST_FILE_SUFFIXES = {".txt", ".list", ".csv"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Twitter/X ユーザーのメディアを一括ダウンロードします。")
    parser.add_argument("username", nargs="?", help="ダウンロード対象のユーザーID、またはユーザー一覧ファイル")
    parser.add_argument("--max", type=int, default=None, dest="max_count", help="取得するツイートの最大件数（省略時は全件取得）")
    parser.add_argument("--include-retweets", action="store_true", help="リツイートのメディアも含める（デフォルト: 除外）")
    parser.add_argument("--full", action="store_true", help="差分モードを無視して全件取得する")
    parser.add_argument("--anonymize", action="store_true", help="デバッグ出力のツイート本文をハッシュ化する")
    parser.add_argument("--debug", action="store_true", help="ツイートの生データをJSONに出力してデバッグ（ダウンロードは行わない）")
    parser.add_argument("--scan-downloads", action="store_true", help="downloads配下を一括スキャンし、旧ファイル名のリネームと状態作成だけを行う")
    parser.add_argument("--update-all", action="store_true", help="downloads配下の全ユーザーを一括更新する")
    parser.add_argument("--retry-unavailable", action="store_true", help="取得不能として記録済みのユーザーも一括処理で再確認する")
    return parser


def parse_args(argv: list[str] | None = None):
    if argv is None and len(sys.argv) == 1:
        user_input = input("input UserID: ").strip()
        sys.argv.extend(user_input.split())

    parser = build_parser()
    args = parser.parse_args(argv)
    bulk_modes = [args.scan_downloads, args.update_all]
    bulk_mode = any(bulk_modes)

    if sum(1 for mode in bulk_modes if mode) > 1:
        parser.error("--scan-downloads と --update-all は同時に指定できません。")
    if bulk_mode and args.username:
        parser.error("--scan-downloads / --update-all では username を指定しないでください。")
    if bulk_mode and args.max_count is not None:
        parser.error("--scan-downloads / --update-all では --max を指定できません。")
    if bulk_mode and args.debug:
        parser.error("--scan-downloads / --update-all では --debug を指定できません。")
    if not bulk_mode and args.retry_unavailable:
        parser.error("--retry-unavailable は --scan-downloads / --update-all と一緒に指定してください。")
    if not bulk_mode and not args.username:
        parser.error("username を指定してください。")

    return args


def load_auth():
    config = load_config()
    auth = config.get("auth", {})

    # 認証情報: config.yaml → 環境変数の順で取得
    auth_token = auth.get("auth_token") or os.environ.get("TWITTER_AUTH_TOKEN", "")
    ct0 = auth.get("ct0") or os.environ.get("TWITTER_CT0", "")
    if not auth_token or not ct0:
        print("❌ 認証情報が見つかりません。")
        print("   config.yaml に auth_token と ct0 を記述するか、")
        print("   環境変数 TWITTER_AUTH_TOKEN / TWITTER_CT0 を設定してください。")
        sys.exit(1)
    return auth_token, ct0


def normalize_username(raw_username: str) -> str:
    username = raw_username.strip().lstrip("@")
    if not USERNAME_RE.match(username):
        raise ValueError(f"ユーザーIDが不正です: {raw_username}")
    return username


def looks_like_list_file(target: str) -> bool:
    """位置引数がユーザー一覧ファイル指定かどうかを判定する。"""
    path = Path(target)
    return path.is_file() or path.suffix.lower() in LIST_FILE_SUFFIXES or "/" in target or "\\" in target


def has_user_list_only_options(args) -> bool:
    """一覧ファイル指定では通常ユーザー処理の追加オプションを受け付けない。"""
    return bool(args.max_count is not None or args.include_retweets or args.full or args.anonymize or args.debug)


def load_user_list(path: str) -> list[str]:
    list_path = Path(path)
    try:
        text = list_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = list_path.read_text(encoding="cp932")
        except (OSError, UnicodeDecodeError) as e:
            raise RuntimeError(f"ユーザー一覧ファイルを読み込めません: {list_path} ({e})") from e
    except OSError as e:
        raise RuntimeError(f"ユーザー一覧ファイルを読み込めません: {list_path} ({e})") from e

    usernames = []
    seen = set()
    for line_no, line in enumerate(text.splitlines(), start=1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        for token in re.split(r"[\s,]+", line):
            if not token:
                continue
            try:
                username = normalize_username(token)
            except ValueError as e:
                raise RuntimeError(f"{list_path}:{line_no}: {e}") from e
            if username in seen:
                continue
            usernames.append(username)
            seen.add(username)

    if not usernames:
        raise RuntimeError(f"ユーザー一覧ファイルに有効なユーザーIDがありません: {list_path}")
    return usernames


def download_user(
    username: str,
    *,
    max_count: int | None,
    include_retweets: bool,
    full: bool,
    anonymize: bool,
    debug: bool,
    auth_token: str,
    ct0: str,
) -> tuple[bool, bool]:
    """1ユーザー分の通常ダウンロード処理を行う。戻り値は (成功, rate limit 検出)。"""
    since_dt = None
    if not full:
        try:
            since_dt = load_since_datetime(username)
        except RuntimeError as e:
            print(f"❌ {e}")
            return False, False
        if since_dt:
            print(f"📅 差分モード: {since_dt.strftime('%Y/%m/%d %H:%M')} 以降を取得")
        else:
            print("📅 全件モード: 既存ファイルが見つからないため全件取得")
    else:
        print("📅 全件モード: --full 指定")

    try:
        tweets, user = fetch_tweets(username, max_count, auth_token, ct0, since_dt)
    except TwitterFetchError as e:
        print(f"❌ ツイート取得に失敗しました: {e}")
        print("   差分境界は更新しません。時間を置いて再実行してください。")
        return False, is_rate_limited(e)

    output_dir = prepare_output_dir(user, username)

    print(f"📁 保存先: {output_dir.resolve()}")
    print(f"📊 取得ツイート数: {len(tweets)} 件")

    # デバッグモード: 生データをJSONに出力して終了
    if debug:
        out_path = write_debug_data(tweets, username, anonymize=anonymize)
        print(f"📄 デバッグデータを出力しました: {out_path}")
        return True, False

    # メディア抽出
    items = sort_media_items_for_download(extract_media_items(tweets, include_retweets))
    if not items:
        print("ℹ️  ダウンロード対象のメディアが見つかりませんでした。")
        return True, False

    rt_msg = "（リツイート含む）" if include_retweets else "（リツイート除外）"
    print(f"🖼️  メディア数: {len(items)} 件 {rt_msg}")
    print()

    # ダウンロード
    initial_watermark = format_download_timestamp(since_dt) if since_dt else None
    summary = download_all(items, output_dir, initial_watermark=initial_watermark, anonymize=anonymize)
    save_since_datetime(username, summary.watermark)
    if summary.watermark:
        print(f"📌 差分境界を保存: {summary.watermark}")
    return True, False


def download_user_list(usernames: list[str], auth_token: str, ct0: str):
    total = len(usernames)
    succeeded = 0
    failed = 0

    print(f"ユーザー一覧処理対象: {total} ユーザー")

    for index, username in enumerate(usernames, start=1):
        print()
        print("=" * 60)
        print(f"[{index}/{total}] @{username}")

        success, rate_limited = download_user(
            username,
            max_count=None,
            include_retweets=False,
            full=False,
            anonymize=False,
            debug=False,
            auth_token=auth_token,
            ct0=ct0,
        )
        if success:
            succeeded += 1
        else:
            failed += 1
        if index < total:
            if rate_limited:
                wait_after_rate_limit()
            wait_between_users()

    print()
    print("=" * 60)
    print(f"一覧処理完了: {succeeded} 件成功 / {failed} 件失敗")


def main(argv: list[str] | None = None):
    args = parse_args(argv)
    username = None
    usernames = None

    if not args.scan_downloads and not args.update_all:
        if looks_like_list_file(args.username):
            if has_user_list_only_options(args):
                print("[ERROR] ユーザー一覧ファイル指定では追加オプションを指定できません。")
                sys.exit(1)
            try:
                usernames = load_user_list(args.username)
            except RuntimeError as e:
                print(f"[ERROR] {e}")
                sys.exit(1)
        else:
            try:
                username = normalize_username(args.username)
            except ValueError as e:
                print(f"❌ {e}")
                sys.exit(1)

    auth_token, ct0 = load_auth()

    if args.scan_downloads:
        scan_downloads(
            auth_token,
            ct0,
            include_retweets=args.include_retweets,
            anonymize=args.anonymize,
            retry_unavailable=args.retry_unavailable,
        )
        return
    if args.update_all:
        update_all_downloads(
            auth_token,
            ct0,
            include_retweets=args.include_retweets,
            full=args.full,
            anonymize=args.anonymize,
            retry_unavailable=args.retry_unavailable,
        )
        return

    if usernames is not None:
        download_user_list(usernames, auth_token, ct0)
        return

    success, _rate_limited = download_user(
        username,
        max_count=args.max_count,
        include_retweets=args.include_retweets,
        full=args.full,
        anonymize=args.anonymize,
        debug=args.debug,
        auth_token=auth_token,
        ct0=ct0,
    )
    if not success:
        sys.exit(1)
