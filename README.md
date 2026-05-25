# PWThor Telegram Downloader Bot

Production-oriented Telegram worker bot for authenticated `pwthor.live` downloads on Northflank.

## Features

- Python 3.11 only
- Pyrogram bot architecture
- FFmpeg-based media download/remux
- PWThor phone + OTP login using the live `/api/auth/login` and `/api/auth/verify-otp` endpoints
- Isolated encrypted session file per Telegram user
- Per-user download queue with one active download at a time
- Cancel command, progress updates, retries, timeouts, URL validation, rate limiting, and forced temp cleanup
- Docker and Northflank worker deployment files

## Repository Layout

```text
app/
bot/
config/
utils/
downloads/
sessions/
```

`downloads/` and `sessions/` are runtime directories. Their contents are ignored by Git.

## Environment

Copy `.env.example` to `.env` locally and set:

```env
BOT_TOKEN=
API_ID=
API_HASH=
```

Recommended optional values:

```env
SESSION_SECRET=
ADMIN_CHAT_ID=
MAX_DOWNLOAD_MB=450
MAX_GLOBAL_DOWNLOADS=1
ALLOWED_SOURCE_HOSTS=*
ALLOWED_MEDIA_HOSTS=*
```

Never commit `.env`, Pyrogram session files, encrypted user sessions, or downloaded media.

## Bot Workflow

1. User sends a public HTTPS video page or direct media link.
2. Public/non-login links are queued immediately.
3. If a PWThor link needs login, the bot asks for the PWThor phone number.
4. Bot requests OTP from PWThor.
5. User replies with OTP.
6. Bot stores an encrypted session as `sessions/<telegram_user_id>.json.enc`.
7. Bot queues the pending PWThor link automatically.
8. Bot resolves the media URL, downloads with FFmpeg, uploads to Telegram, and deletes the temp file.

Commands:

- `/start` login or show ready state
- `/status` show login status
- `/cancel` cancel current and queued downloads
- `/logout` delete encrypted PWThor session

## Local Run

```bash
docker build -t pwthor-downloader .
docker run --env-file .env --rm pwthor-downloader
```

Or without Docker:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m app.main
```

## Northflank Deployment

Create a Northflank service from this repository as a Docker worker service with no public ports.

Set these runtime environment variables in Northflank:

- `BOT_TOKEN`
- `API_ID`
- `API_HASH`

The bot uses ephemeral storage safely by keeping downloads size-limited, deleting files after upload, and cleaning old temp/session files in the background.

## Security Notes

- Secrets are only read from environment variables.
- User sessions are encrypted before disk storage.
- Session files are isolated by Telegram user ID.
- Links are restricted to public HTTPS hosts. Configure `ALLOWED_SOURCE_HOSTS` to narrow sources.
- Extracted media is restricted to `ALLOWED_MEDIA_HOSTS`.
- Path traversal in user-provided URLs is rejected.
- DRM-encrypted streams are not bypassed.

## CI

GitHub Actions builds the Docker image and runs syntax compilation on Python 3.11.
