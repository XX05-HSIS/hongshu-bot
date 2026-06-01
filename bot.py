import logging
import sqlite3
from datetime import datetime, time, timedelta
import pytz
import os
import calendar
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, BotCommand, MenuButtonCommands
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters, JobQueue
)

# ============================================================
# KONFIGURASI — EDIT BAGIAN INI
# ============================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "ISI_TOKEN_BOT_KAMU_DI_SINI")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", None)   # ← WAJIB DIISI CHAT ID KAMU
TIMEZONE = pytz.timezone("Asia/Jakarta")
WORK_START_HOUR = 8      # Jam mulai kerja standar
WORK_END_HOUR = 21
WORK_END_MINUTE = 0
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============================================================
# DATABASE
# ============================================================
def init_db():
    conn = sqlite3.connect("absensi.db")
    c = conn.cursor()
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
# HELPER
# ============================================================
def now():
    return datetime.now(TIMEZONE)

def format_waktu(dt):
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
        if dt.tzinfo is None:
            dt = TIMEZONE.localize(dt)
    return dt.strftime("%d/%m %H:%M:%S")

def format_durasi(detik):
    detik = int(detik)
    jam = detik // 3600
    menit = (detik % 3600) // 60
    sisa = detik % 60
    if jam > 0:
        return f"{jam} jam {menit} menit {sisa} detik"
    elif menit > 0:
        return f"{menit} menit {sisa} detik"
    else:
        return f"{sisa} detik"

def get_display_name(user):
    if user.last_name:
        return f"{user.first_name} {user.last_name}"
    return user.first_name

def footer():
    return (
        "---------------------------------------\n"
        "Lisensi bot sepenuhnya untuk kepentingan perusahaan, "
        "tidak untuk diperjual belikan.\n"
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
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM absensi WHERE user_id=? AND tanggal=?", 
              (user_id, get_tanggal_hari_ini()))
    row = c.fetchone()
    conn.close()
    return row

def get_absensi_kemarin(user_id):
    kemarin = (now().date() - timedelta(days=1)).strftime("%Y-%m-%d")
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM absensi WHERE user_id=? AND tanggal=? AND off_work IS NULL", 
              (user_id, kemarin))
    row = c.fetchone()
    conn.close()
    return row

def get_aktivitas_berjalan(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """SELECT * FROM aktivitas
           WHERE user_id=? AND tanggal=? AND waktu_selesai IS NULL
           ORDER BY id DESC LIMIT 1""",
        (user_id, get_tanggal_hari_ini())
    )
    row = c.fetchone()
    conn.close()
    return row

def hitung_aktivitas(user_id, jenis=None):
    conn = get_conn()
    c = conn.cursor()
    tanggal = get_tanggal_hari_ini()
    if jenis:
        c.execute(
            """SELECT COUNT(*), COALESCE(SUM(durasi_detik), 0)
               FROM aktivitas
               WHERE user_id=? AND tanggal=? AND jenis=?
               AND waktu_selesai IS NOT NULL""",
            (user_id, tanggal, jenis)
        )
    else:
        c.execute(
            """SELECT COUNT(*), COALESCE(SUM(durasi_detik), 0)
               FROM aktivitas
               WHERE user_id=? AND tanggal=?
               AND waktu_selesai IS NOT NULL""",
            (user_id, tanggal)
        )
    row = c.fetchone()
    conn.close()
    return row[0], row[1]

def cek_pulang_lebih_awal():
    sekarang = now()
    jadwal = sekarang.replace(hour=WORK_END_HOUR, minute=WORK_END_MINUTE, second=0, microsecond=0)
    if sekarang < jadwal:
        return True, (jadwal - sekarang).total_seconds()
    return False, 0

