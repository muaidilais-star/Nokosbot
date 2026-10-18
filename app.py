import telebot
from telebot import types
import json, os, re, requests, uuid, time, threading
from flask import Flask, request

# ==========================================
# KONFIGURASI
# ==========================================
TOKEN = "8840935060:AAH439gSJo5e-cWJcgfHK-QrJfJXJs6kFOY"
ADMIN_ID = 8593863534
ADMIN_PASSWORD = "270807"
ADMIN_USERNAME = "@Aisz25"
NAMA_TOKO = "Toko Nokos AISz"
NOKOS_BASE_URL = "https://nokospusat.com/api/v1"
MARKUP = 500 
WAKTU_TUNGGU_OTP = 15 * 60 

# ==========================================
# DATABASE
# ==========================================
DB_FILE = "produk.json"
CONFIG_FILE = "konfigurasi.json"
USERS_FILE = "users.json"
ADMINS_FILE = "admins.json"
SALDO_FILE = "saldo.json"
ORDERS_FILE = "orders.json"

def load_json(file, default=[]):
    if os.path.exists(file):
        with open(file, "r") as f: return json.load(f)
    return default

def simpan_json(file, data):
    with open(file, "w") as f: json.dump(data, f, indent=2)

def load_produk(): return load_json(DB_FILE, {})
def simpan_produk(d): simpan_json(DB_FILE, d)
def load_config(): return load_json(CONFIG_FILE, {})
def simpan_config(d): simpan_json(CONFIG_FILE, d)

def load_users(): return load_json(USERS_FILE, {})
def simpan_user(uid, nama="Kak", username="-"):
    users = load_users()
    if isinstance(users, list): 
        users = {str(u): {"nama": "User Lama", "username": "-"} for u in users}
    users[str(uid)] = {"nama": nama, "username": f"@{username}" if username else "-"}
    simpan_json(USERS_FILE, users)

def load_admins():
    a = load_json(ADMINS_FILE, [])
    if ADMIN_ID not in a: a.append(ADMIN_ID)
    return a

def simpan_admins(d):
    if ADMIN_ID not in d: d.append(ADMIN_ID)
    simpan_json(ADMINS_FILE, d)

def load_orders(): return load_json(ORDERS_FILE, {})
def simpan_orders(d): simpan_json(ORDERS_FILE, d)

def get_saldo(uid):
    s = load_json(SALDO_FILE, {})
    return s.get(str(uid), 0)

def ubah_saldo(uid, nominal):
    s = load_json(SALDO_FILE, {})
    uid = str(uid)
    if uid not in s: s[uid] = 0
    s[uid] += nominal
    simpan_json(SALDO_FILE, s)

def esc(text):
    if not text: return ""
    return str(text).replace('_', '\\_').replace('*', '\\*').replace('`', '\\`').replace('[', '\\[')

bot = telebot.TeleBot(TOKEN)
state = {}

def is_admin(message): return message.from_user.id in load_admins()

def broadcast_to_admins(teks, **kwargs):
    for aid in load_admins():
        try: bot.send_message(aid, teks, **kwargs)
        except: pass

def nokos_api(method, endpoint, payload=None):
    c = load_config()
    if not c.get("nokos_api_key"): return {"error": "API Key belum diset."}
    url = f"{NOKOS_BASE_URL}{endpoint}"
    headers = {"Authorization": f"Bearer {c['nokos_api_key']}", "Accept": "application/json"}
    try:
        if method == "GET": r = requests.get(url, headers=headers, timeout=15)
        elif method == "POST":
            headers["Content-Type"] = "application/json"; headers["Idempotency-Key"] = str(uuid.uuid4())
            r = requests.post(url, headers=headers, json=payload, timeout=15)
        elif method == "DELETE": r = requests.delete(url, headers=headers, timeout=15)
        data = r.json()
        if r.status_code >= 400: return {"error": data.get("error", {}).get("message", "Gagal terhubung.")}
        return data
    except Exception as e: return {"error": str(e)}

def proses_refund(order_id, user_id, harga_jual, chat_id, msg_id, alasan):
    orders = load_orders()
    if orders.get(order_id) == "refunded": return 
    ubah_saldo(user_id, harga_jual)
    orders[order_id] = "refunded"
    simpan_orders(orders)
    teks = f"❌ *Pesanan Dibatalkan / Expired*\n\nID: `{order_id}`\nSaldo Rp {harga_jual:,} telah dikembalikan.\n\n*Info:* {alasan}"
    try: bot.edit_message_text(teks, chat_id, msg_id, parse_mode='Markdown')
    except: bot.send_message(user_id, teks, parse_mode='Markdown')
    broadcast_to_admins(f"⚠️ *PESANAN GAGAL/REFUND*\n\nUser: `{user_id}`\nID: `{order_id}`\nAlasan: {alasan}", parse_mode='Markdown')

def poll_otp(order_id, user_id, markup_harga, chat_id, msg_id):
    batas = time.time() + WAKTU_TUNGGU_OTP
    nomor_tampil = "Sedang dialokasikan..."
    while time.time() < batas:
        res = nokos_api("GET", f"/orders/{order_id}")
        if "error" in res: 
            time.sleep(10)
            continue
        data = res.get("data", {})
        status = data.get("status")
        otp = data.get("otp")
        nomor = data.get("phone_number") or data.get("number")
        if nomor: 
            nomor_tampil = f"`{nomor}`"
        if status == "completed" and otp:
            markup = types.InlineKeyboardMarkup()
            if data.get("can_request_next_otp"): 
                markup.add(types.InlineKeyboardButton("🔄 OTP Lagi", callback_data=f"nextotp_{order_id}"))
            markup.add(types.InlineKeyboardButton("✅ Selesai", callback_data=f"finish_{order_id}"))
            bot.send_message(user_id, f"✅ *OTP DITERIMA!*\n\nNomor: {nomor_tampil}\nKode OTP: `{otp}`", parse_mode='Markdown', reply_markup=markup)
            broadcast_to_admins(f"✅ *OTP BERHASIL KELUAR*\n\nUser: `{user_id}`\nID: `{order_id}`\nOTP: `{otp}`", parse_mode='Markdown')
            return
        elif status in ["cancelled", "expired"]:
            proses_refund(order_id, user_id, markup_harga, chat_id, msg_id, "Server gagal mendapat nomor.")
            return
        try:
            markup_batal = types.InlineKeyboardMarkup(row_width=2)
            markup_batal.add(types.InlineKeyboardButton("🔍 Cek OTP", callback_data=f"checkotp_{order_id}"), types.InlineKeyboardButton("❌ Batalkan", callback_data=f"cancel_{order_id}_{markup_harga}"))
            bot.edit_message_text(f"✅ *Pembelian Sukses!*\nID: `{order_id}`\nNomor: {nomor_tampil}\n\n⏳ Menunggu OTP (Maks 15 menit)...", chat_id, msg_id, parse_mode='Markdown', reply_markup=markup_batal)
        except: 
            pass
        jeda = data.get("next_check_after_seconds", 5)
        time.sleep(max(5, int(jeda)))
    nokos_api("DELETE", f"/orders/{order_id}") 
    proses_refund(order_id, user_id, markup_harga, chat_id, msg_id, "Waktu tunggu OTP (15 Menit) habis.")

