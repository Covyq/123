import os
import re
import json
import datetime
import traceback
import logging
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord.ext import tasks
from peewee import *


# =========================
# НАСТРОЙКИ
# =========================
GUILD_ID = 419565206335651840
FOXHOLE_APP_ID = 505460

STEAM_CHECK_CACHE = {}
STEAM_CACHE_SECONDS = 60

LOCAL_TZ = ZoneInfo("Europe/Riga")

ONLINE_SETTER_ROLE_IDS = [
    1420081710510379079,
    694197038362918923,
    475990315623251969,
    1397716497928949843,
    1397716702242013276,
    422500854910681089,
    1224787828815171595,
]

# Техническое значение для совместимости со старой базой.
# В отображении онлайна разделов больше нет.
DEFAULT_GROUP_SECTION = "group"


# =========================
# ЛОГИ
# =========================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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


class SteamLink(BaseModel):
    guild_id = BigIntegerField()
    discord_user_id = BigIntegerField()
    steam_id = CharField()

    class Meta:
        indexes = (
            (("guild_id", "discord_user_id"), True),
        )


class OnlineStatsSample(BaseModel):
    guild_id = BigIntegerField(index=True)
    created_at = DateTimeField(index=True)
    online_count = IntegerField()


class OnlinePeak(BaseModel):
    guild_id = BigIntegerField()
    day = DateField()
    peak_count = IntegerField(default=0)
    peak_at = DateTimeField(null=True)

    class Meta:
        indexes = (
            (("guild_id", "day"), True),
        )


class OnlineRoleGroup(BaseModel):
    guild_id = BigIntegerField(index=True)
    # Поле оставлено для совместимости со старыми версиями базы.
    # Новая логика отображения разделы не использует.
    section = CharField(index=True, default=DEFAULT_GROUP_SECTION)
    title = CharField()
    # ВАЖНО: порядок ID ролей внутри JSON = иерархия ролей внутри группы.
    role_ids_json = TextField(default="[]")
    position = IntegerField(default=1)

    class Meta:
        indexes = (
            (("guild_id", "position"), False),
        )


class OnlineSettings(BaseModel):
    guild_id = BigIntegerField(unique=True)
    steam_required_role_id = BigIntegerField(null=True)


db.connect(reuse_if_open=True)
db.create_tables([
    OnlineChannel,
    OnlineMessage,
    SteamLink,
    OnlineStatsSample,
    OnlinePeak,
    OnlineRoleGroup,
    OnlineSettings,
])


# =========================
# ВРЕМЯ И ТЕКСТ
# =========================
def utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def local_now():
    return utc_now().astimezone(LOCAL_TZ)


def local_day_start(day=None):
    if day is None:
        day = local_now().date()

    local_start = datetime.datetime.combine(day, datetime.time.min, tzinfo=LOCAL_TZ)
    return local_start.astimezone(datetime.timezone.utc).replace(tzinfo=None)


def local_day_end(day=None):
    if day is None:
        day = local_now().date()

    local_end = datetime.datetime.combine(
        day + datetime.timedelta(days=1),
        datetime.time.min,
        tzinfo=LOCAL_TZ,
    )
    return local_end.astimezone(datetime.timezone.utc).replace(tzinfo=None)


def dt_to_local(dt):
    if not dt:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)

    return dt.astimezone(LOCAL_TZ)


def chunk_text(text, max_len=1000):
    if len(text) <= max_len:
        return [text]

    chunks = []
    current = ""

    for line in text.splitlines():
        if len(current) + len(line) + 1 > max_len:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line

    if current:
        chunks.append(current)

    return chunks


def parse_id_from_text(text):
    if not text:
        return None

    match = re.search(r"\d+", str(text))
    if not match:
        return None

    try:
        return int(match.group(0))
    except ValueError:
        return None


def get_role_from_text(guild, text):
    role_id = parse_id_from_text(text)
    if not role_id:
        return None

    return guild.get_role(role_id)


# =========================
# ГРУППЫ И РОЛИ В БАЗЕ
# =========================
def seed_default_role_groups(guild_id):
    # Стартовых групп в коде больше нет.
    # Все группы создаются через кнопки или команды.
    get_or_create_online_settings(guild_id)


def get_role_ids_from_group(group):
    try:
        data = json.loads(group.role_ids_json or "[]")
        cleaned = []
        for role_id in data:
            role_id = int(role_id)
            if role_id not in cleaned:
                cleaned.append(role_id)
        return cleaned
    except Exception:
        return []


def set_role_ids_for_group(group, role_ids):
    cleaned = []

    for role_id in role_ids:
        role_id = int(role_id)
        if role_id not in cleaned:
            cleaned.append(role_id)

    group.role_ids_json = json.dumps(cleaned)
    group.save()


def get_online_role_groups(guild_id):
    return list(
        OnlineRoleGroup
        .select()
        .where(OnlineRoleGroup.guild_id == guild_id)
        .order_by(OnlineRoleGroup.position, OnlineRoleGroup.id)
    )


def renumber_groups(guild_id):
    groups = get_online_role_groups(guild_id)

    for index, group in enumerate(groups, start=1):
        if group.position != index:
            group.position = index
            group.save()


def get_next_position(guild_id):
    row = (
        OnlineRoleGroup
        .select(fn.MAX(OnlineRoleGroup.position).alias("max_position"))
        .where(OnlineRoleGroup.guild_id == guild_id)
        .dicts()
        .first()
    )

    max_position = row.get("max_position") if row else None
    return int(max_position or 0) + 1


def move_group_to_position(guild_id, group, new_position):
    groups = [item for item in get_online_role_groups(guild_id) if item.id != group.id]

    if new_position < 1:
        new_position = 1

    if new_position > len(groups) + 1:
        new_position = len(groups) + 1

    groups.insert(new_position - 1, group)

    for index, item in enumerate(groups, start=1):
        item.position = index
        item.save()


def get_all_role_ids_from_groups(guild_id):
    role_ids = set()

    for group in get_online_role_groups(guild_id):
        role_ids.update(get_role_ids_from_group(group))

    return role_ids


def get_or_create_online_settings(guild_id):
    row = OnlineSettings.get_or_none(OnlineSettings.guild_id == guild_id)

    if row:
        return row

    return OnlineSettings.create(
        guild_id=guild_id,
        steam_required_role_id=None,
    )


def get_steam_required_role_id(guild_id):
    settings = get_or_create_online_settings(guild_id)
    return settings.steam_required_role_id


def set_steam_required_role_id(guild_id, role_id):
    settings = get_or_create_online_settings(guild_id)
    settings.steam_required_role_id = role_id
    settings.save()
    return settings


