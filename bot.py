import json
import os
from collections import deque
from datetime import datetime, timezone

import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# Bot sahibi - tum komutlara her zaman yetkili
OWNER_ID = 356537796875517953

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# /data volume varsa orayi kullan (Coolify/Fly.io persistent), yoksa mevcut dizin
DATA_DIR = "/data" if os.path.isdir("/data") else "."
WATCHERS_FILE = os.path.join(DATA_DIR, "watchers.json")
AUTHORIZED_FILE = os.path.join(DATA_DIR, "authorized_users.json")

# { guild_id: [ { "id", "channel_id", "keyword", "endpoint" } ] }
watchers: dict[int, list[dict]] = {}
watcher_counter = 0

# OWNER_ID disindaki yetkili kullanicilar
authorized_users: set[int] = set()

# Per-guild son 100 log
watcher_logs: dict[int, deque] = {}
MAX_LOGS = 100


def load_watchers():
    global watchers, watcher_counter
    if os.path.exists(WATCHERS_FILE):
        with open(WATCHERS_FILE, "r") as f:
            data = json.load(f)
        watchers = {int(k): v for k, v in data.get("watchers", {}).items()}
        watcher_counter = data.get("counter", 0)


def save_watchers():
    with open(WATCHERS_FILE, "w") as f:
        json.dump({"watchers": watchers, "counter": watcher_counter}, f, indent=2)


def load_authorized():
    global authorized_users
    if os.path.exists(AUTHORIZED_FILE):
        with open(AUTHORIZED_FILE, "r") as f:
            data = json.load(f)
        authorized_users = {int(u) for u in data.get("users", [])}


def save_authorized():
    with open(AUTHORIZED_FILE, "w") as f:
        json.dump({"users": sorted(authorized_users)}, f, indent=2)


def is_authorized(user_id: int) -> bool:
    return user_id == OWNER_ID or user_id in authorized_users


def get_logs(guild_id: int) -> deque:
    if guild_id not in watcher_logs:
        watcher_logs[guild_id] = deque(maxlen=MAX_LOGS)
    return watcher_logs[guild_id]


def add_log(guild_id: int, log_type: str, watcher_id: int, **kwargs):
    now = datetime.now(timezone.utc)
    entry = {
        "time": now.isoformat(),
        "type": log_type,
        "watcher_id": watcher_id,
        **kwargs,
    }
    get_logs(guild_id).append(entry)

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


@bot.check
async def globally_authorized(ctx: commands.Context) -> bool:
    return is_authorized(ctx.author.id)


@bot.event
async def on_command_error(ctx: commands.Context, error: Exception):
    if isinstance(error, (commands.CheckFailure, commands.CommandNotFound)):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        return await ctx.send(f"Eksik argüman: `{error.param.name}`")
    if isinstance(error, commands.BadArgument):
        return await ctx.send(f"Geçersiz argüman: {error}")
    raise error


@bot.event
async def on_ready():
    load_watchers()
    load_authorized()
    print(
        f"{bot.user} olarak giris yapildi! "
        f"Owner: {OWNER_ID} | Yetkili kullanici: {len(authorized_users)}"
    )


