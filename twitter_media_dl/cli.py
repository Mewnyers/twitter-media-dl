import argparse
import os
import sys

from .client import TwitterFetchError, fetch_tweets
from .config import load_config
from .debug import write_debug_data
from .downloader import download_all
from .maintenance import scan_downloads, update_all_downloads
from .media import extract_media_items, sort_media_items_for_download
from .storage import format_download_timestamp, load_since_datetime, prepare_output_dir, save_since_datetime


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Twitter/X ユーザーのメディアを一括ダウンロードします。")
    parser.add_argument("username", nargs="?", help="ダウンロード対象のユーザーID（@なし）")
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
    bulk_mode = args.scan_downloads or args.update_all

    if args.scan_downloads and args.update_all:
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


def main(argv: list[str] | None = None):
    args = parse_args(argv)
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

    username = args.username.lstrip("@")

    # 差分モード: 既存フォルダのファイル名から最新日時を取得
    since_dt = None
    if not args.full:
        try:
            since_dt = load_since_datetime(username)
        except RuntimeError as e:
            print(f"❌ {e}")
            sys.exit(1)
        if since_dt:
            print(f"📅 差分モード: {since_dt.strftime('%Y/%m/%d %H:%M')} 以降を取得")
        else:
            print("📅 全件モード: 既存ファイルが見つからないため全件取得")

    try:
        tweets, user = fetch_tweets(username, args.max_count, auth_token, ct0, since_dt)
    except TwitterFetchError as e:
        print(f"❌ ツイート取得に失敗しました: {e}")
        print("   差分境界は更新しません。時間を置いて再実行してください。")
        sys.exit(1)

    output_dir = prepare_output_dir(user, username)

    print(f"📁 保存先: {output_dir.resolve()}")
    print(f"📊 取得ツイート数: {len(tweets)} 件")

    # デバッグモード: 生データをJSONに出力して終了
    if args.debug:
        out_path = write_debug_data(tweets, username, anonymize=args.anonymize)
        print(f"📄 デバッグデータを出力しました: {out_path}")
        return

    # メディア抽出
    items = sort_media_items_for_download(extract_media_items(tweets, args.include_retweets))
    if not items:
        print("ℹ️  ダウンロード対象のメディアが見つかりませんでした。")
        return

    rt_msg = "（リツイート含む）" if args.include_retweets else "（リツイート除外）"
    print(f"🖼️  メディア数: {len(items)} 件 {rt_msg}")
    print()

    # ダウンロード
    initial_watermark = format_download_timestamp(since_dt) if since_dt else None
    summary = download_all(items, output_dir, initial_watermark=initial_watermark, anonymize=args.anonymize)
    save_since_datetime(username, summary.watermark)
    if summary.watermark:
        print(f"📌 差分境界を保存: {summary.watermark}")
