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

bot = discord.Bot(intents=discord.Intents.all(), debug_guilds=[GUILD_ID])
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

# ─── CHANNELS ───────────────────────────────────────────
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

# ─── PERMS ──────────────────────────────────────────────
def has_access(member):
    return member.guild_permissions.administrator or any(
        r.id in ALLOWED_ROLE_IDS for r in member.roles
    )

# ─── CLEAN ──────────────────────────────────────────────
def clean_channels():
    for row in ChannelConfig.select():
        guild = bot.get_guild(row.guild_id)
        if not guild or guild.get_channel(row.channel_id) is None:
            row.delete_instance()

# ─── VIEWS ──────────────────────────────────────────────
class SkladView(View):
    def __init__(self):
        super().__init__(timeout=None)

        btn_update = Button(label="Обновить склад", style=discord.ButtonStyle.green, custom_id="sklad_update")
        btn_delete = Button(label="Удалить", style=discord.ButtonStyle.red, custom_id="sklad_delete")

        btn_update.callback = self.update
        btn_delete.callback = self.delete

        self.add_item(btn_update)
        self.add_item(btn_delete)

    async def update(self, interaction):
        await interaction.response.defer()
        row = Timer.get_or_none(Timer.message_id == interaction.message.id)

        if not row:
            return await interaction.followup.send("❌ Не найдено", ephemeral=True)

        new_end = int(datetime.datetime.now(datetime.timezone.utc).timestamp() + 48 * 3600)
        row.time_end = new_end
        row.save()

        await interaction.message.edit(
            content=f"{row.text}\n\n⏰ Обновлено: 48 часов (<t:{new_end}:R>)",
            view=self
        )

    async def delete(self, interaction):
        await interaction.response.defer()
        row = Timer.get_or_none(Timer.message_id == interaction.message.id)

        if not row or interaction.user.id != row.author:
            return await interaction.followup.send("❌ Только автор", ephemeral=True)

        row.delete_instance()
        await interaction.message.delete()


class TimerView(View):
    def __init__(self):
        super().__init__(timeout=None)

        btn = Button(label="Удалить таймер", style=discord.ButtonStyle.red, custom_id="timer_delete")
        btn.callback = self.delete
        self.add_item(btn)

    async def delete(self, interaction):
        await interaction.response.defer()

        row = Timer.get_or_none(Timer.message_id == interaction.message.id)
        if not row or interaction.user.id != row.author:
            return await interaction.followup.send("❌ Только автор", ephemeral=True)

        row.delete_instance()
        await interaction.message.delete()


class MPFView(View):
    def __init__(self, show_take=False):
        super().__init__(timeout=None)

        delete = Button(label="Удалить таймер", style=discord.ButtonStyle.red, custom_id="mpf_delete")
        delete.callback = self.delete
        self.add_item(delete)

        take = Button(
            label="Забрал заказ",
            style=discord.ButtonStyle.green,
            custom_id="mpf_take",
            disabled=not show_take
        )
        take.callback = self.take
        self.add_item(take)

    async def take(self, interaction):
        await interaction.response.defer()

        row = Timer.get_or_none(Timer.message_id == interaction.message.id)
        if not row or row.taken_by:
            return await interaction.followup.send("❌ Уже забрали", ephemeral=True)

        row.taken_by = interaction.user.id
        row.save()

        member = interaction.guild.get_member(interaction.user.id)

        await interaction.message.edit(
            content=interaction.message.content + f"\n\n📦 Забрал: {member.display_name}",
            view=self
        )

        await interaction.followup.send("✅ Забрал", ephemeral=True)

    async def delete(self, interaction):
        await interaction.response.defer()

        row = Timer.get_or_none(Timer.message_id == interaction.message.id)
        if not row or interaction.user.id != row.author:
            return await interaction.followup.send("❌ Только автор", ephemeral=True)

        row.delete_instance()
        await interaction.message.delete()

# ─── LOOP ───────────────────────────────────────────────
@tasks.loop(seconds=30)
async def loop():
    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    expired = Timer.select().where(Timer.time_end < now)

    for t in expired:
        try:
            guild = bot.get_guild(t.guild_id)
            channel = guild.get_channel(t.channel_id)
            msg = await channel.fetch_message(t.message_id)

            member = guild.get_member(t.author)
            nickname = member.display_name if member else "пользователь"

            # MPF
            if t.kind == "mpf":
                item = t.text.split("📦 Что поставил: ")[1].splitlines()[0]

                await msg.edit(
                    content=(
                        f"👤 Кто поставил: {nickname}\n"
                        f"📦 Что поставил: {item}\n"
                        f"📦 Ящиков: {t.boxes}\n"
                        f"✅ Статус: выполнено"
                    ),
                    view=MPFView(show_take=True)
                )

                t.time_end = now + 10**9
                t.save()
                continue

            # TIMER (исправленный)
            if t.kind == "timer":
                await msg.edit(
                    content=(
                        f"👤 {nickname}\n"
                        f"📌 {t.text}\n"
                        f"✅ Статус: выполнено"
                    ),
                    view=TimerView()
                )

                t.delete_instance()
                continue

            # SKLAD
            if t.kind == "sklad":
                await msg.edit(
                    content=f"✅ Склад завершён {nickname}\n⏰ <t:{now}:R>",
                    view=None
                )

                t.delete_instance()

        except Exception:
            print(traceback.format_exc())
            t.delete_instance()

