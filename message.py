import os
import asyncio
import datetime
from peewee import *
from discord import (
    Bot, Intents, ApplicationContext, Option, SlashCommandOptionType, Interaction, InteractionType, ButtonStyle, Guild, Message, TextChannel, HTTPException, ChannelType
)
from discord.ui import View, Button, Select

db = SqliteDatabase('TimerDataBase.db')
DSBot = Bot(intents=Intents.all())
GUILD_ID = 1278259070666801214
HEX_MANAGER_ROLE = "HexManager"  # Измени на название роли

class TableBase(Model):
    guild_id = BigIntegerField()
    class Meta:
        database = db

class Table_Hex(TableBase):
    """Хранит список гексов"""
    hex_value = TextField(unique=True)
    region = TextField()

class Table_SkladTimer(TableBase):
    channel_id = BigIntegerField()
    message_id = BigIntegerField()
    text = TextField()
    time_shift = BigIntegerField()
    time_end = BigIntegerField()
    author_id = BigIntegerField(default=0)
    author_name = TextField(default="")
    created_at = BigIntegerField(default=0)
    hex_id = BigIntegerField(default=0)

class Table_SimpleTimer(TableBase):
    channel_id = BigIntegerField()
    message_id = BigIntegerField()
    text = TextField()
    time_end = BigIntegerField()
    author_id = BigIntegerField(default=0)
    author_name = TextField(default="")
    created_at = BigIntegerField(default=0)

class Table_SkladChannel(TableBase):
    """Каналы для каждого гекса"""
    hex_id = BigIntegerField()
    channel_id = BigIntegerField()

class Table_SimpleChannel(TableBase):
    channel_id = BigIntegerField()

db.create_tables([Table_Hex, Table_SkladTimer, Table_SimpleTimer, Table_SkladChannel, Table_SimpleChannel])

with db:
    try:
        db.execute_sql("ALTER TABLE skladtimer ADD COLUMN hex_id BIGINT DEFAULT 0")
    except Exception:
        pass

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

def has_hex_manager_role(ctx: ApplicationContext) -> bool:
    """Проверяет, есть ли у пользователя роль HexManager"""
    return any(role.name == HEX_MANAGER_ROLE for role in ctx.author.roles)

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

@DSBot.slash_command(name="addhex", guild_ids=[GUILD_ID], description="Добавить новый гекс (только HexManager)")
async def addhex_command(ctx: ApplicationContext,
    hex_val: Option(SlashCommandOptionType.string, name="гекс", description="Значение гекса"),
    region: Option(SlashCommandOptionType.string, name="регион", description="Регион")):
    if not has_hex_manager_role(ctx):
        await ctx.respond("❌ Только для HexManager.", ephemeral=True)
        return
    
    with db:
        existing = Table_Hex.get_or_none(Table_Hex.hex_value == hex_val)
        if existing:
            await ctx.respond(f"❌ Гекс `{hex_val}` уже существует!", ephemeral=True)
            return
        
        Table_Hex.create(guild_id=ctx.guild.id, hex_value=hex_val, region=region)
    
    await ctx.respond(f"✅ Гекс `{hex_val}` ({region}) добавлен!", ephemeral=True)

@DSBot.slash_command(name="listhexes", guild_ids=[GUILD_ID], description="Показать все гексы")
async def listhexes_command(ctx: ApplicationContext):
    with db:
        hexes = Table_Hex.select().where(Table_Hex.guild_id == ctx.guild.id)
    
    if not hexes:
        await ctx.respond("❌ Гексы не добавлены.", ephemeral=True)
        return
    
    hex_list = "\n".join([f"• `{h.hex_value}` — {h.region}" for h in hexes])
    await ctx.respond(f"📋 **Гексы:**\n{hex_list}", ephemeral=True)