def clear_steam_required_role_id(guild_id):
    settings = get_or_create_online_settings(guild_id)
    settings.steam_required_role_id = None
    settings.save()
    return settings


# =========================
# ПРАВА
# =========================
def member_has_any_role(member, role_ids):
    member_role_ids = {role.id for role in member.roles}
    return bool(set(role_ids) & member_role_ids)


def can_manage_online(member):
    if not isinstance(member, discord.Member):
        return False

    return member_has_any_role(member, ONLINE_SETTER_ROLE_IDS)


async def deny_interaction(interaction):
    await interaction.response.send_message(
        "❌ У тебя нет прав использовать эту кнопку.",
        ephemeral=True,
    )


# =========================
# DISCORD ACTIVITY
# =========================
def is_playing_foxhole_discord(member):
    for activity in getattr(member, "activities", []):
        name = getattr(activity, "name", "")
        if name and name.lower() == "foxhole":
            return True

    return False


# =========================
# STEAM API
# =========================
def get_steam_api_key():
    return os.environ.get("STEAM_API_KEY")


def is_valid_steam_id64(steam_id):
    return bool(re.fullmatch(r"\d{17}", steam_id))


async def is_playing_foxhole_steam(guild_id, discord_user_id):
    steam_api_key = get_steam_api_key()

    if not steam_api_key:
        return False

    link = SteamLink.get_or_none(
        (SteamLink.guild_id == guild_id)
        & (SteamLink.discord_user_id == discord_user_id)
    )

    if not link:
        return False

    now = utc_now()
    cached = STEAM_CHECK_CACHE.get(link.steam_id)

    if cached:
        cached_time, cached_result = cached
        diff = (now - cached_time).total_seconds()

        if diff < STEAM_CACHE_SECONDS:
            return cached_result

    url = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"

    params = {
        "key": steam_api_key,
        "steamids": link.steam_id,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=10) as response:
                if response.status != 200:
                    logger.warning(
                        f"Steam API вернул статус {response.status} "
                        f"для SteamID {link.steam_id}"
                    )
                    STEAM_CHECK_CACHE[link.steam_id] = (now, False)
                    return False

                data = await response.json()

        players = data.get("response", {}).get("players", [])

        if not players:
            STEAM_CHECK_CACHE[link.steam_id] = (now, False)
            return False

        player = players[0]
        game_id = str(player.get("gameid", ""))

        result = game_id == str(FOXHOLE_APP_ID)
        STEAM_CHECK_CACHE[link.steam_id] = (now, result)

        return result

    except Exception:
        logger.error("Ошибка проверки Steam API:")
        logger.error(traceback.format_exc())
        STEAM_CHECK_CACHE[link.steam_id] = (now, False)
        return False


async def is_playing_foxhole(member):
    if is_playing_foxhole_discord(member):
        return True

    if await is_playing_foxhole_steam(member.guild.id, member.id):
        return True

    return False


# =========================
# ONLINE LOGIC
# =========================
async def get_online_members(guild):
    allowed_role_ids = get_all_role_ids_from_groups(guild.id)
    online_members = []

    if not allowed_role_ids:
        return online_members

    for member in guild.members:
        if member.bot:
            continue

        if not member_has_any_role(member, allowed_role_ids):
            continue

        if await is_playing_foxhole(member):
            online_members.append(member)

    return online_members


def get_member_role_priority_in_group(member, group):
    member_role_ids = {role.id for role in member.roles}

    for index, role_id in enumerate(get_role_ids_from_group(group), start=1):
        if role_id in member_role_ids:
            return index

    return 999999


def sort_members_inside_group(members, group):
    return sorted(
        members,
        key=lambda member: (
            get_member_role_priority_in_group(member, group),
            member.display_name.lower(),
        ),
    )


def get_mentions(members, group=None):
    if not members:
        return "—"

    if group is not None:
        members = sort_members_inside_group(members, group)
    else:
        members = sorted(members, key=lambda member: member.display_name.lower())

    shown_members = members[:25]
    text = "\n".join(member.mention for member in shown_members)

    if len(members) > 25:
        text += f"\n...и ещё {len(members) - 25}"

    return text


def get_members_by_roles(online_members, role_ids):
    return [
        member for member in online_members
        if member_has_any_role(member, role_ids)
    ]


async def build_online_embed(guild):
    seed_default_role_groups(guild.id)
    renumber_groups(guild.id)

    groups = get_online_role_groups(guild.id)
    online_members = await get_online_members(guild)

    embed = discord.Embed(
        title="🟢 Штабной онлайн Foxhole",
        color=discord.Color.green(),
        description=(
            f"👥 **Всего в игре:** `{len(online_members)}`\n"
            f"🕒 **Последнее обновление:** <t:{int(utc_now().timestamp())}:R>\n"
            f"📡 **Источник:** Discord Activity или Steam API"
        ),
    )

    used_member_ids = set()

    for group in groups:
        role_ids = get_role_ids_from_group(group)
        members = []

        for member in get_members_by_roles(online_members, role_ids):
            if member.id in used_member_ids:
                continue

            members.append(member)

        if not members:
            continue

        for member in members:
            used_member_ids.add(member.id)

        # Нумерация категорий в основном чате онлайна убрана.
        # Порядок групп всё равно сохраняется в базе через group.position.
        embed.add_field(
            name=f"{group.title} — {len(members)}",
            value=get_mentions(members, group),
            inline=False,
        )

    if not online_members:
        if not groups:
            text = "Группы ещё не настроены. Нажми **⚙️ Настройка** и добавь первую группу."
        else:
            text = "Сейчас никто из отслеживаемых ролей не найден в Foxhole."

        embed.add_field(
            name="━━━━━━━━━━━━━━",
            value=text,
            inline=False,
        )

    embed.set_footer(
        text="Обновляется каждые 30 секунд | Управление доступно ONLINE_SETTER_ROLE_IDS"
    )
    embed.timestamp = utc_now()

    return embed, online_members


# =========================
# СТАТИСТИКА
# =========================
def save_online_sample(guild_id, online_count):
    now = utc_now().replace(tzinfo=None)
    today = local_now().date()

    OnlineStatsSample.create(
        guild_id=guild_id,
        created_at=now,
        online_count=online_count,
    )

    peak_row = OnlinePeak.get_or_none(
        (OnlinePeak.guild_id == guild_id)
        & (OnlinePeak.day == today)
    )

    if not peak_row:
        OnlinePeak.create(
            guild_id=guild_id,
            day=today,
            peak_count=online_count,
            peak_at=now,
        )
    elif online_count > peak_row.peak_count:
        peak_row.peak_count = online_count
        peak_row.peak_at = now
        peak_row.save()

    cutoff = now - datetime.timedelta(days=30)
    OnlineStatsSample.delete().where(
        (OnlineStatsSample.guild_id == guild_id)
        & (OnlineStatsSample.created_at < cutoff)
    ).execute()


