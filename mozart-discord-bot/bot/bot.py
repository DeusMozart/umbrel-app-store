#!/usr/bin/env python3
"""
Mozart's Discord Bot — conversation summaries + ambient chime-in.

Runs as a umbrelOS app (see ../umbrel-app.yml). Configuration comes from
environment variables (Settings -> Advanced -> environment variables):

    DISCORD_BOT_TOKEN  (required)  Bot token from the Discord Developer Portal
    LLM_BASE_URL       OpenAI-compatible base URL (default: OpenCode Zen)
    LLM_API_KEY        API key for that endpoint
    LLM_MODEL          Model name (default: deepseek-v4.1-flash)
    DATA_DIR           SQLite + state directory (default /data)
    PORT               Status dashboard port (default 8095)
    QUIET_HOURS        e.g. "1-8" or "22-6"; "off" (default) disables
    LOG_LEVEL          default INFO

Commands: /summarize /catchup /chime /persona /botstatus
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import random
import re
import sqlite3
import sys
import threading
import time
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

import discord
import httpx
from discord import app_commands

VERSION = "1.2.0"
CSRF_TOKEN = uuid.uuid4().hex[:24]  # guards dashboard POSTs; rotates on restart
LOG = logging.getLogger("bot")

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()

TOKEN = env("DISCORD_BOT_TOKEN")
LLM_BASE_URL = env("LLM_BASE_URL", "https://opencode.ai/zen/go/v1").rstrip("/")
LLM_API_KEY = env("LLM_API_KEY")
LLM_MODEL = env("LLM_MODEL", "deepseek-v4.1-flash")
DATA_DIR = env("DATA_DIR", "/data")
PORT = int(env("PORT", "8095") or "8095")
QUIET_HOURS = env("QUIET_HOURS", "off").lower()
LOG_LEVEL = env("LOG_LEVEL", "INFO").upper()

MOODS: dict[str, dict] = {
    # chance: probability a full batch gets evaluated
    # batch:  new messages needed before considering a chime-in
    # cooldown: minimum seconds between two bot messages in a channel
    # daily:  maximum spontaneous messages per channel per day
    "chill":  {"chance": 0.15, "batch": 12, "cooldown": 25 * 60, "daily": 4},
    "normal": {"chance": 0.35, "batch": 8,  "cooldown": 12 * 60, "daily": 8},
    "chatty": {"chance": 0.60, "batch": 5,  "cooldown": 6 * 60,  "daily": 15},
}
MENTION_COOLDOWN = 45          # seconds between mention-triggered replies
EVAL_MIN_GAP = 300             # seconds between chime evaluations per channel
KEEP_DAYS = 7                  # message retention for chime context
KEEP_PER_CHANNEL = 4000        # hard cap of stored messages per channel

DEFAULT_PERSONA = (
    "You are a friendly, easygoing member of this Discord community. "
    "You are brief: 1-2 short sentences, casual and warm, a little witty -- never corporate. "
    "You avoid emojis unless they genuinely add something, and you never lecture or spam."
)

CHIME_SYSTEM = """{persona}

You are looking at recent messages from the #{channel} channel of a Discord server.
You are deciding whether to chime in with a short message right now.

Guidelines:
- Chime in if you can add something genuinely useful, funny, warm, or if you can answer a question.
- Reply with ONLY the message you would send: 1-2 short sentences, no markdown, no surrounding quotes.
- If the conversation is fine without you, or you would just be restating others, reply with exactly: SILENT
- Never repeat someone else's message back at them. Do not announce that you are a language model."""

SUMMARY_SYSTEM = """You summarize Discord conversations into a quick catch-up brief.
Format (plain text, no markdown headings, no bold):
- First line: "TL;DR: " followed by one sentence.
- Then 3-8 short bullets, each starting with "- ", covering what was discussed, decided, and asked.
Mention usernames when it matters who said or asked what. Be concrete: names, numbers, decisions.
Skip filler and pleasantries. If the stretch is mostly banter, say so in a short bullet."""

CATCHUP_NOTE = "Summarize what {name} missed in #{channel} since their last message there."
CATCHUP_FALLBACK = (
    "Summarize the end of a #{channel} conversation. {name} asked to catch up, but "
    "their last message was further back than I can see; summarize the recent stretch instead."
)

# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  msg_id TEXT UNIQUE,
  guild_id TEXT NOT NULL,
  channel_id TEXT NOT NULL,
  author_id TEXT NOT NULL,
  author_name TEXT NOT NULL,
  is_bot INTEGER NOT NULL DEFAULT 0,
  content TEXT NOT NULL,
  ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_channel_ts ON messages(channel_id, ts);