def call_groq(api_key, sys, msg, tokens=300):
    for m in ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]:
        try:
            r = requests.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {api_key}"}, json={"model": m, "messages": [{"role": "system", "content": sys}, {"role": "user", "content": msg}], "max_tokens": tokens}, timeout=20)
            if r.status_code == 200: 
                return r.json()['choices'][0]['message']['content']
        except: 
            pass
    return None

def tanya_ai(pesan, produk, is_admin_mode=False):
    c = load_config()
    key = c.get("groq_api_key")
    if not key: return None
    pt = "".join([f"- {n}: {d['harga']} (Stok: {d['stok']})\n" for n, d in produk.items()]) if produk else "(kosong)"
    if is_admin_mode:
        sys = (f"Kamu asisten Admin toko nokos {NAMA_TOKO}. Santai, becanda.\nJANGAN (**).\n"
               f"Tugasmu: Membantu admin mengelola produk dan admin lain.\n"
               f"CATATAN PENTING: Admin Utama ID {ADMIN_ID} TIDAK BISA DIHAPUS.\n\n"
               f"FORMAT JSON di AKHIR balasan:\n"
               f'{{"action": "add", "nama": "Nama", "harga": "Rp X", "stok": "Y"}}\n'
               f'{{"action": "edit", "nama": "Nama", "harga": "Rp X", "stok": "Y"}}\n'
               f'{{"action": "delete", "nama": "Nama"}}\n'
               f'{{"action": "add_admin", "user_id": "123456789"}}\n'
               f'{{"action": "remove_admin", "user_id": "123456789"}}\n\n'
               f"Produk:\n{pt}")
        return call_groq(key, sys, pesan, 500)
    else:
        sys = (f"Kamu CS ramah {NAMA_TOKO}. Admin: {ADMIN_USERNAME}. Bahasa Indonesia santai, singkat.\nJangan (**).\n"
               f"Kalau user nanya OTP, suruh klik tombol '🔍 Cek OTP' di menu bawah.\n"
               f"Kalau mau beli: arahin ke 'Beli Nokos Otomatis'.\nProduk:\n{pt}")
        return call_groq(key, sys, pesan)

def is_user_joined(user_id):
    c = load_config()
    ch = c.get("channel_username")
    if not ch: return False
    try: 
        return bot.get_chat_member(ch, user_id).status in ['creator', 'administrator', 'member']
    except: 
        return False

def menu_user():
    m = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    m.add("🛒 Beli Nokos Otomatis", "📋 Katalog Manual")
    m.add("🔍 Cek OTP", "💰 Cek Saldo Saya")
    m.add("💳 Top Up Saldo", "🛒 Cara Order")
    m.add("📜 Syarat & Ketentuan", "📞 Hubungi Admin")
    return m

def menu_admin():
    m = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    m.add("➕ Tambah Produk", "✏️ Edit Produk", "🗑️ Hapus Produk", "📋 Lihat Produk")
    m.add("🖼️ Set Gambar QRIS", "⚙️ Atur Channel", "🤖 Pengaturan AI", "💬 Chat dengan AI")
    m.add("🔑 Set API Nokos", "💰 Cek Saldo Nokos", "👥 Kelola Admin", "📊 Statistik")
    m.add("💵 Tambah Saldo User", "📉 Kurangi Saldo User", "💾 Backup Data", "📢 Broadcast")
    m.add("📋 Semua Saldo User", "⬅️ Keluar Mode Admin")
    return m

MENU_BUTTONS = [
    "🛒 Beli Nokos Otomatis", "📋 Katalog Manual", "🔍 Cek OTP", "💰 Cek Saldo Saya", "💳 Top Up Saldo", "🛒 Cara Order", "📜 Syarat & Ketentuan", "📞 Hubungi Admin",
    "➕ Tambah Produk", "✏️ Edit Produk", "🗑️ Hapus Produk", "📋 Lihat Produk", "🖼️ Set Gambar QRIS", "⚙️ Atur Channel", "🤖 Pengaturan AI", "💬 Chat dengan AI",
    "🔑 Set API Nokos", "💰 Cek Saldo Nokos", "👥 Kelola Admin", "📊 Statistik", "💵 Tambah Saldo User", "📉 Kurangi Saldo User", "💾 Backup Data", "📢 Broadcast", "📋 Semua Saldo User", "⬅️ Keluar Mode Admin", "⬅️ Keluar Chat AI"
]

@bot.message_handler(commands=['start'])
def start(message):
    uid = message.from_user.id
    u = message.from_user
    nama_user = u.first_name or u.last_name or u.username or "Kak"
    if not is_admin(message): simpan_user(uid, nama_user, u.username)
    if is_admin(message): 
        state.pop(message.chat.id, None)
        bot.reply_to(message, "🔧 Mode Admin aktif.", reply_markup=menu_admin())
        return
    c = load_config()
    if not c.get("channel_username"): 
        bot.reply_to(message, "⚠️ Admin belum atur channel wajib join.")
        return
    if not is_user_joined(uid):
        m = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("➕ Gabung Channel", url=f"https://t.me/{c['channel_username'].replace('@', '')}")).add(types.InlineKeyboardButton("✅ Cek Status", callback_data="cek_join"))
        bot.reply_to(message, f"Halo *{esc(nama_user)}*! 👋\n\n⚠️ *WAJIB JOIN CHANNEL*\nKlik tombol di bawah, lalu *Cek Status*.", parse_mode='Markdown', reply_markup=m)
        return
    bot.reply_to(message, f"Halo *{esc(nama_user)}*! 👋\nSelamat datang di *{NAMA_TOKO}*.\nSaldo kamu: *Rp {get_saldo(uid):,}*", parse_mode='Markdown', reply_markup=menu_user())

