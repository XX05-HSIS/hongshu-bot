import logging
import sqlite3
from datetime import datetime
import pytz
import os
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, BotCommand, MenuButtonCommands
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# ============================================================
# KONFIGURASI
# ============================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "ISI_TOKEN_BOT_KAMU_DI_SINI")
TIMEZONE = pytz.timezone("Asia/Jakarta")
WORK_END_HOUR = 21
WORK_END_MINUTE = 0

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================
# DATABASE
# ============================================================
def init_db():
    conn = sqlite3.connect("absensi.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS absensi (
        id INTEGER PRIMARY KEY, user_id INTEGER, username TEXT, full_name TEXT,
        tanggal TEXT, start_work TEXT, off_work TEXT, status TEXT DEFAULT 'active'
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS aktivitas (
        id INTEGER PRIMARY KEY, user_id INTEGER, tanggal TEXT, jenis TEXT,
        waktu_mulai TEXT, waktu_selesai TEXT, durasi_detik INTEGER DEFAULT 0
    )''')
    conn.commit()
    conn.close()

def get_conn(): 
    return sqlite3.connect("absensi.db")

# ============================================================
# HELPER
# ============================================================
def now(): return datetime.now(TIMEZONE)

def format_waktu(dt):
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
        if dt.tzinfo is None: dt = TIMEZONE.localize(dt)
    return dt.strftime("%d/%m %H:%M:%S")

def format_durasi(detik):
    detik = int(detik)
    jam = detik // 3600
    menit = (detik % 3600) // 60
    sisa = detik % 60
    if jam > 0: return f"{jam} jam {menit} menit {sisa} detik"
    elif menit > 0: return f"{menit} menit {sisa} detik"
    return f"{sisa} detik"

def get_display_name(user):
    return f"{user.first_name} {user.last_name}" if user.last_name else user.first_name

def footer():
    return "---------------------------------------\nLisensi bot sepenuhnya untuk kepentingan perusahaan, tidak untuk diperjual belikan.\n- Hongshu"

def header(user):
    return f"Pengguna: {get_display_name(user)}\nID: {user.id}\n\n"

def get_tanggal_hari_ini():
    return now().strftime("%Y-%m-%d")

def get_absensi_hari_ini(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM absensi WHERE user_id=? AND tanggal=?", (user_id, get_tanggal_hari_ini()))
    row = c.fetchone()
    conn.close()
    return row

def get_aktivitas_berjalan(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""SELECT * FROM aktivitas WHERE user_id=? AND tanggal=? AND waktu_selesai IS NULL 
                 ORDER BY id DESC LIMIT 1""", (user_id, get_tanggal_hari_ini()))
    row = c.fetchone()
    conn.close()
    return row

def hitung_aktivitas(user_id, jenis=None):
    conn = get_conn()
    c = conn.cursor()
    tanggal = get_tanggal_hari_ini()
    if jenis:
        c.execute("""SELECT COUNT(*), COALESCE(SUM(durasi_detik), 0) FROM aktivitas 
                     WHERE user_id=? AND tanggal=? AND jenis=? AND waktu_selesai IS NOT NULL""",
                  (user_id, tanggal, jenis))
    else:
        c.execute("""SELECT COUNT(*), COALESCE(SUM(durasi_detik), 0) FROM aktivitas 
                     WHERE user_id=? AND tanggal=? AND waktu_selesai IS NOT NULL""",
                  (user_id, tanggal))
    row = c.fetchone()
    conn.close()
    return row[0], row[1]

def cek_pulang_lebih_awal():
    sekarang = now()
    jadwal = sekarang.replace(hour=WORK_END_HOUR, minute=WORK_END_MINUTE, second=0, microsecond=0)
    if sekarang < jadwal:
        return True, (jadwal - sekarang).total_seconds()
    return False, 0

def reply_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("🟢 START WORK"), KeyboardButton("🔴 OFF WORK")],
        [KeyboardButton("🍽️ EAT"), KeyboardButton("🚬 SMOKE")],
        [KeyboardButton("🚻 TOILET"), KeyboardButton("🔙 BACK")],
        [KeyboardButton("📊 STATUS")]
    ], resize_keyboard=True)   # persistent dihapus

# ============================================================
# SETUP COMMANDS
# ============================================================
async def setup_bot_commands(application: Application):
    commands = [
        BotCommand("start", "Buka menu absensi"),
        BotCommand("menu", "Tampilkan keyboard"),
        BotCommand("status", "Cek status"),
    ]
    await application.bot.set_my_commands(commands)
    await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())

# ============================================================
# HANDLERS
# ============================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏢 **Sistem Absensi Hongshu**\n\nGunakan tombol di bawah ini:",
        reply_markup=reply_keyboard(),
        parse_mode="Markdown"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    absensi = get_absensi_hari_ini(user.id)
    if not absensi or not absensi[5]:
        teks = "⚠️ Kamu belum absen masuk hari ini."
    else:
        teks = f"✅ Masuk: {format_waktu(absensi[5])}\n"
        teks += f"✅ Pulang: {format_waktu(absensi[6])}" if absensi[6] else "⏳ Belum pulang"
    await update.message.reply_text(header(user) + teks + "\n\n" + footer())

# ============================================================
# MESSAGE HANDLER
# ============================================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = update.message.text.strip()
        user = update.message.from_user

        if text == "🟢 START WORK":
            await aksi_start_work_message(update, user)
        elif text == "🔴 OFF WORK":
            await aksi_off_work_message(update, user)
        elif text == "🍽️ EAT":
            await aksi_aktivitas_message(update, user, "EAT", "Makan")
        elif text == "🚬 SMOKE":
            await aksi_aktivitas_message(update, user, "SMOKE", "Merokok")
        elif text == "🚻 TOILET":
            await aksi_aktivitas_message(update, user, "TOILET", "Ke toilet")
        elif text == "🔙 BACK":
            await aksi_back_message(update, user)
        elif text == "📊 STATUS":
            await status(update, context)
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text("❌ Terjadi kesalahan. Coba lagi.")

# ============================================================
# AKSI FUNCTIONS
# ============================================================
async def aksi_start_work_message(update, user):
    sekarang = now()
    absensi = get_absensi_hari_ini(user.id)
    if absensi and absensi[5]:
        await update.message.reply_text(header(user) + "⚠️ Sudah absen masuk hari ini!\n\n" + footer(), 
                                      reply_to_message_id=update.message.message_id)
        return

    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT INTO absensi (user_id, username, full_name, tanggal, start_work) VALUES (?,?,?,?,?)",
              (user.id, user.username, get_display_name(user), get_tanggal_hari_ini(), sekarang.isoformat()))
    conn.commit()
    conn.close()

    await update.message.reply_text(
        header(user) + f"✅ Absen masuk berhasil: {format_waktu(sekarang)}\n\n{footer()}",
        reply_to_message_id=update.message.message_id
    )

async def aksi_off_work_message(update, user):
    sekarang = now()
    absensi = get_absensi_hari_ini(user.id)
    if not absensi or not absensi[5]:
        await update.message.reply_text(header(user) + "⚠️ Belum absen masuk hari ini!\n\n" + footer(), 
                                      reply_to_message_id=update.message.message_id)
        return
    if absensi[6]:
        await update.message.reply_text(header(user) + "⚠️ Sudah absen pulang hari ini!\n\n" + footer(), 
                                      reply_to_message_id=update.message.message_id)
        return

    # Tutup aktivitas yang berjalan
    aktif = get_aktivitas_berjalan(user.id)
    conn = get_conn()
    c = conn.cursor()
    if aktif:
        waktu_mulai = datetime.fromisoformat(aktif[4])
        if waktu_mulai.tzinfo is None: waktu_mulai = TIMEZONE.localize(waktu_mulai)
        durasi = (sekarang - waktu_mulai).total_seconds()
        c.execute("UPDATE aktivitas SET waktu_selesai=?, durasi_detik=? WHERE id=?", 
                  (sekarang.isoformat(), int(durasi), aktif[0]))

    c.execute("UPDATE absensi SET off_work=? WHERE user_id=? AND tanggal=?", 
              (sekarang.isoformat(), user.id, get_tanggal_hari_ini()))
    conn.commit()
    conn.close()

    await update.message.reply_text(header(user) + f"✅ Absen pulang berhasil: {format_waktu(sekarang)}\n\n{footer()}", 
                                  reply_to_message_id=update.message.message_id)

async def aksi_aktivitas_message(update, user, jenis, label):
    sekarang = now()
    absensi = get_absensi_hari_ini(user.id)
    if not absensi or not absensi[5]:
        await update.message.reply_text(header(user) + "⚠️ Belum absen masuk!\n\n" + footer(), 
                                      reply_to_message_id=update.message.message_id)
        return
    if absensi[6]:
        await update.message.reply_text(header(user) + "⚠️ Sudah absen pulang!\n\n" + footer(), 
                                      reply_to_message_id=update.message.message_id)
        return

    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT INTO aktivitas (user_id, tanggal, jenis, waktu_mulai) VALUES (?,?,?,?)",
              (user.id, get_tanggal_hari_ini(), jenis, sekarang.isoformat()))
    conn.commit()
    conn.close()

    await update.message.reply_text(
        header(user) + f"✅ {label} dicatat: {format_waktu(sekarang)}\nTekan BACK setelah selesai.\n\n{footer()}",
        reply_to_message_id=update.message.message_id
    )

async def aksi_back_message(update, user):
    sekarang = now()
    aktif = get_aktivitas_berjalan(user.id)
    if not aktif:
        await update.message.reply_text(header(user) + "⚠️ Tidak ada aktivitas yang berjalan.\n\n" + footer(), 
                                      reply_to_message_id=update.message.message_id)
        return

    waktu_mulai = datetime.fromisoformat(aktif[4])
    if waktu_mulai.tzinfo is None: waktu_mulai = TIMEZONE.localize(waktu_mulai)
    durasi_detik = (sekarang - waktu_mulai).total_seconds()
    jenis = aktif[3]

    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE aktivitas SET waktu_selesai=?, durasi_detik=? WHERE id=?", 
              (sekarang.isoformat(), int(durasi_detik), aktif[0]))
    conn.commit()
    conn.close()

    label_map = {"EAT": "makan", "SMOKE": "merokok", "TOILET": "ke toilet"}
    label = label_map.get(jenis, jenis.lower())

    await update.message.reply_text(
        header(user) + f"✅ Kembali ke kerja dari {label} - {format_waktu(sekarang)}\nDurasi: {format_durasi(durasi_detik)}\n\n{footer()}",
        reply_to_message_id=update.message.message_id
    )

# ============================================================
# MAIN
# ============================================================
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.post_init = setup_bot_commands

    print("✅ Bot Absensi Hongshu berjalan...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()