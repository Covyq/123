# CLEAN BUILD: Discord Timer Bot with MPF system

import os
import datetime
import traceback
import discord
from discord.ext import tasks
from discord.ui import View, Button
from peewee import *

# CONFIG
GUILD_ID = 419565206335651840

ALLOWED_ROLE_IDS = [
    1493199914572972032,
    123456789012345678,
    987654321098765432
]

bot = discord.Bot(intents=discord.Intents.all(), debug_guilds=[GUILD_ID])

db = SqliteDatabase("TimerDataBase.db")

# MODELS
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

    time_end = BigIntegerField()
    author = BigIntegerField()
    kind = TextField(default="timer")

    # MPF
    item_name = TextField(null=True)
    boxes = IntegerField(null=True)
    taken_by = BigIntegerField(null=True)

# INIT DB
db.connect(reuse_if_open=True)
db.create_tables([ChannelConfig, Timer])

# CACHE
CHANNEL_CACHE = {"sklad": {}, "simple": {}, "mpf": {}}

def load_channels():
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

# PERMS

def has_access(member):
    return member.guild_permissions.administrator or any(
        r.id in ALLOWED_ROLE_IDS for r in member.roles
    )

# MPF VIEW
class MPFView(View):
    def __init__(self, show_take=False):
        super().__init__(timeout=None)

        if show_take:
            take = Button(label="Забрал заказ", style=discord.ButtonStyle.green)
            take.callback = self.take
            self.add_item(take)

        delete = Button(label="Удалить таймер", style=discord.ButtonStyle.red)
        delete.callback = self.delete
        self.add_item(delete)

    async def take(self, interaction):
        await interaction.response.defer()

        row = Timer.get_or_none(Timer.message_id == interaction.message.id)
        if not row:
            return await interaction.followup.send("❌ Не найден", ephemeral=True)

        if row.taken_by:
            return await interaction.followup.send("❌ Уже забрали", ephemeral=True)

        row.taken_by = interaction.user.id
        row.save()

        member = interaction.guild.get_member(interaction.user.id)
        nickname = member.display_name if member else "пользователь"

        await interaction.message.edit(
            content=interaction.message.content + f"\n\n📦 Забрал: {nickname}",
            view=self
        )

        await interaction.followup.send("✅ Забрал", ephemeral=True)

    async def delete(self, interaction):
        await interaction.response.defer()

        row = Timer.get_or_none(Timer.message_id == interaction.message.id)
        if not row:
            return await interaction.followup.send("❌ Не найден", ephemeral=True)

        if interaction.user.id != row.author:
            return await interaction.followup.send("❌ Только автор", ephemeral=True)

        row.delete_instance()
        await interaction.message.delete()

# LOOP
@tasks.loop(seconds=30)
async def loop():
    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())

    expired = Timer.select().where(Timer.time_end < now)

    for t in expired:
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

            if t.kind == "mpf":
                member = guild.get_member(t.author)
                nickname = member.display_name if member else "пользователь"

                await msg.edit(
                    content=(
                        f"👤 Кто поставил: {nickname}\n"
                        f"📦 Что поставил: {t.item_name}\n"
                        f"📦 Ящиков: {t.boxes}\n"
                        f"✅ Статус: выполнено"
                    ),
                    view=MPFView(show_take=True)
                )

                t.time_end = now + 10**9
                t.save()
                continue

            t.delete_instance()

        except Exception:
            print(traceback.format_exc())
            t.delete_instance()

# READY
@bot.event
async def on_ready():
    print(f"Bot online {bot.user}")

    load_channels()
    bot.add_view(MPFView())

    if not loop.is_running():
        loop.start()

# COMMANDS
@bot.slash_command(name="setmpf", guild_ids=[GUILD_ID])
async def setmpf(ctx, channel: discord.TextChannel):
    if not has_access(ctx.author):
        return await ctx.respond("❌ Нет прав", ephemeral=True)

    set_channel(ctx.guild.id, channel.id, "mpf")
    await ctx.respond("✅ MPF канал установлен", ephemeral=True)

@bot.slash_command(name="мпф", guild_ids=[GUILD_ID])
async def mpf(ctx, что_поставил: str, ящиков: int, days: int = 0, hours: int = 0, minutes: int = 0):
    channel_id = get_channel(ctx.guild.id, "mpf")
    if not channel_id:
        return await ctx.respond("❌ канал не задан", ephemeral=True)

    if ctx.channel.id != channel_id:
        return await ctx.respond("❌ не тот канал", ephemeral=True)

    end = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        days=days, hours=hours, minutes=minutes
    )
    end_ts = int(end.timestamp())

    nickname = ctx.author.display_name

    msg = await ctx.send(
        f"👤 Кто поставил: {nickname}\n"
        f"📦 Что поставил: {что_поставил}\n"
        f"📦 Ящиков: {ящиков}\n"
        f"⌛ Статус: ожидание\n\n"
        f"⏰ <t:{end_ts}:R>",
        view=MPFView()
    )

    Timer.create(
        guild_id=ctx.guild.id,
        channel_id=ctx.channel.id,
        message_id=msg.id,
        time_end=end_ts,
        author=ctx.author.id,
        kind="mpf",
        item_name=что_поставил,
        boxes=ящиков,
        taken_by=None
    )

    await ctx.respond("✅ MPF создан", ephemeral=True)

# RUN
bot.run(os.environ.get("DISCORD_BOT_TOKEN"))