@bot.callback_query_handler(func=lambda call: call.data == "cek_join")
def cek_join_cb(call):
    u = call.from_user
    nama_user = u.first_name or u.last_name or u.username or "Kak"
    if is_user_joined(u.id): 
        simpan_user(u.id, nama_user, u.username)
        bot.answer_callback_query(call.id, "✅ Berhasil join!")
        bot.send_message(call.message.chat.id, "✅ Silakan pilih menu.", reply_markup=menu_user())
    else: 
        bot.answer_callback_query(call.id, "❌ Belum join!", show_alert=True)

@bot.message_handler(commands=['admin'])
def cmd_admin(message):
    if is_admin(message): 
        state.pop(message.chat.id, None)
        bot.reply_to(message, "🔧 Mode Admin.", reply_markup=menu_admin())
    else: 
        bot.reply_to(message, "⛔ Bukan admin.")

@bot.message_handler(func=lambda m: m.text == "💰 Cek Saldo Saya")
def cek_saldo_user(message): 
    state.pop(message.chat.id, None)
    bot.reply_to(message, f"💰 *Saldo Kamu:*\n\nRp {get_saldo(message.from_user.id):,}", parse_mode='Markdown')

@bot.message_handler(func=lambda m: m.text == "🔍 Cek OTP" or m.text.lower() in ["otp", "cek otp"])
def cek_otp_last(message): 
    state.pop(message.chat.id, None)
    state[message.chat.id] = {"aksi": "cek_otp_id"}
    bot.reply_to(message, "🔍 *Cek OTP*\n\nSilakan ketik *ID Pesanan* kamu:", parse_mode='Markdown')

@bot.message_handler(func=lambda m: m.text == "💳 Top Up Saldo")
def topup_user(message):
    state.pop(message.chat.id, None)
    state[message.chat.id] = {"aksi": "waiting_topup"}
    c = load_config()
    qid = c.get("qris_file_id")
    teks = (f"💳 *TOP UP SALDO*\n\n1. Transfer ke QRIS di bawah.\n2. Screenshot bukti.\n3. Kirim gambar ke chat ini.\n\nAdmin akan memverifikasi.")
    if qid: 
        bot.send_photo(message.chat.id, qid, caption=teks, parse_mode='Markdown')
    else: 
        bot.reply_to(message, teks, parse_mode='Markdown')

@bot.message_handler(func=lambda m: m.text == "📋 Katalog Manual")
def katalog_manual(message):
    state.pop(message.chat.id, None)
    if not is_admin(message) and not is_user_joined(message.from_user.id): 
        bot.reply_to(message, "⚠️ Join channel dulu.")
        return
    produk = load_produk()
    if not produk: 
        bot.reply_to(message, "Katalog kosong.")
        return
    teks = f"📋 *DAFTAR HARGA - {NAMA_TOKO}*\n\n" + "".join([f"▪️ *{esc(n)}*\n   Harga: {esc(d.get('harga', '-'))}\n   Stok: {esc(d.get('stok', '-'))}\n\n" for n, d in produk.items()])
    bot.reply_to(message, teks, parse_mode='Markdown')

@bot.message_handler(func=lambda m: m.text == "🛒 Cara Order")
def cara(message): 
    state.pop(message.chat.id, None)
    bot.reply_to(message, "🛒 *CARA ORDER*\n\n1. Isi saldo via Top Up.\n2. Pilih Nokos Otomatis.\n3. Saldo otomatis terpotong.", parse_mode='Markdown')

@bot.message_handler(func=lambda m: m.text == "📜 Syarat & Ketentuan")
def sk(message):
    state.pop(message.chat.id, None)
    teks = ("📜 *SYARAT & KETENTUAN* 📜\n\n💳 *Pembayaran:* Wajib lunas di awal via Saldo.\n\n🔑 *Garansi OTP:* Garansi 100% jika OTP tidak masuk dalam 15 menit. Saldo dikembalikan penuh.\n\n⚠️ *Risiko Akun:* Banned/kenon setelah OTP diterima tanggung jawab pembeli.\n\n💸 *Refund:* Saldo kembali 100% tanpa potongan jika server gagal.")
    bot.reply_to(message, teks, parse_mode='Markdown')

@bot.message_handler(func=lambda m: m.text == "📞 Hubungi Admin")
def hubungi(message): 
    state.pop(message.chat.id, None)
    bot.reply_to(message, f"📞 Hubungi admin: {ADMIN_USERNAME}")

APLIKASI_POPULER = [("WhatsApp", "wa"), ("Telegram", "telegram"), ("TikTok", "tiktok"), ("YouTube", "youtube"), ("Google", "google"), ("Shopee", "shopee"), ("Gojek", "gojek"), ("Grab", "grab"), ("Facebook", "facebook"), ("Instagram", "instagram")]

@bot.message_handler(func=lambda m: m.text == "🛒 Beli Nokos Otomatis")
def beli_nokos_start(message):
    state.pop(message.chat.id, None)
    if not is_admin(message) and not is_user_joined(message.from_user.id): 
        bot.reply_to(message, "⚠️ Join channel dulu.")
        return
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(*[types.InlineKeyboardButton(nama, callback_data=f"cari_app_{query}") for nama, query in APLIKASI_POPULER])
    markup.add(types.InlineKeyboardButton("🔍 Cari Layanan Lain (Ketik Manual)", callback_data="cari_manual"))
    bot.reply_to(message, "🛒 *BELI NOKOS OTOMATIS*\n\nSilakan pilih aplikasi/layanan:", parse_mode='Markdown', reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("cari_app_"))
def cari_app_cb(call):
    query = call.data.split("_")[2]
    chat_id = call.message.chat.id
    msg_id = call.message.message_id
    bot.answer_callback_query(call.id, f"Mencari {query.capitalize()}...")
    bot.edit_message_text(f"⏳ Mencari layanan {query.capitalize()}...", chat_id, msg_id)
    res = nokos_api("GET", f"/services?q={query}&limit=5")
    if "error" in res: 
        bot.edit_message_text(f"❌ Gagal: {res['error']}", chat_id, msg_id)
        return
    services = res.get("data", [])
    if not services: 
        markup_back = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Kembali", callback_data="back_apps"))
        bot.edit_message_text(f"❌ Layanan {query.capitalize()} tidak ditemukan.", chat_id, msg_id, reply_markup=markup_back)
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for svc in services: 
        markup.add(types.InlineKeyboardButton(f"{esc(svc['name'])}", callback_data=f"svc_{svc['id']}"))
    markup.add(types.InlineKeyboardButton("⬅️ Kembali", callback_data="back_apps"))
    bot.edit_message_text(f"🛒 *Pilih Layanan {query.capitalize()}:*", chat_id, msg_id, parse_mode='Markdown', reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "back_apps")
