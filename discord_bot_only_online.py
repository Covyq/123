import os
import datetime
import traceback
import logging

import discord
from discord.ext import tasks
from peewee import *


# =========================
# НАСТРОЙКИ
# =========================
GUILD_ID = 1494712012314509372

ONLINE_ROLE_GROUPS = [
    {
        "title": "Капитан",
        "role_ids": [],
    },
    {
        "title": "Зам. капитана",
        "role_ids": [],
    },
    {
        "title": "Офицерский состав",
        "role_ids": [],
    },
    {
        "title": "Сержантский состав",
        "role_ids": [],
    },
    {
        "title": "Бойцы",
        "role_ids": [],
    },
    {
        "title": "Свежая кровь",
        "role_ids": [],
    },
]

ONLINE_ACTIVITY_GROUPS = [
    {"title": "С ролью пехота", "role_ids": []},
    {"title": "С ролью артиллерия", "role_ids": []},
    {"title": "С ролью Танк", "role_ids": []},
    {"title": "С ролью Флот", "role_ids": []},
    {"title": "С ролью Подводник", "role_ids": []},
    {"title": "С ролью партизан", "role_ids": []},
    {"title": "С ролью пилот", "role_ids": []},
    {"title": "С ролью логист", "role_ids": []},
    {"title": "С ролью заводчанин", "role_ids": []},
    {"title": "С ролью QRF", "role_ids": []},
]

intents = discord.Intents.all()
bot = discord.Bot(intents=intents, debug_guilds=[GUILD_ID])

db = SqliteDatabase("OnlineDatabase.db")


# =========================
# БАЗА ДАННЫХ
# =========================
class BaseModel(Model):
    class Meta:
        database = db


class OnlineChannel(BaseModel):
    guild_id = BigIntegerField(unique=True)
    channel_id = BigIntegerField()


class OnlineMessage(BaseModel):
    guild_id = BigIntegerField(unique=True)
    channel_id = BigIntegerField()
    message_id = BigIntegerField()


db.connect(reuse_if_open=True)
db.create_tables([OnlineChannel, OnlineMessage])


# =========================
# ЛОГИ
# =========================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# =========================
# ONLINE LOGIC
# =========================
def is_playing_foxhole(member):
    for activity in getattr(member, "activities", []):
        name = getattr(activity, "name", "")
        if name and name.lower() == "foxhole":
            return True
    return False


def get_mentions(members):
    return "\n".join(member.mention for member in members[:25])


def build_online_embed(guild):
    online_members = [
        m for m in guild.members
        if not m.bot and is_playing_foxhole(m)
    ]

    embed = discord.Embed(
        title="🟢 Онлайн Foxhole",
        color=discord.Color.green(),
        description=f"👥 Всего в игре: {len(online_members)}",
    )

    # Основные роли
    for group in ONLINE_ROLE_GROUPS:
        members = []

        for member in online_members:
            role_ids = {r.id for r in member.roles}

            if set(group["role_ids"]) & role_ids:
                members.append(member)

        if not members:
            continue

        embed.add_field(
            name=f"{group['title']} — {len(members)}",
            value=get_mentions(members),
            inline=False,
        )

    # Роды деятельности
    activity_fields = []

    for group in ONLINE_ACTIVITY_GROUPS:
        members = []

        for member in online_members:
            role_ids = {r.id for r in member.roles}

            if set(group["role_ids"]) & role_ids:
                members.append(member)

        if not members:
            continue

        activity_fields.append((group["title"], members))

    if activity_fields:
        embed.add_field(
            name="--------------------",
            value="Роды деятельности:",
            inline=False,
        )

        for title, members in activity_fields:
            embed.add_field(
                name=f"{title}: {len(members)}",
                value=get_mentions(members),
                inline=False,
            )

    embed.set_footer(text="Обновляется каждые 30 секунд")
    embed.timestamp = datetime.datetime.now(datetime.timezone.utc)

    return embed


async def update_online_for_guild(guild):
    row = OnlineChannel.get_or_none(OnlineChannel.guild_id == guild.id)
    if not row:
        return

    channel = guild.get_channel_or_thread(row.channel_id)
    if not channel:
        return

    embed = build_online_embed(guild)

    msg_row = OnlineMessage.get_or_none(OnlineMessage.guild_id == guild.id)

    if msg_row:
        try:
            msg = await channel.fetch_message(msg_row.message_id)
            await msg.edit(embed=embed)
            return
        except Exception:
            logger.error(traceback.format_exc())

    try:
        msg = await channel.send(embed=embed)

        OnlineMessage.delete().where(
            OnlineMessage.guild_id == guild.id
        ).execute()

        OnlineMessage.create(
            guild_id=guild.id,
            channel_id=channel.id,
            message_id=msg.id,
        )

    except Exception:
        logger.error(traceback.format_exc())


@tasks.loop(seconds=30)
async def online_loop():
    for guild in bot.guilds:
        try:
            await update_online_for_guild(guild)
        except Exception:
            logger.error(traceback.format_exc())


# =========================
# COMMAND
# =========================
@bot.slash_command(name="онлайн", guild_ids=[GUILD_ID])
async def онлайн(
    ctx,
    канал: discord.TextChannel = None,
    айди_ветки: str = None,
):
    target_id = None

    if канал:
        target_id = канал.id
    elif айди_ветки:
        try:
            target_id = int(айди_ветки)
        except ValueError:
            return await ctx.respond("❌ Неверный ID ветки", ephemeral=True)
    else:
        return await ctx.respond("❌ Укажи канал или айди_ветки", ephemeral=True)

    row = OnlineChannel.get_or_none(OnlineChannel.guild_id == ctx.guild.id)

    if row:
        row.channel_id = target_id
        row.save()
    else:
        OnlineChannel.create(
            guild_id=ctx.guild.id,
            channel_id=target_id,
        )

    await ctx.respond(
        f"✅ Канал онлайна установлен: `{target_id}`",
        ephemeral=True,
    )

    await update_online_for_guild(ctx.guild)


# =========================
# READY
# =========================
@bot.event
async def on_ready():
    logger.info(f"Бот онлайн: {bot.user}")

    if not online_loop.is_running():
        online_loop.start()


# =========================
# RUN
# =========================
token = os.environ.get("DISCORD_BOT_TOKEN")

if not token:
    raise RuntimeError("Не найден DISCORD_BOT_TOKEN")

bot.run(token)
