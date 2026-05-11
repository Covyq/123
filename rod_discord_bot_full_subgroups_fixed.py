from __future__ import annotations
import os
import re
import asyncio
import datetime
import logging
import traceback
from typing import Dict, List, Optional, Tuple, Set

import aiohttp
import discord
from discord.ext import tasks
from discord.ui import View, Button
from peewee import *

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ============================================================
# НАСТРОЙКИ
# ============================================================
GUILD_ID = 419565206335651840
DB_FILE = "RodBotDataBase.db"

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
STEAM_API_KEY = os.getenv("STEAM_API_KEY", "")
FOXHOLE_APP_ID = 505460

ONLINE_UPDATE_SECONDS = 30
STEAM_CACHE_SECONDS = 60
CHANNEL_RENAME_COOLDOWN_SECONDS = 300

# Роли, которым доступно управление ботом/каналами/онлайном.
ALLOWED_ROLE_IDS = [
    1420081710510379079,
    694197038362918923,
    475990315623251969,
    1397716497928949843,
    1397716702242013276,
    422500854910681089,
    1224787828815171595,
    1477953756225081394,
    831242102179758100,
]

ONLINE_SETTER_ROLE_IDS = [
    1420081710510379079,
    694197038362918923,
    475990315623251969,
    1397716497928949843,
    1397716702242013276,
    422500854910681089,
    1224787828815171595,
]

AKTIV_ROLE_IDS = ALLOWED_ROLE_IDS[:]

# Если хочешь, чтобы бот проверял Steam только у людей с определёнными ролями,
# впиши ID ролей сюда. Если список пустой — бот проверяет всех, кто привязал Steam.
STEAM_ONLINE_TRACK_ROLE_IDS: List[int] = []

# ============================================================
# Discord / DB
# ============================================================
logging.basicConfig(level=logging.INFO)
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.presences = True
intents.messages = True
intents.message_content = True

bot = discord.Bot(intents=intents, debug_guilds=[GUILD_ID])
db = SqliteDatabase(DB_FILE)

CHANNEL_CACHE: Dict[str, Dict[str, Optional[int]]] = {
    "simple": {"channel_id": None},
    "mpf": {"channel_id": None},
    "sklad": {"channel_id": None},
    "aktiv": {"channel_id": None},
    "online": {"channel_id": None, "message_id": None, "last_rename_at": None},
}

STEAM_CHECK_CACHE: Dict[int, Tuple[float, bool]] = {}

# ============================================================
# МОДЕЛИ БАЗЫ ДАННЫХ
# ============================================================
class BaseModel(Model):
    class Meta:
        database = db


class ChannelConfig(BaseModel):
    key = CharField(unique=True)
    channel_id = BigIntegerField(null=True)
    message_id = BigIntegerField(null=True)
    updated_at = DateTimeField(default=datetime.datetime.utcnow)


class SteamLink(BaseModel):
    discord_id = BigIntegerField(unique=True)
    steam_id = CharField()
    updated_at = DateTimeField(default=datetime.datetime.utcnow)


class OnlineGroup(BaseModel):
    title = CharField()
    emoji = CharField(default="")
    sort_order = IntegerField(default=0)
    parent = ForeignKeyField("self", null=True, backref="children", on_delete="CASCADE")
    is_enabled = BooleanField(default=True)


class OnlineGroupRole(BaseModel):
    group = ForeignKeyField(OnlineGroup, backref="roles", on_delete="CASCADE")
    role_id = BigIntegerField()
    sort_order = IntegerField(default=0)

    class Meta:
        indexes = ((('group', 'role_id'), True),)


class SimpleTimer(BaseModel):
    author_id = BigIntegerField()
    channel_id = BigIntegerField()
    message_id = BigIntegerField(null=True)
    title = CharField()
    text = TextField(default="")
    end_at = DateTimeField()
    is_done = BooleanField(default=False)
    created_at = DateTimeField(default=datetime.datetime.utcnow)


class MPFOrder(BaseModel):
    author_id = BigIntegerField()
    claimer_id = BigIntegerField(null=True)
    channel_id = BigIntegerField()
    message_id = BigIntegerField(null=True)
    item = CharField()
    amount = CharField(default="")
    comment = TextField(default="")
    is_done = BooleanField(default=False)
    created_at = DateTimeField(default=datetime.datetime.utcnow)


class SkladRequest(BaseModel):
    author_id = BigIntegerField()
    channel_id = BigIntegerField()
    message_id = BigIntegerField(null=True)
    item = CharField()
    amount = CharField(default="")
    comment = TextField(default="")
    is_done = BooleanField(default=False)
    created_at = DateTimeField(default=datetime.datetime.utcnow)


