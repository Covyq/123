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

# ─── CACHE ──────────────────────────────────────────────
CHANNEL_CACHE = {
    "sklad": {},
    "simple": {},
    "mpf": {}
}

# ─── DB ─────────────────────────────────────────────────
class BaseModel(Model):
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


class MPF(BaseModel):
    guild_id = BigIntegerField()
    channel_id = BigIntegerField()
    message_id = BigIntegerField()

    item = TextField()
    boxes = IntegerField()

    time_end = BigIntegerField()
    author = BigIntegerField()

    claimed_by = BigIntegerField(null=True)


db.connect(reuse_if_open=True)
db.create_tables([ChannelConfig, Timer, MPF])

# ─── CHANNELS ───────────────────────────────────────────
def load_channels():
    global CHANNEL_CACHE
    CHANNEL_CACHE = {"sklad": {}, "simple": {}, "mpf": {}}

    for row in ChannelConfig.select():
        CHANNEL_CACHE.setdefault(row.type, {})[row.guild_id] = row.channel_id


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

    CHANNEL_CACHE.setdefault(type_, {})[guild_id] = channel_id


def get_channel(guild_id, type_):
    return CHANNEL_CACHE.get(type_, {}).get(guild_id)


# ─── PERMS ──────────────────────────────────────────────
def has_access(member):
    return member.guild_permissions.administrator or any(
        r.id in ALLOWED_ROLE_IDS for r in member.roles
    )


# ─── SKLAD VIEW ─────────────────────────────────────────
class SkladView(View):
    def __init__(self):
        super().__init__(timeout=None)

        self.add_item(Button(label="Обновить", style=discord.ButtonStyle.green, custom_id="sklad_up"))
        self.add_item(Button(label="Удалить", style=discord.ButtonStyle.red, custom_id="sklad_del"))

        self.children[0].callback = self.update
        self.children[1].callback = self.delete

    async def update(self, interaction):
        row = Timer.get_or_none(Timer.message_id == interaction.message.id)
        if not row:
            return await interaction.response.send_message("❌ Нет", ephemeral=True)

        new_end = int((datetime.datetime.utcnow() + datetime.timedelta(hours=48)).timestamp())
        row.time_end = new_end
        row.save()

        await interaction.message.edit(
            content=f"{row.text}\n\n⏰ 48ч (<t:{new_end}:R>)",
            view=self
        )

        await interaction.response.send_message("✅ Обновлено", ephemeral=True)

    async def delete(self, interaction):
        row = Timer.get_or_none(Timer.message_id == interaction.message.id)
        if not row:
            return await interaction.response.send_message("❌ Нет", ephemeral=True)

        if interaction.user.id != row.author:
            return await interaction.response.send_message("❌ Только автор", ephemeral=True)

        row.delete_instance()
        await interaction.message.delete()


# ─── TIMER VIEW ─────────────────────────────────────────
class TimerView(View):
    def __init__(self):
        super().__init__(timeout=None)

        btn = Button(label="Удалить", style=discord.ButtonStyle.red)
        btn.callback = self.delete
        self.add_item(btn)

    async def delete(self, interaction):
        row = Timer.get_or_none(Timer.message_id == interaction.message.id)

        if not row:
            return await interaction.response.send_message("❌ Нет", ephemeral=True)

        if interaction.user.id != row.author:
            return await interaction.response.send_message("❌ Только автор", ephemeral=True)

        row.delete_instance()
        await interaction.message.delete()


# ─── MPF VIEW ───────────────────────────────────────────
class MPFView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Забрал заказ", style=discord.ButtonStyle.green)
    async def claim(self, interaction, button):
        row = MPF.get_or_none(MPF.message_id == interaction.message.id)

        if not row:
            return await interaction.response.send_message("❌ Нет", ephemeral=True)

        if row.claimed_by:
            return await interaction.response.send_message("❌ Уже забрали", ephemeral=True)

        row.claimed_by = interaction.user.id
        row.save()

        await interaction.message.edit(
            content=interaction.message.content + f"\n\n📦 Забрал: {interaction.user.mention}",
            view=self
        )

        await interaction.response.send_message("✅ Забрали", ephemeral=True)

    @discord.ui.button(label="Удалить заказ", style=discord.ButtonStyle.red)
    async def delete(self, interaction, button):
        row = MPF.get_or_none(MPF.message_id == interaction.message.id)

        if not row:
            return await interaction.response.send_message("❌ Нет", ephemeral=True)

        if interaction.user.id != row.author:
            return await interaction.response.send_message("❌ Только автор", ephemeral=True)

        row.delete_instance()
        await interaction.message.delete()


# ─── LOOP ──────────────────────────────────────────────
@tasks.loop(seconds=30)
async def loop():
    now = int(datetime.datetime.utcnow().timestamp())

    # TIMER
    for t in Timer.select().where(Timer.time_end < now):
        try:
            ch = bot.get_guild(t.guild_id).get_channel(t.channel_id)
            msg = await ch.fetch_message(t.message_id)
            await msg.edit(content=f"✅ {t.text} завершён")
        except:
            print(traceback.format_exc())
        t.delete_instance()

    # MPF
    for m in MPF.select().where(MPF.time_end < now):
        try:
            ch = bot.get_guild(m.guild_id).get_channel(m.channel_id)
            msg = await ch.fetch_message(m.message_id)
            await msg.edit(content=msg.content.replace("⌛", "✅"))
        except:
            print(traceback.format_exc())


