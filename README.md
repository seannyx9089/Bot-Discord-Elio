# Elio Market Discord Bot

Bot Discord untuk operasional toko **Elio Market**. Project ini menyediakan panel status toko, welcome/goodbye, serta ticket panel dengan kategori **Beli**, **Jual**, **General**, dan **Laporan**.

## Fitur yang tersedia

Bot menyediakan command status toko langsung: `/market open` untuk membuka toko dan `/market close` untuk menutup toko. Command `/setup store` tetap tersedia sebagai panel alternatif dengan tombol **Buka Toko** dan **Tutup Toko**. Perintah `/setup ticket` mengirim panel ticket yang dapat digunakan member. Perintah `/setup status` mengirim status toko terakhir yang tersimpan.

Saat member bergabung atau keluar, bot mengirim embed ke channel yang telah dikonfigurasi. Embed menggunakan **avatar profil member sebagai gambar thumbnail**, serta menampilkan mention member, username, dan jumlah total member. Setiap ticket dibuat sebagai channel privat; pemilik ticket dan role staff dapat melihatnya. Staff dapat mengambil ticket dengan tombol **Claim Ticket** atau menutupnya dengan tombol **Tutup Ticket**. Saat ticket ditutup, bot membuat transcript HTML dari seluruh chat dan mengirimkannya melalui DM kepada pemilik ticket.

Untuk menguji tampilan tanpa menunggu member masuk atau keluar, administrator dapat menggunakan `/test welcome` dan `/test goodbye`. Target member dapat dipilih sebagai opsi command; jika dikosongkan, bot menggunakan akun administrator yang menjalankan command. Pemilik ticket harus membuka DM dari server agar transcript dapat diterima.

## Persiapan Discord Developer Portal

Buat Application di [Discord Developer Portal](https://discord.com/developers/applications), masuk ke menu **Bot**, lalu salin token bot. Jangan pernah memasukkan token ke file yang akan diunggah ke GitHub. Pada bagian **Privileged Gateway Intents**, aktifkan **Server Members Intent** karena fitur welcome/goodbye membutuhkan event member.

Undang bot ke server menggunakan OAuth2 URL Generator dengan scopes `bot` dan `applications.commands`. Permission minimum yang dibutuhkan adalah View Channels, Send Messages, Embed Links, Read Message History, Manage Channels, serta Manage Roles jika konfigurasi role memerlukannya. Setelah bot masuk, aktifkan Developer Mode di Discord, lalu salin ID server, channel, kategori ticket, dan role staff. Pastikan permission **Send Messages**, **Embed Links**, dan **View Channel** diberikan pada channel welcome/goodbye.

## Konfigurasi lokal atau Railway

Salin `.env.example` sebagai referensi. Di Railway, tambahkan setiap pasangan nama dan nilai berikut pada **Variables**. Token hanya dimasukkan sebagai variable Railway dan tidak disimpan di repository.

| Variable | Keterangan |
|---|---|
| `DISCORD_TOKEN` | Token bot dari Discord Developer Portal. |
| `GUILD_ID` | ID server Discord. Jika diisi, slash command tersinkron lebih cepat ke server tersebut. |
| `STORE_CHANNEL_ID` | Channel untuk mengirim status toko. |
| `WELCOME_CHANNEL_ID` | Channel welcome member baru. |
| `GOODBYE_CHANNEL_ID` | Channel goodbye member yang keluar. |
| `TICKET_CATEGORY_ID` | Kategori tempat channel ticket dibuat. |
| `STAFF_ROLE_ID` | Role staff yang dapat melihat dan menutup ticket. |
| `WELCOME_IMAGE_URL` | Opsional, URL gambar welcome langsung. |
| `GOODBYE_IMAGE_URL` | Opsional, URL gambar goodbye langsung. |
| `DATA_FILE` | Opsional; default `data/store.json`. |
| `STORE_DEFAULT_STATUS` | Opsional; status awal jika data belum tersimpan. Default `open`. |

## Deployment melalui GitHub dan Railway

Buat repository GitHub baru, unggah `bot.py`, `requirements.txt`, `Procfile`, `.env.example`, dan `README.md`. Jangan unggah file `.env` atau token. Di Railway, pilih **New Project**, kemudian **Deploy from GitHub Repo** dan pilih repository tersebut. Railway akan membaca `requirements.txt` dan menjalankan proses dari `Procfile`.

Setelah variables dimasukkan, lakukan deploy ulang. Buka log deployment dan pastikan terlihat pesan bahwa bot berhasil login serta slash command berhasil tersinkron. Setelah itu administrator dapat menjalankan `/market open` atau `/market close`, kemudian menjalankan `/setup ticket` pada channel yang diinginkan.

## Catatan penting

Status toko disimpan dalam `data/store.json`. Pada Railway, filesystem service dapat bersifat sementara ketika container dibuat ulang, sehingga status awal akan mengikuti `STORE_DEFAULT_STATUS` dan default-nya adalah `OPEN`. Fitur utama bot tetap berjalan, tetapi bila status harus permanen lintas redeploy, tahap berikutnya dapat ditambahkan database atau penyimpanan eksternal.

Gambar contoh yang sudah dikirim dapat dijadikan referensi gaya embed status toko. Setelah contoh welcome/goodbye dikirim, teks, warna, judul, footer, dan layout embed dapat disesuaikan agar konsisten dengan branding Elio Market.

## Struktur project

```text
elio-market-bot/
├── bot.py
├── requirements.txt
├── Procfile
├── .env.example
└── README.md
```