# ============================================================
# УТИЛИТЫ
# ============================================================
def utcnow() -> datetime.datetime:
    return datetime.datetime.utcnow()


def has_any_role(member: discord.Member, role_ids: List[int]) -> bool:
    if member.guild_permissions.administrator:
        return True
    user_roles = {r.id for r in member.roles}
    return bool(user_roles & set(role_ids))


def is_steam_id64(value: str) -> bool:
    return bool(re.fullmatch(r"\d{17}", value.strip()))


def get_channel_from_cache(key: str) -> Optional[discord.abc.GuildChannel]:
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return None
    channel_id = CHANNEL_CACHE.get(key, {}).get("channel_id")
    if not channel_id:
        return None
    return guild.get_channel(int(channel_id))


def save_channel_config(key: str, channel_id: int, message_id: Optional[int] = None) -> None:
    obj, _ = ChannelConfig.get_or_create(key=key)
    obj.channel_id = channel_id
    if message_id is not None:
        obj.message_id = message_id
    obj.updated_at = utcnow()
    obj.save()
    CHANNEL_CACHE.setdefault(key, {})["channel_id"] = channel_id
    if message_id is not None:
        CHANNEL_CACHE[key]["message_id"] = message_id


def load_channel_cache() -> None:
    for obj in ChannelConfig.select():
        CHANNEL_CACHE.setdefault(obj.key, {})["channel_id"] = obj.channel_id
        CHANNEL_CACHE.setdefault(obj.key, {})["message_id"] = obj.message_id


async def safe_respond(ctx: discord.ApplicationContext, content: str, ephemeral: bool = True) -> None:
    try:
        await ctx.respond(content, ephemeral=ephemeral)
    except discord.InteractionResponded:
        await ctx.followup.send(content, ephemeral=ephemeral)


def discord_activity_has_foxhole(member: discord.Member) -> bool:
    for activity in member.activities:
        name = getattr(activity, "name", "") or ""
        if "foxhole" in name.lower():
            return True
    return False


async def steam_has_foxhole(member: discord.Member) -> bool:
    link = SteamLink.get_or_none(SteamLink.discord_id == member.id)
    if not link or not STEAM_API_KEY:
        return False

    cached = STEAM_CHECK_CACHE.get(member.id)
    now_ts = datetime.datetime.now().timestamp()
    if cached and now_ts - cached[0] < STEAM_CACHE_SECONDS:
        return cached[1]

    url = "https://api.steampowered.com/IPlayerService/GetRecentlyPlayedGames/v0001/"
    params = {
        "key": STEAM_API_KEY,
        "steamid": link.steam_id,
        "format": "json",
        "count": 5,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=10) as resp:
                if resp.status != 200:
                    STEAM_CHECK_CACHE[member.id] = (now_ts, False)
                    return False
                data = await resp.json()
        games = data.get("response", {}).get("games", [])
        result = any(int(game.get("appid", 0)) == FOXHOLE_APP_ID for game in games)
        STEAM_CHECK_CACHE[member.id] = (now_ts, result)
        return result
    except Exception:
        logging.exception("Steam check failed")
        STEAM_CHECK_CACHE[member.id] = (now_ts, False)
        return False


async def member_is_online_foxhole(member: discord.Member) -> bool:
    if member.bot:
        return False
    if discord_activity_has_foxhole(member):
        return True
    return await steam_has_foxhole(member)


async def get_online_members(guild: discord.Guild) -> Set[int]:
    online_ids: Set[int] = set()
    tasks_to_run = []
    candidates = []

    for member in guild.members:
        if member.bot:
            continue
        if discord_activity_has_foxhole(member):
            online_ids.add(member.id)
            continue
        if STEAM_ONLINE_TRACK_ROLE_IDS:
            if not ({r.id for r in member.roles} & set(STEAM_ONLINE_TRACK_ROLE_IDS)):
                continue
        if SteamLink.get_or_none(SteamLink.discord_id == member.id):
            candidates.append(member)
            tasks_to_run.append(steam_has_foxhole(member))

    if tasks_to_run:
        results = await asyncio.gather(*tasks_to_run, return_exceptions=True)
        for member, result in zip(candidates, results):
            if result is True:
                online_ids.add(member.id)

    return online_ids