# ============================================================
# LAPORAN HARIAN (00:00)
# ============================================================
async def daily_report(context: ContextTypes.DEFAULT_TYPE):
    if not ADMIN_CHAT_ID:
        return
    tanggal = (now().date() - timedelta(days=1)).strftime("%Y-%m-%d")
    
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT user_id, full_name, start_work, off_work FROM absensi WHERE tanggal=? ORDER BY full_name", (tanggal,))
    rows = c.fetchall()

    if not rows:
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, 
                                     text=f"📊 *Laporan Harian {tanggal}*\n\nTidak ada data absensi.", 
                                     parse_mode="Markdown")
        conn.close()
        return

    teks = f"📊 *LAPORAN HARIAN*\nTanggal: {tanggal}\n\n"
    for row in rows:
        user_id, full_name, start_work, off_work = row
        start_dt = TIMEZONE.localize(datetime.fromisoformat(start_work))
        
        if off_work:
            end_dt = TIMEZONE.localize(datetime.fromisoformat(off_work))
            total_detik = (end_dt - start_dt).total_seconds()
            pulang_text = format_waktu(end_dt)
        else:
            total_detik = (now() - start_dt).total_seconds()
            pulang_text = "BELUM PULANG ⚠️"

        c.execute("SELECT jenis, COUNT(*) FROM aktivitas WHERE user_id=? AND tanggal=? AND waktu_selesai IS NOT NULL GROUP BY jenis", 
                  (user_id, tanggal))
        aktivitas = dict(c.fetchall())

        keterangan = []
        if start_dt.hour >= WORK_START_HOUR + 1:
            keterangan.append("TELAT")
        if off_work and (end_dt.hour < WORK_END_HOUR):
            keterangan.append("PULANG AWAL")

        teks += f"👤 *{full_name}*\n"
        teks += f"   Masuk : {format_waktu(start_dt)}\n"
        teks += f"   Pulang: {pulang_text}\n"
        teks += f"   Kerja : {format_durasi(total_detik)}\n"
        teks += f"   Eat: {aktivitas.get('EAT',0)} | Smoke: {aktivitas.get('SMOKE',0)} | Toilet: {aktivitas.get('TOILET',0)}\n"
        teks += f"   Ket  : {' | '.join(keterangan) if keterangan else 'Normal'}\n\n"

    teks += footer()
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=teks, parse_mode="Markdown")
    conn.close()

# ============================================================
# LAPORAN BULANAN (tgl 30/31 jam 00:00)
# ============================================================
async def monthly_report(context: ContextTypes.DEFAULT_TYPE):
    if not ADMIN_CHAT_ID:
        return

    today = now().date()
    if today.day not in [30, 31]:
        return

    year = today.year
    month = today.month
    bulan_nama = today.strftime("%B %Y")

    conn = get_conn()
    c = conn.cursor()

    start_month = f"{year}-{month:02d}-01"
    end_month = f"{year}-{month:02d}-{calendar.monthrange(year, month)[1]}"

    teks = f"📈 *LAPORAN BULANAN*\nBulan: {bulan_nama}\n\n"

    # Ringkasan per minggu
    c.execute("SELECT DISTINCT tanggal FROM absensi WHERE tanggal BETWEEN ? AND ? ORDER BY tanggal", (start_month, end_month))
    dates = [row[0] for row in c.fetchall()]

    week_num = 1
    for i in range(0, len(dates), 7):
        week_dates = dates[i:i+7]
        if not week_dates:
            break
        teks += f"📅 *Minggu {week_num}* ({week_dates[0]} s/d {week_dates[-1]})\n"
        for tgl in week_dates:
            c.execute("SELECT COUNT(DISTINCT user_id) FROM absensi WHERE tanggal=?", (tgl,))
            hadir = c.fetchone()[0]
            teks += f"   {tgl}: {hadir} orang\n"
        teks += "\n"
        week_num += 1

    # Total Bulanan
    c.execute("SELECT COUNT(DISTINCT user_id), COUNT(*) FROM absensi WHERE tanggal BETWEEN ? AND ?", (start_month, end_month))
    total_user, total_record = c.fetchone()

    teks += f"📊 *TOTAL BULAN INI*\n"
    teks += f"Total Hari Kerja: {len(dates)}\n"
    teks += f"Total Karyawan Absen: {total_user}\n"
    teks += f"Total Record Absensi: {total_record}\n\n"
    teks += footer()

    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=teks, parse_mode="Markdown")
    conn.close()

# ============================================================
# REPLY KEYBOARD
# ============================================================
def reply_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("🟢 START WORK"), KeyboardButton("🔴 OFF WORK")],
        [KeyboardButton("🍽️ EAT"), KeyboardButton("🚬 SMOKE")],
        [KeyboardButton("🚻 TOILET"), KeyboardButton("🔙 BACK")],
        [KeyboardButton("📊 STATUS")]
    ], resize_keyboard=True)

# ============================================================
# SETUP
# ============================================================
async def setup_bot_commands(application: Application):
    commands = [
        BotCommand("start", "Buka menu absensi"),
        BotCommand("menu", "Tampilkan keyboard absensi"),
        BotCommand("status", "Cek status absensi"),
    ]
    await application.bot.set_my_commands(commands)
    await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())

