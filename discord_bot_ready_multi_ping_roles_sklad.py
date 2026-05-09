# discord_bot_ready_multi_ping_roles_sklad.py
# Готовый один файл для Discord-бота Foxhole-клана.
#
# Исправлено/добавлено:
# 1) Добавлены проверки прав бота перед отправкой сообщений.
# 2) Исправлена работа с ветками: команда может быть вызвана в ветке,
#    если настроен ID самой ветки или ID родительского канала.
# 3) Ошибка Discord 403 Missing Access теперь возвращает понятное сообщение,
#    а не валит команду traceback-ом.
# 4) Все команды сохранены:
#    /таймер /мпф /склад /активность
#    /setsimpletimer /setmpf /setskladchannel /setaktivchat
# 5) В /активность добавлена возможность пинговать несколько заранее разрешённых ролей.
# 6) В /склад добавлена возможность пинговать заранее указанные роли
#    при уведомлениях за 3, 2 и 1 час до сгорания склада.
#
# ВАЖНО:
# 1) Токен бота НЕ храни в .txt и НЕ заливай на GitHub.
# 2) Добавь токен в переменную окружения DISCORD_BOT_TOKEN или в Secrets на хостинге.
# 3) Для slash-команд бот должен быть приглашён на сервер со scope:
#    bot applications.commands
# 4) Для работы в каналах/ветках боту нужны права:
#    View Channel, Send Messages, Send Messages in Threads,
#    Embed Links, Read Message History, Use Application Commands.
# 5) Для пинга ролей боту может понадобиться право Mention Everyone,
#    а сами роли должны быть доступны для упоминания в настройках Discord.

import os
import re
import datetime
import traceback
import logging
from logging.handlers import RotatingFileHandler

import discord
from discord.ext import tasks
from discord.ui import View, Button
from peewee import *


# =========================
# НАСТРОЙКИ
# =========================
GUILD_ID = 419565206335651840

# Роли, которым доступны команды настройки бота и удаление активностей/складов
ALLOWED_ROLE_IDS = [
    1493199914572972032,
    123456789012345678,
    987654321098765432,
    1477953756225081394,
    831242102179758100,
]

# Роли, которым можно создавать активности командой /активность
ACTIVITY_ROLE_IDS = [
    422500854910681089,
    1224787828815171595,
    1477953756225081394,
    831242102179758100,
    1384710674294378596,
    1267265658975031296,
    1074446816013197523,
    883270407887683615,
    1210544000600375297,
]

# Роли, которые можно пинговать при создании активности.
# Добавляй сюда ID ролей Discord-сервера, которые разрешено пинговать через /активность.
ACTIVITY_PING_ROLE_IDS = [
    1390688779056058429,
    1463921275775877184,
    1315281120400638034,
    1463921076433064227,
    1463925850654248991,
    1241512966235623505,
    1241663050776580189,
    1308787448137908325,
    1241513004928077885,
    1241513032304295947,
    1194748526131945502,
    556531210323492874,
]

# Роли, которые бот будет пинговать при уведомлениях склада за 3/2/1 час.
# Добавляй сюда ID ролей Discord-сервера, которые должны получать пинг склада.
SKLAD_PING_ROLE_IDS = [
    1390688779056058429,
    1463921275775877184,
    1315281120400638034,
    1463921076433064227,
    1463925850654248991,
    1241512966235623505,
    1241663050776580189,
    1308787448137908325,
    1241513004928077885,
    1241513032304295947,
    1194748526131945502,
    556531210323492874,
]

intents = discord.Intents.all()
bot = discord.Bot(intents=intents, debug_guilds=[GUILD_ID])
db = SqliteDatabase("TimerDataBase.db")


# =========================
# ЛОГИ
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        RotatingFileHandler(
            "bot.log",
            maxBytes=2 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        ),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


# =========================
# КЭШ КАНАЛОВ
# =========================
CHANNEL_CACHE = {
    "sklad": {},
    "sklad_notify": {},
    "simple": {},
    "mpf": {},
    "aktiv": {},
}


# =========================
# БАЗА ДАННЫХ
# =========================
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


class Activity(BaseModel):
    priority = TextField(null=True)
    guild_id = BigIntegerField()
    channel_id = BigIntegerField()
    message_id = BigIntegerField()
    author = BigIntegerField()
    title = TextField()
    location = TextField()
    need_people = TextField()
    voice_channel_id = BigIntegerField()
    created_at = BigIntegerField()


class SkladNotification(BaseModel):
    guild_id = BigIntegerField()
    timer_message_id = BigIntegerField()
    notification_channel_id = BigIntegerField()
    notification_message_id = BigIntegerField()
    warning_key = TextField()
    created_at = BigIntegerField()


db.connect(reuse_if_open=True)
db.create_tables([ChannelConfig, Timer, Activity, SkladNotification])