CREATE TABLE IF NOT EXISTS channels (
  channel_id TEXT PRIMARY KEY,
  guild_id TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT '',
  chime INTEGER NOT NULL DEFAULT 0,
  mood TEXT NOT NULL DEFAULT 'normal'
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id TEXT NOT NULL,
  text TEXT NOT NULL,
  created_ts REAL NOT NULL
);
"""


class DB:
    def __init__(self, path: str):
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()
        self._inserts = 0

    def store_message(self, msg_id, guild_id, channel_id, author_id, author_name,
                      is_bot, content, ts):
        with self._lock:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO messages (msg_id, guild_id, channel_id, author_id,"
                " author_name, is_bot, content, ts) VALUES (?,?,?,?,?,?,?,?)",
                (msg_id, guild_id, channel_id, author_id, author_name,
                 int(is_bot), content, ts))
            self.conn.commit()
            self._inserts += 1
            if self._inserts % 100 == 0:
                self._prune()
            return cur.rowcount > 0

    def _prune(self):
        cutoff = time.time() - KEEP_DAYS * 86400
        self.conn.execute("DELETE FROM messages WHERE ts < ?", (cutoff,))
        for row in self.conn.execute("SELECT channel_id FROM channels").fetchall():
            cid = row["channel_id"]
            self.conn.execute(
                "DELETE FROM messages WHERE channel_id = ? AND id NOT IN "
                "(SELECT id FROM messages WHERE channel_id = ? ORDER BY ts DESC LIMIT ?)",
                (cid, cid, KEEP_PER_CHANNEL))
        self.conn.commit()

    def recent(self, channel_id: str, limit: int = 40):
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM messages WHERE channel_id = ? ORDER BY ts DESC LIMIT ?",
                (channel_id, limit)).fetchall()
        return list(reversed(rows))

    def ensure_channel(self, channel_id, guild_id, name=""):
        with self._lock:
            self.conn.execute(
                "INSERT INTO channels (channel_id, guild_id, name) VALUES (?,?,?) "
                "ON CONFLICT(channel_id) DO UPDATE SET name = excluded.name,"
                " guild_id = excluded.guild_id",
                (channel_id, guild_id, name))
            self.conn.commit()

    def set_chime(self, channel_id, flag: bool):
        with self._lock:
            self.conn.execute("UPDATE channels SET chime = ? WHERE channel_id = ?",
                              (int(flag), channel_id))
            self.conn.commit()

    def set_mood(self, channel_id, mood: str):
        with self._lock:
            self.conn.execute("UPDATE channels SET mood = ? WHERE channel_id = ?",
                              (mood, channel_id))
            self.conn.commit()

    def channel(self, channel_id):
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM channels WHERE channel_id = ?", (channel_id,)).fetchone()

    def chime_channels(self) -> set:
        with self._lock:
            rows = self.conn.execute(
                "SELECT channel_id FROM channels WHERE chime = 1").fetchall()
        return {r["channel_id"] for r in rows}

    def all_channels(self):
        with self._lock:
            return [dict(r) for r in self.conn.execute(
                "SELECT * FROM channels ORDER BY guild_id, name").fetchall()]

    def counts(self):
        with self._lock:
            total = self.conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"]
            per = {r["channel_id"]: r["c"] for r in self.conn.execute(
                "SELECT channel_id, COUNT(*) c FROM messages GROUP BY channel_id")}
        return total, per

    def get_kv(self, key, default=""):
        with self._lock:
            row = self.conn.execute("SELECT v FROM kv WHERE k = ?", (key,)).fetchone()
        return row["v"] if row else default

    def set_kv(self, key, value):
        with self._lock:
            self.conn.execute(
                "INSERT INTO kv (k, v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
                (key, value))
            self.conn.commit()

    def del_kv(self, key):
        with self._lock:
            self.conn.execute("DELETE FROM kv WHERE k = ?", (key,))
            self.conn.commit()

    def add_memory(self, guild_id, text):
        with self._lock:
            self.conn.execute(
                "INSERT INTO memories (guild_id, text, created_ts) VALUES (?,?,?)",
                (guild_id, text, time.time()))
            self.conn.commit()

    def list_memories(self, guild_id):
        with self._lock:
            return [dict(r) for r in self.conn.execute(
                "SELECT * FROM memories WHERE guild_id = ? ORDER BY id",
                (guild_id,)).fetchall()]

    def remove_memory(self, guild_id, mem_id) -> bool:
        with self._lock:
            cur = self.conn.execute(
                "DELETE FROM memories WHERE guild_id = ? AND id = ?", (guild_id, mem_id))
            self.conn.commit()
            return cur.rowcount > 0

    def clear_memories(self, guild_id) -> int:
        with self._lock:
            cur = self.conn.execute(
                "DELETE FROM memories WHERE guild_id = ?", (guild_id,))
            self.conn.commit()
            return cur.rowcount

    def count_memories(self, guild_id=None):
        with self._lock:
            if guild_id is None:
                row = self.conn.execute("SELECT COUNT(*) c FROM memories").fetchone()
            else:
                row = self.conn.execute(
                    "SELECT COUNT(*) c FROM memories WHERE guild_id = ?",
                    (guild_id,)).fetchone()
        return row["c"]


# --------------------------------------------------------------------------
# Runtime state
# --------------------------------------------------------------------------

class State:
    def __init__(self):
        self.start = time.time()
        self.pending: dict[str, int] = {}        # cid -> messages since last bot msg
        self.last_bot_msg: dict[str, float] = {}  # cid -> ts of last bot message
        self.last_eval: dict[str, float] = {}     # cid -> ts of last chime evaluation
        self.last_mention: dict[str, float] = {}
        self.daily: dict[str, list] = {}          # cid -> [date, count]
        self.chime_ids: set[str] = set()
        self.messages_seen = 0
        self.chimes_sent = 0
        self.summaries_run = 0
        self.last_chime: dict = {}
        self.llm_last_error = ""
        self.connected = False
        self.bot_tag = ""
        self.guilds: list = []

    def snapshot(self) -> dict:
        return {
            "connected": self.connected,
            "bot_tag": self.bot_tag,
            "uptime": int(time.time() - self.start),
            "messages_seen": self.messages_seen,
            "chimes_sent": self.chimes_sent,
            "summaries_run": self.summaries_run,
            "last_chime": dict(self.last_chime),
            "guilds": list(self.guilds),
            "llm_last_error": self.llm_last_error,
        }


state = State()
db = None  # initialised in main()/run_check() after DATA_DIR exists


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")  # noqa: DTZ006


def human_delta(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60} min"
    if seconds < 48 * 3600:
        return f"{seconds // 3600} h {(seconds % 3600) // 60} min"
    return f"{seconds // 86400} d"


def parse_quiet_hours(spec: str):
    """'1-8' -> (1, 8); supports wrap-around like '22-6'; 'off' -> None."""
    spec = spec.strip()
    m = re.fullmatch(r"(\d{1,2})\s*-\s*(\d{1,2})", spec)
    if not m:
        return None
    start, end = int(m.group(1)), int(m.group(2))
    if not (0 <= start <= 23 and 0 <= end <= 23) or start == end:
        return None
    return (start, end)


def in_quiet_hours() -> bool:
    rng = parse_quiet_hours(quiet_hours_spec())
    if not rng:
        return False
    start, end = rng
    h = datetime.now().hour  # noqa: DTZ005
    if start < end:
        return start <= h < end
    return h >= start or h < end


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def llm_settings() -> tuple[str, str, str]:
    """(base_url, model, api_key) — dashboard overrides win over env vars."""
    if db is None:
        return LLM_BASE_URL, LLM_MODEL, LLM_API_KEY
    base = db.get_kv("cfg:llm_base_url") or LLM_BASE_URL
    model = db.get_kv("cfg:llm_model") or LLM_MODEL
    key = db.get_kv("cfg:llm_api_key") or LLM_API_KEY
    return base.rstrip("/"), model, key


def llm_key_source() -> str:
    if db is not None and db.get_kv("cfg:llm_api_key"):
        return "dashboard"
    return "env" if LLM_API_KEY else ""


def quiet_hours_spec() -> str:
    if db is None:
        return QUIET_HOURS
    return (db.get_kv("cfg:quiet_hours") or QUIET_HOURS).strip().lower()


def nav_html() -> str:
    return ("<div class='nav'><a href='/'>Status</a><a href='/settings'>Settings</a></div>")


def guild_name(gid: str) -> str:
    if bot is not None:
        try:
            g = bot.get_guild(int(gid))
        except (ValueError, AttributeError):
            g = None
        if g:
            return g.name
    return f"server {gid}"


def to_rowdict(author_name, is_bot, content, ts) -> dict:
    return {"author_name": author_name, "is_bot": is_bot, "content": content, "ts": ts}


def msg_to_rowdict(m: discord.Message) -> dict | None:
    content = (m.clean_content or "").strip()
    if not content and m.attachments:
        content = f"[{len(m.attachments)} attachment(s)]"
    if not content and m.stickers:
        content = "[sticker]"
    if not content:
        return None
    return to_rowdict(m.author.display_name, m.author.bot, content, m.created_at.timestamp())


def build_transcript(rows, max_chars: int = 8000) -> str:
    lines = []
    for r in rows:
        r = r if isinstance(r, dict) else dict(r)
        content = " ".join((r.get("content") or "").split())
        if not content:
            continue
        if len(content) > 400:
            content = content[:400] + "…"
        name = r["author_name"] + (" (bot)" if r.get("is_bot") else "")
        lines.append(f"[{fmt_ts(r['ts'])}] {name}: {content}")
    while lines and sum(len(x) + 1 for x in lines) > max_chars:
        lines.pop(0)
    return "\n".join(lines)


def clean_reply(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[a-z]*\n?|```$", "", text).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'“”":
        text = text[1:-1].strip()
    text = " ".join(text.split())
    return text[:480]


def allowed(interaction: discord.Interaction) -> bool:
    return bool(interaction.guild and interaction.user
                and getattr(interaction.user, "guild_permissions", None)
                and interaction.user.guild_permissions.manage_guild)


async def deny(interaction: discord.Interaction):
    await interaction.response.send_message(
        "You need the **Manage Server** permission for that.", ephemeral=True)


# --------------------------------------------------------------------------
# LLM
# --------------------------------------------------------------------------

class LLMError(Exception):
    pass


async def llm_chat(system: str, user: str, max_tokens: int = 350,
                   temperature: float = 0.7, session: str | None = None) -> str:
    base, model, key = llm_settings()
    if not key:
        raise LLMError("No LLM API key \u2014 add one in the app's Settings \u2192 Advanced \u2192 "
                       "environment variables, or in the dashboard (/settings).")
    if not model:
        raise LLMError("No LLM model is set.")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    headers = {"Authorization": f"Bearer {key}"}
    if "opencode" in base:
        # OpenCode Zen/Go require an affinity key on every request (HTTP 400
        # MissingSessionID without it); a stable per-channel key keeps the
        # upstream prompt cache warm.
        headers["x-opencode-session"] = session or f"oneshot-{uuid.uuid4().hex[:16]}"
    attempts = 0
    while True:
        attempts += 1
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                resp = await client.post(
                    f"{base}/chat/completions",
                    headers=headers,
                    json=payload)
        except httpx.HTTPError as e:
            state.llm_last_error = f"network: {e}"
            raise LLMError(f"Could not reach the LLM endpoint: {e}") from e
        if resp.status_code != 200:
            state.llm_last_error = f"HTTP {resp.status_code}: {resp.text[:160]}"
            raise LLMError(f"LLM endpoint returned HTTP {resp.status_code}: {resp.text[:160]}")
        try:
            text = resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            state.llm_last_error = f"unexpected response: {str(resp.text)[:160]}"
            raise LLMError(f"Unexpected LLM response: {str(resp.text)[:160]}") from e
        text = (text or "").strip()
        if text:
            state.llm_last_error = ""
            return text
        if attempts == 1:
            # Reasoning models (e.g. deepseek-v4.1-flash) count hidden reasoning
            # tokens against max_tokens and can burn the whole budget before
            # emitting content \u2014 retry once with generous headroom.
            payload["max_tokens"] = max_tokens * 2 + 256
            continue
        state.llm_last_error = "empty reply from LLM"
        raise LLMError("The LLM returned an empty reply.")


def persona(guild_id=None) -> str:
    if guild_id is not None:
        v = db.get_kv(f"persona:{guild_id}")
        if v:
            return v
    return db.get_kv("persona", DEFAULT_PERSONA)


def memory_block(guild_id) -> str:
    """Facts the community taught the bot, for the system prompt ('' when none)."""
    if guild_id is None:
        return ""
    mems = db.list_memories(str(guild_id))[:40]
    if not mems:
        return ""
    lines = "\n".join(f"- {m['text']}" for m in mems)
    return f"Things you know about this community (from memory):\n{lines}\n\n"


# --------------------------------------------------------------------------
# Sending
# --------------------------------------------------------------------------

async def store_own_message(channel, text: str):
    if str(channel.id) in state.chime_ids:
        ts = time.time()
        guild = getattr(channel, "guild", None)
        guild_id = str(guild.id) if guild else ""
        db.store_message(f"bot-{channel.id}-{int(ts * 1000)}", guild_id,
                         str(channel.id), str(bot.user.id), bot.user.display_name,
                         True, text, ts)


async def send_chime(channel, text: str):
    sent = await channel.send(text)
    state.chimes_sent += 1
    state.last_bot_msg[str(channel.id)] = time.time()
    state.last_chime = {
        "channel": getattr(channel, "name", "?"),
        "guild": getattr(getattr(channel, "guild", None), "name", ""),
        "text": text[:140],
        "ts": time.time(),
    }
    await store_own_message(channel, text)
    return sent


# --------------------------------------------------------------------------
# Discord client
# --------------------------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)


@bot.event
async def on_ready():
    state.connected = True
    state.bot_tag = str(bot.user)
    LOG.info("Connected as %s (in %d guild(s))", bot.user, len(bot.guilds))
    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.watching, name="the conversation"))
    for guild in bot.guilds:
        try:
            tree.copy_global_to(guild=guild)
            synced = await tree.sync(guild=guild)
            LOG.info("Synced %d commands to guild %s", len(synced), guild.name)
        except Exception as e:  # noqa: BLE001
            LOG.warning("Command sync failed for %s: %s", guild, e)


@bot.event
async def on_guild_join(guild: discord.Guild):
    state.bot_tag = str(bot.user)
    try:
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)
    except Exception as e:  # noqa: BLE001
        LOG.warning("Command sync failed for new guild %s: %s", guild, e)


async def snapshot_loop():
    """Keep a thread-safe view of the bot's world for the status page."""
    while True:
        try:
            guilds = []
            if bot.is_ready():
                chans = {c["channel_id"]: c for c in db.all_channels()}
                for g in bot.guilds:
                    gl = {"id": str(g.id), "name": g.name, "channels": []}
                    for ch in chans.values():
                        if ch["guild_id"] == str(g.id):
                            gl["channels"].append(
                                {"id": ch["channel_id"], "name": ch["name"],
                                 "chime": bool(ch["chime"]), "mood": ch["mood"]})
                    guilds.append(gl)
            state.guilds = guilds
        except Exception as e:  # noqa: BLE001
            LOG.debug("snapshot error: %s", e)
        await asyncio.sleep(15)


