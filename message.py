import os
import asyncio
import datetime
from peewee import *
from discord import (
    Bot, Intents, ApplicationContext, Option, SlashCommandOptionType, Interaction, InteractionType, ButtonStyle, Guild, Message, TextChannel, HTTPException, ChannelType
)
from discord.ui import View, Button

db = SqliteDatabase('TimerDataBase.db')
DSBot = Bot(intents=Intents.all())
GUILD_ID = 1278259070666801214

# ─── Модели ─────────────────────────────────────────────────────────────────── 
class TableBase(Model):
    guild_id = BigIntegerField()
    
    class Meta:
        database = db

class Table_SkladTimer(TableBase):
    """Таймеры склада — при истечении сообщение удаляется"""
    channel_id = BigIntegerField()
    message_id = BigIntegerField()
    text = TextField()
    time_shift = BigIntegerField()
    time_end = BigIntegerField()
    author_id = BigIntegerField(default=0)
    author_name = TextField(default="")
    created_at = BigIntegerField(default=0)

class Table_SimpleTimer(TableBase):
    """Обычные таймеры — при истечении показывают 'Готово'"""
    channel_id = BigIntegerField()
    message_id = BigIntegerField()
    text = TextField()
    time_end = BigIntegerField()
    author_id = BigIntegerField(default=0)
    author_name = TextField(default="")
    created_at = BigIntegerField(default=0)

class Table_SkladChannel(TableBase):
    channel_id = BigIntegerField()

class Table_SimpleChannel(TableBase):
    channel_id = BigIntegerField()

db.create_tables([Table_SkladTimer, Table_SimpleTimer, Table_SkladChannel, Table_SimpleChannel])

with db:
    for col in ("author_id", "created_at", "author_name"):
        try:
            db.execute_sql(f"ALTER TABLE skladtimer ADD COLUMN {col} BIGINT DEFAULT 0" if col != "author_name" else f"ALTER TABLE skladtimer ADD COLUMN {col} TEXT DEFAULT ''")
        except Exception:
            pass
    for col in ("author_id", "created_at"):
        try:
            db.execute_sql(f"ALTER TABLE simpletimer ADD COLUMN {col} BIGINT DEFAULT 0")
        except Exception:
            pass
    try:
        db.execute_sql("ALTER TABLE simpletimer ADD COLUMN author_name TEXT DEFAULT ''")
    except Exception:
        pass

# ─── Вспомогательные функции ────────────────────────────────────────────────── 
def get_channel_id(table, guild_id: int) -> int | None:
    try:
        return table.get(table.guild_id == guild_id).channel_id
    except table.DoesNotExist:
        return None

def set_channel_id(table, guild_id: int, channel_id: int):
    existing = table.get_or_none(table.guild_id == guild_id)
    if existing:
        existing.channel_id = channel_id
        existing.save()
    else:
        table.create(guild_id=guild_id, channel_id=channel_id)

def format_sklad_text(hex_val: str, region: str, warehouse: str, password: str) -> str:
    return (
        f"**Гекс:** {hex_val}\n"
        f"**Регион:** {region}\n"
        f"**Склад:** {warehouse}\n"
        f"**Пароль:** {password}"
    )

# ─── Фоновый цикл ──────────────────────────────────────────────────────────── 
@DSBot.event
async def on_ready():
    print(f"Бот {DSBot.user} запущен и готов к работе!")
    while True:
        now = int(datetime.datetime.now().timestamp())
        with db:
            for timer in Table_SkladTimer.select().where(Table_SkladTimer.time_end < now):
                guild: Guild = DSBot.get_guild(timer.guild_id)
                if guild:
                    channel: TextChannel = guild.get_channel(timer.channel_id)
                    if channel:
                        try:
                            msg: Message = await channel.fetch_message(timer.message_id)
                            await msg.delete()
                        except HTTPException:
                            print(f"[Склад] Ошибка удаления {timer.message_id}")
                        timer.delete_instance()
        
        with db:
            for timer in Table_SimpleTimer.select().where(Table_SimpleTimer.time_end < now):
                guild: Guild = DSBot.get_guild(timer.guild_id)
                if guild:
                    channel: TextChannel = guild.get_channel(timer.channel_id)
                    if channel:
                        try:
                            msg: Message = await channel.fetch_message(timer.message_id)
                            name = (timer.author_name or f"<@{timer.author_id}>") if timer.author_id else ""
                            header = f"👤 {name} · создан в <t:{timer.created_at}:t>" if name else ""
                            content = f"{header}\n{timer.text}\n✅ **Готово**".strip()
                            await msg.edit(content=content, view=None)
                        except HTTPException:
                            print(f"[Таймер] Ошибка редактирования {timer.message_id}")
                        timer.delete_instance()
        
        await asyncio.sleep(6)

