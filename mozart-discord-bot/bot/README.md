# Discord Bot (mozart-discord-bot)

A Discord bot that lives in your server, remembers the conversation, summarizes it,
and can chime in on its own — hosted on your Umbrel.

## Commands

| Command | What it does |
| --- | --- |
| `/summarize [count]` | Summarizes the last N messages (default 120, max 500) of the current channel. |
| `/summary [count]` | A quick public TL;DR of the last N messages (default 100, max 300) — posted for everyone in the channel. |
| `/catchup` | Summarizes everything that happened in the channel since *your* last message. |
| `/chime on` / `off` | Enables/disables the bot joining this channel's conversation spontaneously (needs Manage Server). |
| `/chime mood chill\|normal\|chatty` | How chatty it should be. normal ≈ up to 8 messages/day, min 12 min apart. |
| `/chime status` | Current chime settings and activity for this channel. |
| `/persona show` / `/persona set <text>` / `/persona reset` | The bot's voice for this server (up to 1500 chars). |
| `/memory add <text>` / `list` / `forget <id>` / `clear` | Facts the bot should remember about this community — it weaves them into its chime-ins and replies (up to 60 notes). |
| `/botstatus` | Health check: connection, counters, LLM endpoint, last error. |

Mentions always get a reply, whether or not chime-in is enabled for the channel.

## Settings dashboard

The bot's dashboard — linked as **Settings** at `http://umbrel.local:8095` — configures
everything live, no restart needed:

- **LLM** — base URL, model (dropdown populated from the endpoint's `/models`, or type a
  custom id) and API key. *Test connection* verifies the values before you save.
- **Quiet hours** — same setting as `QUIET_HOURS`, editable without touching env vars.
- **Per server** — channel chime on/off + mood, persona, and memory notes, with the same
  effect as the slash commands.

Dashboard values override the environment variables; "Reset LLM overrides to env" clears
them. Values are stored in the bot's SQLite DB on the Umbrel.

## Soul (personality)

`bot/soul.md` ships with the app and is the bot's core personality — it's fed into every
chime-in and mention reply. Three layers, from general to specific:

1. **soul.md** — the bundled default, versioned in the repo.
2. **Dashboard override** — edit the *Soul* box at `http://umbrel.local:8095/settings`;
   wins over the file. "Reset to bundled soul.md" clears it.
3. **Per-server persona** — `/persona set` (or the server's dashboard page) overrides the
   soul for that server only.

Cap: 6000 characters. `SOUL_FILE` can point at a different file path.

## Configuration (environment variables)

Set under umbrelOS **Settings → Advanced → environment variables** (service `bot`).
Most settings can also be changed live from the [dashboard](#settings-dashboard) — dashboard values override env vars:

| Variable | Default | Meaning |
| --- | --- | --- |
| `DISCORD_BOT_TOKEN` | — | **Required.** From the Discord Developer Portal → your app → Bot. |
| `LLM_API_KEY` | — | **Required.** API key for the OpenAI-compatible endpoint. |
| `LLM_BASE_URL` | `https://opencode.ai/zen/go/v1` | Any OpenAI-compatible base URL (OpenAI: `https://api.openai.com/v1`, OpenRouter, Ollama via `http://host.docker.internal:11434/v1`, …). |
| `LLM_MODEL` | `deepseek-v4.1-flash` | Model name for the endpoint. |
| `DATA_DIR` | `/data` | Where the SQLite memory lives. |
| `PORT` | `8095` | Status dashboard port. |
| `QUIET_HOURS` | `off` | `1-8` or `22-6` = no spontaneous messages in those hours (container-local time, i.e. UTC unless a TZ is set). |
| `LOG_LEVEL` | `INFO` | `DEBUG` for verbosity. |
| `SOUL_FILE` | bundled `soul.md` | Path to a custom soul/personality file. |

## Discord setup (one-time)

1. In the [Discord Developer Portal](https://discord.com/developers/applications), create an application.
2. **Bot → Privileged Gateway Intents: enable MESSAGE CONTENT INTENT** (required to read messages).
3. Copy the bot token (Bot → Reset Token) into the app's environment variables.
4. Invite it with scopes `bot` + `applications.commands` and permissions: View Channels, Send Messages, Read Message History, Embed Links.

## Privacy & behavior notes

- Messages are only stored for channels where chime-in is on, capped per channel and pruned after 7 days. `/summarize`, `/summary` and `/catchup` read live from Discord and work anywhere the bot can read history, even with chime off.
- Spontaneous messages respect per-channel cooldown, a daily cap, and quiet hours. If the model has nothing useful to add it stays silent (it's instructed to prefer SILENT).
- The status dashboard is at `http://umbrel.local:8095` (JSON: `/status.json`, health: `/healthz`).

## Maintenance

- Update: bump the version in all three pinned spots — `umbrel-app.yml` `version:`, the image tag in `docker-compose.yml`, and the `tags:` in `.github/workflows/build-discord-bot.yml` — push; the store offers an Update.
- Local self-check: `python bot.py --check` (validates config, DB, transcript, status server).
