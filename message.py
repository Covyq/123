import os
import time
import heapq
import asyncio
import aiosqlite
import discord

# ─────────────────────────────────────────────
GUILD_ID = 419565206335651840

ALLOWED_ROLE_IDS = [
    1493199914572972032,
    123456789012345678,
    987654321098765432
]

DB_PATH = "timers.db"

bot = discord.Bot(intents=discord.Intents.all(), debug_guilds=[GUILD_ID])

# ─────────────────────────────────────────────
# MEMORY INDEX
CHANNELS = {}     # {guild: {type: channel_id}}
TIMERS = {}       # {message_id: data}
HEAP = []         # (time_end, message_id)

db = None

# ─────────────────────────────────────────────
# DB
async def init_db():
    global db
    db = await aiosqlite.connect(DB_PATH)

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


# ─────────────────────────────────────────────
# LOAD STATE
async def load_state():
    global TIMERS, HEAP, CHANNELS

    TIMERS.clear()
    HEAP.clear()
    CHANNELS.clear()

    async with db.execute("SELECT * FROM timers") as cur:
        rows = await cur.fetchall()

    for msg_id, guild_id, channel_id, author, text, time_end, type_ in rows:
        data = {
            "message_id": msg_id,
            "guild_id": guild_id,
            "channel_id": channel_id,
            "author": author,
            "text": text,
            "time_end": time_end,
            "type": type_
        }

        TIMERS[msg_id] = data
        heapq.heappush(HEAP, (time_end, msg_id))

    async with db.execute("SELECT guild_id, type, channel_id FROM channels") as cur:
        rows = await cur.fetchall()

    for g, t, c in rows:
        CHANNELS.setdefault(g, {})[t] = c


# ─────────────────────────────────────────────
# CHANNELS
def get_channel(guild, type_):
    return CHANNELS.get(guild, {}).get(type_)


def set_channel(guild, type_, channel_id):
    CHANNELS.setdefault(guild, {})[type_] = channel_id

    async def _save():
        await db.execute("""
        INSERT INTO channels VALUES (?, ?, ?)
        ON CONFLICT(guild_id, type)
        DO UPDATE SET channel_id=excluded.channel_id
        """, (guild, type_, channel_id))
        await db.commit()

    asyncio.create_task(_save())


# ─────────────────────────────────────────────
def has_access(member):
    return member.guild_permissions.administrator or any(
        r.id in ALLOWED_ROLE_IDS for r in member.roles
    )


