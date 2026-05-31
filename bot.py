import logging
import sqlite3
from datetime import datetime, timedelta
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)

# ============================================================
# KONFIGURASI — EDIT BAGIAN INI
# ============================================================
BOT_TOKEN = "8663072880:AAHUBNkjv4Jrf7H2B3KCR-a98B1Qu4MhKxY"
TIMEZONE = pytz.timezone("Asia/Jakarta")  # Ganti sesuai timezone kamu
WORK_START_HOUR = 12      # Jam mulai kerja (format 24 jam)
WORK_START_MINUTE = 0
WORK_END_HOUR = 21       # Jam selesai kerja (format 24 jam)
WORK_END_MINUTE = 0
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ============================================================
# DATABASE SETUP
# ============================================================
def init_db():
    conn = sqlite3.connect("absensi.db")
    c = conn.cursor()

    # Tabel utama absensi harian
    c.execute("""
        CREATE TABLE IF NOT EXISTS absensi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            full_name TEXT,
            tanggal TEXT,
            start_work TEXT,
            off_work TEXT,
            status TEXT DEFAULT 'active'
        )
    """)

    # Tabel aktivitas (EAT, TOILET, SMOKE, BACK)
    c.execute("""
        CREATE TABLE IF NOT EXISTS aktivitas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            tanggal TEXT,
            jenis TEXT,
            waktu_mulai TEXT,
            waktu_selesai TEXT,
            durasi_detik INTEGER DEFAULT 0
        )
    """)

    conn.commit()
    conn.close()


def get_conn():
    return sqlite3.connect("absensi.db")


# ============================================================
# HELPER FUNCTIONS
# ============================================================
def now():
    """Waktu sekarang sesuai timezone."""
    return datetime.now(TIMEZONE)


def format_waktu(dt):
    """Format: 31/05 09:57:31"""
    return dt.strftime("%d/%m %H:%M:%S")


def format_durasi(detik):
    """Konversi detik ke format: X jam Y menit Z detik"""
    detik = int(detik)
    jam = detik // 3600
    menit = (detik % 3600) // 60
    sisa_detik = detik % 60
    if jam > 0:
        return f"{jam} jam {menit} menit {sisa_detik} detik"
    elif menit > 0:
        return f"{menit} menit {sisa_detik} detik"
    else:
        return f"{sisa_detik} detik"


def get_display_name(user):
    """Ambil nama tampilan user."""
    if user.last_name:
        return f"{user.first_name} {user.last_name}"
    return user.first_name


def footer():
    return (
        "---------------------------------------\n"
        "Lisensi bot sepenuhnya untuk kepentingan perusahaan, tidak untuk diperjual belikan.\n"
        "- Hongshu"
    )


def header(user):
    return (
        f"Pengguna: {get_display_name(user)}\n"
        f"ID Pengguna: {user.id}\n\n"
    )


def get_tanggal_hari_ini():
    return now().strftime("%Y-%m-%d")


def get_absensi_hari_ini(user_id):
    """Ambil data absensi hari ini untuk user tertentu."""
    conn = get_conn()
    c = conn.cursor()
    tanggal = get_tanggal_hari_ini()
    c.execute(
        "SELECT * FROM absensi WHERE user_id=? AND tanggal=?",
        (user_id, tanggal)
    )
    row = c.fetchone()
    conn.close()
    return row


def get_aktivitas_terakhir(user_id):
    """Ambil aktivitas terakhir yang belum selesai (waktu_selesai NULL)."""
    conn = get_conn()
    c = conn.cursor()
    tanggal = get_tanggal_hari_ini()
    c.execute(
        """SELECT * FROM aktivitas 
           WHERE user_id=? AND tanggal=? AND waktu_selesai IS NULL
           ORDER BY id DESC LIMIT 1""",
        (user_id, tanggal)
    )
    row = c.fetchone()
    conn.close()
    return row


def hitung_total_aktivitas(user_id, jenis=None):
    """Hitung total durasi dan jumlah aktivitas hari ini."""
    conn = get_conn()
    c = conn.cursor()
    tanggal = get_tanggal_hari_ini()

    if jenis:
        c.execute(
            """SELECT COUNT(*), COALESCE(SUM(durasi_detik), 0)
               FROM aktivitas
               WHERE user_id=? AND tanggal=? AND jenis=? AND waktu_selesai IS NOT NULL""",
            (user_id, tanggal, jenis)
        )
    else:
        c.execute(
            """SELECT COUNT(*), COALESCE(SUM(durasi_detik), 0)
               FROM aktivitas
               WHERE user_id=? AND tanggal=? AND waktu_selesai IS NOT NULL""",
            (user_id, tanggal)
        )
    row = c.fetchone()
    conn.close()
    return row[0], row[1]  # (jumlah, total_detik)


