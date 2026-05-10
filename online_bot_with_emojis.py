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
GUILD_ID = 419565206335651840

ONLINE_CHANNEL_NAME_TEMPLATE = "🟢｜foxhole-онлайн-{count}"

LAST_CHANNEL_RENAME = {}
CHANNEL_RENAME_COOLDOWN_SECONDS = 30

ONLINE_SETTER_ROLE_IDS = [
    1420081710510379079,
    694197038362918923,
    475990315623251969,
    1397716497928949843,
    1397716702242013276,
    422500854910681089,
    1224787828815171595,
]

ONLINE_VISIBLE_ROLE_IDS = [
    556531210323492874,
]

ONLINE_ROLE_GROUPS = [
    {
        "title": "👑 Капитан",
        "role_ids": [422500854910681089],
    },
    {
        "title": "🛡️ Зам. капитана",
        "role_ids": [1224787828815171595],
    },
    {
        "title": "🎖️ Офицерский состав",
        "role_ids": [
            1501797623550578889,
            1501799982389264494,
            1501800617176203444,
            1501801258594467870,
            1501801774414299198,
            1501801928915419269,
        ],
    },
    {
        "title": "⚔️ Сержантский состав",
        "role_ids": [
            1501798393909870652,
            1501799300387307540,
            1501799681024462980,
            1501800738312159313,
            1501801036539756635,
        ],
    },
    {
        "title": "🪖 Бойцы",
        "role_ids": [1210544000600375297],
    },
    {
        "title": "🩸 Свежая кровь",
        "role_ids": [831271336612593714],
    },
]

ONLINE_ACTIVITY_GROUPS = [
    {"title": "✈️ С ролью пилот", "role_ids": [1463921076433064227]},
    {"title": "🦊 С ролью партизан", "role_ids": [1501768722304598167]},
    {"title": "🔫 С ролью пехота", "role_ids": [1241513004928077885]},
    {"title": "💥 С ролью артиллерия", "role_ids": [1241663050776580189]},
    {"title": "🛡️ С ролью Танк", "role_ids": [1241512966235623505]},
    {"title": "🚢 С ролью Флот", "role_ids": [1315281120400638034]},
    {"title": "🌊 С ролью Подводник", "role_ids": [1463921275775877184]},
    {"title": "📦 С ролью логист", "role_ids": [1194748526131945502]},
    {"title": "🏗️ С ролью заводчанин-строитель", "role_ids": [1241513032304295947]},
    {"title": "🚨 С ролью QRF", "role_ids": [1390688779056058429]},
    {"title": "🛩️ С ролью Авиация", "role_ids": [1463925850654248991]},
]


# =========================
# BOT
# =========================
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


def member_has_any_role(member, role_ids):
    member_role_ids = {role.id for role in member.roles}
    return bool(set(role_ids) & member_role_ids)


def get_online_members(guild):
    online_members = []

    for member in guild.members:
        if member.bot:
            continue

        if not is_playing_foxhole(member):
            continue

        online_members.append(member)

    return online_members


def get_mentions(members):
    if not members:
        return "—"

    shown_members = members[:25]
    text = "\n".join(member.mention for member in shown_members)

    if len(members) > 25:
        text += f"\n...и ещё {len(members) - 25}"

    return text


def get_members_by_roles(online_members, role_ids):
    found_members = []

    for member in online_members:
        if member_has_any_role(member, role_ids):
            found_members.append(member)

    return found_members


def build_online_embed(guild):
    online_members = get_online_members(guild)

    embed = discord.Embed(
        title="🟢 Онлайн Foxhole",
        color=discord.Color.green(),
        description=f"👥 Всего в игре: {len(online_members)}",
    )

    for group in ONLINE_ROLE_GROUPS:
        members = get_members_by_roles(
            online_members,
            group["role_ids"],
        )

        if not members:
            continue

        embed.add_field(
            name=f"{group['title']} — {len(members)}",
            value=get_mentions(members),
            inline=False,
        )

    activity_fields = []

    for group in ONLINE_ACTIVITY_GROUPS:
        members = get_members_by_roles(
            online_members,
            group["role_ids"],
        )

        if not members:
            continue

        activity_fields.append((group["title"], members))

    if activity_fields:
        embed.add_field(
            name="--------------------",
            value="📌 Роды деятельности:",
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


async def update_online_channel_name(guild, channel, count):
    now = datetime.datetime.now(datetime.timezone.utc)
    last_rename = LAST_CHANNEL_RENAME.get(guild.id)

    if last_rename:
        diff = (now - last_rename).total_seconds()
        if diff < CHANNEL_RENAME_COOLDOWN_SECONDS:
            return

    new_name = ONLINE_CHANNEL_NAME_TEMPLATE.format(count=count)

    if getattr(channel, "name", None) == new_name:
        return

    try:
        await channel.edit(name=new_name)
        LAST_CHANNEL_RENAME[guild.id] = now

    except discord.HTTPException as e:
        logger.warning(
            f"Discord ограничил переименование канала. "
            f"Канал: {channel.id}, ошибка: {e}"
        )

    except Exception:
        logger.error(traceback.format_exc())


async def update_online_for_guild(guild):
    row = OnlineChannel.get_or_none(OnlineChannel.guild_id == guild.id)

    if not row:
        return

    channel = guild.get_channel_or_thread(row.channel_id)

    if not channel:
        logger.warning(f"Канал/ветка не найдены: {row.channel_id}")
        return

    online_members = get_online_members(guild)
    online_count = len(online_members)

    await update_online_channel_name(guild, channel, online_count)

    embed = build_online_embed(guild)

    msg_row = OnlineMessage.get_or_none(
        OnlineMessage.guild_id == guild.id
    )

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
# COMMAND /онлайн
# =========================
@bot.slash_command(name="онлайн", guild_ids=[GUILD_ID])
async def онлайн(
    ctx,
    канал: discord.TextChannel = None,
    айди_ветки: str = None,
):
    user_role_ids = {role.id for role in ctx.author.roles}

    if not (user_role_ids & set(ONLINE_SETTER_ROLE_IDS)):
        return await ctx.respond(
            "❌ У тебя нет прав выставлять чат онлайна.",
            ephemeral=True,
        )

    target_id = None

    if канал:
        target_id = канал.id

    elif айди_ветки:
        try:
            target_id = int(айди_ветки)

        except ValueError:
            return await ctx.respond(
                "❌ Неверный ID ветки.",
                ephemeral=True,
            )

    else:
        return await ctx.respond(
            "❌ Укажи канал или айди_ветки.",
            ephemeral=True,
        )

    row = OnlineChannel.get_or_none(
        OnlineChannel.guild_id == ctx.guild.id
    )

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