def ensure_db_columns():
    """
    create_tables не добавляет новые колонки в уже существующие таблицы.
    Поэтому новые поля добавляются безопасно при запуске.
    """
    with db.atomic():
        activity_columns = [col.name for col in db.get_columns(Activity._meta.table_name)]
        if "priority" not in activity_columns:
            db.execute_sql("ALTER TABLE activity ADD COLUMN priority TEXT")

        timer_columns = [col.name for col in db.get_columns(Timer._meta.table_name)]
        if "boxes" not in timer_columns:
            db.execute_sql("ALTER TABLE timer ADD COLUMN boxes INTEGER")
        if "taken_by" not in timer_columns:
            db.execute_sql("ALTER TABLE timer ADD COLUMN taken_by BIGINT")


ensure_db_columns()


# =========================
# КАНАЛЫ
# =========================
def load_channels():
    global CHANNEL_CACHE

    CHANNEL_CACHE = {
        "sklad": {},
        "sklad_notify": {},
        "simple": {},
        "mpf": {},
        "aktiv": {},
    }

    for row in ChannelConfig.select():
        CHANNEL_CACHE.setdefault(row.channel_type, {})
        CHANNEL_CACHE[row.channel_type][row.guild_id] = row.channel_id


def set_channel(guild_id, channel_id, channel_type):
    row = ChannelConfig.get_or_none(
        (ChannelConfig.guild_id == guild_id)
        & (ChannelConfig.channel_type == channel_type)
    )

    if row:
        row.channel_id = channel_id
        row.save()
    else:
        ChannelConfig.create(
            guild_id=guild_id,
            channel_id=channel_id,
            channel_type=channel_type,
        )

    CHANNEL_CACHE.setdefault(channel_type, {})[guild_id] = channel_id


def get_channel(guild_id, channel_type):
    return CHANNEL_CACHE.get(channel_type, {}).get(guild_id)


def channel_matches(ctx_channel, configured_channel_id):
    """
    Разрешает работу:
    1) в точно настроенном канале;
    2) в точно настроенной ветке;
    3) в ветке, если настроен её родительский канал.
    """
    if not ctx_channel or not configured_channel_id:
        return False

    if ctx_channel.id == configured_channel_id:
        return True

    parent = getattr(ctx_channel, "parent", None)
    if parent and parent.id == configured_channel_id:
        return True

    return False


# =========================
# ПРОВЕРКА ПРАВ БОТА
# =========================
def get_bot_member(guild):
    if not guild or not bot.user:
        return None
    return guild.me or guild.get_member(bot.user.id)


def get_missing_bot_permissions(channel, guild, need_embed=False):
    """
    Возвращает список недостающих прав для текущего канала/ветки.
    """
    me = get_bot_member(guild)
    if not me or not channel:
        return ["не удалось определить канал или участника бота"]

    perms = channel.permissions_for(me)
    missing = []

    if not perms.view_channel:
        missing.append("View Channel / Просмотр канала")

    if not perms.send_messages:
        missing.append("Send Messages / Отправлять сообщения")

    if isinstance(channel, discord.Thread) and not getattr(perms, "send_messages_in_threads", False):
        missing.append("Send Messages in Threads / Отправлять сообщения в ветках")

    if need_embed and not perms.embed_links:
        missing.append("Embed Links / Встраивать ссылки")

    return missing


async def respond_missing_permissions(ctx, missing):
    text = (
        "❌ У бота нет нужных прав в этом канале/ветке:\n"
        + "\n".join(f"• {item}" for item in missing)
        + "\n\nВыдай эти права роли бота в настройках канала/ветки."
    )

    try:
        if ctx.response.is_done():
            await ctx.followup.send(text, ephemeral=True)
        else:
            await ctx.respond(text, ephemeral=True)
    except Exception:
        logger.error(traceback.format_exc())


async def ensure_bot_can_send(ctx, need_embed=False):
    missing = get_missing_bot_permissions(ctx.channel, ctx.guild, need_embed=need_embed)
    if missing:
        await respond_missing_permissions(ctx, missing)
        return False
    return True


async def safe_ctx_send(ctx, *args, **kwargs):
    """
    Безопасная отправка сообщения в канал команды.
    Если Discord вернёт 403 Missing Access, пользователь получит понятное сообщение.
    """
    try:
        return await ctx.send(*args, **kwargs)
    except discord.Forbidden:
        logger.warning(
            "Discord Forbidden при отправке сообщения: guild=%s channel=%s author=%s",
            getattr(ctx.guild, "id", None),
            getattr(ctx.channel, "id", None),
            getattr(ctx.author, "id", None),
        )
        try:
            await ctx.followup.send(
                "❌ Discord запретил боту отправить сообщение в этот канал/ветку. "
                "Проверь права: View Channel, Send Messages, Send Messages in Threads, Embed Links.",
                ephemeral=True,
            )
        except Exception:
            logger.error(traceback.format_exc())
        return None
    except Exception:
        logger.error(traceback.format_exc())
        try:
            await ctx.followup.send("❌ Ошибка при отправке сообщения. Подробности в bot.log", ephemeral=True)
        except Exception:
            logger.error(traceback.format_exc())
        return None


# =========================
# ПРАВА ПОЛЬЗОВАТЕЛЕЙ
# =========================
def has_access(member):
    return member.guild_permissions.administrator or any(
        role.id in ALLOWED_ROLE_IDS for role in member.roles
    )


def has_activity_access(member):
    return member.guild_permissions.administrator or any(
        role.id in ACTIVITY_ROLE_IDS for role in member.roles
    )