# ============================================================
# COMMAND HANDLERS
# ============================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏢 *Sistem Absensi Hongshu*\n\nSilakan pilih aktivitas:",
        reply_markup=reply_keyboard(),
        parse_mode="Markdown"
    )

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 *Menu Absensi:*",
        reply_markup=reply_keyboard(),
        parse_mode="Markdown"
    )

# ============================================================
# MESSAGE HANDLER
# ============================================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
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

# ============================================================
# STATUS
# ============================================================
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    absensi = get_absensi_hari_ini(user.id)
    if not absensi or not absensi[5]:
        teks = "⚠️ Kamu belum absen masuk hari ini."
    else:
        teks = f"✅ Masuk: {format_waktu(absensi[5])}\n"
        teks += f"✅ Pulang: {format_waktu(absensi[6])}" if absensi[6] else "⏳ Belum pulang"
    await update.message.reply_text(header(user) + teks + "\n\n" + footer(), 
                                  reply_to_message_id=update.message.message_id)

# ============================================================
# START WORK (tanpa auto close)
# ============================================================
async def aksi_start_work_message(update, user):
    sekarang = now()
    absensi_hari_ini = get_absensi_hari_ini(user.id)
    
    absensi_kemarin = get_absensi_kemarin(user.id)
    if absensi_kemarin:
        await update.message.reply_text(
            f"{header(user)}⚠️ Peringatan: Kamu belum absen PULANG kemarin!\n"
            f"Silakan tekan OFF WORK jika ingin menutup absensi kemarin.\n\n",
            reply_to_message_id=update.message.message_id
        )

    conn = get_conn()
    c = conn.cursor()

    if absensi_hari_ini:
        if absensi_hari_ini[6]:
            await update.message.reply_text(
                f"{header(user)}⚠️ Hari ini sudah selesai (sudah absen pulang).\n"
                f"Tidak bisa mengubah waktu masuk lagi.\n\n{footer()}",
                reply_to_message_id=update.message.message_id
            )
            conn.close()
            return
        else:
            c.execute("UPDATE absensi SET start_work=? WHERE user_id=? AND tanggal=?",
                      (sekarang.isoformat(), user.id, get_tanggal_hari_ini()))
            conn.commit()
            conn.close()
            await update.message.reply_text(
                f"{header(user)}✅ Waktu masuk hari ini di-update: {format_waktu(sekarang)}\n\n{footer()}",
                reply_to_message_id=update.message.message_id
            )
            return

    c.execute("""INSERT INTO absensi 
                 (user_id, username, full_name, tanggal, start_work, status)
                 VALUES (?, ?, ?, ?, ?, 'active')""",
              (user.id, user.username, get_display_name(user), get_tanggal_hari_ini(), sekarang.isoformat()))
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"{header(user)}✅ Absensi berhasil: Masuk kerja - {format_waktu(sekarang)}\n\n"
        f"Jangan lupa absen pulang nanti.\n"
        f"{footer()}",
        reply_to_message_id=update.message.message_id
    )

# ============================================================
# OFF WORK, AKTIVITAS, BACK (sama seperti asli)
# ============================================================
async def aksi_off_work_message(update, user):
    sekarang = now()
    absensi = get_absensi_hari_ini(user.id)
    if not absensi or not absensi[5]:
        await update.message.reply_text(f"{header(user)}⚠️ Kamu belum absen masuk hari ini!\n\n{footer()}", 
                                      reply_to_message_id=update.message.message_id)
        return
    if absensi[6]:
        await update.message.reply_text(f"{header(user)}⚠️ Sudah absen pulang hari ini!\n\n{footer()}", 
                                      reply_to_message_id=update.message.message_id)
        return

    conn = get_conn()
    c = conn.cursor()
    aktif = get_aktivitas_berjalan(user.id)
    if aktif:
        waktu_mulai = datetime.fromisoformat(aktif[4])
        if waktu_mulai.tzinfo is None:
            waktu_mulai = TIMEZONE.localize(waktu_mulai)
        durasi = (sekarang - waktu_mulai).total_seconds()
        c.execute("UPDATE aktivitas SET waktu_selesai=?, durasi_detik=? WHERE id=?", 
                  (sekarang.isoformat(), int(durasi), aktif[0]))

    c.execute("UPDATE absensi SET off_work=? WHERE user_id=? AND tanggal=?", 
              (sekarang.isoformat(), user.id, get_tanggal_hari_ini()))
    conn.commit()
    conn.close()

    start_dt = datetime.fromisoformat(absensi[5])
    if start_dt.tzinfo is None:
        start_dt = TIMEZONE.localize(start_dt)
    total_detik = (sekarang - start_dt).total_seconds()
    _, total_aktivitas = hitung_aktivitas(user.id)
    waktu_bersih = total_detik - total_aktivitas
    lebih_awal, selisih = cek_pulang_lebih_awal()

    teks = f"{header(user)}"
    if lebih_awal:
        teks += f"⚠️ Peringatan: Anda pulang lebih awal!\nDurasi: {format_durasi(selisih)}\n\n"
    teks += f"✅ Pulang kerja - {format_waktu(sekarang)}\n\nTotal kerja: {format_durasi(total_detik)}\nBersih: {format_durasi(waktu_bersih)}\n\n{footer()}"

    await update.message.reply_text(teks, reply_to_message_id=update.message.message_id)

