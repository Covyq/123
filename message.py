import os
import time
import aiosqlite
import discord
from discord.ext import tasks

# ─── CONFIG ─────────────────────────────────────────────
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

# ─── CACHE ──────────────────────────────────────────────
CHANNEL_CACHE = {"sklad": {}, "simple": {}, "mpf": {}}

# ─── DB ─────────────────────────────────────────────────
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
        await db.commit()


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


async def delete_timer_db(message_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM timers WHERE message_id=?", (message_id,))
        await db.commit()


async def get_timers():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM timers") as cur:
            return await cur.fetchall()


# ─── CHANNELS ───────────────────────────────────────────
def set_channel(guild_id, type_, channel_id):
    CHANNEL_CACHE[type_][guild_id] = channel_id

    async def _save():
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            INSERT INTO channels (guild_id, type, channel_id)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id, type)
            DO UPDATE SET channel_id=excluded.channel_id
            """, (guild_id, type_, channel_id))
            await db.commit()

    bot.loop.create_task(_save())


def get_channel(guild_id, type_):
    return CHANNEL_CACHE.get(type_, {}).get(guild_id)


# ─── PERMISSIONS ────────────────────────────────────────
def has_access(member):
    return member.guild_permissions.administrator or any(
        r.id in ALLOWED_ROLE_IDS for r in member.roles
    )


# ─── GLOBAL BUTTON ROUTER (🔥 FIX 100%) ─────────────────
class GlobalView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    # ── DELETE TIMER ───────────────────────────────────
    @discord.ui.button(
        label="Удалить",
        style=discord.ButtonStyle.red,
        custom_id="timer_delete_btn"
    )
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)

            msg_id = interaction.message.id

            await delete_timer_db(msg_id)

            try:
                await interaction.message.delete()
            except:
                pass

            await interaction.followup.send("✅ Удалено", ephemeral=True)

        except:
            await interaction.followup.send("❌ Ошибка кнопки", ephemeral=True)

    # ── SKLAD UPDATE ───────────────────────────────────
    @discord.ui.button(
        label="Обновить склад",
        style=discord.ButtonStyle.green,
        custom_id="sklad_update_btn"
    )
    async def sklad(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)

            new_end = int(time.time()) + 48 * 3600

            content = interaction.message.content.split("\n⏰")[0]

            await interaction.message.edit(
                content=f"{content}\n\n⏰ Обновлено: 48 часов (<t:{new_end}:R>)"
            )

            await interaction.followup.send("✅ Обновлено", ephemeral=True)

        except:
            await interaction.followup.send("❌ Ошибка кнопки", ephemeral=True)


# ─── CHECKER LOOP ───────────────────────────────────────
@tasks.loop(seconds=5)
async def checker():
    now = int(time.time())
    timers = await get_timers()

    for t in timers:
        msg_id, guild_id, channel_id, author, text, time_end, type_ = t

        if time_end > now:
            continue

        channel = bot.get_channel(channel_id)
        if not channel:
            await delete_timer_db(msg_id)
            continue

        try:
            msg = await channel.fetch_message(msg_id)
        except:
            await delete_timer_db(msg_id)
            continue

        if type_ == "mpf":
            content = f"{text}\n✅ Можно забирать"
        elif type_ == "sklad":
            content = f"{text}\n\n⏰ Склад завершён"
        else:
            content = f"✅ {text} завершён"

        try:
            await msg.edit(content=content)
        except:
            pass

        await delete_timer_db(msg_id)


# ─── READY ──────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

    await init_db()

    # 🔥 CRITICAL FIX
    bot.add_view(GlobalView())

    if not checker.is_running():
        checker.start()


# ─── COMMANDS ──────────────────────────────────────────

@bot.slash_command(name="таймер", guild_ids=[GUILD_ID])
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

    await ctx.respond("✅ Таймер создан", ephemeral=True)


@bot.slash_command(name="склад", guild_ids=[GUILD_ID])
async def sklad(ctx, гекс: str, регион: str, склад: str, пароль: str):

    end = int(time.time()) + 48 * 3600

    text = (
        f"👤 {ctx.author.display_name}\n"
        f"📦 {гекс}\n"
        f"📦 Ящики: {регион}\n"
        f"📦 Склад: {склад}\n"
        f"🔑 {пароль}"
    )

    msg = await ctx.send(
        f"{text}\n\n⏰ 48 часов (<t:{end}:R>)",
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

    await ctx.respond("✅ Склад создан", ephemeral=True)


@bot.slash_command(name="мпф", guild_ids=[GUILD_ID])
async def mpf(ctx, что: str, ящики: int, hours: int = 0, minutes: int = 0):

    channel_id = get_channel(ctx.guild.id, "mpf")

    if not channel_id:
        return await ctx.respond("❌ MPF не настроен", ephemeral=True)

    channel = bot.get_channel(channel_id)

    if not channel:
        return await ctx.respond("❌ Канал не найден", ephemeral=True)

    end = int(time.time()) + hours * 3600 + minutes * 60

    text = (
        f"👤 {ctx.author.display_name}\n"
        f"📦 {что}\n"
        f"📦 Ящики: {ящики}"
    )

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

    await ctx.respond("✅ MPF создан", ephemeral=True)


@bot.slash_command(name="setskladchannel", guild_ids=[GUILD_ID])
async def setskladchannel(ctx, channel: discord.TextChannel):

    if not has_access(ctx.author):
        return await ctx.respond("❌ Нет прав", ephemeral=True)

    set_channel(ctx.guild.id, "sklad", channel.id)
    await ctx.respond("✅ Канал склада установлен", ephemeral=True)


@bot.slash_command(name="setsimpletimer", guild_ids=[GUILD_ID])
async def setsimpletimer(ctx, channel: discord.TextChannel):

    if not has_access(ctx.author):
        return await ctx.respond("❌ Нет прав", ephemeral=True)

    set_channel(ctx.guild.id, "simple", channel.id)
    await ctx.respond("✅ Канал таймеров установлен", ephemeral=True)


@bot.slash_command(name="setmpfchat", guild_ids=[GUILD_ID])
async def setmpfchat(ctx, thread_id: str):

    if not has_access(ctx.author):
        return await ctx.respond("❌ Нет прав", ephemeral=True)

    set_channel(ctx.guild.id, "mpf", int(thread_id))
    await ctx.respond("✅ MPF канал установлен", ephemeral=True)


# ─── RUN ────────────────────────────────────────────────
bot.run(os.environ["DISCORD_BOT_TOKEN"])
