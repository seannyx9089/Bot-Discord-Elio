import asyncio
import io
import json
import logging
import os
import re
import uuid
from html import escape
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("elio-market")

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
STORE_CHANNEL_ID = int(os.getenv("STORE_CHANNEL_ID", "0"))
TRANSACTION_CHANNEL_ID = int(os.getenv("TRANSACTION_CHANNEL_ID", "0"))
RATING_CHANNEL_ID = int(os.getenv("RATING_CHANNEL_ID", "0"))
AUTO_CLOSE_HOURS = float(os.getenv("AUTO_CLOSE_HOURS", "72"))
WELCOME_CHANNEL_ID = int(os.getenv("WELCOME_CHANNEL_ID", "0"))
GOODBYE_CHANNEL_ID = int(os.getenv("GOODBYE_CHANNEL_ID", "0"))
TICKET_CATEGORY_ID = int(os.getenv("TICKET_CATEGORY_ID", "0"))
STAFF_ROLE_ID = int(os.getenv("STAFF_ROLE_ID", "0"))
DATA_FILE = Path(os.getenv("DATA_FILE", "data/store.json"))
CONFIG_FILE = Path(os.getenv("CONFIG_FILE", "data/config.json"))

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


def load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


BOT_CONFIG = load_config()


def save_config() -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(BOT_CONFIG, indent=2))


def configured_staff_role_id() -> int:
    return int(BOT_CONFIG.get("staff_role_id", STAFF_ROLE_ID) or 0)


def is_staff(member: discord.Member) -> bool:
    role_id = configured_staff_role_id()
    return member.guild_permissions.manage_channels or (role_id != 0 and any(role.id == role_id for role in member.roles))


def is_owner(member: discord.Member) -> bool:
    configured_owner_id = int(BOT_CONFIG.get("owner_id", 0) or 0)
    return member.id == (configured_owner_id or member.guild.owner_id)


def is_owner_or_admin(member: discord.Member) -> bool:
    return is_owner(member) or member.guild_permissions.administrator


def owner_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        return isinstance(interaction.user, discord.Member) and is_owner(interaction.user)
    return app_commands.check(predicate)


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


def ticket_owner_id(channel: discord.TextChannel) -> int | None:
    match = re.search(r"\((\d{15,25})\)$", channel.topic or "")
    return int(match.group(1)) if match else None


async def make_transcript(channel: discord.TextChannel) -> bytes:
    rows = []
    async for message in channel.history(limit=None, oldest_first=True):
        content = escape(message.content or "")
        if message.attachments:
            files = "<br>".join(f'<a href="{escape(a.url)}">{escape(a.filename)}</a>' for a in message.attachments)
            content = f"{content}<br>{files}" if content else files
        if not content:
            content = "<em>(tidak ada teks)</em>"
        timestamp = message.created_at.strftime("%d/%m/%Y %H:%M:%S UTC")
        rows.append(f'<div class="message"><div class="meta"><strong>{escape(str(message.author))}</strong> <span>{timestamp}</span></div><div class="content">{content}</div></div>')
    body = "".join(rows) or '<p class="empty">Belum ada pesan.</p>'
    html = f'''<!doctype html><html lang="id"><head><meta charset="utf-8"><title>Transcript {escape(channel.name)}</title><style>body{{font-family:Arial,sans-serif;background:#171525;color:#eee;margin:0;padding:24px}}.wrap{{max-width:900px;margin:auto}}h1{{color:#a98cff}}.message{{background:#27233d;border-left:4px solid #7e34dc;border-radius:8px;padding:12px 16px;margin:10px 0;overflow-wrap:anywhere}}.meta{{color:#d4c9ff}}.meta span{{color:#999;font-size:12px;margin-left:8px}}.content{{margin-top:6px;white-space:pre-wrap}}</style></head><body><div class="wrap"><h1>Elio Market — Transcript Ticket</h1><p>Channel: #{escape(channel.name)}<br>Dibuat: {escape(channel.created_at.strftime("%d/%m/%Y %H:%M:%S UTC"))}<br>Ditutup: {escape(datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M:%S UTC"))}</p>{body}</div></body></html>'''
    return html.encode("utf-8")