@bot.event
async def on_message(message: discord.Message):
    if message.author.id == bot.user.id:
        return

    # Komut isleme: yetki global check ile uygulanir
    if not message.author.bot:
        await bot.process_commands(message)

    # Takip kontrolu (bot mesajlari dahil)
    if not message.guild:
        return

    guild_watchers = watchers.get(message.guild.id, [])
    if not guild_watchers:
        return

    full_content = message.content
    for embed in message.embeds:
        if embed.description:
            full_content += " " + embed.description
        if embed.title:
            full_content += " " + embed.title
        for field in embed.fields:
            full_content += " " + field.name + " " + field.value

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

    for watcher in guild_watchers:
        if watcher["channel_id"] != message.channel.id:
            continue

        keywords = [k.strip() for k in watcher["keyword"].split(",")]
        matched = [k for k in keywords if k and k in full_content]
        if not matched:
            continue

        add_log(
            message.guild.id, "match", watcher["id"],
            channel=message.channel.name,
            author=str(message.author),
            matched=matched,
            content=full_content,
        )

        embeds_data = []
        for embed in message.embeds:
            embeds_data.append({
                "title": embed.title,
                "description": embed.description,
                "fields": [{"name": f.name, "value": f.value} for f in embed.fields],
            })

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
                    allow_redirects=False,
                ) as resp:
                    if resp.status in (301, 302, 307, 308):
                        redirect_url = resp.headers.get("Location", watcher["endpoint"])
                        async with session.post(
                            redirect_url,
                            json=payload,
                            timeout=aiohttp.ClientTimeout(total=10),
                        ) as resp2:
                            add_log(
                                message.guild.id, "sent", watcher["id"],
                                endpoint=watcher["endpoint"],
                                status=resp2.status,
                            )
                            return
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


@bot.command(name="helpwh", aliases=["yardimwh", "komutlarwh"])
async def help_command(ctx: commands.Context):
    embed = discord.Embed(
        title="Webhook Bot — Komutlar",
        description="Sadece yetkili kullanıcılar komut çalıştırabilir. Tüm komutlar `wh` eki ile biter.",
        color=0x2ECC71,
    )

    embed.add_field(
        name="Kanal Takip",
        value=(
            "`!takipwh <#kanal> <kelime> <endpoint>` — Kanaldaki kelimeyi takip et\n"
            "`!takiplerwh` — Aktif takipleri listele (alias: `!watchlistwh`)\n"
            "`!takipkaldirwh <id>` — Takibi kaldır (alias: `!unwatchwh`)\n\n"
            "**Çoklu kelime:** Virgül ile ayır, herhangi biri geçerse tetiklenir\n"
            "**Tam ifade:** Tırnak içinde yaz, tüm ifade aranır"
        ),
        inline=False,
    )

    embed.add_field(
        name="Yetki Yönetimi (sadece bot sahibi)",
        value=(
            "`!yetkiverwh <user_id veya @kullanıcı>` — Yetki ver\n"
            "`!yetkialwh <user_id veya @kullanıcı>` — Yetki kaldır\n"
            "`!yetkilerwh` — Yetkili kullanıcıları listele"
        ),
        inline=False,
    )

    embed.add_field(
        name="Genel",
        value=(
            "`!helpwh` — Bu mesajı göster (alias: `!yardimwh`, `!komutlarwh`)\n"
            "`!configwh` — Sunucudaki takip durumu (alias: `!ayarlarwh`, `!durumwh`)\n"
            "`!loglarwh [adet]` — Son takip logları (alias: `!logswh`)"
        ),
        inline=False,
    )

    embed.add_field(
        name="Örnek Kullanım",
        value=(
            "```\n"
            "!takipwh #genel indirim https://api.example.com/hook\n"
            "!takipwh #genel indirim,kampanya,firsat https://api.example.com/hook\n"
            '!takipwh #genel "buyuk indirim" https://api.example.com/hook\n'
            "!yetkiverwh 123456789012345678\n"
            "```"
        ),
        inline=False,
    )

    await ctx.send(embed=embed)


@bot.command(name="configwh", aliases=["ayarlarwh", "durumwh"])
async def config(ctx: commands.Context):
    guild_id = ctx.guild.id
    embed = discord.Embed(
        title="Sunucu Konfigürasyonu",
        description=f"**{ctx.guild.name}** için mevcut ayarlar",
        color=0x3498DB,
    )

    guild_watchers = watchers.get(guild_id, [])
    if guild_watchers:
        watcher_lines = []
        for w in guild_watchers:
            channel = bot.get_channel(w["channel_id"])
            ch_name = channel.mention if channel else "(silinmis kanal)"
            watcher_lines.append(
                f"`#{w['id']}` {ch_name} — Kelime: `{w['keyword']}`\n"
                f"  Endpoint: `{w['endpoint']}`"
            )
        watcher_text = "\n".join(watcher_lines)
    else:
        watcher_text = "Aktif takip yok\n`!takipwh #kanal kelime endpoint` ile ekle"

    embed.add_field(
        name=f"Kanal Takipleri ({len(guild_watchers)} aktif)",
        value=watcher_text,
        inline=False,
    )

    embed.set_footer(text="!helpwh ile tüm komutları gör")
    await ctx.send(embed=embed)


