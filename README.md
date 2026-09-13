# twitter-media-dl

A Python script to bulk download media (images and videos) from a specified Twitter/X user.

## Features

- Fetches media from a user's tweets without a fixed count limit
- Combines `UserTweets` and `UserMedia` on the first full run to improve coverage
- Uses `UserMedia` for later differential runs
- Saves images in original quality
- Supports videos (mp4)
- Excludes media from retweets by default
- Allows retweet media via `--include-retweets`
- Skips already-downloaded files
- Downloads older media first so interrupted differential runs do not skip unfinished older files
- Saves filenames with the author, tweet timestamp, and tweet text
- Can scan existing downloaded folders to rename old filenames and rebuild state files
- Can update all users already present under `downloads/`
- Can write debug JSON without downloading media

## Project Structure

```text
twitter-media-dl.py       # Compatibility entry point
twitter_media_dl/
  cli.py                  # CLI and main workflow
  client.py               # Twitter/X API access through twitter-cli
  media.py                # Media extraction and download ordering
  downloader.py           # File download logic
  maintenance.py          # Batch scan/update maintenance workflows
  storage.py              # Differential mode and output directory handling
  filename.py             # Filename formatting and sanitizing
  config.py               # config.yaml loading
  debug.py                # Debug JSON output
  settings.py             # Shared constants
```

The existing command remains the same:

```bash
python twitter-media-dl.py <username>
```

## Filename Format

```text
[@author][YYYYMMDDhhmmss] tweetContent{index}.ext
```

Examples:

```text
[@elonmusk][20260309235300] Real picture of @StarbaseTX.jpg
[@elonmusk][20260309235300] Multiple images tweet_1.jpg
[@elonmusk][20260309235300] Multiple images tweet_2.jpg
[@elonmusk][20260309182300] .mp4
```

The timestamp is converted to JST by default.

Very long filenames are shortened with a short hash suffix while keeping the extension.

Older filenames created before filename sanitizing rules changed are detected during normal processing. If an old filename exists and the new filename does not, the file is renamed instead of downloaded again.

## Save Location

Downloaded media is saved under `downloads/` in a folder based on the user's display name, username, and the current date.

```text
downloads/<display name> (@<username>) [YYYYMMDD]/
```

Example:

```text
downloads/Elon Musk (@elonmusk) [20260712]/
```

If a matching folder for the same username already exists, it is renamed to the current folder name and reused.

## Requirements

- Python 3.10 or higher
- [twitter-cli](https://github.com/jackwener/twitter-cli)
- [certifi](https://pypi.org/project/certifi/)

```bash
pip install -r requirements.txt
```

`PyYAML` is optional. If it is not installed, the script uses a small fallback parser for the `auth` section of `config.yaml`.

## Setup

Place `twitter-media-dl.py`, the `twitter_media_dl/` folder, and `config.yaml` in the same project folder.

Open the developer tools in a browser where you are logged in to x.com, and copy the values of the following cookies:

- `auth_token`
- `ct0`

Enter them in `config.yaml`:

```yaml
auth:
  auth_token: "paste your auth_token value here"
  ct0: "paste your ct0 value here"
```

You can also specify them via environment variables:

```cmd
set TWITTER_AUTH_TOKEN=your_auth_token
set TWITTER_CT0=your_ct0
```

> `auth_token` is equivalent to your login credentials. Do not share it with anyone.

## Usage

```bash
# Fetch media
python twitter-media-dl.py <username>

# Fetch up to a specified tweet count
python twitter-media-dl.py <username> --max 100

# Include media from retweets
python twitter-media-dl.py <username> --include-retweets

# Ignore differential mode and fetch from the beginning
python twitter-media-dl.py <username> --full

# Write tweet debug data to JSON and skip downloads
python twitter-media-dl.py <username> --debug

# Hash tweet text in debug output and download logs
python twitter-media-dl.py <username> --anonymize

# Scan all existing downloads, rename old filenames, and rebuild state files
python twitter-media-dl.py --scan-downloads

# Update every user folder found under downloads/
python twitter-media-dl.py --update-all
```

If the script is launched without arguments, it asks for input interactively:

```text
input UserID:
```

## Differential Mode

Differential mode uses a per-user state file under `downloads/.state/`.

The state file stores the newest timestamp that has been processed continuously without an earlier download failure. Later runs fetch media since that saved timestamp.

Timestamps use this format:

```text
[YYYYMMDDhhmmss]
```

If a media download fails, the differential boundary is only advanced up to the item before the first failure. This prevents a later successful download from hiding an older failed media item on the next run.

Downloads are sorted oldest first. This prevents a partially interrupted run from saving the newest file first and causing the next run to treat older unfinished files as already covered by the differential boundary.

When no state file exists yet, the script performs a full scan once and creates the state file after download processing. Existing files are still skipped.

Use `--full` to ignore this behavior and fetch from the beginning.

## Batch Maintenance

`--scan-downloads` scans user folders under `downloads/` whose folder name contains `(@username)`.

It fetches the user's current tweet metadata, checks already-downloaded media files, renames files created with older filename rules, and writes per-user state files. It does not download missing media. If a media file is missing, the saved differential boundary stops before that gap so later runs do not skip it.

If a user cannot be fetched because the account was deleted, suspended, renamed, or is otherwise unavailable, the script records that user as `unavailable` under `downloads/.state/` and continues with the next user.

Use `--update-all` when you want to scan the same set of user folders and download missing or newer media as well. `--full` can be combined with `--update-all` to ignore existing differential boundaries for every user.

## Example Output

```text
📅 差分モード: 2026/03/09 23:53 以降を取得
👤 @elonmusk のプロフィールを取得中...(UserMedia)
   ID: 44196397  ツイート数: 98,782
📡 ツイートを取得中...
   ページ 1: 21 件取得（累計 21 件）
📁 保存先: C:\twitter\downloads\Elon Musk (@elonmusk) [20260712]
📊 取得ツイート数: 21 件
🖼️  メディア数: 12 件 （リツイート除外）

[1/12] ⬇️  [@elonmusk][20260309235300] Real picture of @StarbaseTX.jpg
[2/12] ⏭️  スキップ: [@elonmusk][20260309235400] Already downloaded.jpg

────────────────────────────────────────────────────────────
✅ 完了: 11 件ダウンロード / 1 件スキップ / 0 件失敗
```

## Notes

- Cookie-based authentication is used.
- Pagination requests wait 1.5 seconds between pages.
- Media downloads wait 0.5 seconds between files.
- HTTPS media downloads use `certifi`'s CA bundle when available.
- If the script stops working due to Twitter/X API changes, try updating with `pip install --upgrade twitter-cli`.
- If your cookies expire, re-login in the browser and update `config.yaml` or the environment variables.

## Dependencies

| Package | Purpose |
|---|---|
| twitter-cli | Authentication, communication, and response parsing for the Twitter GraphQL API |
| certifi | CA certificate bundle for HTTPS media downloads |
