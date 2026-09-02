import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("elio-market")

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
STORE_CHANNEL_ID = int(os.getenv("STORE_CHANNEL_ID", "0"))
WELCOME_CHANNEL_ID = int(os.getenv("WELCOME_CHANNEL_ID", "0"))
GOODBYE_CHANNEL_ID = int(os.getenv("GOODBYE_CHANNEL_ID", "0"))
TICKET_CATEGORY_ID = int(os.getenv("TICKET_CATEGORY_ID", "0"))
STAFF_ROLE_ID = int(os.getenv("STAFF_ROLE_ID", "0"))
DATA_FILE = Path(os.getenv("DATA_FILE", "data/store.json"))

COLORS = {"open": discord.Color.green(), "closed": discord.Color.red()}
TICKET_TYPES = {
    "buy": ("Beli", "🛒", "Untuk membeli produk atau layanan Elio Market."),
    "sell": ("Jual", "💰", "Untuk menawarkan produk atau layanan kepada Elio Market."),
    "general": ("General", "💬", "Untuk pertanyaan atau bantuan umum."),
    "report": ("Laporan", "🚨", "Untuk melaporkan masalah, pelanggaran, atau kendala."),
}


def now_text() -> str:
    return discord.utils.format_dt(datetime.now(timezone.utc), style="F")


def load_state() -> str:
    try:
        return json.loads(DATA_FILE.read_text()).get("status", os.getenv("STORE_DEFAULT_STATUS", "open"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return os.getenv("STORE_DEFAULT_STATUS", "open")


def save_state(status: str) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps({"status": status}, indent=2))


def clean_name(name: str) -> str:
    value = re.sub(r"[^a-z0-9-]", "-", name.lower())
    return re.sub(r"-+", "-", value).strip("-")[:18] or "member"


def is_staff(member: discord.Member) -> bool:
    return member.guild_permissions.manage_channels or (
        STAFF_ROLE_ID != 0 and any(role.id == STAFF_ROLE_ID for role in member.roles)
    )


intents = discord.Intents.default()
intents.members = True
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents)


class StoreView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Buka Toko", style=discord.ButtonStyle.success, emoji="🟢", custom_id="store:open")
    async def open_store(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
            return await interaction.response.send_message("Kamu tidak memiliki izin untuk mengubah status toko.", ephemeral=True)
        save_state("open")
        await publish_store(interaction.guild, "open")
        await interaction.response.send_message("Status Elio Market berhasil diubah menjadi **OPEN**.", ephemeral=True)

    @discord.ui.button(label="Tutup Toko", style=discord.ButtonStyle.danger, emoji="🔴", custom_id="store:close")
    async def close_store(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
            return await interaction.response.send_message("Kamu tidak memiliki izin untuk mengubah status toko.", ephemeral=True)
        save_state("closed")
        await publish_store(interaction.guild, "closed")
        await interaction.response.send_message("Status Elio Market berhasil diubah menjadi **CLOSED**.", ephemeral=True)


class CloseTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Tutup Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="ticket:close")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
            return await interaction.response.send_message("Hanya staff yang dapat menutup ticket.", ephemeral=True)
        await interaction.response.send_message("Ticket akan ditutup dalam 5 detik.")
        await asyncio.sleep(5)
        await interaction.channel.delete(reason=f"Ticket ditutup oleh {interaction.user}")


class TicketSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=label, value=value, emoji=emoji, description=desc) for value, (label, emoji, desc) in TICKET_TYPES.items()]
        super().__init__(placeholder="Pilih jenis ticket yang kamu butuhkan...", options=options, custom_id="ticket:type")

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Ticket hanya dapat dibuat di server.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        selected = self.values[0]
        label, emoji, _ = TICKET_TYPES[selected]
        existing = discord.utils.get(interaction.guild.text_channels, name=f"{selected}-{clean_name(interaction.user.name)}")
        if existing:
            return await interaction.edit_original_response(content=f"Ticket kamu sudah ada: {existing.mention}")

        category = interaction.guild.get_channel(TICKET_CATEGORY_ID) if TICKET_CATEGORY_ID else None
        if TICKET_CATEGORY_ID and not isinstance(category, discord.CategoryChannel):
            return await interaction.edit_original_response(content="`TICKET_CATEGORY_ID` salah atau bot tidak dapat melihat kategori tersebut. Masukkan ID kategori Discord yang benar di Railway Variables.")
        staff_role = interaction.guild.get_role(STAFF_ROLE_ID) if STAFF_ROLE_ID else None
        bot_member = interaction.guild.me
        if bot_member is None:
            return await interaction.edit_original_response(content="Bot tidak terdeteksi sebagai member server. Pastikan bot masih berada di server.")
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            bot_member: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
        }
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        try:
            channel = await interaction.guild.create_text_channel(
                name=f"{selected}-{clean_name(interaction.user.name)}", category=category, overwrites=overwrites,
                topic=f"Ticket {label} milik {interaction.user} ({interaction.user.id})",
            )
            embed = discord.Embed(title=f"{emoji} Ticket {label}", description=f"Halo {interaction.user.mention}, silakan jelaskan kebutuhanmu secara lengkap. Staff Elio Market akan segera membantu.", color=discord.Color.blurple())
            embed.set_footer(text="Elio Market • Ticket Support")
            await channel.send(content=interaction.user.mention, embed=embed, view=CloseTicketView())
        except discord.Forbidden:
            return await interaction.edit_original_response(content="Bot tidak memiliki permission untuk membuat ticket. Berikan **Manage Channels**, **View Channel**, **Send Messages**, dan **Embed Links** pada bot/kategori ticket.")
        except discord.HTTPException as error:
            log.error("Ticket creation failed: %s", error)
            return await interaction.edit_original_response(content="Discord menolak pembuatan ticket. Periksa ID kategori dan permission bot, lalu coba lagi.")
        await interaction.edit_original_response(content=f"Ticket berhasil dibuat: {channel.mention}")


