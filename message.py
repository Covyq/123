import os
import time
import asyncio
import discord
import aiosqlite
from discord.ext import tasks
from discord.ui import View

# ─── CONFIG ─────────────────────────────────────────────
GUILD_ID = 419565206335651840

ALLOWED_ROLE_IDS = [
    1493199914572972032,
    123456789012345678,
    987654321098765432
]

bot = discord.Bot(
    intents=discord.Intents.all(),
    debug_guilds=[GUILD_ID]
)

DB_PATH = "timers.db"

# ─── MEMORY CACHE ───────────────────────────────────────
CHANNEL_CACHE = {"sklad": {}, "simple": {}, "mpf": {}}
TIMERS = {}

# ─── DB INIT ────────────────────────────────────────────
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS timers (
            message_id INTEGER PRIMARY KEY,
            guild_id INTEGER,
            channel_id INTEGER,
            author INTEGER,
            text TEXT,
            time_end INTEGER,
            type TEXT
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            guild_id INTEGER,
            type TEXT,
            channel_id INTEGER,
            PRIMARY KEY (guild_id, type)
        )
        """)

        await db.commit()

# ─── LOAD DATA ──────────────────────────────────────────
async def load_timers():
    global TIMERS
    TIMERS = {}

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM timers") as cur:
            async for row in cur:
                TIMERS[row[0]] = {
                    "message_id": row[0],
                    "guild_id": row[1],
                    "channel_id": row[2],
                    "author": row[3],
                    "text": row[4],
                    "time_end": row[5],
                    "type": row[6],
                }

async def load_channels():
    CHANNEL_CACHE["sklad"] = {}
    CHANNEL_CACHE["simple"] = {}
    CHANNEL_CACHE["mpf"] = {}

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT guild_id, type, channel_id FROM channels") as cur:
            async for guild_id, type_, channel_id in cur:
                CHANNEL_CACHE[type_][guild_id] = channel_id

# ─── DB HELPERS ─────────────────────────────────────────
async def save_timer(t):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT OR REPLACE INTO timers
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            t["message_id"],
            t["guild_id"],
            t["channel_id"],
            t["author"],
            t["text"],
            t["time_end"],
            t["type"]
        ))
        await db.commit()

async def delete_timer(message_id):
    TIMERS.pop(message_id, None)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM timers WHERE message_id=?", (message_id,))
        await db.commit()

async def set_channel_db(guild_id, type_, channel_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT INTO channels (guild_id, type, channel_id)
        VALUES (?, ?, ?)
        ON CONFLICT(guild_id, type)
        DO UPDATE SET channel_id=excluded.channel_id
        """, (guild_id, type_, channel_id))
        await db.commit()

    CHANNEL_CACHE[type_][guild_id] = channel_id

def get_channel(guild_id, type_):
    return CHANNEL_CACHE.get(type_, {}).get(guild_id)

# ─── PERMISSION ─────────────────────────────────────────
def has_access(member):
    return member.guild_permissions.administrator or any(
        r.id in ALLOWED_ROLE_IDS for r in member.roles
    )

# ─── VIEWS ──────────────────────────────────────────────
class TimerView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Удалить", style=discord.ButtonStyle.red)
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):

        await interaction.response.defer(ephemeral=True)

        try:
            msg = interaction.message
            if not msg:
                await interaction.followup.send("❌ Сообщение не найдено", ephemeral=True)
                return

            t = TIMERS.get(msg.id)
            if not t:
                await interaction.followup.send("❌ Таймер не найден", ephemeral=True)
                return

            if interaction.user.id != t["author"]:
                await interaction.followup.send("❌ Нет прав", ephemeral=True)
                return

            await delete_timer(msg.id)

            try:
                await msg.delete()
            except:
                pass

            await interaction.followup.send("✅ Удалено", ephemeral=True)

        except Exception as e:
            print("TimerView error:", e)


class SkladView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Обновить склад", style=discord.ButtonStyle.green)
    async def update(self, interaction: discord.Interaction, button: discord.ui.Button):

        t = TIMERS.get(interaction.message.id)
        if not t:
            await interaction.response.send_message("❌ Не найдено", ephemeral=True)
            return

        new_end = int(time.time()) + 48 * 3600
        t["time_end"] = new_end

        await save_timer(t)

        await interaction.message.edit(
            content=f"{t['text']}\n\n⏰ Обновлено: 48 часов (<t:{new_end}:R>)"
        )

        await interaction.response.send_message("✅ Обновлено", ephemeral=True)

    @discord.ui.button(label="Удалить", style=discord.ButtonStyle.red)
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):

        t = TIMERS.get(interaction.message.id)
        if not t or interaction.user.id != t["author"]:
            await interaction.response.send_message("❌ Нет прав", ephemeral=True)
            return

        await delete_timer(interaction.message.id)

        try:
            await interaction.message.delete()
        except:
            pass

        await interaction.response.send_message("✅ Удалено", ephemeral=True)

# ─── LOOP ───────────────────────────────────────────────
@tasks.loop(seconds=5)
async def checker():
    now = int(time.time())
    remove = []

    for msg_id, t in list(TIMERS.items()):
        if t["time_end"] > now:
            continue

        guild = bot.get_guild(t["guild_id"])
        channel = bot.get_channel(t["channel_id"])

        if not guild or not channel:
            remove.append(msg_id)
            continue

        try:
            msg = await channel.fetch_message(msg_id)
        except:
            msg = None

        if not msg:
            remove.append(msg_id)
            continue

        if t["type"] == "mpf":
            content = f"{t['text']}\n\n✅ Можно забирать"
        elif t["type"] == "sklad":
            content = f"{t['text']}\n\n⏰ Склад завершён"
        else:
            content = f"✅ {t['text']} завершён"

        try:
            await msg.edit(content=content)
        except:
            pass

        remove.append(msg_id)

    for i in remove:
        await delete_timer(i)

