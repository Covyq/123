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

# CACHE
CHANNEL_CACHE = {"sklad": {}, "simple": {}, "mpf": {}}

# DB
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

# CHANNELS
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

# PERMS
def has_access(member):
    return member.guild_permissions.administrator or any(
        r.id in ALLOWED_ROLE_IDS for r in member.roles
    )

# CLEAN
def clean_channels():
    for row in ChannelConfig.select():
        guild = bot.get_guild(row.guild_id)
        if not guild or guild.get_channel_or_thread(row.channel_id) is None:
            row.delete_instance()

# VIEWS
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

        row = Timer.get_or_none(
            (Timer.message_id == interaction.message.id) &
            (Timer.channel_id == interaction.channel.id)
        )

        if not row:
            return await interaction.followup.send("❌ Не найдено", ephemeral=True)

        # продлеваем, а не сбрасываем
        new_end = row.time_end + 48 * 3600
        row.time_end = new_end
        row.save()

        await interaction.message.edit(
            content=f"{row.text}\n\n⏰ Продлено на 48 часов (<t:{new_end}:R>)",
            view=self
        )

        await interaction.followup.send("✅ Продлено", ephemeral=True)

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
        nickname = member.display_name if member else "пользователь"

        await interaction.message.edit(
            content=interaction.message.content + f"\n\n📦 Забрал: {nickname}",
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

            channel = guild.get_channel_or_thread(t.channel_id)
            if not channel:
                t.delete_instance()
                continue

            try:
                msg = await channel.fetch_message(t.message_id)
            except:
                t.delete_instance()
                continue

            member = guild.get_member(t.author)
            nickname = member.display_name if member else "пользователь"
            mention = member.mention if member else "пользователь"

            if t.kind == "sklad":
                await msg.edit(
                    content=f"✅ Склад завершён {nickname}\n⏰ <t:{now}:R>",
                    view=None
                )
                t.delete_instance()
                continue

            if t.kind == "timer":
                await msg.edit(
                    content=f"👤 {mention}\n📌 {t.text}\n✅ Статус: выполнено",
                    view=TimerView()
                )
                t.time_end = now + 10**9
                t.save()
                continue

            if t.kind == "mpf":
                try:
                    item = t.text.split("📦 Что поставил: ")[1].splitlines()[0]
                except:
                    item = "неизвестно"

                await msg.edit(
                    content=(
                        f"👤 Кто поставил: {nickname}\n"
                        f"📦 Что поставил: {item}\n"
                        f"📦 Ящиков: {t.boxes}\n"
                        f"Статус: ✅"
                    ),
                    view=MPFView(show_take=True)
                )

                t.time_end = now + 10**9
                t.save()

        except Exception:
            print(traceback.format_exc())
            t.delete_instance()

# READY + PERSISTENT VIEWS
async def setup_hook():
    bot.add_view(SkladView())
    bot.add_view(TimerView())
    bot.add_view(MPFView())

@bot.event
async def on_ready():
    print(f"Bot online {bot.user}")
    clean_channels()
    load_channels()
    if not loop.is_running():
        loop.start()

# COMMAND FIXES

@bot.slash_command(name="setmpf", guild_ids=[GUILD_ID])
async def setmpf(ctx, channel: discord.TextChannel = None, thread_id: str = None):
    if not has_access(ctx.author):
        return await ctx.respond("❌ Нет прав", ephemeral=True)

    if channel:
        target_id = channel.id
    elif thread_id:
        try:
            target_id = int(thread_id)
        except:
            return await ctx.respond("❌ Неверный ID", ephemeral=True)
    else:
        return await ctx.respond("❌ Укажи канал или thread_id", ephemeral=True)

    set_channel(ctx.guild.id, target_id, "mpf")
    await ctx.respond(f"✅ MPF установлен: `{target_id}`", ephemeral=True)

@bot.slash_command(name="мпф", guild_ids=[GUILD_ID])
async def mpf(ctx, что_поставил: str, ящиков: int, days: int = 0, hours: int = 0, minutes: int = 0):
    channel_id = get_channel(ctx.guild.id, "mpf")

    if not channel_id or (
        ctx.channel.id != channel_id and
        getattr(ctx.channel, "parent_id", None) != channel_id
    ):
        return await ctx.respond("❌ не тот канал", ephemeral=True)

    end = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days, hours=hours, minutes=minutes)
    end_ts = int(end.timestamp())

    text = (
        f"👤 Кто поставил: {ctx.author.display_name}\n"
        f"📦 Что поставил: {что_поставил}\n"
        f"📦 Ящиков: {ящиков}\n"
        f"⌛ <t:{end_ts}:R>\n"
        f"Статус: ожидание"
    )

    msg = await ctx.send(text, view=MPFView(show_take=False))

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

    await ctx.respond("✅ MPF создан", ephemeral=True)

# RUN
bot.run(os.environ.get("DISCORD_BOT_TOKEN"))