# ─── READY ───────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"Bot online {bot.user}")

    clean_channels()
    load_channels()

    bot.add_view(SkladView())
    bot.add_view(TimerView())
    bot.add_view(MPFView())

    if not loop.is_running():
        loop.start()

# ─── COMMANDS ───────────────────────────────────────────
@bot.slash_command(name="setskladchannel", guild_ids=[GUILD_ID])
async def setskladchannel(ctx, channel: discord.TextChannel):
    if not has_access(ctx.author):
        return await ctx.respond("❌ Нет прав", ephemeral=True)

    set_channel(ctx.guild.id, channel.id, "sklad")
    await ctx.respond("✅ склад установлен", ephemeral=True)


@bot.slash_command(name="setsimpletimer", guild_ids=[GUILD_ID])
async def setsimpletimer(ctx, channel: discord.TextChannel):
    if not has_access(ctx.author):
        return await ctx.respond("❌ Нет прав", ephemeral=True)

    set_channel(ctx.guild.id, channel.id, "simple")
    await ctx.respond("✅ таймер установлен", ephemeral=True)


@bot.slash_command(name="setmpf", guild_ids=[GUILD_ID])
async def setmpf(ctx, channel: discord.TextChannel):
    if not has_access(ctx.author):
        return await ctx.respond("❌ Нет прав", ephemeral=True)

    set_channel(ctx.guild.id, channel.id, "mpf")
    await ctx.respond("✅ MPF установлен", ephemeral=True)


@bot.slash_command(name="таймер", guild_ids=[GUILD_ID])
async def timer(ctx, название: str, days: int = 0, hours: int = 0, minutes: int = 0):
    if days == 0 and hours == 0 and minutes == 0:
        return await ctx.respond("❌ укажи время", ephemeral=True)

    channel_id = get_channel(ctx.guild.id, "simple")
    if not channel_id or ctx.channel.id != channel_id:
        return await ctx.respond("❌ не тот канал", ephemeral=True)

    end = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days, hours=hours, minutes=minutes)
    end_ts = int(end.timestamp())

    msg = await ctx.send(
        f"👤 {ctx.author.display_name}\n📌 {название}\n⏰ <t:{end_ts}:R>",
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

    await ctx.respond("✅ таймер создан", ephemeral=True)


@bot.slash_command(name="склад", guild_ids=[GUILD_ID])
async def sklad(ctx, гекс: str, регион: str, склад: str, пароль: str):
    channel_id = get_channel(ctx.guild.id, "sklad")
    if channel_id and ctx.channel.id != channel_id:
        return await ctx.respond("❌ не тот канал", ephemeral=True)

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

    await ctx.respond("✅ склад создан", ephemeral=True)


@bot.slash_command(name="мпф", guild_ids=[GUILD_ID])
async def mpf(ctx, что_поставил: str, ящиков: int, days: int = 0, hours: int = 0, minutes: int = 0):
    channel_id = get_channel(ctx.guild.id, "mpf")
    if not channel_id or ctx.channel.id != channel_id:
        return await ctx.respond("❌ не тот канал", ephemeral=True)

    text = (
        f"👤 Кто поставил: {ctx.author.display_name}\n"
        f"📦 Что поставил: {что_поставил}\n"
        f"📦 Ящиков: {ящиков}\n"
        f"⌛ Статус: ожидание"
    )

    msg = await ctx.send(text, view=MPFView())

    end = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days, hours=hours, minutes=minutes)

    Timer.create(
        guild_id=ctx.guild.id,
        channel_id=ctx.channel.id,
        message_id=msg.id,
        text=text,
        time_end=int(end.timestamp()),
        author=ctx.author.id,
        kind="mpf",
        boxes=ящиков,
        taken_by=None
    )

    await ctx.respond("✅ MPF создан", ephemeral=True)

# ─── RUN ────────────────────────────────────────────────
bot.run(os.environ.get("DISCORD_BOT_TOKEN"))
