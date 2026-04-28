import asyncio
import logging
import sqlite3
import random
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Tuple
import aiohttp

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, InputMediaPhoto
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest

# ------------------ КОНФИГ ------------------
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"   # замените
ADMIN_IDS = [123456789]             # ваш ID

# Каналы для проверки подписки (только для просмотра видео)
REQUIRED_CHANNELS = ["@Scam_officiali"]  # укажите ваш канал

# Платёжные реквизиты для ручных переводов
PAYMENT_DETAILS = {
    "card": "2200700538676841",
    "name": "Сергей",
    "currency": "₽"
}

# Цены
VIDEO_PRICE = 2               # алмазов за просмотр
PREMIUM_PRICE_RUB = 99
PREMIUM_PRICE_STARS = 50
PRIVATE_PRICE_RUB = 300
PRIVATE_PRICE_STARS = 150

# Ежедневный бонус (автоматический, без нажатия кнопки)
DAILY_BONUS_NORMAL = 2
DAILY_BONUS_PREMIUM = 5

# Пакеты алмазов (алмазы: цена в рублях)
DIAMOND_PACKS = {6: 50, 10: 90, 12: 110, 20: 150}

# Награда и лимиты для токенов
TOKEN_REWARD = 2              # 2 алмаза за токен
TOKEN_COOLDOWN = 86400        # 1 раз в сутки

# База данных
DB_PATH = "bot_database.db"

