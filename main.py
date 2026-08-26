import asyncio
import logging
import os
import random
import re
import aiosqlite
from datetime import datetime, timedelta
from urllib.parse import unquote, urlparse
from aiogram import Bot, Dispatcher, types, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)

BOT_TOKEN =  "8200444868:AAEuaZJvofaISp1AxChmiXMOGynEiJ5mlD0"
ADMIN_ID = 7825563654
ADMIN_USERNAME = "Bexr7zz"
PREMIUM_DAYS = 30
NOTIFY_BEFORE_DAYS = 3
PAGE_SIZE = 10
BOT_USERNAME = ""

API_TIMEOUT = 10
DB_PATH = "bot_data.db"

# Qo'shimcha adminlarga berilishi mumkin bo'lgan huquqlar. Asosiy admin
# (ADMIN_ID) har doim barcha huquqlarga ega bo'ladi.
ADMIN_PERMISSION_LABELS = {
    "add_media": "➕ Kino/Serial/Anime qo'shish",
    "delete_media": "🗑 Kino/Serial/Anime o'chirish",
    "add_channel": "📢 Kanal qo'shish",
    "delete_channel": "🗑 Kanal o'chirish",
    "list_channels": "📋 Kanallar ro'yxati",
    "give_premium": "👥 Premium berish",
    "remove_premium": "❌ Premium olish",
    "premium_list": "📊 Premium ro'yxati",
    "statistics": "📊 Statistika",
    "broadcast": "📣 Xabar yuborish",
    "change_premium_price": "💰 Premium narxini o'zgartirish",
    "change_referral_price": "🎁 Referal narxini o'zgartirish",
    "edit_messages": "✏️ Bot xabarlarini tahrirlash",
    "change_card": "💳 Karta raqamini o'zgartirish",
    "manage_admins": "👨‍💼 Adminlar",
    "backup": "🗄 Zaxira olish",
    "restore_backup": "📥 Zaxira tiklash",
}
ALL_ADMIN_PERMISSIONS = frozenset(ADMIN_PERMISSION_LABELS)
ADMIN_PERMISSIONS_CACHE = {}

if not BOT_TOKEN:
    raise ValueError(
        "TELEGRAM_BOT_TOKEN topilmadi! Replit Secrets bo'limiga qo'shing."
    )

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ─── ASOSIY MENYU TUGMALARINI HAR DOIM DARHOL ISHLATISH ──────────────────────
# Muammo: admin/foydalanuvchi biror bosqichda (masalan "📣 Xabar yuborish"
# yoki "👥 Premium berish" kutayotgan holatda) turib, fikridan qaytib boshqa
# asosiy menyu tugmasini bossa, matn ESKI bosqich handleriga tushib qolib
# noto'g'ri ishlanardi — shu sabab "qotib qolgandek" bo'lib, ikkinchi marta
# bosish kerak bo'lardi. Quyidagi middleware bu tugmalar bosilganda eskirgan
# holatni DARHOL tozalaydi, shunda tugma birinchi bosishdayoq to'g'ri ishlaydi.
MENU_INTERRUPT_TEXTS = {
    "🎬 Kinolar", "📺 Seriallar", "⛩ Anime va Multfilm", "🔍 Kod orqali qidirish",
    "➕ Kino/Serial/Anime qo'shish", "🗑 Kino/Serial/Anime o'chirish",
    "📢 Kanal qo'shish", "🗑 Kanal o'chirish", "📋 Kanallar ro'yxati",
    "👥 Premium berish", "❌ Premium olish", "📊 Premium ro'yxati",
    "📊 Statistika", "📣 Xabar yuborish", "💰 Premium narxini o'zgartirish",
    "🎁 Referal narxini o'zgartirish", "💰 Hisobim", "✏️ Bot xabarlarini tahrirlash",
    "💳 Karta raqamini o'zgartirish", "👨‍💼 Adminlar", "🗄 Zaxira olish",
    "📥 Zaxira tiklash", "🏠 Bosh menyu",
}
# ("🌟 Premium" atayin bu ro'yxatga kiritilmagan — u kino qo'shish
# bosqichida ham xuddi shu matn bilan ishlatiladi, holatni majburan
# tozalash o'sha bosqichni buzib qo'yishi mumkin edi.)

# Botda HAQIQATDA ro'yxatga olingan buyruqlar (pastdagi set_my_commands
# ro'yxatiga mos). "/avto" kabi so'zlar bu yerga KIRMAYDI — ular haqiqiy
# bot buyrug'i emas, balki muayyan bosqichda kutilayotgan maxsus matn
# (masalan AdminChannel.waiting_for_backup_link), shu sabab ularni
# umumiy "/" bilan boshlanuvchi buyruq deb hisoblab bo'lmaydi — aks holda
# holat noto'g'ri tozalanib, o'sha maxsus so'z ishlamay qolardi.
REAL_BOT_COMMANDS = {
    "/start", "/kino", "/serial", "/anime",
    "/search", "/premium", "/admin", "/bekor",
}

@dp.message.outer_middleware()
async def menu_interrupt_middleware(handler, event: types.Message, data: dict):
    text = event.text or ""
    command_word = text.split()[0].lower() if text else ""
    if text in MENU_INTERRUPT_TEXTS or command_word in REAL_BOT_COMMANDS:
        key = StorageKey(bot_id=bot.id, chat_id=event.chat.id, user_id=event.from_user.id)
        state = FSMContext(storage=dp.storage, key=key)
        if await state.get_state() is not None:
            await state.clear()
    return await handler(event, data)

_db: aiosqlite.Connection = None


async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        _db = await aiosqlite.connect(DB_PATH)
        _db.row_factory = aiosqlite.Row
    return _db


async def init_db():
    global _db
    _db = await aiosqlite.connect(DB_PATH)
    _db.row_factory = aiosqlite.Row
    await ensure_schema()
    await load_admin_permissions()


async def ensure_schema():
    """Barcha jadvallarni yaratadi va kerakli migratsiyalarni qo'llaydi.
    Bot birinchi ishga tushganda (init_db orqali) HAM, zaxiradan tiklash
    amalga oshirilgandan KEYIN ham chaqiriladi — shunda eski (yangi
    ustunlarsiz) zaxira fayli tiklansa ham, bot yangi referal/balans
    ustunlarini avtomatik qo'shib oladi va xatoga tushmaydi."""
    db = await get_db()

    await db.execute("""
    CREATE TABLE IF NOT EXISTS channels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel_id TEXT,
        title TEXT,
        link TEXT,
        type TEXT DEFAULT 'telegram'
    )""")

    await db.execute("""
    CREATE TABLE IF NOT EXISTS manual_confirmations (
        user_id INTEGER,
        channel_id TEXT,
        confirmed_at TEXT,
        PRIMARY KEY (user_id, channel_id)
    )""")

    await db.execute("""
    CREATE TABLE IF NOT EXISTS media (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT,
        title TEXT,
        category TEXT,
        file_id TEXT,
        part INTEGER,
        is_premium INTEGER DEFAULT 0,
        views INTEGER DEFAULT 0,
        likes INTEGER DEFAULT 0,
        dislikes INTEGER DEFAULT 0
    )""")

    await db.execute("""
    CREATE TABLE IF NOT EXISTS premium_users (
        user_id INTEGER PRIMARY KEY,
        expire_date TEXT,
        notified INTEGER DEFAULT 0
    )""")

    await db.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        first_seen TEXT
    )""")

    await db.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )""")

    await db.execute("""
    CREATE TABLE IF NOT EXISTS media_votes (
        user_id INTEGER,
        media_id INTEGER,
        vote INTEGER,
        PRIMARY KEY (user_id, media_id)
    )""")

    await db.execute("""
    CREATE TABLE IF NOT EXISTS admins (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        added_at TEXT,
        permissions TEXT DEFAULT ''
    )""")

    await db.commit()

    migrations = [
        "ALTER TABLE media ADD COLUMN is_premium INTEGER DEFAULT 0",
        "ALTER TABLE media ADD COLUMN views INTEGER DEFAULT 0",
        "ALTER TABLE media ADD COLUMN likes INTEGER DEFAULT 0",
        "ALTER TABLE media ADD COLUMN dislikes INTEGER DEFAULT 0",
        "ALTER TABLE premium_users ADD COLUMN expire_date TEXT",
        "ALTER TABLE premium_users ADD COLUMN notified INTEGER DEFAULT 0",
        "ALTER TABLE channels ADD COLUMN type TEXT DEFAULT 'telegram'",
        "ALTER TABLE users ADD COLUMN referred_by INTEGER DEFAULT NULL",
        "ALTER TABLE users ADD COLUMN balance INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN referral_credited INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN referral_reward_amount INTEGER DEFAULT 0",
        "ALTER TABLE admins ADD COLUMN permissions TEXT DEFAULT ''",
    ]
    for sql in migrations:
        try:
            await db.execute(sql)
            await db.commit()
        except Exception:
            pass

    try:
        async with db.execute("SELECT value FROM settings WHERE key='premium_price'") as cur:
            old_price = await cur.fetchone()
        if old_price:
            await db.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES ('premium_price_30', ?)",
                (old_price[0],)
            )
            await db.commit()
    except Exception:
        pass

    # ── DUBLIKAT KANAL YOZUVLARINI TOZALASH ──
    # `channels` jadvalida ilgari channel_id ustida UNIQUE cheklov yo'q edi,
    # shu sababli bir xil kanal bir necha marta qo'shilsa (masalan, avval
    # eskirgan havola bilan, keyin tuzatilgandan keyin yana), ESKI qator
    # o'chmasdan, bazada bir nechta yozuv qolib ketardi — va foydalanuvchiga
    # ba'zan aynan o'sha ESKI (eskirgan) havola ko'rsatilib turardi. Bu yerda
    # har bir channel_id bo'yicha faqat ENG OXIRGI (eng yangi, id'i eng
    # katta) qatorni qoldirib, qolganlarini butunlay o'chiramiz, so'ng shu
    # ustunga UNIQUE indeks qo'yamiz — shunda kelajakda dublikat umuman
    # yaratilmaydi.
    try:
        await db.execute("""
            DELETE FROM channels
            WHERE id NOT IN (
                SELECT MAX(id) FROM channels GROUP BY channel_id
            )
        """)
        await db.commit()
        await db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_channels_channel_id "
            "ON channels(channel_id)"
        )
        await db.commit()
    except Exception as e:
        logging.warning(f"channels jadvalini tozalab bo'lmadi: {e}")


# ─── FSM HOLATLARI ───────────────────────────────────────────────────────────

class MediaUpload(StatesGroup):
    category = State()
    title = State()
    code_choice = State()
    manual_code = State()
    is_premium = State()
    parts_count = State()
    waiting_for_videos = State()

class CodeSearch(StatesGroup):
    waiting_for_code = State()

class AdminChannel(StatesGroup):
    waiting_for_type = State()
    waiting_for_id = State()
    waiting_for_manual_title = State()
    waiting_for_manual_link = State()
    waiting_for_invite_title = State()
    waiting_for_invite_resolve = State()
    waiting_for_backup_link = State()

class AdminDeleteMedia(StatesGroup):
    waiting_for_code = State()

class AdminPremium(StatesGroup):
    waiting_user_id = State()

class AdminBroadcast(StatesGroup):
    waiting_for_message = State()

class AdminPriceChange(StatesGroup):
    waiting_for_plan = State()
    waiting_for_price = State()

class AdminReferralPrice(StatesGroup):
    waiting_for_amount = State()

class AdminMessageEdit(StatesGroup):
    waiting_for_text = State()

class PaymentReceipt(StatesGroup):
    waiting_for_receipt = State()

class AdminCardChange(StatesGroup):
    waiting_for_number = State()
    waiting_for_holder = State()

class AdminManage(StatesGroup):
    waiting_for_add_id = State()
    waiting_for_permissions = State()
    waiting_for_remove_id = State()

class AdminBackup(StatesGroup):
    waiting_for_file = State()


# ─── YORDAMCHI FUNKSIYALAR ───────────────────────────────────────────────────

async def is_premium_user(user_id: int) -> bool:
    db = await get_db()
    async with db.execute(
        "SELECT user_id, expire_date FROM premium_users WHERE user_id=?", (user_id,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return False
    if row["expire_date"]:
        expire = datetime.fromisoformat(row["expire_date"])
        if datetime.now() > expire:
            await db.execute("DELETE FROM premium_users WHERE user_id=?", (user_id,))
            await db.commit()
            return False
    return True

async def get_expire_date(user_id: int):
    db = await get_db()
    async with db.execute(
        "SELECT expire_date FROM premium_users WHERE user_id=?", (user_id,)
    ) as cur:
        row = await cur.fetchone()
    if not row or not row["expire_date"]:
        return None
    expire = datetime.fromisoformat(row["expire_date"])
    if datetime.now() > expire:
        await db.execute("DELETE FROM premium_users WHERE user_id=?", (user_id,))
        await db.commit()
        return None
    return expire

async def remove_premium(user_id: int):
    db = await get_db()
    await db.execute("DELETE FROM premium_users WHERE user_id=?", (user_id,))
    await db.commit()

async def safe_get_chat_member(chat_id, user_id: int):
    try:
        return await asyncio.wait_for(
            bot.get_chat_member(chat_id=chat_id, user_id=user_id),
            timeout=API_TIMEOUT
        )
    except asyncio.TimeoutError:
        logging.warning(f"get_chat_member timeout: chat={chat_id}, user={user_id}")
        return None
    except Exception as e:
        logging.error(f"get_chat_member xato ({chat_id}): {e}")
        return None

async def safe_get_chat(chat_id):
    try:
        return await asyncio.wait_for(
            bot.get_chat(chat_id),
            timeout=API_TIMEOUT
        )
    except asyncio.TimeoutError:
        logging.warning(f"get_chat timeout: {chat_id}")
        return None
    except Exception as e:
        logging.error(f"get_chat xato ({chat_id}): {e}")
        return None

async def get_channel_link(ch_id: str, link: str) -> str:
    """
    Kanal uchun havola qaytaradi.
    - Ochiq kanal (@username): doim https://t.me/username dan foydalanadi (eskirmaydi)
    - Yopiq kanal: kanal QO'SHILGANDA bir marta yaratilgan (member_limit va
      expire_date berilmagan) taklif havolasi qaytariladi. Bunday havolalar
      Telegram tomonidan o'z-o'zidan eskirmaydi, shuning uchun har bir
      tekshiruvda YANGI havola yaratishga hojat yo'q — aksincha, doimiy
      qayta-yaratish urinishlari Telegram tezlik cheklovi (rate limit)ga
      urilib, tasodifiy xatoliklarga sabab bo'lishi mumkin edi.
    """
    if str(ch_id).startswith("@"):
        username = ch_id.lstrip("@")
        return f"https://t.me/{username}"
    return link

def is_super_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

def parse_admin_permissions(raw_permissions) -> set:
    if not raw_permissions:
        return set()
    return {
        permission
        for permission in str(raw_permissions).split(",")
        if permission in ADMIN_PERMISSION_LABELS
    }

async def load_admin_permissions():
    ADMIN_PERMISSIONS_CACHE.clear()
    db = await get_db()
    try:
        async with db.execute(
            "SELECT user_id, permissions FROM admins"
        ) as cur:
            rows = await cur.fetchall()
        for row in rows:
            ADMIN_PERMISSIONS_CACHE[row["user_id"]] = parse_admin_permissions(
                row["permissions"]
            )
    except Exception:
        # Eski baza faylida migratsiya hali ishlamagan bo'lsa, bot baribir
        # ishga tushadi va keyingi schema tekshiruvida ustun qo'shiladi.
        pass

async def get_admin_permissions(user_id: int) -> set:
    if is_super_admin(user_id):
        return set(ALL_ADMIN_PERMISSIONS)
    if user_id in ADMIN_PERMISSIONS_CACHE:
        return ADMIN_PERMISSIONS_CACHE[user_id]

    db = await get_db()
    async with db.execute(
        "SELECT permissions FROM admins WHERE user_id=?", (user_id,)
    ) as cur:
        row = await cur.fetchone()
    permissions = parse_admin_permissions(row["permissions"]) if row else set()
    ADMIN_PERMISSIONS_CACHE[user_id] = permissions
    return permissions

async def has_admin_permission(user_id: int, permission: str) -> bool:
    return permission in await get_admin_permissions(user_id)

async def is_bot_admin(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    db = await get_db()
    async with db.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,)) as cur:
        return (await cur.fetchone()) is not None

def md_escape(text) -> str:
    """Telegram Markdown v1 uchun maxsus belgilarni escape qiladi."""
    if text is None:
        return ""
    text = str(text)
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, "\\" + ch)
    return text

async def add_admin(
    user_id: int, username: str = "", permissions=None
):
    permissions = set(permissions or [])
    permissions_text = ",".join(
        permission
        for permission in ADMIN_PERMISSION_LABELS
        if permission in permissions
    )
    db = await get_db()
    await db.execute(
        "INSERT OR REPLACE INTO admins "
        "(user_id, username, added_at, permissions) VALUES (?, ?, ?, ?)",
        (
            user_id,
            username,
            datetime.now().strftime("%d.%m.%Y %H:%M"),
            permissions_text,
        )
    )
    await db.commit()
    ADMIN_PERMISSIONS_CACHE[user_id] = set(permissions)

async def remove_admin(user_id: int):
    db = await get_db()
    await db.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
    await db.commit()
    ADMIN_PERMISSIONS_CACHE.pop(user_id, None)

async def get_admins_list():
    db = await get_db()
    async with db.execute(
        "SELECT user_id, username, added_at, permissions "
        "FROM admins ORDER BY added_at"
    ) as cur:
        return await cur.fetchall()

def build_admin_permissions_kb(selected_permissions) -> InlineKeyboardMarkup:
    selected = set(selected_permissions or [])
    rows = []
    for permission, label in ADMIN_PERMISSION_LABELS.items():
        mark = "✅" if permission in selected else "⬜"
        rows.append([InlineKeyboardButton(
            text=f"{mark} {label}",
            callback_data=f"admin_perm_toggle_{permission}",
        )])
    rows.append([
        InlineKeyboardButton(text="✅ Saqlash", callback_data="admin_perm_save"),
        InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin_perm_cancel"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def get_card_info():
    db = await get_db()
    async with db.execute("SELECT value FROM settings WHERE key='card_number'") as cur:
        row = await cur.fetchone()
    card_number = row["value"] if row else "Karta raqami hali kiritilmagan"
    async with db.execute("SELECT value FROM settings WHERE key='card_holder'") as cur:
        row2 = await cur.fetchone()
    card_holder = row2["value"] if row2 else ""
    return card_number, card_holder

async def set_card_info(card_number=None, card_holder=None):
    db = await get_db()
    if card_number is not None:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('card_number', ?)",
            (card_number,)
        )
    if card_holder is not None:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('card_holder', ?)",
            (card_holder,)
        )
    await db.commit()

async def add_premium(user_id: int, days: int = PREMIUM_DAYS):
    expire = datetime.now() + timedelta(days=days)
    db = await get_db()
    await db.execute(
        "INSERT OR REPLACE INTO premium_users (user_id, expire_date, notified) VALUES (?, ?, 0)",
        (user_id, expire.isoformat())
    )
    await db.commit()
    return expire

async def get_premium_price(days: int = 30) -> str:
    db = await get_db()
    async with db.execute(
        "SELECT value FROM settings WHERE key=?", (f"premium_price_{days}",)
    ) as cur:
        row = await cur.fetchone()
    if row:
        return row["value"]
    async with db.execute("SELECT value FROM settings WHERE key='premium_price'") as cur:
        row2 = await cur.fetchone()
    return row2["value"] if row2 else "50,000"

async def set_premium_price(price: str, days: int = 30):
    db = await get_db()
    await db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (f"premium_price_{days}", price)
    )
    await db.commit()