def back_apps_cb(call):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(*[types.InlineKeyboardButton(nama, callback_data=f"cari_app_{query}") for nama, query in APLIKASI_POPULER])
    markup.add(types.InlineKeyboardButton("🔍 Cari Layanan Lain", callback_data="cari_manual"))
    bot.edit_message_text("🛒 *BELI NOKOS OTOMATIS*\n\nSilakan pilih aplikasi:", call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("svc_"))
def pilih_layanan_cb(call):
    svc_id = call.data.split("_")[1]
    chat_id = call.message.chat.id
    msg_id = call.message.message_id
    bot.answer_callback_query(call.id, "Mencari server...")
    bot.edit_message_text("⏳ Mencari server terbaik...", chat_id, msg_id)
    res = nokos_api("GET", f"/services/{svc_id}/offers?provider=provider_1&country_code=6")
    if "error" in res: 
        bot.edit_message_text(f"❌ Gagal: {res['error']}", chat_id, msg_id)
        return
    offers = res.get("data", [])
    if not offers: 
        markup_back = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Kembali", callback_data="back_apps"))
        bot.edit_message_text("❌ Stok kosong.", chat_id, msg_id, reply_markup=markup_back)
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for off in sorted(offers, key=lambda x: x.get('stock', 0), reverse=True)[:5]:
        asli = off['price']
        jual = asli + MARKUP
        markup.add(types.InlineKeyboardButton(f"Server: {esc(off['server_label'])} | Rp {jual:,} (Stok: {off['stock']})", callback_data=f"svr_{svc_id}_{off['provider_id']}_{asli}_{jual}"))
    markup.add(types.InlineKeyboardButton("⬅️ Kembali", callback_data=f"cari_app_{svc_id}"))
    bot.edit_message_text(f"🛒 *Pilih Server*\n\nSilakan pilih server:", chat_id, msg_id, parse_mode='Markdown', reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("svr_"))
def konfirmasi_beli_cb(call):
    parts = call.data.split("_")
    svc_id, provider_id, price_asli, harga_jual = parts[1], parts[2], int(parts[3]), int(parts[4])
    uid = call.from_user.id
    markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("✅ Beli Sekarang", callback_data=f"buy_{svc_id}_{provider_id}_{price_asli}_{harga_jual}")).add(types.InlineKeyboardButton("⬅️ Kembali", callback_data=f"svc_{svc_id}"))
    teks = (f"🛍️ *KONFIRMASI PEMBELIAN*\n\nHarga: *Rp {harga_jual:,}*\nSaldo Kamu: *Rp {get_saldo(uid):,}*\n\nLanjut?")
    bot.edit_message_text(teks, call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "cari_manual")
def cari_manual_cb(call): 
    bot.answer_callback_query(call.id)
    state[call.message.chat.id] = {"aksi": "cari_layanan"}
    bot.send_message(call.message.chat.id, "🛒 Ketik nama layanan (contoh: `WhatsApp`):", parse_mode='Markdown')

@bot.message_handler(func=lambda m: is_admin(m) and m.text == "📋 Semua Saldo User")
def admin_semua_saldo(message):
    state.pop(message.chat.id, None)
    bot.send_chat_action(message.chat.id, 'typing')
    users = load_users()
    teks = "📊 *DAFTAR SALDO USER*\n\n"
        for uid, data in users.items():
        try: 
            chat_info = bot.get_chat(int(uid))
            nama = chat_info.first_name or "User"
            username = f"@{chat_info.username}" if chat_info.username else "-"
        except: 
            nama = data.get('nama', 'User')
            username = data.get('username', '-')
        teks += f"👤 {esc(nama)} ({esc(username)})\n🆔 `{uid}` | 💰 Rp {get_saldo(uid):,}\n\n"
    bot.reply_to(message, teks if len(teks) > 50 else "Belum ada user.", parse_mode='Markdown')

@bot.message_handler(func=lambda m: is_admin(m) and m.text == "💵 Tambah Saldo User")
def admin_tambah_saldo(message): 
    state.pop(message.chat.id, None)
    state[message.chat.id] = {"aksi": "admin_topup", "step": "id"}
    bot.reply_to(message, "💵 *Tambah Saldo User*\n\nKetik ID Telegram user:")

@bot.message_handler(func=lambda m: is_admin(m) and m.text == "📉 Kurangi Saldo User")
def admin_kurangi_saldo(message): 
    state.pop(message.chat.id, None)
    state[message.chat.id] = {"aksi": "admin_minus", "step": "id"}
    bot.reply_to(message, "📉 *Kurangi Saldo User*\n\nKetik ID Telegram user:")

@bot.message_handler(func=lambda m: is_admin(m) and m.text == "🔑 Set API Nokos")
def set_api_nokos(message): 
    state.pop(message.chat.id, None)
    state[message.chat.id] = {"aksi": "set_nokos_key"}
    bot.reply_to(message, "🔑 Kirim *API Key Nokospusat*:")

@bot.message_handler(func=lambda m: is_admin(m) and m.text == "💰 Cek Saldo Nokos")
def cek_saldo_nokos(message):
    state.pop(message.chat.id, None)
    bot.send_chat_action(message.chat.id, 'typing')
    res = nokos_api("GET", "/wallet")
    if "error" in res: 
        bot.reply_to(message, f"❌ {res['error']}")
        return
    bot.reply_to(message, f"💰 *Saldo Nokospusat:*\n\nRp {res.get('data', {}).get('balance', 0):,}", parse_mode='Markdown')

@bot.message_handler(func=lambda m: is_admin(m) and m.text == "🖼️ Set Gambar QRIS")
def set_qris(message): 
    state.pop(message.chat.id, None)
    state[message.chat.id] = {"aksi": "set_qris"}
    bot.reply_to(message, "🖼️ Kirim FOTO QRIS:")

@bot.message_handler(func=lambda m: is_admin(m) and m.text == "⚙️ Atur Channel")
def set_ch(message): 
    state.pop(message.chat.id, None)
    state[message.chat.id] = {"aksi": "set_channel"}
    bot.reply_to(message, "⚙️ Kirim username channel (contoh: @AiszChannel):")

@bot.message_handler(func=lambda m: is_admin(m) and m.text == "📢 Broadcast")
def bc(message): 
    state.pop(message.chat.id, None)
    state[message.chat.id] = {"aksi": "broadcast"}
    bot.reply_to(message, "📢 Ketik pesan broadcast:")

@bot.message_handler(func=lambda m: is_admin(m) and m.text == "➕ Tambah Produk")
def tambah(message): 
    state.pop(message.chat.id, None)
    state[message.chat.id] = {"aksi": "tambah", "step": "nama"}
    bot.reply_to(message, "➕ Ketik nama produk:")

