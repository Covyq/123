import os
import datetime
import traceback
import discord
from discord.ext import tasks
from discord.ui import View, Button
from peewee import *

# ─── НАСТРОЙКИ ─────────────────────────────────────────────
GUILD_ID = 419565206335651840
allowed_role_id = 1493199914572972032

bot = discord.Bot(
    intents=discord.Intents.all(),
    debug_guilds=[GUILD_ID]
)

db = SqliteDatabase("TimerDataBase.db")

# ─── БАЗА ──────────────────────────────────────────────────
class BaseModel(Model):
    guild_id = BigIntegerField()

    class Meta:
        database = db

class SkladTimer(BaseModel):
    channel_id = BigIntegerField()
    message_id = BigIntegerField()
    text = TextField()
    time_end = BigIntegerField()

db.connect(reuse_if_open=True)
db.create_tables([SkladTimer])

# ─── УТИЛИТЫ ───────────────────────────────────────────────
def has_access(member):
    return (
        member.guild_permissions.administrator or
        any(r.id == allowed_role_id for r in member.roles)
    )

# ─── КНОПКА ────────────────────────────────────────────────
class SkladView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Обновить склад", style=discord.ButtonStyle.green)
    async def update(self, button: Button, interaction: discord.Interaction):

        try:
            row = SkladTimer.get_or_none(SkladTimer.message_id == interaction.message.id)
            if not row:
                await interaction.response.send_message("❌ Не найден склад", ephemeral=True)
                return

            now = datetime.datetime.utcnow()
            new_end = int((now + datetime.timedelta(hours=48)).timestamp())

            row.time_end = new_end
            row.save()

            await interaction.message.edit(
                content=f"{row.text}\n⏰ Обновлено: <t:{new_end}:R>",
                view=self
            )

            await interaction.response.send_message("✅ Склад обновлён на 48 часов", ephemeral=True)

        except Exception:
            print(traceback.format_exc())
            await interaction.response.send_message("❌ Ошибка", ephemeral=True)

# ─── READY ────────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ Бот запущен: {bot.user}")

# ─── /СКЛАД ────────────────────────────────────────────────
@bot.slash_command(name="склад", guild_ids=[GUILD_ID])
async def sklad(
    ctx,
    гекс: str,
    регион: str,
    склад: str,
    пароль: str
):

    try:
        if not has_access(ctx.author):
            await ctx.respond("❌ Нет прав", ephemeral=True)
            return

        now = datetime.datetime.utcnow()
        end = int((now + datetime.timedelta(hours=48)).timestamp())

        text = (
            f"👤 {ctx.author.display_name}\n"
            f"**Гекс:** {гекс}\n"
            f"**Регион:** {регион}\n"
            f"**Склад:** {склад}\n"
            f"**Пароль:** {пароль}"
        )

        view = SkladView()

        msg = await ctx.send(
            content=f"{text}\n⏰ Через 48 часов: <t:{end}:R>",
            view=view
        )

        SkladTimer.create(
            guild_id=ctx.guild.id,
            channel_id=ctx.channel.id,
            message_id=msg.id,
            text=text,
            time_end=end
        )

        await ctx.respond("✅ Склад создан", ephemeral=True)

    except Exception:
        print(traceback.format_exc())
        await ctx.respond("❌ Ошибка", ephemeral=True)

# ─── ЗАПУСК ───────────────────────────────────────────────
bot.run(os.environ.get("DISCORD_BOT_TOKEN"))