def ensure_default_online_groups() -> None:
    if OnlineGroup.select().count() > 0:
        return

    defaults = [
        ("👑", "Капитан", [422500854910681089], []),
        ("🛡️", "Зам. капитана", [1224787828815171595], []),
        ("🎖️", "Офицерский состав", [], [
            ("", "Офицеры", [475990315623251969]),
        ]),
        ("⚔️", "Сержантский состав", [], [
            ("", "Сержанты", [1397716497928949843, 1397716702242013276]),
        ]),
        ("🩸", "Свежая кровь", [], [
            ("", "Новобранцы", [1477953756225081394]),
        ]),
        ("🪖", "Бойцы", [], [
            ("", "Основной состав", [831242102179758100]),
        ]),
        ("✈️", "Авиация", [], [
            ("🛫", "Авиационное направление", []),
            ("🛩️", "Пилоты с допуском", []),
        ]),
    ]

    for index, (emoji, title, role_ids, children) in enumerate(defaults, start=1):
        group = OnlineGroup.create(title=title, emoji=emoji, sort_order=index)
        for r_index, role_id in enumerate(role_ids, start=1):
            OnlineGroupRole.create(group=group, role_id=role_id, sort_order=r_index)
        for c_index, (c_emoji, c_title, c_roles) in enumerate(children, start=1):
            child = OnlineGroup.create(title=c_title, emoji=c_emoji, sort_order=c_index, parent=group)
            for r_index, role_id in enumerate(c_roles, start=1):
                OnlineGroupRole.create(group=child, role_id=role_id, sort_order=r_index)


def group_label(group: OnlineGroup) -> str:
    prefix = f"{group.emoji} " if group.emoji else ""
    return f"{prefix}{group.title}".strip()


def member_has_group_role(member: discord.Member, group: OnlineGroup) -> bool:
    member_role_ids = {r.id for r in member.roles}
    role_ids = [r.role_id for r in group.roles.order_by(OnlineGroupRole.sort_order)]
    return bool(member_role_ids & set(role_ids))


def group_direct_members(guild: discord.Guild, group: OnlineGroup, online_ids: Set[int]) -> List[discord.Member]:
    result = []
    for member in guild.members:
        if member.id in online_ids and member_has_group_role(member, group):
            result.append(member)
    return sorted(result, key=lambda m: m.display_name.lower())


def group_total_count(guild: discord.Guild, group: OnlineGroup, online_ids: Set[int]) -> int:
    ids: Set[int] = {m.id for m in group_direct_members(guild, group, online_ids)}
    for child in group.children.where(OnlineGroup.is_enabled == True).order_by(OnlineGroup.sort_order):
        ids |= {m.id for m in group_direct_members(guild, child, online_ids)}
    return len(ids)


def mentions_for_members(members: List[discord.Member]) -> str:
    if not members:
        return ""
    return "\n".join(m.mention for m in members)


async def build_online_embed(guild: discord.Guild) -> discord.Embed:
    online_ids = await get_online_members(guild)
    embed = discord.Embed(
        title="🟢 Штабной онлайн Foxhole",
        color=discord.Color.green(),
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )
    embed.description = (
        f"👥 **Всего в игре:** `{len(online_ids)}`\n"
        f"🕒 **Последнее обновление:** <t:{int(datetime.datetime.now().timestamp())}:R>\n"
        f"🔧 **Источник:** Discord Activity или Steam API"
    )

    used_members: Set[int] = set()
    any_group = False

    parents = OnlineGroup.select().where(
        (OnlineGroup.parent.is_null(True)) & (OnlineGroup.is_enabled == True)
    ).order_by(OnlineGroup.sort_order)

    for group in parents:
        direct = [m for m in group_direct_members(guild, group, online_ids) if m.id not in used_members]
        children = list(group.children.where(OnlineGroup.is_enabled == True).order_by(OnlineGroup.sort_order))

        child_blocks = []
        child_total_ids: Set[int] = set()
        for child in children:
            child_members = [m for m in group_direct_members(guild, child, online_ids) if m.id not in used_members]
            if not child_members:
                continue
            for m in child_members:
                child_total_ids.add(m.id)
            child_blocks.append((child, child_members))

        total = len({m.id for m in direct} | child_total_ids)
        if total <= 0:
            continue

        any_group = True
        used_members |= {m.id for m in direct}
        for _, child_members in child_blocks:
            used_members |= {m.id for m in child_members}

        lines = []
        if direct:
            lines.append(mentions_for_members(direct))
        for i, (child, child_members) in enumerate(child_blocks):
            branch = "└" if i == len(child_blocks) - 1 else "├"
            lines.append(f"{branch} **{group_label(child)} — {len(child_members)}**")
            for member in child_members:
                lines.append(f"│ {member.mention}" if branch == "├" else f"  {member.mention}")

        embed.add_field(
            name=f"{group_label(group)} — {total}",
            value="\n".join(lines)[:1024] if lines else "—",
            inline=False,
        )

    other_members = [m for m in guild.members if m.id in online_ids and m.id not in used_members]
    if other_members:
        embed.add_field(
            name=f"👤 Без группы — {len(other_members)}",
            value=mentions_for_members(other_members)[:1024],
            inline=False,
        )
        any_group = True

    if not any_group:
        embed.add_field(name="Онлайн", value="Сейчас никого не найдено в игре.", inline=False)

    embed.set_footer(text="Обновляется каждые 30 секунд | Управление доступно ONLINE_SETTER_ROLE_IDS")
    return embed