# ─── TAHRIRLANADIGAN BOT XABARLARI ────────────────────────────────────────────
# Bu yerda ro'yxatga olingan matnlar admin panelidan ("✏️ Bot xabarlarini
# tahrirlash") to'g'ridan-to'g'ri o'zgartirilishi mumkin — kod yozishga
# hojat qolmaydi. Har birining KALITI, ADMIN PANELIDAGI NOMI va standart
# (birinchi marta ishlatiladigan) matni bor.

EDITABLE_MESSAGES = {
    "welcome": (
        "👋 Xush kelibsiz xabari",
        "Xush kelibsiz! Kerakli bo'limni tanlang:"
    ),
    "subscribe_required": (
        "⚠️ Majburiy obuna xabari",
        "⚠️ Botdan foydalanish uchun quyidagi kanal(lar)ga obuna bo'ling:\n\n"
        "ℹ️ _Premium a'zolarga majburiy kanal obunasi talab qilinmaydi!_"
    ),
    "code_search_prompt": (
        "🔍 Kod qidiruv xabari",
        "🔍 *Kod orqali qidiruv*\n"
        "ℹ️ _TikTok/Reels da ko'rgan kino kodini kiriting!_\n\n"
        "Kino, Serial yoki Anime kodini kiriting:"
    ),
    "premium_advantages": (
        "🌟 Premium afzalliklari xabari",
        "✅ *Afzalliklar:*\n"
        "• Barcha HD kinolar va seriallar\n"
        "• Reklamasiz tomosha\n"
        "• Video saqlash va uzatish huquqi\n"
        "• Majburiy kanal obunasisiz foydalanish"
    ),
    "payment_instructions": (
        "💳 To'lov qilish yo'riqnomasi",
        "1️⃣ Yuqoridagi kartaga to'lovni amalga oshiring.\n"
        "2️⃣ To'lov chekining *rasmini (screenshot)* shu yerga yuboring.\n\n"
        "⚠️ *Eslatma:* Chekni tashlamasangiz, Premium berilmaydi!"
    ),
    "referral_pending": (
        "👥 Referal — do'st qo'shildi xabari",
        "👥 *Taklif havolangiz orqali yangi foydalanuvchi qo'shildi!*\n\n"
        "⏳ Diqqat: pul faqat u botning BARCHA majburiy kanallariga obuna "
        "bo'lgandan so'nggina hisobingizga tushadi. Agar u keyinchalik "
        "kanal(lar)dan yoki botning o'zidan chiqib ketsa, mukofot "
        "hisobingizdan qayta ayirib olinadi."
    ),
}

async def get_message_template(key: str) -> str:
    default_text = EDITABLE_MESSAGES.get(key, ("", ""))[1]
    db = await get_db()
    async with db.execute(
        "SELECT value FROM settings WHERE key=?", (f"msgtpl_{key}",)
    ) as cur:
        row = await cur.fetchone()
    return row["value"] if row and row["value"] else default_text

async def set_message_template(key: str, text: str):
    db = await get_db()
    await db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (f"msgtpl_{key}", text)
    )
    await db.commit()


# ─── REFERAL DASTURI VA HISOB (BALANS) ────────────────────────────────────────
# Har bir foydalanuvchi o'zining taklif havolasi orqali yangi odam qo'shsa,
# pul FAQAT o'sha yangi odam BOTNING BARCHA MAJBURIY KANALLARIGA obuna
# bo'lgandan keyingina hisobiga tushadi (check_subscriptions bo'sh natija
# qaytarganda). Agar keyinchalik o'sha odam kanal(lar)dan yoki botning
# o'zidan chiqib ketsa (blok qilsa), taklif qilgan odamning hisobidan
# ayni miqdor ayirib olinadi va bu haqda unga xabar beriladi.

async def get_referral_reward() -> int:
    db = await get_db()
    async with db.execute(
        "SELECT value FROM settings WHERE key='referral_reward'"
    ) as cur:
        row = await cur.fetchone()
    return int(row["value"]) if row and row["value"] else 1000

async def set_referral_reward(amount: int):
    db = await get_db()
    await db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('referral_reward', ?)",
        (str(amount),)
    )
    await db.commit()

async def get_balance(user_id: int) -> int:
    db = await get_db()
    async with db.execute(
        "SELECT balance FROM users WHERE user_id=?", (user_id,)
    ) as cur:
        row = await cur.fetchone()
    return row["balance"] if row and row["balance"] is not None else 0

async def get_referral_stats(user_id: int):
    """Umumiy taklif qilinganlar soni va shulardan hozir 'faol' (pul
    hisoblangan) bo'lganlar sonini qaytaradi."""
    db = await get_db()
    async with db.execute(
        "SELECT COUNT(*) c FROM users WHERE referred_by=?", (user_id,)
    ) as cur:
        total = (await cur.fetchone())["c"]
    async with db.execute(
        "SELECT COUNT(*) c FROM users WHERE referred_by=? AND referral_credited=1",
        (user_id,)
    ) as cur:
        active = (await cur.fetchone())["c"]
    return total, active

async def get_referral_list(user_id: int, limit: int = 15):
    db = await get_db()
    async with db.execute(
        "SELECT user_id, first_seen, referral_credited FROM users "
        "WHERE referred_by=? ORDER BY first_seen DESC LIMIT ?",
        (user_id, limit)
    ) as cur:
        return await cur.fetchall()

async def process_referral_reward_if_needed(user_id: int):
    """Foydalanuvchi ENDI barcha majburiy kanallarga obuna bo'lgan paytda
    chaqiriladi. Agar u kimdirning taklifi orqali kelgan bo'lsa va hali
    pul hisoblanmagan bo'lsa — taklif qilgan odamning hisobiga mukofot
    qo'shiladi va unga xabar yuboriladi."""
    db = await get_db()
    async with db.execute(
        "SELECT referred_by, referral_credited FROM users WHERE user_id=?",
        (user_id,)
    ) as cur:
        row = await cur.fetchone()
    if not row or not row["referred_by"] or row["referral_credited"]:
        return

    referrer_id = row["referred_by"]
    reward = await get_referral_reward()

    await db.execute(
        "UPDATE users SET referral_credited=1, referral_reward_amount=? WHERE user_id=?",
        (reward, user_id)
    )
    await db.execute(
        "UPDATE users SET balance = COALESCE(balance, 0) + ? WHERE user_id=?",
        (reward, referrer_id)
    )
    await db.commit()

    try:
        new_balance = await get_balance(referrer_id)
        await bot.send_message(
            referrer_id,
            f"🎉 *Tabriklaymiz!* Siz taklif qilgan foydalanuvchi botning "
            f"barcha majburiy kanallariga obuna bo'ldi.\n\n"
            f"💰 Hisobingizga *{reward:,} so'm* qo'shildi.\n"
            f"💳 Joriy balans: *{new_balance:,} so'm*",
            parse_mode="Markdown"
        )
    except Exception:
        pass

async def process_referral_penalty(user_id: int, reason: str):
    """Foydalanuvchi majburiy kanal(lar)dan yoki botning o'zidan chiqib
    ketganda chaqiriladi. Agar unga avval mukofot hisoblangan bo'lsa,
    taklif qilgan odamning hisobidan ayni miqdor ayirib olinadi."""
    db = await get_db()
    async with db.execute(
        "SELECT referred_by, referral_credited, referral_reward_amount "
        "FROM users WHERE user_id=?", (user_id,)
    ) as cur:
        row = await cur.fetchone()
    if not row or not row["referred_by"] or not row["referral_credited"]:
        return

    referrer_id = row["referred_by"]
    amount = row["referral_reward_amount"] or 0

    await db.execute(
        "UPDATE users SET referral_credited=0, referral_reward_amount=0 WHERE user_id=?",
        (user_id,)
    )
    await db.execute(
        "UPDATE users SET balance = MAX(0, COALESCE(balance, 0) - ?) WHERE user_id=?",
        (amount, referrer_id)
    )
    await db.commit()

    try:
        new_balance = await get_balance(referrer_id)
        await bot.send_message(
            referrer_id,
            f"⚠️ *Diqqat!* Siz taklif qilgan foydalanuvchilardan biri {reason}.\n\n"
            f"💸 Shu sabab hisobingizdan *{amount:,} so'm* ayirib olindi.\n"
            f"💳 Joriy balans: *{new_balance:,} so'm*",
            parse_mode="Markdown"
        )
    except Exception:
        pass


# ─── KANAL USERNAME TOZALASH ─────────────────────────────────────────────────

def clean_tme_path(raw: str) -> str:
    """
    t.me havolasidan yoki username inputidan sof username ajratib oladi.
    URL-encoded belgilar (%20, %5F va h.k.), ortiqcha /, ? parametrlar,
    bosh-oxirdagi bo'sh joylar barchasini tozalaydi.
    """
    raw = raw.strip()
    raw = unquote(raw)
    for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
        if raw.lower().startswith(prefix.lower()):
            raw = raw[len(prefix):]
            break
    raw = raw.split("?")[0].strip("/").strip()
    return raw

def parse_channel_input(raw: str):
    """
    Admin tomonidan kiritilgan kanal input'ini tahlil qiladi.
    Qaytaradi: (chat_id_for_api, full_link_or_none, is_invite)
    """
    raw = raw.strip()
    decoded = unquote(raw)

    is_tme = any(decoded.lower().startswith(p) for p in (
        "https://t.me/", "http://t.me/", "t.me/"
    ))

    if is_tme:
        path = clean_tme_path(decoded)
        full_link = "https://t.me/" + path

        if path.startswith("+") or path.lower().startswith("joinchat/"):
            return None, full_link, True

        username = "@" + path.lstrip("@")
        return username, full_link, False

    stripped = decoded.lstrip("@").strip()
    if re.match(r'^-?\d+$', stripped):
        return int(stripped), None, False

    username = "@" + stripped.lstrip("@")
    return username, None, False


# ─── TUGMALAR ────────────────────────────────────────────────────────────────

def main_menu(user_id: int = 0):
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎬 Kinolar"), KeyboardButton(text="📺 Seriallar")],
            [KeyboardButton(text="⛩ Anime va Multfilm")],
            [KeyboardButton(text="🔍 Kod orqali qidirish"), KeyboardButton(text="🌟 Premium")],
            [KeyboardButton(text="💰 Hisobim")],
        ],
        resize_keyboard=True
    )