@bot.message_handler(func=lambda m: is_admin(m) and m.text == "✏️ Edit Produk")
def edit(message):
    state.pop(message.chat.id, None)
    p = load_produk()
    if not p: 
        bot.reply_to(message, "Kosong.")
        return
    state[message.chat.id] = {"aksi": "edit", "step": "pilih"}
    bot.reply_to(message, "✏️ Ketik nama produk:\n\n" + "\n".join([f"▪️ {n}" for n in p]))

@bot.message_handler(func=lambda m: is_admin(m) and m.text == "🗑️ Hapus Produk")
def hapus(message):
    state.pop(message.chat.id, None)
    p = load_produk()
    if not p: 
        bot.reply_to(message, "Kosong.")
        return
    state[message.chat.id] = {"aksi": "hapus", "step": "pilih"}
    bot.reply_to(message, "🗑️ Ketik nama produk:\n\n" + "\n".join([f"▪️ {n}" for n in p]))

@bot.message_handler(func=lambda m: is_admin(m) and m.text == "📋 Lihat Produk")
def lihat(message):
    state.pop(message.chat.id, None)
    p = load_produk()
    if not p: 
        bot.reply_to(message, "Kosong.")
        return
    bot.reply_to(message, "📋 *PRODUK (ADMIN)*\n\n" + "".join([f"▪️ *{esc(n)}*\n   {esc(d.get('harga', '-'))} | {esc(d.get('stok', '-'))}\n\n" for n, d in p.items()]), parse_mode='Markdown')
@bot.message_handler(func=lambda m: is_admin(m) and m.text == "📊 Statistik")
def stat_admin(message):
    state.pop(message.chat.id, None)
    p = load_produk()
    u = load_users()
    a = load_admins()
    ts = 0
    tn = 0
    for n, d in p.items():
        try: 
            si = int(''.join(filter(str.isdigit, str(d.get('stok', '0')))))
            ts += si
            hi = int(''.join(filter(str.isdigit, str(d.get('harga', '0')))))
            tn += hi * si
        except: 
            pass
    bot.reply_to(message, f"📊 *STATISTIK*\n\n📦 Produk: {len(p)}\n📊 Stok: {ts}\n💰 Nilai: Rp {tn:,}\n👥 User: {len(u)}\n🤖 AI: {'🟢' if load_config().get('ai_enabled') else '🔴'}", parse_mode='Markdown')

@bot.message_handler(func=lambda m: is_admin(m) and m.text == "💾 Backup Data")
def backup(message):
    state.pop(message.chat.id, None)
    try:
        if os.path.exists(DB_FILE): 
            bot.send_document(message.chat.id, open(DB_FILE, 'rb'), caption="💾 Produk")
        if os.path.exists(ADMINS_FILE): 
            bot.send_document(message.chat.id, open(ADMINS_FILE, 'rb'), caption="👥 Admin")
        if os.path.exists(SALDO_FILE): 
            bot.send_document(message.chat.id, open(SALDO_FILE, 'rb'), caption="💰 Saldo")
    except Exception as e: 
        bot.reply_to(message, f"❌ {e}")

@bot.message_handler(func=lambda m: is_admin(m) and m.text == "👥 Kelola Admin")
def kelola_adm(message):
    state.pop(message.chat.id, None)
    admins = load_admins()
    teks = "👥 *DAFTAR ADMIN*\n\n"
    for i, a in enumerate(admins, 1): 
        teks += f"{i}. `{a}`" + (" 👑 (Tidak bisa dihapus)" if a == ADMIN_ID else "") + "\n"
    teks += "\n💡 Tambah/hapus via 💬 Chat dengan AI."
    bot.reply_to(message, teks, parse_mode='Markdown')

@bot.message_handler(func=lambda m: is_admin(m) and m.text == "🤖 Pengaturan AI")
def ai_setting(message):
    state.pop(message.chat.id, None)
    c = load_config()
    st = "🟢 AKTIF" if c.get("ai_enabled") else "🔴 MATI"
    hk = "✅ Diset" if c.get("groq_api_key") else "❌ Belum"
    mk = types.InlineKeyboardMarkup(row_width=1)
    if c.get("ai_enabled"): 
        mk.add(types.InlineKeyboardButton("🔴 Matikan AI", callback_data="ai_off"))
    else: 
        mk.add(types.InlineKeyboardButton("🟢 Nyalakan AI", callback_data="ai_on"))
    mk.add(types.InlineKeyboardButton("🔑 Set API Key Groq", callback_data="ai_setkey"))
    bot.reply_to(message, f"🤖 *PENGATURAN AI*\n\nStatus: {st}\nAPI Key: {hk}", parse_mode='Markdown', reply_markup=mk)

@bot.callback_query_handler(func=lambda call: call.data.startswith('ai_'))
def ai_cb(call):
    if call.from_user.id not in load_admins(): return
    c = load_config()
    if call.data == "ai_on": 
        c["ai_enabled"] = True
        simpan_config(c)
        bot.answer_callback_query(call.id, "✅ AI ON!")
    elif call.data == "ai_off": 
        c["ai_enabled"] = False
        simpan_config(c)
        bot.answer_callback_query(call.id, "✅ AI OFF!")
    elif call.data == "ai_setkey": 
        state[call.from_user.id] = {"aksi": "set_ai_key"}
        bot.send_message(call.from_user.id, "🔑 Kirim API Key Groq:")

@bot.message_handler(func=lambda m: is_admin(m) and m.text == "💬 Chat dengan AI")
def chat_ai(message):
    state.pop(message.chat.id, None)
    if not load_config().get("groq_api_key"): 
        bot.reply_to(message, "❌ Set API Key dulu.")
        return
    state[message.chat.id] = {"aksi": "chat_ai_admin"}
    bot.reply_to(message, "💬 *CHAT AI AKTIF*\n\nKetik perintah atau ngobrol. Klik ⬅️ Keluar Chat AI untuk keluar.", parse_mode='Markdown')

@bot.message_handler(func=lambda m: is_admin(m) and m.text == "⬅️ Keluar Chat AI")
def keluar_ai(message): 
    state.pop(message.chat.id, None)
    bot.reply_to(message, "✅ Keluar.", reply_markup=menu_admin())

@bot.message_handler(func=lambda m: is_admin(m) and m.text == "⬅️ Keluar Mode Admin")
def keluar_admin(message): 
    state.pop(message.chat.id, None)
    bot.reply_to(message, "Keluar.", reply_markup=menu_user())