async def update_online_message(force_rename: bool = False) -> None:
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    channel_id = CHANNEL_CACHE.get("online", {}).get("channel_id")
    message_id = CHANNEL_CACHE.get("online", {}).get("message_id")
    if not channel_id:
        return

    channel = guild.get_channel(int(channel_id))
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        return

    embed = await build_online_embed(guild)
    view = OnlineView()

    message = None
    if message_id:
        try:
            message = await channel.fetch_message(int(message_id))
            await message.edit(embed=embed, view=view)
        except Exception:
            message = None

    if message is None:
        message = await channel.send(embed=embed, view=view)
        save_channel_config("online", int(channel.id), int(message.id))

    if isinstance(channel, discord.TextChannel):
        total = 0
        desc = embed.description or ""
        match = re.search(r"Всего в игре:\*\* `?(\d+)`?", desc)
        if match:
            total = int(match.group(1))
        now = datetime.datetime.now().timestamp()
        last = CHANNEL_CACHE.get("online", {}).get("last_rename_at") or 0
        if force_rename or now - float(last) >= CHANNEL_RENAME_COOLDOWN_SECONDS:
            try:
                new_name = f"🎮│foxhole-{total}"
                if channel.name != new_name:
                    await channel.edit(name=new_name, reason="Foxhole online count update")
                CHANNEL_CACHE["online"]["last_rename_at"] = now
            except Exception:
                logging.warning("Не удалось переименовать канал онлайна", exc_info=True)


