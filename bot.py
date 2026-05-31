import logging
import sqlite3
from datetime import datetime
import pytz
import os
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, ContextTypes
)

# ============================================================
# KONFIGURASI
# ============================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "ISI_TOKEN_BOT_KAMU")
TIMEZONE = pytz.timezone("Asia/Jakarta")
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
    c.execute(
        "SELECT * FROM absensi WHERE user_id=? AND tanggal=?",
        (user_id, get_tanggal_hari_ini())
    )
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
    jadwal = sekarang.replace(
        hour=WORK_END_HOUR,
        minute=WORK_END_MINUTE,
        second=0,
        microsecond=0
    )
    if sekarang < jadwal:
        return True, (jadwal - sekarang).total_seconds()
    return False, 0


# ============================================================
# COMMAND HANDLERS
# ============================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏢 *Sistem Absensi Hongshu*\n\n"
        "Gunakan perintah berikut:\n"
        "/start_work - Masuk kerja\n"
        "/off_work - Pulang kerja\n"
        "/eat - Makan\n"
        "/smoke - Merokok\n"
        "/toilet - Ke toilet\n"
        "/back - Kembali ke tempat kerja\n",
        parse_mode="Markdown"
    )


async def cmd_start_work(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    sekarang = now()
    absensi = get_absensi_hari_ini(user.id)

    if absensi and absensi[5]:
        await update.message.reply_text(
            f"{header(user)}"
            f"⚠️ Kamu sudah absen masuk hari ini!\n"
            f"Waktu masuk: {format_waktu(absensi[5])}\n\n"
            f"{footer()}"
        )
        return

    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """INSERT INTO absensi
           (user_id, username, full_name, tanggal, start_work, status)
           VALUES (?, ?, ?, ?, ?, 'active')""",
        (
            user.id,
            user.username,
            get_display_name(user),
            get_tanggal_hari_ini(),
            sekarang.isoformat()
        )
    )
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"{header(user)}"
        f"✅ Absensi berhasil: Masuk kerja - {format_waktu(sekarang)}\n\n"
        f"Pengingat: Jangan lupa melakukan absensi pulang kerja "
        f"saat selesai bekerja.\n"
        f"{footer()}"
    )


