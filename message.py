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
    debug_guilds=[GUILD_ID]  # мгновенные slash-команды
)

db = SqliteDatabase("TimerDataBase.db")

# ─── БАЗА ДАННЫХ ───────────────────────────────────────────
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
            except Exception:
                pass

            t.delete_instance()

    except Exception:
        print("LOOP ERROR:")
        print(traceback.format_exc())

# ─── READY ────────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ Бот запущен: {bot.user}")

    if not timer_loop.is_running():
        timer_loop.start()

    try:
        synced = await bot.sync_commands(guild_ids=[GUILD_ID])
        print(f"✅ Slash-команды: {len(synced)}")
    except Exception:
        print("SYNC ERROR:")
        print(traceback.format_exc())

# ─── ПРОВЕРКА ─────────────────────────────────────────────
@bot.slash_command(name="ping", guild_ids=[GUILD_ID])
async def ping(ctx):
    await ctx.respond("🏓 Pong!")

# ─── УСТАНОВКА КАНАЛА ─────────────────────────────────────
@bot.slash_command(name="setskladchannel", guild_ids=[GUILD_ID])
async def setskladchannel(ctx, channel: discord.TextChannel):
    try:
        if not has_access(ctx.author):
            await ctx.respond("❌ Нет прав", ephemeral=True)
            return

        set_channel(ctx.guild.id, channel.id, "sklad")
        await ctx.respond(f"✅ Склад канал: {channel.mention}", ephemeral=True)

    except Exception:
        print(traceback.format_exc())
        await ctx.respond("❌ Ошибка", ephemeral=True)


@bot.slash_command(name="setsimplechannel", guild_ids=[GUILD_ID])
async def setsimplechannel(ctx, channel: discord.TextChannel):
    try:
        if not has_access(ctx.author):
            await ctx.respond("❌ Нет прав", ephemeral=True)
            return

        set_channel(ctx.guild.id, channel.id, "simple")
        await ctx.respond(f"✅ Таймер канал: {channel.mention}", ephemeral=True)

    except Exception:
        print(traceback.format_exc())
        await ctx.respond("❌ Ошибка", ephemeral=True)

# ─── ТАЙМЕР ───────────────────────────────────────────────
@bot.slash_command(name="таймер", guild_ids=[GUILD_ID])
async def timer(ctx, text: str, seconds: int):
    try:
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

    except Exception:
        print(traceback.format_exc())
        await ctx.respond("❌ Ошибка таймера", ephemeral=True)

# ─── ЗАПУСК ───────────────────────────────────────────────
bot.run(os.environ.get("DISCORD_BOT_TOKEN"))