# ============================================================
# VIEW / BUTTONS
# ============================================================
class OnlineView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Статистика онлайна", emoji="📊", style=discord.ButtonStyle.blurple, custom_id="rod_online_stats")
    async def stats(self, button: Button, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("Гильдия не найдена.", ephemeral=True)
        online_ids = await get_online_members(guild)
        linked = SteamLink.select().count()
        await interaction.response.send_message(
            f"📊 **Статистика**\n👥 В игре сейчас: **{len(online_ids)}**\n🔗 Steam привязано: **{linked}**",
            ephemeral=True,
        )

    @discord.ui.button(label="Не привязана Steam", emoji="🔗", style=discord.ButtonStyle.gray, custom_id="rod_online_unlinked")
    async def unlinked(self, button: Button, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("Гильдия не найдена.", ephemeral=True)
        members = [m for m in guild.members if not m.bot and not SteamLink.get_or_none(SteamLink.discord_id == m.id)]
        text = "\n".join(m.mention for m in members[:40]) or "Все найденные участники уже привязали Steam."
        if len(members) > 40:
            text += f"\n…и ещё {len(members) - 40}"
        await interaction.response.send_message(f"🔗 **Без привязки Steam:**\n{text}", ephemeral=True)

    @discord.ui.button(label="Настройка", emoji="⚙️", style=discord.ButtonStyle.gray, custom_id="rod_online_settings")
    async def settings(self, button: Button, interaction: discord.Interaction):
        member = interaction.user
        if not isinstance(member, discord.Member) or not has_any_role(member, ONLINE_SETTER_ROLE_IDS):
            return await interaction.response.send_message("Недостаточно прав.", ephemeral=True)
        await interaction.response.send_message(
            "⚙️ **Настройка онлайна**\n"
            "Используй команды:\n"
            "`/онлайн` — установить канал онлайна\n"
            "`/steam` — привязать SteamID64\n"
            "`/группа_добавить` — добавить группу\n"
            "`/подгруппа_добавить` — добавить подгруппу\n"
            "`/группа_роль_добавить` — добавить роль в группу\n"
            "`/группа_роль_удалить` — удалить роль из группы\n"
            "`/группа_порядок` — изменить порядок группы",
            ephemeral=True,
        )

    @discord.ui.button(label="Обновить", emoji="🔄", style=discord.ButtonStyle.green, custom_id="rod_online_update")
    async def refresh(self, button: Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await update_online_message(force_rename=True)
        await interaction.followup.send("✅ Онлайн обновлён.", ephemeral=True)


class DoneView(View):
    def __init__(self, kind: str):
        super().__init__(timeout=None)
        self.kind = kind

    @discord.ui.button(label="Выполнено", emoji="✅", style=discord.ButtonStyle.green, custom_id="rod_done_generic")
    async def done(self, button: Button, interaction: discord.Interaction):
        await interaction.response.send_message("✅ Отмечено как выполненное.", ephemeral=True)
        try:
            msg = interaction.message
            if msg and msg.embeds:
                embed = msg.embeds[0]
                embed.color = discord.Color.green()
                embed.add_field(name="Статус", value=f"✅ Выполнено: {interaction.user.mention}", inline=False)
                await msg.edit(embed=embed, view=None)
        except Exception:
            logging.exception("Done button error")


class MPFView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Взять заказ", emoji="🛠️", style=discord.ButtonStyle.blurple, custom_id="rod_mpf_claim")
    async def claim(self, button: Button, interaction: discord.Interaction):
        await interaction.response.send_message(f"🛠️ Заказ взял: {interaction.user.mention}", ephemeral=False)

    @discord.ui.button(label="Готово", emoji="✅", style=discord.ButtonStyle.green, custom_id="rod_mpf_done")
    async def done(self, button: Button, interaction: discord.Interaction):
        try:
            msg = interaction.message
            if msg and msg.embeds:
                embed = msg.embeds[0]
                embed.color = discord.Color.green()
                embed.add_field(name="Статус", value=f"✅ Выполнено: {interaction.user.mention}", inline=False)
                await msg.edit(embed=embed, view=None)
        finally:
            await interaction.response.send_message("✅ MPF-заказ закрыт.", ephemeral=True)


class ActivityView(View):
    def __init__(self, author_id: int = 0):
        super().__init__(timeout=None)
        self.author_id = author_id

    @discord.ui.button(label="Удалить активность", emoji="🗑️", style=discord.ButtonStyle.red, custom_id="rod_activity_delete")
    async def delete_activity(self, button: Button, interaction: discord.Interaction):
        member = interaction.user
        if isinstance(member, discord.Member):
            allowed = member.id == self.author_id or has_any_role(member, AKTIV_ROLE_IDS)
        else:
            allowed = False
        if not allowed:
            return await interaction.response.send_message("Недостаточно прав для удаления активности.", ephemeral=True)
        try:
            await interaction.message.delete()
        except Exception:
            pass
        await interaction.response.send_message("✅ Активность удалена.", ephemeral=True)


# ============================================================
# КОМАНДЫ НАСТРОЙКИ КАНАЛОВ
# ============================================================
@bot.slash_command(name="setsimpletimer", description="Установить канал для обычных таймеров")
async def setsimpletimer(ctx: discord.ApplicationContext, channel: discord.TextChannel):
    if not has_any_role(ctx.author, ALLOWED_ROLE_IDS):
        return await safe_respond(ctx, "❌ Недостаточно прав.")
    save_channel_config("simple", channel.id)
    await safe_respond(ctx, f"✅ Канал обычных таймеров установлен: {channel.mention}")


@bot.slash_command(name="setmpf", description="Установить канал для MPF")
async def setmpf(ctx: discord.ApplicationContext, channel: discord.TextChannel):
    if not has_any_role(ctx.author, ALLOWED_ROLE_IDS):
        return await safe_respond(ctx, "❌ Недостаточно прав.")
    save_channel_config("mpf", channel.id)
    await safe_respond(ctx, f"✅ Канал MPF установлен: {channel.mention}")


@bot.slash_command(name="setskladchannel", description="Установить канал склада")
async def setskladchannel(ctx: discord.ApplicationContext, channel: discord.TextChannel):
    if not has_any_role(ctx.author, ALLOWED_ROLE_IDS):
        return await safe_respond(ctx, "❌ Недостаточно прав.")
    save_channel_config("sklad", channel.id)
    await safe_respond(ctx, f"✅ Канал склада установлен: {channel.mention}")


@bot.slash_command(name="setaktivchat", description="Установить канал активностей")
async def setaktivchat(ctx: discord.ApplicationContext, channel: discord.TextChannel):
    if not has_any_role(ctx.author, ALLOWED_ROLE_IDS):
        return await safe_respond(ctx, "❌ Недостаточно прав.")
    save_channel_config("aktiv", channel.id)
    await safe_respond(ctx, f"✅ Канал активностей установлен: {channel.mention}")


# ============================================================
# ОСНОВНЫЕ КОМАНДЫ
# ============================================================
@bot.slash_command(name="таймер", description="Создать обычный таймер")
async def timer_command(
    ctx: discord.ApplicationContext,
    название: str,
    минуты: int,
    описание: str = "",
):
    channel = get_channel_from_cache("simple") or ctx.channel
    end_at = utcnow() + datetime.timedelta(minutes=max(1, минуты))
    embed = discord.Embed(title=f"⏱️ {название}", color=discord.Color.blurple())
    embed.add_field(name="Создал", value=ctx.author.mention, inline=True)
    embed.add_field(name="Окончание", value=f"<t:{int(end_at.timestamp())}:R>", inline=True)
    if описание:
        embed.add_field(name="Описание", value=описание[:1024], inline=False)
    message = await channel.send(embed=embed, view=DoneView("timer"))
    SimpleTimer.create(
        author_id=ctx.author.id,
        channel_id=channel.id,
        message_id=message.id,
        title=название,
        text=описание,
        end_at=end_at,
    )
    await safe_respond(ctx, f"✅ Таймер создан в {channel.mention}")


@bot.slash_command(name="склад", description="Создать запрос на склад")
async def sklad_command(
    ctx: discord.ApplicationContext,
    предмет: str,
    количество: str = "",
    комментарий: str = "",
):
    channel = get_channel_from_cache("sklad") or ctx.channel
    embed = discord.Embed(title="📦 Запрос на склад", color=discord.Color.orange())
    embed.add_field(name="Предмет", value=предмет, inline=False)
    if количество:
        embed.add_field(name="Количество", value=количество, inline=True)
    embed.add_field(name="Создал", value=ctx.author.mention, inline=True)
    if комментарий:
        embed.add_field(name="Комментарий", value=комментарий[:1024], inline=False)
    msg = await channel.send(embed=embed, view=DoneView("sklad"))
    SkladRequest.create(author_id=ctx.author.id, channel_id=channel.id, message_id=msg.id, item=предмет, amount=количество, comment=комментарий)
    await safe_respond(ctx, f"✅ Запрос на склад создан в {channel.mention}")


@bot.slash_command(name="мпф", description="Создать MPF-заказ")
async def mpf_command(
    ctx: discord.ApplicationContext,
    предмет: str,
    количество: str = "",
    комментарий: str = "",
):
    channel = get_channel_from_cache("mpf") or ctx.channel
    embed = discord.Embed(title="🏭 MPF-заказ", color=discord.Color.dark_teal())
    embed.add_field(name="Предмет", value=предмет, inline=False)
    if количество:
        embed.add_field(name="Количество", value=количество, inline=True)
    embed.add_field(name="Создал", value=ctx.author.mention, inline=True)
    if комментарий:
        embed.add_field(name="Комментарий", value=комментарий[:1024], inline=False)
    msg = await channel.send(embed=embed, view=MPFView())
    MPFOrder.create(author_id=ctx.author.id, channel_id=channel.id, message_id=msg.id, item=предмет, amount=количество, comment=комментарий)
    await safe_respond(ctx, f"✅ MPF-заказ создан в {channel.mention}")


@bot.slash_command(name="активность", description="Создать объявление активности")
async def activity_command(
    ctx: discord.ApplicationContext,
    название: str,
    место: str = "",
    нужно_людей: str = "",
    описание: str = "",
):
    channel = get_channel_from_cache("aktiv") or ctx.channel
    embed = discord.Embed(title=f"📣 Активность: {название}", color=discord.Color.red(), timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.add_field(name="Организатор", value=ctx.author.mention, inline=True)
    if место:
        embed.add_field(name="Место", value=место, inline=True)
    if нужно_людей:
        embed.add_field(name="Нужно людей", value=нужно_людей, inline=True)
    if описание:
        embed.add_field(name="Описание", value=описание[:1024], inline=False)
    if ctx.author.avatar:
        embed.set_thumbnail(url=ctx.author.avatar.url)
    await channel.send(content="@everyone", embed=embed, view=ActivityView(ctx.author.id), allowed_mentions=discord.AllowedMentions(everyone=True))
    await safe_respond(ctx, f"✅ Активность создана в {channel.mention}")


@bot.slash_command(name="steam", description="Привязать свой SteamID64 для проверки Foxhole через Steam API")
async def steam_command(ctx: discord.ApplicationContext, steamid64: str):
    if not is_steam_id64(steamid64):
        return await safe_respond(ctx, "❌ Нужен SteamID64 из 17 цифр.")
    obj, _ = SteamLink.get_or_create(discord_id=ctx.author.id)
    obj.steam_id = steamid64.strip()
    obj.updated_at = utcnow()
    obj.save()
    STEAM_CHECK_CACHE.pop(ctx.author.id, None)
    await safe_respond(ctx, "✅ SteamID64 привязан.")


@bot.slash_command(name="steam_remove", description="Удалить свою привязку Steam")
async def steam_remove_command(ctx: discord.ApplicationContext):
    deleted = SteamLink.delete().where(SteamLink.discord_id == ctx.author.id).execute()
    STEAM_CHECK_CACHE.pop(ctx.author.id, None)
    await safe_respond(ctx, "✅ Привязка Steam удалена." if deleted else "Steam-привязки не было.")


@bot.slash_command(name="онлайн", description="Установить этот канал как канал онлайна Foxhole")
async def online_command(ctx: discord.ApplicationContext, channel: Optional[discord.TextChannel] = None):
    if not has_any_role(ctx.author, ONLINE_SETTER_ROLE_IDS):
        return await safe_respond(ctx, "❌ У тебя нет прав на настройку онлайна.")
    target = channel or ctx.channel
    if not isinstance(target, discord.TextChannel):
        return await safe_respond(ctx, "❌ Нужно выбрать текстовый канал.")
    save_channel_config("online", target.id)
    await update_online_message(force_rename=True)
    await safe_respond(ctx, f"✅ Канал онлайна установлен: {target.mention}")


# ============================================================
# КОМАНДЫ УПРАВЛЕНИЯ ГРУППАМИ ОНЛАЙНА
# ============================================================
def get_group_by_title(title: str) -> Optional[OnlineGroup]:
    return OnlineGroup.get_or_none(fn.Lower(OnlineGroup.title) == title.lower())


@bot.slash_command(name="группа_добавить", description="Добавить основную группу онлайна")
async def group_add(ctx: discord.ApplicationContext, название: str, эмодзи: str = ""):
    if not has_any_role(ctx.author, ONLINE_SETTER_ROLE_IDS):
        return await safe_respond(ctx, "❌ Недостаточно прав.")
    order = (OnlineGroup.select(fn.MAX(OnlineGroup.sort_order)).where(OnlineGroup.parent.is_null(True)).scalar() or 0) + 1
    OnlineGroup.create(title=название, emoji=эмодзи, sort_order=order)
    await safe_respond(ctx, f"✅ Группа добавлена: {эмодзи} {название}")
    await update_online_message()


@bot.slash_command(name="подгруппа_добавить", description="Добавить подгруппу внутри основной группы")
async def subgroup_add(ctx: discord.ApplicationContext, родительская_группа: str, название: str, эмодзи: str = ""):
    if not has_any_role(ctx.author, ONLINE_SETTER_ROLE_IDS):
        return await safe_respond(ctx, "❌ Недостаточно прав.")
    parent = get_group_by_title(родительская_группа)
    if not parent:
        return await safe_respond(ctx, "❌ Родительская группа не найдена.")
    order = (OnlineGroup.select(fn.MAX(OnlineGroup.sort_order)).where(OnlineGroup.parent == parent).scalar() or 0) + 1
    OnlineGroup.create(title=название, emoji=эмодзи, sort_order=order, parent=parent)
    await safe_respond(ctx, f"✅ Подгруппа добавлена: {group_label(parent)} → {эмодзи} {название}")
    await update_online_message()


@bot.slash_command(name="группа_роль_добавить", description="Добавить Discord-роль в группу или подгруппу онлайна")
async def group_role_add(ctx: discord.ApplicationContext, группа: str, role: discord.Role):
    if not has_any_role(ctx.author, ONLINE_SETTER_ROLE_IDS):
        return await safe_respond(ctx, "❌ Недостаточно прав.")
    group = get_group_by_title(группа)
    if not group:
        return await safe_respond(ctx, "❌ Группа не найдена.")
    order = (OnlineGroupRole.select(fn.MAX(OnlineGroupRole.sort_order)).where(OnlineGroupRole.group == group).scalar() or 0) + 1
    OnlineGroupRole.get_or_create(group=group, role_id=role.id, defaults={"sort_order": order})
    await safe_respond(ctx, f"✅ Роль {role.mention} добавлена в `{group_label(group)}`")
    await update_online_message()


@bot.slash_command(name="группа_роль_удалить", description="Удалить Discord-роль из группы или подгруппы онлайна")
async def group_role_remove(ctx: discord.ApplicationContext, группа: str, role: discord.Role):
    if not has_any_role(ctx.author, ONLINE_SETTER_ROLE_IDS):
        return await safe_respond(ctx, "❌ Недостаточно прав.")
    group = get_group_by_title(группа)
    if not group:
        return await safe_respond(ctx, "❌ Группа не найдена.")
    deleted = OnlineGroupRole.delete().where((OnlineGroupRole.group == group) & (OnlineGroupRole.role_id == role.id)).execute()
    await safe_respond(ctx, "✅ Роль удалена из группы." if deleted else "❌ Такой роли в группе не было.")
    await update_online_message()


@bot.slash_command(name="группа_удалить", description="Удалить группу или подгруппу онлайна")
async def group_delete(ctx: discord.ApplicationContext, название: str):
    if not has_any_role(ctx.author, ONLINE_SETTER_ROLE_IDS):
        return await safe_respond(ctx, "❌ Недостаточно прав.")
    group = get_group_by_title(название)
    if not group:
        return await safe_respond(ctx, "❌ Группа не найдена.")
    label = group_label(group)
    group.delete_instance(recursive=True)
    await safe_respond(ctx, f"✅ Группа удалена: `{label}`")
    await update_online_message()


@bot.slash_command(name="группа_порядок", description="Изменить порядок отображения группы")
async def group_order(ctx: discord.ApplicationContext, название: str, порядок: int):
    if not has_any_role(ctx.author, ONLINE_SETTER_ROLE_IDS):
        return await safe_respond(ctx, "❌ Недостаточно прав.")
    group = get_group_by_title(название)
    if not group:
        return await safe_respond(ctx, "❌ Группа не найдена.")
    group.sort_order = max(1, порядок)
    group.save()
    await safe_respond(ctx, f"✅ Порядок группы `{group_label(group)}` изменён на `{group.sort_order}`")
    await update_online_message()


@bot.slash_command(name="группы_список", description="Показать список групп и ролей онлайна")
async def groups_list(ctx: discord.ApplicationContext):
    if not has_any_role(ctx.author, ONLINE_SETTER_ROLE_IDS):
        return await safe_respond(ctx, "❌ Недостаточно прав.")
    lines = []
    guild = ctx.guild
    parents = OnlineGroup.select().where(OnlineGroup.parent.is_null(True)).order_by(OnlineGroup.sort_order)
    for group in parents:
        roles = []
        for gr in group.roles.order_by(OnlineGroupRole.sort_order):
            role = guild.get_role(gr.role_id) if guild else None
            roles.append(role.mention if role else str(gr.role_id))
        lines.append(f"**{group.sort_order}. {group_label(group)}**" + (f" — {', '.join(roles)}" if roles else ""))
        for child in group.children.order_by(OnlineGroup.sort_order):
            child_roles = []
            for gr in child.roles.order_by(OnlineGroupRole.sort_order):
                role = guild.get_role(gr.role_id) if guild else None
                child_roles.append(role.mention if role else str(gr.role_id))
            lines.append(f"└ {child.sort_order}. {group_label(child)}" + (f" — {', '.join(child_roles)}" if child_roles else ""))
    text = "\n".join(lines) or "Группы не настроены."
    await safe_respond(ctx, text[:1900])


# ============================================================
# ФОНОВЫЕ ЗАДАЧИ
# ============================================================
@tasks.loop(seconds=ONLINE_UPDATE_SECONDS)
async def online_loop():
    try:
        await update_online_message()
    except Exception:
        logging.error("online_loop error:\n%s", traceback.format_exc())


@tasks.loop(seconds=30)
async def timer_loop():
    try:
        now = utcnow()
        for timer in SimpleTimer.select().where((SimpleTimer.is_done == False) & (SimpleTimer.end_at <= now)):
            channel = bot.get_channel(timer.channel_id)
            if channel:
                try:
                    await channel.send(f"⏰ Таймер **{timer.title}** закончился.")
                except Exception:
                    pass
            timer.is_done = True
            timer.save()
    except Exception:
        logging.error("timer_loop error:\n%s", traceback.format_exc())


# ============================================================
# STARTUP
# ============================================================
@bot.event
async def on_ready():
    logging.info("Logged in as %s", bot.user)
    db.connect(reuse_if_open=True)
    db.create_tables([
        ChannelConfig,
        SteamLink,
        OnlineGroup,
        OnlineGroupRole,
        SimpleTimer,
        MPFOrder,
        SkladRequest,
    ], safe=True)
    load_channel_cache()
    ensure_default_online_groups()

    bot.add_view(OnlineView())
    bot.add_view(DoneView("timer"))
    bot.add_view(MPFView())
    bot.add_view(ActivityView())

    if not online_loop.is_running():
        online_loop.start()
    if not timer_loop.is_running():
        timer_loop.start()

    try:
        await update_online_message(force_rename=True)
    except Exception:
        logging.warning("Initial online update failed", exc_info=True)


if __name__ == "__main__":
    if not DISCORD_BOT_TOKEN:
        raise RuntimeError("Не найден DISCORD_BOT_TOKEN в .env или переменных окружения.")
    bot.run(DISCORD_BOT_TOKEN)