def get_average_online(guild_id, start_dt, end_dt):
    result = (
        OnlineStatsSample
        .select(fn.AVG(OnlineStatsSample.online_count).alias("avg_online"))
        .where(
            (OnlineStatsSample.guild_id == guild_id)
            & (OnlineStatsSample.created_at >= start_dt)
            & (OnlineStatsSample.created_at < end_dt)
        )
        .dicts()
        .first()
    )

    if not result or result["avg_online"] is None:
        return 0

    return round(float(result["avg_online"]), 1)


def get_peak_online(guild_id, start_dt, end_dt):
    row = (
        OnlineStatsSample
        .select()
        .where(
            (OnlineStatsSample.guild_id == guild_id)
            & (OnlineStatsSample.created_at >= start_dt)
            & (OnlineStatsSample.created_at < end_dt)
        )
        .order_by(OnlineStatsSample.online_count.desc(), OnlineStatsSample.created_at.asc())
        .first()
    )

    if not row:
        return 0, None

    return row.online_count, row.created_at


def get_best_activity_time(guild_id, start_dt, end_dt):
    samples = list(
        OnlineStatsSample
        .select()
        .where(
            (OnlineStatsSample.guild_id == guild_id)
            & (OnlineStatsSample.created_at >= start_dt)
            & (OnlineStatsSample.created_at < end_dt)
        )
    )

    if not samples:
        return "—"

    buckets = {}

    for sample in samples:
        local_dt = dt_to_local(sample.created_at)
        hour = local_dt.hour
        buckets.setdefault(hour, []).append(sample.online_count)

    best_hour = None
    best_avg = -1

    for hour, values in buckets.items():
        avg_value = sum(values) / len(values)
        if avg_value > best_avg:
            best_avg = avg_value
            best_hour = hour

    if best_hour is None:
        return "—"

    next_hour = (best_hour + 1) % 24
    return f"{best_hour:02d}:00–{next_hour:02d}:00"


def build_stats_embed(guild_id):
    today = local_now().date()
    today_start = local_day_start(today)
    today_end = local_day_end(today)

    week_start = local_day_start(today - datetime.timedelta(days=6))
    week_end = today_end

    today_avg = get_average_online(guild_id, today_start, today_end)
    today_peak, today_peak_at = get_peak_online(guild_id, today_start, today_end)
    week_peak, week_peak_at = get_peak_online(guild_id, week_start, week_end)
    best_time = get_best_activity_time(guild_id, week_start, week_end)

    today_peak_time = "—"
    if today_peak_at:
        today_peak_time = dt_to_local(today_peak_at).strftime("%H:%M")

    week_peak_time = "—"
    if week_peak_at:
        week_peak_time = dt_to_local(week_peak_at).strftime("%d.%m %H:%M")

    embed = discord.Embed(
        title="📊 Статистика онлайна",
        color=discord.Color.blurple(),
        description=(
            "**Сегодня:**\n"
            f"Средний онлайн: `{today_avg}`\n"
            f"Пиковый онлайн: `{today_peak}`\n"
            f"Время пика: `{today_peak_time}`\n\n"
            "**За 7 дней:**\n"
            f"Максимальный пик: `{week_peak}`\n"
            f"Время максимального пика: `{week_peak_time}`\n"
            f"Лучшее время активности: `{best_time}`"
        ),
    )
    embed.set_footer(text="Статистика считается по сэмплам online_loop каждые 30 секунд")
    embed.timestamp = utc_now()
    return embed


async def get_members_without_steam(guild):
    seed_default_role_groups(guild.id)
    missing = []

    steam_required_role_id = get_steam_required_role_id(guild.id)

    if steam_required_role_id:
        allowed_role_ids = {steam_required_role_id}
    else:
        allowed_role_ids = get_all_role_ids_from_groups(guild.id)

    linked_ids = {
        row.discord_user_id
        for row in SteamLink.select().where(SteamLink.guild_id == guild.id)
    }

    for member in guild.members:
        if member.bot:
            continue

        if not member_has_any_role(member, allowed_role_ids):
            continue

        if member.id not in linked_ids:
            missing.append(member)

    return sorted(missing, key=lambda member: member.display_name.lower())


async def build_missing_steam_embed(guild):
    missing = await get_members_without_steam(guild)
    steam_required_role_id = get_steam_required_role_id(guild.id)

    if steam_required_role_id:
        check_description = f"Проверяются только люди с ролью <@&{steam_required_role_id}>."
    else:
        check_description = "Проверяются люди с ролями из онлайн-групп, потому что отдельная Steam-роль не задана."

    embed = discord.Embed(
        title="🔗 Не привязан Steam",
        color=discord.Color.orange(),
        description=(
            f"Найдено людей без привязанного SteamID64: `{len(missing)}`\n\n"
            f"{check_description}"
        ),
    )

    if not missing:
        embed.add_field(
            name="✅ Всё хорошо",
            value="У всех отслеживаемых людей SteamID уже привязан.",
            inline=False,
        )
    else:
        lines = [member.mention for member in missing]
        chunks = chunk_text("\n".join(lines), max_len=1000)

        for index, chunk in enumerate(chunks, start=1):
            embed.add_field(
                name=f"Список {index}",
                value=chunk,
                inline=False,
            )

    embed.set_footer(text="Привязать SteamID можно командой /steam")
    embed.timestamp = utc_now()
    return embed


def build_hierarchy_embed(guild_id):
    seed_default_role_groups(guild_id)
    renumber_groups(guild_id)
    groups = get_online_role_groups(guild_id)

    embed = discord.Embed(
        title="⚙️ Иерархия групп онлайна",
        color=discord.Color.dark_gold(),
        description=(
            "Разделов больше нет. Есть единый список групп сверху вниз.\n"
            "Порядок ролей внутри группы тоже важен: первая роль считается выше следующей."
        ),
    )

    if not groups:
        embed.add_field(
            name="Группы не настроены",
            value="Добавь первую группу через кнопку **⚙️ Настройка** или команду `/online_group_add`.",
            inline=False,
        )
    else:
        lines = []
        for group in groups:
            role_ids = get_role_ids_from_group(group)

            if role_ids:
                role_lines = []
                for index, role_id in enumerate(role_ids, start=1):
                    role_lines.append(f"`{index}.` <@&{role_id}>")
                roles_text = "\n".join(role_lines)
            else:
                roles_text = "роль не указана"

            lines.append(
                f"`ID {group.id}` | `Группа #{group.position}` | {group.title}\n"
                f"{roles_text}"
            )

        for index, chunk in enumerate(chunk_text("\n\n".join(lines), max_len=1000), start=1):
            embed.add_field(
                name=f"Группы | часть {index}",
                value=chunk,
                inline=False,
            )

    embed.set_footer(text="Настройка: кнопка ⚙️ Настройка или команды online_group")
    embed.timestamp = utc_now()
    return embed


