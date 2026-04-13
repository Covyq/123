import os
import datetime
import traceback
import discord
from discord.ext import tasks
from discord.ui import View, Button
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

# ─── КНОПКА ────────────────────────────────────────────────
class SkladView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Обновить склад", style=discord.ButtonStyle.green)
    async def update(self, button: Button, interaction: discord.Interaction):

        try:
            row = Timer.get_or_none(Timer.message_id == interaction.message.id)
            if not row:
                await interaction.response.send_message("❌ Не найден склад", ephemeral=True)
                return

            new_end = int((datetime.datetime.utcnow() + datetime.timedelta(hours=48)).timestamp())
            row.time_end = new_end
            row.save()

            await interaction.message.edit(
                content=f"{row.text}\n\n⏰ Обновлено: 48 часов (<t:{new_end}:R>)",
                view=self
            )

            await interaction.response.send_message("✅ Обновлено", ephemeral=True)

        except Exception:
            print(traceback.format_exc())

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

# ─── SET CHANNELS ─────────────────────────────────────────
@bot.slash_command(name="setskladchannel", guild_ids=[GUILD_ID])
async def setskladchannel(ctx, channel: discord.TextChannel):
    if not has_access(ctx.author):
        await ctx.respond("❌ Нет прав", ephemeral=True)
        return

    set_channel(ctx.guild.id, channel.id, "sklad")
    await ctx.respond("✅ Склад канал установлен", ephemeral=True)


@bot.slash_command(name="setsimpletimerchannel", guild_ids=[GUILD_ID])
async def setsimple(ctx, channel: discord.TextChannel):
    if not has_access(ctx.author):
        await ctx.respond("❌ Нет прав", ephemeral=True)
        return

    set_channel(ctx.guild.id, channel.id, "simple")
    await ctx.respond("✅ Канал таймеров установлен", ephemeral=True)

# ─── /СКЛАД ───────────────────────────────────────────────
@bot.slash_command(name="склад", guild_ids=[GUILD_ID])
async def sklad(ctx, гекс: str, регион: str, склад: str, пароль: str):

    if not has_access(ctx.author):
        await ctx.respond("❌ Нет прав", ephemeral=True)
        return

    end = int((datetime.datetime.utcnow() + datetime.timedelta(hours=48)).timestamp())

    text = (
        f"👤 {ctx.author.display_name}\n"
        f"**Гекс:** {гекс}\n"
        f"**Регион:** {регион}\n"
        f"**Склад:** {склад}\n"
        f"**Пароль:** {пароль}"
    )

    view = SkladView()

    msg = await ctx.send(f"{text}\n⏰ 48 часов (<t:{end}:R>)", view=view)

    Timer.create(
        guild_id=ctx.guild.id,
        channel_id=ctx.channel.id,
        message_id=msg.id,
        text=text,
        time_end=end,
        author=ctx.author.display_name,
        created=int(datetime.datetime.utcnow().timestamp())
    )

    await ctx.respond("✅ Склад создан", ephemeral=True)

# ─── /ТАЙМЕР ──────────────────────────────────────────────
@bot.slash_command(name="таймер", guild_ids=[GUILD_ID])
async def timer(ctx, text: str, seconds: int):

    end = int((datetime.datetime.utcnow() + datetime.timedelta(seconds=seconds)).timestamp())

    msg = await ctx.send(f"⏳ {text}\n⏰ <t:{end}:R>")

    Timer.create(
        guild_id=ctx.guild.id,
        channel_id=ctx.channel.id,
        message_id=msg.id,
        text=text,
        time_end=end,
        author=ctx.author.display_name,
        created=int(datetime.datetime.utcnow().timestamp())
    )

    await ctx.respond("✅ Таймер создан", ephemeral=True)

# ─── ЗАПУСК ───────────────────────────────────────────────
bot.run(os.environ.get("DISCORD_BOT_TOKEN"))