def admin_menu(user_id: int = 0):
    permissions = ALL_ADMIN_PERMISSIONS if is_super_admin(user_id) else ADMIN_PERMISSIONS_CACHE.get(user_id, set())

    def allowed(permission):
        return is_super_admin(user_id) or permission in permissions

    rows = []
    if allowed("add_media"):
        rows.append([KeyboardButton(text=ADMIN_PERMISSION_LABELS["add_media"])])
    if allowed("delete_media"):
        rows.append([KeyboardButton(text=ADMIN_PERMISSION_LABELS["delete_media"])])
    if allowed("add_channel") or allowed("delete_channel"):
        channel_row = []
        if allowed("add_channel"):
            channel_row.append(KeyboardButton(text=ADMIN_PERMISSION_LABELS["add_channel"]))
        if allowed("delete_channel"):
            channel_row.append(KeyboardButton(text=ADMIN_PERMISSION_LABELS["delete_channel"]))
        rows.append(channel_row)
    if allowed("list_channels"):
        rows.append([KeyboardButton(text=ADMIN_PERMISSION_LABELS["list_channels"])])
    if allowed("give_premium") or allowed("remove_premium"):
        premium_row = []
        if allowed("give_premium"):
            premium_row.append(KeyboardButton(text=ADMIN_PERMISSION_LABELS["give_premium"]))
        if allowed("remove_premium"):
            premium_row.append(KeyboardButton(text=ADMIN_PERMISSION_LABELS["remove_premium"]))
        rows.append(premium_row)
    if allowed("premium_list"):
        rows.append([KeyboardButton(text=ADMIN_PERMISSION_LABELS["premium_list"])])
    if allowed("statistics"):
        rows.append([KeyboardButton(text=ADMIN_PERMISSION_LABELS["statistics"])])
    if allowed("broadcast"):
        rows.append([KeyboardButton(text=ADMIN_PERMISSION_LABELS["broadcast"])])
    if allowed("change_premium_price"):
        rows.append([KeyboardButton(text=ADMIN_PERMISSION_LABELS["change_premium_price"])])
    if allowed("change_referral_price"):
        rows.append([KeyboardButton(text=ADMIN_PERMISSION_LABELS["change_referral_price"])])
    if allowed("edit_messages"):
        rows.append([KeyboardButton(text=ADMIN_PERMISSION_LABELS["edit_messages"])])
    if allowed("change_card"):
        rows.append([KeyboardButton(text=ADMIN_PERMISSION_LABELS["change_card"])])
    if allowed("manage_admins"):
        rows.append([KeyboardButton(text=ADMIN_PERMISSION_LABELS["manage_admins"])])
    if allowed("backup") or allowed("restore_backup"):
        backup_row = []
        if allowed("backup"):
            backup_row.append(KeyboardButton(text=ADMIN_PERMISSION_LABELS["backup"]))
        if allowed("restore_backup"):
            backup_row.append(KeyboardButton(text=ADMIN_PERMISSION_LABELS["restore_backup"]))
        rows.append(backup_row)
    rows.append([KeyboardButton(text="🏠 Bosh menyu")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


# ─── MAJBURIY OBUNA TEKSHIRISH ───────────────────────────────────────────────

async def check_subscriptions(user_id: int):
    """
    Foydalanuvchining barcha kanallarga obunasini tekshiradi.
    Qaytaradi: obuna bo'lmagan kanallar ro'yxati.
    """
    db = await get_db()
    async with db.execute("SELECT channel_id, link, title, type FROM channels") as cur:
        channels = await cur.fetchall()

    unsubscribed = []
    for ch in channels:
        ch_id = ch["channel_id"]
        link = ch["link"]
        title = ch["title"]
        ch_type = ch["type"]

        if ch_type in ("bot", "instagram", "manual"):
            async with db.execute(
                "SELECT 1 FROM manual_confirmations WHERE user_id=? AND channel_id=?",
                (user_id, ch_id)
            ) as cur2:
                confirmed = await cur2.fetchone()
            if not confirmed:
                unsubscribed.append((ch_id, link, title, ch_type))
            continue

        # Telegram kanal/guruh — API orqali tekshirish
        if str(ch_id).lstrip("-").isdigit():
            chat_id = int(ch_id)
        else:
            chat_id = ch_id  # @username ko'rinishida

        member = await safe_get_chat_member(chat_id, user_id)
        is_subscribed = member is not None and member.status not in ("left", "kicked")

        if not is_subscribed:
            # Kanalda "Yangi a'zolarni tasdiqlash" yoqilgan bo'lishi mumkin —
            # bunday holda foydalanuvchi hali rasman a'zo emas (admin
            # tasdiqlashini kutmoqda), lekin qo'shilish SO'ROVINI yuborgan
            # bo'lsa, buni yetarli deb hisoblaymiz.
            async with db.execute(
                "SELECT 1 FROM manual_confirmations WHERE user_id=? AND channel_id=?",
                (user_id, ch_id)
            ) as cur2:
                requested = await cur2.fetchone()
            if requested:
                is_subscribed = True

        if not is_subscribed:
            unsubscribed.append((ch_id, link, title, ch_type))

    return unsubscribed

async def build_subscription_keyboard(unsub) -> InlineKeyboardMarkup:
    """
    Faqat obuna bo'linmagan kanallar ko'rsatiladi — har biri oddiy havola
    tugmasi sifatida. Pastda faqat "✅ Tekshirish" va "🌟 Premium" tugmalari
    bo'ladi ("Bajardim" kabi qo'shimcha tugmalar yo'q).
    """
    buttons = []
    for ch_id, link, title, ch_type in unsub:
        if ch_type in ("bot", "instagram", "manual"):
            icon = "🤖" if ch_type == "bot" else ("📸" if ch_type == "instagram" else "🔗")
            if link:
                buttons.append([InlineKeyboardButton(text=f"{icon} {title}", url=link)])
            else:
                buttons.append([InlineKeyboardButton(
                    text=f"{icon} {title}", callback_data="noop"
                )])
        else:
            # Telegram kanal: havola olish (public => username URL, private => yangi invite)
            real_link = await get_channel_link(ch_id, link)
            if real_link:
                buttons.append([InlineKeyboardButton(text=f"📢 {title}", url=real_link)])
            else:
                buttons.append([InlineKeyboardButton(
                    text=f"📢 {title}", callback_data="noop"
                )])
    # Faqat "Tekshirish" va "Premium" tugmalari
    buttons.append([InlineKeyboardButton(text="✅ Tekshirish", callback_data="check_sub")])
    buttons.append([InlineKeyboardButton(
        text="🌟 Premium tarifga obuna bo'lish", callback_data="req_premium"
    )])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ─── FON VAZIFALAR ───────────────────────────────────────────────────────────

async def premium_checker():
    while True:
        await asyncio.sleep(12 * 3600)
        try:
            db = await get_db()
            async with db.execute(
                "SELECT user_id, expire_date FROM premium_users"
            ) as cur:
                all_users = await cur.fetchall()
            now = datetime.now()
            notify_threshold = now + timedelta(days=NOTIFY_BEFORE_DAYS)
            for row in all_users:
                uid = row["user_id"]
                expire_str = row["expire_date"]
                if not expire_str:
                    continue
                expire = datetime.fromisoformat(expire_str)
                if now > expire:
                    await db.execute("DELETE FROM premium_users WHERE user_id=?", (uid,))
                    await db.commit()
                    try:
                        await bot.send_message(
                            uid,
                            "⏰ *Premium obunangiz muddati tugadi.*\n\n"
                            "Davom ettirish uchun 🌟 *Premium* bo'limiga o'ting.",
                            parse_mode="Markdown"
                        )
                    except Exception:
                        pass
                elif expire <= notify_threshold:
                    async with db.execute(
                        "SELECT notified FROM premium_users WHERE user_id=?", (uid,)
                    ) as cur2:
                        nrow = await cur2.fetchone()
                    if nrow and nrow["notified"] == 0:
                        days_left = (expire - now).days + 1
                        await db.execute(
                            "UPDATE premium_users SET notified=1 WHERE user_id=?", (uid,)
                        )
                        await db.commit()
                        try:
                            await bot.send_message(
                                uid,
                                f"⚠️ *Diqqat!* Premium obunangiz *{days_left} kun* ichida tugaydi.\n\n"
                                f"📅 Tugash sanasi: *{expire.strftime('%d.%m.%Y')}*\n\n"
                                f"Uzaytirish uchun @{md_escape(ADMIN_USERNAME)} bilan bog'laning.",
                                parse_mode="Markdown"
                            )
                        except Exception:
                            pass
        except Exception as e:
            logging.error(f"Premium checker xatosi: {e}")

async def backup_scheduler():
    while True:
        await asyncio.sleep(24 * 3600)
        try:
            db = await get_db()
            await db.commit()
            backup_name = f"backup_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.db"
            await bot.send_document(
                chat_id=ADMIN_ID,
                document=types.FSInputFile(DB_PATH, filename=backup_name),
                caption=f"🗄 Avtomatik zaxira nusxa\n📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
            )
        except Exception as e:
            logging.error(f"Zaxira yuborishda xato: {e}")


async def refresh_private_channel_links():
    """
    Yopiq (raqamli ID) Telegram kanallar uchun bazada saqlangan taklif
    havolasini YANGI havola bilan almashtiradi. Bu funksiya muntazam
    (avtomatik) chaqiriladi — shunda foydalanuvchilarga hech qachon
    eskirgan yoki bekor qilingan ("Expired Link") havola ko'rsatilmaydi,
    admin qo'lda "🔄 Yangilash" tugmasini bosishini kutish shart bo'lmaydi.
    Ochiq (@username) kanallarga tegilmaydi — ular eskirmaydi.
    """
    db = await get_db()
    async with db.execute(
        "SELECT channel_id FROM channels WHERE type='telegram'"
    ) as cur:
        rows = await cur.fetchall()

    for row in rows:
        ch_id = row["channel_id"]
        if str(ch_id).startswith("@"):
            continue
        try:
            chat_id_int = int(ch_id)
        except ValueError:
            continue
        try:
            invite = await asyncio.wait_for(
                bot.create_chat_invite_link(chat_id_int, creates_join_request=True),
                timeout=API_TIMEOUT
            )
            await db.execute(
                "UPDATE channels SET link=? WHERE channel_id=?",
                (invite.invite_link, ch_id)
            )
            await db.commit()
            logging.info(f"Kanal havolasi avtomatik yangilandi: {ch_id}")
        except Exception as e:
            logging.warning(f"Havolani avtomatik yangilab bo'lmadi ({ch_id}): {e}")

async def link_refresh_scheduler():
    """
    Ishga tushganda darhol, so'ngra har 6 soatda bir marta barcha yopiq
    kanallar havolasini yangilab turadi.
    """
    while True:
        try:
            await refresh_private_channel_links()
        except Exception as e:
            logging.error(f"link_refresh_scheduler xatosi: {e}")
        await asyncio.sleep(6 * 3600)


# ─── ZAXIRA OLISH ────────────────────────────────────────────────────────────

@dp.message(F.text == "🗄 Zaxira olish", StateFilter("*"))
async def manual_backup(message: types.Message, state: FSMContext):
    if not await has_admin_permission(message.from_user.id, "backup"):
        return
    await state.clear()
    try:
        db = await get_db()
        await db.commit()
        backup_name = f"backup_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.db"
        await message.answer_document(
            document=types.FSInputFile(DB_PATH, filename=backup_name),
            caption=f"🗄 Qo'lda olingan zaxira\n📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
    except Exception as e:
        await message.answer(f"❌ Zaxira olishda xato: {e}")


# ─── ZAXIRADAN TIKLASH (ADMIN) ────────────────────────────────────────────────
# Admin ilgari "🗄 Zaxira olish" orqali olingan (yoki avtomatik yuborilgan)
# .db faylni botga qaytadan yuborib, joriy bazani o'sha fayldagi bilan
# almashtira oladi. Fayl avval haqiqiy va to'liq SQLite baza ekanligiga
# tekshiriladi, admin miqdorlarni (kino/kanal/foydalanuvchi soni) ko'rib
# tasdiqlagandan keyingina almashtirish sodir bo'ladi, va almashtirishdan
# OLDIN joriy baza ehtiyot nusxa sifatida adminга yuboriladi — shunda
# xato bilan tiklangan taqdirda ham hech narsa yo'qolmaydi.

RESTORE_REQUIRED_TABLES = {"channels", "media", "users", "premium_users", "settings", "admins"}

@dp.message(F.text == "📥 Zaxira tiklash", StateFilter("*"))
async def restore_backup_start(message: types.Message, state: FSMContext):
    if not await has_admin_permission(message.from_user.id, "restore_backup"):
        return
    await state.clear()
    await state.set_state(AdminBackup.waiting_for_file)
    await message.answer(
        "📥 *Zaxiradan tiklash*\n\n"
        "Avval botdan olingan `.db` zaxira faylini shu yerga *fayl* sifatida yuboring "
        "(masalan `backup_2026-08-25_13-22.db`).\n\n"
        "⚠️ *DIQQAT:* Bu amal joriy bazani TO'LIQ almashtiradi — barcha kino, "
        "kanal, foydalanuvchi va premium ma'lumotlari shu fayldagisi bilan "
        "almashadi. Joriy baza avtomatik ravishda sizga ehtiyot nusxa qilib "
        "yuboriladi, shuning uchun xato bo'lsa ortga qaytarish mumkin.\n\n"
        "Bekor qilish: /bekor",
        parse_mode="Markdown"
    )

@dp.message(AdminBackup.waiting_for_file, F.document)
async def restore_backup_file(message: types.Message, state: FSMContext):
    if not await has_admin_permission(message.from_user.id, "restore_backup"):
        await state.clear()
        return

    doc = message.document
    if not doc.file_name or not doc.file_name.lower().endswith(".db"):
        await message.answer(
            "❌ Bu `.db` fayl emas. Faqat avval botdan olingan zaxira faylini "
            "yuboring, yoki /bekor bilan bekor qiling."
        )
        return

    tmp_path = f"/tmp/restore_upload_{message.from_user.id}.db"
    try:
        await bot.download(doc, destination=tmp_path)
    except Exception as e:
        await message.answer(f"❌ Faylni yuklab olishda xato: {e}")
        return

    try:
        test_db = await aiosqlite.connect(tmp_path)
        test_db.row_factory = aiosqlite.Row
        async with test_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ) as cur:
            rows = await cur.fetchall()
        found_tables = {r["name"] for r in rows}

        if not RESTORE_REQUIRED_TABLES.issubset(found_tables):
            missing = RESTORE_REQUIRED_TABLES - found_tables
            await test_db.close()
            os.remove(tmp_path)
            await message.answer(
                f"❌ Fayl noto'g'ri yoki to'liq emas — quyidagi jadval(lar) "
                f"topilmadi: {', '.join(missing)}.\n\n"
                "Faqat shu botning o'zi yaratgan zaxira faylini yuboring."
            )
            await state.clear()
            return

        async with test_db.execute("SELECT COUNT(*) c FROM media") as cur:
            media_count = (await cur.fetchone())["c"]
        async with test_db.execute("SELECT COUNT(*) c FROM channels") as cur:
            channel_count = (await cur.fetchone())["c"]
        async with test_db.execute("SELECT COUNT(*) c FROM users") as cur:
            user_count = (await cur.fetchone())["c"]
        await test_db.close()
    except Exception as e:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        await message.answer(f"❌ Fayl haqiqiy SQLite baza emas yoki buzilgan:\n`{e}`", parse_mode="Markdown")
        await state.clear()
        return

    await state.update_data(
        restore_tmp_path=tmp_path,
        media_count=media_count,
        channel_count=channel_count,
        user_count=user_count,
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Ha, tiklash", callback_data="confirm_restore")],
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_restore")],
    ])
    await message.answer(
        f"📦 *Fayl tekshirildi va to'g'ri ekan:*\n\n"
        f"🎬 Kino/serial/anime: *{media_count}*\n"
        f"📢 Kanallar: *{channel_count}*\n"
        f"👤 Foydalanuvchilar: *{user_count}*\n\n"
        f"Joriy bazani shu fayldagisi bilan almashtirishni tasdiqlaysizmi?",
        parse_mode="Markdown",
        reply_markup=kb
    )

@dp.message(AdminBackup.waiting_for_file)
async def restore_backup_wrong_content(message: types.Message):
    await message.answer(
        "📥 Iltimos `.db` zaxira faylini *fayl* sifatida yuboring, yoki /bekor bilan bekor qiling.",
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "cancel_restore", AdminBackup.waiting_for_file)
async def cancel_restore_cb(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    tmp_path = data.get("restore_tmp_path")
    if tmp_path and os.path.exists(tmp_path):
        try:
            os.remove(tmp_path)
        except Exception:
            pass
    await state.clear()
    try:
        await call.message.edit_text("❌ Zaxiradan tiklash bekor qilindi.")
    except Exception:
        pass
    await call.answer()

@dp.callback_query(F.data == "confirm_restore", AdminBackup.waiting_for_file)
async def confirm_restore_cb(call: types.CallbackQuery, state: FSMContext):
    global _db
    if not await has_admin_permission(call.from_user.id, "restore_backup"):
        await call.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return

    data = await state.get_data()
    tmp_path = data.get("restore_tmp_path")
    if not tmp_path or not os.path.exists(tmp_path):
        await call.answer("❌ Fayl topilmadi, qaytadan yuboring.", show_alert=True)
        await state.clear()
        return

    await call.answer("⏳ Tiklanmoqda...")

    try:
        # Almashtirishdan OLDIN joriy bazani ehtiyot nusxa sifatida yuboramiz
        safety_name = f"pre_restore_backup_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.db"
        try:
            if os.path.exists(DB_PATH):
                await bot.send_document(
                    chat_id=call.from_user.id,
                    document=types.FSInputFile(DB_PATH, filename=safety_name),
                    caption="🛟 Tiklashdan OLDINGI joriy baza (ehtiyot nusxa — kerak bo'lsa shu bilan qaytaring)"
                )
        except Exception as e:
            logging.warning(f"Tiklashdan oldingi ehtiyot nusxa yuborilmadi: {e}")

        if _db is not None:
            await _db.close()
            _db = None

        os.replace(tmp_path, DB_PATH)
        await get_db()  # ulanishni yangi fayl bilan qayta ochamiz
        await ensure_schema()  # eski zaxirada yo'q ustunlarni avtomatik qo'shadi

    except Exception as e:
        logging.error(f"Bazani tiklashda xato: {e}")
        try:
            await get_db()
        except Exception:
            pass
        await call.message.answer(f"❌ Tiklashda xato yuz berdi: {e}")
        await state.clear()
        return

    await state.clear()
    try:
        await call.message.edit_text(
            f"✅ *Baza muvaffaqiyatli tiklandi!*\n\n"
            f"🎬 Kino/serial/anime: *{data.get('media_count')}*\n"
            f"📢 Kanallar: *{data.get('channel_count')}*\n"
            f"👤 Foydalanuvchilar: *{data.get('user_count')}*\n\n"
            f"Barcha bo'limlar yangi ma'lumotlar bilan ishlamoqda.",
            parse_mode="Markdown"
        )
    except Exception:
        pass
    await call.message.answer("Bosh menyu:", reply_markup=admin_menu(call.from_user.id))


# ─── START ───────────────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    args = message.text.split(maxsplit=1)
    payload = args[1].strip() if len(args) > 1 else None
    user_id = message.from_user.id
    db = await get_db()

    async with db.execute(
        "SELECT user_id FROM users WHERE user_id=?", (user_id,)
    ) as cur:
        is_new_user = (await cur.fetchone()) is None

    await db.execute(
        "INSERT OR IGNORE INTO users (user_id, first_seen) VALUES (?, ?)",
        (user_id, datetime.now().isoformat())
    )
    await db.commit()

    # ── Referal havolasi orqali kelgan bo'lsa ──
    media_code = payload
    if payload and payload.startswith("ref_"):
        media_code = None
        if is_new_user:
            ref_id_raw = payload[4:]
            if ref_id_raw.isdigit():
                ref_id = int(ref_id_raw)
                if ref_id != user_id:
                    async with db.execute(
                        "SELECT user_id FROM users WHERE user_id=?", (ref_id,)
                    ) as cur:
                        ref_exists = await cur.fetchone()
                    if ref_exists:
                        await db.execute(
                            "UPDATE users SET referred_by=? "
                            "WHERE user_id=? AND referred_by IS NULL",
                            (ref_id, user_id)
                        )
                        await db.commit()
                        try:
                            await bot.send_message(
                                ref_id,
                                await get_message_template("referral_pending"),
                                parse_mode="Markdown"
                            )
                        except Exception:
                            pass

    is_prem = await is_premium_user(user_id)

    if media_code:
        if not is_prem:
            unsub = await check_subscriptions(user_id)
            if unsub:
                kb = await build_subscription_keyboard(unsub)
                await message.answer(
                    await get_message_template("subscribe_required"),
                    reply_markup=kb, parse_mode="Markdown"
                )
                return
            await process_referral_reward_if_needed(user_id)
        await message.answer(
            await get_message_template("welcome"),
            reply_markup=main_menu(user_id)
        )
        await deliver_media_by_code(message, user_id, media_code)
        return

    if is_prem:
        await message.answer(
            await get_message_template("welcome"),
            reply_markup=main_menu(user_id)
        )
        return

    unsub = await check_subscriptions(user_id)
    if unsub:
        kb = await build_subscription_keyboard(unsub)
        await message.answer(
            await get_message_template("subscribe_required"),
            reply_markup=kb, parse_mode="Markdown"
        )
        return
    await process_referral_reward_if_needed(user_id)
    await message.answer(
        await get_message_template("welcome"),
        reply_markup=main_menu(user_id)
    )

@dp.callback_query(F.data == "noop")
async def noop_cb(call: types.CallbackQuery):
    await call.answer("🔒 Bu maxfiy kanal. Admin orqali qo'shiling.", show_alert=True)

@dp.callback_query(F.data == "check_sub")
async def check_sub_cb(call: types.CallbackQuery):
    user_id = call.from_user.id
    if await is_premium_user(user_id):
        try:
            await call.message.delete()
        except Exception:
            pass
        await call.message.answer(
            "✅ Siz Premium a'zosiz! Menyudan foydalanishingiz mumkin:",
            reply_markup=main_menu(user_id)
        )
        await call.answer()
        return

    # bot/instagram/manual kanallar uchun manual tasdiq (API orqali tekshirib bo'lmaydi)
    db = await get_db()
    async with db.execute("SELECT channel_id, type FROM channels") as cur:
        all_channels = await cur.fetchall()
    for ch in all_channels:
        if ch["type"] in ("bot", "instagram", "manual"):
            await db.execute(
                "INSERT OR REPLACE INTO manual_confirmations "
                "(user_id, channel_id, confirmed_at) VALUES (?, ?, ?)",
                (user_id, ch["channel_id"], datetime.now().isoformat())
            )
    await db.commit()

    unsub = await check_subscriptions(user_id)
    if unsub:
        # Faqat hali obuna bo'linmagan kanallarni ko'rsat
        kb = await build_subscription_keyboard(unsub)
        try:
            await call.message.edit_text(
                "⚠️ Quyidagi kanal(lar)ga hali obuna bo'lmadingiz:\n\n"
                "ℹ️ _Premium a'zolarga majburiy kanal obunasi talab qilinmaydi!_",
                reply_markup=kb,
                parse_mode="Markdown"
            )
        except Exception:
            await call.message.answer(
                "⚠️ Quyidagi kanal(lar)ga hali obuna bo'lmadingiz:",
                reply_markup=kb
            )
        await call.answer("❌ Hali barcha kanallarga obuna bo'lmadingiz!", show_alert=True)
    else:
        await process_referral_reward_if_needed(user_id)
        try:
            await call.message.delete()
        except Exception:
            pass
        await call.message.answer(
            "✅ Obuna tasdiqlandi! Menyudan foydalanishingiz mumkin:",
            reply_markup=main_menu(user_id)
        )
        await call.answer()


# ─── KANALDAN / BOTDAN CHIQIB KETISHNI KUZATISH (REFERAL JARIMASI) ───────────
# Telegram bot ADMIN bo'lgan kanallarda a'zolik holati o'zgarganda
# "chat_member" hodisasini yuboradi, xuddi shunday foydalanuvchi botni
# bloklasa/blokdan chiqarsa "my_chat_member" hodisasini yuboradi. Shu ikkala
# hodisa orqali "kimdir majburiy kanaldan yoki botning o'zidan chiqib ketdi"
# holatini REAL VAQTDA aniqlaymiz va kerak bo'lsa referal mukofotini
# taklif qilgan odamning hisobidan ayirib olamiz.

LEFT_STATUSES = ("left", "kicked")
ACTIVE_STATUSES = ("member", "administrator", "creator")

async def _is_tracked_channel(chat: types.Chat) -> bool:
    db = await get_db()
    async with db.execute(
        "SELECT channel_id FROM channels WHERE type='telegram'"
    ) as cur:
        tracked = await cur.fetchall()
    for row in tracked:
        ch_id = row["channel_id"]
        if str(ch_id).lstrip("-").isdigit():
            if int(ch_id) == chat.id:
                return True
        else:
            uname = str(ch_id).lstrip("@")
            if chat.username and chat.username.lower() == uname.lower():
                return True
    return False

@dp.chat_member()
async def on_channel_membership_change(event: types.ChatMemberUpdated):
    try:
        member_user = event.new_chat_member.user
        if member_user.is_bot:
            return
        if not await _is_tracked_channel(event.chat):
            return

        new_status = event.new_chat_member.status
        old_status = event.old_chat_member.status

        if new_status in LEFT_STATUSES and old_status not in LEFT_STATUSES:
            await process_referral_penalty(
                member_user.id, "majburiy kanal(lar)dan chiqib ketdi"
            )
        elif new_status in ACTIVE_STATUSES and old_status in LEFT_STATUSES:
            # Qayta qo'shildi — endi haqiqatan HAMMA majburiy kanallarga
            # obuna bo'lganini tekshirib, kerak bo'lsa mukofotni qayta beramiz
            unsub = await check_subscriptions(member_user.id)
            if not unsub:
                await process_referral_reward_if_needed(member_user.id)
    except Exception as e:
        logging.error(f"chat_member hodisasida xato: {e}")

@dp.my_chat_member()
async def on_bot_blocked_or_unblocked(event: types.ChatMemberUpdated):
    try:
        if event.chat.type != "private":
            return
        new_status = event.new_chat_member.status
        old_status = event.old_chat_member.status
        user_id = event.chat.id

        if new_status == "kicked" and old_status != "kicked":
            # Foydalanuvchi botni bloklab, undan "chiqib ketdi"
            await process_referral_penalty(
                user_id, "botning o'zini bloklab, undan chiqib ketdi"
            )
    except Exception as e:
        logging.error(f"my_chat_member hodisasida xato: {e}")


# ─── MEDIA YETKAZIB BERISH ───────────────────────────────────────────────────

async def deliver_media_by_code(sendable, user_id: int, code: str):
    try:
        db = await get_db()
        async with db.execute(
            "SELECT id, file_id, title, part, is_premium, category, views, likes, dislikes "
            "FROM media WHERE code=? ORDER BY part ASC",
            (code,)
        ) as cur:
            results = await cur.fetchall()

        if not results:
            await sendable.answer("❌ Bunday kodli kino, serial yoki anime topilmadi.")
            return

        is_prem_content = results[0]["is_premium"]
        if is_prem_content and not await is_premium_user(user_id):
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🌟 Premium olish", callback_data="req_premium")],
                [InlineKeyboardButton(
                    text="👨‍💻 Admin bilan bog'lanish",
                    url=f"https://t.me/{ADMIN_USERNAME}"
                )]
            ])
            await sendable.answer(
                "🔒 Bu *Premium* kontent!\n\nTomosha qilish uchun premium a'zo bo'ling.",
                reply_markup=kb, parse_mode="Markdown"
            )
            return

        user_is_admin = await is_bot_admin(user_id)
        # Saqlash/boshqa joyga yuborish FAQAT adminlarga ochiq — Premium
        # a'zolar ham (oddiy foydalanuvchi kabi) videoni saqlay olmaydi,
        # faqat pastdagi silka orqali ulashishi mumkin.
        can_save = user_is_admin

        for row in results:
            media_id = row["id"]
            file_id = row["file_id"]
            title = row["title"]
            part = row["part"]
            category = row["category"]
            views = (row["views"] or 0) + 1
            likes = row["likes"] or 0
            dislikes = row["dislikes"] or 0

            await db.execute("UPDATE media SET views=? WHERE id=?", (views, media_id))
            await db.commit()

            cat_icon = {"kino": "🎬", "serial": "📺", "anime": "⛩"}.get(category, "🎬")
            silka = f"https://t.me/{BOT_USERNAME}?start={code}" if BOT_USERNAME else None
            title_safe = md_escape(title)
            code_safe = md_escape(code)

            caption = (
                f"{cat_icon} {title_safe}\n"
                f"📌 Qism: {part}\n"
                f"🔑 Kod: {code_safe}\n"
                f"👁 Ko'rildi: {views:,} marta"
            )
            if not can_save:
                caption += (
                    "\n\n🔒 _Ushbu videoni saqlash yoki boshqa joyga yuborish "
                    "mumkin emas._\n"
                    "📤 _Do'stlaringizga ulashish uchun quyidagi silkani yuboring:_"
                )
            if silka:
                caption += f"\n🔗 {md_escape(silka)}"

            rating_kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text=f"👍 {likes}", callback_data=f"vote_{media_id}_1"
                ),
                InlineKeyboardButton(
                    text=f"👎 {dislikes}", callback_data=f"vote_{media_id}_0"
                ),
            ]])
            try:
                await sendable.answer_video(
                    video=file_id,
                    caption=caption,
                    parse_mode="Markdown",
                    protect_content=not can_save,
                    reply_markup=rating_kb
                )
            except Exception as e:
                logging.error(f"Video yuborishda xato (media_id={media_id}, file_id={file_id}): {e}")
                if user_is_admin:
                    # Diagnostika xabari ATAYIN parse_mode'siz (oddiy matn)
                    # yuboriladi — chunki xato matnining o'zida ham Markdown'ga
                    # xalaqit beradigan belgilar bo'lishi mumkin.
                    await sendable.answer(
                        f"❌ '{title}' ({part}-qism) videoni yuborib bo'lmadi.\n\n"
                        f"🛠 Xato tafsiloti (faqat admin ko'radi):\n{e}\n\n"
                        f"🆔 media_id: {media_id}"
                    )
                else:
                    await sendable.answer(f"❌ '{title}' videoni yuborib bo'lmadi.")

            # Telegram tezlik cheklovi (flood control)ga urilmaslik uchun —
            # bir nechta qismni ketma-ket yuborganda kichik tanaffus.
            await asyncio.sleep(0.4)

    except Exception as e:
        # Yuqoridagi ichki try/except faqat video yuborish xatolarini
        # ushlaydi. Agar funksiyaning boshqa qismida (masalan baza bilan
        # ishlashda) kutilmagan xato chiqsa, foydalanuvchi hech qanday
        # javob olmay "hech narsa bo'lmagandek" qolib ketmasligi uchun bu
        # tashqi himoya qo'shildi.
        # "query is too old" — muddati o'tgan callback query, jimgina o'tkazib yubor
        if isinstance(e, TelegramBadRequest) and "query is too old" in str(e):
            logging.warning(f"Muddati o'tgan callback query (code={code}, user={user_id})")
            return
        logging.error(f"deliver_media_by_code kutilmagan xato (code={code}, user={user_id}): {e}")
        try:
            if await is_bot_admin(user_id):
                await sendable.answer(f"❌ Kutilmagan xato yuz berdi.\n\n🛠 {e}")
            else:
                await sendable.answer("❌ Xatolik yuz berdi. Birozdan so'ng qaytadan urinib ko'ring.")
        except Exception:
            pass


