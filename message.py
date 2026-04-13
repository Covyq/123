import os
import asyncio
import datetime
from peewee import *
from discord import (
    Bot, Intents, ApplicationContext, Option, SlashCommandOptionType,
    Interaction, InteractionType, ButtonStyle, HTTPException, ChannelType
)
from discord.ui import View, Button
from discord.ext import tasks
from discord.errors import NotFound, Forbidden

# ─── Настройки ────────────────────────────────────────────────
db = SqliteDatabase('TimerDataBase.db')
DSBot = Bot(intents=Intents.all())
GUILD_ID = 419565206335651840

allowed_role_id = 1493199914572972032

# ─── Модели ─────────────────────────────────────────────────
class TableBase(Model):
    guild_id = BigIntegerField()
    class Meta:
        database = db

class Table_SkladTimer(TableBase):
    channel_id = BigIntegerField()
    message_id = BigIntegerField()
    text = TextField()
    time_shift = BigIntegerField()
    time_end = BigIntegerField()

class Table_SimpleTimer(TableBase):
    channel_id = BigIntegerField()
    message_id = BigIntegerField()
    text = TextField()
    time_end = BigIntegerField()
    author_name = TextField(default="")
    created_at = BigIntegerField(default=0)

class Table_SkladChannel(TableBase):
    channel_id = BigIntegerField()

class Table_SimpleChannel(TableBase):
    channel_id = BigIntegerField()

db.create_tables([
    Table_SkladTimer,
    Table_SimpleTimer,
    Table_SkladChannel,
    Table_SimpleChannel
])

# ─── Вспомогательные функции ───────────────────────────────
def has_access(member):
    return (
        member.guild_permissions.administrator or
        any(r.id == allowed_role_id for r in member.roles)
    )

def get_channel_id(table, guild_id: int):
    row = table.get_or_none(table.guild_id == guild_id)
    return row.channel_id if row else None

def set_channel_id(table, guild_id: int, channel_id: int):
    row = table.get_or_none(table.guild_id == guild_id)
    if row:
        row.channel_id = channel_id
        row.save()
    else:
        table.create(guild_id=guild_id, channel_id=channel_id)

def format_sklad_text(author, hex_val, region, warehouse, password):
    return (
        f"👤 {author}\n"
        f"**Гекс:** {hex_val}\n"
        f"**Регион:** {region}\n"
        f"**Склад:** {warehouse}\n"
        f"**Пароль:** {password}"
    )

# ─── Фоновый цикл ──────────────────────────────────────────
@tasks.loop(seconds=6)
async def timer_loop():
    now = int(datetime.datetime.utcnow().timestamp())

    with db:
        sklad_timers = list(Table_SkladTimer.select().where(Table_SkladTimer.time_end < now))
        simple_timers = list(Table_SimpleTimer.select().where(Table_SimpleTimer.time_end < now))

    for timer in sklad_timers:
        guild = DSBot.get_guild(timer.guild_id)
        if not guild:
            continue

        channel = guild.get_channel(timer.channel_id)
        if not channel:
            continue

        try:
            msg = await channel.fetch_message(timer.message_id)
            await msg.delete()
        except (HTTPException, NotFound, Forbidden):
            pass

        timer.delete_instance()

    for timer in simple_timers:
        guild = DSBot.get_guild(timer.guild_id)
        if not guild:
            continue

        channel = guild.get_channel(timer.channel_id)
        if not channel:
            continue

        try:
            msg = await channel.fetch_message(timer.message_id)
            header = f"👤 {timer.author_name} · <t:{timer.created_at}:t>"
            await msg.edit(content=f"✅ {header}\n{timer.text}\n✅ Готово")
        except (HTTPException, NotFound, Forbidden):
            pass

        timer.delete_instance()

# ─── READY ───────────────────────────────────────────────
@DSBot.event
async def on_ready():
    print(f"Бот {DSBot.user} запущен!")

    if not timer_loop.is_running():
        timer_loop.start()

    try:
        await DSBot.sync_commands()
        print("✅ Slash-команды синхронизированы")
    except Exception as e:
        print(f"❌ Ошибка sync: {e}")