def can_delete_activity(member, author_id):
    return (
        member.id == author_id
        or member.guild_permissions.administrator
        or any(role.id in ALLOWED_ROLE_IDS for role in member.roles)
    )


def can_delete_timer_message(member, author_id):
    return (
        member.id == author_id
        or member.guild_permissions.administrator
        or any(role.id in ALLOWED_ROLE_IDS for role in member.roles)
    )


# =========================
# ОЧИСТКА НЕСУЩЕСТВУЮЩИХ КАНАЛОВ
# =========================
def clean_channels():
    for row in list(ChannelConfig.select()):
        guild = bot.get_guild(row.guild_id)
        if not guild or guild.get_channel_or_thread(row.channel_id) is None:
            row.delete_instance()


# =========================
# EMBED АКТИВНОСТИ
# =========================
def build_activity_embed(author, title, hex_value, region, need_people, voice_channel_id):
    now = datetime.datetime.now()
    footer_time = now.strftime("Сегодня в %H:%M")

    embed = discord.Embed(
        title=title,
        color=discord.Color.green(),
    )

    embed.add_field(name="🌐 Гекс", value=hex_value, inline=True)
    embed.add_field(name="🗺️ Регион", value=region, inline=True)
    embed.add_field(name="👥 Нужно людей", value=need_people, inline=False)
    embed.add_field(name="🔊 Голосовой канал", value=f"<#{voice_channel_id}>", inline=False)
    embed.add_field(name="👤 Создатель активности", value=author.mention, inline=False)

    if author.display_avatar:
        embed.set_thumbnail(url=author.display_avatar.url)

    embed.set_footer(text=f"Активность • {footer_time}")
    return embed


def set_or_replace_embed_field(embed, name, value, inline=False):
    """
    Чтобы приоритет не дублировался при повторном нажатии кнопок.
    """
    for index, field in enumerate(embed.fields):
        if field.name == name:
            embed.set_field_at(index, name=name, value=value, inline=inline)
            return
    embed.add_field(name=name, value=value, inline=inline)


# =========================
# ПАРСИНГ РОЛЕЙ ДЛЯ ПИНГА АКТИВНОСТИ
# =========================
def parse_activity_ping_roles(guild, raw_roles):
    """
    Принимает строку с несколькими ролями и возвращает список discord.Role.

    Поддерживает форматы:
    1) 1390688779056058429 1463921275775877184
    2) 1390688779056058429,1463921275775877184
    3) @роль1 @роль2, то есть Discord-упоминания <@&ID>
    """
    if not raw_roles:
        return [], None

    role_ids = re.findall(r"\d{15,25}", raw_roles)
    if not role_ids:
        return [], "❌ Укажи ID ролей или упоминания ролей через пробел/запятую."

    ping_roles = []
    seen_ids = set()

    for raw_id in role_ids:
        role_id = int(raw_id)

        if role_id in seen_ids:
            continue
        seen_ids.add(role_id)

        if role_id not in ACTIVITY_PING_ROLE_IDS:
            return [], f"❌ Роль `{role_id}` нельзя пинговать через команду /активность."

        role = guild.get_role(role_id) if guild else None
        if not role:
            return [], f"❌ Роль `{role_id}` не найдена на сервере."

        ping_roles.append(role)

    return ping_roles, None


# =========================
# УВЕДОМЛЕНИЯ СКЛАДА
# =========================
def get_sklad_warning_key(seconds_left):
    """
    Возвращает ключ уведомления склада.
    Теперь уведомления идут строго один раз за каждый порог:
    3 часа, 2 часа, 1 час.
    """
    if seconds_left <= 0:
        return None

    if 2 * 60 * 60 < seconds_left <= 3 * 60 * 60:
        return "3h"

    if 60 * 60 < seconds_left <= 2 * 60 * 60:
        return "2h"

    if 0 < seconds_left <= 60 * 60:
        return "1h"

    return None


def build_sklad_warning_text(timer_row, seconds_left, ping_text=""):
    warning_time = int(datetime.datetime.now(datetime.timezone.utc).timestamp())

    if seconds_left <= 60 * 60:
        left_text = "1 час"
    elif seconds_left <= 2 * 60 * 60:
        left_text = "2 часа"
    else:
        left_text = "3 часа"

    prefix = f"{ping_text}\n" if ping_text else ""

    return (
        f"{prefix}"
        "⚠️ **Склад скоро сгорит!**\n"
        f"⏳ Осталось примерно: **{left_text}**\n\n"
        f"{timer_row.text}\n"
        f"⏰ До окончания: <t:{timer_row.time_end}:R>\n"
        f"📍 Сообщение склада: <#{timer_row.channel_id}>\n"
        "✅ Нужно обновить склад в игре и нажать **Обновить склад** в чате складов.\n"
        f"🕒 Уведомление: <t:{warning_time}:f>"
    )


def get_sklad_ping_roles(guild):
    """
    Берёт роли из SKLAD_PING_ROLE_IDS и возвращает только те,
    которые реально существуют на сервере.
    """
    if not guild:
        return []

    roles = []
    seen_ids = set()

    for role_id in SKLAD_PING_ROLE_IDS:
        if role_id in seen_ids:
            continue
        seen_ids.add(role_id)

        role = guild.get_role(role_id)
        if role:
            roles.append(role)
        else:
            logger.warning("Роль склада для пинга не найдена на сервере: %s", role_id)

    return roles