# ─── RO'YXATLAR ──────────────────────────────────────────────────────────────

CATEGORY_INFO = {
    "kino":   {"icon": "🎬", "label": "Kinolar",
               "desc": "Eng saralangan va o'zbek tiliga tarjima qilingan kinolar!"},
    "serial": {"icon": "📺", "label": "Seriallar",
               "desc": "Eng mashhur va qiziqarli seriallar!"},
    "anime":  {"icon": "⛩", "label": "Anime va Multfilmlar",
               "desc": "Afsonaviy anime seriyalar va qiziqarli multfilmlar!"},
}

def build_media_list_kb(items, category: str, page: int):
    start = page * PAGE_SIZE
    page_items = items[start:start + PAGE_SIZE]
    buttons = [
        [InlineKeyboardButton(
            text=f"🎞 {title}", callback_data=f"getmedia_{code}"
        )]
        for code, title in page_items
    ]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="⬅️ Oldingi", callback_data=f"page_{category}_{page - 1}"
        ))
    if start + PAGE_SIZE < len(items):
        nav.append(InlineKeyboardButton(
            text="Keyingi ➡️", callback_data=f"page_{category}_{page + 1}"
        ))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(
        text="⬅️ Orqaga", callback_data="close_list"
    )])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

async def render_media_list(category: str, page: int = 0):
    db = await get_db()
    async with db.execute(
        "SELECT DISTINCT code, title FROM media "
        "WHERE category=? AND is_premium=0 ORDER BY id DESC",
        (category,)
    ) as cur:
        items = await cur.fetchall()
    items = [(row["code"], row["title"]) for row in items]
    info = CATEGORY_INFO[category]

    if not items:
        text = (
            f"{info['icon']} ✦ *{info['label']} Bo'limi* ✦\n"
            f"ℹ️ _{info['desc']}_\n\n"
            f"😔 Hozircha ochiq {info['label'].lower()} mavjud emas."
        )
        return text, None

    total_pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    text = (
        f"{info['icon']} ✦ *{info['label']} Bo'limi* ✦\n"
        f"ℹ️ _{info['desc']}_\n"
        f"📄 Sahifa {page + 1}/{total_pages}\n\n"
        f"👇 Kerakli tanlang:"
    )
    return text, build_media_list_kb(items, category, page)

@dp.message(F.text == "🎬 Kinolar")
async def list_movies(message: types.Message):
    unsub = await check_subscriptions(message.from_user.id)
    if unsub and not await is_premium_user(message.from_user.id):
        kb = await build_subscription_keyboard(unsub)
        await message.answer(
            "⚠️ Avval kanallarga obuna bo'ling:", reply_markup=kb
        )
        return
    text, kb = await render_media_list("kino", 0)
    await message.answer(text, parse_mode="Markdown", reply_markup=kb)

@dp.message(F.text == "📺 Seriallar")
async def list_serials(message: types.Message):
    unsub = await check_subscriptions(message.from_user.id)
    if unsub and not await is_premium_user(message.from_user.id):
        kb = await build_subscription_keyboard(unsub)
        await message.answer(
            "⚠️ Avval kanallarga obuna bo'ling:", reply_markup=kb
        )
        return
    text, kb = await render_media_list("serial", 0)
    await message.answer(text, parse_mode="Markdown", reply_markup=kb)

@dp.message(F.text == "⛩ Anime va Multfilm")
async def list_anime(message: types.Message):
    unsub = await check_subscriptions(message.from_user.id)
    if unsub and not await is_premium_user(message.from_user.id):
        kb = await build_subscription_keyboard(unsub)
        await message.answer(
            "⚠️ Avval kanallarga obuna bo'ling:", reply_markup=kb
        )
        return
    text, kb = await render_media_list("anime", 0)
    await message.answer(text, parse_mode="Markdown", reply_markup=kb)

@dp.callback_query(F.data.startswith("prempage_"))
async def prempage_cb(call: types.CallbackQuery):
    page = int(call.data.split("_")[1])
    text, kb = await render_premium_list(page)
    try:
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
    except Exception:
        pass
    await call.answer()

@dp.callback_query(F.data.startswith("page_"))
async def page_cb(call: types.CallbackQuery):
    parts = call.data.split("_")
    category = parts[1]
    page = int(parts[2])
    text, kb = await render_media_list(category, page)
    try:
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
    except Exception:
        pass
    await call.answer()

@dp.callback_query(F.data.startswith("getmedia_"))
async def getmedia_cb(call: types.CallbackQuery):
    code = call.data[len("getmedia_"):]
    user_id = call.from_user.id
    unsub = await check_subscriptions(user_id)
    if unsub and not await is_premium_user(user_id):
        kb = await build_subscription_keyboard(unsub)
        await call.message.answer("⚠️ Avval kanallarga obuna bo'ling:", reply_markup=kb)
        await call.answer()
        return
    await deliver_media_by_code(call.message, user_id, code)
    await call.answer()

@dp.callback_query(F.data == "close_list")
async def close_list_cb(call: types.CallbackQuery):
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.answer()


# ─── REYTING OVOZ ────────────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("vote_"))
async def vote_cb(call: types.CallbackQuery):
    parts = call.data.split("_")
    media_id = int(parts[1])
    vote_value = int(parts[2])
    user_id = call.from_user.id
    db = await get_db()

    async with db.execute(
        "SELECT vote FROM media_votes WHERE user_id=? AND media_id=?",
        (user_id, media_id)
    ) as cur:
        existing = await cur.fetchone()

    if existing and existing["vote"] == vote_value:
        await call.answer("Siz allaqachon shu ovozni bergansiz.")
        return

    if existing:
        old_col = "likes" if existing["vote"] == 1 else "dislikes"
        await db.execute(
            f"UPDATE media SET {old_col} = MAX({old_col} - 1, 0) WHERE id=?", (media_id,)
        )
        await db.execute(
            "UPDATE media_votes SET vote=? WHERE user_id=? AND media_id=?",
            (vote_value, user_id, media_id)
        )
    else:
        await db.execute(
            "INSERT INTO media_votes (user_id, media_id, vote) VALUES (?, ?, ?)",
            (user_id, media_id, vote_value)
        )

    new_col = "likes" if vote_value == 1 else "dislikes"
    await db.execute(
        f"UPDATE media SET {new_col} = {new_col} + 1 WHERE id=?", (media_id,)
    )
    await db.commit()

    async with db.execute(
        "SELECT likes, dislikes FROM media WHERE id=?", (media_id,)
    ) as cur:
        row = await cur.fetchone()
    likes = row["likes"] or 0
    dislikes = row["dislikes"] or 0
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=f"👍 {likes}", callback_data=f"vote_{media_id}_1"
        ),
        InlineKeyboardButton(
            text=f"👎 {dislikes}", callback_data=f"vote_{media_id}_0"
        ),
    ]])
    try:
        await call.message.edit_reply_markup(reply_markup=kb)
    except Exception:
        pass
    await call.answer("✅ Ovozingiz qabul qilindi!")


# ─── QIDIRUV ─────────────────────────────────────────────────────────────────

@dp.message(F.text == "🔍 Kod orqali qidirish")
async def ask_code(message: types.Message, state: FSMContext):
    unsub = await check_subscriptions(message.from_user.id)
    if unsub and not await is_premium_user(message.from_user.id):
        kb = await build_subscription_keyboard(unsub)
        await message.answer("⚠️ Avval kanallarga obuna bo'ling:", reply_markup=kb)
        return
    await state.set_state(CodeSearch.waiting_for_code)
    await message.answer(
        await get_message_template("code_search_prompt"),
        parse_mode="Markdown"
    )

@dp.message(CodeSearch.waiting_for_code)
async def search_by_code(message: types.Message, state: FSMContext):
    code = message.text.strip()
    await deliver_media_by_code(message, message.from_user.id, code)
    await state.clear()


# ─── PREMIUM BO'LIM ──────────────────────────────────────────────────────────

def build_premium_list_kb(items, page: int):
    start = page * PAGE_SIZE
    page_items = items[start:start + PAGE_SIZE]
    icon_map = {"kino": "🎬", "serial": "📺", "anime": "⛩"}
    buttons = [
        [InlineKeyboardButton(
            text=f"{icon_map.get(cat, '🌟')} {title}",
            callback_data=f"getmedia_{code}"
        )]
        for code, title, cat in page_items
    ]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="⬅️ Oldingi", callback_data=f"prempage_{page - 1}"
        ))
    if start + PAGE_SIZE < len(items):
        nav.append(InlineKeyboardButton(
            text="Keyingi ➡️", callback_data=f"prempage_{page + 1}"
        ))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(
        text="⬅️ Orqaga", callback_data="close_list"
    )])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

async def render_premium_list(page: int = 0):
    db = await get_db()
    async with db.execute(
        "SELECT DISTINCT code, title, category FROM media "
        "WHERE is_premium=1 ORDER BY id DESC"
    ) as cur:
        rows = await cur.fetchall()
    items = [(row["code"], row["title"], row["category"]) for row in rows]

    if not items:
        return "Hozircha premium kontent joylanmagan.", None

    total_pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    text = (
        "🔒 *Premium kontent ro'yxati:*\n"
        f"📄 Sahifa {page + 1}/{total_pages}\n\n"
        "👇 Kerakli tanlang:"
    )
    return text, build_premium_list_kb(items, page)

@dp.message(F.text == "🌟 Premium", StateFilter(None))
async def premium_info(message: types.Message):
    user_id = message.from_user.id
    price = await get_premium_price(30)

    if await is_premium_user(user_id):
        expire = await get_expire_date(user_id)
        expire_str = expire.strftime("%d.%m.%Y") if expire else "Noma'lum"
        text, kb = await render_premium_list(0)
        await message.answer(
            f"🌟 *Siz Premium a'zosiz!*\n📅 Muddat: *{expire_str}* gacha\n\n{text}",
            parse_mode="Markdown", reply_markup=kb
        )
        return

    kb = await build_premium_payment_kb(user_id)
    advantages = await get_message_template("premium_advantages")
    await message.answer(
        f"🌟 *Premium a'zolik*\n\n"
        f"{advantages}\n\n"
        f"💰 *Narxi:* {price} so'm / {PREMIUM_DAYS} kun\n\n"
        f"👨‍💻 *Admin:* @{md_escape(ADMIN_USERNAME)}\n\n"
        f"To'lov usulini tanlang:",
        parse_mode="Markdown", reply_markup=kb
    )


# ─── TO'LOV ───────────────────────────────────────────────────────────────────

async def build_premium_payment_kb(user_id: int) -> InlineKeyboardMarkup:
    price_str = await get_premium_price(30)
    balance = await get_balance(user_id)
    rows = [
        [InlineKeyboardButton(
            text=f"💳 Karta orqali to'lash ({price_str} so'm)",
            callback_data="pay_card"
        )],
    ]
    if balance > 0:
        rows.append([InlineKeyboardButton(
            text=f"💰 Balansdan to'lash (mavjud: {balance:,} so'm)",
            callback_data="pay_balance"
        )])
    rows.append([InlineKeyboardButton(
        text="👨‍💻 Admin bilan bog'lanish",
        url=f"https://t.me/{ADMIN_USERNAME}"
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)

@dp.callback_query(F.data == "req_premium")
async def req_premium_cb(call: types.CallbackQuery):
    price = await get_premium_price(30)
    kb = await build_premium_payment_kb(call.from_user.id)
    await call.message.answer(
        f"🌟 *Premium a'zolik — {price} so'm / {PREMIUM_DAYS} kun*\n\n"
        f"Qaysi usul orqali to'lamoqchisiz?",
        parse_mode="Markdown", reply_markup=kb
    )
    await call.answer()

@dp.callback_query(F.data == "pay_card")
async def pay_card_cb(call: types.CallbackQuery, state: FSMContext):
    card_number, card_holder = await get_card_info()
    holder_line = f"\n👤 *Karta egasi:* {md_escape(card_holder)}" if card_holder else ""
    price = await get_premium_price(30)
    instructions = await get_message_template("payment_instructions")
    await state.set_state(PaymentReceipt.waiting_for_receipt)
    await call.message.answer(
        f"💳 *Premium uchun to'lov*\n\n"
        f"💳 Karta raqami: `{card_number}`{holder_line}\n"
        f"💰 Summasi: *{price} so'm* / {PREMIUM_DAYS} kun\n\n"
        f"{instructions}",
        parse_mode="Markdown"
    )
    await call.answer()

@dp.callback_query(F.data == "pay_balance")
async def pay_balance_cb(call: types.CallbackQuery):
    user_id = call.from_user.id
    if await is_premium_user(user_id):
        await call.answer("✅ Siz allaqachon Premium a'zosiz!", show_alert=True)
        return

    price_str = await get_premium_price(30)
    try:
        price = int(price_str.replace(",", "").replace(" ", ""))
    except ValueError:
        price = 0
    balance = await get_balance(user_id)

    if balance < price:
        shortfall = price - balance
        await call.answer(
            f"❌ Balansingiz yetarli emas. Yana {shortfall:,} so'm kerak.",
            show_alert=True
        )
        return

    db = await get_db()
    await db.execute(
        "UPDATE users SET balance = balance - ? WHERE user_id=?", (price, user_id)
    )
    await db.commit()
    expire = await add_premium(user_id, PREMIUM_DAYS)
    expire_str = expire.strftime("%d.%m.%Y")
    new_balance = await get_balance(user_id)

    text = (
        f"✅ *Balansdan {price:,} so'm yechildi va Premium faollashtirildi!*\n\n"
        f"📅 Muddat: *{expire_str}* gacha\n"
        f"💳 Qolgan balans: *{new_balance:,} so'm*"
    )
    try:
        await call.message.edit_text(text, parse_mode="Markdown")
    except Exception:
        await call.message.answer(text, parse_mode="Markdown")
    await call.answer()


# ─── HISOBIM (REFERAL / BALANS) ───────────────────────────────────────────────

@dp.message(F.text == "💰 Hisobim", StateFilter("*"))
async def my_account(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    balance = await get_balance(user_id)
    total_refs, active_refs = await get_referral_stats(user_id)
    reward = await get_referral_reward()
    ref_link = (
        f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"
        if BOT_USERNAME else "⏳ Havola hali tayyorlanmoqda, birozdan so'ng urinib ko'ring."
    )

    referred = await get_referral_list(user_id, limit=15)
    if referred:
        lines = []
        for r in referred:
            status_icon = "✅" if r["referral_credited"] else "⏳"
            try:
                seen = datetime.fromisoformat(r["first_seen"]).strftime("%d.%m.%Y")
            except Exception:
                seen = "-"
            lines.append(f"{status_icon} `{r['user_id']}` — {seen}")
        ref_text = "\n".join(lines)
        if total_refs > len(referred):
            ref_text += f"\n… va yana {total_refs - len(referred)} kishi"
    else:
        ref_text = "_Hali hech kimni taklif qilmagansiz._"

    kb_rows = []
    if BOT_USERNAME:
        kb_rows.append([InlineKeyboardButton(
            text="📤 Do'stlarga ulashish",
            url=f"https://t.me/share/url?url={ref_link}"
        )])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows) if kb_rows else None

    await message.answer(
        f"💰 *Mening hisobim*\n\n"
        f"💳 Balans: *{balance:,} so'm*\n"
        f"👥 Jami taklif qilinganlar: *{total_refs}* kishi\n"
        f"✅ Hozir faol (pul hisoblangan): *{active_refs}* kishi\n"
        f"🎁 Har bir faol taklif uchun: *{reward:,} so'm*\n\n"
        f"🔗 *Sizning taklif havolangiz:*\n`{ref_link}`\n\n"
        f"ℹ️ _Taklif qilgan odamingiz botning barcha majburiy kanallariga "
        f"obuna bo'lgandan so'nggina pul hisobingizga tushadi (⏳ = kutilmoqda, "
        f"✅ = hisoblangan). Agar u keyinchalik kanal(lar)dan yoki botning "
        f"o'zidan chiqib ketsa, pul hisobingizdan qayta ayirib olinadi._\n"
        f"💡 _Balansdagi pulga \"🌟 Premium\" bo'limidan to'lash mumkin._\n\n"
        f"👇 *So'nggi takliflaringiz:*\n{ref_text}",
        parse_mode="Markdown",
        reply_markup=kb
    )

@dp.message(PaymentReceipt.waiting_for_receipt, F.photo)
async def receive_payment_receipt(message: types.Message, state: FSMContext):
    user = message.from_user
    uname = f"@{md_escape(user.username)}" if user.username else "username yo'q"
    full_name_safe = md_escape(user.full_name)
    sent_time = datetime.now().strftime("%d.%m.%Y %H:%M")
    kb_admin = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✅ Tasdiqlash", callback_data=f"approve_prem_{user.id}"
        ),
        InlineKeyboardButton(
            text="❌ Rad etish", callback_data=f"reject_prem_{user.id}"
        )
    ]])
    try:
        await bot.send_photo(
            chat_id=ADMIN_ID,
            photo=message.photo[-1].file_id,
            caption=(
                f"🧾 *Yangi to'lov cheki!*\n\n"
                f"👤 Ism: {full_name_safe}\n"
                f"🔗 Username: {uname}\n"
                f"🆔 ID: `{user.id}`\n"
                f"🕒 Yuborilgan vaqt: {sent_time}\n\n"
                f"✅ Tasdiqlasangiz *{PREMIUM_DAYS} kun* premium beriladi."
            ),
            reply_markup=kb_admin,
            parse_mode="Markdown"
        )
        await message.answer(
            "✅ Chekingiz qabul qilindi va adminga yuborildi!\nTekshirilgach, Premium tasdiqlanadi."
        )
    except Exception:
        await message.answer(
            f"❌ Xatolik yuz berdi. Iltimos, chekni to'g'ridan-to'g'ri adminga yuboring: "
            f"@{ADMIN_USERNAME}"
        )
    await state.clear()

