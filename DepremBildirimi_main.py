import os
import logging
import asyncio
import aiohttp
import aiosqlite
import sys
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


# GÜVENLİK İÇİN TOKEN ORTAM DEĞİŞKENİNDEN OKUNMALI
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN ortam değişkeni ayarlanmamış!")


API_URL = "https://api.orhanaydogdu.com.tr/deprem/kandilli/live"

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO
)

cached_data = None
cache_timestamp = 0
CACHE_EXPIRY_SECONDS = 180  # 3 dakika cache süresi

DB_NAME = "deprem_bot.db"

VALID_CITIES = {
    "adana", "adiyaman", "afyonkarahisar", "agri", "amasya", "ankara", "antalya",
    "artvin", "aydin", "balikesir", "bartin", "batman", "bayburt", "bilecik", "bingol",
    "bitlis", "bolu", "burdur", "bursa", "canakkale", "cankiri", "corum", "denizli",
    "diyarbakir", "edirne", "elazig", "erzincan", "erzurum", "eskisehir", "gaziantep",
    "giresun", "gumushane", "hakkari", "hatay", "isparta", "mersin", "istanbul",
    "izmir", "kars", "kastamonu", "kayseri", "kirklareli", "kirsehir", "kilis",
    "kocaeli", "konya", "kutahya", "malatya", "manisa", "kahramanmaras", "mardin",
    "mugla", "mus", "nevsehir", "nigde", "ordu", "rize", "sakarya", "samsun",
    "sanliurfa", "siirt", "sinop", "sivas", "tekirdag", "tokat", "trabzon", "tunceli",
    "usak", "van", "yozgat", "zonguldak", "aksaray", "karaman", "kirikkale",
    "sirnak", "ardahan", "igdir", "yalova", "karabuk", "osmaniye", "duzce", "adana", "adıyaman", "afyonkarahisar", "ağrı", "amasya", "ankara", "antalya",
"artvin", "aydın", "balıkesir", "bartın", "batman", "bayburt", "bilecik", "bingöl",
"bitlis", "bolu", "burdur", "bursa", "çanakkale", "çankırı", "çorum", "denizli",
"diyarbakır", "edirne", "elazığ", "erzincan", "erzurum", "eskişehir", "gaziantep",
"giresun", "gümüşhane", "hakkâri", "hatay", "isparta", "mersin", "istanbul",
"izmir", "kars", "kastamonu", "kayseri", "kırklareli", "kırşehir", "kilis",
"kocaeli", "konya", "kütahya", "malatya", "manisa", "kahramanmaraş", "mardin",
"muğla", "muş", "nevşehir", "niğde", "ordu", "rize", "sakarya", "samsun",
"şanlıurfa", "siirt", "sinop", "sivas", "tekirdağ", "tokat", "trabzon", "tunceli",
"uşak", "van", "yozgat", "zonguldak", "aksaray", "karaman", "kırıkkale",
"şırnak", "ardahan", "iğdır", "yalova", "karabük", "osmaniye", "düzce"

}