async def aksi_aktivitas_message(update, user, jenis, label):
    sekarang = now()
    absensi = get_absensi_hari_ini(user.id)
    if not absensi or not absensi[5]:
        await update.message.reply_text(f"{header(user)}⚠️ Belum absen masuk hari ini!\n\n{footer()}", 
                                      reply_to_message_id=update.message.message_id)
        return
    if absensi[6]:
        await update.message.reply_text(f"{header(user)}⚠️ Sudah pulang hari ini!\n\n{footer()}", 
                                      reply_to_message_id=update.message.message_id)
        return

    aktif = get_aktivitas_berjalan(user.id)
    if aktif:
        label_map = {"EAT": "makan", "SMOKE": "merokok", "TOILET": "ke toilet"}
        jenis_aktif = label_map.get(aktif[3], aktif[3].lower())
        await update.message.reply_text(
            f"{header(user)}⚠️ Kamu masih dalam aktivitas: *{jenis_aktif}*\nTekan BACK dulu!\n\n{footer()}",
            parse_mode="Markdown", reply_to_message_id=update.message.message_id
        )
        return

    jumlah_hari_ini, _ = hitung_aktivitas(user.id, jenis)
    kali_ini = jumlah_hari_ini + 1
    conn = get_conn()
    c = conn.cursor()
    c.execute("""INSERT INTO aktivitas (user_id, tanggal, jenis, waktu_mulai) VALUES (?,?,?,?)""",
              (user.id, get_tanggal_hari_ini(), jenis, sekarang.isoformat()))
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"{header(user)}✅ {label} dicatat - {format_waktu(sekarang)}\n\n"
        f"Ini kali ke-{kali_ini} {label.lower()} hari ini.\n"
        f"Setelah selesai tekan BACK.\n\n{footer()}",
        reply_to_message_id=update.message.message_id
    )

async def aksi_back_message(update, user):
    sekarang = now()
    aktif = get_aktivitas_berjalan(user.id)
    if not aktif:
        await update.message.reply_text(f"{header(user)}⚠️ Tidak ada aktivitas berjalan.\n\n{footer()}", 
                                      reply_to_message_id=update.message.message_id)
        return

    waktu_mulai = datetime.fromisoformat(aktif[4])
    if waktu_mulai.tzinfo is None:
        waktu_mulai = TIMEZONE.localize(waktu_mulai)
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

    jumlah, total_jenis = hitung_aktivitas(user.id, jenis)
    _, total_semua = hitung_aktivitas(user.id)

    await update.message.reply_text(
        f"{header(user)}✅ Kembali ke tempat kerja dari {label} - {format_waktu(sekarang)}\n\n"
        f"Durasi: {format_durasi(durasi_detik)}\n"
        f"Total {label}: {format_durasi(total_jenis)}\n"
        f"Total aktivitas: {format_durasi(total_semua)}\n\n{footer()}",
        reply_to_message_id=update.message.message_id
    )

# ============================================================
# MAIN
# ============================================================
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    if ADMIN_CHAT_ID:
        app.job_queue.run_daily(daily_report, time(0, 0, 0))
        app.job_queue.run_daily(monthly_report, time(0, 0, 0))
        print(f"✅ Laporan harian & bulanan diatur ke ADMIN_CHAT_ID: {ADMIN_CHAT_ID}")
    else:
        print("⚠️ ADMIN_CHAT_ID belum diisi!")

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.post_init = setup_bot_commands

    print("✅ Bot berjalan...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()