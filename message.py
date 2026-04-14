import os
import datetime
import traceback
import discord
from discord.ext import tasks
from discord.ui import View, Button
from peewee import *

# ─── CONFIG ─────────────────────────────────────────────
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

# ─── CACHE ───────────────────────────────────────────────
CHANNEL_CACHE = {"sklad": {}, "simple": {}, "mpf": {}}

# ─── DB ──────────────────────────────────────────────────
class BaseModel(Model):
    class Meta:
        database = db


class ChannelConfig(BaseModel):
    guild_id = BigIntegerField()
    channel_id = BigIntegerField()
    channel_type = TextField()


class Timer(BaseModel):
    guild_id = BigIntegerField()
    channel_id = BigIntegerField()
    message_id = BigIntegerField()
    text = TextField()
    time_end = BigIntegerField()
    author = BigIntegerField()

    kind = TextField(default="timer")
    boxes = IntegerField(null=True)
    taken_by = BigIntegerField(null=True)


db.connect(reuse_if_open=True)
db.create_tables([ChannelConfig, Timer])

# ─── CHANNEL CACHE ───────────────────────────────────────
def load_channels():
    global CHANNEL_CACHE
    CHANNEL_CACHE = {"sklad": {}, "simple": {}, "mpf": {}}

    for row in ChannelConfig.select():
        CHANNEL_CACHE.setdefault(row.channel_type, {})
        CHANNEL_CACHE[row.channel_type][row.guild_id] = row.channel_id


def set_channel(guild_id, channel_id, channel_type):
    row = ChannelConfig.get_or_none(
        (ChannelConfig.guild_id == guild_id) &
        (ChannelConfig.channel_type == channel_type)
    )

    if row:
        row.channel_id = channel_id
        row.save()
    else:
        ChannelConfig.create(
            guild_id=guild_id,
            channel_id=channel_id,
            channel_type=channel_type
        )

    CHANNEL_CACHE.setdefault(channel_type, {})[guild_id] = channel_id


def get_channel(guild_id, channel_type):
    return CHANNEL_CACHE.get(channel_type, {}).get(guild_id)


# ─── PERMISSIONS ─────────────────────────────────────────
def has_access(member):
    return member.guild_permissions.administrator or any(
        r.id in ALLOWED_ROLE_IDS for r in member.roles
    )


# ─── CLEAN ───────────────────────────────────────────────
def clean_channels():
    for row in ChannelConfig.select():
        guild = bot.get_guild(row.guild_id)
        if not guild:
            row.delete_instance()
            continue
        if guild.get_channel(row.channel_id) is None:
            row.delete_instance()


# ─── SKLAD VIEW ─────────────────────────────────────────
class SkladView(View):
    def __init__(self):
        super().__init__(timeout=None)

        update = Button(label="Обновить склад", style=discord.ButtonStyle.green)
        delete = Button(label="Удалить", style=discord.ButtonStyle.red)

        update.callback = self.update
        delete.callback = self.delete

        self.add_item(update)
        self.add_item(delete)

    async def update(self, interaction):
        await interaction.response.defer()

        row = Timer.get_or_none(Timer.message_id == interaction.message.id)
        if not row:
            return

        new_end = int(datetime.datetime.now(datetime.timezone.utc).timestamp() + 172800)
        row.time_end = new_end
        row.save()

        await interaction.message.edit(
            content=f"{row.text}\n\n⏰ Обновлено: 48 часов (<t:{new_end}:R>)",
            view=self
        )

    async def delete(self, interaction):
        await interaction.response.defer()

        row = Timer.get_or_none(Timer.message_id == interaction.message.id)
        if row and interaction.user.id == row.author:
            row.delete_instance()
            await interaction.message.delete()


# ─── TIMER VIEW ─────────────────────────────────────────
class TimerView(View):
    def __init__(self):
        super().__init__(timeout=None)

        btn = Button(label="Удалить таймер", style=discord.ButtonStyle.red)
        btn.callback = self.delete
        self.add_item(btn)

    async def delete(self, interaction):
        await interaction.response.defer()

        row = Timer.get_or_none(Timer.message_id == interaction.message.id)
        if row and interaction.user.id == row.author:
            row.delete_instance()
            await interaction.message.delete()