def build_settings_help_embed(guild_id):
    embed = build_hierarchy_embed(guild_id)
    embed.title = "⚙️ Настройка онлайн-групп"
    embed.description = (
        "Разделов больше нет. Есть только группы и роли внутри групп.\n\n"
        "**Добавить новую группу:**\n"
        "`/online_group_add section:group название:🏅 Ветераны роль:@Роль`\n\n"
        "**Добавить роль в существующую группу:**\n"
        "`/online_group_role_add group_id:12 роль:@Роль`\n\n"
        "**Убрать роль из группы:**\n"
        "`/online_group_role_remove group_id:12 роль:@Роль`\n\n"
        "**Поменять порядок группы:**\n"
        "`/online_group_move group_id:12 position:1`\n\n"
        "**Поменять порядок роли внутри группы:**\n"
        "`/online_group_role_move group_id:12 роль:@Роль position:1`\n\n"
        "**Переименовать группу:**\n"
        "`/online_group_rename group_id:12 название:🔥 Проверенные бойцы`\n\n"
        "**Удалить группу:**\n"
        "`/online_group_delete group_id:12`\n\n"
        "**Задать отдельную роль для проверки Steam:**\n"
        "`/steam_missing_role_set роль:@Участник_клана`\n\n"
        "**Убрать отдельную роль для проверки Steam:**\n"
        "`/steam_missing_role_clear`"
    )
    return embed


# =========================
# МОДАЛЬНЫЕ ОКНА НАСТРОЙКИ
# =========================
class AddGroupModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Добавить группу")
        self.add_item(discord.ui.InputText(label="Название группы", placeholder="Например: 🏅 Ветераны"))
        self.add_item(discord.ui.InputText(label="ID/упоминание роли", placeholder="@Роль или 123456789012345678", required=False))

    async def callback(self, interaction):
        if not can_manage_online(interaction.user):
            return await deny_interaction(interaction)

        title = self.children[0].value.strip()
        role_text = self.children[1].value.strip()

        if not title:
            return await interaction.response.send_message("❌ Укажи название группы.", ephemeral=True)

        role_ids = []
        if role_text:
            role = get_role_from_text(interaction.guild, role_text)
            if not role:
                return await interaction.response.send_message("❌ Не смог найти роль по ID/упоминанию.", ephemeral=True)
            role_ids.append(role.id)

        group = OnlineRoleGroup.create(
            guild_id=interaction.guild.id,
            section=DEFAULT_GROUP_SECTION,
            title=title,
            role_ids_json=json.dumps(role_ids),
            position=get_next_position(interaction.guild.id),
        )
        renumber_groups(interaction.guild.id)
        await update_online_for_guild(interaction.guild, save_stats=False)

        await interaction.response.send_message(
            f"✅ Группа создана.\nID группы: `{group.id}`\nНазвание: {group.title}",
            ephemeral=True,
        )


class GroupRoleModal(discord.ui.Modal):
    def __init__(self, mode):
        self.mode = mode
        title = {
            "add": "Добавить роль в группу",
            "remove": "Убрать роль из группы",
            "move": "Порядок роли в группе",
        }.get(mode, "Роль в группе")
        super().__init__(title=title)

        self.add_item(discord.ui.InputText(label="ID группы", placeholder="Например: 1"))
        self.add_item(discord.ui.InputText(label="ID/упоминание роли", placeholder="@Роль или 123456789012345678"))

        if mode == "move":
            self.add_item(discord.ui.InputText(label="Новая позиция роли", placeholder="Например: 1"))

    async def callback(self, interaction):
        if not can_manage_online(interaction.user):
            return await deny_interaction(interaction)

        group_id = parse_id_from_text(self.children[0].value)
        role = get_role_from_text(interaction.guild, self.children[1].value)

        if not group_id or not role:
            return await interaction.response.send_message(
                "❌ Укажи корректный ID группы и ID/упоминание роли.",
                ephemeral=True,
            )

        group = OnlineRoleGroup.get_or_none(
            (OnlineRoleGroup.guild_id == interaction.guild.id)
            & (OnlineRoleGroup.id == group_id)
        )

        if not group:
            return await interaction.response.send_message("❌ Группа с таким ID не найдена.", ephemeral=True)

        role_ids = get_role_ids_from_group(group)

        if self.mode == "add":
            if role.id in role_ids:
                return await interaction.response.send_message(
                    f"ℹ️ Роль {role.mention} уже есть в группе {group.title}.",
                    ephemeral=True,
                )
            role_ids.append(role.id)
            set_role_ids_for_group(group, role_ids)
            text = f"✅ Роль {role.mention} добавлена в группу {group.title}."

        elif self.mode == "remove":
            if role.id not in role_ids:
                return await interaction.response.send_message(
                    f"ℹ️ Роли {role.mention} нет в группе {group.title}.",
                    ephemeral=True,
                )
            role_ids.remove(role.id)
            set_role_ids_for_group(group, role_ids)
            text = f"✅ Роль {role.mention} удалена из группы {group.title}."

        else:
            if role.id not in role_ids:
                return await interaction.response.send_message(
                    f"ℹ️ Роли {role.mention} нет в группе {group.title}.",
                    ephemeral=True,
                )

            position = parse_id_from_text(self.children[2].value)
            if not position:
                return await interaction.response.send_message("❌ Укажи корректную позицию роли.", ephemeral=True)

            role_ids.remove(role.id)

            if position < 1:
                position = 1
            if position > len(role_ids) + 1:
                position = len(role_ids) + 1

            role_ids.insert(position - 1, role.id)
            set_role_ids_for_group(group, role_ids)
            text = f"✅ Роль {role.mention} перемещена в группе {group.title} на позицию `{position}`."

        await update_online_for_guild(interaction.guild, save_stats=False)
        await interaction.response.send_message(text, ephemeral=True)


class RenameGroupModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Переименовать группу")
        self.add_item(discord.ui.InputText(label="ID группы", placeholder="Например: 1"))
        self.add_item(discord.ui.InputText(label="Новое название", placeholder="Например: 🔥 Проверенные бойцы"))

    async def callback(self, interaction):
        if not can_manage_online(interaction.user):
            return await deny_interaction(interaction)

        group_id = parse_id_from_text(self.children[0].value)
        title = self.children[1].value.strip()

        if not group_id or not title:
            return await interaction.response.send_message("❌ Укажи ID группы и новое название.", ephemeral=True)

        group = OnlineRoleGroup.get_or_none(
            (OnlineRoleGroup.guild_id == interaction.guild.id)
            & (OnlineRoleGroup.id == group_id)
        )

        if not group:
            return await interaction.response.send_message("❌ Группа с таким ID не найдена.", ephemeral=True)

        old_title = group.title
        group.title = title
        group.save()

        await update_online_for_guild(interaction.guild, save_stats=False)
        await interaction.response.send_message(
            f"✅ Группа переименована: {old_title} → {group.title}",
            ephemeral=True,
        )


class MoveGroupModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Поменять порядок группы")
        self.add_item(discord.ui.InputText(label="ID группы", placeholder="Например: 1"))
        self.add_item(discord.ui.InputText(label="Новая позиция", placeholder="Например: 1"))

    async def callback(self, interaction):
        if not can_manage_online(interaction.user):
            return await deny_interaction(interaction)

        group_id = parse_id_from_text(self.children[0].value)
        position = parse_id_from_text(self.children[1].value)

        if not group_id or not position:
            return await interaction.response.send_message("❌ Укажи ID группы и новую позицию.", ephemeral=True)

        group = OnlineRoleGroup.get_or_none(
            (OnlineRoleGroup.guild_id == interaction.guild.id)
            & (OnlineRoleGroup.id == group_id)
        )

        if not group:
            return await interaction.response.send_message("❌ Группа с таким ID не найдена.", ephemeral=True)

        move_group_to_position(interaction.guild.id, group, position)
        await update_online_for_guild(interaction.guild, save_stats=False)

        await interaction.response.send_message(
            f"✅ Группа {group.title} перемещена на позицию `{position}`.",
            ephemeral=True,
        )


class DeleteGroupModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Удалить группу")
        self.add_item(discord.ui.InputText(label="ID группы", placeholder="Например: 1"))

    async def callback(self, interaction):
        if not can_manage_online(interaction.user):
            return await deny_interaction(interaction)

        group_id = parse_id_from_text(self.children[0].value)

        if not group_id:
            return await interaction.response.send_message("❌ Укажи ID группы.", ephemeral=True)

        group = OnlineRoleGroup.get_or_none(
            (OnlineRoleGroup.guild_id == interaction.guild.id)
            & (OnlineRoleGroup.id == group_id)
        )

        if not group:
            return await interaction.response.send_message("❌ Группа с таким ID не найдена.", ephemeral=True)

        title = group.title
        group.delete_instance()
        renumber_groups(interaction.guild.id)
        await update_online_for_guild(interaction.guild, save_stats=False)

        await interaction.response.send_message(f"✅ Группа удалена: {title}", ephemeral=True)


class SteamRequiredRoleModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Роль для проверки Steam")
        self.add_item(discord.ui.InputText(label="ID/упоминание роли", placeholder="@Роль или 123456789012345678"))

    async def callback(self, interaction):
        if not can_manage_online(interaction.user):
            return await deny_interaction(interaction)

        role = get_role_from_text(interaction.guild, self.children[0].value)

        if not role:
            return await interaction.response.send_message("❌ Не смог найти роль по ID/упоминанию.", ephemeral=True)

        set_steam_required_role_id(interaction.guild.id, role.id)
        await interaction.response.send_message(
            f"✅ Роль для проверки Steam-привязки установлена: {role.mention}",
            ephemeral=True,
        )


class SettingsPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="➕ Группа", style=discord.ButtonStyle.green)
    async def add_group_button(self, button, interaction):
        if not can_manage_online(interaction.user):
            return await deny_interaction(interaction)
        await interaction.response.send_modal(AddGroupModal())

    @discord.ui.button(label="➕ Роль", style=discord.ButtonStyle.green)
    async def add_role_button(self, button, interaction):
        if not can_manage_online(interaction.user):
            return await deny_interaction(interaction)
        await interaction.response.send_modal(GroupRoleModal("add"))

    @discord.ui.button(label="➖ Роль", style=discord.ButtonStyle.gray)
    async def remove_role_button(self, button, interaction):
        if not can_manage_online(interaction.user):
            return await deny_interaction(interaction)
        await interaction.response.send_modal(GroupRoleModal("remove"))

    @discord.ui.button(label="↕️ Роль", style=discord.ButtonStyle.blurple)
    async def move_role_button(self, button, interaction):
        if not can_manage_online(interaction.user):
            return await deny_interaction(interaction)
        await interaction.response.send_modal(GroupRoleModal("move"))

    @discord.ui.button(label="✏️ Название", style=discord.ButtonStyle.blurple)
    async def rename_group_button(self, button, interaction):
        if not can_manage_online(interaction.user):
            return await deny_interaction(interaction)
        await interaction.response.send_modal(RenameGroupModal())

    @discord.ui.button(label="↕️ Группа", style=discord.ButtonStyle.blurple)
    async def move_group_button(self, button, interaction):
        if not can_manage_online(interaction.user):
            return await deny_interaction(interaction)
        await interaction.response.send_modal(MoveGroupModal())

    @discord.ui.button(label="🗑️ Группа", style=discord.ButtonStyle.red)
    async def delete_group_button(self, button, interaction):
        if not can_manage_online(interaction.user):
            return await deny_interaction(interaction)
        await interaction.response.send_modal(DeleteGroupModal())

    @discord.ui.button(label="🔗 Роль Steam", style=discord.ButtonStyle.gray)
    async def steam_role_button(self, button, interaction):
        if not can_manage_online(interaction.user):
            return await deny_interaction(interaction)
        await interaction.response.send_modal(SteamRequiredRoleModal())

    @discord.ui.button(label="🧹 Убрать роль Steam", style=discord.ButtonStyle.gray)
    async def clear_steam_role_button(self, button, interaction):
        if not can_manage_online(interaction.user):
            return await deny_interaction(interaction)

        clear_steam_required_role_id(interaction.guild.id)
        await interaction.response.send_message(
            "✅ Отдельная роль для проверки Steam-привязки убрана.",
            ephemeral=True,
        )

    @discord.ui.button(label="📋 Список", style=discord.ButtonStyle.gray)
    async def list_button(self, button, interaction):
        if not can_manage_online(interaction.user):
            return await deny_interaction(interaction)

        embed = build_hierarchy_embed(interaction.guild.id)
        await interaction.response.send_message(embed=embed, ephemeral=True)


