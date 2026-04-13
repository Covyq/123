import os
import datetime
import traceback
import discord
from discord.ext import tasks
from peewee import *

# ─── НАСТРОЙКИ ─────────────────────────────────────────────
GUILD_ID = 419565206335651840
ALLOWED_ROLE_ID = 1493199914572972032  # 👈 твоя роль

bot = discord.Bot(
    intents=discord.Intents.all(),
    debug_guilds=[GUILD_ID]
)

db = SqliteDatabase("TimerDataBase.db")

# ─── БАЗА ──────────────────────────────────────────────────
class BaseModel(Model):
    guild_id = BigIntegerField()

    class Meta:
        database = db

class ChannelConfig(BaseModel):
    channel_id = BigIntegerField()
    type = TextField()

class Timer(BaseModel):
    guild_id = BigIntegerField()
    channel_id = BigIntegerField()
    message_id = BigIntegerField()
    text = TextField()
    time_end = BigIntegerField()

db.connect(reuse_if_open=True)
db.create_tables([ChannelConfig, Timer])

# ─── ПРАВА ────────────────────────────────────────────────
def has_access(member):
    return (
        member.guild_permissions.administrator or
        any(r.id == ALLOWED_ROLE_ID for r in member.roles)
    )

# ─── КАНАЛЫ ────────────────────────────────────────────────
def set_channel(guild_id, channel_id, type_):
    row = ChannelConfig.get_or_none(
        (ChannelConfig.guild_id == guild_id) &
        (ChannelConfig.type == type_)
    )
    if row:
        row.channel_id = channel_id
        row.save()
    else:
        ChannelConfig.create(
            guild_id=guild_id,
            channel_id=channel_id,
            type=type_
        )

def get_channel(guild_id, type_):
    row = ChannelConfig.get_or_none(
        (ChannelConfig.guild_id == guild_id) &
        (ChannelConfig.type == type_)
    )
    return row.channel_id if row else None

# ─── /SET SIMPLE TIMER CHANNEL ─────────────────────────────
@bot.slash_command(name="setsimpletimer", guild_ids=[GUILD_ID])
async def setsimpletimer(ctx, channel: discord.TextChannel):

    if not has_access(ctx.author):
        await ctx.respond("❌ Нет прав", ephemeral=True)
        return

    set_channel(ctx.guild.id, channel.id, "simple")
    await ctx.respond(f"✅ Канал таймеров: {channel.mention}", ephemeral=True)

# ─── /SET SKLAD CHANNEL ────────────────────────────────────
@bot.slash_command(name="setskladchannel", guild_ids=[GUILD_ID])
async def setskladchannel(ctx, channel: discord.TextChannel):

    if not has_access(ctx.author):
        await ctx.respond("❌ Нет прав", ephemeral=True)
        return

    set_channel(ctx.guild.id, channel.id, "sklad")
    await ctx.respond(f"✅ Канал складов: {channel.mention}", ephemeral=True)

# ─── /ТАЙМЕР ───────────────────────────────────────────────
@bot.slash_command(name="таймер", guild_ids=[GUILD_ID])
async def timer(
    ctx,
    text: str,
    days: int = 0,
    hours: int = 0,
    seconds: int = 0
):

    channel_id = get_channel(ctx.guild.id, "simple")
    if channel_id and ctx.channel.id != channel_id:
        await ctx.respond("❌ Не тот канал", ephemeral=True)
        return

    now = datetime.datetime.utcnow()
    end = now + datetime.timedelta(days=days, hours=hours, seconds=seconds)
    end_ts = int(end.timestamp())

    msg = await ctx.send(f"⏳ {text}\n⏰ <t:{end_ts}:R>")

    Timer.create(
        guild_id=ctx.guild.id,
        channel_id=ctx.channel.id,
        message_id=msg.id,
        text=text,
        time_end=end_ts
    )

    await ctx.respond("✅ Таймер создан", ephemeral=True)

# ─── /СКЛАД ────────────────────────────────────────────────
@bot.slash_command(name="склад", guild_ids=[GUILD_ID])
async def sklad(
    ctx,
    гекс: str,
    регион: str,
    склад: str,
    пароль: str
):

    channel_id = get_channel(ctx.guild.id, "sklad")
    if channel_id and ctx.channel.id != channel_id:
        await ctx.respond("❌ Не тот канал", ephemeral=True)
        return

    now = datetime.datetime.utcnow()
    end_ts = int((now + datetime.timedelta(hours=48)).timestamp())

    text = (
        f"👤 {ctx.author.display_name}\n"
        f"**Гекс:** {гекс}\n"
        f"**Регион:** {регион}\n"
        f"**Склад:** {склад}\n"
        f"**Пароль:** {пароль}"
    )

    msg = await ctx.send(f"{text}\n⏰ 48 часов (<t:{end_ts}:R>)")

    Timer.create(
        guild_id=ctx.guild.id,
        channel_id=ctx.channel.id,
        message_id=msg.id,
        text=text,
        time_end=end_ts
    )

    await ctx.respond("✅ Склад создан", ephemeral=True)

# ─── LOOP (очистка завершённых) ───────────────────────────
@tasks.loop(seconds=5)
async def loop():
    now = int(datetime.datetime.utcnow().timestamp())

    timers = Timer.select().where(Timer.time_end < now)

    for t in timers:
        try:
            guild = bot.get_guild(t.guild_id)
            if not guild:
                continue

            channel = guild.get_channel(t.channel_id)
            if not channel:
                continue

            msg = await channel.fetch_message(t.message_id)
            await msg.edit(content=f"✅ {t.text}\n⏰ Завершено")

        except Exception:
            pass

        t.delete_instance()

# ─── READY ────────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ Бот запущен: {bot.user}")

    if not loop.is_running():
        loop.start()

# ─── ЗАПУСК ───────────────────────────────────────────────
bot.run(os.environ.get("DISCORD_BOT_TOKEN"))