# ─── READY ──────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")

    await init_db()
    await load_timers()
    await load_channels()

    bot.add_view(TimerView())
    bot.add_view(SkladView())

    if not checker.is_running():
        checker.start()

# ─── COMMANDS ───────────────────────────────────────────

@bot.slash_command(name="таймер", guild_ids=[GUILD_ID])
async def timer(ctx, название: str, hours: int = 0, minutes: int = 0):

    if hours == 0 and minutes == 0:
        await ctx.respond("❌ Укажи время", ephemeral=True)
        return

    end = int(time.time()) + hours * 3600 + minutes * 60

    msg = await ctx.send(
        f"👤 {ctx.author.mention}\n📌 {название}\n⏰ <t:{end}:R>",
        view=TimerView()
    )

    t = {
        "message_id": msg.id,
        "guild_id": ctx.guild.id,
        "channel_id": ctx.channel.id,
        "author": ctx.author.id,
        "text": название,
        "time_end": end,
        "type": "simple"
    }

    TIMERS[msg.id] = t
    await save_timer(t)

    await ctx.respond("✅ Таймер создан", ephemeral=True)


@bot.slash_command(name="склад", guild_ids=[GUILD_ID])
async def sklad(ctx, гекс: str, регион: str, склад: str, пароль: str):

    channel_id = get_channel(ctx.guild.id, "sklad")
    if channel_id and ctx.channel.id != channel_id:
        await ctx.respond("❌ Не тот канал", ephemeral=True)
        return

    end = int(time.time()) + 48 * 3600

    text = (
        f"👤 {ctx.author.display_name}\n"
        f"**Гекс:** {гекс}\n"
        f"**Регион:** {регион}\n"
        f"**Склад:** {склад}\n"
        f"**Пароль:** {пароль}"
    )

    msg = await ctx.send(
        f"{text}\n\n⏰ 48 часов (<t:{end}:R>)",
        view=SkladView()
    )

    t = {
        "message_id": msg.id,
        "guild_id": ctx.guild.id,
        "channel_id": ctx.channel.id,
        "author": ctx.author.id,
        "text": text,
        "time_end": end,
        "type": "sklad"
    }

    TIMERS[msg.id] = t
    await save_timer(t)

    await ctx.respond("✅ Склад создан", ephemeral=True)


@bot.slash_command(name="мпф", guild_ids=[GUILD_ID])
async def mpf(ctx, что: str, ящики: int, hours: int = 0, minutes: int = 0):

    if hours == 0 and minutes == 0:
        await ctx.respond("❌ Укажи время", ephemeral=True)
        return

    channel_id = get_channel(ctx.guild.id, "mpf")
    if not channel_id:
        await ctx.respond("❌ MPF не настроен", ephemeral=True)
        return

    channel = bot.get_channel(channel_id)
    if not channel:
        await ctx.respond("❌ Канал не найден", ephemeral=True)
        return

    end = int(time.time()) + hours * 3600 + minutes * 60

    text = (
        f"👤 {ctx.author.display_name}\n"
        f"📦 {что}\n"
        f"📦 Ящики: {ящики}"
    )

    msg = await channel.send(
        f"{text}\n⏰ <t:{end}:R>",
        view=TimerView()
    )

    t = {
        "message_id": msg.id,
        "guild_id": ctx.guild.id,
        "channel_id": channel.id,
        "author": ctx.author.id,
        "text": text,
        "time_end": end,
        "type": "mpf"
    }

    TIMERS[msg.id] = t
    await save_timer(t)

    await ctx.respond("✅ MPF создан", ephemeral=True)


@bot.slash_command(name="setskladchannel", guild_ids=[GUILD_ID])
async def setskladchannel(ctx, channel: discord.TextChannel):
    if not has_access(ctx.author):
        await ctx.respond("❌ Нет прав", ephemeral=True)
        return

    await set_channel_db(ctx.guild.id, "sklad", channel.id)
    await ctx.respond(f"✅ Склад канал: {channel.mention}", ephemeral=True)


@bot.slash_command(name="setsimpletimer", guild_ids=[GUILD_ID])
async def setsimpletimer(ctx, channel: discord.TextChannel):
    if not has_access(ctx.author):
        await ctx.respond("❌ Нет прав", ephemeral=True)
        return

    await set_channel_db(ctx.guild.id, "simple", channel.id)
    await ctx.respond(f"✅ Таймер канал: {channel.mention}", ephemeral=True)


@bot.slash_command(name="setmpfchat", guild_ids=[GUILD_ID])
async def setmpfchat(ctx, thread_id: str):

    if not has_access(ctx.author):
        await ctx.respond("❌ Нет прав", ephemeral=True)
        return

    try:
        thread_id = int(thread_id)
    except:
        await ctx.respond("❌ Неверный ID", ephemeral=True)
        return

    await set_channel_db(ctx.guild.id, "mpf", thread_id)
    await ctx.respond(f"✅ MPF ветка: `{thread_id}`", ephemeral=True)

# ─── RUN ────────────────────────────────────────────────
bot.run(os.environ["DISCORD_BOT_TOKEN"])