# =========================
# КНОПКИ ПАНЕЛИ ОНЛАЙНА
# =========================
class OnlineControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="📊 Статистика онлайна",
        style=discord.ButtonStyle.blurple,
        custom_id="online_panel:online_stats",
    )
    async def online_stats_button(self, button, interaction):
        if not can_manage_online(interaction.user):
            return await deny_interaction(interaction)

        embed = build_stats_embed(interaction.guild.id)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(
        label="🔗 Не привязан Steam",
        style=discord.ButtonStyle.gray,
        custom_id="online_panel:missing_steam",
    )
    async def missing_steam_button(self, button, interaction):
        if not can_manage_online(interaction.user):
            return await deny_interaction(interaction)

        embed = await build_missing_steam_embed(interaction.guild)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(
        label="⚙️ Настройка",
        style=discord.ButtonStyle.gray,
        custom_id="online_panel:settings",
    )
    async def settings_button(self, button, interaction):
        if not can_manage_online(interaction.user):
            return await deny_interaction(interaction)

        embed = build_settings_help_embed(interaction.guild.id)
        await interaction.response.send_message(embed=embed, view=SettingsPanelView(), ephemeral=True)

    @discord.ui.button(
        label="🔄 Обновить",
        style=discord.ButtonStyle.green,
        custom_id="online_panel:refresh",
    )
    async def refresh_button(self, button, interaction):
        if not can_manage_online(interaction.user):
            return await deny_interaction(interaction)

        await interaction.response.defer(ephemeral=True)
        await update_online_for_guild(interaction.guild, save_stats=False)
        await interaction.followup.send("✅ Онлайн обновлён.", ephemeral=True)


# =========================
# ОБНОВЛЕНИЕ СООБЩЕНИЯ
# =========================
async def update_online_for_guild(guild, save_stats=True):
    seed_default_role_groups(guild.id)

    row = OnlineChannel.get_or_none(OnlineChannel.guild_id == guild.id)

    if not row:
        return

    channel = guild.get_channel_or_thread(row.channel_id)

    if not channel:
        logger.warning(f"Канал/ветка не найдены: {row.channel_id}")
        return

    embed, online_members = await build_online_embed(guild)

    if save_stats:
        save_online_sample(guild.id, len(online_members))

    msg_row = OnlineMessage.get_or_none(OnlineMessage.guild_id == guild.id)
    view = OnlineControlView()

    if msg_row:
        try:
            msg = await channel.fetch_message(msg_row.message_id)
            await msg.edit(embed=embed, view=view)
            return

        except discord.NotFound:
            logger.warning("Старое сообщение онлайна не найдено. Создаю новое.")
            OnlineMessage.delete().where(OnlineMessage.guild_id == guild.id).execute()

        except Exception:
            logger.error(traceback.format_exc())

    try:
        msg = await channel.send(embed=embed, view=view)

        OnlineMessage.delete().where(OnlineMessage.guild_id == guild.id).execute()

        OnlineMessage.create(
            guild_id=guild.id,
            channel_id=channel.id,
            message_id=msg.id,
        )

    except Exception:
        logger.error(traceback.format_exc())


# =========================
# LOOP
# =========================
@tasks.loop(seconds=30)
async def online_loop():
    for guild in bot.guilds:
        try:
            seed_default_role_groups(guild.id)
            await update_online_for_guild(guild, save_stats=True)
        except Exception:
            logger.error(traceback.format_exc())


# =========================
# COMMAND /steam
# =========================
@bot.slash_command(name="steam", guild_ids=[GUILD_ID])
async def steam(ctx, пользователь: discord.Member, steam_id: str):
    if not can_manage_online(ctx.author):
        return await ctx.respond("❌ У тебя нет прав привязывать Steam аккаунты.", ephemeral=True)

    steam_id = steam_id.strip()

    if not is_valid_steam_id64(steam_id):
        return await ctx.respond(
            "❌ Неверный SteamID64.\nПример: `76561198000000000`",
            ephemeral=True,
        )

    row = SteamLink.get_or_none(
        (SteamLink.guild_id == ctx.guild.id)
        & (SteamLink.discord_user_id == пользователь.id)
    )

    if row:
        old_steam_id = row.steam_id
        row.steam_id = steam_id
        row.save()
        STEAM_CHECK_CACHE.pop(old_steam_id, None)
    else:
        SteamLink.create(
            guild_id=ctx.guild.id,
            discord_user_id=пользователь.id,
            steam_id=steam_id,
        )

    STEAM_CHECK_CACHE.pop(steam_id, None)

    await ctx.respond(
        f"✅ SteamID привязан.\n"
        f"👤 Пользователь: {пользователь.mention}\n"
        f"🎮 SteamID64: `{steam_id}`",
        ephemeral=True,
    )


@bot.slash_command(name="steam_remove", guild_ids=[GUILD_ID])
async def steam_remove(ctx, пользователь: discord.Member):
    if not can_manage_online(ctx.author):
        return await ctx.respond("❌ У тебя нет прав отвязывать Steam аккаунты.", ephemeral=True)

    row = SteamLink.get_or_none(
        (SteamLink.guild_id == ctx.guild.id)
        & (SteamLink.discord_user_id == пользователь.id)
    )

    if not row:
        return await ctx.respond(f"ℹ️ У {пользователь.mention} SteamID не был привязан.", ephemeral=True)

    old_steam_id = row.steam_id
    row.delete_instance()
    STEAM_CHECK_CACHE.pop(old_steam_id, None)

    await ctx.respond(f"✅ SteamID отвязан у {пользователь.mention}.", ephemeral=True)


@bot.slash_command(name="steam_list", guild_ids=[GUILD_ID])
async def steam_list(ctx):
    if not can_manage_online(ctx.author):
        return await ctx.respond("❌ У тебя нет прав смотреть список Steam привязок.", ephemeral=True)

    rows = list(SteamLink.select().where(SteamLink.guild_id == ctx.guild.id))

    if not rows:
        return await ctx.respond("ℹ️ Пока нет ни одной Steam-привязки.", ephemeral=True)

    lines = []
    for row in rows:
        member = ctx.guild.get_member(row.discord_user_id)
        name = member.mention if member else f"ID {row.discord_user_id}"
        lines.append(f"{name} — `{row.steam_id}`")

    embed = discord.Embed(
        title="🔗 Steam-привязки",
        color=discord.Color.blue(),
        description=f"Всего привязок: `{len(rows)}`",
    )

    for index, chunk in enumerate(chunk_text("\n".join(lines), max_len=1000), start=1):
        embed.add_field(name=f"Список {index}", value=chunk, inline=False)

    await ctx.respond(embed=embed, ephemeral=True)


