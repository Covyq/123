import os
import datetime
import traceback
import discord
from discord.ext import tasks
from discord.ui import View, Button
from peewee import *

# ─── НАСТРОЙКИ ─────────────────────────────────────────────
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

db = SqliteDatabase("TimerDataBase.db")

# ─── КЭШ ───────────────────────────────────────────────────
CHANNEL_CACHE = {
    "sklad": {},
    "simple": {},
    "mpf": {}
}

# ─── БАЗА ──────────────────────────────────────────────────
class BaseModel(Model):
    guild_id = BigIntegerField()

    class Meta:
        database = db


class ChannelConfig(BaseModel):
    guild_id = BigIntegerField()
    channel_id = BigIntegerField()
    type = TextField()


class Timer(BaseModel):
    guild_id = BigIntegerField()
    channel_id = BigIntegerField()
    message_id = BigIntegerField()
    text = TextField()
    time_end = BigIntegerField()
    author = BigIntegerField()
    type = TextField(default="simple")


db.connect(reuse_if_open=True)
db.create_tables([ChannelConfig, Timer])

# ─── КЭШ ───────────────────────────────────────────────────
def load_channels():
    global CHANNEL_CACHE
    CHANNEL_CACHE = {"sklad": {}, "simple": {}, "mpf": {}}

    for row in ChannelConfig.select():
        CHANNEL_CACHE[row.type][row.guild_id] = row.channel_id


def clean_channels():
    print("🧹 Cleaning invalid channels...")

    for row in ChannelConfig.select():
        guild = bot.get_guild(row.guild_id)

        if not guild:
            row.delete_instance()
            continue

        if guild.get_channel(row.channel_id) is None:
            row.delete_instance()