@DSBot.slash_command(name="setskladchannel", guild_ids=[GUILD_ID], description="Установить канал для таймеров склада по гексу")
async def setskladchannel_command(ctx: ApplicationContext,
    hex_val: Option(SlashCommandOptionType.string, name="гекс", description="Гекс"),
    channel: Option(SlashCommandOptionType.channel, description="Канал", channel_types=[ChannelType.text])):
    if not ctx.author.guild_permissions.administrator:
        await ctx.respond("❌ Только для администраторов.", ephemeral=True)
        return
    
    with db:
        hex_obj = Table_Hex.get_or_none(Table_Hex.hex_value == hex_val, Table_Hex.guild_id == ctx.guild.id)
        if not hex_obj:
            await ctx.respond(f"❌ Гекс `{hex_val}` не найден!", ephemeral=True)
            return
        
        existing = Table_SkladChannel.get_or_none(Table_SkladChannel.hex_id == hex_obj.id)
        if existing:
            existing.channel_id = channel.id
            existing.save()
        else:
            Table_SkladChannel.create(guild_id=ctx.guild.id, hex_id=hex_obj.id, channel_id=channel.id)
    
    await ctx.respond(f"✅ Канал для гекса `{hex_val}`: {channel.mention}", ephemeral=True)

@DSBot.slash_command(name="setsimplechannel", guild_ids=[GUILD_ID], description="Установить канал для обычных таймеров")
async def setsimplechannel_command(ctx: ApplicationContext, channel: Option(SlashCommandOptionType.channel, description="Канал", channel_types=[ChannelType.text])):
    if not ctx.author.guild_permissions.administrator:
        await ctx.respond("❌ Только для администраторов.", ephemeral=True)
        return
    with db:
        set_channel_id(Table_SimpleChannel, ctx.guild.id, channel.id)
    await ctx.respond(f"✅ Канал обычных таймеров: {channel.mention}", ephemeral=True)

@DSBot.slash_command(name="склад", guild_ids=[GUILD_ID], description="Создать таймер склада")
async def timer_command(ctx: ApplicationContext,
    warehouse: Option(SlashCommandOptionType.string, name="склад", description="Склад"),
    password: Option(SlashCommandOptionType.string, name="пароль", description="Пароль")):
    
    with db:
        hexes = list(Table_Hex.select().where(Table_Hex.guild_id == ctx.guild.id))
    
    if not hexes:
        await ctx.respond("❌ Гексы не добавлены администратором.", ephemeral=True)
        return
    
    class HexSelect(Select):
        async def callback(self, interaction: Interaction):
            selected_hex_id = int(self.values[0])
            with db:
                hex_obj = Table_Hex.get_by_id(selected_hex_id)
                sklad_channel = Table_SkladChannel.get_or_none(Table_SkladChannel.hex_id == hex_obj.id)
            
            if not sklad_channel:
                await interaction.response.send_message("❌ Канал для этого гекса не настроен.", ephemeral=True)
                return
            
            channel = ctx.guild.get_channel(sklad_channel.channel_id)
            if not channel:
                await interaction.response.send_message("❌ Канал не найден.", ephemeral=True)
                return
            
            current_time = datetime.datetime.now()
            created_at = int(current_time.timestamp())
            time_end = int((current_time + datetime.timedelta(days=2, hours=1)).timestamp())
            text = format_sklad_text(hex_obj.hex_value, hex_obj.region, warehouse, password)
            
            header = f"👤 {ctx.author.display_name} · создан в <t:{created_at}:t>"
            
            view = View()
            view.add_item(Button(label="Обновить таймер", style=ButtonStyle.grey, custom_id="update_sklad_timer"))
            timer_message = await channel.send(f"{header}\n{text}\n⏰ — <t:{time_end}:R>", view=view)
            
            with db:
                Table_SkladTimer.create(guild_id=ctx.guild.id, channel_id=channel.id, message_id=timer_message.id,
                    text=text, time_shift=time_end - int(current_time.timestamp()), time_end=time_end,
                    author_id=ctx.author.id, author_name=ctx.author.display_name, created_at=created_at,
                    hex_id=hex_obj.id)
            
            await interaction.response.send_message("✅ Таймер склада установлен на 2 дня и 1 час!", ephemeral=True)
    
    view = View()
    options = [{"label": f"{h.hex_value} ({h.region})", "value": str(h.id)} for h in hexes]
    select = HexSelect(placeholder="Выбери гекс...", options=[{"label": opt["label"], "value": opt["value"]} for opt in options])
    view.add_item(select)
    
    await ctx.respond("Выбери гекс для склада:", view=view, ephemeral=True)

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

token = os.environ.get("DISCORD_BOT_TOKEN")
if not token:
    print("ОШИБКА: Токен не найден!")
    exit(1)

DSBot.run(token)