@bot.event
async def on_message(message: discord.Message):
    if bot.user is None or message.author.id == bot.user.id:
        return
    if not message.guild:  # ignore DMs
        return
    if message.author.bot:  # ignore other bots
        return

    cid = str(message.channel.id)
    if cid in state.chime_ids:
        row = msg_to_rowdict(message)
        if row:
            state.messages_seen += 1
            db.store_message(str(message.id), str(message.guild.id), cid,
                             str(message.author.id), row["author_name"], False,
                             row["content"], row["ts"])

    mentioned = bot.user in message.mentions
    if not mentioned and message.reference and isinstance(
            message.reference.resolved, discord.Message) and message.reference.resolved.author.id == bot.user.id:
        mentioned = True

    if mentioned:
        await handle_mention(message)
    elif cid in state.chime_ids:
        await maybe_chime(message)


async def handle_mention(message: discord.Message):
    cid = str(message.channel.id)
    now = time.time()
    if now - state.last_mention.get(cid, 0) < MENTION_COOLDOWN:
        return
    state.last_mention[cid] = now

    rows = []
    try:
        async for m in message.channel.history(limit=25):
            row = msg_to_rowdict(m)
            if row:
                rows.append(row)
    except discord.HTTPException as e:
        LOG.warning("history fetch failed: %s", e)
    rows.reverse()
    transcript = build_transcript(rows, max_chars=5000)

    system = (memory_block(message.guild.id)
              + f"You are {bot.user.display_name}, a member of this Discord community.\n"
                f"{persona(message.guild.id)}\n\n"
                f"You were just mentioned in #{getattr(message.channel, 'name', '?')}. "
                f"Reply helpfully and briefly \u2014 1-3 short sentences, casual, no markdown. "
                f"Use the recent conversation below as context. Don't prefix with your name, "
                f"don't announce that you're a bot, and don't summarize unless asked.")
    prompt = (f"Recent messages:\n\n{transcript}\n\n"
              f"{message.author.display_name} just wrote: {message.clean_content}\n\nReply to them.")
    try:
        reply = clean_reply(await llm_chat(system, prompt, max_tokens=500, temperature=0.8,
                                           session=f"discord-{cid}"))
    except LLMError as e:
        LOG.warning("mention reply failed: %s", e)
        try:
            await message.reply(str(e), mention_author=False)
        except discord.HTTPException:
            pass
        return
    if not reply:
        return
    try:
        await message.reply(reply, mention_author=False)
        state.last_bot_msg[cid] = time.time()
        await store_own_message(message.channel, reply)
    except discord.HTTPException as e:
        LOG.warning("reply failed: %s", e)


async def maybe_chime(message: discord.Message):
    cid = str(message.channel.id)
    mood_name = "normal"
    row = db.channel(cid)
    if row:
        mood_name = row["mood"] if row["mood"] in MOODS else "normal"
    mood = MOODS[mood_name]

    state.pending[cid] = state.pending.get(cid, 0) + 1
    if state.pending[cid] < mood["batch"]:
        return

    now = time.time()
    if now - state.last_bot_msg.get(cid, 0) < mood["cooldown"]:
        return
    if now - state.last_eval.get(cid, 0) < EVAL_MIN_GAP:
        return
    if in_quiet_hours():
        return
    day = datetime.now().strftime("%Y-%m-%d")  # noqa: DTZ005
    daily = state.daily.get(cid)
    if not daily or daily[0] != day:
        daily = [day, 0]
        state.daily[cid] = daily
    if daily[1] >= mood["daily"]:
        return

    state.pending[cid] = 0
    if random.random() > mood["chance"]:
        return
    state.last_eval[cid] = now

    rows = db.recent(cid, limit=40)
    if len(rows) < 4:
        return
    transcript = build_transcript(rows, max_chars=6000)
    system = memory_block(message.guild.id) + CHIME_SYSTEM.format(
        persona=persona(message.guild.id),
        channel=getattr(message.channel, "name", "?"))
    prompt = (f"Recent messages in #{getattr(message.channel, 'name', '?')}:\n\n{transcript}\n\n"
              f"Should you chime in? Reply with SILENT or the message text only.")
    try:
        text = await llm_chat(system, prompt, max_tokens=400, temperature=0.85,
                              session=f"discord-{cid}")
    except LLMError as e:
        LOG.warning("chime evaluation failed: %s", e)
        state.last_eval[cid] = now + 600  # back off on errors
        return
    if text.strip().upper().startswith("SILENT") or not text.strip():
        LOG.debug("chime: silent in #%s", getattr(message.channel, "name", "?"))
        return
    reply = clean_reply(text)
    if not reply:
        return
    try:
        await send_chime(message.channel, reply)
        daily[1] += 1
        LOG.info("chimed in on #%s: %s", getattr(message.channel, "name", "?"), reply[:80])
    except discord.HTTPException as e:
        LOG.warning("chime send failed: %s", e)


# --------------------------------------------------------------------------
# Slash commands
# --------------------------------------------------------------------------