# ------------------ ИНИЦИАЛИЗАЦИЯ БД ------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Таблица users
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        balance INTEGER DEFAULT 4,
        is_premium INTEGER DEFAULT 0,
        premium_until INTEGER DEFAULT 0,
        is_private INTEGER DEFAULT 0,
        private_until INTEGER DEFAULT 0,
        banned INTEGER DEFAULT 0,
        payment_id TEXT UNIQUE,
        next_bonus_time INTEGER DEFAULT 0,
        referrer_id INTEGER DEFAULT 0,
        referrals_count INTEGER DEFAULT 0,
        lang TEXT DEFAULT 'ru'
    )''')

    # Добавляем недостающие колонки
    c.execute("PRAGMA table_info(users)")
    existing = [col[1] for col in c.fetchall()]
    if "is_private" not in existing:
        c.execute("ALTER TABLE users ADD COLUMN is_private INTEGER DEFAULT 0")
    if "private_until" not in existing:
        c.execute("ALTER TABLE users ADD COLUMN private_until INTEGER DEFAULT 0")
    if "next_bonus_time" not in existing:
        c.execute("ALTER TABLE users ADD COLUMN next_bonus_time INTEGER DEFAULT 0")
    if "referrer_id" not in existing:
        c.execute("ALTER TABLE users ADD COLUMN referrer_id INTEGER DEFAULT 0")
    if "referrals_count" not in existing:
        c.execute("ALTER TABLE users ADD COLUMN referrals_count INTEGER DEFAULT 0")
    # Колонка daily_bonus_last больше не нужна, но оставим для совместимости
    if "daily_bonus_last" not in existing:
        c.execute("ALTER TABLE users ADD COLUMN daily_bonus_last INTEGER DEFAULT 0")

    # Таблица контента
    c.execute('''CREATE TABLE IF NOT EXISTS contents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        file_id TEXT,
        media_type TEXT DEFAULT 'video',
        is_vip INTEGER DEFAULT 0
    )''')
    # Перенос из старой таблицы videos
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='videos'")
    if c.fetchone():
        c.execute("INSERT INTO contents (name, file_id, is_vip) SELECT name, file_id, is_vip FROM videos")
        c.execute("DROP TABLE videos")

    # Таблица покупок
    c.execute('''CREATE TABLE IF NOT EXISTS purchases (
        user_id INTEGER,
        content_id INTEGER,
        timestamp INTEGER,
        PRIMARY KEY (user_id, content_id)
    )''')

    # Промокоды
    c.execute('''CREATE TABLE IF NOT EXISTS promocodes (
        code TEXT PRIMARY KEY,
        reward INTEGER,
        max_uses INTEGER,
        used_count INTEGER DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS used_promocodes (
        user_id INTEGER,
        code TEXT,
        PRIMARY KEY (user_id, code)
    )''')

    # Скидки
    c.execute('''CREATE TABLE IF NOT EXISTS discounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_type TEXT,
        product_id INTEGER DEFAULT 0,
        discount_percent INTEGER,
        until INTEGER,
        is_active INTEGER DEFAULT 1
    )''')

    # Заявки на оплату
    c.execute('''CREATE TABLE IF NOT EXISTS pending_payments (
        user_id INTEGER,
        payment_id TEXT,
        amount_rub INTEGER,
        type TEXT,
        diamonds INTEGER DEFAULT 0,
        timestamp INTEGER,
        status TEXT DEFAULT 'pending'
    )''')

    # TikTok задания
    c.execute('''CREATE TABLE IF NOT EXISTS tiktok_tasks (
        user_id INTEGER PRIMARY KEY,
        last_completed INTEGER DEFAULT 0
    )''')

    # Токены
    c.execute('''CREATE TABLE IF NOT EXISTS user_tokens (
        user_id INTEGER,
        token TEXT,
        submitted_at INTEGER,
        bot_username TEXT,
        PRIMARY KEY (user_id, token)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS token_stats (
        user_id INTEGER PRIMARY KEY,
        last_submit INTEGER DEFAULT 0
    )''')
    if "bot_username" not in [col[1] for col in c.execute("PRAGMA table_info(user_tokens)").fetchall()]:
        c.execute("ALTER TABLE user_tokens ADD COLUMN bot_username TEXT")

    # Сообщения в поддержку
    c.execute('''CREATE TABLE IF NOT EXISTS support_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        message TEXT,
        created_at INTEGER,
        status TEXT DEFAULT 'pending'
    )''')

    # Логи алмазов
    c.execute('''CREATE TABLE IF NOT EXISTS diamond_logs (
        user_id INTEGER,
        amount INTEGER,
        reason TEXT,
        timestamp INTEGER
    )''')

    conn.commit()
    conn.close()

    # Генерация payment_id для старых пользователей
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE payment_id IS NULL")
    rows = c.fetchall()
    for (user_id,) in rows:
        while True:
            new_id = f"#Radion_{random.randint(10000, 999999)}"
            try:
                c.execute("UPDATE users SET payment_id = ? WHERE user_id = ?", (new_id, user_id))
                conn.commit()
                break
            except sqlite3.IntegrityError:
                continue
    conn.close()

init_db()

# ------------------ РАБОТА С БД ------------------
def get_user(user_id: int) -> Optional[Dict]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id, username, balance, is_premium, premium_until, is_private, private_until, banned, payment_id, next_bonus_time, referrer_id, referrals_count FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            "user_id": row[0], "username": row[1], "balance": row[2],
            "is_premium": row[3], "premium_until": row[4],
            "is_private": row[5], "private_until": row[6],
            "banned": row[7], "payment_id": row[8],
            "next_bonus_time": row[9], "referrer_id": row[10], "referrals_count": row[11]
        }
    return None

def generate_unique_payment_id() -> str:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    while True:
        new_id = f"#Radion_{random.randint(10000, 999999)}"
        c.execute("SELECT 1 FROM users WHERE payment_id = ?", (new_id,))
        if not c.fetchone():
            conn.close()
            return new_id

def update_balance(user_id: int, delta: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (delta, user_id))
    conn.commit()
    conn.close()

def set_premium(user_id: int, days: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    until = int((datetime.now() + timedelta(days=days)).timestamp())
    c.execute("UPDATE users SET is_premium = 1, premium_until = ? WHERE user_id = ?", (until, user_id))
    conn.commit()
    conn.close()

def set_private(user_id: int, days: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    until = int((datetime.now() + timedelta(days=days)).timestamp())
    c.execute("UPDATE users SET is_private = 1, private_until = ? WHERE user_id = ?", (until, user_id))
    conn.commit()
    conn.close()

def set_ban(user_id: int, banned: bool):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET banned = ? WHERE user_id = ?", (1 if banned else 0, user_id))
    conn.commit()
    conn.close()

# ---------- АВТОМАТИЧЕСКИЙ ЕЖЕДНЕВНЫЙ БОНУС ----------
def set_next_bonus_time(user_id: int, seconds: int = 86400):
    """Устанавливает время следующего бонуса (текущее время + seconds)."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    next_time = int(datetime.now().timestamp()) + seconds
    c.execute("UPDATE users SET next_bonus_time = ? WHERE user_id = ?", (next_time, user_id))
    conn.commit()
    conn.close()

def check_and_give_bonus(user_id: int) -> bool:
    """Проверяет, не пора ли выдать бонус. Если пора – выдает и возвращает True."""
    user = get_user(user_id)
    if not user:
        return False
    now = int(datetime.now().timestamp())
    if user['next_bonus_time'] == 0:
        # Первый раз – устанавливаем бонус через 24 часа
        set_next_bonus_time(user_id)
        return False
    if now >= user['next_bonus_time']:
        premium_active = user['is_premium'] and user['premium_until'] > now
        bonus = DAILY_BONUS_PREMIUM if premium_active else DAILY_BONUS_NORMAL
        update_balance(user_id, bonus)
        set_next_bonus_time(user_id)
        return True
    return False

# ---------- ФУНКЦИИ ДЛЯ КОНТЕНТА ----------
def get_all_content(media_type=None, vip_only=False) -> List[Dict]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    query = "SELECT id, name, file_id, media_type, is_vip FROM contents WHERE 1=1"
    params = []
    if media_type:
        query += " AND media_type = ?"
        params.append(media_type)
    if vip_only:
        query += " AND is_vip = 1"
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    return [{"id": r[0], "name": r[1], "file_id": r[2], "media_type": r[3], "is_vip": r[4]} for r in rows]

def get_random_content(media_type='video') -> Optional[Dict]:
    items = get_all_content(media_type=media_type, vip_only=False)
    if not items:
        return None
    return random.choice(items)

def add_content(file_id: str, media_type: str, is_vip: int = 0):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM contents WHERE media_type = ?", (media_type,))
    count = c.fetchone()[0] + 1
    name = f"{'Видео' if media_type=='video' else 'Фото'} #{count}"
    c.execute("INSERT INTO contents (name, file_id, media_type, is_vip) VALUES (?, ?, ?, ?)", (name, file_id, media_type, is_vip))
    conn.commit()
    conn.close()

def remove_content(content_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM contents WHERE id = ?", (content_id,))
    conn.commit()
    conn.close()

def user_has_purchased(user_id: int, content_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT 1 FROM purchases WHERE user_id = ? AND content_id = ?", (user_id, content_id))
    res = c.fetchone()
    conn.close()
    return res is not None

def add_purchase(user_id: int, content_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO purchases (user_id, content_id, timestamp) VALUES (?, ?, ?)", (user_id, content_id, int(datetime.now().timestamp())))
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    finally:
        conn.close()

# ---------- ФУНКЦИИ ДЛЯ ПРОМОКОДОВ ----------
def apply_promocode(user_id: int, code: str) -> Tuple[bool, int]:
    code = code.strip().upper()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT reward, max_uses, used_count FROM promocodes WHERE code = ?", (code,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False, 0
    reward, max_uses, used = row
    if max_uses > 0 and used >= max_uses:
        conn.close()
        return False, 0
    c.execute("SELECT 1 FROM used_promocodes WHERE user_id = ? AND code = ?", (user_id, code))
    if c.fetchone():
        conn.close()
        return False, 0
    c.execute("UPDATE promocodes SET used_count = used_count + 1 WHERE code = ?", (code,))
    c.execute("INSERT INTO used_promocodes (user_id, code) VALUES (?, ?)", (user_id, code))
    c.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (reward, user_id))
    conn.commit()
    conn.close()
    return True, reward

def get_all_promocodes():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT code, reward, max_uses, used_count FROM promocodes")
    rows = c.fetchall()
    conn.close()
    return rows

def add_promocode(code: str, reward: int, max_uses: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO promocodes (code, reward, max_uses, used_count) VALUES (?, ?, ?, 0)", (code.upper(), reward, max_uses))
    conn.commit()
    conn.close()

def delete_promocode(code: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM promocodes WHERE code = ?", (code.upper(),))
    conn.commit()
    conn.close()

# ---------- ФУНКЦИИ ДЛЯ СКИДОК ----------
def get_discount(product_type: str, product_id: int = 0) -> int:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    now = int(datetime.now().timestamp())
    c.execute("SELECT discount_percent FROM discounts WHERE product_type = ? AND product_id = ? AND is_active = 1 AND until > ?", (product_type, product_id, now))
    row = c.fetchone()
    if row:
        conn.close()
        return row[0]
    c.execute("SELECT discount_percent FROM discounts WHERE product_type = ? AND product_id = 0 AND is_active = 1 AND until > ?", (product_type, now))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0

def add_discount(product_type: str, discount_percent: int, days: int, product_id: int = 0):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    until = int((datetime.now() + timedelta(days=days)).timestamp())
    c.execute("INSERT INTO discounts (product_type, product_id, discount_percent, until, is_active) VALUES (?, ?, ?, ?, 1)",
              (product_type, product_id, discount_percent, until))
    conn.commit()
    conn.close()

def remove_discount(discount_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE discounts SET is_active = 0 WHERE id = ?", (discount_id,))
    conn.commit()
    conn.close()

def get_all_discounts():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, product_type, product_id, discount_percent, until FROM discounts WHERE is_active = 1")
    rows = c.fetchall()
    conn.close()
    return rows

# ---------- TIKTOK ----------
def can_submit_tiktok(user_id: int) -> Tuple[bool, int]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT last_completed FROM tiktok_tasks WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    last = row[0] if row else 0
    now = int(datetime.now().timestamp())
    if now - last < 86400:
        return False, 86400 - (now - last)
    return True, 0

def set_tiktok_completed(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO tiktok_tasks (user_id, last_completed) VALUES (?, ?)", (user_id, int(datetime.now().timestamp())))
    conn.commit()
    conn.close()

# ---------- ТОКЕНЫ ----------
def can_submit_token(user_id: int) -> Tuple[bool, int]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT last_submit FROM token_stats WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    now = int(datetime.now().timestamp())
    if not row:
        conn.close()
        return True, 0
    last = row[0]
    if now - last < TOKEN_COOLDOWN:
        return False, TOKEN_COOLDOWN - (now - last)
    conn.close()
    return True, 0

def register_token_usage(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    now = int(datetime.now().timestamp())
    c.execute("INSERT OR REPLACE INTO token_stats (user_id, last_submit) VALUES (?, ?)", (user_id, now))
    conn.commit()
    conn.close()

# ---------- СТАТИСТИКА ----------
def get_stats():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    total_users = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE banned = 1")
    banned_users = c.fetchone()[0]
    now = int(datetime.now().timestamp())
    c.execute("SELECT COUNT(*) FROM users WHERE is_premium = 1 AND premium_until > ?", (now,))
    premium_users = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE is_private = 1 AND private_until > ?", (now,))
    private_users = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM contents WHERE media_type='video' AND is_vip=0")
    videos = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM contents WHERE media_type='video' AND is_vip=1")
    vip_videos = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM pending_payments WHERE status='pending'")
    pending_payments = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM tiktok_tasks")
    tiktok_tasks = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM support_messages")
    support_msgs = c.fetchone()[0]
    conn.close()
    return total_users, banned_users, premium_users, private_users, videos, vip_videos, pending_payments, tiktok_tasks, support_msgs

# ---------- АКТИВНЫЕ ПОПОЛНЕНИЯ ----------
def get_active_payments():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id, payment_id, amount_rub, type, diamonds, timestamp FROM pending_payments WHERE status='pending' ORDER BY timestamp DESC")
    rows = c.fetchall()
    conn.close()
    return rows

# ---------- БОТ И КЛАВИАТУРЫ ----------
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
BOT_USERNAME = None

async def get_bot_username():
    global BOT_USERNAME
    me = await bot.get_me()
    BOT_USERNAME = me.username

# ---------- КАРТИНКА ГЛАВНОГО МЕНЮ ----------
MAIN_MENU_PHOTO_URL = "https://gspics.org/images/2026/04/27/IAm18s.png"
MAIN_MENU_PHOTO_FILE_ID = None

async def get_main_menu_photo() -> str:
    global MAIN_MENU_PHOTO_FILE_ID
    if MAIN_MENU_PHOTO_FILE_ID:
        return MAIN_MENU_PHOTO_FILE_ID
    try:
        msg = await bot.send_photo(chat_id=ADMIN_IDS[0] if ADMIN_IDS else 123456789, photo=MAIN_MENU_PHOTO_URL)
        MAIN_MENU_PHOTO_FILE_ID = msg.photo[-1].file_id
        return MAIN_MENU_PHOTO_FILE_ID
    except Exception:
        return None

# ---------- ПРОВЕРКА ПОДПИСКИ (только для видео) ----------
async def is_subscribed(user_id: int) -> bool:
    for channel in REQUIRED_CHANNELS:
        try:
            chat = await bot.get_chat(channel)
            member = await bot.get_chat_member(chat.id, user_id)
            if member.status not in ["member", "administrator", "creator"]:
                return False
        except Exception:
            return False
    return True

# ---------- КЛАВИАТУРЫ ----------
def get_main_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton(text="🎬 Смотреть видео"), KeyboardButton(text="💎 Купить алмазы")],
        [KeyboardButton(text="⭐ Премиум"), KeyboardButton(text="🔞 ПРИВАТ 18+")],
        [KeyboardButton(text="💰 Заработать"), KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="🎫 Промокод"), KeyboardButton(text="📞 Поддержка")],
        [KeyboardButton(text="🔄 Обновить")]
    ]
    if user_id in ADMIN_IDS:
        buttons.append([KeyboardButton(text="⚙️ Админ панель")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_subscription_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for ch in REQUIRED_CHANNELS:
        builder.row(InlineKeyboardButton(text=f"📢 ПОДПИСАТЬСЯ НА {ch}", url=f"https://t.me/{ch.lstrip('@')}"))
    builder.row(InlineKeyboardButton(text="✅ ПРОВЕРИТЬ ПОДПИСКУ", callback_data="check_sub"))
    return builder.as_markup()

def get_earn_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="👥 РЕФЕРАЛЫ", callback_data="earn_ref"))
    builder.row(InlineKeyboardButton(text="📸 TIKTOK", callback_data="earn_tiktok"))
    builder.row(InlineKeyboardButton(text="🤖 ТОКЕН БОТА", callback_data="earn_token"))
    builder.row(InlineKeyboardButton(text="❌ ОТМЕНА", callback_data="cancel_earn"))
    return builder.as_markup()

def get_diamond_packs_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for diamonds, price in DIAMOND_PACKS.items():
        discount = get_discount("diamonds")
        if discount:
            new_price = price * (100 - discount) // 100
            builder.row(InlineKeyboardButton(text=f"💎 {diamonds} алмазов – {new_price}₽ (скидка {discount}%)", callback_data=f"buy_diamonds_{diamonds}"))
        else:
            builder.row(InlineKeyboardButton(text=f"💎 {diamonds} алмазов – {price}₽", callback_data=f"buy_diamonds_{diamonds}"))
    builder.row(InlineKeyboardButton(text="◀️ ОТМЕНА", callback_data="cancel"))
    return builder.as_markup()

def get_payment_keyboard(payment_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📋 СКОПИРОВАТЬ ID", callback_data=f"copy_id_{payment_id}"))
    builder.row(InlineKeyboardButton(text="✅ Я ОПЛАТИЛ", callback_data="i_paid"))
    builder.row(InlineKeyboardButton(text="❌ ОТМЕНА", callback_data="cancel"))
    return builder.as_markup()

def get_admin_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="➕ ДОБАВИТЬ КОНТЕНТ", callback_data="admin_add_content"))
    builder.row(InlineKeyboardButton(text="🗑 УДАЛИТЬ КОНТЕНТ", callback_data="admin_del_content"))
    builder.row(InlineKeyboardButton(text="💎 ВЫДАТЬ АЛМАЗЫ", callback_data="admin_give_diamonds"))
    builder.row(InlineKeyboardButton(text="⭐ ВЫДАТЬ PREMIUM", callback_data="admin_give_premium"))
    builder.row(InlineKeyboardButton(text="🔞 ВЫДАТЬ ПРИВАТ 18+", callback_data="admin_give_private"))
    builder.row(InlineKeyboardButton(text="🚫 ЗАБАНИТЬ/РАЗБАНИТЬ", callback_data="admin_ban"))
    builder.row(InlineKeyboardButton(text="🎟 ПРОМОКОДЫ", callback_data="admin_promocodes"))
    builder.row(InlineKeyboardButton(text="🏷 СКИДКИ", callback_data="admin_discounts"))
    builder.row(InlineKeyboardButton(text="💳 АКТИВНЫЕ ПОПОЛНЕНИЯ", callback_data="admin_active_payments"))
    builder.row(InlineKeyboardButton(text="📊 СТАТИСТИКА", callback_data="admin_stats"))
    builder.row(InlineKeyboardButton(text="📢 РАССЫЛКА", callback_data="admin_broadcast"))
    builder.row(InlineKeyboardButton(text="🪞 ЗЕРКАЛА", callback_data="admin_mirror"))
    builder.row(InlineKeyboardButton(text="◀️ ВЫХОД", callback_data="exit_admin"))
    return builder.as_markup()

def get_admin_payment_keyboard(user_id: int, payment_id: str, amount_rub: int, type_payment: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🚫 ЗАБАНИТЬ", callback_data=f"admin_ban_user_{user_id}"))
    builder.row(InlineKeyboardButton(text="✅ ВЫДАТЬ", callback_data=f"admin_approve_{payment_id}_{user_id}_{type_payment}"))
    builder.row(InlineKeyboardButton(text="❌ ОТКАЗАНО", callback_data=f"admin_reject_{payment_id}_{user_id}"))
    return builder.as_markup()

def get_tiktok_admin_keyboard(user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✅ ПРИНЯТО (12💎)", callback_data=f"tiktok_accept_{user_id}"))
    builder.row(InlineKeyboardButton(text="❌ ОТКЛОНИТЬ", callback_data=f"tiktok_reject_{user_id}"))
    return builder.as_markup()

# ---------- FSM ----------
class AdminStates(StatesGroup):
    waiting_for_content_file = State()
    waiting_for_more_videos = State()
    waiting_for_content_type = State()
    waiting_for_diamonds_user = State()
    waiting_for_diamonds_amount = State()
    waiting_for_premium_user = State()
    waiting_for_premium_days = State()
    waiting_for_private_user = State()
    waiting_for_private_days = State()
    waiting_for_ban_user = State()
    waiting_for_promo_code = State()
    waiting_for_promo_reward = State()
    waiting_for_promo_uses = State()
    waiting_for_discount_type = State()
    waiting_for_discount_percent = State()
    waiting_for_discount_days = State()
    waiting_for_discount_product_id = State()
    waiting_for_broadcast = State()

class PromoState(StatesGroup):
    waiting_for_code = State()

class PaymentState(StatesGroup):
    waiting_for_screenshot = State()

class TikTokState(StatesGroup):
    waiting_for_screenshots = State()

class TokenState(StatesGroup):
    waiting_for_token = State()

# ---------- ГЛАВНОЕ МЕНЮ ----------
async def show_main_menu(message: Message, user_id: int):
    user = get_user(user_id)
    if not user:
        await message.answer("Ошибка: пользователь не найден. Нажмите /start")
        return
    premium_active = user['is_premium'] and user['premium_until'] > int(datetime.now().timestamp())
    premium_status = "✅ ДА" if premium_active else "❌ НЕТ"
    private_active = user['is_private'] and user['private_until'] > int(datetime.now().timestamp())
    private_status = "✅ ДА" if private_active else "❌ НЕТ"
    text = (
        f"🔞 ВНИМАНИЕ! ТОВАР 18+ 🔞\n\n"
        f"👋 ПРИВЕТ, {user['username'] or 'гость'}!\n\n"
        f"🏠 ГЛАВНОЕ МЕНЮ\n\n"
        f"💎 АЛМАЗЫ: {user['balance']}\n"
        f"⭐ PREMIUM: {premium_status}\n"
        f"🔞 ПРИВАТ 18+: {private_status}\n\n"
        f"📢 НАШ КАНАЛ: {', '.join(REQUIRED_CHANNELS)}\n"
        f"👨‍💼 АДМИН: @scam_lil"
    )
    kb = get_main_keyboard(user_id)
    photo_id = await get_main_menu_photo()
    if photo_id:
        await message.answer_photo(photo=photo_id, caption=text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)

# ---------- КОМАНДА СТАРТ ----------
@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username or "radion"
    referrer_id = 0
    if len(message.text.split()) > 1:
        arg = message.text.split()[1]
        if arg.startswith("ref_"):
            try:
                referrer_id = int(arg.split("_")[1])
            except:
                pass
    if referrer_id == user_id:
        referrer_id = 0
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if not c.fetchone():
        payment_id = generate_unique_payment_id()
        c.execute("INSERT INTO users (user_id, username, payment_id, referrer_id, next_bonus_time) VALUES (?, ?, ?, ?, ?)",
                  (user_id, username, payment_id, referrer_id, int(datetime.now().timestamp()) + 86400))
        if referrer_id != 0:
            c.execute("SELECT banned FROM users WHERE user_id = ?", (referrer_id,))
            ref = c.fetchone()
            if ref and not ref[0]:
                c.execute("UPDATE users SET balance = balance + 1, referrals_count = referrals_count + 1 WHERE user_id = ?", (referrer_id,))
                c.execute("INSERT INTO diamond_logs (user_id, amount, reason, timestamp) VALUES (?, ?, ?, ?)",
                          (referrer_id, 1, f"Реферал {user_id}", int(datetime.now().timestamp())))
        conn.commit()
    conn.close()
    user = get_user(user_id)
    if user['banned']:
        await message.answer("❌ ВЫ ЗАБЛОКИРОВАНЫ В БОТЕ.")
        return
    await show_main_menu(message, user_id)

# ---------- ОБНОВИТЬ ----------
@dp.message(lambda m: m.text == "🔄 Обновить")
async def refresh_main_menu(message: Message, state: FSMContext):
    await state.clear()
    await show_main_menu(message, message.from_user.id)

# ---------- ПРОВЕРКА ПОДПИСКИ ----------
@dp.callback_query(F.data == "check_sub")
async def check_subscription(callback: CallbackQuery):
    user_id = callback.from_user.id
    if await is_subscribed(user_id):
        await callback.message.delete()
        await show_main_menu(callback.message, user_id)
    else:
        await callback.answer("❌ Вы не подписаны на все необходимые каналы! Подпишитесь и нажмите снова.", show_alert=True)

# ---------- СМОТРЕТЬ ВИДЕО ----------
@dp.message(lambda m: m.text == "🎬 Смотреть видео")
async def watch_content_command(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    # Проверяем автобонус перед действием
    if check_and_give_bonus(user_id):
        await message.answer("🎁 Вам начислен ежедневный бонус! Проверьте баланс.")
    user = get_user(user_id)
    if not user or user['banned']:
        await message.answer("❌ Доступ запрещён.")
        return
    if not await is_subscribed(user_id):
        await message.answer(
            "⚠️ ДЛЯ ПРОСМОТРА КОНТЕНТА ПОДПИШИТЕСЬ НА ВСЕ КАНАЛЫ:\n" + "\n".join(REQUIRED_CHANNELS) + "\n\n"
            "После подписки нажмите кнопку ниже 👇",
            reply_markup=get_subscription_keyboard()
        )
        return
    content = get_random_content(media_type='video')
    if not content:
        await message.answer("❌ Видео пока нет в базе. Администратор добавит позже.")
        return
    if content['is_vip'] == 1:
        premium_active = user['is_premium'] and user['premium_until'] > int(datetime.now().timestamp())
        if not premium_active:
            await message.answer("❌ Это VIP-видео. Оформите PREMIUM подписку!")
            return
    if user['balance'] < VIDEO_PRICE:
        await message.answer(f"❌ Недостаточно алмазов! Нужно {VIDEO_PRICE}💎. Пополните баланс через «Купить алмазы».")
        return
    update_balance(user_id, -VIDEO_PRICE)
    add_purchase(user_id, content['id'])
    await message.answer_video(content['file_id'], caption=f"🎬 {content['name']}\n\nСписано {VIDEO_PRICE}💎")

# ---------- КУПИТЬ АЛМАЗЫ ----------
@dp.message(lambda m: m.text == "💎 Купить алмазы")
async def buy_diamonds_command(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    # Проверяем автобонус перед действием
    if check_and_give_bonus(user_id):
        await message.answer("🎁 Вам начислен ежедневный бонус! Проверьте баланс.")
    user = get_user(user_id)
    if not user or user['banned']:
        await message.answer("❌ Доступ запрещён.")
        return
    await message.answer("💎 ВЫБЕРИТЕ КОЛИЧЕСТВО АЛМАЗОВ:", reply_markup=get_diamond_packs_keyboard())

@dp.callback_query(F.data.startswith("buy_diamonds_"))
async def buy_diamonds_pack(callback: CallbackQuery):
    diamonds = int(callback.data.split("_")[2])
    price = DIAMOND_PACKS[diamonds]
    discount = get_discount("diamonds")
    if discount:
        price = price * (100 - discount) // 100
    user_id = callback.from_user.id
    user = get_user(user_id)
    payment_id = user['payment_id']
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO pending_payments (user_id, payment_id, amount_rub, type, diamonds, timestamp, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
              (user_id, payment_id, price, 'diamonds', diamonds, int(datetime.now().timestamp()), 'pending'))
    conn.commit()
    conn.close()
    text = (f"🔞 ВНИМАНИЕ! ТОВАР 18+ 🔞\n\n💎 АЛМАЗЫ: {diamonds} шт\n💰 ЦЕНА: {price} ₽\n\n"
            f"💳 РЕКВИЗИТЫ ДЛЯ ОПЛАТЫ:\n{PAYMENT_DETAILS['card']} | {PAYMENT_DETAILS['name']}\n\n"
            f"🆔 ВАШ ID ДЛЯ ОПЛАТЫ: {payment_id}\n📌 ВСТАВЬТЕ ЭТОТ ID В КОММЕНТАРИЙ К ПЕРЕВОДУ!\n\n"
            f"📌 ПОСЛЕ ОПЛАТЫ НАЖМИТЕ «✅ Я ОПЛАТИЛ» И ОТПРАВЬТЕ ЧЕК")
    await callback.message.edit_text(text, reply_markup=get_payment_keyboard(payment_id))
    await callback.answer()

# ---------- ПРЕМИУМ ----------
@dp.message(lambda m: m.text == "⭐ Премиум")
async def premium_command(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    if check_and_give_bonus(user_id):
        await message.answer("🎁 Вам начислен ежедневный бонус! Проверьте баланс.")
    user = get_user(user_id)
    if not user or user['banned']:
        await message.answer("❌ Доступ запрещён.")
        return
    payment_id = user['payment_id']
    discount = get_discount("premium")
    price = PREMIUM_PRICE_RUB
    if discount:
        price = price * (100 - discount) // 100
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO pending_payments (user_id, payment_id, amount_rub, type, diamonds, timestamp, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
              (user_id, payment_id, price, 'premium', 0, int(datetime.now().timestamp()), 'pending'))
    conn.commit()
    conn.close()
    text = (f"⭐ PREMIUM ПОДПИСКА (30 ДНЕЙ)\n\n▫️ ДОСТУП К VIP-КОНТЕНТУ В БОТЕ\n▫️ 5 VIP ВИДЕО КАЖДЫЙ ДЕНЬ\n"
            f"▫️ ЕЖЕДНЕВНЫЙ БОНУС {DAILY_BONUS_PREMIUM}💎\n▫️ ПРИОРИТЕТНАЯ ПОДДЕРЖКА\n\n"
            f"💰 ЦЕНА: {price} ₽\n\n💳 РЕКВИЗИТЫ ДЛЯ ОПЛАТЫ:\n{PAYMENT_DETAILS['card']} | {PAYMENT_DETAILS['name']}\n\n"
            f"🆔 ВАШ ID ДЛЯ ОПЛАТЫ: {payment_id}\n📌 ВСТАВЬТЕ ЭТОТ ID В КОММЕНТАРИЙ К ПЕРЕВОДУ!\n\n"
            f"📌 ПОСЛЕ ОПЛАТЫ НАЖМИТЕ «✅ Я ОПЛАТИЛ» И ОТПРАВЬТЕ ЧЕК")
    await message.answer(text, reply_markup=get_payment_keyboard(payment_id))

# ---------- ПРИВАТ 18+ ----------
@dp.message(lambda m: m.text == "🔞 ПРИВАТ 18+")
async def private_command(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    if check_and_give_bonus(user_id):
        await message.answer("🎁 Вам начислен ежедневный бонус! Проверьте баланс.")
    user = get_user(user_id)
    if not user or user['banned']:
        await message.answer("❌ Доступ запрещён.")
        return
    payment_id = user['payment_id']
    discount = get_discount("private")
    price = PRIVATE_PRICE_RUB
    if discount:
        price = price * (100 - discount) // 100
    # Отправляем выбор оплаты: рубли или звёзды
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Оплатить рублями", callback_data="pay_private_rub")],
        [InlineKeyboardButton(text="⭐ Оплатить звёздами (напишите @scam_lil)", callback_data="pay_private_stars")],
        [InlineKeyboardButton(text="◀️ НАЗАД", callback_data="main_menu")]
    ])
    text = (f"🔞 ПРИВАТ 18+ (30 ДНЕЙ)\n\n"
            f"▫️ ДОСТУП К ЭКСКЛЮЗИВНОМУ КОНТЕНТУ 18+\n"
            f"▫️ ПРИОРИТЕТНАЯ ПОДДЕРЖКА\n\n"
            f"💰 ЦЕНА ЗА РУБЛИ: {price} ₽\n"
            f"⭐ ЦЕНА ЗА ЗВЁЗДЫ: {PRIVATE_PRICE_STARS} ⭐ (для оплаты звёздами напишите @scam_lil)\n\n"
            f"Выберите способ оплаты:")
    await message.answer(text, reply_markup=kb)

@dp.callback_query(F.data == "pay_private_rub")
async def pay_private_rub(callback: CallbackQuery):
    user_id = callback.from_user.id
    user = get_user(user_id)
    payment_id = user['payment_id']
    discount = get_discount("private")
    price = PRIVATE_PRICE_RUB
    if discount:
        price = price * (100 - discount) // 100
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO pending_payments (user_id, payment_id, amount_rub, type, diamonds, timestamp, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
              (user_id, payment_id, price, 'private', 0, int(datetime.now().timestamp()), 'pending'))
    conn.commit()
    conn.close()
    text = (f"🔞 ПРИВАТ 18+ (30 ДНЕЙ)\n\n"
            f"💰 ЦЕНА: {price} ₽\n\n"
            f"💳 РЕКВИЗИТЫ ДЛЯ ОПЛАТЫ:\n{PAYMENT_DETAILS['card']} | {PAYMENT_DETAILS['name']}\n\n"
            f"🆔 ВАШ ID ДЛЯ ОПЛАТЫ: {payment_id}\n📌 ВСТАВЬТЕ ЭТОТ ID В КОММЕНТАРИЙ К ПЕРЕВОДУ!\n\n"
            f"📌 ПОСЛЕ ОПЛАТЫ НАЖМИТЕ «✅ Я ОПЛАТИЛ» И ОТПРАВЬТЕ ЧЕК")
    await callback.message.edit_text(text, reply_markup=get_payment_keyboard(payment_id))
    await callback.answer()

@dp.callback_query(F.data == "pay_private_stars")
async def pay_private_stars(callback: CallbackQuery):
    await callback.answer("Напишите @scam_lil для оплаты звёздами.", show_alert=True)
    await callback.message.delete()
    await callback.message.answer("🔞 Для оплаты звёздами напишите администратору: @scam_lil")

# ---------- ПРОФИЛЬ ----------
@dp.message(lambda m: m.text == "👤 Профиль")
async def profile_command(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    if check_and_give_bonus(user_id):
        await message.answer("🎁 Вам начислен ежедневный бонус! Проверьте баланс.")
    user = get_user(user_id)
    if not user or user['banned']:
        await message.answer("❌ Доступ запрещён.")
        return
    premium_active = user['is_premium'] and user['premium_until'] > int(datetime.now().timestamp())
    premium_text = "✅ ДА" if premium_active else "❌ НЕТ"
    if premium_active:
        until = datetime.fromtimestamp(user['premium_until']).strftime("%d.%m.%Y")
        premium_text += f" (до {until})"
    private_active = user['is_private'] and user['private_until'] > int(datetime.now().timestamp())
    private_text = "✅ ДА" if private_active else "❌ НЕТ"
    if private_active:
        until = datetime.fromtimestamp(user['private_until']).strftime("%d.%m.%Y")
        private_text += f" (до {until})"
    text = (f"👤 ПРОФИЛЬ\n\n🆔 ID: {user_id}\n💎 АЛМАЗЫ: {user['balance']}\n⭐ PREMIUM: {premium_text}\n🔞 ПРИВАТ 18+: {private_text}\n🔗 ВАШ ID ДЛЯ ОПЛАТЫ: {user['payment_id']}")
    await message.answer(text)

# ---------- ПРОМОКОД ----------
@dp.message(lambda m: m.text == "🎫 Промокод")
async def promocode_start(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    if check_and_give_bonus(user_id):
        await message.answer("🎁 Вам начислен ежедневный бонус! Проверьте баланс.")
    await state.set_state(PromoState.waiting_for_code)
    await message.answer("Введите промокод:")

@dp.message(PromoState.waiting_for_code)
async def promocode_apply(message: Message, state: FSMContext):
    user_id = message.from_user.id
    code = message.text.strip().upper()
    success, reward = apply_promocode(user_id, code)
    if success:
        await message.answer(f"✅ Промокод активирован! +{reward}💎")
    else:
        await message.answer("❌ Неверный или уже использованный промокод.")
    await state.clear()
    await show_main_menu(message, user_id)

# ---------- ПОДДЕРЖКА ----------
@dp.message(lambda m: m.text == "📞 Поддержка")
async def support_command(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    if check_and_give_bonus(user_id):
        await message.answer("🎁 Вам начислен ежедневный бонус! Проверьте баланс.")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO support_messages (user_id, message, created_at, status) VALUES (?, ?, ?, ?)",
              (message.from_user.id, message.text, int(datetime.now().timestamp()), 'pending'))
    conn.commit()
    conn.close()
    await message.answer("📞 Связь с администратором: @scam_lil\nВаше сообщение сохранено, администратор ответит в ближайшее время.")

# ---------- ЗАРАБОТОК ----------
@dp.message(lambda m: m.text == "💰 Заработать")
async def earn_menu(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    if check_and_give_bonus(user_id):
        await message.answer("🎁 Вам начислен ежедневный бонус! Проверьте баланс.")
    user = get_user(user_id)
    if not user or user['banned']:
        await message.answer("❌ Доступ запрещён.")
        return
    text = "💰 **СПОСОБЫ ЗАРАБОТКА АЛМАЗОВ**\n\nВыберите один из вариантов:"
    await message.answer(text, reply_markup=get_earn_keyboard(), parse_mode="Markdown")

def get_referral_link(bot_username: str, user_id: int) -> str:
    return f"https://t.me/{bot_username}?start=ref_{user_id}"

@dp.callback_query(F.data == "earn_ref")
async def earn_ref(callback: CallbackQuery):
    user_id = callback.from_user.id
    user = get_user(user_id)
    if not user:
        await callback.answer("Ошибка", show_alert=True)
        return
    if not BOT_USERNAME:
        await get_bot_username()
    link = get_referral_link(BOT_USERNAME, user_id)
    text = (
        f"👥 **РЕФЕРАЛЬНАЯ СИСТЕМА**\n\n"
        f"👤 ВАШИ РЕФЕРАЛЫ: {user['referrals_count']}\n"
        f"💰 ЗА КАЖДОГО ПРИГЛАШЕННОГО: +1💎\n\n"
        f"🔗 ВАША ССЫЛКА:\n`{link}`\n\n"
        f"📤 ПРОСТО ПЕРЕШЛИТЕ ЭТУ ССЫЛКУ ДРУЗЬЯМ!"
    )
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ НАЗАД", callback_data="back_to_earn")]]), parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "earn_tiktok")
async def earn_tiktok(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    can, remain = can_submit_tiktok(user_id)
    if not can:
        hours = remain // 3600
        mins = (remain % 3600) // 60
        await callback.answer(f"Вы уже выполняли задание сегодня. Повторно через {hours}ч {mins}мин.", show_alert=True)
        return
    text = (
        "📸 **ЗАДАНИЕ TIKTOK**\n\n"
        "1️⃣ В ПОИСКЕ TIKTOK НАПИШИТЕ: **детское питание**\n"
        "2️⃣ ПОД 10 ВИДЕО НАПИШИТЕ КОММЕНТАРИЙ:\n"
        "🔞 `@RadionShop_bot не повторимый` 🔞\n"
        "3️⃣ ОБЯЗАТЕЛЬНО ПОСТАВЬТЕ ЛАЙК НА СВОЙ КОММЕНТАРИЙ\n"
        "4️⃣ СДЕЛАЙТЕ 10 СКРИНШОТОВ (ЛАЙК ДОЛЖЕН БЫТЬ ВИДЕН)\n"
        "5️⃣ ОТПРАВЬТЕ ВСЕ 10 СКРИНШОТОВ **ПО ОДНОМУ** В БОТА\n\n"
        "💰 НАГРАДА: 12💎\n"
        "⏰ МОЖНО ВЫПОЛНЯТЬ 1 РАЗ В ДЕНЬ"
    )
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📤 ОТПРАВИТЬ СКРИНЫ", callback_data="tiktok_send"), InlineKeyboardButton(text="◀️ НАЗАД", callback_data="back_to_earn")]]), parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "tiktok_send")
async def tiktok_send_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(TikTokState.waiting_for_screenshots)
    await state.update_data(photos=[])
    await callback.message.edit_text("📸 Отправьте **10 скриншотов** по одному. Я соберу их.\nПосле получения 10-го фото они будут отправлены администратору на проверку.")
    await callback.answer()

@dp.message(TikTokState.waiting_for_screenshots, F.photo)
async def tiktok_collect_photos(message: Message, state: FSMContext):
    data = await state.get_data()
    photos = data.get("photos", [])
    photos.append(message.photo[-1].file_id)
    await state.update_data(photos=photos)
    count = len(photos)
    if count < 10:
        await message.answer(f"📸 Получен {count}/10 скриншот. Отправьте следующий.")
    else:
        admin_id = ADMIN_IDS[0]
        media_group = []
        for i, fid in enumerate(photos):
            if i == 0:
                caption = f"📸 Новая заявка TikTok от {message.from_user.id} (@{message.from_user.username})"
                media_group.append(InputMediaPhoto(media=fid, caption=caption))
            else:
                media_group.append(InputMediaPhoto(media=fid))
        await bot.send_media_group(admin_id, media=media_group)
        await bot.send_message(admin_id, "Проверьте скриншоты и примите/отклоните задание.", reply_markup=get_tiktok_admin_keyboard(message.from_user.id))
        await message.answer("✅ Все 10 скриншотов получены и отправлены администратору. Ожидайте начисления алмазов.")
        await state.clear()

@dp.callback_query(F.data.startswith("tiktok_accept_"))
async def tiktok_accept(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    user_id = int(callback.data.split("_")[2])
    update_balance(user_id, 12)
    set_tiktok_completed(user_id)
    await callback.message.edit_text(f"✅ Задание TikTok принято! Пользователю {user_id} начислено 12💎.")
    await bot.send_message(user_id, "✅ Ваше задание TikTok принято! Вам начислено 12💎.")
    await callback.answer()

@dp.callback_query(F.data.startswith("tiktok_reject_"))
async def tiktok_reject(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    user_id = int(callback.data.split("_")[2])
    await callback.message.edit_text(f"❌ Задание TikTok отклонено для пользователя {user_id}.")
    await bot.send_message(user_id, "❌ Ваше задание TikTok отклонено. Попробуйте выполнить заново.")
    await callback.answer()

@dp.callback_query(F.data == "earn_token")
async def earn_token(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    can, remain = can_submit_token(user_id)
    if not can:
        hours = remain // 3600
        mins = (remain % 3600) // 60
        await callback.answer(f"Вы уже отправляли токен сегодня. Повторно через {hours}ч {mins}мин.", show_alert=True)
        return
    text = (
        f"🤖 **ПОЛУЧИ АЛМАЗЫ ЗА ТОКЕН БОТА!**\n\n"
        f"1️⃣ ПЕРЕЙДИ К @BotFather\n"
        f"2️⃣ ОТПРАВЬ КОМАНДУ /newbot\n"
        f"3️⃣ ПРИДУМАЙ ИМЯ И USERNAME (ДОЛЖЕН ЗАКАНЧИВАТЬСЯ НА 'bot')\n"
        f"4️⃣ СКОПИРУЙ ПОЛУЧЕННЫЙ ТОКЕН\n"
        f"5️⃣ ОТПРАВЬ ТОКЕН СЮДА\n\n"
        f"💰 **НАГРАДА: +{TOKEN_REWARD}💎 ЗА КАЖДЫЙ РАБОЧИЙ ТОКЕН**\n"
        f"⏰ **МОЖНО 1 РАЗ В ДЕНЬ**\n\n"
        f"📌 ТОКЕНЫ АВТОМАТИЧЕСКИ ПРОВЕРЯЮТСЯ!"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔑 ПЕРЕЙТИ К @BotFather", url="https://t.me/BotFather")],
        [InlineKeyboardButton(text="❌ ОТМЕНА", callback_data="back_to_earn")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    await state.set_state(TokenState.waiting_for_token)
    await callback.answer()

@dp.message(TokenState.waiting_for_token)
async def token_submit(message: Message, state: FSMContext):
    user_id = message.from_user.id
    token = message.text.strip()
    can, remain = can_submit_token(user_id)
    if not can:
        hours = remain // 3600
        mins = (remain % 3600) // 60
        await message.answer(f"❌ Лимит: 1 токен в день. Повторите через {hours}ч {mins}мин.")
        await state.clear()
        return

    bot_username = None
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"https://api.telegram.org/bot{token}/getMe") as resp:
                data = await resp.json()
                if not data.get("ok"):
                    await message.answer("❌ Токен недействителен. Попробуйте другой.")
                    return
                bot_username = data["result"].get("username")
        except Exception:
            await message.answer("❌ Ошибка проверки токена. Попробуйте позже.")
            return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT 1 FROM user_tokens WHERE user_id = ? AND token = ?", (user_id, token))
    if c.fetchone():
        conn.close()
        await message.answer("❌ Вы уже использовали этот токен ранее.")
        return

    now = int(datetime.now().timestamp())
    c.execute("INSERT INTO user_tokens (user_id, token, submitted_at, bot_username) VALUES (?, ?, ?, ?)",
              (user_id, token, now, bot_username))
    conn.commit()
    conn.close()

    register_token_usage(user_id)
    update_balance(user_id, TOKEN_REWARD)

    reply = f"✅ Токен принят! Вам начислено +{TOKEN_REWARD}💎."
    if bot_username:
        reply += f"\n🤖 Юзернейм бота: @{bot_username}"
    await message.answer(reply)
    await state.clear()

@dp.callback_query(F.data == "back_to_earn")
async def back_to_earn(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await earn_menu(callback.message, state)
    await callback.answer()

@dp.callback_query(F.data == "cancel_earn")
async def cancel_earn(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await callback.answer()

# ---------- ОБРАБОТКА ПЛАТЕЖЕЙ (ручная) ----------
@dp.callback_query(F.data == "i_paid")
async def i_paid(callback: CallbackQuery, state: FSMContext):
    await state.set_state(PaymentState.waiting_for_screenshot)
    await state.update_data(user_id=callback.from_user.id)
    await callback.message.edit_text("📸 Отправьте скриншот чека (фото или файл). После проверки администратор начислит алмазы или подписку.")
    await callback.answer()

@dp.message(PaymentState.waiting_for_screenshot, F.photo | F.document)
async def receive_screenshot(message: Message, state: FSMContext):
    user_id = message.from_user.id
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT payment_id, amount_rub, type, diamonds FROM pending_payments WHERE user_id = ? AND status = 'pending' ORDER BY timestamp DESC LIMIT 1", (user_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        await message.answer("❌ Не найдена активная заявка на оплату. Начните сначала через меню покупки.")
        await state.clear()
        return
    payment_id, amount, pay_type, diamonds = row
    caption = f"📨 Новая заявка на оплату!\n👤 Пользователь: {user_id} (@{message.from_user.username})\n💰 Сумма: {amount}₽\n📦 Товар: {pay_type}\n🆔 Payment ID: {payment_id}"
    if pay_type == 'diamonds':
        caption += f"\n💎 Алмазов: {diamonds}"
    elif pay_type == 'premium':
        caption += f"\n⭐ Премиум подписка"
    elif pay_type == 'private':
        caption += f"\n🔞 ПРИВАТ 18+"
    for admin_id in ADMIN_IDS:
        if message.photo:
            await bot.send_photo(admin_id, message.photo[-1].file_id, caption=caption, reply_markup=get_admin_payment_keyboard(user_id, payment_id, amount, pay_type))
        elif message.document:
            await bot.send_document(admin_id, message.document.file_id, caption=caption, reply_markup=get_admin_payment_keyboard(user_id, payment_id, amount, pay_type))
    await message.answer("✅ Чек отправлен администратору. Ожидайте подтверждения.")
    await state.clear()

@dp.callback_query(F.data.startswith("admin_approve_"))
async def admin_approve(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    rest = callback.data[len("admin_approve_"):]
    parts2 = rest.split('_')
    payment_id = parts2[0] + '_' + parts2[1]
    user_id = int(parts2[2])
    pay_type = parts2[3]
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT diamonds, amount_rub FROM pending_payments WHERE payment_id = ? AND user_id = ? AND status = 'pending'", (payment_id, user_id))
    row = c.fetchone()
    if not row:
        await callback.answer("Заявка уже обработана или не найдена.", show_alert=True)
        return
    diamonds, amount = row
    if pay_type == 'diamonds':
        update_balance(user_id, diamonds)
        await bot.send_message(user_id, f"✅ Ваша оплата на {amount}₽ подтверждена! Вам начислено {diamonds}💎.")
    elif pay_type == 'premium':
        set_premium(user_id, 30)
        await bot.send_message(user_id, f"✅ Ваша оплата на {amount}₽ подтверждена! PREMIUM подписка активирована на 30 дней.")
    elif pay_type == 'private':
        set_private(user_id, 30)
        await bot.send_message(user_id, f"✅ Ваша оплата на {amount}₽ подтверждена! ДОСТУП К ПРИВАТ 18+ активирован на 30 дней.")
    c.execute("UPDATE pending_payments SET status = 'approved' WHERE payment_id = ? AND user_id = ?", (payment_id, user_id))
    conn.commit()
    conn.close()
    await callback.message.answer(f"✅ Выдано пользователю {user_id}.")
    await callback.answer()

@dp.callback_query(F.data.startswith("admin_reject_"))
async def admin_reject(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    rest = callback.data[len("admin_reject_"):]
    parts2 = rest.split('_')
    payment_id = parts2[0] + '_' + parts2[1]
    user_id = int(parts2[2])
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE pending_payments SET status = 'rejected' WHERE payment_id = ? AND user_id = ?", (payment_id, user_id))
    conn.commit()
    conn.close()
    try:
        await bot.send_message(user_id, "❌ Ваша оплата отклонена администратором. Проверьте правильность заполнения ID или свяжитесь с поддержкой.")
    except:
        pass
    await callback.message.answer(f"❌ Отказано пользователю {user_id}.")
    await callback.answer()

@dp.callback_query(F.data.startswith("admin_ban_user_"))
async def admin_ban_user(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    user_id = int(callback.data.split("_")[3])
    set_ban(user_id, True)
    await callback.message.answer(f"✅ Пользователь {user_id} забанен.")
    await callback.answer()
    try:
        await bot.send_message(user_id, "❌ Вы были забанены администратором за нарушение правил оплаты.")
    except:
        pass

@dp.callback_query(F.data.startswith("copy_id_"))
async def copy_payment_id(callback: CallbackQuery):
    payment_id = callback.data.split("_", 2)[2]
    await callback.answer(f"✅ ID скопирован: {payment_id}", show_alert=True)
    await callback.message.answer(f"🆔 Ваш ID для оплаты: `{payment_id}`", parse_mode="Markdown")

@dp.callback_query(F.data == "cancel")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("Действие отменено.")
    await callback.answer()

@dp.callback_query(F.data == "main_menu")
async def main_menu_callback(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await show_main_menu(callback.message, callback.from_user.id)
    await callback.answer()

# ---------- АДМИН ПАНЕЛЬ ----------
@dp.message(lambda m: m.text == "⚙️ Админ панель")
async def admin_panel_command(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        await message.answer("⛔ Нет доступа.")
        return
    await message.answer("⚙️ АДМИН ПАНЕЛЬ:", reply_markup=get_admin_keyboard())

# ---------- АКТИВНЫЕ ПОПОЛНЕНИЯ ----------
@dp.callback_query(F.data == "admin_active_payments")
async def admin_active_payments(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    payments = get_active_payments()
    if not payments:
        await callback.message.answer("📭 Нет активных заявок на пополнение.")
        await callback.answer()
        return
    text = "<b>💳 АКТИВНЫЕ ПОПОЛНЕНИЯ (незавершённые оплаты)</b>\n\n"
    for user_id, payment_id, amount, ptype, diamonds, ts in payments:
        date = datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")
        user = get_user(user_id)
        username = user['username'] if user else str(user_id)
        item = ""
        if ptype == 'diamonds':
            item = f"💎 {diamonds} алмазов"
        elif ptype == 'premium':
            item = "⭐ Премиум подписка"
        elif ptype == 'private':
            item = "🔞 ПРИВАТ 18+"
        text += f"👤 {username} (ID {user_id})\n"
        text += f"📦 {item} – {amount}₽\n"
        text += f"🆔 ID оплаты: {payment_id}\n"
        text += f"📅 {date}\n\n"
        if len(text) > 3800:
            await callback.message.answer(text, parse_mode="HTML")
            text = ""
    if text:
        await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()

# ---------- ОСТАЛЬНЫЕ АДМИН ХЭНДЛЕРЫ ----------
# Добавление контента (несколько видео)
@dp.callback_query(F.data == "admin_add_content")
async def admin_add_content_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminStates.waiting_for_content_file)
    await state.update_data(videos=[])
    await callback.message.answer("📹 Отправьте видео (можно несколько по одному). После каждого видео я спрошу, нужно ли добавить ещё.\nДля завершения нажмите «Закончить».")
    await callback.answer()

@dp.message(AdminStates.waiting_for_content_file, F.video)
async def admin_get_video_file(message: Message, state: FSMContext):
    data = await state.get_data()
    videos = data.get("videos", [])
    videos.append(message.video.file_id)
    await state.update_data(videos=videos)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ ДОБАВИТЬ ЕЩЁ", callback_data="add_more_video")],
        [InlineKeyboardButton(text="✅ ЗАКОНЧИТЬ", callback_data="finish_videos")]
    ])
    await message.answer(f"📹 Видео #{len(videos)} получено. Что дальше?", reply_markup=kb)

@dp.callback_query(F.data == "add_more_video")
async def add_more_video(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("📹 Отправьте следующее видео (или нажмите «Закончить»).")
    await callback.answer()

@dp.callback_query(F.data == "finish_videos")
async def finish_videos(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    videos = data.get("videos", [])
    if not videos:
        await callback.message.answer("Нет видео для сохранения.")
        await state.clear()
        return
    await state.update_data(videos_to_save=videos)
    await state.set_state(AdminStates.waiting_for_content_type)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📀 ОБЫЧНЫЕ", callback_data="type_normal_all")],
        [InlineKeyboardButton(text="👑 VIP", callback_data="type_vip_all")]
    ])
    await callback.message.edit_text(f"Получено {len(videos)} видео. Выберите тип для всех:", reply_markup=kb)
    await callback.answer()

@dp.callback_query(StateFilter(AdminStates.waiting_for_content_type), F.data.startswith("type_"))
async def admin_set_vip_for_all(callback: CallbackQuery, state: FSMContext):
    is_vip = 1 if callback.data == "type_vip_all" else 0
    data = await state.get_data()
    videos = data.get("videos_to_save", [])
    for file_id in videos:
        add_content(file_id, "video", is_vip)
    await state.clear()
    await callback.message.answer(f"✅ Добавлено {len(videos)} видео.")
    await callback.answer()

# Удаление контента
@dp.callback_query(F.data == "admin_del_content")
async def admin_del_content_start(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    items = get_all_content()
    if not items:
        await callback.message.answer("Нет контента для удаления")
        return
    builder = InlineKeyboardBuilder()
    for item in items:
        builder.row(InlineKeyboardButton(text=f"{item['name']} ({item['media_type']})", callback_data=f"del_content_{item['id']}"))
    builder.row(InlineKeyboardButton(text="◀️ НАЗАД", callback_data="exit_admin"))
    await callback.message.answer("Выберите контент для удаления:", reply_markup=builder.as_markup())
    await callback.answer()

@dp.callback_query(F.data.startswith("del_content_"))
async def admin_del_content_confirm(callback: CallbackQuery):
    content_id = int(callback.data.split("_")[2])
    remove_content(content_id)
    await callback.message.answer("🗑 Контент удалён")
    await callback.answer()

# Выдача алмазов
@dp.callback_query(F.data == "admin_give_diamonds")
async def admin_give_diamonds_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminStates.waiting_for_diamonds_user)
    await callback.message.answer("Введите ID пользователя:")
    await callback.answer()

@dp.message(AdminStates.waiting_for_diamonds_user)
async def admin_give_diamonds_user(message: Message, state: FSMContext):
    try:
        user_id = int(message.text)
    except:
        await message.answer("Ошибка: введите число (ID)")
        return
    if not get_user(user_id):
        await message.answer("Пользователь не найден")
        return
    await state.update_data(target_user=user_id)
    await state.set_state(AdminStates.waiting_for_diamonds_amount)
    await message.answer("Введите количество алмазов:")

@dp.message(AdminStates.waiting_for_diamonds_amount)
async def admin_give_diamonds_amount(message: Message, state: FSMContext):
    try:
        amount = int(message.text)
    except:
        await message.answer("Введите число")
        return
    data = await state.get_data()
    user_id = data['target_user']
    update_balance(user_id, amount)
    await state.clear()
    await message.answer(f"✅ Пользователю {user_id} выдано {amount}💎")

# Выдача PREMIUM
@dp.callback_query(F.data == "admin_give_premium")
async def admin_give_premium_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminStates.waiting_for_premium_user)
    await callback.message.answer("Введите ID пользователя:")
    await callback.answer()

@dp.message(AdminStates.waiting_for_premium_user)
async def admin_give_premium_user(message: Message, state: FSMContext):
    try:
        user_id = int(message.text)
    except:
        await message.answer("Ошибка: введите число")
        return
    if not get_user(user_id):
        await message.answer("Пользователь не найден")
        return
    await state.update_data(target_user=user_id)
    await state.set_state(AdminStates.waiting_for_premium_days)
    await message.answer("Введите количество дней премиума:")

@dp.message(AdminStates.waiting_for_premium_days)
async def admin_give_premium_days(message: Message, state: FSMContext):
    try:
        days = int(message.text)
    except:
        await message.answer("Введите число")
        return
    data = await state.get_data()
    user_id = data['target_user']
    set_premium(user_id, days)
    await state.clear()
    await message.answer(f"✅ Пользователю {user_id} выдан PREMIUM на {days} дней")

# Выдача ПРИВАТ 18+
@dp.callback_query(F.data == "admin_give_private")
async def admin_give_private_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminStates.waiting_for_private_user)
    await callback.message.answer("Введите ID пользователя:")
    await callback.answer()

@dp.message(AdminStates.waiting_for_private_user)
async def admin_give_private_user(message: Message, state: FSMContext):
    try:
        user_id = int(message.text)
    except:
        await message.answer("Ошибка: введите число")
        return
    if not get_user(user_id):
        await message.answer("Пользователь не найден")
        return
    await state.update_data(target_user=user_id)
    await state.set_state(AdminStates.waiting_for_private_days)
    await message.answer("Введите количество дней доступа к ПРИВАТ 18+:")

@dp.message(AdminStates.waiting_for_private_days)
async def admin_give_private_days(message: Message, state: FSMContext):
    try:
        days = int(message.text)
    except:
        await message.answer("Введите число")
        return
    data = await state.get_data()
    user_id = data['target_user']
    set_private(user_id, days)
    await state.clear()
    await message.answer(f"✅ Пользователю {user_id} выдан доступ к ПРИВАТ 18+ на {days} дней")

# Бан/разбан
@dp.callback_query(F.data == "admin_ban")
async def admin_ban_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminStates.waiting_for_ban_user)
    await callback.message.answer("Введите ID пользователя для бана/разбана:")
    await callback.answer()

@dp.message(AdminStates.waiting_for_ban_user)
async def admin_ban_user_general(message: Message, state: FSMContext):
    try:
        user_id = int(message.text)
    except:
        await message.answer("Ошибка: введите число")
        return
    user = get_user(user_id)
    if not user:
        await message.answer("Пользователь не найден")
        return
    new_ban = not user['banned']
    set_ban(user_id, new_ban)
    status = "забанен" if new_ban else "разбанен"
    await message.answer(f"✅ Пользователь {user_id} {status}")
    await state.clear()

# Промокоды (админ)
@dp.callback_query(F.data == "admin_promocodes")
async def admin_promocodes_menu(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    promos = get_all_promocodes()
    text = "🎟 СПИСОК ПРОМОКОДОВ:\n\n"
    if not promos:
        text += "Нет промокодов"
    else:
        for code, reward, max_uses, used in promos:
            text += f"▪️ `{code}` → +{reward}💎, лимит {max_uses}, активаций {used}\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ ДОБАВИТЬ", callback_data="admin_add_promo")],
        [InlineKeyboardButton(text="❌ УДАЛИТЬ", callback_data="admin_remove_promo")],
        [InlineKeyboardButton(text="◀️ НАЗАД", callback_data="exit_admin")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "admin_add_promo")
async def admin_add_promo_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminStates.waiting_for_promo_code)
    await callback.message.edit_text("Введите код промокода (латиница/цифры):")
    await callback.answer()

@dp.message(AdminStates.waiting_for_promo_code)
async def admin_add_promo_code(message: Message, state: FSMContext):
    await state.update_data(promo_code=message.text.strip().upper())
    await state.set_state(AdminStates.waiting_for_promo_reward)
    await message.answer("Введите награду (алмазы):")

@dp.message(AdminStates.waiting_for_promo_reward)
async def admin_add_promo_reward(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Введите число")
        return
    await state.update_data(promo_reward=int(message.text))
    await state.set_state(AdminStates.waiting_for_promo_uses)
    await message.answer("Введите лимит активаций (0 = безлимит):")

@dp.message(AdminStates.waiting_for_promo_uses)
async def admin_add_promo_uses(message: Message, state: FSMContext):
    max_uses = int(message.text) if message.text.isdigit() else 0
    data = await state.get_data()
    add_promocode(data["promo_code"], data["promo_reward"], max_uses)
    await state.clear()
    await message.answer("✅ Промокод добавлен")

@dp.callback_query(F.data == "admin_remove_promo")
async def admin_remove_promo_start(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    promos = get_all_promocodes()
    if not promos:
        await callback.answer("Нет промокодов", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    for code, reward, max_uses, used in promos:
        builder.row(InlineKeyboardButton(text=f"{code} (+{reward}💎, ост.{max_uses-used}/{max_uses})", callback_data=f"rm_promo_{code}"))
    builder.row(InlineKeyboardButton(text="◀️ НАЗАД", callback_data="admin_promocodes"))
    await callback.message.edit_text("Выберите промокод для удаления:", reply_markup=builder.as_markup())
    await callback.answer()

@dp.callback_query(F.data.startswith("rm_promo_"))
async def admin_remove_promo(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    code = callback.data[len("rm_promo_"):]
    delete_promocode(code)
    await callback.message.answer(f"✅ Промокод `{code}` удалён", parse_mode="Markdown")
    await admin_promocodes_menu(callback)
    await callback.answer()

# Скидки
@dp.callback_query(F.data == "admin_discounts")
async def admin_discounts_menu(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    discounts = get_all_discounts()
    text = "🏷 АКТИВНЫЕ СКИДКИ:\n\n"
    if not discounts:
        text += "Нет активных скидок"
    else:
        for did, ptype, pid, percent, until in discounts:
            until_str = datetime.fromtimestamp(until).strftime("%d.%m.%Y")
            product = ptype
            if ptype == "diamonds":
                product = "💎 Алмазы"
            elif ptype == "premium":
                product = "⭐ Премиум подписка"
            elif ptype == "private":
                product = "🔞 ПРИВАТ 18+"
            elif ptype == "video":
                product = f"🎬 Видео #{pid}" if pid != 0 else "🎬 Все видео"
            text += f"• {product}: скидка {percent}% до {until_str}\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ ДОБАВИТЬ СКИДКУ", callback_data="admin_add_discount")],
        [InlineKeyboardButton(text="❌ УДАЛИТЬ СКИДКУ", callback_data="admin_remove_discount")],
        [InlineKeyboardButton(text="◀️ НАЗАД", callback_data="exit_admin")]
    ])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()

@dp.callback_query(F.data == "admin_add_discount")
async def admin_add_discount_type(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 АЛМАЗЫ", callback_data="discount_type_diamonds")],
        [InlineKeyboardButton(text="⭐ PREMIUM", callback_data="discount_type_premium")],
        [InlineKeyboardButton(text="🔞 ПРИВАТ 18+", callback_data="discount_type_private")],
        [InlineKeyboardButton(text="🎬 ВСЕ ВИДЕО", callback_data="discount_type_video_all")],
        [InlineKeyboardButton(text="◀️ НАЗАД", callback_data="admin_discounts")]
    ])
    await callback.message.edit_text("Выберите товар для скидки:", reply_markup=kb)
    await callback.answer()

@dp.callback_query(F.data.startswith("discount_type_"))
async def admin_discount_get_type(callback: CallbackQuery, state: FSMContext):
    product_type = callback.data.split("_")[2]
    if product_type == "video_all":
        product_type = "video"
        product_id = 0
    else:
        product_id = 0
    await state.update_data(discount_product_type=product_type, discount_product_id=product_id)
    await state.set_state(AdminStates.waiting_for_discount_percent)
    await callback.message.edit_text("Введите процент скидки (целое число от 1 до 99):")
    await callback.answer()

@dp.message(AdminStates.waiting_for_discount_percent)
async def admin_discount_percent(message: Message, state: FSMContext):
    try:
        percent = int(message.text)
        if percent < 1 or percent > 99:
            raise ValueError
    except:
        await message.answer("Ошибка: введите число от 1 до 99")
        return
    await state.update_data(discount_percent=percent)
    await state.set_state(AdminStates.waiting_for_discount_days)
    await message.answer("Введите количество дней действия скидки:")

@dp.message(AdminStates.waiting_for_discount_days)
async def admin_discount_days(message: Message, state: FSMContext):
    try:
        days = int(message.text)
        if days < 1:
            raise ValueError
    except:
        await message.answer("Ошибка: введите число больше 0")
        return
    data = await state.get_data()
    add_discount(data["discount_product_type"], data["discount_percent"], days, data["discount_product_id"])
    await state.clear()
    await message.answer("✅ Скидка добавлена!")
    await admin_discounts_menu(message)

@dp.callback_query(F.data == "admin_remove_discount")
async def admin_remove_discount_menu(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    discounts = get_all_discounts()
    if not discounts:
        await callback.answer("Нет активных скидок", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    for did, ptype, pid, percent, until in discounts:
        if ptype == "video" and pid == 0:
            label = f"Все видео - {percent}%"
        else:
            label = f"{ptype} - {percent}%"
        builder.row(InlineKeyboardButton(text=label, callback_data=f"rm_discount_{did}"))
    builder.row(InlineKeyboardButton(text="◀️ НАЗАД", callback_data="admin_discounts"))
    await callback.message.edit_text("Выберите скидку для удаления:", reply_markup=builder.as_markup())
    await callback.answer()

@dp.callback_query(F.data.startswith("rm_discount_"))
async def admin_remove_discount(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    did = int(callback.data.split("_")[2])
    remove_discount(did)
    await callback.message.answer("✅ Скидка удалена")
    await admin_discounts_menu(callback)
    await callback.answer()

# Статистика
@dp.callback_query(F.data == "admin_stats")
async def admin_stats_callback(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    total, banned, premium, private, videos, vip_videos, payments, tiktok, support = get_stats()
    text = (
        f"📊 СТАТИСТИКА БОТА @RadionShop_bot\n\n"
        f"👥 ПОЛЬЗОВАТЕЛИ:\n"
        f"• ВСЕГО: {total}\n"
        f"• ЗАБАНЕНЫ: {banned}\n"
        f"• PREMIUM: {premium}\n"
        f"• ПРИВАТ 18+: {private}\n\n"
        f"📹 КОНТЕНТ:\n"
        f"• ВИДЕО: {videos}\n"
        f"• VIP ВИДЕО: {vip_videos}\n\n"
        f"💰 ЗАПРОСОВ НА ОПЛАТУ: {payments}\n"
        f"📸 ЗАДАНИЙ TIKTOK: {tiktok}\n"
        f"📨 СООБЩЕНИЙ В ПОДДЕРЖКУ: {support}"
    )
    await callback.message.answer(text)
    await callback.answer()

# Рассылка
@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminStates.waiting_for_broadcast)
    await callback.message.answer("Введите текст рассылки (можно с фото/видео):")
    await callback.answer()

@dp.message(StateFilter(AdminStates.waiting_for_broadcast))
async def admin_broadcast_send(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE banned = 0")
    users = c.fetchall()
    conn.close()
    sent = 0
    for (uid,) in users:
        try:
            if message.text:
                await bot.send_message(uid, message.text)
            elif message.photo:
                await bot.send_photo(uid, message.photo[-1].file_id, caption=message.caption)
            elif message.video:
                await bot.send_video(uid, message.video.file_id, caption=message.caption)
            sent += 1
            await asyncio.sleep(0.05)
        except:
            pass
    await state.clear()
    await message.answer(f"📢 Рассылка завершена. Отправлено {sent} сообщений.")

# Зеркала
@dp.callback_query(F.data == "admin_mirror")
async def admin_mirror(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id, token, submitted_at, bot_username FROM user_tokens ORDER BY submitted_at DESC")
    rows = c.fetchall()
    conn.close()
    if not rows:
        await callback.message.answer("📭 Ещё никто не отправлял токены.")
        await callback.answer()
        return
    text = "<b>🪞 ЗЕРКАЛО (ЛОГ ТОКЕНОВ)</b>\n\n"
    for user_id, token, ts, bot_username in rows:
        date = datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")
        user = get_user(user_id)
        username = user['username'] if user else str(user_id)
        bot_uname = f"@{bot_username}" if bot_username else "—"
        text += f"👤 {username} (ID {user_id})\n"
        text += f"🔑 Токен: <code>{token}</code>\n"
        text += f"🤖 Бот: {bot_uname}\n"
        text += f"📅 {date}\n\n"
        if len(text) > 3800:
            await callback.message.answer(text, parse_mode="HTML")
            text = ""
    if text:
        await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()

# Выход
@dp.callback_query(F.data == "exit_admin")
async def exit_admin(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("Выход из админ-панели.")
    await callback.answer()

# ---------- ЗАПУСК ----------
async def main():
    await get_bot_username()
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
