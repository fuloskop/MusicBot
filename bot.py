import asyncio
import json
import unicodedata
from datetime import datetime, timezone
import discord
from discord.ext import commands
from collections import deque
from dotenv import load_dotenv
import os
import yt_dlp
import aiohttp


def normalize_turkish(text: str) -> str:
    """Turkce karakterleri normalize et ve kucuk harfe cevir."""
    text = unicodedata.normalize("NFC", text)
    # Turkce buyuk harfleri kucuge cevir
    tr_map = str.maketrans("İIŞĞÜÖÇ", "iısğüöç")
    text = text.translate(tr_map)
    return text.lower()

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# Her sunucu (guild) için ayrı kuyruk
queues: dict[int, deque] = {}
now_playing: dict[int, str] = {}

# Kanal takip sistemi
# { guild_id: [ { "id": int, "channel_id": int, "keyword": str, "endpoint": str } ] }
# /data volume'u varsa orayi kullan (Fly.io), yoksa mevcut dizin (lokal)
DATA_DIR = "/data" if os.path.isdir("/data") else "."
WATCHERS_FILE = os.path.join(DATA_DIR, "watchers.json")
watchers: dict[int, list[dict]] = {}
watcher_counter = 0

# Log sistemi - son 100 log tutulur
watcher_logs: dict[int, deque] = {}
MAX_LOGS = 100


def get_logs(guild_id: int) -> deque:
    if guild_id not in watcher_logs:
        watcher_logs[guild_id] = deque(maxlen=MAX_LOGS)
    return watcher_logs[guild_id]


def add_log(guild_id: int, log_type: str, watcher_id: int, **kwargs):
    """Log kaydı ekle ve konsola yaz."""
    now = datetime.now(timezone.utc)
    entry = {
        "time": now.isoformat(),
        "type": log_type,
        "watcher_id": watcher_id,
        **kwargs,
    }
    get_logs(guild_id).append(entry)

    # Fly.io monitoring icin konsol logu
    time_str = now.strftime("%Y-%m-%d %H:%M:%S")
    if log_type == "match":
        print(
            f"[{time_str}] [MATCH] Takip #{watcher_id} | "
            f"Kanal: #{kwargs.get('channel', '?')} | "
            f"Yazan: {kwargs.get('author', '?')} | "
            f"Eslesen: {kwargs.get('matched', [])} | "
            f"Mesaj: {kwargs.get('content', '')[:80]}"
        )
    elif log_type == "sent":
        print(
            f"[{time_str}] [SENT] Takip #{watcher_id} | "
            f"Endpoint: {kwargs.get('endpoint', '?')} | "
            f"Status: {kwargs.get('status', '?')}"
        )
    elif log_type == "error":
        print(
            f"[{time_str}] [ERROR] Takip #{watcher_id} | "
            f"Endpoint: {kwargs.get('endpoint', '?')} | "
            f"Hata: {kwargs.get('error', '?')}"
        )
    elif log_type == "added":
        print(
            f"[{time_str}] [ADDED] Takip #{watcher_id} | "
            f"Kanal: #{kwargs.get('channel', '?')} | "
            f"Kelime: {kwargs.get('keyword', '?')}"
        )
    elif log_type == "removed":
        print(f"[{time_str}] [REMOVED] Takip #{watcher_id}")


def load_watchers():
    global watchers, watcher_counter
    if os.path.exists(WATCHERS_FILE):
        with open(WATCHERS_FILE, "r") as f:
            data = json.load(f)
        # JSON key'leri string, int'e cevir
        watchers = {int(k): v for k, v in data.get("watchers", {}).items()}
        watcher_counter = data.get("counter", 0)


def save_watchers():
    with open(WATCHERS_FILE, "w") as f:
        json.dump({"watchers": watchers, "counter": watcher_counter}, f, indent=2)

COOKIES_FILE = os.path.join(DATA_DIR, "cookies.txt")

YDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": False,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "extract_flat": False,
    "extractor_args": {"youtube": {"player_client": ["web_music", "android_music"]}},
    "http_headers": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    },
}

if os.path.exists(COOKIES_FILE):
    YDL_OPTIONS["cookiefile"] = COOKIES_FILE

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}


def get_queue(guild_id: int) -> deque:
    if guild_id not in queues:
        queues[guild_id] = deque()
    return queues[guild_id]