@dp.message(PaymentReceipt.waiting_for_receipt)
async def receipt_wrong_format(message: types.Message):
    await message.answer(
        "⚠️ Iltimos, to'lov chekining *rasmini (screenshot)* yuboring — "
        "matn qabul qilinmaydi.",
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("approve_prem_"))
async def approve_premium(call: types.CallbackQuery):
    target_id = int(call.data.split("_")[2])
    expire = await add_premium(target_id, PREMIUM_DAYS)
    expire_str = expire.strftime("%d.%m.%Y")
    await call.message.edit_text(
        f"✅ Foydalanuvchi `{target_id}` Premium a'zolikka qo'shildi!\n"
        f"📅 Muddat: *{expire_str}* gacha",
        parse_mode="Markdown"
    )
    try:
        await bot.send_message(
            target_id,
            f"🎉 *Tabriklaymiz!* Premium obunangiz tasdiqlandi.\n\n"
            f"📅 *Muddat:* {PREMIUM_DAYS} kun ({expire_str} gacha)\n\n"
            f"🌟 *Premium* tugmasini bosib barcha eksklyuziv kinolarni tomosha qiling!",
            parse_mode="Markdown"
        )
    except Exception:
        pass

@dp.callback_query(F.data.startswith("reject_prem_"))
async def reject_premium(call: types.CallbackQuery):
    target_id = int(call.data.split("_")[2])
    await call.message.edit_text(
        f"❌ Foydalanuvchi `{target_id}` so'rovi rad etildi.",
        parse_mode="Markdown"
    )
    try:
        await bot.send_message(
            target_id,
            "❌ Afsuski, to'lovingiz tasdiqlanmadi.\n\n"
            "Muammo bo'lsa, adminimizga murojaat qiling."
        )
    except Exception:
        pass


# ─── ADMIN PANEL ─────────────────────────────────────────────────────────────

@dp.message(Command("admin"), StateFilter("*"))
async def admin_panel(message: types.Message, state: FSMContext):
    if not await is_bot_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer(
        "👨‍💻 Admin panelga xush kelibsiz!",
        reply_markup=admin_menu(message.from_user.id)
    )

@dp.message(Command("bekor"), StateFilter("*"))
async def cancel_cmd(message: types.Message, state: FSMContext):
    await state.clear()
    is_adm = await is_bot_admin(message.from_user.id)
    menu = admin_menu(message.from_user.id) if is_adm else main_menu(message.from_user.id)
    await message.answer("❌ Amal bekor qilindi.", reply_markup=menu)

@dp.message(F.text == "🏠 Bosh menyu", StateFilter("*"))
async def back_to_main(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Bosh menyu:", reply_markup=main_menu(message.from_user.id))


# ─── PREMIUM BERISH (ADMIN) ───────────────────────────────────────────────────

@dp.message(F.text == "👥 Premium berish", StateFilter("*"))
async def admin_give_premium(message: types.Message, state: FSMContext):
    if not await has_admin_permission(message.from_user.id, "give_premium"):
        return
    await state.clear()
    await state.update_data(action="give")
    await state.set_state(AdminPremium.waiting_user_id)
    await message.answer(
        "Premium bermoqchi bo'lgan foydalanuvchining *Telegram ID* sini kiriting:\n\n"
        "_(ID ni bilish uchun foydalanuvchi @userinfobot ga yozsin)_",
        parse_mode="Markdown",
        reply_markup=types.ReplyKeyboardRemove()
    )

@dp.message(F.text == "❌ Premium olish", StateFilter("*"))
async def admin_remove_premium_cmd(message: types.Message, state: FSMContext):
    if not await has_admin_permission(message.from_user.id, "remove_premium"):
        return
    await state.clear()
    await state.update_data(action="remove")
    await state.set_state(AdminPremium.waiting_user_id)
    await message.answer(
        "Premium *olmoqchi* bo'lgan foydalanuvchining *Telegram ID* sini kiriting:",
        parse_mode="Markdown",
        reply_markup=types.ReplyKeyboardRemove()
    )

@dp.message(AdminPremium.waiting_user_id)
async def process_premium_action(message: types.Message, state: FSMContext):
    data = await state.get_data()
    required_permission = "give_premium" if data.get("action", "give") == "give" else "remove_premium"
    if not await has_admin_permission(message.from_user.id, required_permission):
        await state.clear()
        await message.answer("⛔ Sizda bu amal uchun huquq yo'q.")
        return
    try:
        target_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Noto'g'ri ID. Faqat raqam kiriting.")
        return

    action = data.get("action", "give")
    if action == "give":
        expire = await add_premium(target_id, PREMIUM_DAYS)
        expire_str = expire.strftime("%d.%m.%Y")
        await message.answer(
            f"✅ Foydalanuvchi `{target_id}` ga {PREMIUM_DAYS} kunlik Premium berildi.\n"
            f"📅 Tugash sanasi: *{expire_str}*",
            reply_markup=admin_menu(message.from_user.id), parse_mode="Markdown"
        )
        try:
            await bot.send_message(
                target_id,
                f"🎉 *Tabriklaymiz!* Sizga {PREMIUM_DAYS} kunlik Premium berildi.\n"
                f"📅 *Muddat:* {expire_str} gacha\n\n"
                f"🌟 *Premium* tugmasini bosib tomosha qiling!",
                parse_mode="Markdown"
            )
        except Exception:
            pass
    else:
        if await is_premium_user(target_id):
            await remove_premium(target_id)
            await message.answer(
                f"✅ Foydalanuvchi `{target_id}` ning Premium obunasi bekor qilindi.",
                reply_markup=admin_menu(message.from_user.id), parse_mode="Markdown"
            )
            try:
                await bot.send_message(
                    target_id,
                    "❌ Sizning Premium obunangiz admin tomonidan bekor qilindi."
                )
            except Exception:
                pass
        else:
            await message.answer(
                f"⚠️ Foydalanuvchi `{target_id}` premium a'zo emas.",
                reply_markup=admin_menu(message.from_user.id), parse_mode="Markdown"
            )
    await state.clear()

@dp.message(F.text == "📊 Premium ro'yxati", StateFilter("*"))
async def premium_list_admin(message: types.Message, state: FSMContext):
    if not await has_admin_permission(message.from_user.id, "premium_list"):
        return
    await state.clear()
    db = await get_db()
    async with db.execute(
        "SELECT user_id, expire_date FROM premium_users ORDER BY expire_date DESC"
    ) as cur:
        rows = await cur.fetchall()
    if not rows:
        await message.answer("Hozircha premium a'zolar yo'q.")
        return
    text = "🌟 *Premium a'zolar:*\n\n"
    for row in rows:
        exp = datetime.fromisoformat(row["expire_date"]) if row["expire_date"] else None
        exp_str = exp.strftime("%d.%m.%Y") if exp else "Noma'lum"
        status = "✅" if exp and exp > datetime.now() else "❌"
        text += f"{status} `{row['user_id']}` — {exp_str}\n"
    await message.answer(text, parse_mode="Markdown")


# ─── STATISTIKA ───────────────────────────────────────────────────────────────

@dp.message(F.text == "📊 Statistika", StateFilter("*"))
async def statistics(message: types.Message, state: FSMContext):
    if not await has_admin_permission(message.from_user.id, "statistics"):
        return
    await state.clear()
    db = await get_db()
    async with db.execute("SELECT COUNT(*) as cnt FROM users") as cur:
        users_count = (await cur.fetchone())["cnt"]
    async with db.execute(
        "SELECT COUNT(*) as cnt FROM premium_users WHERE expire_date > ?",
        (datetime.now().isoformat(),)
    ) as cur:
        prem_count = (await cur.fetchone())["cnt"]
    async with db.execute(
        "SELECT COUNT(DISTINCT code) as cnt FROM media WHERE is_premium=0"
    ) as cur:
        free_media = (await cur.fetchone())["cnt"]
    async with db.execute(
        "SELECT COUNT(DISTINCT code) as cnt FROM media WHERE is_premium=1"
    ) as cur:
        prem_media = (await cur.fetchone())["cnt"]
    async with db.execute("SELECT COUNT(*) as cnt FROM channels") as cur:
        channels_count = (await cur.fetchone())["cnt"]

    await message.answer(
        f"📊 *Bot statistikasi:*\n\n"
        f"👥 Jami foydalanuvchilar: *{users_count:,}*\n"
        f"🌟 Faol premium a'zolar: *{prem_count:,}*\n"
        f"🎬 Ochiq kontentlar: *{free_media:,}*\n"
        f"🔒 Premium kontentlar: *{prem_media:,}*\n"
        f"📢 Kanallar: *{channels_count:,}*",
        parse_mode="Markdown"
    )


# ─── XABAR YUBORISH ───────────────────────────────────────────────────────────

@dp.message(F.text == "📣 Xabar yuborish", StateFilter("*"))
async def start_broadcast(message: types.Message, state: FSMContext):
    if not await has_admin_permission(message.from_user.id, "broadcast"):
        return
    await state.set_state(AdminBroadcast.waiting_for_message)
    await message.answer(
        "📣 Barcha foydalanuvchilarga yubormoqchi bo'lgan xabaringizni yuboring.\n"
        "(Matn, rasm yoki video bo'lishi mumkin)\n\n"
        "Bekor qilish uchun /bekor",
        reply_markup=types.ReplyKeyboardRemove()
    )

@dp.message(AdminBroadcast.waiting_for_message)
async def send_broadcast(message: types.Message, state: FSMContext):
    if not await has_admin_permission(message.from_user.id, "broadcast"):
        await state.clear()
        await message.answer("⛔ Sizda xabar yuborish huquqi yo'q.")
        return
    await state.clear()
    db = await get_db()
    async with db.execute("SELECT user_id FROM users") as cur:
        all_users = await cur.fetchall()

    sent, failed = 0, 0
    for row in all_users:
        uid = row["user_id"]
        try:
            await message.copy_to(uid, protect_content=True)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)

    await message.answer(
        f"📣 Xabar yuborildi!\n\n✅ Muvaffaqiyatli: *{sent}*\n❌ Yuborilmadi: *{failed}*",
        reply_markup=admin_menu(message.from_user.id), parse_mode="Markdown"
    )


# ─── KINO/SERIAL/ANIME QO'SHISH ──────────────────────────────────────────────

async def get_next_sequential_code() -> str:
    """Bazadagi ENG KATTA raqamli koddan keyingi sonni qaytaradi.
    Faqat butunlay raqamlardan iborat kodlar hisobga olinadi (qo'lda
    kiritilgan matnli kodlar e'tiborga olinmaydi). Bazada hech qanday
    raqamli kod bo'lmasa, 1 dan boshlanadi."""
    db = await get_db()
    async with db.execute("SELECT DISTINCT code FROM media") as cur:
        rows = await cur.fetchall()
    max_code = 0
    for row in rows:
        code = row["code"]
        if code and str(code).isdigit():
            max_code = max(max_code, int(code))
    return str(max_code + 1)


async def media_code_exists(code: str) -> bool:
    """Kino/serial/anime kodi avval ishlatilgan-yo'qligini tekshiradi."""
    db = await get_db()
    async with db.execute(
        "SELECT 1 FROM media WHERE code=? LIMIT 1", (code,)
    ) as cur:
        return await cur.fetchone() is not None


@dp.message(F.text == "➕ Kino/Serial/Anime qo'shish", StateFilter("*"))
async def add_media_start(message: types.Message, state: FSMContext):
    if not await has_admin_permission(message.from_user.id, "add_media"):
        return
    await state.clear()
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎬 Kino"), KeyboardButton(text="📺 Serial")],
            [KeyboardButton(text="⛩ Anime/Multfilm")],
        ],
        resize_keyboard=True
    )
    await state.set_state(MediaUpload.category)
    await message.answer("Kategoriyani tanlang:", reply_markup=kb)

@dp.message(MediaUpload.category)
async def process_category(message: types.Message, state: FSMContext):
    text = message.text
    if "Kino" in text:
        cat = "kino"
    elif "Serial" in text:
        cat = "serial"
    elif "Anime" in text or "Multfilm" in text:
        cat = "anime"
    else:
        await message.answer("Iltimos, ro'yxatdan birini tanlang.")
        return
    await state.update_data(category=cat)
    await state.set_state(MediaUpload.title)
    await message.answer(
        "Nom kiriting (masalan: Avengers):",
        reply_markup=types.ReplyKeyboardRemove()
    )

@dp.message(MediaUpload.title)
async def process_title(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text.strip())
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎲 Random kod"),
             KeyboardButton(text="✏️ Qo'lda kiritish")],
            [KeyboardButton(text="🔢 Ketma-ket kod")],
        ],
        resize_keyboard=True
    )
    await state.set_state(MediaUpload.code_choice)
    await message.answer("Kodni qanday belgilash kerak?", reply_markup=kb)

@dp.message(MediaUpload.code_choice)
async def process_code_choice(message: types.Message, state: FSMContext):
    if message.text == "🎲 Random kod":
        db = await get_db()
        code = str(random.randint(100, 9999))
        async with db.execute("SELECT id FROM media WHERE code=?", (code,)) as cur:
            while await cur.fetchone():
                code = str(random.randint(100, 9999))
        await state.update_data(code=code)
        kb = ReplyKeyboardMarkup(
            keyboard=[[
                KeyboardButton(text="🌐 Oddiy (Ochiq)"),
                KeyboardButton(text="🌟 Premium")
            ]],
            resize_keyboard=True
        )
        await state.set_state(MediaUpload.is_premium)
        await message.answer(
            f"✅ Generatsiya qilingan kod: *{code}*\n\nKino turini tanlang:",
            reply_markup=kb, parse_mode="Markdown"
        )
    elif message.text == "🔢 Ketma-ket kod":
        code = await get_next_sequential_code()
        await state.update_data(code=code)
        kb = ReplyKeyboardMarkup(
            keyboard=[[
                KeyboardButton(text="🌐 Oddiy (Ochiq)"),
                KeyboardButton(text="🌟 Premium")
            ]],
            resize_keyboard=True
        )
        await state.set_state(MediaUpload.is_premium)
        await message.answer(
            f"✅ Ketma-ket kod: *{code}*\n\nKino turini tanlang:",
            reply_markup=kb, parse_mode="Markdown"
        )
    else:
        await state.set_state(MediaUpload.manual_code)
        await message.answer(
            "Kod kiriting (raqam):", reply_markup=types.ReplyKeyboardRemove()
        )

@dp.message(MediaUpload.manual_code)
async def process_manual_code(message: types.Message, state: FSMContext):
    code = message.text.strip()
    if not code:
        await message.answer("❌ Kod bo'sh bo'lishi mumkin emas. Boshqa kod kiriting:")
        return

    if await media_code_exists(code):
        await message.answer(
            "❌ Bu kod avval kiritilgan.\n"
            "Iltimos, boshqa kod kiriting:"
        )
        return

    await state.update_data(code=code)
    kb = ReplyKeyboardMarkup(
        keyboard=[[
            KeyboardButton(text="🌐 Oddiy (Ochiq)"),
            KeyboardButton(text="🌟 Premium")
        ]],
        resize_keyboard=True
    )
    await state.set_state(MediaUpload.is_premium)
    await message.answer(
        f"Kod: *{md_escape(code)}*\n\nKino turini tanlang:", reply_markup=kb, parse_mode="Markdown"
    )

@dp.message(MediaUpload.is_premium)
async def process_is_premium(message: types.Message, state: FSMContext):
    is_prem = 1 if "Premium" in message.text else 0
    await state.update_data(is_premium=is_prem)
    await state.set_state(MediaUpload.parts_count)
    await message.answer(
        "Necha qismdan iborat? (masalan: 1, 5, 12):",
        reply_markup=types.ReplyKeyboardRemove()
    )

@dp.message(MediaUpload.parts_count)
async def process_parts_count(message: types.Message, state: FSMContext):
    try:
        count = int(message.text.strip())
        if count < 1:
            raise ValueError
        await state.update_data(parts_count=count, current_part=1)
        await state.set_state(MediaUpload.waiting_for_videos)
        await message.answer("1-qism videoni yuboring:")
    except ValueError:
        await message.answer("Iltimos, faqat musbat raqam kiriting!")

@dp.message(MediaUpload.waiting_for_videos, F.video)
async def process_video_upload(message: types.Message, state: FSMContext):
    data = await state.get_data()
    current_part = data["current_part"]
    total_parts = data["parts_count"]
    db = await get_db()

    # Kod tanlangandan keyin boshqa admin shu kod bilan media qo'shgan
    # bo'lishi mumkin. Saqlashdan oldingi tekshiruv dublikatni yakuniy
    # bosqichda ham bloklaydi.
    if await media_code_exists(data["code"]):
        await state.clear()
        await message.answer(
            "❌ Bu kod avval kiritilgan, video saqlanmadi.\n"
            "Yangi kod bilan qaytadan boshlang.",
            reply_markup=admin_menu(message.from_user.id),
        )
        return

    await db.execute(
        "INSERT INTO media (code, title, category, file_id, part, is_premium) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (data["code"], data["title"], data["category"],
         message.video.file_id, current_part, data["is_premium"])
    )
    await db.commit()

    if current_part < total_parts:
        await state.update_data(current_part=current_part + 1)
        await message.answer(
            f"✅ {current_part}-qism saqlandi. {current_part + 1}-qism videoni yuboring:"
        )
    else:
        cat_icon = {"kino": "🎬", "serial": "📺", "anime": "⛩"}.get(
            data["category"], "🎬"
        )
        status_str = "🌟 Premium" if data["is_premium"] else "🌐 Oddiy"
        title_safe = md_escape(data['title'])
        code_safe = md_escape(data['code'])
        await message.answer(
            f"🎉 Barcha *{total_parts}* ta qism saqlandi!\n\n"
            f"{cat_icon} *{title_safe}*\n"
            f"🔑 Kod: `{code_safe}`\n"
            f"📌 Turi: {status_str}",
            reply_markup=admin_menu(message.from_user.id), parse_mode="Markdown"
        )
        await state.clear()


# ─── MEDIA O'CHIRISH ──────────────────────────────────────────────────────────

@dp.message(F.text == "🗑 Kino/Serial/Anime o'chirish", StateFilter("*"))
async def delete_media_start(message: types.Message, state: FSMContext):
    if not await has_admin_permission(message.from_user.id, "delete_media"):
        return
    await state.clear()
    db = await get_db()
    async with db.execute(
        "SELECT DISTINCT code, title, category, is_premium FROM media ORDER BY category, id DESC"
    ) as cur:
        items = await cur.fetchall()
    if not items:
        await message.answer("Hozircha hech qanday kontent yo'q.")
        return
    text = "🗑 *O'chirish uchun kodni yuboring:*\n\n"
    for row in items:
        icon = {"kino": "🎬", "serial": "📺", "anime": "⛩"}.get(row["category"], "🎬")
        prem = " 🌟" if row["is_premium"] else ""
        title_safe = md_escape(row['title'])
        code_safe = md_escape(row['code'])
        text += f"{icon}{prem} {title_safe} — `{code_safe}`\n"
    await message.answer(text, parse_mode="Markdown")
    await state.set_state(AdminDeleteMedia.waiting_for_code)

