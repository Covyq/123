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


# ─── CHANNEL SYSTEM ──────────────────────────────────────
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


# ─── VIEWS (NO PERSISTENT SYSTEM) ───────────────────────
class SkladView(View):
    def __init__(self):
        super().__init__()

    async def interaction_check(self, interaction):
        return True

    @discord.ui.button(label="Обновить склад", style=discord.ButtonStyle.green)
    async def update(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer()

        row = Timer.get_or_none(Timer.message_id == interaction.message.id)
        if not row:
            return

        new_end = int(datetime.datetime.now(datetime.timezone.utc).timestamp() + 172800)
        row.time_end = new_end
        row.save()

        await interaction.message.edit(
            content=f"{row.text}\n\n⏰ 48 часов (<t:{new_end}:R>)",
            view=self
        )

    @discord.ui.button(label="Удалить", style=discord.ButtonStyle.red)
    async def delete(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer()

        row = Timer.get_or_none(Timer.message_id == interaction.message.id)
        if row and interaction.user.id == row.author:
            row.delete_instance()
            await interaction.message.delete()


class TimerView(View):
    def __init__(self):
        super().__init__()

    @discord.ui.button(label="Удалить", style=discord.ButtonStyle.red)
    async def delete(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer()

        row = Timer.get_or_none(Timer.message_id == interaction.message.id)
        if row and interaction.user.id == row.author:
            row.delete_instance()
            await interaction.message.delete()


class MPFView(View):
    def __init__(self):
        super().__init__()

    @discord.ui.button(label="Забрал заказ", style=discord.ButtonStyle.green)
    async def take(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer()

        row = Timer.get_or_none(Timer.message_id == interaction.message.id)
        if not row or row.taken_by:
            return

        row.taken_by = interaction.user.id
        row.save()

        await interaction.message.edit(
            content=interaction.message.content + f"\n\n📦 Забрал: {interaction.user.display_name}",
            view=self
        )

    @discord.ui.button(label="Удалить", style=discord.ButtonStyle.red)
    async def delete(self, interaction: discord.Interaction, button: Button):
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
            name = member.display_name if member else "неизвестный"

            if t.kind == "mpf":
                what = t.text.split("Что:")[-1].strip() if "Что:" in t.text else "неизвестно"

                await msg.edit(
                    content=
                    f"👤 Кто поставил: {name}\n"
                    f"📦 Что поставил: {what}\n"
                    f"📦 Ящиков: {t.boxes}\n"
                    f"✅ Статус: Завершено",
                    view=MPFView()
                )

                t.time_end = now + 10**9
                t.save()
                continue

            await msg.edit(content=f"✅ {t.text} завершён {name}")
            t.delete_instance()

        except Exception:
            print(traceback.format_exc())
            t.delete_instance()


# ─── READY ───────────────────────────────────────────────
@bot.event
async def on_ready():
    load_channels()
    print(f"Bot online {bot.user}")


# ─── CHANNEL COMMANDS ───────────────────────────────────
@bot.slash_command(name="setskladchannel", guild_ids=[GUILD_ID])
async def setskladchannel(ctx, channel: discord.TextChannel):
    if not has_access(ctx.author):
        return await ctx.respond("❌ нет прав", ephemeral=True)

    set_channel(ctx.guild.id, channel.id, "sklad")
    await ctx.respond("✅ склад установлен", ephemeral=True)


@bot.slash_command(name="setsimpletimer", guild_ids=[GUILD_ID])
async def setsimpletimer(ctx, channel: discord.TextChannel):
    if not has_access(ctx.author):
        return await ctx.respond("❌ нет прав", ephemeral=True)

    set_channel(ctx.guild.id, channel.id, "simple")
    await ctx.respond("✅ таймер установлен", ephemeral=True)


@bot.slash_command(name="setmpf", guild_ids=[GUILD_ID])
async def setmpf(ctx, channel: discord.TextChannel):
    if not has_access(ctx.author):
        return await ctx.respond("❌ нет прав", ephemeral=True)

    set_channel(ctx.guild.id, channel.id, "mpf")
    await ctx.respond("✅ MPF установлен", ephemeral=True)


# ─── RUN ────────────────────────────────────────────────
bot.run(os.environ["DISCORD_BOT_TOKEN"])