class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketSelect())


async def publish_store(guild: discord.Guild | None, status: str):
    if not guild:
        return
    channel = guild.get_channel(STORE_CHANNEL_ID) if STORE_CHANNEL_ID else None
    if not isinstance(channel, discord.TextChannel):
        return
    is_open = status == "open"
    embed = discord.Embed(
        title=("🟢 Store Open" if is_open else "🔴 Store Closed"),
        description=("**Elio Market sekarang sudah buka.**\n\nSilakan order kebutuhanmu melalui ticket panel." if is_open else "**Elio Market sekarang sudah tutup.**\n\nOrder tetap boleh ditinggalkan, tetapi akan diproses saat toko kembali buka."),
        color=COLORS[status],
    )
    embed.add_field(name="Status", value="OPEN" if is_open else "CLOSED", inline=True)
    embed.add_field(name="Waktu", value=now_text(), inline=True)
    embed.set_footer(text="Elio Market • Store Status")
    await channel.send(embed=embed)


@bot.event
async def on_ready():
    bot.add_view(StoreView())
    bot.add_view(TicketView())
    if GUILD_ID:
        guild = discord.Object(id=GUILD_ID)
        synced = await bot.tree.sync(guild=guild)
        log.info("Logged in as %s; synced %d guild commands", bot.user, len(synced))
    else:
        await bot.tree.sync()
        log.info("Logged in as %s; synced global commands", bot.user)


