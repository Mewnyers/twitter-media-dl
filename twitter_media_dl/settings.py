import re


CONFIG_FILE = "config.yaml"
DOWNLOADS_DIR = "downloads"

DATE_PATTERN = "%Y%m%d%H%M%S"  # YYYYMMDDhhmmss
TWEET_CONTENT_MAX_LEN = 200  # ファイル名内のツイート本文の最大文字数
FILENAME_MAX_LEN = 180  # 保存先フォルダ分の余裕を残したファイル名全体の最大文字数
REQUEST_INTERVAL = 0.5  # メディアダウンロード間隔（秒）
TIMEZONE_OFFSET_HOURS = 9  # 0=UTC, 9=JST

BATCH_SIZE = 40  # 1リクエストあたりの取得件数
REQUEST_DELAY = 3.0  # APIページネーション間のウェイト（秒）
ENDPOINT_DELAY = 5.0  # 初回取得で UserTweets と UserMedia を連続実行する間のウェイト（秒）
USER_DELAY = 60.0  # 全件取得を伴う複数ユーザー処理のユーザー間ウェイト（秒）
UPDATE_ALL_USER_DELAY = 5.0  # 差分一括更新のユーザー間ウェイト（秒）

# Windowsで使えないファイル名文字
INVALID_CHARS_RE = re.compile(r'[\\/:*?"<>|\r\n\t]')
