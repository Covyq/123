import os
import time
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

bot = discord.Bot(
    intents=discord.Intents.all(),
    debug_guilds=[GUILD_ID]
)

# ─────────────────────────────────────────────
CHANNELS = {}   # {guild: {type: channel_id}}
TIMERS = {}     # {message_id: data}
db = None

# ─────────────────────────────────────────────
# SAFE HELPERS
async def safe_defer(interaction):
    try:
        await interaction.response.defer(ephemeral=True)
    except:
        pass


async def safe_send(interaction, text):
    try:
        await interaction.followup.send(text, ephemeral=True)
    except:
        pass


async def safe_edit(message, content):
    try:
        await message.edit(content=content)
    except:
        pass


async def safe_delete(message):
    try:
        await message.delete()
    except:
        pass


# ─────────────────────────────────────────────
# DB INIT
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
    global CHANNELS, TIMERS

    CHANNELS.clear()
    TIMERS.clear()

    async with db.execute("SELECT * FROM timers") as cur:
        for row in await cur.fetchall():
            msg_id, guild_id, channel_id, author, text, end, type_ = row
            TIMERS[msg_id] = {
                "message_id": msg_id,
                "guild_id": guild_id,
                "channel_id": channel_id,
                "author": author,
                "text": text,
                "time_end": end,
                "type": type_
            }

    async with db.execute("SELECT guild_id, type, channel_id FROM channels") as cur:
        for g, t, c in await cur.fetchall():
            CHANNELS.setdefault(g, {})[t] = c


# ─────────────────────────────────────────────
# CHANNEL SYSTEM
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
# BUTTONS (BULLETPROOF)
class GlobalView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Удалить", style=discord.ButtonStyle.red, custom_id="del")
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        await safe_defer(interaction)

        try:
            await delete_timer(interaction.message.id)
            await safe_delete(interaction.message)
            await safe_send(interaction, "✅ удалено")
        except:
            pass


    @discord.ui.button(label="Обновить склад", style=discord.ButtonStyle.green, custom_id="skl")
    async def sklad(self, interaction: discord.Interaction, button: discord.ui.Button):
        await safe_defer(interaction)

        try:
            new_end = int(time.time()) + 172800
            base = interaction.message.content.split("\n⏰")[0]

            await safe_edit(
                interaction.message,
                f"{base}\n\n⏰ обновлено 48ч (<t:{new_end}:R>)"
            )

            await safe_send(interaction, "✅ обновлено")

        except:
            pass


# ─────────────────────────────────────────────
# SCHEDULER (NO CRASH VERSION)
async def scheduler():
    while True:
        try:
            now = int(time.time())

            for msg_id, t in list(TIMERS.items()):
                if t["time_end"] > now:
                    continue

                channel = bot.get_channel(t["channel_id"])
                if not channel:
                    await delete_timer(msg_id)
                    continue

                try:
                    msg = await channel.fetch_message(msg_id)
                except:
                    await delete_timer(msg_id)
                    continue

                if t["type"] == "mpf":
                    content = f"{t['text']}\n✅ можно забирать"
                elif t["type"] == "sklad":
                    content = f"{t['text']}\n\n⏰ склад завершён"
                else:
                    content = f"✅ {t['text']} завершён"

                await safe_edit(msg, content)
                await delete_timer(msg_id)

            await asyncio.sleep(10)

        except:
            await asyncio.sleep(5)


# ─────────────────────────────────────────────
@bot.event
async def on_ready():
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

    await ctx.respond("✅ создано", ephemeral=True)


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

    await ctx.respond("✅ создано", ephemeral=True)


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

    await ctx.respond("✅ создано", ephemeral=True)


# ─────────────────────────────────────────────
# ADMIN COMMANDS

@bot.slash_command(guild_ids=[GUILD_ID], name="setmpf")
async def setmpf(ctx, channel: discord.TextChannel):
    if not has_access(ctx.author):
        return await ctx.respond("❌ нет прав", ephemeral=True)

    set_channel(ctx.guild.id, "mpf", channel.id)
    await ctx.respond("✅ сохранено", ephemeral=True)


@bot.slash_command(guild_ids=[GUILD_ID], name="setsklad")
async def setsklad(ctx, channel: discord.TextChannel):
    if not has_access(ctx.author):
        return await ctx.respond("❌ нет прав", ephemeral=True)

    set_channel(ctx.guild.id, "sklad", channel.id)
    await ctx.respond("✅ сохранено", ephemeral=True)


@bot.slash_command(guild_ids=[GUILD_ID], name="setsimple")
async def setsimple(ctx, channel: discord.TextChannel):
    if not has_access(ctx.author):
        return await ctx.respond("❌ нет прав", ephemeral=True)

    set_channel(ctx.guild.id, "simple", channel.id)
    await ctx.respond("✅ сохранено", ephemeral=True)


# ─────────────────────────────────────────────
bot.run(os.environ["DISCORD_BOT_TOKEN"])
