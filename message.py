import os
import datetime
import traceback
import discord
from discord.ext import tasks
from discord.ui import View, Button
from peewee import *

# ───────────────────────── CONFIG ─────────────────────────

GUILD_ID = 419565206335651840

ALLOWED_ROLE_IDS = {
    1493199914572972032,
    123456789012345678,
    987654321098765432
}

bot = discord.Bot(
    intents=discord.Intents.all(),
    debug_guilds=[GUILD_ID]
)

db = SqliteDatabase("TimerDataBase.db")


# ───────────────────────── DB MODELS ──────────────────────

class BaseModel(Model):
    class Meta:
        database = db


class ChannelConfig(BaseModel):
    guild_id = BigIntegerField(index=True)
    channel_id = BigIntegerField()
    channel_type = TextField(index=True)


class Timer(BaseModel):
    guild_id = BigIntegerField(index=True)
    channel_id = BigIntegerField(index=True)
    message_id = BigIntegerField(index=True)
    text = TextField()
    time_end = BigIntegerField(index=True)
    author = BigIntegerField(index=True)


db.connect(reuse_if_open=True)
db.create_tables([ChannelConfig, Timer])


# ───────────────────────── CACHE ─────────────────────────

CHANNEL_CACHE = {"sklad": {}, "simple": {}}


def load_cache():
    global CHANNEL_CACHE
    CHANNEL_CACHE = {"sklad": {}, "simple": {}}

    for row in ChannelConfig.select():
        CHANNEL_CACHE.setdefault(row.channel_type, {})[row.guild_id] = row.channel_id


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


# ───────────────────────── PERMISSIONS ───────────────────

def has_access(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    return any(role.id in ALLOWED_ROLE_IDS for role in member.roles)


# ───────────────────────── SERVICE LAYER ──────────────────

def get_timer_by_message(message_id: int):
    return Timer.get_or_none(Timer.message_id == message_id)


def delete_timer(timer: Timer):
    if timer:
        timer.delete_instance()


def extend_timer_48h(timer: Timer):
    new_end = int(
        datetime.datetime.now(datetime.timezone.utc).timestamp()
        + 48 * 3600
    )
    timer.time_end = new_end
    timer.save()
    return new_end


# ───────────────────────── CLEANUP ───────────────────────

def clean_orphan_channels():
    print("🧹 Cleaning channels...")

    for row in ChannelConfig.select():
        guild = bot.get_guild(row.guild_id)
        if not guild:
            row.delete_instance()
            continue

        if guild.get_channel(row.channel_id) is None:
            row.delete_instance()


# ───────────────────────── UI HELPERS ────────────────────

async def safe_edit_message(message, content, view=None):
    try:
        await message.edit(content=content, view=view)
    except Exception:
        print(traceback.format_exc())


async def safe_delete_message(message):
    try:
        await message.delete()
    except Exception:
        pass


# ───────────────────────── VIEWS ─────────────────────────

class SkladView(View):
    def __init__(self):
        super().__init__(timeout=None)

        self.add_item(Button(
            label="Обновить склад",
            style=discord.ButtonStyle.green,
            custom_id="sklad_update"
        ))
        self.add_item(Button(
            label="Удалить",
            style=discord.ButtonStyle.red,
            custom_id="sklad_delete"
        ))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return True

    async def on_timeout(self):
        pass

    async def update(self, interaction: discord.Interaction):
        await interaction.response.defer()

        timer = get_timer_by_message(interaction.message.id)
        if not timer:
            return await interaction.followup.send("❌ Не найдено", ephemeral=True)

        new_end = extend_timer_48h(timer)

        await safe_edit_message(
            interaction.message,
            f"{timer.text}\n\n⏰ Обновлено: 48 часов (<t:{new_end}:R>)",
            view=self
        )

        await interaction.followup.send("✅ Обновлено", ephemeral=True)

    async def delete(self, interaction: discord.Interaction):
        await interaction.response.defer()

        timer = get_timer_by_message(interaction.message.id)
        if not timer:
            return await interaction.followup.send("❌ Уже удалено", ephemeral=True)

        if interaction.user.id != timer.author:
            return await interaction.followup.send("❌ Только автор", ephemeral=True)

        delete_timer(timer)
        await safe_delete_message(interaction.message)


class TimerView(View):
    def __init__(self):
        super().__init__(timeout=None)

        self.add_item(Button(
            label="Удалить таймер",
            style=discord.ButtonStyle.red,
            custom_id="timer_delete"
        ))

    async def delete(self, interaction: discord.Interaction):
        await interaction.response.defer()

        timer = get_timer_by_message(interaction.message.id)
        if not timer:
            return await interaction.followup.send("❌ Не найден", ephemeral=True)

        if interaction.user.id != timer.author:
            return await interaction.followup.send("❌ Только автор", ephemeral=True)

        delete_timer(timer)
        await safe_delete_message(interaction.message)


# ───────────────────────── LOOP ──────────────────────────

@tasks.loop(seconds=30)
async def timer_loop():
    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())

    expired = Timer.select().where(Timer.time_end <= now).limit(100)

    for t in expired:
        try:
            guild = bot.get_guild(t.guild_id)
            if not guild:
                delete_timer(t)
                continue

            channel = guild.get_channel(t.channel_id)
            if not channel:
                delete_timer(t)
                continue

            msg = await channel.fetch_message(t.message_id)

            member = guild.get_member(t.author)
            mention = member.mention if member else "пользователь"

            await msg.edit(
                content=f"✅ **{t.text}** завершён {mention}\n⏰ Закончен: <t:{now}:R>"
            )

        except Exception:
            print(traceback.format_exc())

        delete_timer(t)