@dp.message(AdminDeleteMedia.waiting_for_code)
async def delete_media_finish(message: types.Message, state: FSMContext):
    code = message.text.strip()
    db = await get_db()
    async with db.execute(
        "SELECT title FROM media WHERE code=? LIMIT 1", (code,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        await message.answer("❌ Bunday kod topilmadi.")
        await state.clear()
        return
    await db.execute("DELETE FROM media WHERE code=?", (code,))
    await db.commit()
    await message.answer(
        f"✅ *{md_escape(row['title'])}* (kod: `{md_escape(code)}`) o'chirildi.",
        reply_markup=admin_menu(message.from_user.id), parse_mode="Markdown"
    )
    await state.clear()


# ─── KANAL QO'SHISH ──────────────────────────────────────────────────────────

async def save_telegram_channel(
    message: types.Message, state: FSMContext,
    save_ch_id: str, title: str, link: str, type_label: str
):
    """Kanal/guruhni bazaga saqlaydi (yoki eskisini yangilaydi) va adminга
    yakuniy xabar yuboradi. Agar bu channel_id bazada allaqachon bo'lsa —
    endi YANGI ma'lumot (jumladan yangi havola) bilan ustidan yoziladi,
    eski (eskirgan) qator saqlanib qolmaydi."""
    db = await get_db()
    await db.execute(
        "INSERT INTO channels (channel_id, title, link, type) "
        "VALUES (?, ?, ?, 'telegram') "
        "ON CONFLICT(channel_id) DO UPDATE SET "
        "title=excluded.title, link=excluded.link, type=excluded.type",
        (save_ch_id, title, link)
    )
    await db.commit()
    await state.clear()
    title_safe = md_escape(title)
    await message.answer(
        f"✅ *Kanal/guruh qo'shildi!*\n\n"
        f"🏷 Nomi: *{title_safe}*\n"
        f"📌 Turi: {type_label}\n"
        f"🆔 ID: `{save_ch_id}`\n"
        f"🔗 Havola: `{link}`\n\n"
        f"ℹ️ Yopiq kanal bo'lsa: bot qo'shilish so'rovlarini *avtomatik "
        f"tasdiqlamaydi* — admin qo'lda tasdiqlaydi. Lekin foydalanuvchi "
        f"so'rov yuborgani bilanoq botdan foydalanish uchun yetarli deb "
        f"hisoblanadi (\"✅ Tekshirish\" tugmasi ishlaydi).",
        parse_mode="Markdown",
        reply_markup=admin_menu(message.from_user.id)
    )


async def ask_backup_link(
    message: types.Message, state: FSMContext,
    chat_id_int: int, can_invite: bool, title: str, type_label: str
):
    """
    Yopiq kanal uchun adminDAN taklif havolasini so'raydi.

    MUHIM: /avto orqali bot yaratgan havola endi har doim
    `creates_join_request=True` bilan yaratiladi — ya'ni shu havoladan
    kirgan foydalanuvchi to'g'ridan-to'g'ri A'ZO BO'LMAYDI, balki
    "qo'shilish so'rovi" yuboradi va admin buni qo'lda tasdiqlashi
    kerak bo'ladi (aynan shuni admin so'ragan edi).

    Agar admin havolani qo'lda (Telegramdan nusxalab) yuborsa — bu holda
    o'sha havola qanday sozlab yaratilgan bo'lsa, xuddi shunday ishlaydi:
    agar u yaratilganda "So'rov orqali qo'shish" yoqilmagan bo'lsa,
    foydalanuvchi to'g'ridan-to'g'ri a'zo bo'lib ketadi. Shu sababli
    quyida buni ochiq ogohlantiramiz va /avto ni tavsiya qilamiz.
    """
    await state.update_data(
        pending_chat_id=chat_id_int,
        pending_title=title,
        pending_type_label=type_label,
        pending_can_invite=can_invite,
    )
    await state.set_state(AdminChannel.waiting_for_backup_link)

    if can_invite:
        hint = (
            "✅ *Tavsiya:* /avto deb yozing — bot o'zi \"qo'shilish so'rovi\" "
            "talab qiladigan havola yaratadi (hech kim to'g'ridan-to'g'ri "
            "a'zo bo'lolmaydi)."
        )
    else:
        hint = (
            "⚠️ *Diqqat:* botda hozircha \"Foydalanuvchilarni havola orqali "
            "qo'shish\" huquqi yo'q — shu sabab bot /avto havola yarata "
            "OLMAYDI. Havolani albatta qo'lda yuboring."
        )

    await message.answer(
        f"🔗 *{md_escape(title)}* — yopiq kanal uchun taklif havolasini yuboring.\n\n"
        f"{hint}\n\n"
        "❗️ Agar havolani o'zingiz qo'lda yubormoqchi bo'lsangiz: Telegram'da "
        "kanal → Havolalar (Invite Links) → \"Yangi havola yaratish\" → "
        "*\"So'rov orqali qo'shish\"* (Request admin's approval) tugmasini "
        "SHART yoqing — aks holda foydalanuvchi to'g'ridan-to'g'ri a'zo "
        "bo'lib ketadi va \"qo'shilish so'rovi\" umuman ishlamaydi.\n\n"
        "Bekor qilish: /bekor",
        parse_mode="Markdown"
    )


@dp.message(F.text == "📢 Kanal qo'shish", StateFilter("*"))
async def add_channel_start(message: types.Message, state: FSMContext):
    if not await has_admin_permission(message.from_user.id, "add_channel"):
        return
    await state.clear()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📢 Telegram kanal/guruh",
            callback_data="chtype_telegram"
        )],
        [InlineKeyboardButton(
            text="📸 Instagram / boshqa tashqi havola",
            callback_data="chtype_instagram"
        )],
    ])
    await state.set_state(AdminChannel.waiting_for_type)
    await message.answer(
        "Qanday turdagi kanal/havola qo'shmoqchisiz?",
        reply_markup=kb
    )

