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
        return json.loads(DATA_FILE.read_text()).get("status", "closed")
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return "closed"


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
        selected = self.values[0]
        label, emoji, _ = TICKET_TYPES[selected]
        existing = discord.utils.get(interaction.guild.text_channels, name=f"{selected}-{clean_name(interaction.user.name)}")
        if existing:
            return await interaction.response.send_message(f"Ticket kamu sudah ada: {existing.mention}", ephemeral=True)

        category = interaction.guild.get_channel(TICKET_CATEGORY_ID) if TICKET_CATEGORY_ID else None
        if category and not isinstance(category, discord.CategoryChannel):
            category = None
        staff_role = interaction.guild.get_role(STAFF_ROLE_ID) if STAFF_ROLE_ID else None
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            interaction.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
        }
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        channel = await interaction.guild.create_text_channel(
            name=f"{selected}-{clean_name(interaction.user.name)}", category=category, overwrites=overwrites,
            topic=f"Ticket {label} milik {interaction.user} ({interaction.user.id})",
        )
        embed = discord.Embed(title=f"{emoji} Ticket {label}", description=f"Halo {interaction.user.mention}, silakan jelaskan kebutuhanmu secara lengkap. Staff Elio Market akan segera membantu.", color=discord.Color.blurple())
        embed.set_footer(text="Elio Market • Ticket Support")
        await channel.send(content=interaction.user.mention, embed=embed, view=CloseTicketView())
        await interaction.response.send_message(f"Ticket berhasil dibuat: {channel.mention}", ephemeral=True)


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


@bot.event
async def on_member_join(member: discord.Member):
    channel = member.guild.get_channel(WELCOME_CHANNEL_ID) if WELCOME_CHANNEL_ID else None
    if not isinstance(channel, discord.TextChannel):
        return
    embed = discord.Embed(title="Selamat Datang di Elio Market", description=f"Halo {member.mention}, selamat bergabung di server kami. Silakan baca informasi server dan hubungi staff jika membutuhkan bantuan.", color=discord.Color.green())
    embed.set_thumbnail(url=member.display_avatar.url)
    welcome_image = os.getenv("WELCOME_IMAGE_URL", "")
    if welcome_image:
        embed.set_image(url=welcome_image)
    embed.set_footer(text=f"Member ke-{member.guild.member_count} • Elio Market")
    await channel.send(embed=embed)


@bot.event
async def on_member_remove(member: discord.Member):
    channel = member.guild.get_channel(GOODBYE_CHANNEL_ID) if GOODBYE_CHANNEL_ID else None
    if not isinstance(channel, discord.TextChannel):
        return
    embed = discord.Embed(title="Sampai Jumpa", description=f"{member.name} telah meninggalkan Elio Market. Semoga sukses selalu.", color=discord.Color.orange())
    goodbye_image = os.getenv("GOODBYE_IMAGE_URL", "")
    if goodbye_image:
        embed.set_image(url=goodbye_image)
    embed.set_footer(text="Elio Market")
    await channel.send(embed=embed)


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


bot.tree.add_command(setup, guild=discord.Object(id=GUILD_ID) if GUILD_ID else None)


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