# ─── READY ─────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"Bot: {bot.user}")

    db.connect(reuse_if_open=True)
    db.create_tables([ChannelConfig, Timer, MPF])

    load_channels()

    bot.add_view(SkladView())
    bot.add_view(TimerView())
    bot.add_view(MPFView())

    if not loop.is_running():
        loop.start()


# ─── COMMANDS ──────────────────────────────────────────

@bot.slash_command(name="setsklad", guild_ids=[GUILD_ID])
async def setsklad(ctx, channel: discord.TextChannel):
    if not has_access(ctx.author):
        return await ctx.respond("❌ Нет прав", ephemeral=True)

    set_channel(ctx.guild.id, channel.id, "sklad")
    await ctx.respond("✅ SKLAD OK", ephemeral=True)


@bot.slash_command(name="setsimple", guild_ids=[GUILD_ID])
async def setsimple(ctx, channel: discord.TextChannel):
    if not has_access(ctx.author):
        return await ctx.respond("❌ Нет прав", ephemeral=True)

    set_channel(ctx.guild.id, channel.id, "simple")
    await ctx.respond("✅ TIMER OK", ephemeral=True)


@bot.slash_command(name="setmpf", guild_ids=[GUILD_ID])
async def setmpf(ctx, channel: discord.TextChannel):
    if not has_access(ctx.author):
        return await ctx.respond("❌ Нет прав", ephemeral=True)

    set_channel(ctx.guild.id, channel.id, "mpf")
    await ctx.respond("✅ MPF OK", ephemeral=True)


# ─── SKLAD ─────────────────────────────────────────────
@bot.slash_command(name="склад", guild_ids=[GUILD_ID])
async def sklad(ctx, гекс: str, регион: str, склад: str, пароль: str):

    ch = get_channel(ctx.guild.id, "sklad")
    if ch and ctx.channel.id != ch:
        return await ctx.respond("❌ Неверный канал", ephemeral=True)

    end = int((datetime.datetime.utcnow() + datetime.timedelta(hours=48)).timestamp())

    text = f"{ctx.author.mention}\n{гекс}\n{регион}\n{склад}\n{пароль}"

    msg = await ctx.send(f"{text}\n⏰ <t:{end}:R>", view=SkladView())

    Timer.create(
        guild_id=ctx.guild.id,
        channel_id=ctx.channel.id,
        message_id=msg.id,
        text=text,
        time_end=end,
        author=ctx.author.id
    )

    await ctx.respond("✅ Склад создан", ephemeral=True)


# ─── TIMER ──────────────────────────────────────────────
@bot.slash_command(name="таймер", guild_ids=[GUILD_ID])
async def timer(ctx, название: str, days: int = 0, hours: int = 0, minutes: int = 0):

    ch = get_channel(ctx.guild.id, "simple")

    if not ch or ctx.channel.id != ch:
        return await ctx.respond("❌ Нельзя", ephemeral=True)

    end = datetime.datetime.utcnow() + datetime.timedelta(days=days, hours=hours, minutes=minutes)

    msg = await ctx.send(
        f"👤 {ctx.author.mention}\n📌 {название}\n⏰ <t:{int(end.timestamp())}:R>",
        view=TimerView()
    )

    Timer.create(
        guild_id=ctx.guild.id,
        channel_id=ctx.channel.id,
        message_id=msg.id,
        text=название,
        time_end=int(end.timestamp()),
        author=ctx.author.id
    )

    await ctx.respond("✅ OK", ephemeral=True)


# ─── MPF ───────────────────────────────────────────────
@bot.slash_command(name="мпф", guild_ids=[GUILD_ID])
async def mpf(ctx, что: str, ящики: int, days: int = 0, hours: int = 0, minutes: int = 0):

    ch = get_channel(ctx.guild.id, "mpf")

    if not ch or ctx.channel.id != ch:
        return await ctx.respond("❌ Нельзя", ephemeral=True)

    end = datetime.datetime.utcnow() + datetime.timedelta(days=days, hours=hours, minutes=minutes)

    msg = await ctx.send(
        f"👤 {ctx.author.mention}\n📦 {что}\n📦 {ящики}\n⌛ ⌛\n⏰ <t:{int(end.timestamp())}:R>",
        view=MPFView()
    )

    MPF.create(
        guild_id=ctx.guild.id,
        channel_id=ctx.channel.id,
        message_id=msg.id,
        item=что,
        boxes=ящики,
        time_end=int(end.timestamp()),
        author=ctx.author.id
    )

    await ctx.respond("✅ MPF создан", ephemeral=True)


# ─── RUN ───────────────────────────────────────────────
bot.run(os.environ.get("DISCORD_BOT_TOKEN"))