# ─── Команды настройки каналов ──────────────────────────────────────────────── 
@DSBot.slash_command(name="setskladchannel", guild_ids=[GUILD_ID], description="Установить канал для таймеров склада (только администраторы)")
async def setskladchannel_command(ctx: ApplicationContext, channel: Option(SlashCommandOptionType.channel, description="Канал", channel_types=[ChannelType.text])):
    if not ctx.author.guild_permissions.administrator:
        await ctx.respond("❌ Только для администраторов.", ephemeral=True)
        return
    with db:
        set_channel_id(Table_SkladChannel, ctx.guild.id, channel.id)
    await ctx.respond(f"✅ Канал склада: {channel.mention}", ephemeral=True)

@DSBot.slash_command(name="setsimplechannel", guild_ids=[GUILD_ID], description="Установить канал для обычных таймеров (только администраторы)")
async def setsimplechannel_command(ctx: ApplicationContext, channel: Option(SlashCommandOptionType.channel, description="Канал", channel_types=[ChannelType.text])):
    if not ctx.author.guild_permissions.administrator:
        await ctx.respond("❌ Только для администраторов.", ephemeral=True)
        return
    with db:
        set_channel_id(Table_SimpleChannel, ctx.guild.id, channel.id)
    await ctx.respond(f"✅ Канал обычных таймеров: {channel.mention}", ephemeral=True)

@DSBot.slash_command(name="channels", guild_ids=[GUILD_ID], description="Показать каналы бота")
async def channels_command(ctx: ApplicationContext):
    sklad_id = get_channel_id(Table_SkladChannel, ctx.guild.id)
    simple_id = get_channel_id(Table_SimpleChannel, ctx.guild.id)
    sklad_m = ctx.guild.get_channel(sklad_id).mention if sklad_id and ctx.guild.get_channel(sklad_id) else "не задан"
    simple_m = ctx.guild.get_channel(simple_id).mention if simple_id and ctx.guild.get_channel(simple_id) else "не задан"
    await ctx.respond(
        f"📋 **Каналы бота:**\n🏭 Склад: {sklad_m}\n🕐 Таймеры: {simple_m}",
        ephemeral=True
    )

# ─── /склад ─────────────────────────────────────────────────────────────────── 
@DSBot.slash_command(name="склад", guild_ids=[GUILD_ID], description="Создать таймер склада")
async def timer_command(ctx: ApplicationContext, 
    hex_val: Option(SlashCommandOptionType.string, name="гекс", description="Гекс"), 
    region: Option(SlashCommandOptionType.string, name="регион", description="Регион"), 
    warehouse: Option(SlashCommandOptionType.string, name="склад", description="Склад"), 
    password: Option(SlashCommandOptionType.string, name="пароль", description="Пароль")):
    sklad_channel_id = get_channel_id(Table_SkladChannel, ctx.guild.id)
    if sklad_channel_id and ctx.channel.id != sklad_channel_id:
        ch = ctx.guild.get_channel(sklad_channel_id)
        mention = ch.mention if ch else f"<#{sklad_channel_id}>"
        await ctx.respond(f"❌ Только в канале {mention}.", ephemeral=True)
        return
    
    current_time = datetime.datetime.now()
    created_at = int(current_time.timestamp())
    time_end = int((current_time + datetime.timedelta(days=2, hours=1)).timestamp())
    text = format_sklad_text(hex_val, region, warehouse, password)
    
    header = f"👤 {ctx.author.display_name} · создан в <t:{created_at}:t>"
    
    view = View()
    view.add_item(Button(label="Обновить таймер", style=ButtonStyle.grey, custom_id="update_sklad_timer"))
    timer_message = await ctx.send(f"{header}\n{text}\n⏰ — <t:{time_end}:R>", view=view)
    
    with db:
        Table_SkladTimer.create(guild_id=ctx.guild.id, channel_id=ctx.channel.id, message_id=timer_message.id, 
            text=text, time_shift=time_end - int(current_time.timestamp()), time_end=time_end,
            author_id=ctx.author.id, author_name=ctx.author.display_name, created_at=created_at)
    
    await ctx.respond("✅ Таймер склада установлен на 2 дня и 1 час!", ephemeral=True)