async def extract_info(query: str) -> list[dict]:
    """Linkten veya arama sorgusundan sarki bilgilerini cikar."""
    loop = asyncio.get_event_loop()

    def _extract():
        with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
            info = ydl.extract_info(query, download=False)
            if "entries" in info:
                # Playlist
                return [
                    {"title": e.get("title", "Bilinmeyen"), "url": e["url"]}
                    for e in info["entries"]
                    if e and e.get("url")
                ]
            else:
                return [{"title": info.get("title", "Bilinmeyen"), "url": info["url"]}]

    return await loop.run_in_executor(None, _extract)


async def play_next(ctx: commands.Context):
    """Kuyruktaki siradaki sarkiyi cal."""
    guild_id = ctx.guild.id
    queue = get_queue(guild_id)

    if not queue:
        now_playing.pop(guild_id, None)
        await ctx.send("Kuyruk bitti!")
        return

    song = queue.popleft()
    now_playing[guild_id] = song["title"]

    source = discord.FFmpegOpusAudio(song["url"], **FFMPEG_OPTIONS)

    def after_playing(error):
        if error:
            print(f"Hata: {error}")
        asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop)

    ctx.voice_client.play(source, after=after_playing)
    await ctx.send(f"Caliniyor: **{song['title']}**")


@bot.event
async def on_ready():
    load_watchers()
    print(f"{bot.user} olarak giris yapildi!")


