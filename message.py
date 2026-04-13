import os
import datetime
import traceback
import discord
from discord.ext import tasks
from peewee import *

# ─── НАСТРОЙКИ ─────────────────────────────────────────────
GUILD_ID = 419565206335651840
allowed_role_id = 1493199914572972032

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
    channel_id = BigIntegerField()
    message_id = BigIntegerField()
    text = TextField()
    time_end = BigIntegerField()
    author = TextField()
    created = BigIntegerField()

db.connect(reuse_if_open=True)
db.create_tables([ChannelConfig, Timer])

# ─── УТИЛИТЫ ───────────────────────────────────────────────
def has_access(member):
    return (
        member.guild_permissions.administrator or
        any(r.id == allowed_role_id for r in member.roles)
    )

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

# ─── LOOP ─────────────────────────────────────────────────
@tasks.loop(seconds=5)
async def timer_loop():
    try:
        now = int(datetime.datetime.utcnow().timestamp())
        timers = list(Timer.select().where(Timer.time_end < now))

        for t in timers:
            guild = bot.get_guild(t.guild_id)
            if not guild:
                continue

            channel = guild.get_channel(t.channel_id)
            if not channel:
                continue

            try:
                msg = await channel.fetch_message(t.message_id)
                await msg.edit(content=f"✅ {t.text}\n⏰ Завершено")
            except:
                pass

            t.delete_instance()

    except Exception:
        print(traceback.format_exc())

# ─── READY ────────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ Бот запущен: {bot.user}")

    if not timer_loop.is_running():
        timer_loop.start()

    try:
        await bot.sync_commands(guild_ids=[GUILD_ID])
        print("✅ Slash-команды синхронизированы")
    except Exception:
        print("❌ SYNC ERROR")
        print(traceback.format_exc())

    print("GUILDS:", [(g.name, g.id) for g in bot.guilds])

# ─── TEST ────────────────────────────────────────────────
@bot.slash_command(name="ping", guild_ids=[GUILD_ID])
async def ping(ctx):
    await ctx.respond("🏓 pong")

# ─── КАНАЛЫ ───────────────────────────────────────────────
@bot.slash_command(name="setskladchannel", guild_ids=[GUILD_ID])
async def setskladchannel(ctx, channel: discord.TextChannel):
    if not has_access(ctx.author):
        await ctx.respond("❌ Нет прав", ephemeral=True)
        return

    set_channel(ctx.guild.id, channel.id, "sklad")
    await ctx.respond(f"✅ Склад: {channel.mention}", ephemeral=True)


@bot.slash_command(name="setsimplechannel", guild_ids=[GUILD_ID])
async def setsimplechannel(ctx, channel: discord.TextChannel):
    if not has_access(ctx.author):
        await ctx.respond("❌ Нет прав", ephemeral=True)
        return

    set_channel(ctx.guild.id, channel.id, "simple")
    await ctx.respond(f"✅ Таймер: {channel.mention}", ephemeral=True)

# ─── СКЛАД ────────────────────────────────────────────────
@bot.slash_command(name="склад", guild_ids=[GUILD_ID])
async def sklad(ctx,
    hex_val: str,
    region: str,
    warehouse: str,
    password: str):

    channel_id = get_channel(ctx.guild.id, "sklad")
    if channel_id and ctx.channel.id != channel_id:
        await ctx.respond("❌ Не тот канал", ephemeral=True)
        return

    now = datetime.datetime.utcnow()
    end = int((now + datetime.timedelta(days=2)).timestamp())

    text = (
        f"👤 {ctx.author.display_name}\n"
        f"**Гекс:** {hex_val}\n"
        f"**Регион:** {region}\n"
        f"**Склад:** {warehouse}\n"
        f"**Пароль:** {password}"
    )

    msg = await ctx.send(f"{text}\n⏰ <t:{end}:R>")

    Timer.create(
        guild_id=ctx.guild.id,
        channel_id=ctx.channel.id,
        message_id=msg.id,
        text=text,
        time_end=end,
        author=ctx.author.display_name,
        created=int(now.timestamp())
    )

    await ctx.respond("✅ Создано", ephemeral=True)

# ─── ТАЙМЕР ───────────────────────────────────────────────
@bot.slash_command(name="таймер", guild_ids=[GUILD_ID])
async def timer(ctx, text: str, seconds: int):

    channel_id = get_channel(ctx.guild.id, "simple")
    if channel_id and ctx.channel.id != channel_id:
        await ctx.respond("❌ Не тот канал", ephemeral=True)
        return

    now = datetime.datetime.utcnow()
    end = int((now + datetime.timedelta(seconds=seconds)).timestamp())

    msg = await ctx.send(f"⏳ {text}\n⏰ <t:{end}:R>")

    Timer.create(
        guild_id=ctx.guild.id,
        channel_id=ctx.channel.id,
        message_id=msg.id,
        text=text,
        time_end=end,
        author=ctx.author.display_name,
        created=int(now.timestamp())
    )

    await ctx.respond("✅ Таймер создан", ephemeral=True)

# ─── ЗАПУСК ───────────────────────────────────────────────
bot.run(os.environ.get("DISCORD_BOT_TOKEN"))