# ─── MPF VIEW ───────────────────────────────────────────
class MPFView(View):
    def __init__(self, show_take=False):
        super().__init__(timeout=None)

        if show_take:
            take = Button(label="Забрал заказ", style=discord.ButtonStyle.green)
            take.callback = self.take
            self.add_item(take)

        delete = Button(label="Удалить", style=discord.ButtonStyle.red)
        delete.callback = self.delete
        self.add_item(delete)

    async def take(self, interaction):
        await interaction.response.defer()

        row = Timer.get_or_none(Timer.message_id == interaction.message.id)
        if not row or row.taken_by:
            return

        row.taken_by = interaction.user.id
        row.save()

        nickname = interaction.user.display_name

        await interaction.message.edit(
            content=interaction.message.content + f"\n\n📦 Забрал: {nickname}",
            view=self
        )

    async def delete(self, interaction):
        await interaction.response.defer()

        row = Timer.get_or_none(Timer.message_id == interaction.message.id)
        if row and interaction.user.id == row.author:
            row.delete_instance()
            await interaction.message.delete()


# ─── LOOP ───────────────────────────────────────────────
@tasks.loop(seconds=30)
async def loop():
    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())

    for t in Timer.select().where(Timer.time_end < now):
        try:
            guild = bot.get_guild(t.guild_id)
            if not guild:
                t.delete_instance()
                continue

            channel = guild.get_channel(t.channel_id)
            if not channel:
                t.delete_instance()
                continue

            msg = await channel.fetch_message(t.message_id)

            member = guild.get_member(t.author)
            nickname = member.display_name if member else "неизвестный"

            if t.kind == "mpf":
                what = "неизвестно"
                for line in t.text.split("\n"):
                    if "Что:" in line:
                        what = line.split("Что:")[-1].strip()

                await msg.edit(
                    content=
                    f"👤 Кто поставил: {nickname}\n"
                    f"📦 Что поставил: {what}\n"
                    f"📦 Ящиков: {t.boxes}\n"
                    f"✅ Статус: Завершено\n"
                    f"⌛ завершено: {nickname}",
                    view=MPFView(show_take=True)
                )

                # не удаляем чтобы работали кнопки
                t.time_end = now + 10**9
                t.save()
                continue

            await msg.edit(
                content=f"✅ {t.text} завершён {nickname}",
            )

            t.delete_instance()

        except Exception:
            print(traceback.format_exc())
            t.delete_instance()


# ─── READY ───────────────────────────────────────────────
@bot.event
async def on_ready():
    load_channels()
    bot.add_view(SkladView())
    bot.add_view(TimerView())
    bot.add_view(MPFView())

    if not loop.is_running():
        loop.start()

    print(f"Bot online {bot.user}")


# ─── COMMANDS ───────────────────────────────────────────
@bot.slash_command(name="таймер", guild_ids=[GUILD_ID])
async def timer(ctx, название: str, days: int = 0, hours: int = 0, minutes: int = 0):
    if days == hours == minutes == 0:
        return await ctx.respond("❌ укажи время", ephemeral=True)

    channel_id = get_channel(ctx.guild.id, "simple")
    if ctx.channel.id != channel_id:
        return await ctx.respond("❌ не тот канал", ephemeral=True)

    end = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days, hours=hours, minutes=minutes)
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
        kind="timer"
    )

    await ctx.respond("✅ создано", ephemeral=True)


@bot.slash_command(name="склад", guild_ids=[GUILD_ID])
async def sklad(ctx, гекс: str, регион: str, склад: str, пароль: str):
    end_ts = int((datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=48)).timestamp())

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
        kind="sklad"
    )

    await ctx.respond("✅ создано", ephemeral=True)


@bot.slash_command(name="мпф", guild_ids=[GUILD_ID])
async def mpf(ctx, что_поставил: str, ящиков: int, days: int = 0, hours: int = 0, minutes: int = 0):
    end = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days, hours=hours, minutes=minutes)
    end_ts = int(end.timestamp())

    text = (
        f"👤 {ctx.author.mention}\n"
        f"📦 Что: {что_поставил}\n"
        f"📦 Ящиков: {ящиков}\n"
        f"⌛ Статус: ⌛"
    )

    msg = await ctx.send(
        f"{text}\n\n⏰ <t:{end_ts}:R>",
        view=MPFView()
    )

    Timer.create(
        guild_id=ctx.guild.id,
        channel_id=ctx.channel.id,
        message_id=msg.id,
        text=text,
        time_end=end_ts,
        author=ctx.author.id,
        kind="mpf",
        boxes=ящиков
    )

    await ctx.respond("✅ создано", ephemeral=True)


bot.run(os.environ["DISCORD_BOT_TOKEN"])