@bot.event
async def on_message(message: discord.Message):
    # Kendi mesajlarimizi yoksay (sadece bu bot)
    if message.author.id == bot.user.id:
        return

    # Komutlari isle (sadece insan mesajlari icin)
    if not message.author.bot:
        await bot.process_commands(message)

    # Takip kontrolu (bot mesajlari dahil)
    if not message.guild:
        return

    guild_watchers = watchers.get(message.guild.id, [])
    if not guild_watchers:
        return

    # Embed icerigini de kontrol et
    full_content = message.content
    for embed in message.embeds:
        if embed.description:
            full_content += " " + embed.description
        if embed.title:
            full_content += " " + embed.title
        for field in embed.fields:
            full_content += " " + field.name + " " + field.value

    # Debug: takip edilen kanallardaki tum mesajlari logla
    watched_channel_ids = {w["channel_id"] for w in guild_watchers}
    if message.channel.id in watched_channel_ids:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        print(
            f"[{now}] [DEBUG] Kanal: #{message.channel.name} | "
            f"Yazan: {message.author} (bot={message.author.bot}, webhook={message.webhook_id}) | "
            f"Content: '{message.content[:100]}' | "
            f"Embeds: {len(message.embeds)} | "
            f"FullContent: '{full_content[:100]}'"
        )

    if not full_content.strip():
        return

    content_check = full_content

    for watcher in guild_watchers:
        if watcher["channel_id"] != message.channel.id:
            continue

        # Virgülle ayrılmış kelimelerden herhangi biri geçiyorsa eşleş
        keywords = [k.strip() for k in watcher["keyword"].split(",")]
        matched = [k for k in keywords if k and k in content_check]
        if not matched:
            continue

        # Log: eslesti
        add_log(
            message.guild.id, "match", watcher["id"],
            channel=message.channel.name,
            author=str(message.author),
            matched=matched,
            content=full_content,
        )

        # Embed verilerini ayri olarak topla
        embeds_data = []
        for embed in message.embeds:
            embeds_data.append({
                "title": embed.title,
                "description": embed.description,
                "fields": [{"name": f.name, "value": f.value} for f in embed.fields],
            })

        # Eslesen mesaji endpoint'e gonder
        payload = {
            "guild_id": message.guild.id,
            "guild_name": message.guild.name,
            "channel_id": message.channel.id,
            "channel_name": message.channel.name,
            "author_id": message.author.id,
            "author_name": str(message.author),
            "message_id": message.id,
            "content": message.content,
            "full_content": full_content,
            "embeds": embeds_data,
            "keyword": watcher["keyword"],
            "matched_keywords": matched,
            "timestamp": message.created_at.isoformat(),
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    watcher["endpoint"],
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    add_log(
                        message.guild.id, "sent", watcher["id"],
                        endpoint=watcher["endpoint"],
                        status=resp.status,
                    )
        except Exception as e:
            add_log(
                message.guild.id, "error", watcher["id"],
                endpoint=watcher["endpoint"],
                error=str(e),
            )


@bot.command(name="help", aliases=["yardim", "komutlar"])
async def help_command(ctx: commands.Context):
    embed = discord.Embed(
        title="MusicBot - Komutlar",
        description="Asagida kullanabilcegin tum komutlar listelenmistir.",
        color=0x2ECC71,
    )

    embed.add_field(
        name="Muzik Komutlari",
        value=(
            "`!gel` — Botu ses kanalina cagir\n"
            "`!cal <link/arama>` — Sarki cal (alias: `!p`, `!play`)\n"
            "`!liste` — Kuyruktaki sarkilari goster (alias: `!q`, `!queue`)\n"
            "`!atla` — Sarkiyi atla (alias: `!s`, `!skip`)\n"
            "`!duraklat` — Sarkiyi duraklat (alias: `!pause`)\n"
            "`!devam` — Duraklatilmis sarkiyi devam ettir (alias: `!resume`)\n"
            "`!dur` — Calmayi durdur ve kuyrugu temizle (alias: `!stop`)\n"
            "`!git` — Ses kanalindan ayril (alias: `!leave`, `!dc`)"
        ),
        inline=False,
    )

    embed.add_field(
        name="Kanal Takip Komutlari",
        value=(
            "`!takip <#kanal> <kelime> <endpoint>` — Kanaldaki kelimeyi takip et\n"
            "`!takipler` — Aktif takipleri listele (alias: `!watchlist`)\n"
            "`!takipkaldir <id>` — Takibi kaldir (alias: `!unwatch`)\n\n"
            "**Coklu kelime:** Virgul ile ayir, herhangi biri gecerse tetiklenir\n"
            '**Tam ifade:** Tirnak icinde yaz, tum ifade aranir'
        ),
        inline=False,
    )

    embed.add_field(
        name="Genel",
        value=(
            "`!help` — Bu mesaji goster (alias: `!yardim`, `!komutlar`)\n"
            "`!config` — Sunucunun mevcut ayarlarini goster\n"
            "`!loglar [adet]` — Son takip loglarini goster (alias: `!logs`)"
        ),
        inline=False,
    )

    embed.add_field(
        name="Ornek Kullanim",
        value=(
            "```\n"
            "!cal https://youtube.com/watch?v=...\n"
            "!cal never gonna give you up\n"
            "!takip #genel indirim https://api.example.com/hook\n"
            '!takip #genel indirim,kampanya,firsat https://api.example.com/hook\n'
            '!takip #genel "buyuk indirim" https://api.example.com/hook\n'
            "```"
        ),
        inline=False,
    )

    embed.set_footer(text="MusicBot | !help ile bu mesaji tekrar gorebilirsin")
    await ctx.send(embed=embed)


@bot.command(name="config", aliases=["ayarlar", "durum"])
async def config(ctx: commands.Context):
    guild_id = ctx.guild.id
    embed = discord.Embed(
        title="Sunucu Konfigurasyonu",
        description=f"**{ctx.guild.name}** icin mevcut ayarlar",
        color=0x3498DB,
    )

    # Muzik durumu
    queue = get_queue(guild_id)
    if guild_id in now_playing:
        music_status = f"Caliniyor: **{now_playing[guild_id]}**\nKuyrukta **{len(queue)}** sarki var"
    elif len(queue) > 0:
        music_status = f"Duraklatilmis — Kuyrukta **{len(queue)}** sarki var"
    else:
        music_status = "Su an bir sey calmiyor"

    vc_status = "Bagli" if ctx.voice_client else "Bagli degil"
    embed.add_field(
        name="Muzik Durumu",
        value=f"{music_status}\nSes kanali: **{vc_status}**",
        inline=False,
    )

    # Takip durumu
    guild_watchers = watchers.get(guild_id, [])
    if guild_watchers:
        watcher_lines = []
        for w in guild_watchers:
            channel = bot.get_channel(w["channel_id"])
            ch_name = channel.mention if channel else f"(silinmis kanal)"
            watcher_lines.append(
                f"`#{w['id']}` {ch_name} — Kelime: `{w['keyword']}`\n"
                f"  Endpoint: `{w['endpoint']}`"
            )
        watcher_text = "\n".join(watcher_lines)
    else:
        watcher_text = "Aktif takip yok\n`!takip #kanal kelime endpoint` ile ekle"

    embed.add_field(
        name=f"Kanal Takipleri ({len(guild_watchers)} aktif)",
        value=watcher_text,
        inline=False,
    )

    embed.set_footer(text="MusicBot | Ayarlari degistirmek icin !help yaz")
    await ctx.send(embed=embed)


@bot.command(name="gel", help="Botu ses kanalina cagir")
async def join(ctx: commands.Context):
    if not ctx.author.voice:
        return await ctx.send("Once bir ses kanalina gir!")
    channel = ctx.author.voice.channel
    if ctx.voice_client:
        await ctx.voice_client.move_to(channel)
    else:
        await channel.connect()
    await ctx.send(f"**{channel.name}** kanalina katildim!")


@bot.command(name="cal", aliases=["p", "play"], help="Sarki cal (link veya arama)")
async def play(ctx: commands.Context, *, query: str):
    # Ses kanalina baglan
    if not ctx.voice_client:
        if not ctx.author.voice:
            return await ctx.send("Once bir ses kanalina gir!")
        await ctx.author.voice.channel.connect()

    await ctx.send(f"Araniyor: **{query}**...")

    try:
        songs = await extract_info(query)
    except Exception as e:
        return await ctx.send(f"Hata olustu: {e}")

    queue = get_queue(ctx.guild.id)

    if len(songs) > 1:
        for song in songs:
            queue.append(song)
        await ctx.send(f"**{len(songs)}** sarki kuyruga eklendi!")
    else:
        queue.append(songs[0])
        await ctx.send(f"Kuyruga eklendi: **{songs[0]['title']}**")

    # Halihazirda bir sey calmiyorsa baslat
    if not ctx.voice_client.is_playing() and not ctx.voice_client.is_paused():
        await play_next(ctx)


@bot.command(name="liste", aliases=["queue", "q"], help="Kuyruktaki sarkilari goster")
async def show_queue(ctx: commands.Context):
    guild_id = ctx.guild.id
    queue = get_queue(guild_id)

    if guild_id in now_playing:
        msg = f"Simdi caliniyor: **{now_playing[guild_id]}**\n\n"
    else:
        msg = ""

    if not queue:
        msg += "Kuyruk bos."
    else:
        for i, song in enumerate(queue, 1):
            msg += f"`{i}.` {song['title']}\n"
            if i >= 20:
                msg += f"... ve {len(queue) - 20} sarki daha"
                break

    await ctx.send(msg)


@bot.command(name="atla", aliases=["skip", "s"], help="Sarkiyi atla")
async def skip(ctx: commands.Context):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()  # after callback play_next'i tetikler
        await ctx.send("Atlandi!")
    else:
        await ctx.send("Su an bir sey calmiyor.")


@bot.command(name="duraklat", aliases=["pause"], help="Sarkiyi duraklat")
async def pause(ctx: commands.Context):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("Duraklatildi.")


@bot.command(name="devam", aliases=["resume"], help="Duraklatilan sarkiyi devam ettir")
async def resume(ctx: commands.Context):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("Devam ediliyor.")


@bot.command(name="dur", aliases=["stop"], help="Calmayi durdur ve kuyrugu temizle")
async def stop(ctx: commands.Context):
    guild_id = ctx.guild.id
    get_queue(guild_id).clear()
    now_playing.pop(guild_id, None)
    if ctx.voice_client:
        ctx.voice_client.stop()
    await ctx.send("Durduruldu ve kuyruk temizlendi.")


@bot.command(name="git", aliases=["leave", "dc"], help="Ses kanalindan ayril")
async def leave(ctx: commands.Context):
    if ctx.voice_client:
        guild_id = ctx.guild.id
        get_queue(guild_id).clear()
        now_playing.pop(guild_id, None)
        await ctx.voice_client.disconnect()
        await ctx.send("Gorusuruz!")
    else:
        await ctx.send("Zaten bir ses kanalinda degilim.")


@bot.command(name="takip", help="Bir kanaldaki belirli kelimeyi takip et ve endpoint'e gonder")
async def watch(ctx: commands.Context, channel: discord.TextChannel, keyword: str, endpoint: str):
    global watcher_counter
    guild_id = ctx.guild.id

    if guild_id not in watchers:
        watchers[guild_id] = []

    watcher_counter += 1
    watcher = {
        "id": watcher_counter,
        "channel_id": channel.id,
        "keyword": keyword,
        "endpoint": endpoint,
    }
    watchers[guild_id].append(watcher)
    save_watchers()
    add_log(guild_id, "added", watcher_counter, channel=channel.name, keyword=keyword)

    await ctx.send(
        f"Takip **#{watcher_counter}** eklendi!\n"
        f"Kanal: {channel.mention}\n"
        f"Kelime: `{keyword}`\n"
        f"Endpoint: `{endpoint}`"
    )


@bot.command(name="takipler", aliases=["watchlist"], help="Aktif takipleri listele")
async def list_watchers(ctx: commands.Context):
    guild_watchers = watchers.get(ctx.guild.id, [])
    if not guild_watchers:
        return await ctx.send("Aktif takip yok.")

    msg = "**Aktif Takipler:**\n"
    for w in guild_watchers:
        channel = bot.get_channel(w["channel_id"])
        ch_name = channel.mention if channel else f"(silinmis kanal {w['channel_id']})"
        msg += f"`#{w['id']}` | {ch_name} | Kelime: `{w['keyword']}` | Endpoint: `{w['endpoint']}`\n"

    await ctx.send(msg)


@bot.command(name="takipkaldir", aliases=["unwatch"], help="Takibi kaldır (ID ile)")
async def remove_watcher(ctx: commands.Context, watcher_id: int):
    guild_watchers = watchers.get(ctx.guild.id, [])
    for i, w in enumerate(guild_watchers):
        if w["id"] == watcher_id:
            guild_watchers.pop(i)
            save_watchers()
            add_log(ctx.guild.id, "removed", watcher_id)
            return await ctx.send(f"Takip **#{watcher_id}** kaldirildi.")

    await ctx.send(f"Takip **#{watcher_id}** bulunamadi.")


@bot.command(name="loglar", aliases=["logs"], help="Son takip loglarini goster")
async def show_logs(ctx: commands.Context, adet: int = 10):
    adet = min(adet, 25)
    logs = get_logs(ctx.guild.id)

    if not logs:
        return await ctx.send("Henuz log kaydı yok.")

    recent = list(logs)[-adet:]
    recent.reverse()

    embed = discord.Embed(
        title=f"Takip Loglari (son {len(recent)})",
        color=0xE67E22,
    )

    type_icons = {
        "match": "🔍",
        "sent": "✅",
        "error": "❌",
        "added": "➕",
        "removed": "➖",
    }

    lines = []
    for log in recent:
        icon = type_icons.get(log["type"], "📋")
        time_str = log["time"][11:19]  # HH:MM:SS
        wid = log["watcher_id"]

        if log["type"] == "match":
            line = (
                f"{icon} `{time_str}` **#{wid}** Eslesti\n"
                f"  Kanal: #{log.get('channel', '?')} | Yazan: {log.get('author', '?')}\n"
                f"  Kelimeler: `{', '.join(log.get('matched', []))}`\n"
                f"  Mesaj: `{log.get('content', '')[:60]}`"
            )
        elif log["type"] == "sent":
            line = (
                f"{icon} `{time_str}` **#{wid}** Gonderildi\n"
                f"  Status: `{log.get('status', '?')}` | Endpoint: `{log.get('endpoint', '?')[:40]}`"
            )
        elif log["type"] == "error":
            line = (
                f"{icon} `{time_str}` **#{wid}** Hata\n"
                f"  `{log.get('error', '?')[:60]}`"
            )
        elif log["type"] == "added":
            line = f"{icon} `{time_str}` **#{wid}** Takip eklendi — #{log.get('channel', '?')} | `{log.get('keyword', '?')}`"
        elif log["type"] == "removed":
            line = f"{icon} `{time_str}` **#{wid}** Takip kaldirildi"
        else:
            line = f"📋 `{time_str}` **#{wid}** {log['type']}"

        lines.append(line)

    embed.description = "\n\n".join(lines)
    embed.set_footer(text="Fly.io monitoring: fly.io/apps/musicbot-rfsieg/monitoring")
    await ctx.send(embed=embed)


bot.run(os.getenv("DISCORD_TOKEN"))