async def delete_sklad_notifications(timer_message_id):
    rows = list(
        SkladNotification.select().where(
            SkladNotification.timer_message_id == timer_message_id
        )
    )

    for row in rows:
        try:
            guild = bot.get_guild(row.guild_id)
            channel = guild.get_channel_or_thread(row.notification_channel_id) if guild else None

            if channel:
                try:
                    msg = await channel.fetch_message(row.notification_message_id)
                    await msg.delete()
                except discord.NotFound:
                    pass
                except discord.Forbidden:
                    logger.warning(
                        "Нет прав удалить уведомление склада: channel=%s message=%s",
                        row.notification_channel_id,
                        row.notification_message_id,
                    )
        except Exception:
            logger.error(traceback.format_exc())
        finally:
            row.delete_instance()


async def send_sklad_warning_if_needed(timer_row, now_ts):
    if timer_row.kind != "sklad":
        return

    notify_channel_id = get_channel(timer_row.guild_id, "sklad_notify")
    if not notify_channel_id:
        return

    seconds_left = timer_row.time_end - now_ts
    warning_key = get_sklad_warning_key(seconds_left)
    if not warning_key:
        return

    exists = SkladNotification.get_or_none(
        (SkladNotification.timer_message_id == timer_row.message_id)
        & (SkladNotification.warning_key == warning_key)
    )
    if exists:
        return

    guild = bot.get_guild(timer_row.guild_id)
    channel = guild.get_channel_or_thread(notify_channel_id) if guild else None
    if not channel:
        return

    missing = get_missing_bot_permissions(channel, guild, need_embed=False)
    if missing:
        logger.warning(
            "Нет прав отправить уведомление склада: guild=%s channel=%s missing=%s",
            timer_row.guild_id,
            notify_channel_id,
            missing,
        )
        return

    ping_roles = get_sklad_ping_roles(guild)
    ping_text = " ".join(role.mention for role in ping_roles)

    allowed_mentions = discord.AllowedMentions(
        roles=ping_roles if ping_roles else False,
        users=False,
        everyone=False,
    )

    try:
        msg = await channel.send(
            build_sklad_warning_text(timer_row, seconds_left, ping_text),
            allowed_mentions=allowed_mentions,
        )
    except discord.Forbidden:
        logger.warning(
            "Discord Forbidden при отправке уведомления склада: guild=%s channel=%s",
            timer_row.guild_id,
            notify_channel_id,
        )
        return

    SkladNotification.create(
        guild_id=timer_row.guild_id,
        timer_message_id=timer_row.message_id,
        notification_channel_id=notify_channel_id,
        notification_message_id=msg.id,
        warning_key=warning_key,
        created_at=now_ts,
    )


# =========================
# КНОПКИ
# =========================
class PriorityView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(PriorityButton("🔴", "high", discord.Color.from_rgb(255, 0, 0)))
        self.add_item(PriorityButton("🟠", "medium", discord.Color.orange()))
        self.add_item(PriorityButton("🟡", "low", discord.Color.gold()))
        self.add_item(PriorityButton("🟢", "minimal", discord.Color.green()))


class PriorityButton(Button):
    def __init__(self, emoji, value, color):
        super().__init__(
            emoji=emoji,
            style=discord.ButtonStyle.secondary,
            custom_id=f"priority_{value}",
        )
        self.value = value
        self.embed_color = color

    async def callback(self, interaction: discord.Interaction):
        row = Activity.get_or_none(Activity.message_id == interaction.message.id)
        if not row:
            return await interaction.response.send_message("❌ Активность не найдена", ephemeral=True)

        if interaction.user.id != row.author:
            return await interaction.response.send_message("❌ Приоритет может выбрать только автор", ephemeral=True)

        row.priority = self.value
        row.save()

        if not interaction.message.embeds:
            return await interaction.response.send_message("❌ Embed активности не найден", ephemeral=True)

        embed = interaction.message.embeds[0]
        embed.color = self.embed_color

        priority_text = {
            "high": "🔴 Высокий",
            "medium": "🟠 Средний",
            "low": "🟡 Низкий",
            "minimal": "🟢 Минимальный",
        }

        set_or_replace_embed_field(
            embed,
            name="⚡ Приоритет",
            value=priority_text[self.value],
            inline=False,
        )

        await interaction.response.edit_message(embed=embed, view=ActivityView())