# ─── /таймер ───────────────────────────────────────────────────────────────── 
@DSBot.slash_command(name="таймер", guild_ids=[GUILD_ID], description="Создать обычный таймер")
async def simpletimer_command(ctx: ApplicationContext, 
    text: Option(SlashCommandOptionType.string, name="текст", description="Текст таймера"), 
    days: Option(SlashCommandOptionType.integer, default=0, description="Время в днях"), 
    hours: Option(SlashCommandOptionType.integer, default=0, description="Время в часах"), 
    minutes: Option(SlashCommandOptionType.integer, default=1, description="Время в минутах")):
    simple_channel_id = get_channel_id(Table_SimpleChannel, ctx.guild.id)
    if simple_channel_id and ctx.channel.id != simple_channel_id:
        ch = ctx.guild.get_channel(simple_channel_id)
        mention = ch.mention if ch else f"<#{simple_channel_id}>"
        await ctx.respond(f"❌ Только в канале {mention}.", ephemeral=True)
        return
    
    current_time = datetime.datetime.now()
    created_at = int(current_time.timestamp())
    time_end = int((current_time + datetime.timedelta(
        days=days or 0, hours=hours or 0, minutes=minutes or 0)).timestamp())
    header = f"👤 {ctx.author.display_name} · создан в <t:{created_at}:t>"
    timer_message = await ctx.send(f"{header}\n{text}\n⏰ — <t:{time_end}:R>")
    
    with db:
        Table_SimpleTimer.create(guild_id=ctx.guild.id, channel_id=ctx.channel.id, message_id=timer_message.id, 
            text=text, time_end=time_end, author_id=ctx.author.id, 
            author_name=ctx.author.display_name, created_at=created_at)
    
    await ctx.respond("✅ Таймер установлен!", ephemeral=True)

# ─── Кнопка обновления ─────────────────────────────────────────────────────── 
async def on_button_clicked(interaction: Interaction):
    if interaction.type == InteractionType.component:
        if interaction.data.get("custom_id") == "update_sklad_timer":
            try:
                timer = Table_SkladTimer.get(
                    Table_SkladTimer.guild_id == interaction.guild.id,
                    Table_SkladTimer.channel_id == interaction.channel.id,
                    Table_SkladTimer.message_id == interaction.message.id)
                new_end = int(datetime.datetime.now().timestamp()) + timer.time_shift
                timer.time_end = new_end
                timer.save()
                await interaction.message.edit(content=f"{timer.text}\n⏰ — <t:{new_end}:R>")
                await interaction.response.send_message("✅ Таймер обновлён!", ephemeral=True)
            except Table_SkladTimer.DoesNotExist:
                await interaction.response.send_message("⚠️ Таймер не найден.", ephemeral=True)

DSBot.add_listener(func=on_button_clicked, name="on_interaction")

# ─── Очистка при ручном удалении ───────────────────────────────────────────── 
@DSBot.event
async def on_message_delete(message: Message):
    with db:
        for table in [Table_SkladTimer, Table_SimpleTimer]:
            try:
                table.get(table.guild_id == message.guild.id, 
                    table.channel_id == message.channel.id, 
                    table.message_id == message.id).delete_instance()
            except table.DoesNotExist:
                pass

# ─── Запуск ─────────────────────────────────────────────────────────────────── 
token = os.environ.get("DISCORD_BOT_TOKEN")
if not token:
    print("ОШИБКА: Токен не найден!")
    exit(1)

DSBot.run(token)

DSBot.run(token)
