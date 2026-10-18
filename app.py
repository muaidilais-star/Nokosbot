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
 