class SkladView(View):
    def __init__(self):
        super().__init__(timeout=None)

        btn_update = Button(
            label="Обновить склад",
            style=discord.ButtonStyle.green,
            custom_id="sklad_update",
        )
        btn_delete = Button(
            label="Удалить",
            style=discord.ButtonStyle.red,
            custom_id="sklad_delete",
        )

        btn_update.callback = self.update
        btn_delete.callback = self.delete

        self.add_item(btn_delete)
        self.add_item(btn_update)

    async def update(self, interaction):
        await interaction.response.defer()

        row = Timer.get_or_none(Timer.message_id == interaction.message.id)
        if not row:
            return await interaction.followup.send("❌ Склад не найден", ephemeral=True)

        now = datetime.datetime.now(datetime.timezone.utc)
        new_end = int((now + datetime.timedelta(hours=48)).timestamp())

        row.time_end = new_end
        row.kind = "sklad"
        row.save()

        await delete_sklad_notifications(row.message_id)

        member = interaction.guild.get_member(interaction.user.id)
        nickname = member.display_name if member else "пользователь"

        updated_text = (
            f"{row.text}\n"
            f"⏰ До окончания: <t:{new_end}:R>\n"
            f"🔄 Обновил склад: {nickname}"
        )

        await interaction.message.edit(content=updated_text, view=SkladView())
        await interaction.followup.send("✅ Склад обновлён на 48 часов", ephemeral=True)

    async def delete(self, interaction):
        await interaction.response.defer()

        row = Timer.get_or_none(Timer.message_id == interaction.message.id)
        if not row:
            return await interaction.followup.send("❌ Склад не найден", ephemeral=True)

        member = interaction.guild.get_member(interaction.user.id)
        if not member or not can_delete_timer_message(member, row.author):
            return await interaction.followup.send("❌ Нет прав на удаление", ephemeral=True)

        await delete_sklad_notifications(row.message_id)
        row.delete_instance()
        await interaction.message.delete()


class SkladExpiredView(View):
    def __init__(self):
        super().__init__(timeout=None)

        btn_delete = Button(
            label="Удалить",
            style=discord.ButtonStyle.red,
            custom_id="sklad_expired_delete",
        )
        btn_delete.callback = self.delete
        self.add_item(btn_delete)

    async def delete(self, interaction):
        await interaction.response.defer()

        row = Timer.get_or_none(Timer.message_id == interaction.message.id)
        if not row:
            return await interaction.followup.send("❌ Склад не найден", ephemeral=True)

        member = interaction.guild.get_member(interaction.user.id)
        if not member or not can_delete_timer_message(member, row.author):
            return await interaction.followup.send("❌ Нет прав на удаление", ephemeral=True)

        await delete_sklad_notifications(row.message_id)
        row.delete_instance()
        await interaction.message.delete()


class TimerView(View):
    def __init__(self):
        super().__init__(timeout=None)

        btn = Button(
            label="Удалить таймер",
            style=discord.ButtonStyle.red,
            custom_id="timer_delete",
        )
        btn.callback = self.delete
        self.add_item(btn)

    async def delete(self, interaction):
        await interaction.response.defer()

        row = Timer.get_or_none(Timer.message_id == interaction.message.id)
        if not row:
            return await interaction.followup.send("❌ Таймер не найден", ephemeral=True)

        if interaction.user.id != row.author:
            return await interaction.followup.send("❌ Только автор может удалить этот таймер", ephemeral=True)

        row.delete_instance()
        await interaction.message.delete()


class MPFView(View):
    def __init__(self, show_take=False):
        super().__init__(timeout=None)

        delete = Button(
            label="Удалить таймер",
            style=discord.ButtonStyle.red,
            custom_id="mpf_delete",
        )
        delete.callback = self.delete
        self.add_item(delete)

        take = Button(
            label="Забрал заказ",
            style=discord.ButtonStyle.green,
            custom_id="mpf_take",
            disabled=not show_take,
        )
        take.callback = self.take
        self.add_item(take)

    async def take(self, interaction):
        await interaction.response.defer()

        row = Timer.get_or_none(Timer.message_id == interaction.message.id)
        if not row:
            return await interaction.followup.send("❌ МПФ не найден", ephemeral=True)

        if row.taken_by:
            return await interaction.followup.send("❌ Уже забрали", ephemeral=True)

        row.taken_by = interaction.user.id
        row.save()

        member = interaction.guild.get_member(interaction.user.id)
        nickname = member.display_name if member else "пользователь"

        await interaction.message.edit(
            content=interaction.message.content + f"\n\n📦 Забрал: {nickname}",
            view=MPFView(show_take=False),
        )
        await interaction.followup.send("✅ Забрал", ephemeral=True)

    async def delete(self, interaction):
        await interaction.response.defer()

        row = Timer.get_or_none(Timer.message_id == interaction.message.id)
        if not row:
            return await interaction.followup.send("❌ МПФ не найден", ephemeral=True)

        if interaction.user.id != row.author:
            return await interaction.followup.send("❌ Только автор может удалить этот МПФ", ephemeral=True)

        row.delete_instance()
        await interaction.message.delete()


class ActivityView(View):
    def __init__(self):
        super().__init__(timeout=None)

        delete = Button(
            label="Удалить активность",
            style=discord.ButtonStyle.red,
            custom_id="activity_delete",
        )
        delete.callback = self.delete
        self.add_item(delete)

    async def delete(self, interaction):
        await interaction.response.defer()

        row = Activity.get_or_none(Activity.message_id == interaction.message.id)
        if not row:
            return await interaction.followup.send("❌ Активность не найдена", ephemeral=True)

        member = interaction.guild.get_member(interaction.user.id)
        if not member or not can_delete_activity(member, row.author):
            return await interaction.followup.send(
                "❌ Удалить активность может только создатель, администратор или спец.роль",
                ephemeral=True,
            )

        row.delete_instance()
        await interaction.message.delete()