class RatingView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=86400)
        for score in range(1, 6):
            button = discord.ui.Button(label=f"{score}/5", style=discord.ButtonStyle.primary, custom_id=f"rating:{score}")
            button.callback = self.make_callback(score)
            self.add_item(button)

    def make_callback(self, score: int):
        async def callback(interaction: discord.Interaction):
            guild = bot.get_guild(GUILD_ID) if GUILD_ID else None
            channel = guild.get_channel(RATING_CHANNEL_ID) if guild and RATING_CHANNEL_ID else None
            if isinstance(channel, discord.TextChannel):
                await channel.send(f"⭐ Rating ticket: **{score}/5** dari {interaction.user.mention} (`{interaction.user}`)")
            await interaction.response.send_message(f"Terima kasih, rating kamu **{score}/5** sudah diterima.", ephemeral=True)
            for child in self.children:
                child.disabled = True
            await interaction.message.edit(view=self)
        return callback


class TransactionApprovalView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Terima", style=discord.ButtonStyle.success, emoji="✅", custom_id="transaction:accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not is_owner_or_admin(interaction.user):
            return await interaction.response.send_message("Hanya Owner atau Administrator yang dapat menyetujui transaksi.", ephemeral=True)
        embed = interaction.message.embeds[0]
        embed.title = "✅ Transaksi Berhasil"
        embed.color = discord.Color.green()
        button.disabled = True
        self.children[1].disabled = True
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Tolak", style=discord.ButtonStyle.danger, emoji="❌", custom_id="transaction:reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not is_owner_or_admin(interaction.user):
            return await interaction.response.send_message("Hanya Owner atau Administrator yang dapat menolak transaksi.", ephemeral=True)
        embed = interaction.message.embeds[0]
        embed.title = "❌ Transaksi Ditolak"
        embed.color = discord.Color.red()
        button.disabled = True
        self.children[0].disabled = True
        await interaction.response.edit_message(embed=embed, view=self)


class CloseTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim Ticket", style=discord.ButtonStyle.primary, emoji="🙋", custom_id="ticket:claim")
    async def claim_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
            return await interaction.response.send_message("Hanya staff yang dapat melakukan claim ticket.", ephemeral=True)
        if not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("Channel ticket tidak ditemukan.", ephemeral=True)
        await interaction.response.send_message(f"Ticket ini telah di-claim oleh {interaction.user.mention}.")
        button.disabled = True
        button.label = f"Di-claim: {interaction.user.display_name[:70]}"
        await interaction.message.edit(view=self)

    @discord.ui.button(label="Tutup Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="ticket:close")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
            return await interaction.response.send_message("Hanya staff yang dapat menutup ticket.", ephemeral=True)
        if not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("Channel ticket tidak ditemukan.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        channel = interaction.channel
        transcript = await make_transcript(channel)
        owner_id = ticket_owner_id(channel)
        dm_sent = False
        if owner_id:
            owner = interaction.guild.get_member(owner_id) if interaction.guild else None
            if owner is None and interaction.guild:
                try:
                    owner = await interaction.guild.fetch_member(owner_id)
                except discord.HTTPException:
                    owner = None
            if owner:
                try:
                    await owner.send(content=f"Ticket **#{channel.name}** sudah ditutup. Berikan rating pelayanan dengan tombol di bawah. Transcript terlampir.", file=discord.File(io.BytesIO(transcript), filename=f"transcript-{channel.name}.html"), view=RatingView())
                    dm_sent = True
                except discord.HTTPException:
                    dm_sent = False
        await interaction.edit_original_response(content=("Transcript berhasil dikirim ke DM pemilik ticket. Ticket akan ditutup dalam 5 detik." if dm_sent else "Ticket akan ditutup dalam 5 detik, tetapi DM transcript gagal dikirim. Pastikan DM pemilik ticket terbuka."))
        await asyncio.sleep(5)
        await channel.delete(reason=f"Ticket ditutup oleh {interaction.user}")


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
        staff_role_id = configured_staff_role_id()
        staff_role = interaction.guild.get_role(staff_role_id) if staff_role_id else None
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
            mentions = [interaction.user.mention]
            owner_id = int(BOT_CONFIG.get("owner_id", 0) or 0)
            if owner_id and owner_id != interaction.user.id:
                mentions.append(f"<@{owner_id}>")
            if staff_role:
                mentions.append(staff_role.mention)
            await channel.send(content=" ".join(mentions) + "\nTicket baru telah dibuat. Silakan segera ditangani.", embed=embed, view=CloseTicketView(), allowed_mentions=discord.AllowedMentions(users=True, roles=True))
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


class TransactionModal(discord.ui.Modal, title="Catat Transaksi Elio Market"):
    def __init__(self, buyer: discord.Member, source_ticket: discord.TextChannel | None = None):
        super().__init__()
        self.buyer = buyer
        self.source_ticket = source_ticket

    product = discord.ui.TextInput(
        label="Produk",
        placeholder="Contoh: SETUP BOT",
        max_length=100,
        required=True,
    )
    price = discord.ui.TextInput(
        label="Harga",
        placeholder="Contoh: 19000 atau Rp 19.000",
        max_length=30,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member) or not is_owner_or_admin(interaction.user):
            return await interaction.response.send_message("Hanya Owner atau Administrator server yang dapat mencatat transaksi.", ephemeral=True)
        channel = interaction.guild.get_channel(TRANSACTION_CHANNEL_ID) if interaction.guild and TRANSACTION_CHANNEL_ID else interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message("`TRANSACTION_CHANNEL_ID` belum benar atau channel transaksi tidak ditemukan.", ephemeral=True)
        raw_price = self.price.value.strip().replace("Rp", "").replace("rp", "").replace(" ", "").replace(".", "").replace(",", "")
        if not raw_price.isdigit():
            return await interaction.response.send_message("Format harga tidak valid. Gunakan contoh `50000` atau `Rp 50.000`.", ephemeral=True)
        amount = int(raw_price)
        transaction_id = f"TRX-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"
        embed = discord.Embed(title="💳 Transaksi Baru", color=discord.Color.green(), timestamp=datetime.now(timezone.utc))
        embed.add_field(name="ID Transaksi", value=f"`{transaction_id}`", inline=False)
        embed.add_field(name="Produk", value=self.product.value, inline=False)
        embed.add_field(name="Harga", value=f"Rp {amount:,}".replace(",", "."), inline=True)
        embed.add_field(name="Buyer", value=f"{buyer.mention}\n`{buyer.name}`", inline=True)
        if self.source_ticket:
            embed.add_field(name="Ticket", value=f"{self.source_ticket.mention}\n`{self.source_ticket.name}`", inline=True)
        embed.add_field(name="Dicatat oleh", value=f"{interaction.user.mention}\n`{interaction.user.name}`", inline=True)
        embed.add_field(name="Waktu", value=now_text(), inline=False)
        embed.set_footer(text="Elio Market • Transaction Log")
        await channel.send(embed=embed, view=TransactionApprovalView())
        await interaction.response.send_message(f"Transaksi `{transaction_id}` berhasil dicatat dan menunggu persetujuan admin.", ephemeral=True)


transaction = app_commands.Group(name="transaction", description="Catat transaksi Elio Market")


@transaction.command(name="add", description="Catat transaksi dengan Buyer, Produk, dan Harga")
async def transaction_add(interaction: discord.Interaction, buyer: discord.Member):
    if not isinstance(interaction.user, discord.Member) or not is_owner_or_admin(interaction.user):
        return await interaction.response.send_message("Hanya Owner atau Administrator server yang dapat mencatat transaksi.", ephemeral=True)
    source_ticket = interaction.channel if isinstance(interaction.channel, discord.TextChannel) and interaction.channel.name.startswith("buy-") else None
    await interaction.response.send_modal(TransactionModal(buyer, source_ticket))


@tasks.loop(hours=1)
async def auto_close_tickets():
    if not TICKET_CATEGORY_ID:
        return
    category = bot.get_channel(TICKET_CATEGORY_ID)
    if not isinstance(category, discord.CategoryChannel):
        return
    cutoff = datetime.now(timezone.utc).timestamp() - (AUTO_CLOSE_HOURS * 3600)
    for channel in list(category.text_channels):
        try:
            recent = [message async for message in channel.history(limit=1)]
            if not recent or recent[0].created_at.timestamp() > cutoff:
                continue
            transcript = await make_transcript(channel)
            owner_id = ticket_owner_id(channel)
            owner = category.guild.get_member(owner_id) if owner_id else None
            if owner:
                try:
                    await owner.send(content=f"Ticket **#{channel.name}** ditutup otomatis karena tidak aktif. Transcript terlampir.", file=discord.File(io.BytesIO(transcript), filename=f"transcript-{channel.name}.html"), view=RatingView())
                except discord.HTTPException:
                    pass
            await channel.delete(reason=f"Auto-close setelah {AUTO_CLOSE_HOURS} jam tidak aktif")
        except (discord.Forbidden, discord.HTTPException) as error:
            log.warning("Auto-close gagal untuk %s: %s", channel, error)


@auto_close_tickets.before_loop
async def before_auto_close():
    await bot.wait_until_ready()


@bot.event
async def on_ready():
    bot.add_view(StoreView())
    bot.add_view(TicketView())
    bot.add_view(CloseTicketView())
    bot.add_view(TransactionApprovalView())
    if not auto_close_tickets.is_running():
        auto_close_tickets.start()
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
@owner_only()
async def test_welcome(interaction: discord.Interaction, member: discord.Member | None = None):
    target = member or interaction.user
    if not isinstance(target, discord.Member):
        return await interaction.response.send_message("Member tidak ditemukan.", ephemeral=True)
    await interaction.channel.send(embed=welcome_embed(target))
    await interaction.response.send_message("Contoh welcome berhasil dikirim.", ephemeral=True)


@test.command(name="goodbye", description="Kirim contoh pesan goodbye")
@owner_only()
async def test_goodbye(interaction: discord.Interaction, member: discord.Member | None = None):
    target = member or interaction.user
    if not isinstance(target, discord.Member):
        return await interaction.response.send_message("Member tidak ditemukan.", ephemeral=True)
    await interaction.channel.send(embed=goodbye_embed(target))
    await interaction.response.send_message("Contoh goodbye berhasil dikirim.", ephemeral=True)


ticket = app_commands.Group(name="ticket", description="Pengaturan ticket Elio Market")
ticket_set = app_commands.Group(name="set", description="Atur Owner dan role staff ticket")


@ticket_set.command(name="staff", description="Atur role staff ticket")
@owner_only()
async def ticket_set_staff(interaction: discord.Interaction, role: discord.Role):
    BOT_CONFIG["staff_role_id"] = role.id
    save_config()
    await interaction.response.send_message(f"Role staff berhasil diatur ke {role.mention}.", ephemeral=True)


@ticket_set.command(name="owner", description="Jadikan akun yang menjalankan command sebagai Owner bot")
async def ticket_set_owner(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member) or not (interaction.user.guild.owner_id == interaction.user.id or is_owner(interaction.user)):
        return await interaction.response.send_message("Hanya Owner server yang dapat mengatur Owner bot.", ephemeral=True)
    BOT_CONFIG["owner_id"] = interaction.user.id
    save_config()
    await interaction.response.send_message(f"Akun {interaction.user.mention} sekarang menjadi Owner bot Elio Market.", ephemeral=True)


ticket.add_command(ticket_set)


market = app_commands.Group(name="market", description="Kontrol status toko Elio Market")


@market.command(name="open", description="Buka Elio Market")
@owner_only()
async def market_open(interaction: discord.Interaction):
    save_state("open")
    await publish_store(interaction.guild, "open")
    await interaction.response.send_message("Elio Market berhasil dibuka: **OPEN**.", ephemeral=True)


@market.command(name="close", description="Tutup Elio Market")
@owner_only()
async def market_close(interaction: discord.Interaction):
    save_state("closed")
    await publish_store(interaction.guild, "closed")
    await interaction.response.send_message("Elio Market berhasil ditutup: **CLOSED**.", ephemeral=True)


setup = app_commands.Group(name="setup", description="Pengaturan panel Elio Market")


@setup.command(name="ticket", description="Kirim panel pembuatan ticket")
@owner_only()
async def setup_ticket(interaction: discord.Interaction):
    embed = discord.Embed(title="Elio Market • Ticket Center", description="Pilih kategori ticket yang sesuai dengan kebutuhanmu. Jangan membuat ticket berulang untuk masalah yang sama.", color=discord.Color.blurple())
    embed.add_field(name="🛒 Beli", value="Membeli produk atau layanan.", inline=False)
    embed.add_field(name="💰 Jual", value="Menawarkan produk atau layanan.", inline=False)
    embed.add_field(name="💬 General", value="Pertanyaan umum.", inline=False)
    embed.add_field(name="🚨 Laporan", value="Laporan masalah atau pelanggaran.", inline=False)
    await interaction.channel.send(embed=embed, view=TicketView())
    await interaction.response.send_message("Panel ticket berhasil dikirim.", ephemeral=True)


bot.tree.add_command(ticket, guild=discord.Object(id=GUILD_ID) if GUILD_ID else None)
bot.tree.add_command(market, guild=discord.Object(id=GUILD_ID) if GUILD_ID else None)
bot.tree.add_command(transaction, guild=discord.Object(id=GUILD_ID) if GUILD_ID else None)
bot.tree.add_command(setup, guild=discord.Object(id=GUILD_ID) if GUILD_ID else None)
bot.tree.add_command(test, guild=discord.Object(id=GUILD_ID) if GUILD_ID else None)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions) or isinstance(error, app_commands.CheckFailure):
        message = "Perintah ini hanya dapat digunakan oleh Owner server."
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