@bot.command(name="takipwh")
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


@bot.command(name="takiplerwh", aliases=["watchlistwh"])
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


@bot.command(name="takipkaldirwh", aliases=["unwatchwh"])
async def remove_watcher(ctx: commands.Context, watcher_id: int):
    guild_watchers = watchers.get(ctx.guild.id, [])
    for i, w in enumerate(guild_watchers):
        if w["id"] == watcher_id:
            guild_watchers.pop(i)
            save_watchers()
            add_log(ctx.guild.id, "removed", watcher_id)
            return await ctx.send(f"Takip **#{watcher_id}** kaldirildi.")

    await ctx.send(f"Takip **#{watcher_id}** bulunamadi.")


@bot.command(name="loglarwh", aliases=["logswh"])
async def show_logs(ctx: commands.Context, adet: int = 10):
    adet = min(adet, 25)
    logs = get_logs(ctx.guild.id)

    if not logs:
        return await ctx.send("Henüz log kaydı yok.")

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
        time_str = log["time"][11:19]
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
    await ctx.send(embed=embed)


def _resolve_user_id(arg: str) -> int | None:
    arg = arg.strip()
    if arg.startswith("<@") and arg.endswith(">"):
        arg = arg[2:-1].lstrip("!")
    try:
        return int(arg)
    except ValueError:
        return None


@bot.command(name="yetkiverwh")
async def grant(ctx: commands.Context, target: str):
    if ctx.author.id != OWNER_ID:
        return await ctx.send("Bu komutu sadece bot sahibi kullanabilir.")

    user_id = _resolve_user_id(target)
    if user_id is None:
        return await ctx.send("Geçerli bir user ID veya mention ver.")

    if user_id == OWNER_ID:
        return await ctx.send("Sahibi zaten her zaman yetkili.")

    if user_id in authorized_users:
        return await ctx.send(f"`{user_id}` zaten yetkili.")

    authorized_users.add(user_id)
    save_authorized()
    await ctx.send(f"`{user_id}` artık yetkili. Toplam {len(authorized_users)} yetkili kullanıcı.")


@bot.command(name="yetkialwh")
async def revoke(ctx: commands.Context, target: str):
    if ctx.author.id != OWNER_ID:
        return await ctx.send("Bu komutu sadece bot sahibi kullanabilir.")

    user_id = _resolve_user_id(target)
    if user_id is None:
        return await ctx.send("Geçerli bir user ID veya mention ver.")

    if user_id == OWNER_ID:
        return await ctx.send("Sahibinin yetkisi kaldirilamaz.")

    if user_id not in authorized_users:
        return await ctx.send(f"`{user_id}` zaten yetkili degil.")

    authorized_users.discard(user_id)
    save_authorized()
    await ctx.send(f"`{user_id}` icin yetki kaldirildi. Kalan {len(authorized_users)} yetkili kullanici.")


@bot.command(name="yetkilerwh")
async def list_authorized(ctx: commands.Context):
    lines = [f"**Sahibi:** `{OWNER_ID}`"]
    if authorized_users:
        lines.append(f"\n**Yetkili kullanicilar ({len(authorized_users)}):**")
        for uid in sorted(authorized_users):
            lines.append(f"`{uid}`")
    else:
        lines.append("\nBaska yetkili kullanici yok.")
    await ctx.send("\n".join(lines))


bot.run(os.getenv("DISCORD_TOKEN"))
