import os
import asyncio
import datetime
from peewee import *
from discord import (
    Bot, Intents, ApplicationContext, Option, SlashCommandOptionType,
    Interaction, InteractionType, ButtonStyle, Guild, Message, TextChannel,
    HTTPException, ChannelType
)
from discord.ui import View, Button

# ─── Настройки ───────────────────────────────────────────────────────────
db = SqliteDatabase('TimerDataBase.db')
DSBot = Bot(intents=Intents.all())
GUILD_ID = 1492964694577905684
allowed_role_name = "Таймер-Мастер"  # Роль, которой разрешено менять каналы

# ─── Модели ──────────────────────────────────────────────────────────────
class TableBase(Model):
    guild_id = BigIntegerField()
    class Meta:
        database = db

class Table_SkladTimer(TableBase):
    channel_id = BigIntegerField()
    message_id = BigIntegerField()
    text = TextField()
    time_end = BigIntegerField()

class Table_SimpleTimer(TableBase):
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

# ─── Вспомогательные функции ─────────────────────────────────────────────
def get_channel_id(table, guild_id: int):
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

def format_sklad_text(author, hex_val, region, warehouse, password):
    return (
        f"👤 {author}\n"
        f"**Гекс:** {hex_val}\n"
        f"**Регион:** {region}\n"
        f"**Склад:** {warehouse}\n"
        f"**Пароль:** {password}"
    )

# ─── Фоновый цикл ────────────────────────────────────────────────────────
@DSBot.event
async def on_ready():
    print(f"Бот {DSBot.user} запущен!")
    while True:
        now = int(datetime.datetime.now().timestamp())

        # склад таймеры (удаляются)
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
                            pass
                timer.delete_instance()

        # обычные таймеры
        with db:
            for timer in Table_SimpleTimer.select().where(Table_SimpleTimer.time_end < now):
                guild: Guild = DSBot.get_guild(timer.guild_id)
                if guild:
                    channel: TextChannel = guild.get_channel(timer.channel_id)
                    if channel:
                        try:
                            msg: Message = await channel.fetch_message(timer.message_id)
                            header = f"👤 {timer.author_name} · создан в <t:{timer.created_at}:t>"
                            await msg.edit(content=f"{header}\n{timer.text}\n✅ **Готово**")
                        except HTTPException:
                            pass
                timer.delete_instance()

        await asyncio.sleep(6)

# ─── Настройка каналов ───────────────────────────────────────────────────
@DSBot.slash_command(name="setskladchannel", guild_ids=[GUILD_ID])
async def setskladchannel(ctx: ApplicationContext,
    channel: Option(SlashCommandOptionType.channel, channel_types=[ChannelType.text])):

    if not ctx.author.guild_permissions.administrator and allowed_role_name not in [role.name for role in ctx.author.roles]:
        await ctx.respond(f"❌ Только админы или роль **{allowed_role_name}** могут менять канал склада.", ephemeral=True)
        return

    with db:
        set_channel_id(Table_SkladChannel, ctx.guild.id, channel.id)

    await ctx.respond(f"✅ Канал склада установлен: {channel.mention}", ephemeral=True)


@DSBot.slash_command(name="setsimplechannel", guild_ids=[GUILD_ID])
async def setsimplechannel(ctx: ApplicationContext,
    channel: Option(SlashCommandOptionType.channel, channel_types=[ChannelType.text])):

    if not ctx.author.guild_permissions.administrator and allowed_role_name not in [role.name for role in ctx.author.roles]:
        await ctx.respond(f"❌ Только админы или роль **{allowed_role_name}** могут менять канал таймеров.", ephemeral=True)
        return

    with db:
        set_channel_id(Table_SimpleChannel, ctx.guild.id, channel.id)

    await ctx.respond(f"✅ Канал таймеров установлен: {channel.mention}", ephemeral=True)

# ─── Склад ───────────────────────────────────────────────────────────────
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

    now = datetime.datetime.now()
    time_end = int((now + datetime.timedelta(days=2, hours=1)).timestamp())

    text = format_sklad_text(ctx.author.display_name, hex_val, region, warehouse, password)

    view = View()
    view.add_item(Button(label="Обновить таймер", style=ButtonStyle.grey, custom_id="update_sklad_timer"))

    msg = await ctx.send(f"{text}\n⏰ — <t:{time_end}:R>", view=view)

    with db:
        Table_SkladTimer.create(
            guild_id=ctx.guild.id,
            channel_id=ctx.channel.id,
            message_id=msg.id,
            text=text,
            time_end=time_end
        )

    await ctx.respond("✅ Таймер создан!", ephemeral=True)

# ─── Обычный таймер (с выбором времени) ─────────────────────────────────
@DSBot.slash_command(name="таймер", guild_ids=[GUILD_ID])
async def timer(ctx: ApplicationContext,
    text: Option(str, name="текст", description="Текст таймера"),
    days: Option(int, name="дни", description="Дни", default=0),
    hours: Option(int, name="часы", description="Часы", default=0),
    minutes: Option(int, name="минуты", description="Минуты", default=0),
    seconds: Option(int, name="секунды", description="Секунды", default=0)):

    simple_channel_id = get_channel_id(Table_SimpleChannel, ctx.guild.id)
    if simple_channel_id and ctx.channel.id != simple_channel_id:
        ch = ctx.guild.get_channel(simple_channel_id)
        mention = ch.mention if ch else f"<#{simple_channel_id}>"
        await ctx.respond(f"❌ Только в канале {mention}", ephemeral=True)
        return

    if days == 0 and hours == 0 and minutes == 0 and seconds == 0:
        await ctx.respond("❌ Укажи хотя бы какое-то время", ephemeral=True)
        return

    now = datetime.datetime.now()
    created = int(now.timestamp())
    time_end = int((now + datetime.timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)).timestamp())

    header = f"👤 {ctx.author.display_name} · создан в <t:{created}:t>"
    msg = await ctx.send(f"{header}\n{text}\n⏰ — <t:{time_end}:R>")

    with db:
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

# ─── Кнопка обновления склада ─────────────────────────────────────────────
async def on_button_clicked(interaction: Interaction):
    if interaction.type == InteractionType.component:
        if interaction.data.get("custom_id") == "update_sklad_timer":
            try:
                timer = Table_SkladTimer.get(
                    Table_SkladTimer.guild_id == interaction.guild.id,
                    Table_SkladTimer.channel_id == interaction.channel.id,
                    Table_SkladTimer.message_id == interaction.message.id
                )
                new_end = int(datetime.datetime.now().timestamp()) + (timer.time_end - int(datetime.datetime.now().timestamp()))
                timer.time_end = new_end
                timer.save()
                await interaction.message.edit(content=f"{timer.text}\n⏰ — <t:{new_end}:R>")
                await interaction.response.send_message("✅ Таймер обновлён!", ephemeral=True)
            except Table_SkladTimer.DoesNotExist:
                await interaction.response.send_message("⚠️ Таймер не найден", ephemeral=True)

DSBot.add_listener(on_button_clicked, "on_interaction")

# ─── Очистка при ручном удалении ───────────────────────────────────────────
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

# ─── Запуск ──────────────────────────────────────────────────────────────
token = os.environ.get("DISCORD_BOT_TOKEN")
if not token:
    print("Нет токена!")
    exit()
DSBot.run(token)