# ─── ПРАВА ────────────────────────────────────────────────
def has_access(member):
    return (
        member.guild_permissions.administrator or
        any(r.id in ALLOWED_ROLE_IDS for r in member.roles)
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

    CHANNEL_CACHE[type_][guild_id] = channel_id


def get_channel(guild_id, type_):
    return CHANNEL_CACHE.get(type_, {}).get(guild_id)

# ─── VIEW: TIMER ──────────────────────────────────────────
class TimerView(View):
    def __init__(self):
        super().__init__(timeout=None)

        self.delete_btn = Button(
            label="Удалить",
            style=discord.ButtonStyle.red,
            custom_id="timer_delete_btn"
        )

        self.delete_btn.callback = self.delete
        self.add_item(self.delete_btn)

    async def delete(self, interaction: discord.Interaction):
        row = Timer.get_or_none(Timer.message_id == interaction.message.id)

        if not row or interaction.user.id != row.author:
            await interaction.response.send_message("❌ Нет прав", ephemeral=True)
            return

        row.delete_instance()
        await interaction.message.delete()

# ─── VIEW: СКЛАД ──────────────────────────────────────────
class SkladView(View):
    def __init__(self):
        super().__init__(timeout=None)

        self.update_btn = Button(
            label="Обновить склад",
            style=discord.ButtonStyle.green,
            custom_id="sklad_update_btn"
        )

        self.delete_btn = Button(
            label="Удалить",
            style=discord.ButtonStyle.red,
            custom_id="sklad_delete_btn"
        )

        self.update_btn.callback = self.update_callback
        self.delete_btn.callback = self.delete_callback

        self.add_item(self.update_btn)
        self.add_item(self.delete_btn)

    async def update_callback(self, interaction: discord.Interaction):
        row = Timer.get_or_none(Timer.message_id == interaction.message.id)

        if not row:
            await interaction.response.send_message("❌ Склад не найден", ephemeral=True)
            return

        new_end = int((datetime.datetime.utcnow() + datetime.timedelta(hours=48)).timestamp())
        row.time_end = new_end
        row.save()

        await interaction.message.edit(
            content=f"{row.text}\n\n⏰ Обновлено: 48 часов (<t:{new_end}:R>)",
            view=self
        )

        await interaction.response.send_message("✅ Обновлено", ephemeral=True)

    async def delete_callback(self, interaction: discord.Interaction):
        row = Timer.get_or_none(Timer.message_id == interaction.message.id)

        if not row or interaction.user.id != row.author:
            await interaction.response.send_message("❌ Нет прав", ephemeral=True)
            return

        row.delete_instance()
        await interaction.message.delete()

# ─── LOOP ─────────────────────────────────────────────────
@tasks.loop(seconds=30)
async def loop():
    now = int(datetime.datetime.utcnow().timestamp())

    for t in Timer.select().where(Timer.time_end < now):
        try:
            guild = bot.get_guild(t.guild_id)
            if not guild:
                continue

            channel = bot.get_channel(t.channel_id)
            if not channel:
                continue

            msg = await channel.fetch_message(t.message_id)

            if t.type == "mpf":
                content = f"{t.text}\n\n✅ Можно забирать"

            elif t.type == "sklad":
                content = f"{t.text}\n\n⏰ Склад завершён"

            else:
                member = guild.get_member(t.author)
                mention = member.mention if member else "пользователь"

                content = (
                    f"✅ **{t.text}** завершён {mention}\n"
                    f"⏰ Закончен: <t:{now}:R>"
                )

            await msg.edit(content=content)

        except Exception:
            print(traceback.format_exc())

        t.delete_instance()

# ─── READY ────────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ Бот запущен: {bot.user}")

    clean_channels()
    load_channels()

    bot.add_view(SkladView())
    bot.add_view(TimerView())

    if not loop.is_running():
        loop.start()

# ─── КАНАЛЫ ───────────────────────────────────────────────
@bot.slash_command(name="setskladchannel", guild_ids=[GUILD_ID])
async def setskladchannel(ctx, channel: discord.TextChannel):
    if not has_access(ctx.author):
        await ctx.respond("❌ Нет прав", ephemeral=True)
        return

    set_channel(ctx.guild.id, channel.id, "sklad")
    await ctx.respond(f"✅ Склад канал: {channel.mention}", ephemeral=True)


@bot.slash_command(name="setsimpletimer", guild_ids=[GUILD_ID])
async def setsimpletimer(ctx, channel: discord.TextChannel):
    if not has_access(ctx.author):
        await ctx.respond("❌ Нет прав", ephemeral=True)
        return

    set_channel(ctx.guild.id, channel.id, "simple")
    await ctx.respond(f"✅ Таймер канал: {channel.mention}", ephemeral=True)


# 🔥 ПЕРЕИМЕНОВАНА КОМАНДА
@bot.slash_command(name="setmpfchat", guild_ids=[GUILD_ID])
async def setmpfchat(ctx, channel: discord.TextChannel):
    if not has_access(ctx.author):
        await ctx.respond("❌ Нет прав", ephemeral=True)
        return

    set_channel(ctx.guild.id, channel.id, "mpf")
    await ctx.respond(f"✅ MPF канал: {channel.mention}", ephemeral=True)

# ─── ТАЙМЕР ───────────────────────────────────────────────
@bot.slash_command(name="таймер", guild_ids=[GUILD_ID])
async def timer(ctx, название: str, days: int = 0, hours: int = 0, minutes: int = 0):

    if days == 0 and hours == 0 and minutes == 0:
        await ctx.respond("❌ Укажи время", ephemeral=True)
        return

    channel_id = get_channel(ctx.guild.id, "simple")

    if not channel_id or ctx.channel.id != channel_id:
        await ctx.respond("❌ Не тот канал", ephemeral=True)
        return

    end = datetime.datetime.utcnow() + datetime.timedelta(days=days, hours=hours, minutes=minutes)
    end_ts = int(end.timestamp())

    msg = await ctx.send(
        f"👤 {ctx.author.mention}\n📌 {название}\n⏰ <t:{end_ts}:R>",
        view=TimerView()
    )

    Timer.create(
        guild_id=ctx.guild.id,
        channel_id=ctx.channel.id,
        message_id=msg.id,
        text=название,
        time_end=end_ts,
        author=ctx.author.id,
        type="simple"
    )

    await ctx.respond("✅ Таймер создан", ephemeral=True)

# ─── MPF ────────────────────────────────────────────────
@bot.slash_command(name="мпф", guild_ids=[GUILD_ID])
async def mpf(ctx, что: str, ящики: int, дни: int = 0, часы: int = 0, минуты: int = 0):

    if дни == 0 and часы == 0 and минуты == 0:
        await ctx.respond("❌ Укажи время", ephemeral=True)
        return

    channel_id = get_channel(ctx.guild.id, "mpf")

    if not channel_id:
        await ctx.respond("❌ MPF канал не настроен", ephemeral=True)
        return

    channel = bot.get_channel(channel_id)

    if not channel:
        await ctx.respond("❌ Канал не найден", ephemeral=True)
        return

    end = datetime.datetime.utcnow() + datetime.timedelta(days=дни, hours=часы, minutes=минуты)
    end_ts = int(end.timestamp())

    text = (
        f"👤 Кто создал: {ctx.author.display_name}\n"
        f"📦 Что поставил: {что}\n"
        f"📦 Количество ящиков: {ящики}"
    )

    msg = await channel.send(
        f"{text}\n⏰ <t:{end_ts}:R>",
        view=TimerView()
    )

    Timer.create(
        guild_id=ctx.guild.id,
        channel_id=channel.id,
        message_id=msg.id,
        text=text,
        time_end=end_ts,
        author=ctx.author.id,
        type="mpf"
    )

    await ctx.respond("✅ MPF таймер создан", ephemeral=True)

# ─── СКЛАД ────────────────────────────────────────────────
@bot.slash_command(name="склад", guild_ids=[GUILD_ID])
async def sklad(ctx, гекс: str, регион: str, склад: str, пароль: str):

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
        author=ctx.author.id,
        type="sklad"
    )

    await ctx.respond("✅ Склад создан", ephemeral=True)

# ─── RUN ────────────────────────────────────────────────
bot.run(os.environ.get("DISCORD_BOT_TOKEN"))