# ─────────────────────────────────────────────
# TIMER CORE
async def save_timer(t):
    TIMERS[t["message_id"]] = t
    heapq.heappush(HEAP, (t["time_end"], t["message_id"]))

    await db.execute("""
    INSERT OR REPLACE INTO timers VALUES (?, ?, ?, ?, ?, ?, ?)
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


async def delete_timer(msg_id):
    TIMERS.pop(msg_id, None)

    await db.execute("DELETE FROM timers WHERE message_id=?", (msg_id,))
    await db.commit()


# ─────────────────────────────────────────────
# BUTTON ROUTER (STABLE)
class GlobalView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Удалить", style=discord.ButtonStyle.red, custom_id="del")
    async def delete(self, interaction, button):
        await interaction.response.defer(ephemeral=True)

        await delete_timer(interaction.message.id)

        try:
            await interaction.message.delete()
        except:
            pass

        await interaction.followup.send("✅ удалено", ephemeral=True)


    @discord.ui.button(label="Обновить склад", style=discord.ButtonStyle.green, custom_id="skl")
    async def sklad(self, interaction, button):
        await interaction.response.defer(ephemeral=True)

        new_end = int(time.time()) + 172800

        base = interaction.message.content.split("\n⏰")[0]

        await interaction.message.edit(
            content=f"{base}\n\n⏰ обновлено 48ч (<t:{new_end}:R>)"
        )

        await interaction.followup.send("✅ обновлено", ephemeral=True)


# ─────────────────────────────────────────────
# ENTERPRISE SCHEDULER (NO POLLING LOOP)
async def scheduler():
    while True:
        if not HEAP:
            await asyncio.sleep(2)
            continue

        time_end, msg_id = HEAP[0]
        now = int(time.time())

        sleep_time = max(0, time_end - now)
        await asyncio.sleep(min(sleep_time, 60))

        now = int(time.time())

        while HEAP and HEAP[0][0] <= now:
            _, mid = heapq.heappop(HEAP)

            t = TIMERS.get(mid)
            if not t:
                continue

            channel = bot.get_channel(t["channel_id"])
            if not channel:
                await delete_timer(mid)
                continue

            try:
                msg = await channel.fetch_message(mid)
            except:
                await delete_timer(mid)
                continue

            if t["type"] == "mpf":
                content = f"{t['text']}\n✅ можно забирать"
            elif t["type"] == "sklad":
                content = f"{t['text']}\n\n⏰ склад завершён"
            else:
                content = f"✅ {t['text']} завершён"

            try:
                await msg.edit(content=content)
            except:
                pass

            await delete_timer(mid)


# ─────────────────────────────────────────────
@bot.event
async def on_ready():
    global db

    print(f"Logged in as {bot.user}")

    await init_db()
    await load_state()

    bot.add_view(GlobalView())

    asyncio.create_task(scheduler())


# ─────────────────────────────────────────────
# COMMANDS

@bot.slash_command(guild_ids=[GUILD_ID], name="таймер")
async def timer(ctx, название: str, hours: int = 0, minutes: int = 0):

    end = int(time.time()) + hours * 3600 + minutes * 60

    msg = await ctx.send(
        f"👤 {ctx.author.mention}\n📌 {название}\n⏰ <t:{end}:R>",
        view=GlobalView()
    )

    await save_timer({
        "message_id": msg.id,
        "guild_id": ctx.guild.id,
        "channel_id": ctx.channel.id,
        "author": ctx.author.id,
        "text": название,
        "time_end": end,
        "type": "simple"
    })

    await ctx.respond("✅ ok", ephemeral=True)


@bot.slash_command(guild_ids=[GUILD_ID], name="склад")
async def sklad(ctx, гекс: str, регион: str, склад: str, пароль: str):

    end = int(time.time()) + 172800

    text = (
        f"👤 {ctx.author.display_name}\n"
        f"📦 {гекс}\n"
        f"📦 {регион}\n"
        f"📦 {склад}\n"
        f"🔑 {пароль}"
    )

    msg = await ctx.send(
        f"{text}\n\n⏰ 48ч (<t:{end}:R>)",
        view=GlobalView()
    )

    await save_timer({
        "message_id": msg.id,
        "guild_id": ctx.guild.id,
        "channel_id": ctx.channel.id,
        "author": ctx.author.id,
        "text": text,
        "time_end": end,
        "type": "sklad"
    })

    await ctx.respond("✅ ok", ephemeral=True)


@bot.slash_command(guild_ids=[GUILD_ID], name="мпф")
async def mpf(ctx, что: str, ящики: int, hours: int = 0):

    ch = get_channel(ctx.guild.id, "mpf")

    if not ch:
        return await ctx.respond("❌ нет канала", ephemeral=True)

    channel = bot.get_channel(ch)

    end = int(time.time()) + hours * 3600

    text = f"👤 {ctx.author.display_name}\n📦 {что}\n📦 {ящики}"

    msg = await channel.send(f"{text}\n⏰ <t:{end}:R>")

    await save_timer({
        "message_id": msg.id,
        "guild_id": ctx.guild.id,
        "channel_id": channel.id,
        "author": ctx.author.id,
        "text": text,
        "time_end": end,
        "type": "mpf"
    })

    await ctx.respond("✅ ok", ephemeral=True)


@bot.slash_command(guild_ids=[GUILD_ID], name="setmpf")
async def setmpf(ctx, channel: discord.TextChannel):

    if not has_access(ctx.author):
        return await ctx.respond("❌ no", ephemeral=True)

    set_channel(ctx.guild.id, "mpf", channel.id)
    await ctx.respond("✅ saved", ephemeral=True)


@bot.slash_command(guild_ids=[GUILD_ID], name="setsklad")
async def setsklad(ctx, channel: discord.TextChannel):

    if not has_access(ctx.author):
        return await ctx.respond("❌ no", ephemeral=True)

    set_channel(ctx.guild.id, "sklad", channel.id)
    await ctx.respond("✅ saved", ephemeral=True)


@bot.slash_command(guild_ids=[GUILD_ID], name="setsimple")
async def setsimple(ctx, channel: discord.TextChannel):

    if not has_access(ctx.author):
        return await ctx.respond("❌ no", ephemeral=True)

    set_channel(ctx.guild.id, "simple", channel.id)
    await ctx.respond("✅ saved", ephemeral=True)


# ─────────────────────────────────────────────
bot.run(os.environ["DISCORD_BOT_TOKEN"])