def hitung_total_semua_aktivitas(user_id):
    """Hitung total semua aktivitas (EAT, TOILET, SMOKE) hari ini."""
    conn = get_conn()
    c = conn.cursor()
    tanggal = get_tanggal_hari_ini()
    c.execute(
        """SELECT COUNT(*), COALESCE(SUM(durasi_detik), 0)
           FROM aktivitas
           WHERE user_id=? AND tanggal=? AND waktu_selesai IS NOT NULL""",
        (user_id, tanggal)
    )
    row = c.fetchone()
    conn.close()
    return row[0], row[1]


def hitung_waktu_kerja(user_id):
    """Hitung total waktu kerja dan waktu bersih."""
    absensi = get_absensi_hari_ini(user_id)
    if not absensi or not absensi[5] or not absensi[6]:
        return 0, 0

    start = datetime.fromisoformat(absensi[5])
    end = datetime.fromisoformat(absensi[6])
    total_detik = (end - start).total_seconds()

    _, total_aktivitas_detik = hitung_total_semua_aktivitas(user_id)
    waktu_bersih = total_detik - total_aktivitas_detik

    return total_detik, waktu_bersih


def cek_pulang_lebih_awal():
    """Cek apakah pulang lebih awal dari jadwal."""
    sekarang = now()
    jadwal_pulang = sekarang.replace(
        hour=WORK_END_HOUR,
        minute=WORK_END_MINUTE,
        second=0,
        microsecond=0
    )
    if sekarang < jadwal_pulang:
        selisih = (jadwal_pulang - sekarang).total_seconds()
        return True, selisih
    return False, 0