# -- Veritabanı işlemleri --

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER PRIMARY KEY,
                city TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS sent_earthquakes (
                earthquake_id TEXT PRIMARY KEY
            )
        ''')
        await db.commit()

async def get_user_city(chat_id: int) -> str:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT city FROM users WHERE chat_id = ?", (chat_id,)) as cursor:
            row = await cursor.fetchone()
            if row and row[0]:
                return row[0]
            return ""

async def set_user_city(chat_id: int, city: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT INTO users (chat_id, city)
            VALUES (?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET city=excluded.city
        """, (chat_id, city))
        await db.commit()

async def add_sent_earthquake(eq_id: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO sent_earthquakes (earthquake_id) VALUES (?)", (eq_id,))
        await db.commit()

async def check_earthquake_sent(eq_id: str) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT 1 FROM sent_earthquakes WHERE earthquake_id = ?", (eq_id,)) as cursor:
            row = await cursor.fetchone()
            return row is not None

async def remove_user(chat_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM users WHERE chat_id = ?", (chat_id,))
        await db.commit()

# -- API ve veri çekme --

async def fetch_data():
    global cached_data, cache_timestamp
    now = asyncio.get_event_loop().time()
    if cached_data and (now - cache_timestamp) < CACHE_EXPIRY_SECONDS:
        return cached_data
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(API_URL) as resp:
                resp.raise_for_status()
                data = await resp.json()
                cached_data = data
                cache_timestamp = now
                return cached_data
        except Exception as e:
            logging.error(f"Deprem verisi alınamadı: {e}")
            return None

async def fetch_latest_earthquake():
    data = await fetch_data()
    if not data:
        return None
    results = data.get("result", [])
    if not results:
        return None
    return results[0]

async def fetch_latest_20_earthquakes():
    data = await fetch_data()
    if not data:
        return []
    results = data.get("result", [])
    return results[:20]

# -- Menü --

MAIN_MENU_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📍 Şehir Seç")],
        [KeyboardButton("📊 Son 20 Deprem")],
        [KeyboardButton("🏙️ Şehrinin Son Depremleri")]
    ],
    resize_keyboard=True
)

# -- Handlers --

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not await get_user_city(chat_id):
        await set_user_city(chat_id, "")

    welcome_text = (
        "Hoş geldiniz! Menüden seçim yapabilirsiniz.\n\n"
        "<b>Komutlar:</b>\n"
        "/start - Botu başlatır ve bu mesajı gösterir\n"
        "/ilim - Seçtiğiniz ili gösterir\n"
        "/il Şehirİsmi - Deprem bildirimleri için şehir seçer\n\n"
        "Menüdeki butonlar:\n"
        "📍 Şehir Seç - Bildirim almak istediğiniz şehri ayarlayın\n"
        "📊 Son 20 Deprem - Son 20 deprem bilgisini gösterir (tüm Türkiye)\n"
        "🏙️ Şehrinin Son Depremleri - Sadece sizin seçtiğiniz şehirdeki son 20 deprem\n"
    )

    await update.message.reply_text(
        welcome_text,
        reply_markup=MAIN_MENU_KEYBOARD,
        parse_mode="HTML"
    )

async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "📍 Şehir Seç":
        await update.message.reply_text(
            "Lütfen şehir seçmek için mesaj kısmına aşağıdaki formatta yazınız:\n"
            "/il Şehirİsmi\n\n"
            "Örnek:\n"
            "/il Kütahya",
            reply_markup=MAIN_MENU_KEYBOARD
        )
    elif text == "📊 Son 20 Deprem":
        await all_20_earthquakes_handler(update, context)
    elif text == "🏙️ Şehrinin Son Depremleri":
        await user_city_20_earthquakes_handler(update, context)
    else:
        await update.message.reply_text(
            "Lütfen menüden geçerli bir seçenek seçiniz.",
            reply_markup=MAIN_MENU_KEYBOARD
        )

async def set_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    args = context.args
    if not args:
        await update.message.reply_text(
            "Lütfen bir il adı giriniz. Örnek: /il Kütahya",
            reply_markup=MAIN_MENU_KEYBOARD
        )
        return
    city = " ".join(args).strip().lower()

    if city not in VALID_CITIES:
        await update.message.reply_text(
            f"'{city.capitalize()}' geçerli bir il adı değil.\nLütfen Türkiye'deki geçerli illerden birini girin.",
            reply_markup=MAIN_MENU_KEYBOARD
        )
        return

    await set_user_city(chat_id, city)
    await update.message.reply_text(
        f"Deprem bildirimi için '{city.capitalize()}' ili seçildi.",
        reply_markup=MAIN_MENU_KEYBOARD
    )

async def all_20_earthquakes_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    deprem_listesi = await fetch_latest_20_earthquakes()
    if not deprem_listesi:
        await update.message.reply_text(
            "Deprem verisi alınamıyor.",
            reply_markup=MAIN_MENU_KEYBOARD
        )
        return
    mesaj = "<b>Son 20 Deprem (Tüm Türkiye, Güncelden Eskiye):</b>\n\n"
    for i, deprem in enumerate(deprem_listesi, 1):
        title = deprem.get("title", "Bilinmiyor")
        mag = deprem.get("mag", "Bilinmiyor")
        date = deprem.get("date", "Bilinmiyor")
        mesaj += f"{i}. {title} | Şiddet: {mag} | Tarih: {date}\n"
    await update.message.reply_text(mesaj, parse_mode="HTML", reply_markup=MAIN_MENU_KEYBOARD)

async def ilim_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    city = await get_user_city(chat_id)
    if city:
        mesaj = f"Seçili iliniz: {city.capitalize()}"
    else:
        mesaj = "Henüz bir il seçmediniz."
    await update.message.reply_text(mesaj, reply_markup=MAIN_MENU_KEYBOARD)

async def user_city_20_earthquakes_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    city = await get_user_city(chat_id)
    if not city:
        await update.message.reply_text(
            "Henüz şehir seçmediniz. Lütfen önce şehir seçmek için /il komutunu kullanınız.",
            reply_markup=MAIN_MENU_KEYBOARD
        )
        return

    data = await fetch_data()
    if not data:
        await update.message.reply_text(
            "Deprem verisi alınamıyor.",
            reply_markup=MAIN_MENU_KEYBOARD
        )
        return

    results = data.get("result", [])

    filtered = [deprem for deprem in results if (deprem.get("location_properties", {}).get("epiCenter", {}).get("name") or "").lower() == city.lower()]
    son_20 = filtered[:20]

    if not son_20:
        await update.message.reply_text(
            f"Seçilen şehir '{city.capitalize()}' için yakın zamanda deprem bilgisi bulunamadı.",
            reply_markup=MAIN_MENU_KEYBOARD
        )
        return

    mesaj = f"<b>Son 20 Deprem - {city.capitalize()} (Güncelden Eskiye):</b>\n\n"
    for i, deprem in enumerate(son_20, 1):
        title = deprem.get("title", "Bilinmiyor")
        mag = deprem.get("mag", "Bilinmiyor")
        date = deprem.get("date", "Bilinmiyor")
        mesaj += f"{i}. {title} | Şiddet: {mag} | Tarih: {date}\n"

    await update.message.reply_text(mesaj, parse_mode="HTML", reply_markup=MAIN_MENU_KEYBOARD)

# -- Background earthquake check --

async def check_earthquakes(application):
    while True:
        eq = await fetch_latest_earthquake()
        if eq:
            eq_id = eq.get("earthquake_id")
            if eq_id and not await check_earthquake_sent(eq_id):
                title = eq.get("title", "").lower()
                il = (eq.get("location_properties", {}).get("epiCenter", {}).get("name") or "").lower()
                mag = eq.get("mag", "Bilinmiyor")
                date = eq.get("date", "Bilinmiyor")
                msg = (f"⚠️ Yeni Deprem Bildirimi ⚠️\n"
                       f"Yer: {title}\n"
                       f"İl: {il.capitalize() if il else 'Bilinmiyor'}\n"
                       f"Büyüklük: {mag}\n"
                       f"Tarih: {date}")
                async with aiosqlite.connect(DB_NAME) as db:
                    async with db.execute("SELECT chat_id, city FROM users") as cursor:
                        rows = await cursor.fetchall()
                for chat_id, city in rows:
                    if city == "" or city == il:
                        try:
                            await application.bot.send_message(chat_id=chat_id, text=msg, disable_notification=False)
                        except Exception as e:
                            logging.warning(f"Mesaj gönderilemedi {chat_id}: {e}")
                            await remove_user(chat_id)
                await add_sent_earthquake(eq_id)
        await asyncio.sleep(CACHE_EXPIRY_SECONDS)

# -- Main --

if __name__ == "__main__":
    if sys.platform.startswith('win'):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("ilim", ilim_handler))
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("il", set_city))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, main_menu_handler))

    async def on_startup(app):
        asyncio.create_task(check_earthquakes(app))

    application.post_init = on_startup

    asyncio.get_event_loop().run_until_complete(init_db())

    application.run_polling()