@bot.message_handler(content_types=['photo'])
def handle_foto(message):
    chat_id = message.chat.id
    uid = message.from_user.id
    u = message.from_user
    nama_user = u.first_name or u.last_name or u.username or "Kak"
    if is_admin(message) and chat_id in state and state[chat_id].get("aksi") == "set_qris":
        c = load_config()
        c["qris_file_id"] = message.photo[-1].file_id
        simpan_config(c)
        state.pop(chat_id, None)
        bot.reply_to(message, "✅ QRIS disimpan!", reply_markup=menu_admin())
        return
    if not is_admin(message):
        if not is_user_joined(uid): 
            bot.reply_to(message, "⚠️ Join channel dulu.")
            return
        simpan_user(uid, nama_user, u.username)
        if chat_id in state and state[chat_id].get("aksi") == "waiting_topup":
            state.pop(chat_id, None)
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(types.InlineKeyboardButton("✅ Dana Masuk", callback_data=f"approve_{uid}_{message.message_id}")).add(types.InlineKeyboardButton("❌ Belum Masuk", callback_data=f"reject_{uid}_{message.message_id}"))
            bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=f"💳 *BUKTI TOP UP MASUK*\n\n👤 Nama: {esc(nama_user)}\n🆔 ID: `{uid}`\n\nPilih aksi:", parse_mode='Markdown', reply_markup=markup)
            bot.reply_to(message, "✅ Bukti transfer diterima. Mohon tunggu verifikasi Admin.")
        else:
            bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=f"📥 *PESAN FOTO DARI PEMBELI*\n\n👤 {esc(nama_user)}\n🆔 {uid}\n\n💬 {esc(message.caption or 'Tanpa caption')}", parse_mode='Markdown')
            bot.reply_to(message, "✅ Foto diteruskan ke admin. 🙏")