@bot.slash_command(name="steam_missing", guild_ids=[GUILD_ID])
async def steam_missing(ctx):
    if not can_manage_online(ctx.author):
        return await ctx.respond("❌ У тебя нет прав смотреть список без Steam-привязки.", ephemeral=True)

    embed = await build_missing_steam_embed(ctx.guild)
    await ctx.respond(embed=embed, ephemeral=True)


@bot.slash_command(name="steam_missing_role_set", guild_ids=[GUILD_ID])
async def steam_missing_role_set(ctx, роль: discord.Role):
    if not can_manage_online(ctx.author):
        return await ctx.respond("❌ У тебя нет прав настраивать роль проверки Steam.", ephemeral=True)

    set_steam_required_role_id(ctx.guild.id, роль.id)

    await ctx.respond(
        f"✅ Роль для проверки Steam-привязки установлена: {роль.mention}\n"
        f"Теперь /steam_missing и кнопка `🔗 Не привязан Steam` будут показывать только людей с этой ролью без SteamID.",
        ephemeral=True,
    )


@bot.slash_command(name="steam_missing_role_clear", guild_ids=[GUILD_ID])
async def steam_missing_role_clear(ctx):
    if not can_manage_online(ctx.author):
        return await ctx.respond("❌ У тебя нет прав настраивать роль проверки Steam.", ephemeral=True)

    clear_steam_required_role_id(ctx.guild.id)

    await ctx.respond(
        "✅ Отдельная роль для проверки Steam-привязки убрана.\n"
        "Теперь /steam_missing и кнопка `🔗 Не привязан Steam` снова будут проверять все роли из онлайн-групп.",
        ephemeral=True,
    )


@bot.slash_command(name="online_stats", guild_ids=[GUILD_ID])
async def online_stats(ctx):
    if not can_manage_online(ctx.author):
        return await ctx.respond("❌ У тебя нет прав смотреть статистику онлайна.", ephemeral=True)

    embed = build_stats_embed(ctx.guild.id)
    await ctx.respond(embed=embed, ephemeral=True)


@bot.slash_command(name="online_hierarchy", guild_ids=[GUILD_ID])
async def online_hierarchy(ctx):
    if not can_manage_online(ctx.author):
        return await ctx.respond("❌ У тебя нет прав смотреть иерархию онлайна.", ephemeral=True)

    embed = build_hierarchy_embed(ctx.guild.id)
    await ctx.respond(embed=embed, ephemeral=True)


# =========================
# КОМАНДЫ НАСТРОЙКИ ГРУПП
# =========================
@bot.slash_command(name="online_group_add", guild_ids=[GUILD_ID])
async def online_group_add(
    ctx,
    section: str,
    название: str,
    роль: discord.Role = None,
):
    if not can_manage_online(ctx.author):
        return await ctx.respond("❌ У тебя нет прав настраивать онлайн-группы.", ephemeral=True)

    # Параметр section оставлен только для совместимости.
    # Разделов в отображении больше нет.
    seed_default_role_groups(ctx.guild.id)

    role_ids = [роль.id] if роль else []
    group = OnlineRoleGroup.create(
        guild_id=ctx.guild.id,
        section=DEFAULT_GROUP_SECTION,
        title=название.strip(),
        role_ids_json=json.dumps(role_ids),
        position=get_next_position(ctx.guild.id),
    )
    renumber_groups(ctx.guild.id)

    await ctx.respond(
        f"✅ Группа создана.\n"
        f"ID группы: `{group.id}`\n"
        f"Название: {group.title}",
        ephemeral=True,
    )

    await update_online_for_guild(ctx.guild, save_stats=False)


@bot.slash_command(name="online_group_delete", guild_ids=[GUILD_ID])
async def online_group_delete(ctx, group_id: int):
    if not can_manage_online(ctx.author):
        return await ctx.respond("❌ У тебя нет прав настраивать онлайн-группы.", ephemeral=True)

    group = OnlineRoleGroup.get_or_none(
        (OnlineRoleGroup.guild_id == ctx.guild.id)
        & (OnlineRoleGroup.id == group_id)
    )

    if not group:
        return await ctx.respond("❌ Группа с таким ID не найдена.", ephemeral=True)

    title = group.title
    group.delete_instance()
    renumber_groups(ctx.guild.id)

    await ctx.respond(f"✅ Группа удалена: {title}", ephemeral=True)
    await update_online_for_guild(ctx.guild, save_stats=False)


@bot.slash_command(name="online_group_rename", guild_ids=[GUILD_ID])
async def online_group_rename(ctx, group_id: int, название: str):
    if not can_manage_online(ctx.author):
        return await ctx.respond("❌ У тебя нет прав настраивать онлайн-группы.", ephemeral=True)

    group = OnlineRoleGroup.get_or_none(
        (OnlineRoleGroup.guild_id == ctx.guild.id)
        & (OnlineRoleGroup.id == group_id)
    )

    if not group:
        return await ctx.respond("❌ Группа с таким ID не найдена.", ephemeral=True)

    old_title = group.title
    group.title = название.strip()
    group.save()

    await ctx.respond(f"✅ Группа переименована: {old_title} → {group.title}", ephemeral=True)
    await update_online_for_guild(ctx.guild, save_stats=False)


@bot.slash_command(name="online_group_move", guild_ids=[GUILD_ID])
async def online_group_move(ctx, group_id: int, position: int):
    if not can_manage_online(ctx.author):
        return await ctx.respond("❌ У тебя нет прав настраивать онлайн-группы.", ephemeral=True)

    if position < 1:
        return await ctx.respond("❌ Позиция должна быть 1 или больше.", ephemeral=True)

    group = OnlineRoleGroup.get_or_none(
        (OnlineRoleGroup.guild_id == ctx.guild.id)
        & (OnlineRoleGroup.id == group_id)
    )

    if not group:
        return await ctx.respond("❌ Группа с таким ID не найдена.", ephemeral=True)

    move_group_to_position(ctx.guild.id, group, position)

    await ctx.respond(
        f"✅ Позиция изменена.\nГруппа: {group.title}\nНовая позиция: `{position}`",
        ephemeral=True,
    )
    await update_online_for_guild(ctx.guild, save_stats=False)