# =========================
# ОЧИСТКА БД ПРИ РУЧНОМ УДАЛЕНИИ СООБЩЕНИЯ
# =========================
@bot.event
async def on_raw_message_delete(payload):
    """
    Если кто-то вручную удалил сообщение таймера/склада/МПФ/активности,
    бот чистит запись из БД.
    """
    try:
        timer_row = Timer.get_or_none(Timer.message_id == payload.message_id)
        if timer_row:
            if timer_row.kind in ("sklad", "sklad_done"):
                await delete_sklad_notifications(timer_row.message_id)
            timer_row.delete_instance()
            logger.info("Удалена запись Timer из БД после ручного удаления сообщения: %s", payload.message_id)
            return

        activity_row = Activity.get_or_none(Activity.message_id == payload.message_id)
        if activity_row:
            activity_row.delete_instance()
            logger.info("Удалена запись Activity из БД после ручного удаления сообщения: %s", payload.message_id)
            return

        notification_row = SkladNotification.get_or_none(
            SkladNotification.notification_message_id == payload.message_id
        )
        if notification_row:
            notification_row.delete_instance()
            logger.info("Удалена запись SkladNotification после ручного удаления уведомления: %s", payload.message_id)

    except Exception:
        logger.error(traceback.format_exc())


# =========================
# ЦИКЛ ТАЙМЕРОВ
# =========================
@tasks.loop(seconds=30)
async def loop():
    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())

    for t in Timer.select().where((Timer.time_end >= now) & (Timer.kind == "sklad")):
        try:
            await send_sklad_warning_if_needed(t, now)
        except Exception:
            logger.error(traceback.format_exc())

    expired = Timer.select().where(
        (Timer.time_end < now)
        & (Timer.kind.in_(["mpf", "timer", "sklad"]))
    )

    for t in list(expired):
        try:
            guild = bot.get_guild(t.guild_id)
            if not guild:
                if t.kind == "sklad":
                    await delete_sklad_notifications(t.message_id)
                t.delete_instance()
                continue

            channel = guild.get_channel_or_thread(t.channel_id)
            if not channel:
                if t.kind == "sklad":
                    await delete_sklad_notifications(t.message_id)
                t.delete_instance()
                continue

            try:
                msg = await channel.fetch_message(t.message_id)
            except discord.NotFound:
                if t.kind == "sklad":
                    await delete_sklad_notifications(t.message_id)
                t.delete_instance()
                continue
            except discord.Forbidden:
                logger.warning(
                    "Нет доступа к сообщению таймера: guild=%s channel=%s message=%s",
                    t.guild_id,
                    t.channel_id,
                    t.message_id,
                )
                continue

            member = guild.get_member(t.author)
            nickname = member.display_name if member else "пользователь"
            mention = member.mention if member else "пользователь"

            if t.kind == "mpf":
                item = t.text
                marker = "📦 Что поставил: "
                if marker in item:
                    item = item.split(marker, 1)[1].splitlines()[0]

                await msg.edit(
                    content=(
                        f"👤 Кто поставил: {nickname}\n"
                        f"📦 Что поставил: {item}\n"
                        f"📦 Ящиков: {t.boxes}\n"
                        f"✅ Статус: готово"
                    ),
                    view=MPFView(show_take=True),
                )

                t.kind = "mpf_ready"
                t.save()
                continue

            if t.kind == "timer":
                await msg.edit(
                    content=(
                        f"👤 {mention}\n"
                        f"📌 {t.text}\n"
                        f"✅ Статус: готово"
                    ),
                    view=TimerView(),
                )

                t.kind = "timer_done"
                t.save()
                continue

            if t.kind == "sklad":
                await delete_sklad_notifications(t.message_id)
                await msg.edit(
                    content=(
                        f"🔥 **Склад сгорел**\n"
                        f"{t.text}\n"
                        f"⏰ Сгорел: <t:{now}:f> (<t:{now}:R>)"
                    ),
                    view=SkladExpiredView(),
                )
                t.kind = "sklad_done"
                t.save()
                continue

        except Exception:
            logger.error(traceback.format_exc())
            try:
                if t.kind == "sklad":
                    await delete_sklad_notifications(t.message_id)
                t.delete_instance()
            except Exception:
                logger.error(traceback.format_exc())


# =========================
# ЗАПУСК БОТА
# =========================
@bot.event
async def on_ready():
    logger.info(f"Бот онлайн: {bot.user}")
    logger.info(f"Сервера бота: {[f'{g.name} ({g.id})' for g in bot.guilds]}")

    clean_channels()
    load_channels()

    bot.add_view(SkladView())
    bot.add_view(SkladExpiredView())
    bot.add_view(TimerView())
    bot.add_view(MPFView())
    bot.add_view(ActivityView())
    bot.add_view(PriorityView())

    if not loop.is_running():
        loop.start()


