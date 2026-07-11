"""
指定したTwitter/Xユーザーのメディア（画像・動画）を一括ダウンロードするスクリプト。

使い方:
  python twitter-media-dl.py <username>
  python twitter-media-dl.py <username> --max 100
  python twitter-media-dl.py <username> --include-retweets
"""

from twitter_media_dl.cli import main


if __name__ == "__main__":
    main()
    input("\nPress Enter to exit...")
