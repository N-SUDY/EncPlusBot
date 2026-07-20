# EncPlusBot — TheMisfitDK Fork

A fast, clean Telegram video encoding bot built on FFmpeg and HandBrake.

**Forked from:** [Nubuki-all/Enc](https://github.com/Nubuki-all/Enc) and [Danish_00/compressorqueue](https://github.com/1Danish-00/compressorqueue)  
**Maintained by:** [TheMisfitDK](https://github.com/TheMisfitDK)

---

## What's changed in this fork
- 🔕 **Auto-encode on file send is OFF by default** — use `/add` to queue manually
  - Set `AUTO_ENCODE=True` in `.env`, or toggle live with `/autoencode on|off`
- 🖼️ **Auto-thumbnail toggle** — `/autothumb on|off` controls whether the bot
  grabs a video frame as thumbnail on upload (skip for lower CPU/RAM use)
- 🗑️ **`/delthumb`** — remove the saved custom thumbnail
- 📋 **Cleaner command menu** — reorganised by category, easier to scan
- 🪶 **Memory-tuned for low-RAM hosts (eg.,Heroku 512MB)** — qBittorrent is removed, capped log rotation, ffmpeg memory-safe defaults
- 🔗 **Updated attribution** — properly credits original authors

---

## Quick Start

### 1. Clone
```bash
git clone https://github.com/TheMisfitDK/EncPlusBot
cd EncPlusBot
```

### 2. Configure
```bash
cp env.sample .env
nano .env
```

**Required vars:**
```
APP_ID=        # from https://my.telegram.org
API_HASH=      # from https://my.telegram.org
BOT_TOKEN=     # from @BotFather
OWNER=         # your Telegram user ID
```

### 3. Run
```bash
pip install -r requirements.txt
bash run.sh
```

### Docker
```bash
docker build -t encplusbot .
docker run --env-file .env encplusbot
```

### Heroku
Required buildpacks, **in this order**:
```
https://github.com/heroku/heroku-buildpack-apt
https://github.com/jonathanong/heroku-buildpack-ffmpeg-latest.git
heroku/python
```
- `apt` reads `Aptfile` (repo root) — installs `aria2` if enabled.
- `ffmpeg` buildpack provides ffmpeg/ffprobe with libx264/libx265/libaom-av1/libopus etc.
- `.python-version` (repo root, contents: `3.12`) — required, `runtime.txt` is deprecated.

---

## Basic Usage

| Action | Command |
|--------|---------|
| Queue a replied video | Reply to video → `/add` |
| Queue a direct link | `/leech <url>` |
| Queue a torrent | `/qbleech <magnet>` |
| View queue | `/queue` (alias `/select`, `/s`) |
| View FFmpeg cmd | `/get` |
| Set FFmpeg cmd | `/set <params>` |
| Reset FFmpeg | `/reset` |
| Pause bot | `/pause` (alias `/lock`) |
| Cancel all | `/clean` (alias `/cancelall`) |
| Bot status | `/status` |
| Help | `/start` or `/help` |

> **Files sent to the bot will not auto-encode by default.**  
> Reply to the file and use `/add` — or `/autoencode on` / `AUTO_ENCODE=True`.

---

## Toggles (owner-only, runtime)

These persist until the bot restarts; reply with no args to check current state.

| Command | Effect |
|---------|--------|
| `/autoencode on\|off` | Auto-queue files sent to bot/group on receive. Seeded from `AUTO_ENCODE` env on boot. |
| `/autothumb on\|off` | Auto-generate a video-frame thumbnail on upload when no custom thumb is set. On by default. |
| `/groupenc on\|off` | Allow downloading/encoding & setting thumbnails inside groups. |
| `/delthumb` | Delete the saved custom thumbnail (send a photo to set a new one). |
| `/showthumb` | Show currently saved thumbnail(s). |

---

## Key Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTO_ENCODE` | `False` | Seed value for `/autoencode` on boot |
| `FFMPEG` | x265/AAC | Default encode command |
| `DATABASE_URL` | None | MongoDB URI (recommended) |
| `LOG_CHANNEL` | 0 | Telegram channel for logs |
| `FCHANNEL` | 0 | Forward completed encodes here |
| `USE_ANILIST` | True | Anime metadata from Anilist |
| `WORKERS` | 2 | Parallel workers |
| `ENABLE_ARIA2` | `True` | Direct-link (`/leech`) downloads |

See `env.sample` for all options.

---

## Credits

- **Original:** [Danish_00](https://github.com/1Danish-00) — [compressorqueue](https://github.com/1Danish-00/compressorqueue)
- **Forked from:** [Nubuki-all](https://github.com/Nubuki-all) — [Enc](https://github.com/Nubuki-all/Enc)
- **This fork:** [TheMisfitDK](https://github.com/TheMisfitDK)

Licensed under GPL v3. See [License](License).