# =========================
# КОМАНДЫ НАСТРОЙКИ
# =========================
@bot.slash_command(name="setskladchannel", guild_ids=[GUILD_ID])
async def set_sklad_channel(
    ctx,
    канал_склада: discord.TextChannel = None,
    канал_уведомлений: discord.TextChannel = None,
    айди_ветки: str = None,
):
    """
    Установить канал/ветку для складов и канал уведомлений склада.
    """
    if not has_access(ctx.author):
        return await ctx.respond("❌ Нет прав", ephemeral=True)

    target_id = None

    if канал_склада:
        target_id = канал_склада.id
    elif айди_ветки:
        try:
            target_id = int(айди_ветки)
        except ValueError:
            return await ctx.respond("❌ Неверный ID ветки", ephemeral=True)
    else:
        return await ctx.respond("❌ Укажи канал_склада или айди_ветки", ephemeral=True)

    if not канал_уведомлений:
        return await ctx.respond("❌ Укажи канал_уведомлений", ephemeral=True)

    set_channel(ctx.guild.id, target_id, "sklad")
    set_channel(ctx.guild.id, канал_уведомлений.id, "sklad_notify")

    await ctx.respond(
        f"✅ Чат склада установлен: `{target_id}`\n"
        f"🔔 Чат уведомлений склада установлен: {канал_уведомлений.mention}",
        ephemeral=True,
    )


@bot.slash_command(name="setsimpletimer", guild_ids=[GUILD_ID])
async def set_simple_timer_channel(
    ctx,
    канал: discord.TextChannel = None,
    айди_ветки: str = None,
):
    """
    Установить канал/ветку для команды /таймер.
    """
    if not has_access(ctx.author):
        return await ctx.respond("❌ Нет прав", ephemeral=True)

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

    set_channel(ctx.guild.id, target_id, "simple")
    await ctx.respond(f"✅ Чат таймера установлен: `{target_id}`", ephemeral=True)


@bot.slash_command(name="setmpf", guild_ids=[GUILD_ID])
async def set_mpf_channel(
    ctx,
    канал: discord.TextChannel = None,
    айди_ветки: str = None,
):
    """
    Установить канал/ветку для команды /мпф.
    """
    if not has_access(ctx.author):
        return await ctx.respond("❌ Нет прав", ephemeral=True)

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

    set_channel(ctx.guild.id, target_id, "mpf")
    await ctx.respond(f"✅ Чат МПФ установлен: `{target_id}`", ephemeral=True)


@bot.slash_command(name="setaktivchat", guild_ids=[GUILD_ID])
async def set_activity_channel(
    ctx,
    канал: discord.TextChannel = None,
    айди_ветки: str = None,
):
    """
    Установить канал/ветку для команды /активность.
    """
    if not has_access(ctx.author):
        return await ctx.respond("❌ Нет прав", ephemeral=True)

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

    set_channel(ctx.guild.id, target_id, "aktiv")
    await ctx.respond(f"✅ Чат активностей установлен: `{target_id}`", ephemeral=True)


# =========================
# КОМАНДА: ТАЙМЕР
# =========================
@bot.slash_command(name="таймер", guild_ids=[GUILD_ID])
async def таймер(
    ctx,
    название: str,
    дни: int = 0,
    часы: int = 0,
    минуты: int = 0,
):
    if дни == 0 and часы == 0 and минуты == 0:
        return await ctx.respond("❌ Укажи время", ephemeral=True)

    if дни < 0 or часы < 0 or минуты < 0:
        return await ctx.respond("❌ Время не может быть отрицательным", ephemeral=True)

    channel_id = get_channel(ctx.guild.id, "simple")
    if not channel_id or not channel_matches(ctx.channel, channel_id):
        return await ctx.respond("❌ Не тот канал", ephemeral=True)

    if not await ensure_bot_can_send(ctx, need_embed=False):
        return

    await ctx.defer(ephemeral=True)

    end = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        days=дни,
        hours=часы,
        minutes=минуты,
    )
    end_ts = int(end.timestamp())

    msg = await safe_ctx_send(
        ctx,
        f"👤 {ctx.author.mention}\n📌 {название}\n⏰ <t:{end_ts}:R>",
        view=TimerView(),
    )

    if not msg:
        return

    Timer.create(
        guild_id=ctx.guild.id,
        channel_id=ctx.channel.id,
        message_id=msg.id,
        text=название,
        time_end=end_ts,
        author=ctx.author.id,
        kind="timer",
    )

    await ctx.followup.send("✅ Таймер создан", ephemeral=True)


# =========================
# КОМАНДА: СКЛАД
# =========================
@bot.slash_command(name="склад", guild_ids=[GUILD_ID])
async def склад(ctx, гекс: str, регион: str, склад: str, пароль: str):
    channel_id = get_channel(ctx.guild.id, "sklad")
    if channel_id and not channel_matches(ctx.channel, channel_id):
        return await ctx.respond("❌ Не тот канал", ephemeral=True)

    if not await ensure_bot_can_send(ctx, need_embed=False):
        return

    await ctx.defer(ephemeral=True)

    end_ts = int(
        (
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=48)
        ).timestamp()
    )

    text = (
        f"👤 {ctx.author.display_name}\n"
        f"**Гекс:** {гекс}\n"
        f"**Регион:** {регион}\n"
        f"**Склад:** {склад}\n"
        f"**Пароль:** {пароль}"
    )

    msg = await safe_ctx_send(
        ctx,
        f"{text}\n⏰ До окончания: <t:{end_ts}:R>",
        view=SkladView(),
    )

    if not msg:
        return

    Timer.create(
        guild_id=ctx.guild.id,
        channel_id=ctx.channel.id,
        message_id=msg.id,
        text=text,
        time_end=end_ts,
        author=ctx.author.id,
        kind="sklad",
    )

    await ctx.followup.send("✅ Склад создан", ephemeral=True)