async def cmd_off_work(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    sekarang = now()
    absensi = get_absensi_hari_ini(user.id)

    if not absensi or not absensi[5]:
        await update.message.reply_text(
            f"{header(user)}"
            f"⚠️ Kamu belum absen masuk kerja hari ini!\n\n"
            f"{footer()}"
        )
        return

    if absensi[6]:
        await update.message.reply_text(
            f"{header(user)}"
            f"⚠️ Kamu sudah absen pulang hari ini!\n\n"
            f"{footer()}"
        )
        return

    conn = get_conn()
    c = conn.cursor()

    # Tutup aktivitas yang masih berjalan
    aktif = get_aktivitas_berjalan(user.id)
    if aktif:
        waktu_mulai = datetime.fromisoformat(aktif[4])
        durasi = (sekarang - waktu_mulai).total_seconds()
        c.execute(
            "UPDATE aktivitas SET waktu_selesai=?, durasi_detik=? WHERE id=?",
            (sekarang.isoformat(), int(durasi), aktif[0])
        )

    c.execute(
        "UPDATE absensi SET off_work=? WHERE user_id=? AND tanggal=?",
        (sekarang.isoformat(), user.id, get_tanggal_hari_ini())
    )
    conn.commit()
    conn.close()

    # Hitung durasi
    start_dt = datetime.fromisoformat(absensi[5])
    total_detik = (sekarang - start_dt).total_seconds()

    _, total_aktivitas = hitung_aktivitas(user.id)
    waktu_bersih = total_detik - total_aktivitas

    jumlah_toilet, durasi_toilet = hitung_aktivitas(user.id, "TOILET")
    jumlah_eat, durasi_eat = hitung_aktivitas(user.id, "EAT")
    jumlah_smoke, durasi_smoke = hitung_aktivitas(user.id, "SMOKE")

    lebih_awal, selisih = cek_pulang_lebih_awal()

    teks = f"{header(user)}"

    if lebih_awal:
        teks += (
            f"⚠️ Peringatan: Anda telah pulang lebih awal!\n"
            f"Durasi pulang lebih awal: {format_durasi(selisih)}\n"
            f"Catatan: Kejadian pulang lebih awal ini telah dicatat.\n\n"
        )

    teks += (
        f"✅ Absensi berhasil: Pulang kerja - {format_waktu(sekarang)}\n\n"
        f"Catatan: Jam kerja hari ini telah dihitung.\n\n"
        f"Total waktu kerja hari ini: {format_durasi(total_detik)}\n"
        f"Waktu kerja bersih: {format_durasi(waktu_bersih)}\n\n"
        f"Total waktu aktivitas hari ini: {format_durasi(total_aktivitas)}\n"
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
    await update.message.reply_text(teks)


async def cmd_aktivitas(update: Update, context: ContextTypes.DEFAULT_TYPE, jenis, label):
    user = update.message.from_user
    sekarang = now()
    absensi = get_absensi_hari_ini(user.id)

    if not absensi or not absensi[5]:
        await update.message.reply_text(
            f"{header(user)}"
            f"⚠️ Kamu belum absen masuk kerja hari ini!\n\n"
            f"{footer()}"
        )
        return

    aktif = get_aktivitas_berjalan(user.id)
    if aktif:
        label_map = {"EAT": "makan", "SMOKE": "merokok", "TOILET": "ke toilet"}
        jenis_aktif = label_map.get(aktif[3], aktif[3].lower())
        await update.message.reply_text(
            f"{header(user)}"
            f"⚠️ Kamu masih dalam aktivitas: *{jenis_aktif}*\n"
            f"Ketik /back terlebih dahulu!\n\n"
            f"{footer()}",
            parse_mode="Markdown"
        )
        return

    jumlah_hari_ini, _ = hitung_aktivitas(user.id, jenis)
    kali_ini = jumlah_hari_ini + 1

    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """INSERT INTO aktivitas (user_id, tanggal, jenis, waktu_mulai)
           VALUES (?, ?, ?, ?)""",
        (user.id, get_tanggal_hari_ini(), jenis, sekarang.isoformat())
    )
    conn.commit()
    conn.close()

    label_display = {
        "EAT": "makan",
        "SMOKE": "merokok",
        "TOILET": "ke toilet"
    }
    label_kali = label_display.get(jenis, label.lower())

    await update.message.reply_text(
        f"{header(user)}"
        f"✅ Absensi berhasil: {label} - {format_waktu(sekarang)}\n\n"
        f"Perhatian: Ini adalah kali ke-{kali_ini} Anda {label_kali} hari ini.\n\n"
        f"Pengingat: Setelah selesai, harap segera melakukan absensi "
        f"kembali ke tempat kerja.\n\n"
        f"Kembali ke tempat kerja: /back\n"
        f"{footer()}"
    )


async def cmd_eat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_aktivitas(update, context, "EAT", "Makan")


async def cmd_smoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_aktivitas(update, context, "SMOKE", "Merokok")


async def cmd_toilet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_aktivitas(update, context, "TOILET", "Ke toilet")


async def cmd_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    sekarang = now()
    aktif = get_aktivitas_berjalan(user.id)

    if not aktif:
        await update.message.reply_text(
            f"{header(user)}"
            f"⚠️ Tidak ada aktivitas yang sedang berjalan.\n\n"
            f"{footer()}"
        )
        return

    waktu_mulai = datetime.fromisoformat(aktif[4])
    durasi_detik = (sekarang - waktu_mulai).total_seconds()
    jenis = aktif[3]

    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "UPDATE aktivitas SET waktu_selesai=?, durasi_detik=? WHERE id=?",
        (sekarang.isoformat(), int(durasi_detik), aktif[0])
    )
    conn.commit()
    conn.close()

    label_map = {"EAT": "makan", "SMOKE": "merokok", "TOILET": "ke toilet"}
    label = label_map.get(jenis, jenis.lower())

    jumlah, total_jenis = hitung_aktivitas(user.id, jenis)
    _, total_semua = hitung_aktivitas(user.id)

    await update.message.reply_text(
        f"{header(user)}"
        f"✅ {format_waktu(sekarang)} – Absensi kembali ke tempat kerja berhasil: "
        f"Dari aktivitas {label}\n\n"
        f"Durasi aktivitas kali ini: {format_durasi(durasi_detik)}\n"
        f"Total waktu {label} hari ini: {format_durasi(total_jenis)}\n"
        f"Total waktu seluruh aktivitas hari ini: {format_durasi(total_semua)}\n\n"
        f"Jumlah {label} hari ini: {jumlah} kali\n"
        f"{footer()}"
    )


# ============================================================
# MAIN
# ============================================================
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("start_work", cmd_start_work))
    app.add_handler(CommandHandler("off_work", cmd_off_work))
    app.add_handler(CommandHandler("eat", cmd_eat))
    app.add_handler(CommandHandler("smoke", cmd_smoke))
    app.add_handler(CommandHandler("toilet", cmd_toilet))
    app.add_handler(CommandHandler("back", cmd_back))

    print("✅ Bot berjalan...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
