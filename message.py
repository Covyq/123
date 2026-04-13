import os
import datetime
from peewee import *
import discord
from discord.ext import tasks
from discord.ui import View, Button
from discord.errors import NotFound, Forbidden, HTTPException

# ─── НАСТРОЙКИ ─────────────────────────────────────────────
GUILD_ID = 419565206335651840
allowed_role_id = 1493199914572972032

intents = discord.Intents.all()

bot = discord.Bot(
    intents=intents,
    debug_guilds=[GUILD_ID]  # 🔥 ключ к мгновенным slash-командам
)

db = SqliteDatabase('TimerDataBase.db')

# ─── БАЗА ──────────────────────────────────────────────────
class BaseModel(Model):
    guild_id = BigIntegerField()

    class Meta:
        database = db

class Timer(BaseModel):
    channel_id = BigIntegerField()
    message_id = BigIntegerField()
    text = TextField()
    time_end = BigIntegerField()
    author = TextField()
    created = BigIntegerField()

class ChannelConfig(BaseModel):
    channel_id = BigIntegerField()
    type = TextField()  # "sklad" или "simple"

db.create_tables([Timer, ChannelConfig])

# ─── УТИЛИТЫ ───────────────────────────────────────────────
def has_access(member):
    return (
        member.guild_permissions.administrator or
        any(r.id == allowed_role_id for r in member.roles)
    )

def get_channel(guild_id, type_):
    row = ChannelConfig.get_or_none(
        (ChannelConfig.guild_id == guild_id) &
        (ChannelConfig.type == type_)
    )
    return row.channel_id if row else None

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

# ─── LOOP ─────────────────────────────────────────────────
@tasks.loop(seconds=5)
async def timer_loop():
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
        except (NotFound, Forbidden, HTTPException):
            pass

        t.delete_instance()

# ─── READY ────────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ Бот {bot.user} запущен")

    print("Серверы:", [g.id for g in bot.guilds])

    if not timer_loop.is_running():
        timer_loop.start()

    try:
        synced = await bot.sync_commands(guild_ids=[GUILD_ID])
        print(f"✅ Команд синхронизировано: {len(synced)}")
    except Exception as e:
        print(f"❌ Ошибка sync: {e}")

# ─── КОМАНДЫ ──────────────────────────────────────────────

@bot.slash_command(name="ping", description="Проверка")
async def ping(ctx):
    await ctx.respond("🏓 Pong!")

@bot.slash_command(name="setchannel", description="Установить канал", guild_ids=[GUILD_ID])
async def setchannel(ctx,
    channel: discord.Option(discord.SlashCommandOptionType.channel),
    type: discord.Option(str, choices=["sklad", "simple"])
):
    if not has_access(ctx.author):
        await ctx.respond("❌ Нет прав", ephemeral=True)
        return

    set_channel(ctx.guild.id, channel.id, type)
    await ctx.respond("✅ Канал установлен", ephemeral=True)

@bot.slash_command(name="таймер", description="Создать таймер", guild_ids=[GUILD_ID])
async def timer(ctx,
    text: discord.Option(str),
    seconds: discord.Option(int)
):
    channel_id = get_channel(ctx.guild.id, "simple")
    if channel_id and ctx.channel.id != channel_id:
        await ctx.respond("❌ Не тот канал", ephemeral=True)
        return

    now = datetime.datetime.utcnow()
    created = int(now.timestamp())
    end = int((now + datetime.timedelta(seconds=seconds)).timestamp())

    msg = await ctx.send(f"⏳ {text}\n⏰ <t:{end}:R>")

    Timer.create(
        guild_id=ctx.guild.id,
        channel_id=ctx.channel.id,
        message_id=msg.id,
        text=text,
        time_end=end,
        author=ctx.author.display_name,
        created=created
    )

    await ctx.respond("✅ Таймер создан", ephemeral=True)

# ─── ЗАПУСК ───────────────────────────────────────────────
bot.run(os.environ.get("DISCORD_BOT_TOKEN"))