@bot.slash_command(name="online_group_role_add", guild_ids=[GUILD_ID])
async def online_group_role_add(ctx, group_id: int, роль: discord.Role):
    if not can_manage_online(ctx.author):
        return await ctx.respond("❌ У тебя нет прав настраивать онлайн-группы.", ephemeral=True)

    group = OnlineRoleGroup.get_or_none(
        (OnlineRoleGroup.guild_id == ctx.guild.id)
        & (OnlineRoleGroup.id == group_id)
    )

    if not group:
        return await ctx.respond("❌ Группа с таким ID не найдена.", ephemeral=True)

    role_ids = get_role_ids_from_group(group)

    if роль.id in role_ids:
        return await ctx.respond(f"ℹ️ Роль {роль.mention} уже есть в группе {group.title}.", ephemeral=True)

    role_ids.append(роль.id)
    set_role_ids_for_group(group, role_ids)

    await ctx.respond(f"✅ Роль {роль.mention} добавлена в группу {group.title}.", ephemeral=True)
    await update_online_for_guild(ctx.guild, save_stats=False)


@bot.slash_command(name="online_group_role_remove", guild_ids=[GUILD_ID])
async def online_group_role_remove(ctx, group_id: int, роль: discord.Role):
    if not can_manage_online(ctx.author):
        return await ctx.respond("❌ У тебя нет прав настраивать онлайн-группы.", ephemeral=True)

    group = OnlineRoleGroup.get_or_none(
        (OnlineRoleGroup.guild_id == ctx.guild.id)
        & (OnlineRoleGroup.id == group_id)
    )

    if not group:
        return await ctx.respond("❌ Группа с таким ID не найдена.", ephemeral=True)

    role_ids = get_role_ids_from_group(group)

    if роль.id not in role_ids:
        return await ctx.respond(f"ℹ️ Роли {роль.mention} нет в группе {group.title}.", ephemeral=True)

    role_ids.remove(роль.id)
    set_role_ids_for_group(group, role_ids)

    await ctx.respond(f"✅ Роль {роль.mention} удалена из группы {group.title}.", ephemeral=True)
    await update_online_for_guild(ctx.guild, save_stats=False)


@bot.slash_command(name="online_group_role_move", guild_ids=[GUILD_ID])
async def online_group_role_move(ctx, group_id: int, роль: discord.Role, position: int):
    if not can_manage_online(ctx.author):
        return await ctx.respond("❌ У тебя нет прав настраивать онлайн-группы.", ephemeral=True)

    if position < 1:
        return await ctx.respond("❌ Позиция должна быть 1 или больше.", ephemeral=True)

    group = OnlineRoleGroup.get_or_none(
        (OnlineRoleGroup.guild_id == ctx.guild.id)
        & (OnlineRoleGroup.id == group_id)
    )

    if not group:
        return await ctx.respond("❌ Группа с таким ID не найдена.", ephemeral=True)

    role_ids = get_role_ids_from_group(group)

    if роль.id not in role_ids:
        return await ctx.respond(f"ℹ️ Роли {роль.mention} нет в группе {group.title}.", ephemeral=True)

    role_ids.remove(роль.id)

    if position > len(role_ids) + 1:
        position = len(role_ids) + 1

    role_ids.insert(position - 1, роль.id)
    set_role_ids_for_group(group, role_ids)

    await ctx.respond(
        f"✅ Роль {роль.mention} перемещена в группе {group.title} на позицию `{position}`.",
        ephemeral=True,
    )
    await update_online_for_guild(ctx.guild, save_stats=False)


@bot.slash_command(name="online_group_section", guild_ids=[GUILD_ID])
async def online_group_section(ctx, group_id: int, section: str):
    if not can_manage_online(ctx.author):
        return await ctx.respond("❌ У тебя нет прав настраивать онлайн-группы.", ephemeral=True)

    group = OnlineRoleGroup.get_or_none(
        (OnlineRoleGroup.guild_id == ctx.guild.id)
        & (OnlineRoleGroup.id == group_id)
    )

    if not group:
        return await ctx.respond("❌ Группа с таким ID не найдена.", ephemeral=True)

    group.section = DEFAULT_GROUP_SECTION
    group.save()

    await ctx.respond(
        "ℹ️ Разделы больше не используются. Группа оставлена в единой иерархии.",
        ephemeral=True,
    )
    await update_online_for_guild(ctx.guild, save_stats=False)


@bot.slash_command(name="online_group_list", guild_ids=[GUILD_ID])
async def online_group_list(ctx):
    if not can_manage_online(ctx.author):
        return await ctx.respond("❌ У тебя нет прав смотреть список онлайн-групп.", ephemeral=True)

    embed = build_hierarchy_embed(ctx.guild.id)
    await ctx.respond(embed=embed, ephemeral=True)


# =========================
# COMMAND /онлайн
# =========================
@bot.slash_command(name="онлайн", guild_ids=[GUILD_ID])
async def онлайн(ctx, канал: discord.TextChannel = None, айди_ветки: str = None):
    if not can_manage_online(ctx.author):
        return await ctx.respond("❌ У тебя нет прав выставлять чат онлайна.", ephemeral=True)

    seed_default_role_groups(ctx.guild.id)
    target_id = None

    if канал:
        target_id = канал.id
    elif айди_ветки:
        try:
            target_id = int(айди_ветки)
        except ValueError:
            return await ctx.respond("❌ Неверный ID ветки.", ephemeral=True)
    else:
        return await ctx.respond("❌ Укажи канал или айди_ветки.", ephemeral=True)

    row = OnlineChannel.get_or_none(OnlineChannel.guild_id == ctx.guild.id)

    if row:
        row.channel_id = target_id
        row.save()
    else:
        OnlineChannel.create(guild_id=ctx.guild.id, channel_id=target_id)

    await ctx.respond(f"✅ Канал онлайна установлен: `{target_id}`", ephemeral=True)
    await update_online_for_guild(ctx.guild, save_stats=False)


# =========================
# READY
# =========================
@bot.event
async def on_ready():
    logger.info(f"Бот онлайн: {bot.user}")

    try:
        for guild in bot.guilds:
            seed_default_role_groups(guild.id)
            renumber_groups(guild.id)
    except Exception:
        logger.error("Ошибка подготовки онлайн-групп:")
        logger.error(traceback.format_exc())

    try:
        bot.add_view(OnlineControlView())
    except Exception:
        logger.error("Ошибка регистрации persistent-кнопок:")
        logger.error(traceback.format_exc())

    try:
        if not online_loop.is_running():
            online_loop.start()
            logger.info("Цикл online_loop запущен")
    except Exception:
        logger.error("Ошибка запуска online_loop:")
        logger.error(traceback.format_exc())


# =========================
# RUN
# =========================
token = os.environ.get("DISCORD_BOT_TOKEN")

if not token:
    raise RuntimeError("Не найден DISCORD_BOT_TOKEN")

if not get_steam_api_key():
    logger.warning(
        "STEAM_API_KEY не найден. Steam API работать не будет, "
        "но Discord Activity продолжит работать."
    )

bot.run(token)