async def run_summary(interaction: discord.Interaction, rows, title: str,
                      system: str, prompt_prefix: str = ""):
    transcript = build_transcript(rows, max_chars=9000)
    if len(transcript) < 60:
        await interaction.followup.send("Not enough recent conversation to summarize.")
        return
    user = f"{prompt_prefix}Conversation:\n\n{transcript}"
    try:
        text = await llm_chat(system, user, max_tokens=1400, temperature=0.35,
                              session=f"discord-{interaction.channel.id}")
    except LLMError as e:
        await interaction.followup.send(f"Can't summarize right now: {e}")
        return
    state.summaries_run += 1
    if not text:
        await interaction.followup.send("The model returned nothing. Try again in a moment.")
        return
    embed = discord.Embed(title=title[:250], description=text[:4000],
                          color=0x5865F2)
    embed.set_footer(text=f"{len(rows)} messages \u00b7 {LLM_MODEL}")
    await interaction.followup.send(embed=embed)
    rest = text[4000:]
    while rest:
        await interaction.followup.send(rest[:1900])
        rest = rest[1900:]


@tree.command(name="summarize", description="Summarize the recent conversation in this channel")
@app_commands.describe(count="How many recent messages to summarize (10-500, default 120)")
async def summarize(interaction: discord.Interaction, count: int = 120):
    if not interaction.guild:
        await interaction.response.send_message("Use this in a server channel.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    limit = max(10, min(500, count))
    rows = []
    try:
        async for m in interaction.channel.history(limit=limit):
            row = msg_to_rowdict(m)
            if row and (not m.author.bot or m.author.id == bot.user.id):
                rows.append(row)
    except discord.HTTPException as e:
        await interaction.followup.send(f"Couldn't read history: {e}")
        return
    rows.reverse()
    await run_summary(interaction, rows,
                      f"Summary \u2014 #{(interaction.channel.name or 'channel')} (last {len(rows)})",
                      SUMMARY_SYSTEM)


@tree.command(name="catchup", description="What happened here since you last spoke?")
async def catchup(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("Use this in a server channel.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    name = interaction.user.display_name
    channel_name = interaction.channel.name or "channel"
    history = []
    try:
        async for m in interaction.channel.history(limit=400):
            history.append(m)
    except discord.HTTPException as e:
        await interaction.followup.send(f"Couldn't read history: {e}")
        return
    idx = next((i for i, m in enumerate(history)
                if m.author.id == interaction.user.id), None)
    if idx is None:
        picked = history[:150]
        note = CATCHUP_FALLBACK.format(name=name, channel=channel_name)
    else:
        picked = history[:idx]
        note = CATCHUP_NOTE.format(name=name, channel=channel_name)
    rows = []
    for m in reversed(picked):
        if m.author.bot and (bot.user is None or m.author.id != bot.user.id):
            continue
        row = msg_to_rowdict(m)
        if row:
            rows.append(row)
    if len(rows) < 3:
        await interaction.followup.send(
            f"Not much to report \u2014 barely anything since your last message in "
            f"#{channel_name}.")
        return
    await run_summary(interaction, rows,
                      f"Catch-up for {name} \u2014 #{channel_name}", SUMMARY_SYSTEM,
                      prompt_prefix=note + "\n\n")


chime_group = app_commands.Group(
    name="chime", description="Spontaneous participation settings for this channel",
    default_permissions=discord.Permissions(manage_guild=True))


@chime_group.command(name="on", description="Let the bot join this channel's conversation on its own")
async def chime_on(interaction: discord.Interaction):
    if not allowed(interaction):
        await deny(interaction)
        return
    await interaction.response.defer(thinking=True)
    cid = str(interaction.channel.id)
    db.ensure_channel(cid, str(interaction.guild.id), interaction.channel.name or "")
    db.set_chime(cid, True)
    state.chime_ids = db.chime_channels()
    backfilled = 0
    try:
        async for m in interaction.channel.history(limit=150):
            if m.author.bot and (bot.user is None or m.author.id != bot.user.id):
                continue
            row = msg_to_rowdict(m)
            if row:
                ok = db.store_message(str(m.id), str(m.guild.id), cid,
                                      str(m.author.id), row["author_name"],
                                      m.author.bot, row["content"], row["ts"])
                backfilled += int(ok)
    except discord.HTTPException as e:
        LOG.warning("backfill failed: %s", e)
    ch = db.channel(cid)
    mood = MOODS.get(ch["mood"] if ch else "normal", MOODS["normal"])
    await interaction.followup.send(
        f"Alright \u2014 I'll occasionally join the conversation here "
        f"(~{mood['daily']}/day max, min {mood['cooldown'] // 60} min apart). "
        f"Backfilled {backfilled} messages for context. Tune with `/chime mood`.")


@chime_group.command(name="off", description="Stop the bot from chiming in here")
async def chime_off(interaction: discord.Interaction):
    if not allowed(interaction):
        await deny(interaction)
        return
    cid = str(interaction.channel.id)
    db.ensure_channel(cid, str(interaction.guild.id), interaction.channel.name or "")
    db.set_chime(cid, False)
    state.chime_ids = db.chime_channels()
    state.pending.pop(cid, None)
    await interaction.response.send_message("Okay \u2014 I'll stay quiet here unless mentioned.")


@chime_group.command(name="mood", description="How chatty should the bot be in this channel?")
@app_commands.choices(mood=[
    app_commands.Choice(name="chill (rare)", value="chill"),
    app_commands.Choice(name="normal", value="normal"),
    app_commands.Choice(name="chatty", value="chatty"),
])
async def chime_mood(interaction: discord.Interaction, mood: app_commands.Choice[str]):
    if not allowed(interaction):
        await deny(interaction)
        return
    cid = str(interaction.channel.id)
    db.ensure_channel(cid, str(interaction.guild.id), interaction.channel.name or "")
    db.set_mood(cid, mood.value)
    m = MOODS[mood.value]
    await interaction.response.send_message(
        f"Mood set to **{mood.value}**: chime-in chance {int(m['chance'] * 100)}% per "
        f"{m['batch']}-message stretch, min {m['cooldown'] // 60} min apart, "
        f"up to {m['daily']}/day.")


@chime_group.command(name="status", description="Show chime settings and activity for this channel")
async def chime_status(interaction: discord.Interaction):
    cid = str(interaction.channel.id)
    ch = db.channel(cid)
    if not ch or not ch["chime"]:
        await interaction.response.send_message(
            "Chime-in is **off** here. Admins can enable it with `/chime on`.",
            ephemeral=True)
        return
    m = MOODS.get(ch["mood"], MOODS["normal"])
    daily = state.daily.get(cid)
    today = daily[1] if daily and daily[0] == datetime.now().strftime("%Y-%m-%d") else 0  # noqa: DTZ005
    cool = max(0, m["cooldown"] - (time.time() - state.last_bot_msg.get(cid, 0)))
    _, per = db.counts()
    await interaction.response.send_message(
        f"**Chime-in: on** \u00b7 mood **{ch['mood']}**\n"
        f"Pending messages: {state.pending.get(cid, 0)}/{m['batch']} \u00b7 "
        f"cooldown: {'ready' if cool <= 0 else human_delta(cool) + ' left'} \u00b7 "
        f"sent today: {today}/{m['daily']} \u00b7 stored context: {per.get(cid, 0)} messages"
        + (f"\nQuiet hours: {quiet_hours_spec()} (now {'quiet' if in_quiet_hours() else 'active'})"
           if parse_quiet_hours(quiet_hours_spec()) else ""),
        ephemeral=True)


persona_group = app_commands.Group(
    name="persona", description="The bot's voice and personality",
    default_permissions=discord.Permissions(manage_guild=True))


@persona_group.command(name="show", description="Show the bot's persona for this server")
async def persona_show(interaction: discord.Interaction):
    gid = interaction.guild.id if interaction.guild else None
    src = "set for this server" if gid and db.get_kv(f"persona:{gid}") else "default"
    await interaction.response.send_message(
        f"Persona ({src}):\n```\n{persona(gid)[:1700]}\n```", ephemeral=True)


@persona_group.command(name="set", description="Set the bot's persona for this server (voice, style, quirks)")
@app_commands.describe(text="The new persona text (up to 1500 characters)")
async def persona_set(interaction: discord.Interaction, text: str):
    if not allowed(interaction):
        await deny(interaction)
        return
    if len(text) > 1500:
        await interaction.response.send_message("That's over 1500 characters \u2014 trim it down.",
                                                ephemeral=True)
        return
    db.set_kv(f"persona:{interaction.guild.id}", text.strip())
    await interaction.response.send_message(
        "Persona updated for this server. It applies to chime-ins and replies here.",
        ephemeral=True)


@persona_group.command(name="reset", description="Reset this server's persona back to the default")
async def persona_reset(interaction: discord.Interaction):
    if not allowed(interaction):
        await deny(interaction)
        return
    db.del_kv(f"persona:{interaction.guild.id}")
    await interaction.response.send_message("Back to the default persona.", ephemeral=True)


memory_group = app_commands.Group(
    name="memory", description="Things the bot remembers about this community",
    default_permissions=discord.Permissions(manage_guild=True))


@memory_group.command(name="add", description="Teach the bot a fact it should remember here")
@app_commands.describe(text="e.g. 'Zak leads rallies in Kingshot, Kingdom 259'")
async def memory_add(interaction: discord.Interaction, text: str):
    if not allowed(interaction):
        await deny(interaction)
        return
    text = " ".join(text.split())[:400]
    if not text:
        await interaction.response.send_message("Empty note \u2014 nothing saved.", ephemeral=True)
        return
    if db.count_memories(str(interaction.guild.id)) >= 60:
        await interaction.response.send_message(
            "Memory is full (60 notes). Check `/memory list` and free space with `/memory forget`.",
            ephemeral=True)
        return
    db.add_memory(str(interaction.guild.id), text)
    await interaction.response.send_message(f"Got it \u2014 remembering: {text}", ephemeral=True)


@memory_group.command(name="list", description="List what the bot remembers about this server")
async def memory_list(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("Use this in a server.", ephemeral=True)
        return
    mems = db.list_memories(str(interaction.guild.id))
    if not mems:
        await interaction.response.send_message(
            "Nothing in memory yet. Add notes with `/memory add`.", ephemeral=True)
        return
    chunks, buf = [], f"**Memory ({len(mems)} notes)**\n"
    for m in mems:
        line = f"`{m['id']}.` {m['text'][:300]}\n"
        if len(buf) + len(line) > 1900:
            chunks.append(buf)
            buf = ""
        buf += line
    chunks.append(buf)
    await interaction.response.send_message(chunks[0], ephemeral=True)
    for c in chunks[1:8]:
        await interaction.followup.send(c, ephemeral=True)


@memory_group.command(name="forget", description="Remove one memory note by its number")
@app_commands.describe(mem_id="The number shown by /memory list")
async def memory_forget(interaction: discord.Interaction, mem_id: int):
    if not allowed(interaction):
        await deny(interaction)
        return
    if db.remove_memory(str(interaction.guild.id), mem_id):
        await interaction.response.send_message(f"Forgotten: note {mem_id}.", ephemeral=True)
    else:
        await interaction.response.send_message(
            f"No note with id {mem_id} here \u2014 check `/memory list`.", ephemeral=True)


@memory_group.command(name="clear", description="Wipe ALL memory notes for this server")
async def memory_clear(interaction: discord.Interaction):
    if not allowed(interaction):
        await deny(interaction)
        return
    n = db.clear_memories(str(interaction.guild.id))
    await interaction.response.send_message(f"Cleared {n} notes from memory.", ephemeral=True)


for _group in (chime_group, persona_group, memory_group):
    tree.add_command(_group)


@tree.command(name="botstatus", description="Bot health: connection, memory, LLM, counters")
async def botstatus(interaction: discord.Interaction):
    ok = bot.is_ready()
    total, _ = db.counts()
    mems = db.count_memories(str(interaction.guild.id)) if interaction.guild else 0
    lbase, lmodel, lkey = llm_settings()
    chans = db.all_channels()
    mine = [c for c in chans if interaction.guild and c["guild_id"] == str(interaction.guild.id)]
    active = [c["name"] for c in mine if c["chime"]]
    await interaction.response.send_message(
        f"**Status** {'online' if ok else 'offline'} \u00b7 up {human_delta(time.time() - state.start)} "
        f"\u00b7 v{VERSION}\n"
        f"Guilds: {len(bot.guilds)} \u00b7 chime channels here: "
        f"{len(active) if active else 'none'}{(' (' + ', '.join('#' + n for n in active[:8]) + ')') if active else ''}\n"
        f"Stored messages: {total} \u00b7 memory notes: {mems} \u00b7 "
        f"chimes sent: {state.chimes_sent} \u00b7 summaries: {state.summaries_run}\n"
        f"LLM: `{lmodel}` @ `{lbase}` \u00b7 API key "
        f"{'set' if lkey else '**missing**'}\n"
        f"Dashboard: http://umbrel.local:{PORT}/settings"
        + (f"\nLast LLM error: `{state.llm_last_error[:160]}`" if state.llm_last_error else ""),
        ephemeral=True)


# --------------------------------------------------------------------------
# Status dashboard (http://umbrel.local:8095)
# --------------------------------------------------------------------------

CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { margin:0; background:#0f1115; color:#e8eaf0;
       font:15px/1.5 -apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif; }
.wrap { max-width:860px; margin:0 auto; padding:28px 20px 60px; }
h1 { font-size:20px; margin:0 0 4px; }
.sub { color:#8b90a0; font-size:13px; margin-bottom:22px; }
.dot { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:7px; }
.on { background:#3fb950; } .off { background:#f85149; }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; margin:18px 0; }
.card { background:#171a21; border:1px solid #232733; border-radius:12px; padding:14px 16px; }
.card .k { color:#8b90a0; font-size:12px; text-transform:uppercase; letter-spacing:.06em; }
.card .v { font-size:19px; margin-top:5px; font-weight:600; }
h2 { font-size:14px; color:#aab0c0; margin:26px 0 10px; text-transform:uppercase; letter-spacing:.08em; }
table { width:100%; border-collapse:collapse; background:#171a21; border:1px solid #232733; border-radius:12px; overflow:hidden; }
th,td { text-align:left; padding:9px 14px; font-size:14px; border-bottom:1px solid #232733; }
th { color:#8b90a0; font-weight:500; font-size:12px; text-transform:uppercase; letter-spacing:.06em; }
tr:last-child td { border-bottom:none; }
.pill { display:inline-block; padding:2px 10px; border-radius:999px; font-size:12px; }
.pill.on { background:rgba(63,185,80,.15); color:#3fb950; }
.pill.off { background:rgba(139,144,160,.15); color:#9aa0b0; }
.muted { color:#8b90a0; }
.mono { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:13px; }
.snippet { background:#171a21; border:1px solid #232733; border-radius:12px; padding:12px 16px; font-size:14px; }
footer { color:#5c6170; font-size:12px; margin-top:34px; }
.nav { margin:12px 0 6px; }
.nav a { color:#8fb0ff; text-decoration:none; margin-right:18px; font-size:14px; }
label { display:block; font-size:12px; color:#8b90a0; text-transform:uppercase; letter-spacing:.06em; margin:14px 0 5px; }
input[type=text], input[type=password], select, textarea {
  width:100%; background:#0f1115; color:#e8eaf0; border:1px solid #2a2f3d;
  border-radius:8px; padding:9px 11px; font:14px/1.45 inherit; }
textarea { min-height:120px; resize:vertical; }
button { background:#2f6fed; color:#fff; border:0; border-radius:8px; padding:9px 16px;
  font:600 14px/1 inherit; cursor:pointer; margin:12px 10px 0 0; }
button.ghost { background:#232733; }
button.danger { background:#7a3b3b; padding:5px 11px; margin:0; font-size:13px; }
.note { color:#8b90a0; font-size:13px; margin-top:10px; }
.note.ok { color:#3fb950; }
.note.err { color:#f85149; }
form { margin:0; }
"""


def status_payload() -> dict:
    snap = state.snapshot()
    total, per = db.counts()
    memories_total = db.count_memories()
    lbase, lmodel, lkey = llm_settings()
    channels = []
    for c in db.all_channels():
        channels.append({
            "guild_id": c["guild_id"], "name": c["name"], "chime": bool(c["chime"]),
            "mood": c["mood"], "stored": per.get(c["channel_id"], 0)})
    return {
        "version": VERSION,
        "connected": snap["connected"],
        "bot": snap["bot_tag"],
        "uptime_seconds": snap["uptime"],
        "uptime": human_delta(snap["uptime"]),
        "guilds": snap["guilds"],
        "messages_stored": total,
        "memories_total": memories_total,
        "messages_seen": snap["messages_seen"],
        "chimes_sent": snap["chimes_sent"],
        "summaries_run": snap["summaries_run"],
        "last_chime": snap["last_chime"],
        "llm": {"base_url": lbase, "model": lmodel,
                "api_key_set": bool(lkey), "last_error": snap["llm_last_error"]},
        "quiet_hours": quiet_hours_spec(), "quiet_now": in_quiet_hours(),
        "discord_token_set": bool(TOKEN),
        "channels": channels,
    }


def render_html() -> str:
    p = status_payload()
    dot = "on" if p["connected"] else "off"
    status_txt = p["bot"] if p["connected"] else "not connected"
    if not p["discord_token_set"]:
        banner = ("<div class='snippet' style='border-color:#7a3b3b'>"
                  "<b>DISCORD_BOT_TOKEN is not set.</b> Add it in the app's Settings \u2192 "
                  "Advanced \u2192 environment variables, then save.</div>")
    else:
        banner = ""
    rows = []
    for c in p["channels"]:
        pill = f"<span class='pill {'on' if c['chime'] else 'off'}'>" \
               f"{'on' if c['chime'] else 'off'}</span>"
        rows.append(f"<tr><td>#{c['name']}</td><td>{pill}</td><td>{c['mood']}</td>"
                    f"<td>{c['stored']}</td></tr>")
    chan_table = ("<table><tr><th>Channel</th><th>Chime-in</th><th>Mood</th>"
                  "<th>Stored msgs</th></tr>" + "".join(rows) + "</table>") if rows else \
                 "<div class='muted'>No channels configured yet. Use <span class='mono'>/chime on</span> in Discord.</div>"
    lc = p["last_chime"]
    if lc:
        last = (f"<div class='snippet'><b>#{lc.get('channel','?')}</b> "
                f"<span class='muted'>{human_delta(time.time() - lc.get('ts', time.time()))} ago</span>"
                f"<br>{lc.get('text','')}</div>")
    else:
        last = "<div class='muted'>Nothing yet.</div>"
    llm_state = "key set" if p["llm"]["api_key_set"] else "key missing"
    body = f"""
<div class="wrap">
  <h1><span class="dot {dot}"></span>{status_txt}</h1>
  <div class="sub">Discord bot \u00b7 v{p['version']} \u00b7 up {p['uptime']}</div>
  {nav_html()}
  {banner}
  <div class="grid">
    <div class="card"><div class="k">Guilds</div><div class="v">{len(p['guilds'])}</div></div>
    <div class="card"><div class="k">Stored messages</div><div class="v">{p['messages_stored']}</div></div>
    <div class="card"><div class="k">Memory notes</div><div class="v">{p['memories_total']}</div></div>
    <div class="card"><div class="k">Chimes sent</div><div class="v">{p['chimes_sent']}</div></div>
    <div class="card"><div class="k">Summaries</div><div class="v">{p['summaries_run']}</div></div>
  </div>
  <h2>LLM</h2>
  <div class="snippet mono">{p['llm']['model']} @ {p['llm']['base_url']} \u00b7 {llm_state}
  {('<br>Last error: ' + p['llm']['last_error']) if p['llm']['last_error'] else ''}</div>
  <h2>Channels</h2>
  {chan_table}
  <h2>Last chime-in</h2>
  {last}
  <footer>MOZART DISCORD BOT \u00b7 status refreshes every 15 s \u00b7 http://umbrel.local:{PORT}</footer>
</div>"""
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            f"<meta http-equiv='refresh' content='15'>"
            f"<title>Discord Bot \u2014 status</title><style>{CSS}</style></head>"
            f"<body>{body}</body></html>")


MODEL_FALLBACK = ["deepseek-v4.1-flash", "deepseek-v4-flash", "deepseek-v4-pro",
                  "glm-5.3", "glm-5.3-flash", "grok-4.7", "gpt-6-luna"]

SETTINGS_JS = """
<script>
async function loadModels() {
  const sel = document.getElementById('model-select');
  if (!sel) return;
  try {
    const r = await fetch('/settings/models');
    const d = await r.json();
    if (!d.models || !d.models.length) return;
    const cur = sel.value;
    sel.innerHTML = '';
    const ids = d.models.slice();
    if (cur && !ids.includes(cur)) ids.unshift(cur);
    for (const m of ids) {
      const o = document.createElement('option');
      o.value = m; o.textContent = m;
      if (m === cur) o.selected = true;
      sel.appendChild(o);
    }
  } catch (e) {}
}
async function testLLM() {
  const btn = document.getElementById('test-btn');
  const out = document.getElementById('test-result');
  const f = btn.closest('form');
  out.textContent = 'Testing...';
  out.className = 'note';
  try {
    const r = await fetch('/settings/llm/test', {method: 'POST',
      body: new URLSearchParams(new FormData(f))});
    const d = await r.json();
    if (d.ok) { out.textContent = 'OK (' + d.ms + ' ms) - model replied: ' + d.reply;
                out.className = 'note ok'; }
    else { out.textContent = 'Failed: ' + d.error; out.className = 'note err'; }
  } catch (e) { out.textContent = 'Failed: ' + e; out.className = 'note err'; }
}
document.addEventListener('DOMContentLoaded', function () {
  loadModels();
  const b = document.getElementById('test-btn');
  if (b) b.addEventListener('click', testLLM);
});
</script>
"""


def render_settings(saved: str = "", err: str = "") -> str:
    base, model, _key = llm_settings()
    key_src = llm_key_source() or "none set"
    qh = quiet_hours_spec()
    qh_val = "" if qh in ("", "off") else qh
    opts = []
    for m in dict.fromkeys([model] + MODEL_FALLBACK):
        sel = " selected" if m == model else ""
        opts.append(f"<option value='{esc(m)}'{sel}>{esc(m)}</option>")
    guild_ids = {str(g.id) for g in bot.guilds}
    guild_ids |= {c["guild_id"] for c in db.all_channels()}
    if guild_ids:
        rows = "".join(
            f"<tr><td><a href='/settings/guild/{esc(g)}'>{esc(guild_name(g))}</a></td>"
            f"<td class='muted mono'>{esc(g)}</td></tr>"
            for g in sorted(guild_ids))
        guild_table = "<table><tr><th>Server</th><th>ID</th></tr>" + rows + "</table>"
    else:
        guild_table = ("<div class='muted'>No servers yet \u2014 they appear once the bot "
                       "connects and sees messages.</div>")
    banner = ""
    if saved:
        banner = "<div class='snippet' style='border-color:#2e5e34'><b>Saved.</b></div>"
    if err:
        banner = f"<div class='snippet' style='border-color:#7a3b3b'><b>{esc(err)}</b></div>"
    body = f"""
<div class="wrap">
  <h1>Discord Bot \u2014 settings</h1>
  <div class="sub">v{VERSION} \u00b7 changes apply immediately, no restart needed</div>
  {nav_html()}
  {banner}
  <h2>LLM</h2>
  <div class="snippet">
    <form method="post" action="/settings/llm">
      <input type="hidden" name="csrf" value="{CSRF_TOKEN}">
      <label>Base URL</label>
      <input type="text" name="base_url" value="{esc(base)}">
      <label>Model</label>
      <select name="model" id="model-select">{opts}</select>
      <label>Custom model id (optional \u2014 overrides the dropdown)</label>
      <input type="text" name="model_custom" placeholder="e.g. deepseek-v4.1-flash">
      <label>API key</label>
      <input type="password" name="api_key" placeholder="leave blank to keep current ({esc(key_src)})" autocomplete="new-password">
      <button type="submit">Save</button>
      <button type="button" class="ghost" id="test-btn">Test connection</button>
      <div id="test-result" class="note"></div>
    </form>
    <div class="note">Key currently from: {esc(key_src)}. Blank fields fall back to the
    app's environment variables.</div>
  </div>
  <form method="post" action="/settings/llm/clear">
    <input type="hidden" name="csrf" value="{CSRF_TOKEN}">
    <button type="submit" class="ghost">Reset LLM overrides to env</button>
  </form>
  <h2>Chime defaults</h2>
  <div class="snippet">
    <form method="post" action="/settings/chime">
      <input type="hidden" name="csrf" value="{CSRF_TOKEN}">
      <label>Quiet hours (no spontaneous chime-ins)</label>
      <input type="text" name="quiet_hours" value="{esc(qh_val)}" placeholder="e.g. 23-7 \u2014 empty = off">
      <button type="submit">Save</button>
    </form>
    <div class="note">24h range like <span class="mono">23-7</span> (wraps midnight).
    Per-channel chime on/off and moods are on each server's page.</div>
  </div>
  <h2>Servers</h2>
  {guild_table}
  <footer>MOZART DISCORD BOT \u00b7 http://umbrel.local:{PORT}/settings</footer>
</div>"""
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>Discord Bot \u2014 settings</title><style>{CSS}</style></head>"
            f"<body>{body}{SETTINGS_JS}</body></html>")


def render_guild_page(gid: str, saved: str = "", err: str = "") -> str:
    chans = [c for c in db.all_channels() if c["guild_id"] == gid]
    rows = []
    for c in chans:
        cid = esc(c["channel_id"])
        chime_opts = "".join(
            f"<option value='{v}'{' selected' if bool(c['chime']) == (v == '1') else ''}>{lab}</option>"
            for v, lab in (("1", "on"), ("0", "off")))
        mood_opts = "".join(
            f"<option value='{m}'{' selected' if (c['mood'] or 'normal') == m else ''}>{m}</option>"
            for m in MOODS)
        rows.append(
            f"<tr><td>#{esc(c['name'] or c['channel_id'])}</td>"
            f"<td><select name='chime_{cid}'>{chime_opts}</select></td>"
            f"<td><select name='mood_{cid}'>{mood_opts}</select></td>"
            f"<td class='muted mono'>{esc(c['channel_id'])}</td></tr>")
    if rows:
        chan_block = (
            f"<form method='post' action='/settings/guild/{esc(gid)}/channels'>"
            f"<input type='hidden' name='csrf' value='{CSRF_TOKEN}'>"
            "<table><tr><th>Channel</th><th>Chime-in</th><th>Mood</th><th>ID</th></tr>"
            + "".join(rows) + "</table>"
            "<button type='submit'>Save channels</button></form>")
    else:
        chan_block = ("<div class='muted'>No channels here yet \u2014 a channel appears once "
                      "the bot has seen messages in it (or after "
                      "<span class='mono'>/chime on</span>).</div>")
    override = db.get_kv(f"persona:{gid}")
    p_note = "set for this server" if override else "using the default (shown as placeholder)"
    mems = db.list_memories(gid)
    mem_rows = "".join(
        f"<tr><td class='mono'>{m['id']}</td><td>{esc(m['text'])}</td>"
        f"<td style='text-align:right'><form method='post' "
        f"action='/settings/guild/{esc(gid)}/memory/delete'>"
        f"<input type='hidden' name='csrf' value='{CSRF_TOKEN}'>"
        f"<input type='hidden' name='id' value='{m['id']}'>"
        f"<button type='submit' class='danger'>Delete</button></form></td></tr>"
        for m in mems)
    mem_block = (f"<table><tr><th>#</th><th>Note</th><th></th></tr>{mem_rows}</table>"
                 if mems else "<div class='muted'>No notes yet.</div>")
    banner = ""
    if saved:
        banner = "<div class='snippet' style='border-color:#2e5e34'><b>Saved.</b></div>"
    if err:
        banner = f"<div class='snippet' style='border-color:#7a3b3b'><b>{esc(err)}</b></div>"
    body = f"""
<div class="wrap">
  <h1>{esc(guild_name(gid))}</h1>
  <div class="sub">server settings \u00b7 {esc(gid)}</div>
  {nav_html()}
  <div class="nav"><a href="/settings">\u2190 all servers</a></div>
  {banner}
  <h2>Channels</h2>
  {chan_block}
  <h2>Persona</h2>
  <div class="snippet">
    <form method="post" action="/settings/guild/{esc(gid)}/persona">
      <input type="hidden" name="csrf" value="{CSRF_TOKEN}">
      <label>Voice and personality ({esc(p_note)})</label>
      <textarea name="text" placeholder="{esc(DEFAULT_PERSONA)}">{esc(override)}</textarea>
      <button type="submit">Save persona</button>
    </form>
    <form method="post" action="/settings/guild/{esc(gid)}/persona/reset">
      <input type="hidden" name="csrf" value="{CSRF_TOKEN}">
      <button type="submit" class="ghost">Reset to default</button>
    </form>
  </div>
  <h2>Memory ({len(mems)}/60)</h2>
  <div class="snippet">
    <form method="post" action="/settings/guild/{esc(gid)}/memory/add">
      <input type="hidden" name="csrf" value="{CSRF_TOKEN}">
      <label>Add a note the bot should remember here</label>
      <input type="text" name="text" placeholder="e.g. Zak leads rallies in Kingshot, Kingdom 259">
      <button type="submit">Add note</button>
    </form>
  </div>
  {mem_block}
  <footer>MOZART DISCORD BOT \u00b7 http://umbrel.local:{PORT}/settings</footer>
</div>"""
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>{esc(guild_name(gid))} \u2014 settings</title><style>{CSS}</style></head>"
            f"<body>{body}{SETTINGS_JS}</body></html>")


class StatusHandler(BaseHTTPRequestHandler):
    # POSTs are guarded by a per-process CSRF token embedded in every form
    # (an Origin check would be unreliable here: umbrel's app proxy may
    # rewrite the Host header before the request reaches us).
    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            q = parse_qs(parsed.query)
            if path.startswith("/healthz"):
                ready = bool(bot and bot.is_ready())
                self._json(200, {"ok": True, "discord_ready": ready})
            elif path.startswith("/status.json"):
                self._json(200, status_payload())
            elif path == "/settings":
                self._html(200, render_settings(
                    saved=q.get("saved", [""])[0], err=q.get("err", [""])[0]))
            elif path == "/settings/models":
                self._models()
            elif path.startswith("/settings/guild/"):
                parts = path.split("/")
                gid = parts[3] if len(parts) > 3 else ""
                if gid:
                    self._html(200, render_guild_page(
                        gid, saved=q.get("saved", [""])[0], err=q.get("err", [""])[0]))
                else:
                    self._json(404, {"error": "missing server id"})
            elif path == "/" or path.startswith("/index"):
                self._html(200, render_html())
            else:
                self._json(404, {"error": "not found"})
        except BrokenPipeError:
            pass
        except Exception as e:  # noqa: BLE001
            LOG.warning("status server error: %s", e)

    def do_POST(self):
        try:
            path = urlparse(self.path).path
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8", "replace") if length else ""
            f = {k: v[0] for k, v in parse_qs(raw).items()}
            if f.get("csrf") != CSRF_TOKEN:
                self._json(403, {"error": "bad csrf token \u2014 reload the page"})
                return
            if path == "/settings/llm":
                self._save_llm(f)
            elif path == "/settings/llm/test":
                self._json(200, self._test_llm(f))
            elif path == "/settings/llm/clear":
                for k in ("cfg:llm_base_url", "cfg:llm_model", "cfg:llm_api_key"):
                    db.del_kv(k)
                self._redirect("/settings?saved=1")
            elif path == "/settings/chime":
                spec = (f.get("quiet_hours") or "").strip().lower()
                if spec in ("", "off"):
                    db.del_kv("cfg:quiet_hours")
                elif parse_quiet_hours(spec):
                    db.set_kv("cfg:quiet_hours", spec)
                else:
                    self._redirect("/settings?err=" + quote(
                        "Quiet hours must look like 23-7, or be empty."))
                    return
                self._redirect("/settings?saved=1")
            elif path.startswith("/settings/guild/"):
                self._guild_action(path, f)
            else:
                self._json(404, {"error": "not found"})
        except BrokenPipeError:
            pass
        except Exception as e:  # noqa: BLE001
            LOG.warning("settings POST error: %s", e)
            try:
                self._json(500, {"error": str(e)[:200]})
            except Exception as e2:  # noqa: BLE001
                LOG.debug("could not send error response: %s", e2)

    def _save_llm(self, f):
        base = (f.get("base_url") or "").strip().rstrip("/")
        custom = (f.get("model_custom") or "").strip()
        model = custom or (f.get("model") or "").strip()
        key = (f.get("api_key") or "").strip()
        if base:
            db.set_kv("cfg:llm_base_url", base)
        else:
            db.del_kv("cfg:llm_base_url")
        if model:
            db.set_kv("cfg:llm_model", model)
        else:
            db.del_kv("cfg:llm_model")
        if key:
            db.set_kv("cfg:llm_api_key", key)
        self._redirect("/settings?saved=1")

    def _test_llm(self, f) -> dict:
        sbase, smodel, skey = llm_settings()
        base = ((f.get("base_url") or "").strip().rstrip("/")) or sbase
        custom = (f.get("model_custom") or "").strip()
        model = custom or (f.get("model") or "").strip() or smodel
        key = (f.get("api_key") or "").strip() or skey
        if not key:
            return {"ok": False, "error": "no API key set"}
        if not model:
            return {"ok": False, "error": "no model set"}
        headers = {"Authorization": f"Bearer {key}"}
        if "opencode" in base:
            headers["x-opencode-session"] = f"dashboard-test-{uuid.uuid4().hex[:8]}"
        payload = {"model": model,
                   "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                   "max_tokens": 400, "temperature": 0.2}
        t0 = time.time()
        try:
            with httpx.Client(timeout=30) as c:
                r = c.post(f"{base}/chat/completions", headers=headers, json=payload)
            ms = int((time.time() - t0) * 1000)
            if r.status_code != 200:
                return {"ok": False, "ms": ms, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
            reply = (r.json()["choices"][0]["message"].get("content") or "").strip()
            return {"ok": True, "ms": ms,
                    "reply": reply[:200] or "(empty reply \u2014 try another model)"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "ms": int((time.time() - t0) * 1000),
                    "error": f"{type(e).__name__}: {e}"}

    def _models(self):
        base, model, key = llm_settings()
        out: dict = {"models": [], "current": model}
        if key:
            headers = {"Authorization": f"Bearer {key}"}
            if "opencode" in base:
                headers["x-opencode-session"] = "dashboard-models"
            try:
                with httpx.Client(timeout=10) as c:
                    r = c.get(f"{base}/models", headers=headers)
                if r.status_code == 200:
                    out["models"] = sorted(
                        {m.get("id") for m in r.json().get("data", []) if m.get("id")})
                else:
                    out["error"] = f"HTTP {r.status_code}"
            except Exception as e:  # noqa: BLE001
                out["error"] = str(e)[:160]
        self._json(200, out)

    def _guild_action(self, path, f):
        parts = path.split("/")
        gid = parts[3] if len(parts) > 3 else ""
        rest = "/".join(p for p in parts[4:] if p)
        back = f"/settings/guild/{quote(gid)}"
        if not gid:
            self._json(404, {"error": "missing server id"})
        elif rest == "channels":
            for c in db.all_channels():
                if c["guild_id"] != gid:
                    continue
                cid = c["channel_id"]
                if f"chime_{cid}" in f:
                    db.set_chime(cid, f[f"chime_{cid}"] == "1")
                if f.get(f"mood_{cid}") in MOODS:
                    db.set_mood(cid, f[f"mood_{cid}"])
            state.chime_ids = db.chime_channels()
            self._redirect(back + "?saved=1")
        elif rest == "persona":
            text = (f.get("text") or "").strip()
            if len(text) > 1500:
                self._redirect(back + "?err=" + quote("Persona is over 1500 characters."))
            elif text:
                db.set_kv(f"persona:{gid}", text)
                self._redirect(back + "?saved=1")
            else:
                db.del_kv(f"persona:{gid}")
                self._redirect(back + "?saved=1")
        elif rest == "persona/reset":
            db.del_kv(f"persona:{gid}")
            self._redirect(back + "?saved=1")
        elif rest == "memory/add":
            text = " ".join((f.get("text") or "").split())[:400]
            if not text:
                self._redirect(back + "?err=" + quote("Empty note."))
            elif db.count_memories(gid) >= 60:
                self._redirect(back + "?err=" + quote("Memory is full (60 notes)."))
            else:
                db.add_memory(gid, text)
                self._redirect(back + "?saved=1")
        elif rest == "memory/delete":
            try:
                mid = int(f.get("id") or "0")
            except ValueError:
                mid = 0
            db.remove_memory(gid, mid)
            self._redirect(back + "?saved=1")
        else:
            self._json(404, {"error": "unknown settings action"})

    def _json(self, code: int, obj: dict):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _html(self, code: int, page: str):
        data = page.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, path: str):
        self.send_response(303)
        self.send_header("Location", path)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt, *args):
        LOG.debug("status: " + fmt, *args)


def start_status_server():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), StatusHandler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True,
                     name="status-server").start()
    LOG.info("Status dashboard on http://0.0.0.0:%d", PORT)


# --------------------------------------------------------------------------
# Entrypoints
# --------------------------------------------------------------------------

def run_check() -> int:
    global db
    print(f"== mozart-discord-bot v{VERSION} self-check ==")
    print(f"token:      {'set' if TOKEN else 'MISSING (add later in app settings)'}")
    print(f"llm:        {LLM_MODEL} @ {LLM_BASE_URL} "
          f"(key {'set' if LLM_API_KEY else 'MISSING'})")
    print(f"data dir:   {DATA_DIR}")
    print(f"port:       {PORT}")
    print(f"quiet hrs:  {QUIET_HOURS} (parse: {parse_quiet_hours(QUIET_HOURS)})")
    os.makedirs(DATA_DIR, exist_ok=True)
    db = DB(os.path.join(DATA_DIR, "bot.db"))
    now = time.time()
    for i in range(5):
        db.store_message(f"check-{i}", "0", "999", "1", f"tester{i % 2}", False,
                         f"test message {i}", now - 300 + i * 30)
    rows = db.recent("999", limit=10)
    transcript = build_transcript(rows)
    assert "test message 4" in transcript
    print("transcript: OK")
    print(transcript)
    db.add_memory("0", "self-check note")
    mems = db.list_memories("0")
    assert len(mems) == 1 and mems[0]["text"] == "self-check note"
    assert db.count_memories("0") == 1
    assert db.remove_memory("0", mems[0]["id"])
    db.clear_memories("0")
    print("memory store: OK")
    db.conn.execute("DELETE FROM messages WHERE channel_id = '999'")
    db.conn.commit()
    start_status_server()
    import urllib.error
    import urllib.parse
    import urllib.request
    url = f"http://127.0.0.1:{PORT}/healthz"
    with urllib.request.urlopen(url, timeout=5) as r:
        print(f"status server: OK ({url} -> {r.read().decode()})")
    base_url = f"http://127.0.0.1:{PORT}"
    with urllib.request.urlopen(f"{base_url}/settings", timeout=5) as r:
        page = r.read().decode()
    assert "Model" in page and "csrf" in page
    with urllib.request.urlopen(f"{base_url}/settings/guild/0", timeout=5) as r:
        page = r.read().decode()
    assert "Persona" in page and "Memory" in page

    def post(path, data):
        body = urllib.parse.urlencode({"csrf": CSRF_TOKEN, **data}).encode()
        req = urllib.request.Request(base_url + path, data=body, method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.read()

    post("/settings/llm", {"base_url": "https://example.com/v1", "model": "m",
                           "model_custom": "", "api_key": ""})
    assert db.get_kv("cfg:llm_base_url") == "https://example.com/v1"
    db.del_kv("cfg:llm_base_url")
    try:
        urllib.request.urlopen(urllib.request.Request(
            base_url + "/settings/llm", data=b"csrf=wrong", method="POST"), timeout=5)
        raise AssertionError("bad csrf token was accepted")
    except urllib.error.HTTPError as e:
        assert e.code == 403
    print("settings endpoints: OK")
    print("== all checks passed ==")
    return 0


async def amain():
    global db
    os.makedirs(DATA_DIR, exist_ok=True)
    db = DB(os.path.join(DATA_DIR, "bot.db"))
    state.chime_ids = db.chime_channels()
    start_status_server()
    asyncio.create_task(snapshot_loop())

    if not TOKEN:
        LOG.error("DISCORD_BOT_TOKEN is not set. Add it in the app's Settings \u2192 "
                  "Advanced \u2192 environment variables. The status page is up at "
                  "http://umbrel.local:%d; the bot will not connect without a token.", PORT)
        while True:
            await asyncio.sleep(3600)

    while True:
        try:
            await bot.start(TOKEN)
        except discord.LoginFailure as e:
            state.connected = False
            LOG.error("Discord rejected the bot token (%s). Double-check DISCORD_BOT_TOKEN "
                      "in the app settings \u2014 retrying in 120 s.", e)
            await asyncio.sleep(120)
        except discord.PrivilegedIntentsRequired as e:
            state.connected = False
            LOG.error("Discord requires privileged intents that are not enabled: %s. "
                      "Enable MESSAGE CONTENT INTENT in the Developer Portal \u2192 your app "
                      "\u2192 Bot.", e)
            await asyncio.sleep(300)
        except Exception as e:  # noqa: BLE001
            state.connected = False
            LOG.error("Connection lost (%r) \u2014 reconnecting in 15 s.", e)
            await asyncio.sleep(15)


def main():
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("discord").setLevel(logging.WARNING)
    if "--check" in sys.argv:
        sys.exit(run_check())
    asyncio.run(amain())


if __name__ == "__main__":
    main()
