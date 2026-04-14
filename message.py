import os
import datetime
import traceback
import discord
from discord.ext import tasks
from discord.ui import View, Button
from peewee import *

# ───────────────── CONFIG ─────────────────

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

db = SqliteDatabase("bot_v4.db")


# ───────────────── DB ─────────────────

class BaseModel(Model):
    class Meta:
        database = db


class ChannelConfig(BaseModel):
    guild_id = BigIntegerField(index=True)
    channel_type = TextField(index=True)
    channel_id = BigIntegerField()


class Task(BaseModel):
    guild_id = BigIntegerField(index=True)
    channel_id = BigIntegerField(index=True)
    message_id = BigIntegerField(index=True)

    author_id = BigIntegerField(index=True)

    type = TextField()  # timer | sklad | mpf

    title = TextField(null=True)
    item = TextField(null=True)
    boxes = IntegerField(null=True)

    taken_by = BigIntegerField(null=True)

    time_end = BigIntegerField(index=True)
    is_done = BooleanField(default=False)


db.connect(reuse_if_open=True)
db.create_tables([ChannelConfig, Task])


# ───────────────── CACHE ─────────────────

CACHE = {}


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
            channel_type=channel_type,
            channel_id=channel_id
        )

    CACHE[(guild_id, channel_type)] = channel_id


def get_channel(guild_id, channel_type):
    return CACHE.get((guild_id, channel_type))


def load_cache():
    CACHE.clear()
    for r in ChannelConfig.select():
        CACHE[(r.guild_id, r.channel_type)] = r.channel_id


# ───────────────── PERMISSIONS ─────────────────

def has_access(member: discord.Member):
    return member.guild_permissions.administrator or any(
        r.id in ALLOWED_ROLE_IDS for r in member.roles
    )


def now():
    return int(datetime.datetime.now(datetime.timezone.utc).timestamp())


# ───────────────── HELPERS ─────────────────

async def safe_edit(msg, content, view=None):
    try:
        await msg.edit(content=content, view=view)
    except:
        print(traceback.format_exc())


async def safe_delete(msg):
    try:
        await msg.delete()
    except:
        pass


# ───────────────── VIEW MPF ─────────────────

class MPFView(View):
    def __init__(self):
        super().__init__(timeout=None)

        self.add_item(Button(
            label="Забрал заказ",
            style=discord.ButtonStyle.green,
            custom_id="mpf_take"
        ))

        self.add_item(Button(
            label="Удалить",
            style=discord.ButtonStyle.red,
            custom_id="mpf_delete"
        ))

    async def interaction_check(self, interaction):
        return True

    async def mpf_take(self, interaction):
        await interaction.response.defer()

        t = Task.get_or_none(Task.message_id == interaction.message.id)
        if not t:
            return await interaction.followup.send("❌ Не найдено", ephemeral=True)

        if t.taken_by:
            return await interaction.followup.send("❌ Уже забрали", ephemeral=True)

        t.taken_by = interaction.user.id
        t.save()

        await interaction.message.edit(
            content=(
                f"👤 Поставил: <@{t.author_id}>\n"
                f"📦 {t.item}\n"
                f"📦 Ящиков: {t.boxes}\n"
                f"👤 Забрал: {interaction.user.mention}\n"
                f"⏳ Статус: ⌛"
            ),
            view=self
        )

        await interaction.followup.send("✅ Забрали", ephemeral=True)

    async def mpf_delete(self, interaction):
        await interaction.response.defer()

        t = Task.get_or_none(Task.message_id == interaction.message.id)
        if not t:
            return await interaction.followup.send("❌ Не найдено", ephemeral=True)

        if interaction.user.id != t.author_id:
            return await interaction.followup.send("❌ Только автор", ephemeral=True)

        t.delete_instance()
        await safe_delete(interaction.message)


# ───────────────── LOOP ENGINE ─────────────────

