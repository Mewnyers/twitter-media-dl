import argparse
import os
import sys

from .client import fetch_all_tweets_by_UserMedia, fetch_all_tweets_by_UserTweets
from .config import load_config
from .debug import write_debug_data
from .downloader import download_all
from .media import extract_media_items, sort_media_items_for_download
from .storage import find_since_datetime, prepare_output_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Twitter/X ユーザーのメディアを一括ダウンロードします。")
    parser.add_argument("username", help="ダウンロード対象のユーザーID（@なし）")
    parser.add_argument("--max", type=int, default=None, dest="max_count", help="取得するツイートの最大件数（省略時は全件取得）")
    parser.add_argument("--include-retweets", action="store_true", help="リツイートのメディアも含める（デフォルト: 除外）")
    parser.add_argument("--full", action="store_true", help="差分モードを無視して全件取得する")
    parser.add_argument("--anonymize", action="store_true", help="デバッグ出力のツイート本文をハッシュ化する")
    parser.add_argument("--debug", action="store_true", help="ツイートの生データをJSONに出力してデバッグ（ダウンロードは行わない）")
    return parser


def parse_args(argv: list[str] | None = None):
    if argv is None and len(sys.argv) == 1:
        user_input = input("input UserID: ").strip()
        sys.argv.extend(user_input.split())
    return build_parser().parse_args(argv)


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


def fetch_tweets(username: str, max_count: int | None, auth_token: str, ct0: str, since_dt):
    if since_dt is None:
        # 初回: UserTweets（全件）→ UserMedia（全件）で完全取得
        tweets_usertweets, user = fetch_all_tweets_by_UserTweets(username, max_count, auth_token, ct0)
        tweets_usermedia, _ = fetch_all_tweets_by_UserMedia(username, max_count, auth_token, ct0)

        # 重複除去して合算
        seen_ids = {t.id for t in tweets_usertweets}
        for tweet in tweets_usermedia:
            if tweet.id not in seen_ids:
                tweets_usertweets.append(tweet)
                seen_ids.add(tweet.id)
        return tweets_usertweets, user

    # 2回目以降: UserMedia（差分）のみ
    return fetch_all_tweets_by_UserMedia(username, max_count, auth_token, ct0, since_dt)


def main(argv: list[str] | None = None):
    args = parse_args(argv)
    username = args.username.lstrip("@")
    auth_token, ct0 = load_auth()

    # 差分モード: 既存フォルダのファイル名から最新日時を取得
    since_dt = None
    if not args.full:
        since_dt = find_since_datetime(username)
        if since_dt:
            print(f"📅 差分モード: {since_dt.strftime('%Y/%m/%d %H:%M')} 以降を取得")
        else:
            print("📅 全件モード: 既存ファイルが見つからないため全件取得")

    tweets, user = fetch_tweets(username, args.max_count, auth_token, ct0, since_dt)
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
    download_all(items, output_dir, anonymize=args.anonymize)