@bot.message_handler(func=lambda m: True, content_types=['text'])
def handle_all(message):
    chat_id = message.chat.id
    text = message.text
    uid = message.from_user.id
    u = message.from_user
    nama_user = u.first_name or u.last_name or u.username or "Kak"
    if not is_admin(message): 
        simpan_user(uid, nama_user, u.username)
    if text in MENU_BUTTONS: 
        state.pop(chat_id, None)
    if chat_id in state:
        s = state[chat_id]
        if s["aksi"] == "admin_approve_topup":
            try: 
                nom = int(text)
            except: 
                bot.reply_to(message, "❌ Harus angka. Ketik ulang nominalnya:")
                return
            target_uid = s["target_uid"]
            ubah_saldo(target_uid, nom)
            state.pop(chat_id, None)
            bot.reply_to(message, f"✅ Berhasil menambahkan Rp {nom:,} ke User `{target_uid}`.", reply_markup=menu_admin())
            try: 
                bot.send_message(target_uid, f"🎉 *TOP UP BERHASIL*\n\nSaldo kamu telah ditambahkan sebesar *Rp {nom:,}*.", parse_mode='Markdown')
            except: 
                pass
            return
        if s["aksi"] == "cek_otp_id":
            order_id = text.strip()
            state.pop(chat_id, None)
            bot.send_chat_action(chat_id, 'typing')
            res = nokos_api("GET", f"/orders/{order_id}")
            if "error" in res: 
                bot.reply_to(message, f"❌ Pesanan tidak ditemukan: {res['error']}" if "diperiksa" not in res["error"].lower() else "⏳ Status sedang diperiksa server. Tunggu 5-10 detik lalu cek lagi.")
                return
            data = res.get("data", {})
            if data.get("id"): 
                bot.reply_to(message, f"🔍 *STATUS PESANAN*\n\nID: `{order_id}`\nNomor: `{data.get('phone_number', '-')}`\nStatus: {data.get('status')}\nOTP: `{data.get('otp') or 'Belum masuk'}`", parse_mode='Markdown')
            else: 
                bot.reply_to(message, "❌ Pesanan tidak ditemukan.")
            return
        if s["aksi"] == "set_nokos_key": 
            c = load_config()
            c["nokos_api_key"] = text.strip()
            simpan_config(c)
            state.pop(chat_id, None)
            bot.reply_to(message, "✅ API Key disimpan!", reply_markup=menu_admin())
            return
        if s["aksi"] == "set_ai_key": 
            c = load_config()
            c["groq_api_key"] = text.strip()
            simpan_config(c)
            state.pop(chat_id, None)
            bot.reply_to(message, "✅ API Key AI disimpan!", reply_markup=menu_admin())
            return
        if s["aksi"] in ["admin_topup", "admin_minus"]:
            tipe = "Tambah" if s["aksi"] == "admin_topup" else "Kurangi"
            if s["step"] == "id": 
                s["uid"] = text.strip()
                s["step"] = "nominal"
                bot.reply_to(message, f"ID: `{s['uid']}`\nKetik nominal yang mau di{tipe.lower()}:")
            elif s["step"] == "nominal":
                try: 
                    nom = int(text)
                except: 
                    bot.reply_to(message, "❌ Harus angka. Atau ketik /admin untuk batal.")
                    return
                ubah_saldo(s["uid"], nom if s["aksi"] == "admin_topup" else -nom)
                state.pop(chat_id, None)
                bot.reply_to(message, f"✅ Saldo user `{s['uid']}` berhasil di{tipe.lower()} Rp {nom:,}", reply_markup=menu_admin())
                try: 
                    bot.send_message(s["uid"], f"🎉 Saldo kamu telah di{tipe.lower()} sebesar Rp {nom:,} oleh Admin.", parse_mode='Markdown')
                except: 
                    pass
            return
        if s["aksi"] == "cari_layanan":
            bot.send_chat_action(chat_id, 'typing')
            res = nokos_api("GET", f"/services?q={text}&limit=5")
            if "error" in res: 
                bot.reply_to(message, f"❌ {res['error']}")
                state.pop(chat_id, None)
                return
            services = res.get("data", [])
            if not services: 
                bot.reply_to(message, "❌ Layanan tidak ditemukan.")
                return
            markup = types.InlineKeyboardMarkup(row_width=1)
            for svc in services: 
                markup.add(types.InlineKeyboardButton(f"{esc(svc['name'])}", callback_data=f"svc_{svc['id']}"))
            markup.add(types.InlineKeyboardButton("⬅️ Kembali", callback_data="back_apps"))
            bot.reply_to(message, "Hasil pencarian:", reply_markup=markup)
            state.pop(chat_id, None)
            return
        if s["aksi"] == "set_channel":
            ch = text.strip().replace("https://t.me/", "").replace("t.me/", "").replace("@", "")
            ch = "@" + ch
            try: 
                bot.get_chat_member(ch, bot.get_me().id)
                c = load_config()
                c["channel_username"] = ch
                simpan_config(c)
                state.pop(chat_id, None)
                bot.reply_to(message, f"✅ Channel: {ch}", reply_markup=menu_admin())
            except Exception as e: 
                state.pop(chat_id, None)
                bot.reply_to(message, f"❌ {e}", reply_markup=menu_admin())
            return
        if s["aksi"] == "broadcast":
            users = load_users()
            ok = 0
            no = 0
            for uid_bc in users:
                if uid_bc in load_admins(): continue
                try: 
                    bot.send_message(uid_bc, f"📢 *PENGUMUMAN*\n\n{text}", parse_mode='Markdown')
                    ok += 1
                except: 
                    no += 1
            state.pop(chat_id, None)
            bot.reply_to(message, f"✅ Broadcast!\nSukses: {ok}\nGagal: {no}", reply_markup=menu_admin())
            return
        if s["aksi"] == "tambah":
            if not text.strip(): 
                bot.reply_to(message, "❌ Tidak boleh kosong. Ketik ulang:")
                return
            if s["step"] == "nama": 
                s["nama"] = text
                s["step"] = "harga"
                bot.reply_to(message, f"Nama: *{esc(s['nama'])}*\n\nHarga:")
            elif s["step"] == "harga": 
                s["harga"] = text
                s["step"] = "stok"
                bot.reply_to(message, f"Harga: *{esc(s['harga'])}*\n\nStok:")
            elif s["step"] == "stok":
                produk = load_produk()
                produk[s["nama"]] = {"harga": s["harga"], "stok": text}
                simpan_produk(produk)
                state.pop(chat_id, None)
                bot.reply_to(message, f"✅ *{esc(s['nama'])}* ditambahkan!", reply_markup=menu_admin())
            return
        elif s["aksi"] == "edit":
            produk = load_produk()
            if s["step"] == "pilih":
                if text not in produk: 
                    bot.reply_to(message, "⚠️ Gak ada. Ketik persis:")
                    return
                s["nama"] = text
                s["step"] = "harga"
                bot.reply_to(message, "Harga baru (atau `-` skip):")
            elif s["step"] == "harga":
                if text != "-": produk[s["nama"]]["harga"] = text
                s["step"] = "stok"
                bot.reply_to(message, "Stok baru (atau `-` skip):")
            elif s["step"] == "stok":
                if text != "-": produk[s["nama"]]["stok"] = text
                simpan_produk(produk)
                state.pop(chat_id, None)
                bot.reply_to(message, f"✅ Diupdate!", reply_markup=menu_admin())
            return
        elif s["aksi"] == "hapus":
            produk = load_produk()
            if text not in produk: 
                bot.reply_to(message, "⚠️ Gak ada. Ketik persis:")
                return
            del produk[text]
            simpan_produk(produk)
            state.pop(chat_id, None)
            bot.reply_to(message, f"✅ Dihapus!", reply_markup=menu_admin())
            return
        if s["aksi"] == "chat_ai_admin":
            if s.get("pending_action"):
                pwd = text.strip()
                if pwd == "batal": 
                    bot.reply_to(message, "❌ Dibatalkan.")
                    s.pop("pending_action", None)
                    s.pop("pending_user_id", None)
                    return
                if pwd == ADMIN_PASSWORD:
                    admins = load_admins()
                    try: 
                        uid_target = int(s["pending_user_id"])
                    except: 
                        bot.reply_to(message, "❌ ID invalid.")
                        s.pop("pending_action", None)
                        s.pop("pending_user_id", None)
                        return
                    if s["pending_action"] == "add_admin":
                        if uid_target in admins: 
                            bot.reply_to(message, f"⚠️ `{uid_target}` udah admin.")
                        else: 
                            admins.append(uid_target)
                            simpan_admins(admins)
                            bot.reply_to(message, f"✅ `{uid_target}` jadi admin!")
                            bot.send_message(uid_target, "🎉 Kamu jadi admin.\n/start")
                    elif s["pending_action"] == "remove_admin":
                        if uid_target == ADMIN_ID: 
                            bot.reply_to(message, "❌ Gak bisa hapus admin utama!")
                        elif uid_target not in admins: 
                            bot.reply_to(message, f"⚠️ `{uid_target}` bukan admin.")
                        else: 
                            admins.remove(uid_target)
                            simpan_admins(admins)
                            bot.reply_to(message, f"✅ `{uid_target}` dicabut.")
                else: 
                    bot.reply_to(message, "❌ Password salah!")
                s.pop("pending_action", None)
                s.pop("pending_user_id", None)
                return
            ml = text.lower().strip()
            if ml in ["statistik", "stat"]: 
                stat_admin(message)
                return
            if ml in ["daftar admin", "list admin"]: 
                kelola_adm(message)
                return
            if ml in ["backup", "backup data"]: 
                backup(message)
                return
            bot.send_chat_action(chat_id, 'typing')
            resp = tanya_ai(text, load_produk(), True)
            if not resp: 
                bot.reply_to(message, "❌ AI gagal.")
                return
            match = re.search(r'\{.*?\}', resp, re.DOTALL)
            if match:
                try:
                    cmd = json.loads(match.group(0))
                    aksi = cmd.get("action")
                    produk = load_produk()
                    if aksi == "add": 
                        produk[cmd["nama"]] = {"harga": cmd.get("harga", "-"), "stok": cmd.get("stok", "-")}
                        simpan_produk(produk)
                        bot.reply_to(message, f"✅ *Ditambah*\n{esc(cmd['nama'])}")
                    elif aksi == "edit":
                        if cmd["nama"] in produk:
                            if cmd.get("harga"): produk[cmd["nama"]]["harga"] = cmd["harga"]
                            if cmd.get("stok"): produk[cmd["nama"]]["stok"] = cmd["stok"]
                            simpan_produk(produk)
                            bot.reply_to(message, f"✅ *Diupdate*\n{esc(cmd['nama'])}")
                        else: 
                            bot.reply_to(message, "⚠️ Gak ada.")
                    elif aksi == "delete":
                        if cmd["nama"] in produk: 
                            del produk[cmd["nama"]]
                            simpan_produk(produk)
                            bot.reply_to(message, "✅ Dihapus!")
                        else: 
                            bot.reply_to(message, "⚠️ Gak ada.")
                    elif aksi == "add_admin": 
                        s["pending_action"] = "add_admin"
                        s["pending_user_id"] = str(cmd.get("user_id", ""))
                        bot.reply_to(message, f"🔐 ID: `{s['pending_user_id']}`\n\nKetik password:")
                    elif aksi == "remove_admin": 
                        s["pending_action"] = "remove_admin"
                        s["pending_user_id"] = str(cmd.get("user_id", ""))
                        bot.reply_to(message, f"🔐 ID: `{s['pending_user_id']}`\n\nKetik password:")
                    bersih = resp.replace(match.group(0), "").strip()
                    if bersih: 
                        bot.send_message(chat_id, f"🤖 {bersih}")
                except Exception as e: 
                    bot.reply_to(message, f"❌ {e}")
            else: 
                bot.reply_to(message, f"🤖 {resp}")
            return

    if not is_admin(message):
        if not is_user_joined(uid): 
            bot.reply_to(message, "⚠️ Kamu belum join channel. Silakan klik /start untuk join.")
            return
        c = load_config()
        if c.get("ai_enabled", False): 
            h = tanya_ai(text, load_produk(), False)
            bot.reply_to(message, h if h else "Diteruskan ke admin. 🙏")
        else: 
            bot.reply_to(message, "Pesan diteruskan ke admin. 🙏", reply_markup=menu_user())
        teks = f"📥 *PESAN MASUK DARI PEMBELI*\n\n👤 {esc(nama_user)}\n🔗 @{esc(u.username) if u.username else 'Tidak ada'}\n🆔 {uid}\n\n💬 {esc(text)}"
        broadcast_to_admins(teks, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    data = call.data
    chat_id = call.message.chat.id
    uid = call.from_user.id
    msg_id = call.message.message_id
    if data.startswith("buy_"):
        parts = data.split("_")
        svc_id, provider_id, price_asli, harga_jual = parts[1], parts[2], int(parts[3]), int(parts[4])
        if get_saldo(uid) < harga_jual: 
            bot.answer_callback_query(call.id, "❌ Saldo tidak cukup! Top Up dulu.", show_alert=True)
            return
        bot.answer_callback_query(call.id, "Memproses...")
        bot.edit_message_text("⏳ Memproses...", chat_id, msg_id)
        ubah_saldo(uid, -harga_jual)
        payload = {"service_id": svc_id, "provider": "provider_1", "country_code": "6", "provider_id": provider_id, "quoted_price": price_asli}
        res = nokos_api("POST", "/orders", payload)
        if "error" in res: 
            ubah_saldo(uid, harga_jual)
            bot.edit_message_text(f"❌ Gagal (Saldo dikembalikan):\n{res['error']}", chat_id, msg_id)
            return
        order_id = res.get("data", {}).get("id")
        orders = load_orders()
        orders[order_id] = "pending"
        simpan_orders(orders)
        markup_batal = types.InlineKeyboardMarkup(row_width=2).add(types.InlineKeyboardButton("🔍 Cek OTP", callback_data=f"checkotp_{order_id}"), types.InlineKeyboardButton("❌ Batalkan", callback_data=f"cancel_{order_id}_{harga_jual}"))
        bot.edit_message_text(f"✅ *Pesanan Dibuat!*\nID: `{order_id}`\n\n⏳ Menunggu OTP (Maks 15 menit)...", chat_id, msg_id, parse_mode='Markdown', reply_markup=markup_batal)
        u_data = load_users().get(str(uid), {"nama": "Unknown", "username": "-"})
        broadcast_to_admins(f"🛒 *PEMBELIAN BARU*\n\n👤 Nama: {esc(u_data['nama'])}\n🔗 Username: {esc(u_data['username'])}\n🆔 ID: `{uid}`\nLayanan ID: `{svc_id}`\nHarga: Rp {harga_jual:,}", parse_mode='Markdown')
        threading.Thread(target=poll_otp, args=(order_id, uid, harga_jual, chat_id, msg_id)).start()
    elif data.startswith("checkotp_"):
        order_id = data.split("_")[1]
        bot.answer_callback_query(call.id, "Mengecek OTP...")
        res = nokos_api("GET", f"/orders/{order_id}")
        if "error" in res: 
            bot.send_message(chat_id, f"❌ {res['error']}" if "diperiksa" not in res["error"].lower() else "⏳ Status sedang diperiksa server. Tunggu 5-10 detik lalu cek lagi.")
            return
        data_order = res.get("data", {})
        otp = data_order.get("otp")
        status = data_order.get("status")
        bot.send_message(chat_id, f"✅ *OTP KAMU:* `{otp}`" if otp else f"⏳ Status: {status}. OTP belum masuk.", parse_mode='Markdown')
    elif data.startswith("cancel_"):
        parts = data.split("_")
        order_id = parts[1]
        harga_jual = int(parts[2])
        bot.answer_callback_query(call.id, "Membatalkan...")
        res = nokos_api("DELETE", f"/orders/{order_id}")
        if "error" in res: 
            bot.send_message(chat_id, f"❌ Gagal membatalkan:\n{res['error']}")
        else: 
            proses_refund(order_id, uid, harga_jual, chat_id, msg_id, "Dibatalkan oleh pengguna.")
    elif data.startswith("finish_"):
        order_id = data.split("_")[1]
        bot.answer_callback_query(call.id, "Menutup...")
        nokos_api("POST", f"/orders/{order_id}/finish")
        bot.edit_message_text("✅ Pesanan selesai.", chat_id, msg_id)
    elif data.startswith("approve_"):
        parts = data.split("_")
        target_uid = parts[1]
        original_msg_id = parts[2]
        bot.answer_callback_query(call.id, "Silakan ketik nominal.")
        state[chat_id] = {"aksi": "admin_approve_topup", "target_uid": target_uid, "msg_id": original_msg_id}
        bot.send_message(chat_id, f"✅ *Dana Masuk*\n\nKetik nominal saldo yang masuk untuk User `{target_uid}` (contoh: `10000`):", parse_mode='Markdown')
    elif data.startswith("reject_"):
        parts = data.split("_")
        target_uid = parts[1]
        original_msg_id = parts[2]
        bot.answer_callback_query(call.id, "Ditolak.")
        bot.edit_message_caption("❌ *BUKTI DITOLAK*\n\nDana belum masuk.", chat_id, msg_id)
        try: 
            bot.send_message(target_uid, f"❌ *Top Up Ditolak*\n\nBukti transfer belum masuk ke rekening admin.", parse_mode='Markdown')
        except: 
            pass

# ==========================================
# WEBHOOK FLASK
# ==========================================
app = Flask(__name__)

@app.route('/' + TOKEN, methods=['POST'])
def getMessage():
    json_string = request.get_data().decode('utf-8')
    update = telebot.types.Update.de_json(json_string)
    bot.process_new_updates([update])
    return "!", 200

@app.route("/")
def webhook():
    bot.remove_webhook()
    bot.set_webhook(url="https://Aisz25.pythonanywhere.com/" + TOKEN)
return "Webhook berhasil diset!", 200