@tasks.loop(seconds=30)
async def loop():
    tnow = now()

    tasks = Task.select().where(Task.time_end <= tnow, Task.is_done == False)

    for t in tasks:
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

            member = guild.get_member(t.author_id)
            author = member.mention if member else "пользователь"

            # FINISH TIMER
            if t.type in ("timer", "sklad"):
                await msg.edit(
                    content=f"✅ **{t.title}** завершён {author}\n⏰ <t:{tnow}:R>"
                )

            # FINISH MPF
            elif t.type == "mpf":
                t.is_done = True
                t.save()

                taken = f"<@{t.taken_by}>" if t.taken_by else "никто"

                await msg.edit(
                    content=(
                        f"👤 Поставил: <@{t.author_id}>\n"
                        f"📦 {t.item}\n"
                        f"📦 Ящиков: {t.boxes}\n"
                        f"👤 Забрал: {taken}\n"
                        f"⏳ Статус: ✅"
                    )
                )

        except:
            print(traceback.format_exc())

        t.delete_instance()


# ───────────────── COMMANDS ─────────────────

@bot.slash_command(name="setmpf", guild_ids=[GUILD_ID])
async def setmpf(ctx, channel: discord.TextChannel):
    if not has_access(ctx.author):
        return await ctx.respond("❌ Нет прав", ephemeral=True)

    set_channel(ctx.guild.id, channel.id, "mpf")
    await ctx.respond("✅ MPF канал установлен", ephemeral=True)


@bot.slash_command(name="settimer", guild_ids=[GUILD_ID])
async def settimer(ctx, channel: discord.TextChannel):
    if not has_access(ctx.author):
        return await ctx.respond("❌ Нет прав", ephemeral=True)

    set_channel(ctx.guild.id, channel.id, "timer")
    await ctx.respond("✅ Timer канал установлен", ephemeral=True)


@bot.slash_command(name="setsklad", guild_ids=[GUILD_ID])
async def setsklad(ctx, channel: discord.TextChannel):
    if not has_access(ctx.author):
        return await ctx.respond("❌ Нет прав", ephemeral=True)

    set_channel(ctx.guild.id, channel.id, "sklad")
    await ctx.respond("✅ Sklad канал установлен", ephemeral=True)


# ───────────────── TIMER ─────────────────

@bot.slash_command(name="таймер", guild_ids=[GUILD_ID])
async def timer(ctx, текст: str, дни: int = 0, часы: int = 0, минуты: int = 0):

    if дни + часы + минуты == 0:
        return await ctx.respond("❌ Укажи время", ephemeral=True)

    end = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        days=дни, hours=часы, minutes=минуты
    )

    msg = await ctx.send(
        f"👤 {ctx.author.mention}\n📌 {текст}\n⏰ <t:{int(end.timestamp())}:R>"
    )

    Task.create(
        guild_id=ctx.guild.id,
        channel_id=ctx.channel.id,
        message_id=msg.id,
        author_id=ctx.author.id,
        type="timer",
        title=текст,
        time_end=int(end.timestamp())
    )

    await ctx.respond("✅ Таймер создан", ephemeral=True)


# ───────────────── MPF ─────────────────

@bot.slash_command(name="мпф", guild_ids=[GUILD_ID])
async def mpf(ctx, что: str, ящиков: int, дни: int = 0, часы: int = 0, минуты: int = 0):

    channel_id = get_channel(ctx.guild.id, "mpf")

    if not channel_id:
        return await ctx.respond("❌ MPF канал не задан", ephemeral=True)

    if ctx.channel.id != channel_id:
        return await ctx.respond("❌ Неверный канал", ephemeral=True)

    if дни + часы + минуты == 0:
        return await ctx.respond("❌ Укажи время", ephemeral=True)

    end = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        days=дни, hours=часы, minutes=минуты
    )

    msg = await ctx.send(
        f"👤 Поставил: {ctx.author.mention}\n"
        f"📦 {что}\n"
        f"📦 Ящиков: {ящиков}\n"
        f"⏳ Статус: ⌛\n"
        f"⏰ <t:{int(end.timestamp())}:R>",
        view=MPFView()
    )

    Task.create(
        guild_id=ctx.guild.id,
        channel_id=ctx.channel.id,
        message_id=msg.id,
        author_id=ctx.author.id,
        type="mpf",
        item=что,
        boxes=ящиков,
        time_end=int(end.timestamp())
    )

    await ctx.respond("✅ MPF создан", ephemeral=True)


# ───────────────── READY ─────────────────

@bot.event
async def on_ready():
    print(f"✅ Online: {bot.user}")

    load_cache()

    bot.add_view(MPFView())

    if not loop.is_running():
        loop.start()


# ───────────────── RUN ─────────────────

bot.run(os.environ.get("DISCORD_BOT_TOKEN"))