def welcome_embed(member: discord.Member) -> discord.Embed:
    embed = discord.Embed(
        title="👋 Welcome to Elio Market",
        description=(f"Halo {member.mention}, selamat datang di **Elio Market - Market Digital!**\n\n"
                     "**Info Member:**\n"
                     f"• **Username:** `{member.name}`\n"
                     f"• **Total Member:** `{member.guild.member_count}`"),
        color=discord.Color.from_rgb(126, 52, 220),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"Elio Market • Digital Store | {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    return embed


def goodbye_embed(member: discord.Member) -> discord.Embed:
    embed = discord.Embed(
        title="👋 Goodbye from Elio Market",
        description=(f"**{member.name}** telah meninggalkan **Elio Market - Market Digital.**\n\n"
                     "Semoga sukses selalu dan sampai jumpa kembali."),
        color=discord.Color.from_rgb(126, 52, 220),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Info Member", value=f"• **Username:** `{member.name}`\n• **Total Member:** `{member.guild.member_count}`", inline=False)
    embed.set_footer(text=f"Elio Market • Digital Store | {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    return embed


@bot.event
async def on_member_join(member: discord.Member):
    channel = member.guild.get_channel(WELCOME_CHANNEL_ID) if WELCOME_CHANNEL_ID else None
    if isinstance(channel, discord.TextChannel):
        await channel.send(embed=welcome_embed(member))


@bot.event
async def on_member_remove(member: discord.Member):
    channel = member.guild.get_channel(GOODBYE_CHANNEL_ID) if GOODBYE_CHANNEL_ID else None
    if isinstance(channel, discord.TextChannel):
        await channel.send(embed=goodbye_embed(member))


test = app_commands.Group(name="test", description="Uji coba pesan welcome dan goodbye")


@test.command(name="welcome", description="Kirim contoh pesan welcome")
@app_commands.checks.has_permissions(administrator=True)
async def test_welcome(interaction: discord.Interaction, member: discord.Member | None = None):
    target = member or interaction.user
    if not isinstance(target, discord.Member):
        return await interaction.response.send_message("Member tidak ditemukan.", ephemeral=True)
    await interaction.channel.send(embed=welcome_embed(target))
    await interaction.response.send_message("Contoh welcome berhasil dikirim.", ephemeral=True)


@test.command(name="goodbye", description="Kirim contoh pesan goodbye")
@app_commands.checks.has_permissions(administrator=True)
async def test_goodbye(interaction: discord.Interaction, member: discord.Member | None = None):
    target = member or interaction.user
    if not isinstance(target, discord.Member):
        return await interaction.response.send_message("Member tidak ditemukan.", ephemeral=True)
    await interaction.channel.send(embed=goodbye_embed(target))
    await interaction.response.send_message("Contoh goodbye berhasil dikirim.", ephemeral=True)


market = app_commands.Group(name="market", description="Kontrol status toko Elio Market")


@market.command(name="open", description="Buka Elio Market")
@app_commands.checks.has_permissions(administrator=True)
async def market_open(interaction: discord.Interaction):
    save_state("open")
    await publish_store(interaction.guild, "open")
    await interaction.response.send_message("Elio Market berhasil dibuka: **OPEN**.", ephemeral=True)


@market.command(name="close", description="Tutup Elio Market")
@app_commands.checks.has_permissions(administrator=True)
async def market_close(interaction: discord.Interaction):
    save_state("closed")
    await publish_store(interaction.guild, "closed")
    await interaction.response.send_message("Elio Market berhasil ditutup: **CLOSED**.", ephemeral=True)


setup = app_commands.Group(name="setup", description="Pengaturan panel Elio Market")


@setup.command(name="store", description="Kirim panel kontrol status toko")
@app_commands.checks.has_permissions(administrator=True)
async def setup_store(interaction: discord.Interaction):
    embed = discord.Embed(title="Elio Market • Store Control", description="Gunakan tombol di bawah untuk membuka atau menutup toko.", color=discord.Color.blurple())
    await interaction.channel.send(embed=embed, view=StoreView())
    await interaction.response.send_message("Panel kontrol toko berhasil dikirim.", ephemeral=True)


@setup.command(name="ticket", description="Kirim panel pembuatan ticket")
@app_commands.checks.has_permissions(administrator=True)
async def setup_ticket(interaction: discord.Interaction):
    embed = discord.Embed(title="Elio Market • Ticket Center", description="Pilih kategori ticket yang sesuai dengan kebutuhanmu. Jangan membuat ticket berulang untuk masalah yang sama.", color=discord.Color.blurple())
    embed.add_field(name="🛒 Beli", value="Membeli produk atau layanan.", inline=False)
    embed.add_field(name="💰 Jual", value="Menawarkan produk atau layanan.", inline=False)
    embed.add_field(name="💬 General", value="Pertanyaan umum.", inline=False)
    embed.add_field(name="🚨 Laporan", value="Laporan masalah atau pelanggaran.", inline=False)
    await interaction.channel.send(embed=embed, view=TicketView())
    await interaction.response.send_message("Panel ticket berhasil dikirim.", ephemeral=True)


@setup.command(name="status", description="Kirim status toko saat ini")
@app_commands.checks.has_permissions(administrator=True)
async def setup_status(interaction: discord.Interaction):
    await publish_store(interaction.guild, load_state())
    await interaction.response.send_message("Status toko berhasil dikirim.", ephemeral=True)


bot.tree.add_command(market, guild=discord.Object(id=GUILD_ID) if GUILD_ID else None)
bot.tree.add_command(setup, guild=discord.Object(id=GUILD_ID) if GUILD_ID else None)
bot.tree.add_command(test, guild=discord.Object(id=GUILD_ID) if GUILD_ID else None)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        message = "Perintah ini hanya dapat digunakan oleh administrator."
    else:
        log.exception("Command error", exc_info=error)
        message = "Terjadi kesalahan saat menjalankan perintah. Periksa konfigurasi bot."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN belum diatur")
bot.run(TOKEN)