@dp.callback_query(F.data == "chtype_telegram", AdminChannel.waiting_for_type)
async def add_channel_type_telegram(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(ch_type="telegram")
    await state.set_state(AdminChannel.waiting_for_id)
    await call.message.answer(
        "📢 *Telegram kanal yoki guruh qo'shish*\n\n"
        "Quyidagilardan birini yuboring:\n"
        "• Kanal username: @mening\\_kanalim\n"
        "• Kanal ID raqami: -1001234567890\n"
        "• t.me havolasi: https://t.me/mening\\_kanalim\n"
        "• Yopiq kanal taklifi: https://t.me/+AbCdEfGh1234\n\n"
        "ℹ️ _Bot kanalga admin qilib qo'shilgan bo'lishi shart!_\n\n"
        "⚠️ *Yopiq (maxfiy) kanal bo'lsa:* botga \"Foydalanuvchilarni havola "
        "orqali qo'shish\" (Invite Users via Link) huquqini bering — aks "
        "holda bot foydalanuvchilarning \"qo'shilish so'rovi\"larini qabul "
        "qila olmaydi. Keyingi qadamda bot sizdan kanal havolasini ham "
        "so'raydi.\n\n"
        "Bekor qilish: /bekor",
        parse_mode="Markdown",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await call.answer()

@dp.message(AdminChannel.waiting_for_id)
async def add_channel_get_id(message: types.Message, state: FSMContext):
    raw = message.text.strip()

    chat_id_for_api, full_link, is_invite = parse_channel_input(raw)

    # ── Yopiq kanal invite havolasi ──
    if is_invite:
        # Faqat havola matnini saqlash YETARLI EMAS: statik taklif havolasi
        # vaqt o'tishi, foydalanish limiti yoki qayta generatsiya qilinishi
        # sababli "eskirgan havola" bo'lib qoladi va bot a'zolikni Telegram
        # API orqali hech qachon tekshira olmaydi. Shuning uchun kanalning
        # HAQIQIY (raqamli) ID'ini aniqlashimiz kerak — shunda bot: (1) har
        # safar yangi, eskirmaydigan taklif havolasi yaratadi, (2) a'zolikni
        # real vaqtda tekshiradi.
        await state.update_data(invite_link=full_link)
        await state.set_state(AdminChannel.waiting_for_invite_resolve)
        await message.answer(
            "🔒 Bu — yopiq kanalning *taklif havolasi*.\n\n"
            "Yopiq kanallar uchun bot kanalning *haqiqiy ID raqamini* bilishi shart "
            "— aks holda havola vaqt o'tib \"eskirgan\" bo'lib qoladi va "
            "a'zolikni tekshira olmaydi.\n\n"
            "Iltimos quyidagilardan *birini* bajaring:\n"
            "1️⃣ Botni shu kanalga *administrator* qilib qo'shing, so'ng o'sha "
            "kanaldan istalgan xabarni shu yerga *forward* (uzatib) yuboring — "
            "ID avtomatik aniqlanadi.\n"
            "2️⃣ Yoki kanal ID raqamini qo'lda yuboring (masalan: `-1001234567890`).\n\n"
            "Bekor qilish: /bekor",
            parse_mode="Markdown"
        )
        return

    # ── Telegram kanal/guruh: API orqali tekshirish ──
    chat = await safe_get_chat(chat_id_for_api)

    if not chat:
        if isinstance(chat_id_for_api, str) and chat_id_for_api.startswith("@"):
            alt = chat_id_for_api.lstrip("@")
            chat = await safe_get_chat(alt)

    if not chat:
        await message.answer(
            "❌ Bot bu kanal/guruhni topa olmadi.\n\n"
            "*Tekshiring:*\n"
            "1. Username yoki havola to'g'ri yozilganmi?\n"
            "2. Bot kanalga admin qilib qo'shilganmi?\n"
            "3. Maxsus belgilar bo'lsa, ID raqamini (-100...) ishlating\n\n"
            "Qaytadan yuboring yoki /bekor deb yozing.",
            parse_mode="Markdown"
        )
        return

    ch_type_detected = getattr(chat, "type", None)

    if ch_type_detected in ("channel", "group", "supergroup"):
        title = chat.title or str(chat.id)
        uname = f"@{chat.username}" if getattr(chat, "username", None) else None

        type_label = {
            "channel": "📢 Kanal",
            "group": "👥 Guruh",
            "supergroup": "👥 Guruh (supergroup)"
        }.get(ch_type_detected, "Chat")

        uname_safe = md_escape(uname) if uname else "_(username yo'q)_"
        title_safe = md_escape(title)

        # Botning admin holati va "havola orqali qo'shish" huquqini tekshiramiz
        is_admin_here = False
        can_invite = False
        try:
            me_member = await asyncio.wait_for(
                bot.get_chat_member(chat.id, (await bot.get_me()).id),
                timeout=API_TIMEOUT
            )
            is_admin_here = me_member.status in ("administrator", "creator")
            if me_member.status == "creator":
                can_invite = True
            else:
                can_invite = bool(getattr(me_member, "can_invite_users", False))
        except Exception as e:
            logging.warning(f"Bot admin holatini tekshirib bo'lmadi ({chat.id}): {e}")

        if not is_admin_here:
            await state.clear()
            await message.answer(
                f"✅ *Aniqlandi!*\n\n"
                f"🏷 Nomi: *{title_safe}*\n"
                f"📌 Turi: {type_label}\n"
                f"👤 Username: {uname_safe}\n\n"
                "⚠️ *DIQQAT:* Bot bu kanalda hali *administrator* emas!\n"
                "Botni admin qilib qo'shing, so'ng \"📢 Kanal qo'shish\" dan "
                "qaytadan urinib ko'ring.",
                parse_mode="Markdown",
                reply_markup=admin_menu(message.from_user.id)
            )
            return

        if uname:
            # Ochiq kanal: o'zgarmas, hech qachon eskirmaydigan t.me/username havolasi
            link = f"https://t.me/{uname.lstrip('@')}"
            save_ch_id = uname
            await save_telegram_channel(message, state, save_ch_id, title, link, type_label)
            return

        # Yopiq kanal (username yo'q): ID to'g'ridan-to'g'ri yuborilgan bo'lsa
        # ham, endi bot HAVOLANI ADMINDAN HAM SO'RAYDI — faqat o'zining
        # avtomatik yaratgan havolasiga ishonib qolmaydi. Shu tufayl
        # "havola eskirgan" muammosi oldini oladi: agar botning huquqi
        # yetarli bo'lmasa yoki avtomatik yaratish muvaffaqiyatsiz bo'lsa,
        # adminning o'zi bergan joriy (eskirmagan) havola ishlatiladi.
        await ask_backup_link(message, state, chat.id, can_invite, title, type_label)
        return

    else:
        # Bot yoki oddiy foydalanuvchi
        uname = getattr(chat, "username", None)
        if not uname and isinstance(chat_id_for_api, str):
            uname = chat_id_for_api.lstrip("@")

        is_real_bot = bool(uname) and uname.lower().endswith("bot")

        if not is_real_bot:
            await message.answer(
                "❌ Bu — bot emas, oddiy foydalanuvchi profili ko'rinadi.\n\n"
                "Majburiy obuna faqat *kanal*, *guruh* yoki *bot* uchun qo'shiladi.\n"
                "Agar bu chindan ham bot bo'lsa, uning username doim "
                "`...bot` bilan tugashi kerak.\n\n"
                "Qaytadan yuboring yoki /bekor deb yozing.",
                parse_mode="Markdown"
            )
            return

        title = (
            getattr(chat, "full_name", None)
            or getattr(chat, "first_name", None)
            or uname
            or str(chat.id)
        )
        link = f"https://t.me/{uname}" if uname else ""
        ch_id = f"bot_{uname or int(datetime.now().timestamp())}"

        db = await get_db()
        await db.execute(
            "INSERT INTO channels (channel_id, title, link, type) "
            "VALUES (?, ?, ?, 'bot') "
            "ON CONFLICT(channel_id) DO UPDATE SET "
            "title=excluded.title, link=excluded.link, type=excluded.type",
            (ch_id, title, link)
        )
        await db.commit()
        await state.clear()
        uname_safe = md_escape(uname) if uname else "yo'q"
        title_safe = md_escape(title)
        no_link = "_(yo'q)_"
        link_display = f"`{link}`" if link else no_link
        await message.answer(
            f"✅ *Telegram bot qo'shildi!*\n\n"
            f"🤖 Nomi: *{title_safe}*\n"
            f"👤 Username: @{uname_safe}\n"
            f"🔗 Havola: {link_display}\n\n"
            f"ℹ️ Foydalanuvchilar botga o'tib, keyin "
            f"\"✅ Tekshirish\" tugmasini bosib tasdiqlaydi.",
            parse_mode="Markdown",
            reply_markup=admin_menu(message.from_user.id)
        )

@dp.message(AdminChannel.waiting_for_backup_link)
async def add_channel_backup_link(message: types.Message, state: FSMContext):
    data = await state.get_data()
    chat_id_int = data.get("pending_chat_id")
    title = data.get("pending_title") or ""
    type_label = data.get("pending_type_label") or "📢 Kanal"
    can_invite = bool(data.get("pending_can_invite"))

    if chat_id_int is None:
        await state.clear()
        await message.answer(
            "❌ Xatolik yuz berdi, qaytadan boshlang: \"📢 Kanal qo'shish\".",
            reply_markup=admin_menu(message.from_user.id)
        )
        return

    raw = (message.text or "").strip()
    use_auto = raw.lower() in ("/avto", "avto", "/auto")

    link = None
    if not use_auto:
        decoded = unquote(raw)
        is_tme = any(
            decoded.lower().startswith(p)
            for p in ("https://t.me/", "http://t.me/", "t.me/")
        )
        if not is_tme:
            extra = ", yoki /avto deb yozing." if can_invite else "."
            await message.answer(
                "❌ Bu to'g'ri taklif havolasiga o'xshamaydi.\n\n"
                f"`https://t.me/+...` ko'rinishidagi havola yuboring{extra}\n\n"
                "Bekor qilish: /bekor",
                parse_mode="Markdown"
            )
            return
        path = clean_tme_path(decoded)
        link = "https://t.me/" + path

    if link is None:
        if not can_invite:
            await message.answer(
                "⛔ Botda taklif havolasi yaratish huquqi yo'q va siz ham "
                "havola yubormadingiz.\n\nIltimos havolani qo'lda yuboring "
                "yoki /bekor deb yozing.",
                parse_mode="Markdown"
            )
            return
        try:
            invite = await asyncio.wait_for(
                bot.create_chat_invite_link(
                    chat_id_int,
                    creates_join_request=True
                ),
                timeout=API_TIMEOUT
            )
            link = invite.invite_link
        except Exception as e:
            logging.error(f"Invite link yaratib bo'lmadi ({chat_id_int}): {e}")
            await message.answer(
                f"❌ Avtomatik havola yaratib bo'lmadi: `{e}`\n\n"
                "Iltimos havolani qo'lda yuboring yoki /bekor deb yozing.",
                parse_mode="Markdown"
            )
            return

    await save_telegram_channel(
        message, state, str(chat_id_int), title, link, type_label
    )


@dp.message(AdminChannel.waiting_for_invite_resolve)
async def add_channel_invite_resolve(message: types.Message, state: FSMContext):
    data = await state.get_data()
    invite_link = data.get("invite_link", "")

    chat_id_int = None

    # 1) Kanaldan forward qilingan xabar bo'lsa — undan chat ID olamiz
    fwd_chat = getattr(message, "forward_from_chat", None)
    if fwd_chat is not None:
        chat_id_int = fwd_chat.id
    else:
        # 2) Yoki admin to'g'ridan-to'g'ri raqamli ID yuborgan bo'lishi mumkin
        raw = (message.text or "").strip()
        if re.match(r'^-?\d+$', raw):
            chat_id_int = int(raw)

    if chat_id_int is None:
        await message.answer(
            "❌ Kanal ID'ini aniqlab bo'lmadi.\n\n"
            "• Kanaldan xabar *forward* qiling (bot o'sha kanalda admin bo'lishi shart), "
            "yoki\n"
            "• Kanal ID raqamini yuboring (masalan: `-1001234567890`).\n\n"
            "Bekor qilish: /bekor",
            parse_mode="Markdown"
        )
        return

    # ID topilgach — bot haqiqatan ham shu kanalni ko'ra oladimi, tekshiramiz
    chat = await safe_get_chat(chat_id_int)
    if not chat:
        await message.answer(
            "❌ Bot bu kanalni topa olmadi.\n\n"
            "Bot kanalga *administrator* qilib qo'shilganini tekshirib, "
            "qaytadan urinib ko'ring yoki /bekor deb yozing.",
            parse_mode="Markdown"
        )
        return

    # Bot shu kanalda admin ekanini VA aniq "havola orqali qo'shish" huquqiga
    # ega ekanini tekshiramiz — aks holda create_chat_invite_link ishlamaydi
    is_admin_here = False
    can_invite = False
    try:
        me_member = await asyncio.wait_for(
            bot.get_chat_member(chat.id, (await bot.get_me()).id),
            timeout=API_TIMEOUT
        )
        is_admin_here = me_member.status in ("administrator", "creator")
        # Creator uchun bu huquq har doim bor; administrator uchun aniq
        # can_invite_users maydoni tekshiriladi
        if me_member.status == "creator":
            can_invite = True
        else:
            can_invite = bool(getattr(me_member, "can_invite_users", False))
    except Exception as e:
        logging.warning(f"Bot admin holatini tekshirib bo'lmadi ({chat.id}): {e}")

    if not is_admin_here:
        await message.answer(
            "⚠️ Bot bu kanalda hali *administrator* emas.\n\n"
            "Botni kanalga admin qilib qo'shing (\"Taklif havolalari orqali qo'shish\" "
            "huquqi bilan), so'ng shu xabarni qaytadan yuboring yoki forward qiling.",
            parse_mode="Markdown"
        )
        return

    title = chat.title or invite_link or str(chat.id)
    uname = f"@{chat.username}" if getattr(chat, "username", None) else None

    if uname:
        # Ochiq kanal (username bor): doim yangilanadigan, eskirmaydigan
        # t.me/username havolasi ishlatiladi — havola so'rashga hojat yo'q.
        save_ch_id = uname
        link_to_store = f"https://t.me/{uname.lstrip('@')}"
        await save_telegram_channel(
            message, state, save_ch_id, title, link_to_store, "📢 Yopiq kanal"
        )
        return

    # Yopiq kanal (username yo'q): endi HAVOLANI ADMINDAN HAM SO'RAYMIZ —
    # bot o'zining avtomatik yaratgan havolasiga yolg'iz ishonib qolmaydi.
    # Shu tufayl "havola eskirgan" muammosi bartaraf etiladi.
    await ask_backup_link(message, state, chat.id, can_invite, title, "📢 Yopiq kanal")

@dp.callback_query(F.data == "chtype_instagram", AdminChannel.waiting_for_type)
async def add_channel_type_instagram(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(ch_type="instagram")
    await state.set_state(AdminChannel.waiting_for_manual_title)
    await call.message.answer(
        "📸 *Instagram yoki boshqa tashqi havola qo'shish*\n\n"
        "1. Bu havola uchun nom kiriting:\n"
        "Masalan: Instagram sahifamiz yoki TikTok kanalimiz",
        parse_mode="Markdown"
    )
    await call.answer()

@dp.message(AdminChannel.waiting_for_manual_title)
async def add_channel_manual_title(message: types.Message, state: FSMContext):
    await state.update_data(manual_title=message.text.strip())
    await state.set_state(AdminChannel.waiting_for_manual_link)
    await message.answer(
        "2. Havolani (link) yuboring:\n"
        "Masalan: https://instagram.com/mening\\_sahifam\n\n"
        "ℹ️ *Eslatma:* Instagram havolalarga a'zolikni Telegram orqali tekshirib bo'lmaydi.\n"
        "Foydalanuvchi havolani ochib, keyin \"✅ Tekshirish\" tugmasini bosadi.",
        parse_mode="Markdown"
    )

@dp.message(AdminChannel.waiting_for_manual_link)
async def add_channel_manual_finish(message: types.Message, state: FSMContext):
    data = await state.get_data()
    title = data.get("manual_title", "Havola")
    link = message.text.strip()
    ch_type = data.get("ch_type", "instagram")

    if not (link.startswith("http://") or link.startswith("https://")):
        await message.answer(
            "❌ Iltimos to'liq havola yuboring:\nMasalan: https://instagram.com/...",
            parse_mode="Markdown"
        )
        return

    ch_id = f"{ch_type}_{int(datetime.now().timestamp())}"
    db = await get_db()
    await db.execute(
        "INSERT INTO channels (channel_id, title, link, type) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(channel_id) DO UPDATE SET "
        "title=excluded.title, link=excluded.link, type=excluded.type",
        (ch_id, title, link, ch_type)
    )
    await db.commit()
    icon = "📸" if ch_type == "instagram" else "🔗"
    await state.clear()
    title_safe = md_escape(title)
    await message.answer(
        f"✅ *Havola qo'shildi!*\n\n"
        f"{icon} Nomi: *{title_safe}*\n"
        f"🔗 `{link}`\n\n"
        f"ℹ️ Foydalanuvchilar havolani ochib, \"✅ Tekshirish\" tugmasini bosib tasdiqlaydi.",
        parse_mode="Markdown",
        reply_markup=admin_menu(message.from_user.id)
    )


# ─── KANAL O'CHIRISH ──────────────────────────────────────────────────────────

@dp.message(F.text == "🗑 Kanal o'chirish", StateFilter("*"))
async def list_del_channels(message: types.Message, state: FSMContext):
    if not await has_admin_permission(message.from_user.id, "delete_channel"):
        return
    await state.clear()
    db = await get_db()
    async with db.execute(
        "SELECT id, title, channel_id, type FROM channels"
    ) as cur:
        channels = await cur.fetchall()
    if not channels:
        await message.answer("Kanallar mavjud emas.")
        return

    type_icons = {"telegram": "📢", "bot": "🤖", "instagram": "📸", "manual": "🔗"}
    buttons = [
        [InlineKeyboardButton(
            text=f"❌ {type_icons.get(row['type'], '🔗')} {row['title']}",
            callback_data=f"del_ch_{row['id']}"
        )]
        for row in channels
    ]
    await message.answer(
        "O'chirmoqchi bo'lgan kanalni tanlang:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )

@dp.callback_query(F.data.startswith("del_ch_"))
async def delete_channel_cb(call: types.CallbackQuery):
    if not await has_admin_permission(call.from_user.id, "delete_channel"):
        await call.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return
    ch_db_id = int(call.data.split("_")[2])
    db = await get_db()
    async with db.execute(
        "SELECT title FROM channels WHERE id=?", (ch_db_id,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        await call.answer("Kanal topilmadi.", show_alert=True)
        return
    await db.execute("DELETE FROM channels WHERE id=?", (ch_db_id,))
    await db.commit()
    await call.message.answer(
        f"✅ *{md_escape(row['title'])}* kanali o'chirildi.",
        parse_mode="Markdown",
        reply_markup=admin_menu(call.from_user.id)
    )
    await call.answer()

@dp.message(F.text == "📋 Kanallar ro'yxati", StateFilter("*"))
async def list_channels(message: types.Message, state: FSMContext):
    if not await has_admin_permission(message.from_user.id, "list_channels"):
        return
    await state.clear()
    db = await get_db()
    async with db.execute(
        "SELECT channel_id, title, link, type FROM channels"
    ) as cur:
        channels = await cur.fetchall()
    if not channels:
        await message.answer("Hozircha kanallar qo'shilmagan.")
        return

    type_icons = {"telegram": "📢", "bot": "🤖", "instagram": "📸", "manual": "🔗"}
    text = "📋 *Kanallar ro'yxati:*\n\n"
    refresh_buttons = []
    for ch in channels:
        icon = type_icons.get(ch["type"], "🔗")
        link_str = f"`{ch['link']}`" if ch["link"] else "_(havola yo'q)_"
        ch_id_str = md_escape(str(ch["channel_id"]))
        title_str = md_escape(ch["title"])
        text += (
            f"{icon} *{title_str}*\n"
            f"  🆔 `{ch_id_str}`\n"
            f"  🔗 {link_str}\n\n"
        )
        # Faqat yopiq (raqamli ID) Telegram kanallar uchun havolani
        # yangilash imkoniyati beramiz — @username kanallarga kerak emas
        if ch["type"] == "telegram" and not str(ch["channel_id"]).startswith("@"):
            short_title = ch["title"][:28]
            refresh_buttons.append([InlineKeyboardButton(
                text=f"🔄 {short_title}",
                callback_data=f"refresh_link_{ch['channel_id']}"
            )])

    kb = None
    if refresh_buttons:
        text += "ℹ️ _Yopiq kanal havolasi ishlamay qolsa, quyidan yangilang:_"
        kb = InlineKeyboardMarkup(inline_keyboard=refresh_buttons)
    await message.answer(text, parse_mode="Markdown", reply_markup=kb)

    diag_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🩺 Kanallarni diagnostika qilish", callback_data="diag_channels")
    ]])
    await message.answer(
        "Ochiq kanalga obuna kelmayotgan bo'lsa yoki tekshiruv noto'g'ri "
        "ishlayotganidan shubhalansangiz — pastdagi tugma orqali botning "
        "har bir kanaldagi HAQIQIY holatini tekshirib ko'ring:",
        reply_markup=diag_kb
    )

@dp.callback_query(F.data == "diag_channels")
async def diagnose_channels_cb(call: types.CallbackQuery):
    if not await has_admin_permission(call.from_user.id, "list_channels"):
        await call.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return
    await call.answer("⏳ Tekshirilmoqda...")

    db = await get_db()
    async with db.execute(
        "SELECT channel_id, title, link, type FROM channels"
    ) as cur:
        channels = await cur.fetchall()

    if not channels:
        await call.message.answer("Hozircha kanallar qo'shilmagan.")
        return

    me = await bot.get_me()
    lines = ["🩺 *Kanallar diagnostikasi:*\n"]

    for ch in channels:
        ch_id = ch["channel_id"]
        title = md_escape(ch["title"])
        ch_type = ch["type"]

        if ch_type != "telegram":
            icon = "🤖" if ch_type == "bot" else ("📸" if ch_type == "instagram" else "🔗")
            lines.append(
                f"{icon} *{title}*\n"
                f"  ℹ️ Turi: `{ch_type}` — API orqali tekshirilmaydi, "
                f"foydalanuvchi \"Tekshirish\" bosganda avtomatik tasdiqlanadi.\n"
            )
            continue

        chat_id = int(ch_id) if str(ch_id).lstrip("-").isdigit() else ch_id

        # 1) Bot bu chatni ko'ra oladimi (username/ID to'g'rimi)
        chat_obj = await safe_get_chat(chat_id)
        if not chat_obj:
            lines.append(
                f"📢 *{title}*  (`{md_escape(str(ch_id))}`)\n"
                f"  ❌ *XATO:* Bot bu kanalni topa olmayapti! Ehtimol "
                f"kanal username'i o'zgargan, kanal o'chirilgan yoki ID "
                f"noto'g'ri saqlangan. Kanalni o'chirib, qaytadan to'g'ri "
                f"qo'shing.\n"
            )
            continue

        real_title = md_escape(chat_obj.title or "?")
        real_username = f"@{chat_obj.username}" if chat_obj.username else "_(username yo'q)_"

        # 2) Botning o'zi bu kanalda ADMIN ekanligini tekshirish
        bot_member = await safe_get_chat_member(chat_id, me.id)
        if not bot_member:
            lines.append(
                f"📢 *{title}* → topildi: {real_title} ({real_username})\n"
                f"  ❌ *XATO:* Bot bu kanaldagi holatini bilib bo'lmayapti "
                f"(API xatosi). Bot kanalga umuman qo'shilmagan bo'lishi mumkin.\n"
            )
        elif bot_member.status not in ("administrator", "creator"):
            lines.append(
                f"📢 *{title}* → topildi: {real_title} ({real_username})\n"
                f"  ❌ *XATO:* Bot bu kanalda ADMIN EMAS (holati: "
                f"`{bot_member.status}`)! Shu sabab a'zolarni tekshira "
                f"olmaydi va HAMMA foydalanuvchi \"obuna emas\" deb "
                f"ko'rsatiladi. Botni kanalga admin qilib tayinlang.\n"
            )
        else:
            saved_username = f"@{str(ch_id).lstrip('@')}" if str(ch_id).startswith("@") else None
            mismatch_warn = ""
            if saved_username and chat_obj.username and \
               saved_username.lstrip("@").lower() != chat_obj.username.lower():
                mismatch_warn = (
                    f"  ⚠️ *DIQQAT:* Bazada saqlangan username (`{saved_username}`) "
                    f"kanalning HOZIRGI username'idan (`@{chat_obj.username}`) FARQ "
                    f"QILADI! Kanal o'chirib, qaytadan to'g'ri username bilan qo'shing.\n"
                )
            lines.append(
                f"📢 *{title}* → topildi: {real_title} ({real_username})\n"
                f"  ✅ Bot admin, tekshiruv to'g'ri ishlashi kerak.\n"
                f"{mismatch_warn}"
            )

    full_text = "\n".join(lines)
    # Xabar juda uzun bo'lib ketmasligi uchun bo'lib yuboramiz
    for i in range(0, len(full_text), 3500):
        await call.message.answer(full_text[i:i + 3500], parse_mode="Markdown")

@dp.callback_query(F.data.startswith("refresh_link_"))
async def refresh_channel_link_cb(call: types.CallbackQuery):
    if not await has_admin_permission(call.from_user.id, "list_channels"):
        await call.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return
    ch_id = call.data[len("refresh_link_"):]
    try:
        chat_id_int = int(ch_id)
    except ValueError:
        await call.answer("❌ Noto'g'ri kanal ID.", show_alert=True)
        return

    try:
        invite = await asyncio.wait_for(
            bot.create_chat_invite_link(
                chat_id_int,
                creates_join_request=True
            ),
            timeout=API_TIMEOUT
        )
        new_link = invite.invite_link
    except Exception as e:
        logging.error(f"Havolani yangilashda xato ({ch_id}): {e}")
        await call.answer(
            "❌ Yangi havola yaratib bo'lmadi. Botga kanalda \"Foydalanuvchilarni "
            "havola orqali qo'shish\" huquqi berilganini tekshiring.",
            show_alert=True
        )
        return

    db = await get_db()
    await db.execute("UPDATE channels SET link=? WHERE channel_id=?", (new_link, ch_id))
    await db.commit()
    await call.answer("✅ Yangi havola yaratildi!", show_alert=True)
    await call.message.answer(f"🔗 Yangi havola:\n{new_link}")


# ─── NARX O'ZGARTIRISH ───────────────────────────────────────────────────────

@dp.message(F.text == "💰 Premium narxini o'zgartirish", StateFilter("*"))
async def start_price_change(message: types.Message, state: FSMContext):
    if not await has_admin_permission(message.from_user.id, "change_premium_price"):
        return
    await state.clear()
    price = await get_premium_price(30)
    await state.set_state(AdminPriceChange.waiting_for_price)
    await message.answer(
        f"💰 Hozirgi narx: *{price}* so'm\n\nYangi narxni kiriting (masalan: 25000):",
        parse_mode="Markdown",
        reply_markup=types.ReplyKeyboardRemove()
    )

@dp.message(AdminPriceChange.waiting_for_price)
async def finish_price_change(message: types.Message, state: FSMContext):
    if not await has_admin_permission(message.from_user.id, "change_premium_price"):
        await state.clear()
        await message.answer("⛔ Sizda Premium narxini o'zgartirish huquqi yo'q.")
        return
    new_price = message.text.strip().replace(" ", "")
    if not new_price.isdigit():
        await message.answer("❌ Iltimos faqat raqam kiriting (masalan: 25000).")
        return
    formatted = f"{int(new_price):,}"
    await set_premium_price(formatted, 30)
    await state.clear()
    await message.answer(
        f"✅ Premium narxi *{formatted} so'm* qilib o'zgartirildi.",
        reply_markup=admin_menu(message.from_user.id), parse_mode="Markdown"
    )


# ─── REFERAL MUKOFOTI NARXINI O'ZGARTIRISH ────────────────────────────────────

@dp.message(F.text == "🎁 Referal narxini o'zgartirish", StateFilter("*"))
async def start_referral_price_change(message: types.Message, state: FSMContext):
    if not await has_admin_permission(message.from_user.id, "change_referral_price"):
        return
    await state.clear()
    reward = await get_referral_reward()
    await state.set_state(AdminReferralPrice.waiting_for_amount)
    await message.answer(
        f"🎁 Hozirgi referal mukofoti: *{reward:,} so'm*\n\n"
        f"Har bir taklif qilingan odam uchun yangi mukofot miqdorini kiriting "
        f"(masalan: 1000):",
        parse_mode="Markdown",
        reply_markup=types.ReplyKeyboardRemove()
    )

@dp.message(AdminReferralPrice.waiting_for_amount)
async def finish_referral_price_change(message: types.Message, state: FSMContext):
    if not await has_admin_permission(message.from_user.id, "change_referral_price"):
        await state.clear()
        await message.answer("⛔ Sizda referal narxini o'zgartirish huquqi yo'q.")
        return
    new_amount = message.text.strip().replace(" ", "")
    if not new_amount.isdigit():
        await message.answer("❌ Iltimos faqat raqam kiriting (masalan: 1000).")
        return
    amount = int(new_amount)
    await set_referral_reward(amount)
    await state.clear()
    await message.answer(
        f"✅ Referal mukofoti *{amount:,} so'm* qilib o'zgartirildi.\n\n"
        f"ℹ️ Bu narx bundan buyon yangi qo'shiladigan taklif qilingan "
        f"odamlarga qo'llanadi.",
        reply_markup=admin_menu(message.from_user.id), parse_mode="Markdown"
    )


# ─── BOT XABARLARINI TAHRIRLASH ────────────────────────────────────────────────
# Bu bo'lim orqali admin botning foydalanuvchilarga yuboradigan asosiy
# matnlarini (xush kelibsiz, majburiy obuna, kod qidiruv, Premium tavsifi,
# to'lov yo'riqnomasi, referal xabari) KOD YOZMASDAN, to'g'ridan-to'g'ri
# shu yerdan o'zgartirishi mumkin.

def build_message_edit_kb() -> InlineKeyboardMarkup:
    rows = []
    for key, (label, _default) in EDITABLE_MESSAGES.items():
        rows.append([InlineKeyboardButton(text=label, callback_data=f"editmsg_{key}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

@dp.message(F.text == "✏️ Bot xabarlarini tahrirlash", StateFilter("*"))
async def edit_messages_menu(message: types.Message, state: FSMContext):
    if not await has_admin_permission(message.from_user.id, "edit_messages"):
        return
    await state.clear()
    await message.answer(
        "✏️ *Bot xabarlarini tahrirlash*\n\n"
        "Qaysi xabarni o'zgartirmoqchisiz? Tanlang:",
        parse_mode="Markdown",
        reply_markup=build_message_edit_kb()
    )

@dp.callback_query(F.data.startswith("editmsg_"))
async def edit_message_select_cb(call: types.CallbackQuery, state: FSMContext):
    if not await has_admin_permission(call.from_user.id, "edit_messages"):
        await call.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return
    key = call.data[len("editmsg_"):]
    if key not in EDITABLE_MESSAGES:
        await call.answer("❌ Topilmadi.", show_alert=True)
        return

    label, _default = EDITABLE_MESSAGES[key]
    current_text = await get_message_template(key)

    await state.set_state(AdminMessageEdit.waiting_for_text)
    await state.update_data(editmsg_key=key)

    await call.message.answer(
        f"✏️ *{label}*\n\n"
        f"📄 *Hozirgi matn:*\n{current_text}\n\n"
        f"👇 Yangi matnni yuboring (Markdown formatlash qo'llab-quvvatlanadi: "
        f"*qalin*, _kursiv_, `kod`).\n\n"
        f"Bekor qilish: /bekor",
        parse_mode="Markdown"
    )
    await call.answer()

@dp.message(AdminMessageEdit.waiting_for_text)
async def edit_message_save(message: types.Message, state: FSMContext):
    if not await has_admin_permission(message.from_user.id, "edit_messages"):
        await state.clear()
        await message.answer("⛔ Sizda bot xabarlarini tahrirlash huquqi yo'q.")
        return
    data = await state.get_data()
    key = data.get("editmsg_key")
    if not key or key not in EDITABLE_MESSAGES:
        await state.clear()
        await message.answer("❌ Xatolik yuz berdi, qaytadan urinib ko'ring.")
        return

    new_text = message.text
    if not new_text:
        await message.answer("❌ Iltimos matn yuboring.")
        return

    await set_message_template(key, new_text)
    label, _default = EDITABLE_MESSAGES[key]
    await state.clear()
    await message.answer(
        f"✅ *{label}* muvaffaqiyatli yangilandi!",
        parse_mode="Markdown",
        reply_markup=admin_menu(message.from_user.id)
    )


# ─── KARTA O'ZGARTIRISH ───────────────────────────────────────────────────────

@dp.message(F.text == "💳 Karta raqamini o'zgartirish", StateFilter("*"))
async def start_card_change(message: types.Message, state: FSMContext):
    if not await has_admin_permission(message.from_user.id, "change_card"):
        return
    await state.clear()
    card_number, card_holder = await get_card_info()
    holder_line = f"\n👤 Hozirgi egasi: {md_escape(card_holder)}" if card_holder else ""
    await state.set_state(AdminCardChange.waiting_for_number)
    await message.answer(
        f"💳 Hozirgi karta raqami: `{card_number}`{holder_line}\n\n"
        "Yangi karta raqamini kiriting (masalan: 8600 1234 5678 9012):",
        parse_mode="Markdown",
        reply_markup=types.ReplyKeyboardRemove()
    )

@dp.message(AdminCardChange.waiting_for_number)
async def process_card_number(message: types.Message, state: FSMContext):
    if not await has_admin_permission(message.from_user.id, "change_card"):
        await state.clear()
        await message.answer("⛔ Sizda karta ma'lumotlarini o'zgartirish huquqi yo'q.")
        return
    await state.update_data(card_number=message.text.strip())
    await state.set_state(AdminCardChange.waiting_for_holder)
    await message.answer("👤 Endi karta egasining F.I.Sh (ism-familiyasi)ni kiriting:")

@dp.message(AdminCardChange.waiting_for_holder)
async def process_card_holder(message: types.Message, state: FSMContext):
    if not await has_admin_permission(message.from_user.id, "change_card"):
        await state.clear()
        await message.answer("⛔ Sizda karta ma'lumotlarini o'zgartirish huquqi yo'q.")
        return
    data = await state.get_data()
    card_number = data.get("card_number")
    card_holder = message.text.strip()
    await set_card_info(card_number, card_holder)
    await state.clear()
    await message.answer(
        f"✅ Karta ma'lumotlari yangilandi!\n\n"
        f"💳 `{card_number}`\n"
        f"👤 {md_escape(card_holder)}",
        parse_mode="Markdown",
        reply_markup=admin_menu(message.from_user.id)
    )


# ─── ADMINLARNI BOSHQARISH ────────────────────────────────────────────────────

@dp.message(F.text == "👨‍💼 Adminlar", StateFilter("*"))
async def admins_panel(message: types.Message, state: FSMContext):
    if not await has_admin_permission(message.from_user.id, "manage_admins"):
        return
    await state.clear()
    admins = await get_admins_list()
    text = "👨‍💼 *Adminlar ro'yxati:*\n\n"
    text += f"👑 Asosiy admin: `{ADMIN_ID}`\n\n"
    if admins:
        for row in admins:
            uname_part = f"@{md_escape(row['username'])}" if row["username"] else "username yo'q"
            permissions = parse_admin_permissions(row["permissions"])
            permission_names = [
                ADMIN_PERMISSION_LABELS[key]
                for key in ADMIN_PERMISSION_LABELS
                if key in permissions
            ]
            permission_text = ", ".join(permission_names) if permission_names else "Huquq berilmagan"
            text += (
                f"🔹 `{row['user_id']}` — {uname_part} ({row['added_at']})\n"
                f"   🔐 Huquqlar: {permission_text}\n"
            )
    else:
        text += "Qo'shimcha adminlar yo'q."

    if await has_admin_permission(message.from_user.id, "manage_admins"):
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="➕ Admin qo'shish", callback_data="add_admin_start"
            )],
            [InlineKeyboardButton(
                text="➖ Admin olib tashlash", callback_data="remove_admin_start"
            )]
        ])
        await message.answer(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await message.answer(text, parse_mode="Markdown")

@dp.callback_query(F.data == "add_admin_start")
async def add_admin_start_cb(call: types.CallbackQuery, state: FSMContext):
    if not await has_admin_permission(call.from_user.id, "manage_admins"):
        await call.answer(
            "⛔ Sizda adminlarni boshqarish huquqi yo'q.", show_alert=True
        )
        return
    await state.set_state(AdminManage.waiting_for_add_id)
    await call.message.answer("➕ Yangi adminning Telegram ID raqamini yuboring:")
    await call.answer()

@dp.message(AdminManage.waiting_for_add_id)
async def process_add_admin(message: types.Message, state: FSMContext):
    if not await has_admin_permission(message.from_user.id, "manage_admins"):
        await state.clear()
        await message.answer("⛔ Sizda adminlarni boshqarish huquqi yo'q.")
        return
    try:
        new_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Noto'g'ri format. Faqat raqam (ID) yuboring.")
        return
    if new_id == ADMIN_ID or await is_bot_admin(new_id):
        await message.answer("⚠️ Bu foydalanuvchi allaqachon admin.")
        await state.clear()
        return
    chat = await safe_get_chat(new_id)
    uname = getattr(chat, "username", "") if chat else ""
    await state.update_data(
        new_admin_id=new_id,
        new_admin_username=uname or "",
        selected_permissions=[],
    )
    await state.set_state(AdminManage.waiting_for_permissions)
    await message.answer(
        f"👤 Yangi admin: `{new_id}`\n\n"
        "Qaysi huquqlarni bermoqchisiz?\n"
        "Kerakli bo'limlarni bosib tanlang, so'ng «✅ Saqlash»ni bosing.",
        parse_mode="Markdown",
        reply_markup=build_admin_permissions_kb([]),
    )

@dp.callback_query(
    F.data.startswith("admin_perm_toggle_"),
    AdminManage.waiting_for_permissions,
)
async def toggle_admin_permission_cb(call: types.CallbackQuery, state: FSMContext):
    if not await has_admin_permission(call.from_user.id, "manage_admins"):
        await call.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return
    permission = call.data[len("admin_perm_toggle_"):]
    if permission not in ADMIN_PERMISSION_LABELS:
        await call.answer("❌ Noma'lum huquq.", show_alert=True)
        return

    data = await state.get_data()
    selected = set(data.get("selected_permissions", []))
    if permission in selected:
        selected.remove(permission)
    else:
        selected.add(permission)
    selected_list = [
        key for key in ADMIN_PERMISSION_LABELS if key in selected
    ]
    await state.update_data(selected_permissions=selected_list)
    try:
        await call.message.edit_reply_markup(
            reply_markup=build_admin_permissions_kb(selected_list)
        )
    except Exception:
        pass
    await call.answer()

@dp.callback_query(
    F.data == "admin_perm_cancel",
    AdminManage.waiting_for_permissions,
)
async def cancel_admin_permissions_cb(call: types.CallbackQuery, state: FSMContext):
    if not await has_admin_permission(call.from_user.id, "manage_admins"):
        await call.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return
    await state.clear()
    await call.message.answer(
        "❌ Admin qo'shish bekor qilindi.",
        reply_markup=admin_menu(call.from_user.id),
    )
    await call.answer()

@dp.callback_query(
    F.data == "admin_perm_save",
    AdminManage.waiting_for_permissions,
)
async def save_admin_permissions_cb(call: types.CallbackQuery, state: FSMContext):
    if not await has_admin_permission(call.from_user.id, "manage_admins"):
        await call.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return
    data = await state.get_data()
    new_id = data.get("new_admin_id")
    uname = data.get("new_admin_username", "")
    permissions = data.get("selected_permissions", [])
    if not new_id:
        await state.clear()
        await call.answer("❌ Ma'lumot topilmadi, qaytadan boshlang.", show_alert=True)
        return

    await add_admin(new_id, uname, permissions)
    await state.clear()
    await call.message.answer(
        f"✅ `{new_id}` endi admin!\n\n"
        f"🔐 Berilgan huquqlar: "
        f"{', '.join(ADMIN_PERMISSION_LABELS[key] for key in permissions) or 'Huquq berilmagan'}",
        parse_mode="Markdown",
        reply_markup=admin_menu(call.from_user.id)
    )
    try:
        await bot.send_message(
            new_id,
            "🎉 Sizga bot admin huquqi berildi!\n"
            "Botdagi /admin buyrug'ini yuboring.\n\n"
            "Sizga berilgan bo'limlar admin panelida knopka sifatida ko'rinadi."
        )
    except Exception:
        pass
    await call.answer("✅ Saqlandi")

@dp.callback_query(F.data == "remove_admin_start")
async def remove_admin_start_cb(call: types.CallbackQuery):
    if not await has_admin_permission(call.from_user.id, "manage_admins"):
        await call.answer(
            "⛔ Sizda adminlarni boshqarish huquqi yo'q.", show_alert=True
        )
        return
    admins = await get_admins_list()
    if not admins:
        await call.answer("Qo'shimcha adminlar yo'q.", show_alert=True)
        return
    kb_rows = []
    for row in admins:
        label = f"@{row['username']}" if row["username"] else str(row["user_id"])
        kb_rows.append([InlineKeyboardButton(
            text=f"❌ {label}",
            callback_data=f"rm_admin_{row['user_id']}"
        )])
    await call.message.answer(
        "Olib tashlamoqchi bo'lgan adminni tanlang:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
    )
    await call.answer()

@dp.callback_query(F.data.startswith("rm_admin_"))
async def process_remove_admin(call: types.CallbackQuery):
    if not await has_admin_permission(call.from_user.id, "manage_admins"):
        await call.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return
    target_id = int(call.data.split("_")[2])
    await remove_admin(target_id)
    await call.message.answer(
        f"✅ `{target_id}` admin huquqidan olib tashlandi.", parse_mode="Markdown"
    )
    await call.answer()
    try:
        await bot.send_message(
            target_id, "ℹ️ Sizning bot admin huquqingiz olib tashlandi."
        )
    except Exception:
        pass


# ─── MAXFIY KANAL: A'ZOLIKKA SO'ROV (JOIN REQUEST) ───────────────────────────

async def resolve_tracked_channel_id(chat_id: int, username: str = None):
    """
    Berilgan chat botning `channels` bazasida (type='telegram') qanday
    channel_id bilan saqlanganini topadi (raqamli ID yoki @username
    ko'rinishida) va shuni qaytaradi. Topilmasa None qaytaradi — bu holda
    kanal botga aloqasi yo'q, aralashmaymiz.
    """
    db = await get_db()
    async with db.execute(
        "SELECT channel_id FROM channels WHERE type='telegram'"
    ) as cur:
        rows = await cur.fetchall()
    ids = {row["channel_id"] for row in rows}
    if str(chat_id) in ids:
        return str(chat_id)
    if username and f"@{username}" in ids:
        return f"@{username}"
    return None

@dp.chat_join_request()
async def handle_join_request(join_request: types.ChatJoinRequest):
    """
    Kanalda "Yangi a'zolarni tasdiqlash" (Approve New Members) yoqilgan
    bo'lsa, taklif havolasini bosgan foydalanuvchi darhol a'zo bo'lmaydi —
    Telegram "qo'shilish so'rovi" yaratadi va kanal administratori buni
    QO'LDA tasdiqlaydi (bot AVTOMATIK tasdiqlamaydi).

    Lekin botdan foydalanish uchun buni kutish shart emas: foydalanuvchi
    so'rov YUBORGANI botga yetarli dalil sifatida hisoblanadi va
    check_subscriptions uni "obuna bo'lgan" deb qabul qiladi.
    """
    chat = join_request.chat
    user = join_request.from_user

    ch_id = await resolve_tracked_channel_id(chat.id, getattr(chat, "username", None))
    if not ch_id:
        return  # Botga aloqasi bo'lmagan kanal — aralashmaymiz

    db = await get_db()
    await db.execute(
        "INSERT OR REPLACE INTO manual_confirmations (user_id, channel_id, confirmed_at) "
        "VALUES (?, ?, ?)",
        (user.id, ch_id, datetime.now().isoformat())
    )
    await db.commit()
    logging.info(f"Join so'rovi qayd etildi (avtomatik tasdiqlanmadi): chat={chat.id} user={user.id}")

    # Foydalanuvchiga botdan qisqa xabar (ixtiyoriy, botni bloklagan bo'lsa xato bermaydi)
    try:
        await bot.send_message(
            user.id,
            f"✅ *{md_escape(chat.title or 'Kanal')}* ga qo'shilish so'rovingiz qabul qilindi!\n\n"
            f"Administrator tez orada tasdiqlaydi. Hozircha botdan foydalanishingiz mumkin — "
            f"\"✅ Tekshirish\" tugmasini bosing.",
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.warning(f"Join-request foydalanuvchiga xabar yuborilmadi ({user.id}): {e}")


# ─── COMMANDS ────────────────────────────────────────────────────────────────

@dp.message(Command("kino"))
async def cmd_kino(message: types.Message):
    text, kb = await render_media_list("kino", 0)
    await message.answer(text, parse_mode="Markdown", reply_markup=kb)

@dp.message(Command("serial"))
async def cmd_serial(message: types.Message):
    text, kb = await render_media_list("serial", 0)
    await message.answer(text, parse_mode="Markdown", reply_markup=kb)

@dp.message(Command("anime"))
async def cmd_anime(message: types.Message):
    text, kb = await render_media_list("anime", 0)
    await message.answer(text, parse_mode="Markdown", reply_markup=kb)

@dp.message(Command("search"))
async def cmd_search(message: types.Message, state: FSMContext):
    args = message.text.split(maxsplit=1)
    if len(args) > 1:
        code = args[1].strip()
        await deliver_media_by_code(message, message.from_user.id, code)
    else:
        await state.set_state(CodeSearch.waiting_for_code)
        await message.answer("🔍 Kino kodini kiriting:")

@dp.message(Command("premium"))
async def cmd_premium(message: types.Message):
    await premium_info(message)

@dp.message(Command("get_main"))
async def send_main_file(message: types.Message):
    """Faqat asosiy admin uchun tayyor bot faylini yuboradi."""
    user_id = message.from_user.id if message.from_user else 0
    if not is_super_admin(user_id):
        return

    file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
    if not os.path.isfile(file_path):
        await message.answer("❌ main.py fayli topilmadi.")
        return

    await message.answer_document(
        document=types.FSInputFile(file_path, filename="main.py"),
        caption="✅ Botning tayyor main.py fayli."
    )


# ─── TANILMAGAN XABARLAR (CATCH-ALL) ─────────────────────────────────────────

@dp.message(StateFilter(None))
async def unknown_message(message: types.Message):
    """
    Hech qaysi handlerga tushmaydigan xabarlarga darhol javob beradi.
    """
    user_id = message.from_user.id

    if await is_bot_admin(user_id):
        await message.answer(
            "ℹ️ Noma'lum buyruq. Menyudan foydalaning:",
            reply_markup=admin_menu(user_id)
        )
        return

    if not await is_premium_user(user_id):
        unsub = await check_subscriptions(user_id)
        if unsub:
            kb = await build_subscription_keyboard(unsub)
            await message.answer(
                await get_message_template("subscribe_required"),
                reply_markup=kb, parse_mode="Markdown"
            )
            return

    await message.answer(
        "ℹ️ Noma'lum buyruq. Quyidagi menyudan foydalaning:",
        reply_markup=main_menu(user_id)
    )


# ─── ISHGA TUSHIRISH ─────────────────────────────────────────────────────────

async def set_commands():
    user_commands = [
        types.BotCommand(command="start",   description="🚀 Botni ishga tushirish"),
        types.BotCommand(command="kino",    description="🎬 Kinolar ro'yxati"),
        types.BotCommand(command="serial",  description="📺 Seriallar ro'yxati"),
        types.BotCommand(command="anime",   description="⛩ Anime va Multfilmlar"),
        types.BotCommand(command="search",  description="🔍 Kod orqali qidirish"),
        types.BotCommand(command="premium", description="🌟 Premium bo'lim"),
    ]
    admin_commands = user_commands + [
        types.BotCommand(command="admin",  description="👨‍💻 Admin panel"),
        types.BotCommand(command="bekor",  description="❌ Amalni bekor qilish"),
        types.BotCommand(command="get_main", description="📄 main.py faylini olish"),
    ]
    # Umumiy buyruqlar ro'yxatida /admin bo'lmaydi.
    await bot.set_my_commands(user_commands)
    try:
        # Faqat asosiy admin chatida qo'shimcha buyruqlar ko'rinadi.
        await bot.set_my_commands(
            admin_commands,
            scope=types.BotCommandScopeChat(chat_id=ADMIN_ID)
        )
    except Exception as e:
        logging.warning(f"Admin buyruqlari o'rnatilmadi: {e}")

@dp.errors()
async def global_error_handler(event: types.ErrorEvent):
    """
    Har qanday handlerda ushlanmagan kutilmagan xato shu yerga tushadi.
    Buning yo'qligi sabab, ilgari ba'zi xatolar foydalanuvchiga HECH
    QANDAY javob bermay, "sukut bilan" yo'qolib ketardi (masalan tugma
    bosilganda hech narsa bo'lmagandek tuyulishi). Endi bunday holatda
    ham kamida log yoziladi va imkon bo'lsa foydalanuvchi/admin xabardor
    qilinadi.
    """
    # "query is too old" — muddati o'tgan callback query, jimgina o'tkazib yubor
    if isinstance(event.exception, TelegramBadRequest) and "query is too old" in str(event.exception):
        logging.warning(f"Muddati o'tgan callback query e'tiborsiz qoldirildi: {event.exception}")
        return

    logging.error(f"Global xato: {event.exception}", exc_info=event.exception)
    try:
        upd = event.update
        chat_id = None
        if upd.message:
            chat_id = upd.message.chat.id
        elif upd.callback_query and upd.callback_query.message:
            chat_id = upd.callback_query.message.chat.id
            try:
                await upd.callback_query.answer("❌ Xatolik yuz berdi.", show_alert=True)
            except Exception:
                pass
        if chat_id:
            if await is_bot_admin(chat_id):
                await bot.send_message(
                    chat_id,
                    f"❌ Kutilmagan xato yuz berdi.\n\n🛠 {event.exception}"
                )
            else:
                await bot.send_message(
                    chat_id, "❌ Xatolik yuz berdi. Birozdan so'ng qaytadan urinib ko'ring."
                )
    except Exception:
        pass


async def run_bot():
    global BOT_USERNAME

    await init_db()

    try:
        me = await bot.get_me()
        BOT_USERNAME = me.username
        logging.info(f"Bot: @{BOT_USERNAME} (id={me.id})")
    except Exception as e:
        logging.error(f"Bot username olishda xato: {e}")

    try:
        await bot.set_my_short_description("Kino, Serial va Anime botga xush kelibsiz!")
        await bot.set_my_description(
            "🚀 Kino, serial va animelarni tezkor tomosha qiling!\n"
            "🌟 Premium a'zolik: eksklyuziv HD kinolar!"
        )
        await set_commands()
    except Exception as e:
        logging.error(f"Bot ma'lumotlarini o'rnatishda xato: {e}")

    # Birinchi ishga tushishda tayyor faylni asosiy adminga yuborishga urinadi.
    # Admin bot bilan avval /start orqali suhbat boshlagan bo'lishi kerak.
    try:
        file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
        if os.path.isfile(file_path):
            await bot.send_document(
                chat_id=ADMIN_ID,
                document=types.FSInputFile(file_path, filename="main.py"),
                caption="✅ Botning tayyor main.py fayli."
            )
            logging.info("main.py fayli asosiy adminga yuborildi.")
    except Exception as e:
        logging.warning(f"main.py faylini adminga yuborib bo'lmadi: {e}")

    asyncio.create_task(premium_checker())
    asyncio.create_task(backup_scheduler())
    asyncio.create_task(link_refresh_scheduler())

    first_start = True
    while True:
        try:
            logging.info("Bot ishga tushmoqda (polling)...")
            await dp.start_polling(
                bot,
                allowed_updates=dp.resolve_used_update_types(),
                drop_pending_updates=first_start,
            )
        except Exception as e:
            logging.error(f"Polling to'xtadi: {e}. 5 soniyadan keyin qayta uriniladi...")
            await asyncio.sleep(5)
        finally:
            first_start = False

if __name__ == "__main__":
    asyncio.run(run_bot())