# =========================
# КОМАНДА: МПФ
# =========================
@bot.slash_command(name="мпф", guild_ids=[GUILD_ID])
async def мпф(
    ctx,
    что_поставил: str,
    ящиков: int,
    дни: int = 0,
    часы: int = 0,
    минуты: int = 0,
):
    if дни == 0 and часы == 0 and минуты == 0:
        return await ctx.respond("❌ Укажи время", ephemeral=True)

    if дни < 0 or часы < 0 or минуты < 0:
        return await ctx.respond("❌ Время не может быть отрицательным", ephemeral=True)

    if ящиков <= 0:
        return await ctx.respond("❌ Количество ящиков должно быть больше 0", ephemeral=True)

    channel_id = get_channel(ctx.guild.id, "mpf")
    if not channel_id or not channel_matches(ctx.channel, channel_id):
        return await ctx.respond("❌ Не тот канал", ephemeral=True)

    if not await ensure_bot_can_send(ctx, need_embed=False):
        return

    await ctx.defer(ephemeral=True)

    end = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        days=дни,
        hours=часы,
        minutes=минуты,
    )
    end_ts = int(end.timestamp())

    text = (
        f"👤 Кто поставил: {ctx.author.display_name}\n"
        f"📦 Что поставил: {что_поставил}\n"
        f"📦 Ящиков: {ящиков}\n"
        f"⌛ <t:{end_ts}:R>\n"
        f"Статус: ожидание"
    )

    msg = await safe_ctx_send(ctx, text, view=MPFView(show_take=False))

    if not msg:
        return

    Timer.create(
        guild_id=ctx.guild.id,
        channel_id=ctx.channel.id,
        message_id=msg.id,
        text=text,
        time_end=end_ts,
        author=ctx.author.id,
        kind="mpf",
        boxes=ящиков,
        taken_by=None,
    )

    await ctx.followup.send("✅ МПФ создан", ephemeral=True)


# =========================
# КОМАНДА: АКТИВНОСТЬ
# =========================
@bot.slash_command(name="активность", guild_ids=[GUILD_ID])
async def активность(
    ctx,
    название_активности: str,
    гекс: str,
    регион: str,
    нужно_людей: str,
    голосовой_канал: discord.VoiceChannel,
    роли_для_пинга: str = None,
):
    """
    Создать активность.

    роли_для_пинга можно указывать так:
    - 1390688779056058429 1463921275775877184
    - 1390688779056058429,1463921275775877184
    - @роль1 @роль2
    """
    if not has_activity_access(ctx.author):
        return await ctx.respond("❌ Нет прав на создание активности", ephemeral=True)

    channel_id = get_channel(ctx.guild.id, "aktiv")
    if not channel_id or not channel_matches(ctx.channel, channel_id):
        return await ctx.respond("❌ Не тот канал", ephemeral=True)

    ping_roles, error_text = parse_activity_ping_roles(ctx.guild, роли_для_пинга)
    if error_text:
        return await ctx.respond(error_text, ephemeral=True)

    if not await ensure_bot_can_send(ctx, need_embed=True):
        return

    await ctx.defer(ephemeral=True)

    created_at = int(datetime.datetime.now(datetime.timezone.utc).timestamp())

    embed = build_activity_embed(
        author=ctx.author,
        title=название_активности,
        hex_value=гекс,
        region=регион,
        need_people=нужно_людей,
        voice_channel_id=голосовой_канал.id,
    )

    ping_text = " ".join(role.mention for role in ping_roles) if ping_roles else None

    allowed_mentions = discord.AllowedMentions(
        roles=ping_roles if ping_roles else False,
        users=False,
        everyone=False,
    )

    msg = await safe_ctx_send(
        ctx,
        content=ping_text,
        embed=embed,
        view=PriorityView(),
        allowed_mentions=allowed_mentions,
    )

    if not msg:
        return

    Activity.create(
        guild_id=ctx.guild.id,
        channel_id=ctx.channel.id,
        message_id=msg.id,
        author=ctx.author.id,
        title=название_активности,
        location=f"Гекс: {гекс}\nРегион: {регион}",
        need_people=нужно_людей,
        voice_channel_id=голосовой_канал.id,
        created_at=created_at,
        priority=None,
    )

    await ctx.followup.send("✅ Активность создана. Теперь выбери приоритет кнопкой под сообщением.", ephemeral=True)


# =========================
# RUN
# =========================
token = os.environ.get("DISCORD_BOT_TOKEN")
if not token:
    raise RuntimeError(
        "Не найден DISCORD_BOT_TOKEN. "
        "Добавь токен в переменные окружения/Secrets, а не в .txt файл."
    )

bot.run(token)
