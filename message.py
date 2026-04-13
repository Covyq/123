import os
import datetime
from peewee import *
from discord import (
    Bot, Intents, ApplicationContext, Option, SlashCommandOptionType,
    Interaction, InteractionType, ButtonStyle, ChannelType, HTTPException
)
from discord.ui import View, Button
from discord.ext import tasks
from discord.errors import NotFound, Forbidden

# ─── НАСТРОЙКИ ─────────────────────────────────────────────
GUILD_ID = 419565206335651840
allowed_role_id = 1493199914572972032

bot = Bot(
    intents=Intents.all(),
    debug_guilds=[GUILD_ID]  # 🔥 важно для slash-команд
)

db = SqliteDatabase('TimerDataBase.db')

# ─── БАЗА ДАННЫХ ───────────────────────────────────────────
class BaseModel(Model):
    guild_id = BigIntegerField()

    class Meta:
        database = db

class SkladTimer(BaseModel):
    channel_id = BigIntegerField()
    message_id = BigIntegerField()
    text = TextField()
    time_end = BigIntegerField()

class SimpleTimer(BaseModel):
    channel_id = BigIntegerField()
    message_id = BigIntegerField()
    text = TextField()
    time_end = BigIntegerField()
    author_name = TextField()
    created_at = BigIntegerField()

class SkladChannel(BaseModel):
    channel_id = BigIntegerField()

class SimpleChannel(BaseModel):
    channel_id = BigIntegerField()

db.create_tables([SkladTimer, SimpleTimer, SkladChannel, SimpleChannel])

# ─── УТИЛИТЫ ───────────────────────────────────────────────
def has_access(member):
    return (
        member.guild_permissions.administrator or
        any(r.id == allowed_role_id for r in member.roles)
    )

def format_sklad_text(author, hex_val, region, warehouse, password):
    return (
        f"👤 {author}\n"
        f"**Гекс:** {hex_val}\n"
        f"**Регион:** {region}\n"
        f"**Склад:** {warehouse}\n"
        f"**Пароль:** {password}"
    )

def get_channel(table, guild_id):
    row = table.get_or_none(table.guild_id == guild_id)
    return row.channel_id if row else None

def set_channel(table, guild_id, channel_id):
    row = table.get_or_none(table.guild_id == guild_id)
    if row:
        row.channel_id = channel_id
        row.save()
    else:
        table.create(guild_id=guild_id, channel_id=channel_id)

# ─── LOOP ─────────────────────────────────────────────────
@tasks.loop(seconds=6)
async def timer_loop():
    now = int(datetime.datetime.utcnow().timestamp())

    sklad = list(SkladTimer.select().where(SkladTimer.time_end < now))
    simple = list(SimpleTimer.select().where(SimpleTimer.time_end < now))

    for t in sklad:
        guild = bot.get_guild(t.guild_id)
        if not guild:
            continue

        channel = guild.get_channel(t.channel_id)
        if not channel:
            continue

        try:
            msg = await channel.fetch_message(t.message_id)
            await msg.delete()
        except (NotFound, Forbidden, HTTPException):
            pass

        t.delete_instance()

    for t in simple:
        guild = bot.get_guild(t.guild_id)
        if not guild:
            continue

        channel = guild.get_channel(t.channel_id)
        if not channel:
            continue

        try:
            msg = await channel.fetch_message(t.message_id)
            header = f"👤 {t.author_name} · <t:{t.created_at}:t>"
            await msg.edit(content=f"✅ {header}\n{t.text}\n✅ Готово")
        except (NotFound, Forbidden, HTTPException):
            pass

        t.delete_instance()

# ─── READY ────────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ Бот {bot.user} запущен")

    if not timer_loop.is_running():
        timer_loop.start()

    try:
        await bot.sync_commands(guild_ids=[GUILD_ID])
        print("✅ Slash-команды готовы")
    except Exception as e:
        print(f"❌ Ошибка sync: {e}")

# ─── КНОПКИ ───────────────────────────────────────────────
@bot.event
async def on_interaction(interaction: Interaction):
    if interaction.type == InteractionType.component:
        if interaction.data["custom_id"] == "update_timer":
            await interaction.response.send_message(
                "🔄 Пока не реализовано",
                ephemeral=True
            )

# ─── КОМАНДЫ ──────────────────────────────────────────────

@bot.slash_command(name="setskladchannel", guild_ids=[GUILD_ID])
async def setskladchannel(ctx: ApplicationContext,
    channel: Option(SlashCommandOptionType.channel, channel_types=[ChannelType.text])):

    if not has_access(ctx.author):
        await ctx.respond("❌ Нет прав", ephemeral=True)
        return

    set_channel(SkladChannel, ctx.guild.id, channel.id)
    await ctx.respond(f"✅ Канал склада: {channel.mention}", ephemeral=True)


@bot.slash_command(name="setsimplechannel", guild_ids=[GUILD_ID])
async def setsimplechannel(ctx: ApplicationContext,
    channel: Option(SlashCommandOptionType.channel, channel_types=[ChannelType.text])):

    if not has_access(ctx.author):
        await ctx.respond("❌ Нет прав", ephemeral=True)
        return

    set_channel(SimpleChannel, ctx.guild.id, channel.id)
    await ctx.respond(f"✅ Канал таймеров: {channel.mention}", ephemeral=True)


@bot.slash_command(name="склад", guild_ids=[GUILD_ID])
async def sklad(ctx: ApplicationContext,
    hex_val: Option(str, name="гекс"),
    region: Option(str, name="регион"),
    warehouse: Option(str, name="склад"),
    password: Option(str, name="пароль")):

    channel_id = get_channel(SkladChannel, ctx.guild.id)
    if channel_id and ctx.channel.id != channel_id:
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
    view.add_item(Button(label="Обновить", custom_id="update_timer"))

    msg = await ctx.send(f"{text}\n⏰ — <t:{time_end}:R>", view=view)

    SkladTimer.create(
        guild_id=ctx.guild.id,
        channel_id=ctx.channel.id,
        message_id=msg.id,
        text=text,
        time_end=time_end
    )

    await ctx.respond("✅ Таймер создан", ephemeral=True)


@bot.slash_command(name="таймер", guild_ids=[GUILD_ID])
async def timer(ctx: ApplicationContext,
    text: Option(str, name="текст"),
    days: Option(int, default=0),
    hours: Option(int, default=0),
    minutes: Option(int, default=0),
    seconds: Option(int, default=0)):

    channel_id = get_channel(SimpleChannel, ctx.guild.id)
    if channel_id and ctx.channel.id != channel_id:
        await ctx.respond("❌ Не тот канал", ephemeral=True)
        return

    now = datetime.datetime.utcnow()
    created = int(now.timestamp())
    time_end = int((now + datetime.timedelta(
        days=days, hours=hours, minutes=minutes, seconds=seconds
    )).timestamp())

    msg = await ctx.send(
        f"⏳ 👤 {ctx.author.display_name}\n{text}\n⏰ — <t:{time_end}:R>"
    )

    SimpleTimer.create(
        guild_id=ctx.guild.id,
        channel_id=ctx.channel.id,
        message_id=msg.id,
        text=text,
        time_end=time_end,
        author_name=ctx.author.display_name,
        created_at=created
    )

    await ctx.respond("✅ Таймер создан", ephemeral=True)

# ─── ЗАПУСК ───────────────────────────────────────────────
bot.run(os.environ.get("DISCORD_BOT_TOKEN"))
