# twitter-media-dl

A Python script to bulk download media (images and videos) from a specified Twitter/X user.

## Features

- Fetches all media from a user's tweets without any count limit
- Saves images in original quality
- Supports videos (mp4)
- Excludes media from retweets (can be included via option)
- Skips already-downloaded files, allowing resumable downloads
- Saves files with filenames containing the author, timestamp, and tweet text

## Filename Format

```
[@author][YYYYMMDDhhmm] tweetContent{index}.ext
```

Examples:
```
[@elonmusk][202603092353] Real picture of @StarbaseTX.jpg
[@elonmusk][202603092353] Multiple images tweet_1.jpg
[@elonmusk][202603092353] Multiple images tweet_2.jpg
[@elonmusk][202603091823] .mp4   ← empty string when tweet has no text
```

## Save Location

```
./downloads/@<username>/
```

Example: specifying `@elonmusk` → `./downloads/@elonmusk/`

## Requirements

- Python 3.10 or higher
- [twitter-cli](https://github.com/jackwener/twitter-cli)

```bash
pip install twitter-cli
```

## Setup

### 1. Place the Script

Put `twitter-media-dl.py` and `config.yaml` in the same folder.

### 2. Configure Credentials

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

> ⚠️ `auth_token` is equivalent to your login credentials. Do not share it with anyone.

## Usage

```bash
# Fetch all media
python twitter-media-dl.py <username>

# Fetch up to a specified count
python twitter-media-dl.py <username> --max 100

# Include media from retweets
python twitter-media-dl.py <username> --include-retweets
```

### Example Output

```
📁 Save location: C:\twitter\downloads\@elonmusk
👤 Fetching profile for @elonmusk...
   ID: 44196397  Tweets: 98,782
📡 Fetching tweets...
   Page 1: 21 fetched (total 21)
   Page 2: 20 fetched (total 41)
   ...
📊 Tweets fetched: 100
🖼️  Media count: 12 (retweets excluded)

[1/12] ⬇️  [@elonmusk][202603092353] Real picture of @StarbaseTX.jpg
[2/12] ⏭️  Skipped: file already exists
...
────────────────────────────────────────────────────────────
✅ Done: 11 downloaded / 1 skipped / 0 failed
```

## Notes

- Cookie-based authentication is used; a 4-second delay between requests is in place to minimize impact on x.com.
- If the script stops working due to Twitter API changes, try updating with `pip install --upgrade twitter-cli`.
- If your cookies expire, re-login in the browser and update the values in `config.yaml`.

## Dependencies

| Package | Purpose |
|---|---|
| twitter-cli | Authentication, communication, and response parsing for the Twitter GraphQL API |
