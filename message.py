import os
import datetime
import traceback
import discord
from discord.ext import tasks
from discord.ui import View, Button
from peewee import *

# ─── НАСТРОЙКИ ─────────────────────────────────────────────
GUILD_ID = 419565206335651840
ALLOWED_ROLE_ID = 1493199914572972032

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
    author = BigIntegerField()


db.connect(reuse_if_open=True)
db.create_tables([ChannelConfig, Timer])

# ─── ПРАВА ────────────────────────────────────────────────
def has_access(member):
    return (
        member.guild_permissions.administrator or
        any(r.id == ALLOWED_ROLE_ID for r in member.roles)
    )

# ─── КАНАЛЫ ───────────────────────────────────────────────
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

# ─── VIEW ДЛЯ СКЛАДА ──────────────────────────────────────
class SkladView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Обновить склад", style=discord.ButtonStyle.green)
    async def update(self, button: Button, interaction: discord.Interaction):
        try:
            row = Timer.get_or_none(Timer.message_id == interaction.message.id)

            if not row:
                await interaction.response.send_message("❌ Склад не найден", ephemeral=True)
                return

            if interaction.user.id != row.author:
                await interaction.response.send_message("❌ Не твой склад", ephemeral=True)
                return

            new_end = int((datetime.datetime.utcnow() + datetime.timedelta(hours=48)).timestamp())
            row.time_end = new_end
            row.save()

            await interaction.message.edit(
                content=f"{row.text}\n\n⏰ Обновлено: 48 часов (<t:{new_end}:R>)",
                view=self
            )

            await interaction.response.send_message("✅ Склад обновлён", ephemeral=True)

        except Exception:
            print(traceback.format_exc())
            await interaction.response.send_message("❌ Ошибка", ephemeral=True)

    @discord.ui.button(label="Удалить", style=discord.ButtonStyle.red)
    async def delete(self, button: Button, interaction: discord.Interaction):
        try:
            row = Timer.get_or_none(Timer.message_id == interaction.message.id)

            if not row:
                await interaction.response.send_message("❌ Уже удалено", ephemeral=True)
                return

            if interaction.user.id != row.author:
                await interaction.response.send_message("❌ Не твой склад", ephemeral=True)
                return

            row.delete_instance()
            await interaction.message.delete()

        except Exception:
            print(traceback.format_exc())
            await interaction.response.send_message("❌ Ошибка", ephemeral=True)

# ─── VIEW ДЛЯ ТАЙМЕРА ─────────────────────────────────────
class TimerView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Удалить таймер", style=discord.ButtonStyle.red)
    async def delete(self, button: Button, interaction: discord.Interaction):
        try:
            row = Timer.get_or_none(Timer.message_id == interaction.message.id)

            if not row:
                await interaction.response.send_message("❌ Таймер не найден", ephemeral=True)
                return

            if interaction.user.id != row.author:
                await interaction.response.send_message("❌ Не твой таймер", ephemeral=True)
                return

            row.delete_instance()
            await interaction.message.delete()

        except Exception:
            print(traceback.format_exc())
            await interaction.response.send_message("❌ Ошибка", ephemeral=True)

# ─── LOOP ─────────────────────────────────────────────────
@tasks.loop(seconds=30)
async def loop():
    now = int(datetime.datetime.utcnow().timestamp())

    for t in Timer.select().where(Timer.time_end < now):
        try:
            guild = bot.get_guild(t.guild_id)
            if not guild:
                continue

            channel = guild.get_channel(t.channel_id)
            if not channel:
                continue

            member = guild.get_member(t.author)
            mention = member.mention if member else "пользователь"

            msg = await channel.fetch_message(t.message_id)

            await msg.edit(
                content=f"✅ **{t.text}** завершён {mention}\n⏰ Закончен: <t:{now}:R>"
            )

        except Exception:
            print(traceback.format_exc())

        t.delete_instance()

# ─── READY ────────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ Бот запущен: {bot.user}")

    bot.add_view(SkladView())
    bot.add_view(TimerView())

    if not loop.is_running():
        loop.start()

# ─── КОМАНДЫ ──────────────────────────────────────────────
@bot.slash_command(name="setskladchannel", guild_ids=[GUILD_ID])
async def setskladchannel(ctx, channel: discord.TextChannel):

    if not has_access(ctx.author):
        await ctx.respond("❌ Нет прав", ephemeral=True)
        return

    set_channel(ctx.guild.id, channel.id, "sklad")
    await ctx.respond(f"✅ Канал складов: {channel.mention}", ephemeral=True)


@bot.slash_command(name="setsimpletimer", guild_ids=[GUILD_ID])
async def setsimpletimer(ctx, channel: discord.TextChannel):

    if not has_access(ctx.author):
        await ctx.respond("❌ Нет прав", ephemeral=True)
        return

    set_channel(ctx.guild.id, channel.id, "simple")
    await ctx.respond(f"✅ Канал таймеров: {channel.mention}", ephemeral=True)


@bot.slash_command(name="таймер", guild_ids=[GUILD_ID])
async def timer(
    ctx,
    название: str,
    days: int = 0,
    hours: int = 0,
    minutes: int = 0
):
    if days == 0 and hours == 0 and minutes == 0:
        await ctx.respond("❌ Укажи время", ephemeral=True)
        return

    channel_id = get_channel(ctx.guild.id, "simple")
    if channel_id and ctx.channel.id != channel_id:
        await ctx.respond("❌ Не тот канал", ephemeral=True)
        return

    now = datetime.datetime.utcnow()
    end = now + datetime.timedelta(days=days, hours=hours, minutes=minutes)
    end_ts = int(end.timestamp())

    msg = await ctx.send(
        f"⏳ **{название}**\n"
        f"👤 Создал: {ctx.author.mention}\n"
        f"⏰ <t:{end_ts}:R>",
        view=TimerView()
    )

    Timer.create(
        guild_id=ctx.guild.id,
        channel_id=ctx.channel.id,
        message_id=msg.id,
        text=название,
        time_end=end_ts,
        author=ctx.author.id
    )

    await ctx.respond("✅ Таймер создан", ephemeral=True)


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

    end_ts = int((datetime.datetime.utcnow() + datetime.timedelta(hours=48)).timestamp())

    text = (
        f"👤 {ctx.author.display_name}\n"
        f"**Гекс:** {гекс}\n"
        f"**Регион:** {регион}\n"
        f"**Склад:** {склад}\n"
        f"**Пароль:** {пароль}"
    )

    msg = await ctx.send(
        f"{text}\n\n⏰ 48 часов (<t:{end_ts}:R>)",
        view=SkladView()
    )

    Timer.create(
        guild_id=ctx.guild.id,
        channel_id=ctx.channel.id,
        message_id=msg.id,
        text=text,
        time_end=end_ts,
        author=ctx.author.id
    )

    await ctx.respond("✅ Склад создан", ephemeral=True)

# ─── RUN ────────────────────────────────────────────────
bot.run(os.environ.get("DISCORD_BOT_TOKEN"))