def main_menu_keyboard():
    """Buat tombol inline keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("🟢 START WORK", callback_data="start_work"),
            InlineKeyboardButton("🔴 OFF WORK", callback_data="off_work"),
        ],
        [
            InlineKeyboardButton("🍽️ EAT", callback_data="eat"),
            InlineKeyboardButton("🚬 SMOKE", callback_data="smoke"),
        ],
        [
            InlineKeyboardButton("🚻 TOILET", callback_data="toilet"),
            InlineKeyboardButton("🔙 BACK", callback_data="back"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


# ============================================================
# COMMAND HANDLERS
# ============================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk command /start — tampilkan menu absensi."""
    await update.message.reply_text(
        "🏢 *Sistem Absensi Perusahaan Hongshu*\n\n"
        "Silakan pilih aktivitas kamu:",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown"
    )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk command /menu — tampilkan ulang tombol."""
    await update.message.reply_text(
        "📋 *Menu Absensi:*",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown"
    )


# ============================================================
# CALLBACK HANDLERS (Tombol Ditekan)
# ============================================================
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # Hapus loading indicator

    user = query.from_user
    action = query.data

    if action == "start_work":
        await handle_start_work(query, user)
    elif action == "off_work":
        await handle_off_work(query, user)
    elif action == "eat":
        await handle_aktivitas(query, user, "EAT", "Makan")
    elif action == "smoke":
        await handle_aktivitas(query, user, "SMOKE", "Merokok")
    elif action == "toilet":
        await handle_aktivitas(query, user, "TOILET", "Ke toilet")
    elif action == "back":
        await handle_back(query, user)


async def handle_start_work(query, user):
    """Proses tombol START WORK."""
    conn = get_conn()
    c = conn.cursor()
    tanggal = get_tanggal_hari_ini()
    sekarang = now()

    # Cek apakah sudah absen masuk hari ini
    existing = get_absensi_hari_ini(user.id)
    if existing and existing[5]:  # kolom start_work tidak kosong
        await query.message.reply_text(
            f"{header(user)}"
            f"⚠️ Kamu sudah melakukan absensi masuk hari ini!\n"
            f"Waktu masuk: {format_waktu(datetime.fromisoformat(existing[5]))}\n\n"
            f"{footer()}"
        )
        conn.close()
        return

    # Simpan ke database
    c.execute(
        """INSERT INTO absensi (user_id, username, full_name, tanggal, start_work, status)
           VALUES (?, ?, ?, ?, ?, 'active')""",
        (user.id, user.username, get_display_name(user), tanggal, sekarang.isoformat())
    )
    conn.commit()
    conn.close()

    teks = (
        f"{header(user)}"
        f"✅ Absensi berhasil: Masuk kerja - {format_waktu(sekarang)}\n\n"
        f"Pengingat: Jangan lupa melakukan absensi pulang kerja saat selesai bekerja.\n"
        f"{footer()}"
    )
    await query.message.reply_text(teks)


async def handle_off_work(query, user):
    """Proses tombol OFF WORK."""
    conn = get_conn()
    c = conn.cursor()
    tanggal = get_tanggal_hari_ini()
    sekarang = now()

    # Cek apakah sudah absen masuk
    absensi = get_absensi_hari_ini(user.id)
    if not absensi or not absensi[5]:
        await query.message.reply_text(
            f"{header(user)}"
            f"⚠️ Kamu belum melakukan absensi masuk kerja hari ini!\n\n"
            f"{footer()}"
        )
        conn.close()
        return

    if absensi[6]:  # Sudah off work
        await query.message.reply_text(
            f"{header(user)}"
            f"⚠️ Kamu sudah melakukan absensi pulang hari ini!\n\n"
            f"{footer()}"
        )
        conn.close()
        return

    # Tutup aktivitas yang masih berjalan (jika ada)
    aktivitas_berjalan = get_aktivitas_terakhir(user.id)
    if aktivitas_berjalan:
        waktu_mulai = datetime.fromisoformat(aktivitas_berjalan[5])
        durasi = (sekarang - waktu_mulai).total_seconds()
        c.execute(
            "UPDATE aktivitas SET waktu_selesai=?, durasi_detik=? WHERE id=?",
            (sekarang.isoformat(), int(durasi), aktivitas_berjalan[0])
        )

    # Update waktu off_work
    c.execute(
        "UPDATE absensi SET off_work=? WHERE user_id=? AND tanggal=?",
        (sekarang.isoformat(), user.id, tanggal)
    )
    conn.commit()
    conn.close()

    # Hitung durasi kerja
    start_dt = datetime.fromisoformat(absensi[5])
    total_detik = (sekarang - start_dt).total_seconds()

    # Hitung aktivitas
    _, total_aktivitas_detik = hitung_total_semua_aktivitas(user.id)
    waktu_bersih_detik = total_detik - total_aktivitas_detik

    # Detail per aktivitas
    jumlah_toilet, durasi_toilet = hitung_total_aktivitas(user.id, "TOILET")
    jumlah_eat, durasi_eat = hitung_total_aktivitas(user.id, "EAT")
    jumlah_smoke, durasi_smoke = hitung_total_aktivitas(user.id, "SMOKE")

    # Cek pulang lebih awal
    lebih_awal, selisih_detik = cek_pulang_lebih_awal()

    teks = f"{header(user)}"

    if lebih_awal:
        teks += (
            f"⚠️ Peringatan: Anda telah pulang lebih awal!\n"
            f"Durasi pulang lebih awal: {format_durasi(selisih_detik)}\n"
            f"Catatan: Kejadian pulang lebih awal ini telah dicatat.\n\n"
        )

    teks += (
        f"✅ Absensi berhasil: Pulang kerja - {format_waktu(sekarang)}\n\n"
        f"Catatan: Jam kerja hari ini telah dihitung.\n\n"
        f"Total waktu kerja hari ini: {format_durasi(total_detik)}\n"
        f"Waktu kerja bersih: {format_durasi(waktu_bersih_detik)}\n\n"
        f"Total waktu aktivitas hari ini: {format_durasi(total_aktivitas_detik)}\n"
    )

    if jumlah_toilet > 0:
        teks += (
            f"Total jumlah ke toilet hari ini: {jumlah_toilet} kali\n"
            f"Total waktu di toilet hari ini: {format_durasi(durasi_toilet)}\n"
        )
    if jumlah_eat > 0:
        teks += (
            f"Total jumlah makan hari ini: {jumlah_eat} kali\n"
            f"Total waktu makan hari ini: {format_durasi(durasi_eat)}\n"
        )
    if jumlah_smoke > 0:
        teks += (
            f"Total jumlah merokok hari ini: {jumlah_smoke} kali\n"
            f"Total waktu merokok hari ini: {format_durasi(durasi_smoke)}\n"
        )

    teks += footer()
    await query.message.reply_text(teks)


async def handle_aktivitas(query, user, jenis, label):
    """Proses tombol EAT / SMOKE / TOILET."""
    conn = get_conn()
    c = conn.cursor()
    tanggal = get_tanggal_hari_ini()
    sekarang = now()

    # Cek apakah sudah absen masuk
    absensi = get_absensi_hari_ini(user.id)
    if not absensi or not absensi[5]:
        await query.message.reply_text(
            f"{header(user)}"
            f"⚠️ Kamu belum absen masuk kerja hari ini!\n\n"
            f"{footer()}"
        )
        conn.close()
        return

    # Cek apakah ada aktivitas yang sedang berjalan
    aktivitas_berjalan = get_aktivitas_terakhir(user.id)
    if aktivitas_berjalan:
        jenis_berjalan = aktivitas_berjalan[3]
        await query.message.reply_text(
            f"{header(user)}"
            f"⚠️ Kamu masih dalam aktivitas: *{jenis_berjalan}*\n"
            f"Harap tekan tombol BACK terlebih dahulu!\n\n"
            f"{footer()}",
            parse_mode="Markdown"
        )
        conn.close()
        return

    # Hitung sudah berapa kali aktivitas ini hari ini
    jumlah_hari_ini, _ = hitung_total_aktivitas(user.id, jenis)
    kali_ini = jumlah_hari_ini + 1

    # Simpan aktivitas baru
    c.execute(
        """INSERT INTO aktivitas (user_id, tanggal, jenis, waktu_mulai)
           VALUES (?, ?, ?, ?)""",
        (user.id, tanggal, jenis, sekarang.isoformat())
    )
    conn.commit()
    conn.close()

    teks = (
        f"{header(user)}"
        f"✅ Absensi berhasil: {label} - {format_waktu(sekarang)}\n\n"
        f"Perhatian: Ini adalah kali ke-{kali_ini} Anda {label.lower()} hari ini.\n\n"
        f"Pengingat: Setelah selesai, harap segera melakukan absensi kembali ke tempat kerja.\n\n"
        f"Kembali ke tempat kerja: /back\n"
        f"{footer()}"
    )
    await query.message.reply_text(teks)


async def handle_back(query, user):
    """Proses tombol BACK."""
    conn = get_conn()
    c = conn.cursor()
    sekarang = now()

    # Cari aktivitas yang sedang berjalan
    aktivitas = get_aktivitas_terakhir(user.id)
    if not aktivitas:
        await query.message.reply_text(
            f"{header(user)}"
            f"⚠️ Tidak ada aktivitas yang sedang berjalan.\n\n"
            f"{footer()}"
        )
        conn.close()
        return

    # Hitung durasi
    waktu_mulai = datetime.fromisoformat(aktivitas[5])
    durasi_detik = (sekarang - waktu_mulai).total_seconds()
    jenis = aktivitas[3]

    # Mapping label
    label_map = {
        "EAT": "makan",
        "SMOKE": "merokok",
        "TOILET": "ke toilet"
    }
    label = label_map.get(jenis, jenis.lower())

    # Update waktu selesai
    c.execute(
        "UPDATE aktivitas SET waktu_selesai=?, durasi_detik=? WHERE id=?",
        (sekarang.isoformat(), int(durasi_detik), aktivitas[0])
    )
    conn.commit()
    conn.close()

    # Hitung total aktivitas jenis ini
    jumlah, total_detik_jenis = hitung_total_aktivitas(user.id, jenis)
    _, total_semua_detik = hitung_total_semua_aktivitas(user.id)

    teks = (
        f"{header(user)}"
        f"✅ {format_waktu(sekarang)} – Absensi kembali ke tempat kerja berhasil: Dari aktivitas {label}\n\n"
        f"Durasi aktivitas kali ini: {format_durasi(durasi_detik)}\n"
        f"Total waktu {label} hari ini: {format_durasi(total_detik_jenis)}\n"
        f"Total waktu seluruh aktivitas hari ini: {format_durasi(total_semua_detik)}\n\n"
        f"Jumlah {label} hari ini: {jumlah} kali\n"
        f"{footer()}"
    )
    await query.message.reply_text(teks)


# ============================================================
# MAIN — JALANKAN BOT
# ============================================================
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    # Command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))

    # Callback handler untuk tombol inline
    app.add_handler(CallbackQueryHandler(handle_callback))

    print("✅ Bot berjalan...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
=======
import logging
import sqlite3
from datetime import datetime, timedelta
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)

# ============================================================
# KONFIGURASI — EDIT BAGIAN INI
# ============================================================
BOT_TOKEN = "8663072880:AAHUBNkjv4Jrf7H2B3KCR-a98B1Qu4MhKxY"
TIMEZONE = pytz.timezone("Asia/Jakarta")  # Ganti sesuai timezone kamu
WORK_START_HOUR = 12      # Jam mulai kerja (format 24 jam)
WORK_START_MINUTE = 0
WORK_END_HOUR = 21       # Jam selesai kerja (format 24 jam)
WORK_END_MINUTE = 0
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ============================================================
# DATABASE SETUP
# ============================================================
def init_db():
    conn = sqlite3.connect("absensi.db")
    c = conn.cursor()

    # Tabel utama absensi harian
    c.execute("""
        CREATE TABLE IF NOT EXISTS absensi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            full_name TEXT,
            tanggal TEXT,
            start_work TEXT,
            off_work TEXT,
            status TEXT DEFAULT 'active'
        )
    """)

    # Tabel aktivitas (EAT, TOILET, SMOKE, BACK)
    c.execute("""
        CREATE TABLE IF NOT EXISTS aktivitas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            tanggal TEXT,
            jenis TEXT,
            waktu_mulai TEXT,
            waktu_selesai TEXT,
            durasi_detik INTEGER DEFAULT 0
        )
    """)

    conn.commit()
    conn.close()


def get_conn():
    return sqlite3.connect("absensi.db")


# ============================================================
# HELPER FUNCTIONS
# ============================================================
def now():
    """Waktu sekarang sesuai timezone."""
    return datetime.now(TIMEZONE)


def format_waktu(dt):
    """Format: 31/05 09:57:31"""
    return dt.strftime("%d/%m %H:%M:%S")


def format_durasi(detik):
    """Konversi detik ke format: X jam Y menit Z detik"""
    detik = int(detik)
    jam = detik // 3600
    menit = (detik % 3600) // 60
    sisa_detik = detik % 60
    if jam > 0:
        return f"{jam} jam {menit} menit {sisa_detik} detik"
    elif menit > 0:
        return f"{menit} menit {sisa_detik} detik"
    else:
        return f"{sisa_detik} detik"


def get_display_name(user):
    """Ambil nama tampilan user."""
    if user.last_name:
        return f"{user.first_name} {user.last_name}"
    return user.first_name


def footer():
    return (
        "---------------------------------------\n"
        "Lisensi bot sepenuhnya untuk kepentingan perusahaan, tidak untuk diperjual belikan.\n"
        "- Hongshu"
    )


def header(user):
    return (
        f"Pengguna: {get_display_name(user)}\n"
        f"ID Pengguna: {user.id}\n\n"
    )


def get_tanggal_hari_ini():
    return now().strftime("%Y-%m-%d")


def get_absensi_hari_ini(user_id):
    """Ambil data absensi hari ini untuk user tertentu."""
    conn = get_conn()
    c = conn.cursor()
    tanggal = get_tanggal_hari_ini()
    c.execute(
        "SELECT * FROM absensi WHERE user_id=? AND tanggal=?",
        (user_id, tanggal)
    )
    row = c.fetchone()
    conn.close()
    return row


def get_aktivitas_terakhir(user_id):
    """Ambil aktivitas terakhir yang belum selesai (waktu_selesai NULL)."""
    conn = get_conn()
    c = conn.cursor()
    tanggal = get_tanggal_hari_ini()
    c.execute(
        """SELECT * FROM aktivitas 
           WHERE user_id=? AND tanggal=? AND waktu_selesai IS NULL
           ORDER BY id DESC LIMIT 1""",
        (user_id, tanggal)
    )
    row = c.fetchone()
    conn.close()
    return row


def hitung_total_aktivitas(user_id, jenis=None):
    """Hitung total durasi dan jumlah aktivitas hari ini."""
    conn = get_conn()
    c = conn.cursor()
    tanggal = get_tanggal_hari_ini()

    if jenis:
        c.execute(
            """SELECT COUNT(*), COALESCE(SUM(durasi_detik), 0)
               FROM aktivitas
               WHERE user_id=? AND tanggal=? AND jenis=? AND waktu_selesai IS NOT NULL""",
            (user_id, tanggal, jenis)
        )
    else:
        c.execute(
            """SELECT COUNT(*), COALESCE(SUM(durasi_detik), 0)
               FROM aktivitas
               WHERE user_id=? AND tanggal=? AND waktu_selesai IS NOT NULL""",
            (user_id, tanggal)
        )
    row = c.fetchone()
    conn.close()
    return row[0], row[1]  # (jumlah, total_detik)


def hitung_total_semua_aktivitas(user_id):
    """Hitung total semua aktivitas (EAT, TOILET, SMOKE) hari ini."""
    conn = get_conn()
    c = conn.cursor()
    tanggal = get_tanggal_hari_ini()
    c.execute(
        """SELECT COUNT(*), COALESCE(SUM(durasi_detik), 0)
           FROM aktivitas
           WHERE user_id=? AND tanggal=? AND waktu_selesai IS NOT NULL""",
        (user_id, tanggal)
    )
    row = c.fetchone()
    conn.close()
    return row[0], row[1]


def hitung_waktu_kerja(user_id):
    """Hitung total waktu kerja dan waktu bersih."""
    absensi = get_absensi_hari_ini(user_id)
    if not absensi or not absensi[5] or not absensi[6]:
        return 0, 0

    start = datetime.fromisoformat(absensi[5])
    end = datetime.fromisoformat(absensi[6])
    total_detik = (end - start).total_seconds()

    _, total_aktivitas_detik = hitung_total_semua_aktivitas(user_id)
    waktu_bersih = total_detik - total_aktivitas_detik

    return total_detik, waktu_bersih


def cek_pulang_lebih_awal():
    """Cek apakah pulang lebih awal dari jadwal."""
    sekarang = now()
    jadwal_pulang = sekarang.replace(
        hour=WORK_END_HOUR,
        minute=WORK_END_MINUTE,
        second=0,
        microsecond=0
    )
    if sekarang < jadwal_pulang:
        selisih = (jadwal_pulang - sekarang).total_seconds()
        return True, selisih
    return False, 0


def main_menu_keyboard():
    """Buat tombol inline keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("🟢 START WORK", callback_data="start_work"),
            InlineKeyboardButton("🔴 OFF WORK", callback_data="off_work"),
        ],
        [
            InlineKeyboardButton("🍽️ EAT", callback_data="eat"),
            InlineKeyboardButton("🚬 SMOKE", callback_data="smoke"),
        ],
        [
            InlineKeyboardButton("🚻 TOILET", callback_data="toilet"),
            InlineKeyboardButton("🔙 BACK", callback_data="back"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


# ============================================================
# COMMAND HANDLERS
# ============================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk command /start — tampilkan menu absensi."""
    await update.message.reply_text(
        "🏢 *Sistem Absensi Perusahaan Hongshu*\n\n"
        "Silakan pilih aktivitas kamu:",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown"
    )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk command /menu — tampilkan ulang tombol."""
    await update.message.reply_text(
        "📋 *Menu Absensi:*",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown"
    )


# ============================================================
# CALLBACK HANDLERS (Tombol Ditekan)
# ============================================================
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # Hapus loading indicator

    user = query.from_user
    action = query.data

    if action == "start_work":
        await handle_start_work(query, user)
    elif action == "off_work":
        await handle_off_work(query, user)
    elif action == "eat":
        await handle_aktivitas(query, user, "EAT", "Makan")
    elif action == "smoke":
        await handle_aktivitas(query, user, "SMOKE", "Merokok")
    elif action == "toilet":
        await handle_aktivitas(query, user, "TOILET", "Ke toilet")
    elif action == "back":
        await handle_back(query, user)


async def handle_start_work(query, user):
    """Proses tombol START WORK."""
    conn = get_conn()
    c = conn.cursor()
    tanggal = get_tanggal_hari_ini()
    sekarang = now()

    # Cek apakah sudah absen masuk hari ini
    existing = get_absensi_hari_ini(user.id)
    if existing and existing[5]:  # kolom start_work tidak kosong
        await query.message.reply_text(
            f"{header(user)}"
            f"⚠️ Kamu sudah melakukan absensi masuk hari ini!\n"
            f"Waktu masuk: {format_waktu(datetime.fromisoformat(existing[5]))}\n\n"
            f"{footer()}"
        )
        conn.close()
        return

    # Simpan ke database
    c.execute(
        """INSERT INTO absensi (user_id, username, full_name, tanggal, start_work, status)
           VALUES (?, ?, ?, ?, ?, 'active')""",
        (user.id, user.username, get_display_name(user), tanggal, sekarang.isoformat())
    )
    conn.commit()
    conn.close()

    teks = (
        f"{header(user)}"
        f"✅ Absensi berhasil: Masuk kerja - {format_waktu(sekarang)}\n\n"
        f"Pengingat: Jangan lupa melakukan absensi pulang kerja saat selesai bekerja.\n"
        f"{footer()}"
    )
    await query.message.reply_text(teks)


async def handle_off_work(query, user):
    """Proses tombol OFF WORK."""
    conn = get_conn()
    c = conn.cursor()
    tanggal = get_tanggal_hari_ini()
    sekarang = now()

    # Cek apakah sudah absen masuk
    absensi = get_absensi_hari_ini(user.id)
    if not absensi or not absensi[5]:
        await query.message.reply_text(
            f"{header(user)}"
            f"⚠️ Kamu belum melakukan absensi masuk kerja hari ini!\n\n"
            f"{footer()}"
        )
        conn.close()
        return

    if absensi[6]:  # Sudah off work
        await query.message.reply_text(
            f"{header(user)}"
            f"⚠️ Kamu sudah melakukan absensi pulang hari ini!\n\n"
            f"{footer()}"
        )
        conn.close()
        return

    # Tutup aktivitas yang masih berjalan (jika ada)
    aktivitas_berjalan = get_aktivitas_terakhir(user.id)
    if aktivitas_berjalan:
        waktu_mulai = datetime.fromisoformat(aktivitas_berjalan[5])
        durasi = (sekarang - waktu_mulai).total_seconds()
        c.execute(
            "UPDATE aktivitas SET waktu_selesai=?, durasi_detik=? WHERE id=?",
            (sekarang.isoformat(), int(durasi), aktivitas_berjalan[0])
        )

    # Update waktu off_work
    c.execute(
        "UPDATE absensi SET off_work=? WHERE user_id=? AND tanggal=?",
        (sekarang.isoformat(), user.id, tanggal)
    )
    conn.commit()
    conn.close()

    # Hitung durasi kerja
    start_dt = datetime.fromisoformat(absensi[5])
    total_detik = (sekarang - start_dt).total_seconds()

    # Hitung aktivitas
    _, total_aktivitas_detik = hitung_total_semua_aktivitas(user.id)
    waktu_bersih_detik = total_detik - total_aktivitas_detik

    # Detail per aktivitas
    jumlah_toilet, durasi_toilet = hitung_total_aktivitas(user.id, "TOILET")
    jumlah_eat, durasi_eat = hitung_total_aktivitas(user.id, "EAT")
    jumlah_smoke, durasi_smoke = hitung_total_aktivitas(user.id, "SMOKE")

    # Cek pulang lebih awal
    lebih_awal, selisih_detik = cek_pulang_lebih_awal()

    teks = f"{header(user)}"

    if lebih_awal:
        teks += (
            f"⚠️ Peringatan: Anda telah pulang lebih awal!\n"
            f"Durasi pulang lebih awal: {format_durasi(selisih_detik)}\n"
            f"Catatan: Kejadian pulang lebih awal ini telah dicatat.\n\n"
        )

    teks += (
        f"✅ Absensi berhasil: Pulang kerja - {format_waktu(sekarang)}\n\n"
        f"Catatan: Jam kerja hari ini telah dihitung.\n\n"
        f"Total waktu kerja hari ini: {format_durasi(total_detik)}\n"
        f"Waktu kerja bersih: {format_durasi(waktu_bersih_detik)}\n\n"
        f"Total waktu aktivitas hari ini: {format_durasi(total_aktivitas_detik)}\n"
    )

    if jumlah_toilet > 0:
        teks += (
            f"Total jumlah ke toilet hari ini: {jumlah_toilet} kali\n"
            f"Total waktu di toilet hari ini: {format_durasi(durasi_toilet)}\n"
        )
    if jumlah_eat > 0:
        teks += (
            f"Total jumlah makan hari ini: {jumlah_eat} kali\n"
            f"Total waktu makan hari ini: {format_durasi(durasi_eat)}\n"
        )
    if jumlah_smoke > 0:
        teks += (
            f"Total jumlah merokok hari ini: {jumlah_smoke} kali\n"
            f"Total waktu merokok hari ini: {format_durasi(durasi_smoke)}\n"
        )

    teks += footer()
    await query.message.reply_text(teks)


async def handle_aktivitas(query, user, jenis, label):
    """Proses tombol EAT / SMOKE / TOILET."""
    conn = get_conn()
    c = conn.cursor()
    tanggal = get_tanggal_hari_ini()
    sekarang = now()

    # Cek apakah sudah absen masuk
    absensi = get_absensi_hari_ini(user.id)
    if not absensi or not absensi[5]:
        await query.message.reply_text(
            f"{header(user)}"
            f"⚠️ Kamu belum absen masuk kerja hari ini!\n\n"
            f"{footer()}"
        )
        conn.close()
        return

    # Cek apakah ada aktivitas yang sedang berjalan
    aktivitas_berjalan = get_aktivitas_terakhir(user.id)
    if aktivitas_berjalan:
        jenis_berjalan = aktivitas_berjalan[3]
        await query.message.reply_text(
            f"{header(user)}"
            f"⚠️ Kamu masih dalam aktivitas: *{jenis_berjalan}*\n"
            f"Harap tekan tombol BACK terlebih dahulu!\n\n"
            f"{footer()}",
            parse_mode="Markdown"
        )
        conn.close()
        return

    # Hitung sudah berapa kali aktivitas ini hari ini
    jumlah_hari_ini, _ = hitung_total_aktivitas(user.id, jenis)
    kali_ini = jumlah_hari_ini + 1

    # Simpan aktivitas baru
    c.execute(
        """INSERT INTO aktivitas (user_id, tanggal, jenis, waktu_mulai)
           VALUES (?, ?, ?, ?)""",
        (user.id, tanggal, jenis, sekarang.isoformat())
    )
    conn.commit()
    conn.close()

    teks = (
        f"{header(user)}"
        f"✅ Absensi berhasil: {label} - {format_waktu(sekarang)}\n\n"
        f"Perhatian: Ini adalah kali ke-{kali_ini} Anda {label.lower()} hari ini.\n\n"
        f"Pengingat: Setelah selesai, harap segera melakukan absensi kembali ke tempat kerja.\n\n"
        f"Kembali ke tempat kerja: /back\n"
        f"{footer()}"
    )
    await query.message.reply_text(teks)


async def handle_back(query, user):
    """Proses tombol BACK."""
    conn = get_conn()
    c = conn.cursor()
    sekarang = now()

    # Cari aktivitas yang sedang berjalan
    aktivitas = get_aktivitas_terakhir(user.id)
    if not aktivitas:
        await query.message.reply_text(
            f"{header(user)}"
            f"⚠️ Tidak ada aktivitas yang sedang berjalan.\n\n"
            f"{footer()}"
        )
        conn.close()
        return

    # Hitung durasi
    waktu_mulai = datetime.fromisoformat(aktivitas[5])
    durasi_detik = (sekarang - waktu_mulai).total_seconds()
    jenis = aktivitas[3]

    # Mapping label
    label_map = {
        "EAT": "makan",
        "SMOKE": "merokok",
        "TOILET": "ke toilet"
    }
    label = label_map.get(jenis, jenis.lower())

    # Update waktu selesai
    c.execute(
        "UPDATE aktivitas SET waktu_selesai=?, durasi_detik=? WHERE id=?",
        (sekarang.isoformat(), int(durasi_detik), aktivitas[0])
    )
    conn.commit()
    conn.close()

    # Hitung total aktivitas jenis ini
    jumlah, total_detik_jenis = hitung_total_aktivitas(user.id, jenis)
    _, total_semua_detik = hitung_total_semua_aktivitas(user.id)

    teks = (
        f"{header(user)}"
        f"✅ {format_waktu(sekarang)} – Absensi kembali ke tempat kerja berhasil: Dari aktivitas {label}\n\n"
        f"Durasi aktivitas kali ini: {format_durasi(durasi_detik)}\n"
        f"Total waktu {label} hari ini: {format_durasi(total_detik_jenis)}\n"
        f"Total waktu seluruh aktivitas hari ini: {format_durasi(total_semua_detik)}\n\n"
        f"Jumlah {label} hari ini: {jumlah} kali\n"
        f"{footer()}"
    )
    await query.message.reply_text(teks)


# ============================================================
# MAIN — JALANKAN BOT
# ============================================================
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CallbackQueryHandler(handle_callback))

    print("✅ Bot berjalan...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

75d4fd65977d9fc568f08844dda5a513bde53255