# ───────────────────────── EVENTS ────────────────────────

@bot.event
async def on_ready():
    print(f"✅ Bot online: {bot.user}")

    db.connect(reuse_if_open=True)
    db.create_tables([ChannelConfig, Timer])

    clean_orphan_channels()
    load_cache()

    bot.add_view(SkladView())
    bot.add_view(TimerView())

    if not timer_loop.is_running():
        timer_loop.start()


# ───────────────────────── COMMANDS ──────────────────────

@bot.slash_command(name="setskladchannel", guild_ids=[GUILD_ID])
async def setskladchannel(ctx, channel: discord.TextChannel):
    if not has_access(ctx.author):
        return await ctx.respond("❌ Нет прав", ephemeral=True)

    set_channel(ctx.guild.id, channel.id, "sklad")
    await ctx.respond(f"✅ Склад: {channel.mention}", ephemeral=True)


@bot.slash_command(name="setsimpletimer", guild_ids=[GUILD_ID])
async def setsimpletimer(ctx, channel: discord.TextChannel):
    if not has_access(ctx.author):
        return await ctx.respond("❌ Нет прав", ephemeral=True)

    set_channel(ctx.guild.id, channel.id, "simple")
    await ctx.respond(f"✅ Таймер: {channel.mention}", ephemeral=True)


@bot.slash_command(name="таймер", guild_ids=[GUILD_ID])
async def timer(ctx, название: str, days: int = 0, hours: int = 0, minutes: int = 0):

    if days == 0 and hours == 0 and minutes == 0:
        return await ctx.respond("❌ Укажи время", ephemeral=True)

    channel_id = get_channel(ctx.guild.id, "simple")

    if not channel_id:
        return await ctx.respond("❌ Канал не задан", ephemeral=True)

    if ctx.channel.id != channel_id:
        return await ctx.respond("❌ Неверный канал", ephemeral=True)

    end = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        days=days, hours=hours, minutes=minutes
    )

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
        author=ctx.author.id
    )

    await ctx.respond("✅ Таймер создан", ephemeral=True)


@bot.slash_command(name="склад", guild_ids=[GUILD_ID])
async def sklad(ctx, гекс: str, регион: str, склад: str, пароль: str):

    channel_id = get_channel(ctx.guild.id, "sklad")

    if channel_id and ctx.channel.id != channel_id:
        return await ctx.respond("❌ Неверный канал", ephemeral=True)

    end_ts = int(
        (datetime.datetime.now(datetime.timezone.utc)
         + datetime.timedelta(hours=48)).timestamp()
    )

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


# ───────────────────────── RUN ───────────────────────────

bot.run(os.environ.get("DISCORD_BOT_TOKEN"))
