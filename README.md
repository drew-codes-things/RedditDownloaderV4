# starbottyyay

A Reddit app (built on [Devvit](https://developers.reddit.com/)) that scans a subreddit for media posts and downloads the matching images, galleries, and videos to your own machine — locally, with nothing kept on a third-party server.

## Why this needs three separate pieces

Devvit apps run in a sandboxed environment. Both the server-side code and the client-side webview are blocked from making network requests to arbitrary domains — only a small, Reddit-controlled allowlist is permitted (things like `discord.com` and `api.telegram.org`), and personal/self-hosted domains are never approved, even if you list them in `devvit.json`. This was confirmed directly: a real Cloudflare Tunnel domain and a `trycloudflare.com` quick tunnel were both tried and rejected with `PERMISSION_DENIED`, and the client-side webview's CSP blocks `connect-src` to non-allowlisted domains outright.

So this app can't call your machine directly. Instead:

1. **The Devvit app** scans the subreddit and posts the media links it finds to a **Discord webhook** (`discord.com` is on Reddit's allowlist).
2. **`discord-poller`** — a small script that runs on your own machine, completely outside Devvit's sandbox — polls that Discord channel with a bot token, reads the links back out, and forwards them to `reddit-relay`.
3. **`reddit-relay`** — a small local Flask service on your machine that takes those links and actually downloads the media with [yt-dlp](https://github.com/yt-dlp/yt-dlp).

```
Reddit posts  →  Devvit app  →  Discord webhook  →  discord-poller  →  reddit-relay  →  yt-dlp  →  disk
              (sandboxed)     (Reddit's allowlist)      (your machine, unsandboxed)
```

## Repo layout

```
devvit-app/       the Reddit app itself (TypeScript, Devvit Web)
reddit-relay/      local Flask + yt-dlp download service
discord-poller/    local script that reads Discord and feeds reddit-relay
systemd/           example systemd --user unit files for the two local services
```

## Prerequisites

- Node.js ≥ 24 and npm
- A Reddit account with [Reddit Developers](https://developers.reddit.com/) access, and the [Devvit CLI](https://developers.reddit.com/docs/quickstart) (`devvit login`)
- Python 3.11+ and `venv`
- **ffmpeg** — required for merging Reddit's separate video/audio streams. Install it via your system package manager, e.g. `sudo apt-get install -y ffmpeg` on Debian/Ubuntu.
- A Discord server you control, to host the webhook and bot

## Setup

### 1. Discord webhook and bot

You need both a webhook (for the app to *post* links) and a bot (for the poller to *read* them back).

**Webhook:**
1. In your Discord server, pick or create a channel for this.
2. Channel Settings → Integrations → Webhooks → New Webhook. Copy its URL (`https://discord.com/api/webhooks/{id}/{token}`).

**Bot:**
1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) → New Application.
2. Bot tab → Reset Token, copy it somewhere safe.
3. **Bot tab → Privileged Gateway Intents → enable "Message Content Intent".** Without this, the bot can technically read message metadata but the `content` field always comes back empty — this is a Discord API restriction, not a bug in this code, and it's easy to miss since everything *looks* like it's working except no content ever arrives.
4. OAuth2 → URL Generator → scope `bot`, permission `Read Message History` → open the generated URL and invite the bot to your server.
5. Get the channel ID: enable Developer Mode in Discord (User Settings → Advanced), then right-click the channel → Copy Channel ID.

### 2. Devvit app

```
cd devvit-app
npm install
devvit login
```

Edit `devvit.json` if you want a different app name, then set the webhook URL as an app setting (this prompts interactively — the value never needs to be typed anywhere else):

```
devvit settings set discordWebhookUrl
```

Run it against a test subreddit:

```
npm run playtest
```

Open the subreddit, use the subreddit menu (`...`) → "Fetch Media to Server" to trigger a fetch.

### 3. reddit-relay (local download service)

```
cd reddit-relay
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:
- `RELAY_API_TOKEN` — make up a long random string; this authenticates requests from `discord-poller`
- `DOWNLOAD_DIR` — where downloaded media should land

Run it directly to test:

```
./venv/bin/python app.py
```

Or install it as a systemd user service so it survives reboots (see `systemd/reddit-relay.service` — copy it to `~/.config/systemd/user/`, then `systemctl --user daemon-reload && systemctl --user enable --now reddit-relay.service`). To have it keep running after you log out, also run `loginctl enable-linger $USER` once.

### 4. discord-poller

```
cd discord-poller
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with your bot's `BOT_TOKEN` and the `CHANNEL_ID` from step 1.

Run it directly to test:

```
./venv/bin/python poller.py
```

Or install `systemd/discord-poller.service` the same way as above.

## How it works

- `devvit-app/src/server/media.ts` fetches posts from the target subreddit via Devvit's already-authenticated Reddit API and extracts a media URL from each: for Reddit-hosted video it uses the post's `hlsUrl`/`dashUrl`/`fallbackUrl` directly (not the post permalink — Reddit's public `.json` scrape API used by generic download tools is blocked outright for unauthenticated requests, so getting the real media URL from Devvit's own authenticated API sidesteps that entirely); for galleries and direct image/file links it uses those URLs as-is.
- `devvit-app/src/server/relay.ts` batches those links into JSON, prefixes each batch with `RELAY:`, and posts them as Discord messages via the webhook (chunked to stay under Discord's 2000-character message limit, with retry-on-429 for Discord's rate limit).
- `discord-poller/poller.py` polls `GET /channels/{id}/messages` with the bot token, looks for `RELAY:`-prefixed messages, parses the JSON, and `POST`s the items to `reddit-relay`'s `/jobs` endpoint. It tracks the last message ID it processed in `last_message_id.txt` so restarts don't reprocess old messages.
- `reddit-relay/app.py` is a small bearer-token-authed Flask API; `/jobs` queues each URL, `worker.py` runs the actual downloads through yt-dlp in a thread pool.
- `worker.py` sends `Accept: */*` specifically for Reddit's direct-image CDN hosts (`preview.redd.it`, `i.redd.it`) — by default, Reddit's CDN does content negotiation on the `Accept` header and 307-redirects a browser-like request to a JS-only viewer page instead of serving the raw file. Video/audio hosts don't need this.
- Output filenames are prefixed with the Reddit post ID. This matters specifically for video: every Reddit video's HLS manifest is named identically (`HLSPlaylist.m3u8`), so without a unique prefix, downloading a second video into the same subreddit folder would silently overwrite (or get skipped in place of) the first.

## Security notes

- Every secret (`RELAY_API_TOKEN`, `BOT_TOKEN`, the webhook URL) lives in a gitignored `.env` file — never commit these, and use `.env.example` as the template when setting up a fresh clone.
- If a token or webhook URL is ever pasted into a chat log, terminal history, or committed by mistake, rotate it — webhook URLs can be regenerated from the channel's Integrations settings, and bot tokens from the Developer Portal's Bot tab.
- `reddit-relay`'s Flask server is the built-in development server, not hardened for production — this is intended for personal, local use behind your own machine, not as an internet-facing service.

## License

MIT — see [LICENSE](LICENSE). `devvit-app/LICENSE` is kept separately as the BSD-3-Clause license from the Devvit bare template this app started from, attributed to Reddit Inc. for the template portions it still contains.