# ─── КНОПКА ───────────────────────────────────────────────
@DSBot.event
async def on_interaction(interaction: Interaction):
    if interaction.type == InteractionType.component:
        if interaction.data["custom_id"] == "update_sklad_timer":
            await interaction.response.send_message(
                "🔄 Обновление таймера пока не реализовано",
                ephemeral=True
            )

# ─── КОМАНДЫ ───────────────────────────────────────────────

@DSBot.slash_command(name="setskladchannel", guild_ids=[GUILD_ID])
async def setskladchannel(ctx: ApplicationContext,
    channel: Option(SlashCommandOptionType.channel, channel_types=[ChannelType.text])):

    if not has_access(ctx.author):
        await ctx.respond("❌ Нет прав", ephemeral=True)
        return

    set_channel_id(Table_SkladChannel, ctx.guild.id, channel.id)
    await ctx.respond(f"✅ Канал склада: {channel.mention}", ephemeral=True)


@DSBot.slash_command(name="setsimplechannel", guild_ids=[GUILD_ID])
async def setsimplechannel(ctx: ApplicationContext,
    channel: Option(SlashCommandOptionType.channel, channel_types=[ChannelType.text])):

    if not has_access(ctx.author):
        await ctx.respond("❌ Нет прав", ephemeral=True)
        return

    set_channel_id(Table_SimpleChannel, ctx.guild.id, channel.id)
    await ctx.respond(f"✅ Канал таймеров: {channel.mention}", ephemeral=True)


@DSBot.slash_command(name="склад", guild_ids=[GUILD_ID])
async def sklad(ctx: ApplicationContext,
    hex_val: Option(str, name="гекс"),
    region: Option(str, name="регион"),
    warehouse: Option(str, name="склад"),
    password: Option(str, name="пароль")):

    sklad_channel_id = get_channel_id(Table_SkladChannel, ctx.guild.id)
    if sklad_channel_id and ctx.channel.id != sklad_channel_id:
        await ctx.respond("❌ Не тот канал", ephemeral=True)
        return

    now = datetime.datetime.utcnow()
    time_end = int((now + datetime.timedelta(days=2, hours=1)).timestamp())

    text = format_sklad_text(
        ctx.author.display_name,
        hex_val,
        region,
        warehouse,
        password
    )

    view = View()
    view.add_item(Button(
        label="Обновить таймер",
        style=ButtonStyle.grey,
        custom_id="update_sklad_timer"
    ))

    msg = await ctx.send(f"{text}\n⏰ — <t:{time_end}:R>", view=view)

    Table_SkladTimer.create(
        guild_id=ctx.guild.id,
        channel_id=ctx.channel.id,
        message_id=msg.id,
        text=text,
        time_shift=2*24*3600 + 3600,
        time_end=time_end
    )

    await ctx.respond("✅ Таймер создан!", ephemeral=True)


@DSBot.slash_command(name="таймер", guild_ids=[GUILD_ID])
async def timer(ctx: ApplicationContext,
    text: Option(str, name="текст"),
    days: Option(int, default=0),
    hours: Option(int, default=0),
    minutes: Option(int, default=0),
    seconds: Option(int, default=0)):

    simple_channel_id = get_channel_id(Table_SimpleChannel, ctx.guild.id)
    if simple_channel_id and ctx.channel.id != simple_channel_id:
        await ctx.respond("❌ Не тот канал", ephemeral=True)
        return

    now = datetime.datetime.utcnow()
    created = int(now.timestamp())
    time_end = int((now + datetime.timedelta(
        days=days, hours=hours, minutes=minutes, seconds=seconds
    )).timestamp())

    header = f"👤 {ctx.author.display_name} · <t:{created}:t>"

    msg = await ctx.send(f"⏳ {header}\n{text}\n⏰ — <t:{time_end}:R>")

    Table_SimpleTimer.create(
        guild_id=ctx.guild.id,
        channel_id=ctx.channel.id,
        message_id=msg.id,
        text=text,
        time_end=time_end,
        author_name=ctx.author.display_name,
        created_at=created
    )

    await ctx.respond("✅ Таймер создан!", ephemeral=True)

# ─── ЗАПУСК ───────────────────────────────────────────────
token = os.environ.get("DISCORD_BOT_TOKEN")
DSBot.run(token)
