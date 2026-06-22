import asyncio
import logging
import os
import random
import sqlite3
from datetime import datetime, timedelta

from aiogram import html
from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    LabeledPrice,
    PreCheckoutQuery,
)
from aiogram.filters.callback_data import CallbackData

# ================= НАСТРОЙКИ БОТА =================
from dotenv import load_dotenv

ENV_PATH = "/storage/emulated/0/asnos/gb/tu/.env"

if os.path.exists(ENV_PATH):
    load_dotenv(dotenv_path=ENV_PATH)
else:
    load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", 963968579))
except (ValueError, TypeError):
    ADMIN_ID = 963968579

CHANNEL_USERNAME = "@giftez_base"

# ================= ИНИЦИАЛИЗАЦИЯ =================
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ================= БАЗА ДАННЫХ =================
conn = sqlite3.connect("bot_databasetu.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    balance REAL DEFAULT 0.0,
    last_bonus TEXT,
    is_banned INTEGER DEFAULT 0,
    season_earned REAL DEFAULT 0.0,
    referrer_id INTEGER DEFAULT 0,
    channel_bonus_claimed INTEGER DEFAULT 0,
    case_common INTEGER DEFAULT 0,
    case_ref INTEGER DEFAULT 0,
    case_all_nothing INTEGER DEFAULT 0,
    cases_opened_for_refs INTEGER DEFAULT 0
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS withdraws (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    amount REAL,
    status TEXT DEFAULT 'pending',
    date TEXT
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS review_stars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    screenshot_id TEXT,
    status TEXT DEFAULT 'pending'
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS season_info (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    end_date TEXT
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS promocodes (
    code TEXT PRIMARY KEY,
    max_activations INTEGER,
    current_activations INTEGER DEFAULT 0,
    target_user_id INTEGER DEFAULT 0,
    type TEXT,
    reward_amount REAL DEFAULT 0.0,
    expires_at TEXT DEFAULT 'never',
    is_infinite_activations INTEGER DEFAULT 0,
    include_season INTEGER DEFAULT 1,
    case_type TEXT DEFAULT '',
    case_count INTEGER DEFAULT 0
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS promo_activations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT,
    user_id INTEGER
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS bot_admins (
    user_id INTEGER PRIMARY KEY,
    can_manage_balance INTEGER DEFAULT 0,
    can_manage_promo INTEGER DEFAULT 0,
    can_broadcast INTEGER DEFAULT 0,
    can_ban INTEGER DEFAULT 0,
    can_manage_cases INTEGER DEFAULT 0
)
""")
conn.commit()

# Автоматические миграции для поддержки динамической структуры полей
try:
    cursor.execute("ALTER TABLE users ADD COLUMN referrer_id INTEGER DEFAULT 0")
    conn.commit()
except sqlite3.OperationalError:
    pass

try:
    cursor.execute("ALTER TABLE promocodes ADD COLUMN include_season INTEGER DEFAULT 1")
    conn.commit()
except sqlite3.OperationalError:
    pass

try:
    cursor.execute("ALTER TABLE users ADD COLUMN channel_bonus_claimed INTEGER DEFAULT 0")
    conn.commit()
except sqlite3.OperationalError:
    pass

# Миграции для кейсов и новых прав админов
try:
    cursor.execute("ALTER TABLE users ADD COLUMN case_common INTEGER DEFAULT 0")
    cursor.execute("ALTER TABLE users ADD COLUMN case_ref INTEGER DEFAULT 0")
    cursor.execute("ALTER TABLE users ADD COLUMN case_all_nothing INTEGER DEFAULT 0")
    cursor.execute("ALTER TABLE users ADD COLUMN cases_opened_for_refs INTEGER DEFAULT 0")
    conn.commit()
except sqlite3.OperationalError:
    pass

try:
    cursor.execute("ALTER TABLE promocodes ADD COLUMN case_type TEXT DEFAULT ''")
    cursor.execute("ALTER TABLE promocodes ADD COLUMN case_count INTEGER DEFAULT 0")
    conn.commit()
except sqlite3.OperationalError:
    pass

try:
    cursor.execute("ALTER TABLE bot_admins ADD COLUMN can_manage_cases INTEGER DEFAULT 0")
    conn.commit()
except sqlite3.OperationalError:
    pass

cursor.execute("SELECT end_date FROM season_info ORDER BY id DESC LIMIT 1")
if not cursor.fetchone():
    default_end = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("INSERT INTO season_info (end_date) VALUES (?)", (default_end,))
    conn.commit()

# ================= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ПРОВЕРКИ ПРАВ =================
def is_super_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

def has_admin_permission(user_id: int, permission_field: str) -> bool:
    if user_id == ADMIN_ID:
        return True
    cursor.execute(f"SELECT {permission_field} FROM bot_admins WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    return bool(res and res[0] == 1)

def is_any_admin(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    cursor.execute("SELECT user_id FROM bot_admins WHERE user_id = ?", (user_id,))
    return bool(cursor.fetchone())

# ================= ФАБРИКИ КОЛБЭКОВ =================
class MenuCB(CallbackData, prefix="menu"):
    target: str

class WithdrawCB(CallbackData, prefix="wd"):
    amount: float

class WithdrawActionCB(CallbackData, prefix="wda"):
    action: str
    amount: float

class AdminWdCB(CallbackData, prefix="awd"):
    text_id: int
    wd_id: int
    action: str

class AdminStarCB(CallbackData, prefix="astar"):
    req_id: int
    action: str

class AdminGiveStarCB(CallbackData, prefix="gstar"):
    req_id: int
    mode: str

class AdminManageCB(CallbackData, prefix="amanage"):
    action: str

class AdminSeasonCB(CallbackData, prefix="aseason"):
    action: str

class AdminConfirmGiveCB(CallbackData, prefix="cfgive"):
    target_id: int
    amount: float
    include_season: int

class AdminConfirmTakeCB(CallbackData, prefix="cftake"):
    target_id: int
    amount: float
    include_season: int

class AdminPromoCB(CallbackData, prefix="apromo"):
    action: str
    value: str = ""

class AdminBcCB(CallbackData, prefix="abc"):
    mode: str

class RightsCB(CallbackData, prefix="rights"):
    user_id: int
    field: str

class DepositCB(CallbackData, prefix="deposit"):
    amount: int

class CaseCB(CallbackData, prefix="case"):
    action: str
    type: str = ""
    payment: str = ""

# ================= СОСТОЯНИЯ FSM =================
class BotStates(StatesGroup):
    waiting_for_screenshot = State()
    waiting_for_custom_stars = State()
    waiting_for_random_stars = State()
    waiting_for_ban_id = State()
    waiting_for_unban_id = State()
    
    # Рассылка
    waiting_for_broadcast_target = State()
    waiting_for_broadcast_msg = State()
    
    waiting_for_give_id = State()
    waiting_for_give_amount = State()
    waiting_for_take_id = State()
    waiting_for_take_amount = State()
    
    # Состояния для промокодов
    waiting_for_promo_name = State()
    waiting_for_promo_limit_type = State()
    waiting_for_promo_activations = State()
    waiting_for_promo_target_id = State()
    waiting_for_promo_expiration_date = State()
    waiting_for_promo_reward = State()
    waiting_for_promo_case_type = State()
    waiting_for_promo_case_count = State()
    waiting_for_user_promo_activate = State()
    
    # Управление правами
    waiting_for_admin_add_id = State()
    
    # Пополнение
    waiting_for_deposit_amount = State()
    
    # Админ кейсы
    waiting_for_admin_case_id = State()
    waiting_for_admin_case_type = State()
    waiting_for_admin_case_count = State()

# ================= МИДЛВАРЬ ДЛЯ ЗАЩИТЫ (БАН) =================
class BanMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user:
            cursor.execute("SELECT is_banned FROM users WHERE user_id = ?", (user.id,))
            res = cursor.fetchone()
            if res and res[0] == 1:
                if isinstance(event, CallbackQuery):
                    await event.answer("Вы заблокированы в боте!", show_alert=True)
                return
        return await handler(event, data)

dp.update.middleware(BanMiddleware())

# ================= КЛАВИАТУРЫ (ГЕНЕРАТОРЫ) =================
def get_main_menu():
    kb = [
        [InlineKeyboardButton(text="👤 Профиль", callback_data=MenuCB(target="profile").pack()),
         InlineKeyboardButton(text="🏆 Лидерборд", callback_data=MenuCB(target="user_leaderboard").pack())],
        [InlineKeyboardButton(text="⭐ Получить звезды", callback_data=MenuCB(target="get_stars").pack()),
         InlineKeyboardButton(text="🎁 Ежедневный бонус", callback_data=MenuCB(target="bonus").pack())],
        [InlineKeyboardButton(text="🎰 Кейсы", callback_data=MenuCB(target="cases_menu").pack())],
        [InlineKeyboardButton(text="📢 Скам база", url="https://t.me/giftez_base"),
         InlineKeyboardButton(text="🤖 Внести скамера", url="https://t.me/giftez_base_bot")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_back_btn(target="main"):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⬅ Назад в меню", callback_data=MenuCB(target=target).pack())
    ]])

# ================= ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ: ЗАВЕРШЕНИЕ СЕЗОНА =================
async def check_and_close_season(force=False):
    cursor.execute("SELECT end_date FROM season_info ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    if not row:
         return
    
    end_date = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
    if datetime.now() >= end_date or force:
        cursor.execute("SELECT user_id, username, season_earned FROM users WHERE is_banned = 0 AND season_earned > 0 ORDER BY season_earned DESC LIMIT 3")
        winners = cursor.fetchall()
        
        report_text = "🏆 Сезон лидерборда успешно завершен!\n\n"
        
        if winners:
            w1_id, w1_name, w1_earned = winners[0]
            report_text += f"🥇 Топ-1: {w1_name} (ID: {w1_id}) | За сезон: {round(w1_earned, 2)} ⭐\n(Награда: Приз от Администратора, звезды автоматически не начислялись)\n\n"
            try:
                await bot.send_message(
                    w1_id,
                    f"🏆 Поздравляем! Вы заняли Топ-1 в этом сезоне лидерборда, заработав {round(w1_earned, 2)} ⭐!\n\n"
                    f"ℹ Для получения главного приза от администрации, пожалуйста, ожидайте выплаты/выдачи."
                )
            except Exception: pass

            top2_prize = round(random.uniform(5.0, 10.0), 2)
            top3_prize = round(random.uniform(1.0, 4.9), 2)

            if len(winners) >= 2:
                w2_id, w2_name, w2_earned = winners[1]
                cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (top2_prize, w2_id))
                report_text += f"🥈 Топ-2: {w2_name} (ID: {w2_id}) | За сезон: {round(w2_earned, 2)} ⭐\n(Авто-приз: +{top2_prize} ⭐ начислен)\n\n"
                try:
                    await bot.send_message(
                        w2_id,
                        f"🏆 Поздравляем! Вы заняли Топ-2 в этом сезоне лидерборда, заработав {round(w2_earned, 2)} ⭐!\n\n"
                        f"🎁 Вам на баланс автоматически начислен приз в размере {top2_prize} ⭐!"
                    )
                except Exception: pass

            if len(winners) >= 3:
                w3_id, w3_name, w3_earned = winners[2]
                cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (top3_prize, w3_id))
                report_text += f"🥉 Топ-3: {w3_name} (ID: {w3_id}) | За сезон: {round(w3_earned, 2)} ⭐\n(Авто-приз: +{top3_prize} ⭐ начислен)\n\n"
                try:
                    await bot.send_message(
                        w3_id,
                        f"🏆 Поздравляем! Вы заняли Топ-3 в этом сезоне лидерборда, заработав {round(w3_earned, 2)} ⭐!\n\n"
                        f"🎁 Вам на баланс автоматически начислен приз в размере {top3_prize} ⭐!"
                    )
                except Exception: pass
                
            conn.commit()
            
            try:
                await bot.send_message(ADMIN_ID, report_text)
            except Exception: pass
        else:
            try:
                await bot.send_message(ADMIN_ID, "🏆 Сезон лидерборда завершился, но никто из пользователей не заработал звёзд. Победителей нет.")
            except Exception: pass
                
        cursor.execute("UPDATE users SET season_earned = 0.0")
        new_end = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("INSERT INTO season_info (end_date) VALUES (?)", (new_end,))
        conn.commit()

async def season_checker_loop():
    while True:
        try:
            await check_and_close_season(force=False)
        except Exception as e:
            logging.error(f"Ошибка в проверке сезона: {e}")
        await asyncio.sleep(3600)

# ================= ЛОГИКА ПОЛЬЗОВАТЕЛЯ =================

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    uname = f"@{message.from_user.username}" if message.from_user.username else "Без юзернейма"
    
    args = message.text.split()
    ref_id = 0
    if len(args) > 1:
        try:
            ref_id = int(args[1])
            if ref_id == uid:
                ref_id = 0
        except ValueError:
            ref_id = 0

    cursor.execute("SELECT user_id, referrer_id FROM users WHERE user_id = ?", (uid,))
    user_row = cursor.fetchone()
    
    if not user_row:
        if ref_id != 0:
            cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (ref_id,))
            if cursor.fetchone():
                is_premium = getattr(message.from_user, "is_premium", False) or False
                bonus_stars = 2.0 if is_premium else 1.0
                
                # ИЗМЕНЕНИЕ: Звезды за реферальный переход идут и на баланс, и в лидерборд сезона
                cursor.execute("UPDATE users SET balance = balance + ?, season_earned = season_earned + ? WHERE user_id = ?", (bonus_stars, bonus_stars, ref_id))
                try:
                    p_text = " (Премиум ⭐)" if is_premium else ""
                    await bot.send_message(ref_id, f"🎉 По вашей реф. ссылке зарегистрировался новый пользователь {uname}{p_text}!\nВам начислено +{bonus_stars} ⭐ (засчитано в лидерборд)")
                except Exception:
                    pass
            else:
                ref_id = 0
                
        cursor.execute("INSERT INTO users (user_id, username, referrer_id) VALUES (?, ?, ?)", (uid, uname, ref_id))
        conn.commit()
    else:
        cursor.execute("UPDATE users SET username = ? WHERE user_id = ?", (uname, uid))
        conn.commit()
        
    await message.answer(
        "Добро пожаловать в бот бонусов скам базы! Выберите интересующий раздел в меню ниже:",
        reply_markup=get_main_menu()
    )

@dp.callback_query(MenuCB.filter(F.target == "main"))
async def back_to_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "Добро пожаловать в бот бонусов скам базы! Выберите интересующий раздел в меню ниже:",
        reply_markup=get_main_menu()
    )

@dp.callback_query(MenuCB.filter(F.target == "user_leaderboard"))
async def view_user_leaderboard(callback: CallbackQuery):
    cursor.execute("SELECT end_date FROM season_info ORDER BY id DESC LIMIT 1")
    end_str = cursor.fetchone()[0]
    end_date = datetime.strptime(end_str, "%Y-%m-%d %H:%M:%S")
    time_left = end_date - datetime.now()
    
    days_left = max(0, time_left.days)
    hours_left = max(0, time_left.seconds // 3600)
    
    cursor.execute("SELECT username, season_earned FROM users WHERE is_banned = 0 AND season_earned > 0 ORDER BY season_earned DESC LIMIT 10")
    top_users = cursor.fetchall()
    
    text = "🏆 ТОП-10 ИГРОКОВ ТЕКУЩЕГО СЕЗОНА\n\n"
    text += f"📅 Окончание сезона: {end_str}\n"
    text += f"⏳ До подведения итогов осталось: {days_left}д {hours_left}ч\n\n"
    text += "🎁 НАГРАДЫ СЕЗОНА:\n"
    text += "🥇 Топ-1: Секретный главный приз от Админа!\n"
    text += "🥈 Топ-2: Автоматический бонус на баланс (до 10 ⭐)\n"
    text += "🥉 Топ-3: Автоматический бонус на баланс (меньше, чем Топ-2)\n\n"
    text += "📋 Рейтинг участников:\n"
    
    if top_users:
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        for i, user in enumerate(top_users):
            medal = medals[i] if i < len(medals) else "•"
            text += f"{medal} {user[0]} — {round(user[1], 2)} ⭐\n"
    else:
        text += "Рейтинг пока пуст. Станьте первым, зарабокав звёзды!"
        
    await callback.message.edit_text(text, reply_markup=get_back_btn("main"))

@dp.callback_query(MenuCB.filter(F.target == "profile"))
async def view_profile(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    uid = callback.from_user.id
    cursor.execute("SELECT balance, channel_bonus_claimed, case_common, case_ref, case_all_nothing FROM users WHERE user_id = ?", (uid,))
    balance, claimed, c_common, c_ref, c_all = cursor.fetchone()
    
    kb = [
        [InlineKeyboardButton(text="💳 Вывести", callback_data=MenuCB(target="withdraw_select").pack()),
         InlineKeyboardButton(text="🎟 Промокод", callback_data=MenuCB(target="user_promo_menu").pack())],
        [InlineKeyboardButton(text="💰 Пополнить баланс", callback_data=MenuCB(target="deposit_menu").pack())],
        [InlineKeyboardButton(text="🔗 Реферальная система", callback_data=MenuCB(target="user_ref_menu").pack())],
    ]
    
    if not claimed:
        kb.append([InlineKeyboardButton(text="📢 Бонус за подписку (+2 ⭐)", callback_data=MenuCB(target="claim_channel_bonus").pack())])
        
    kb.append([InlineKeyboardButton(text="👨‍💻 Поддержка", url="https://t.me/giftezbase_sup")])
    kb.append([InlineKeyboardButton(text="⬅ Назад в меню", callback_data=MenuCB(target="main").pack())])
    
    if is_any_admin(uid):
        kb.insert(3, [InlineKeyboardButton(text="⚙ Админ-Панель", callback_data=MenuCB(target="admin_panel").pack())])

    await callback.message.edit_text(
        f"👤 Ваш профиль\n\n🆔 Ваш ID: {uid}\n"
        f"⭐ Баланс: {round(balance, 2)} звезд\n\n"
        f"🎒 Ваши инвентарь кейсов:\n"
        f"📦 Обычные: {c_common} шт.\n"
        f"🤝 Реферальные: {c_ref} шт.\n"
        f"⚡ Всё или ничего: {c_all} шт.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )

# --- ПОПОЛНЕНИЕ БАЛАНСА ЧЕРЕЗ TELEGRAM STARS ---
@dp.callback_query(MenuCB.filter(F.target == "deposit_menu"))
async def deposit_menu_view(callback: CallbackQuery):
    kb = [
        [InlineKeyboardButton(text="2 ⭐", callback_data=DepositCB(amount=2).pack()),
         InlineKeyboardButton(text="5 ⭐", callback_data=DepositCB(amount=5).pack()),
         InlineKeyboardButton(text="10 ⭐", callback_data=DepositCB(amount=10).pack())],
        [InlineKeyboardButton(text="25 ⭐", callback_data=DepositCB(amount=25).pack()),
         InlineKeyboardButton(text="50 ⭐", callback_data=DepositCB(amount=50).pack())],
        [InlineKeyboardButton(text="✏ Своя сумма", callback_data=DepositCB(amount=0).pack())],
        [InlineKeyboardButton(text="⬅ Назад в профиль", callback_data=MenuCB(target="profile").pack())]
    ]
    await callback.message.edit_text(
        "💰 **Пополнение баланса через Telegram Stars**\n\nВыберите готовый вариант или укажите свою сумму (минимально от 1 звезды):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        parse_mode="Markdown"
    )

@dp.callback_query(DepositCB.filter())
async def process_deposit_choice(callback: CallbackQuery, callback_data: DepositCB, state: FSMContext):
    amount = callback_data.amount
    if amount == 0:
        await state.set_state(BotStates.waiting_for_deposit_amount)
        await callback.message.edit_text("✏ Введите сумму пополнения (целое число звезд, от 1):", reply_markup=get_back_btn("deposit_menu"))
        return
    
    await send_deposit_invoice(callback.message, callback.from_user.id, amount)
    await callback.answer()

@dp.message(BotStates.waiting_for_deposit_amount)
async def process_custom_deposit(message: Message, state: FSMContext):
    try:
        amount = int(message.text)
        if amount < 1:
            await message.answer("❌ Минимальная сумма пополнения — 1 звезда. Введите корректное число:")
            return
        await state.clear()
        await send_deposit_invoice(message, message.from_user.id, amount)
    except ValueError:
        await message.answer("❌ Введите корректное целое число звезд:")

async def send_deposit_invoice(message: Message, user_id: int, amount: int):
    prices = [LabeledPrice(label="Звезды", amount=amount)]
    await bot.send_invoice(
        chat_id=user_id,
        title="Пополнение баланса",
        description=f"Пополнение личного счета на {amount} звезд(ы) в боте.",
        prices=prices,
        provider_token="",
        currency="XTR",
        payload=f"deposit_{amount}"
    )

@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: Message):
    payload = message.successful_payment.invoice_payload
    uid = message.from_user.id
    
    if payload.startswith("deposit_"):
        amount = int(payload.split("_")[1])
        cursor.execute("UPDATE users SET balance = balance + ?, season_earned = season_earned + ? WHERE user_id = ?", (amount, amount, uid))
        conn.commit()
        await message.answer(f"✅ Баланс успешно пополнен на {amount} ⭐! Звезды зачислены на счет и в сезон.", reply_markup=get_main_menu())
    elif payload.startswith("buycase_"):
        parts = payload.split("_")
        case_type = parts[1]
        
        col_map = {"common": "case_common", "all": "case_all_nothing"}
        if case_type in col_map:
            cursor.execute(f"UPDATE users SET {col_map[case_type]} = {col_map[case_type]} + 1 WHERE user_id = ?", (uid,))
            conn.commit()
            await message.answer("✅ Оплата прошла успешно! Кейс добавлен в ваш инвентарь. Открыть его можно в разделе 'Кейсы'.", reply_markup=get_main_menu())

# --- РАЗДЕЛ КЕЙСОВ ДЛЯ ПОЛЬЗОВАТЕЛЯ ---
@dp.callback_query(MenuCB.filter(F.target == "cases_menu"))
async def cases_menu_view(callback: CallbackQuery):
    uid = callback.from_user.id
    
    # Автоматическая проверка рефералов для выдачи бесплатных реф-кейсов
    cursor.execute("SELECT COUNT(user_id) FROM users WHERE referrer_id = ?", (uid,))
    total_refs = cursor.fetchone()[0]
    cursor.execute("SELECT cases_opened_for_refs, case_common, case_ref, case_all_nothing FROM users WHERE user_id = ?", (uid,))
    opened_for_refs, c_common, c_ref, c_all = cursor.fetchone()
    
    available_ref_cases = (total_refs // 5) - opened_for_refs
    if available_ref_cases > 0:
        cursor.execute("UPDATE users SET case_ref = case_ref + ?, cases_opened_for_refs = cases_opened_for_refs + ? WHERE user_id = ?", (available_ref_cases, available_ref_cases, uid))
        conn.commit()
        c_ref += available_ref_cases
        await callback.answer(f"🎁 Вам начислено {available_ref_cases} реферальных кейсов за приглашенных друзей!", show_alert=True)

    text = (
        "🎰 **Раздел игровых кейсов**\n\n"
        "📦 **1. Обычный кейс** (Цена: 2 звезды)\n"
        "• Призы: простые награды, промокоды от 0.1 до 3 звезд на баланс, чистые звезды и т.д.\n"
        f"• В наличии: {c_common} шт.\n\n"
        "🤝 **2. Реферальный кейс** (Каждые 5 рефералов = 1 кейс)\n"
        "• Призы: от 3 до 6 звезд, промокоды на сброс ежедневного бонуса, или гарантированно 13 звезд (минимальный шанс).\n"
        f"• В наличии: {c_ref} шт.\n\n"
        "⚡ **3. Всё или ничего** (Цена: 5 звезд)\n"
        "• Призы: Либо НИЧЕГО (самый большой шанс), либо крупный джекпот на вывод: 13, 21 или 43 звезды (очень маленький шанс).\n"
        f"• В наличии: {c_all} шт."
    )
    
    kb = [
        [InlineKeyboardButton(text="📦 Обычный (Открыть / Купить)", callback_data=CaseCB(action="info", type="common").pack())],
        [InlineKeyboardButton(text="🤝 Реферальный (Открыть)", callback_data=CaseCB(action="info", type="ref").pack())],
        [InlineKeyboardButton(text="⚡ Всё или ничего (Открыть / Купить)", callback_data=CaseCB(action="info", type="all").pack())],
        [InlineKeyboardButton(text="⬅ В главное меню", callback_data=MenuCB(target="main").pack())]
    ]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")

@dp.callback_query(CaseCB.filter(F.action == "info"))
async def case_info_view(callback: CallbackQuery, callback_data: CaseCB):
    ctype = callback_data.type
    uid = callback.from_user.id
    
    cursor.execute("SELECT balance, case_common, case_ref, case_all_nothing FROM users WHERE user_id = ?", (uid,))
    balance, c_common, c_ref, c_all = cursor.fetchone()
    
    kb = []
    if ctype == "common":
        text = "📦 **Обычный кейс**\nЦена: 2 звезды.\n\nВы можете открыть имеющийся кейс из инвентаря или купить новый моментально."
        if c_common > 0:
            kb.append([InlineKeyboardButton(text="🚀 Открыть из инвентаря", callback_data=CaseCB(action="open", type="common").pack())])
        kb.append([InlineKeyboardButton(text="💳 Купить с баланса (2 ⭐)", callback_data=CaseCB(action="buy", type="common", payment="balance").pack())])
        kb.append([InlineKeyboardButton(text="⭐ Купить за Stars (2 ⭐)", callback_data=CaseCB(action="buy", type="common", payment="stars").pack())])
        
    elif ctype == "ref":
        text = "🤝 **Реферальный кейс**\nВыдается за каждые 5 рефералов.\nКупить за деньги нельзя."
        if c_ref > 0:
            kb.append([InlineKeyboardButton(text="🚀 Открыть из инвентаря", callback_data=CaseCB(action="open", type="ref").pack())])
        else:
            text += "\n\n❌ У вас нет доступных реферальных кейсов. Приглашайте друзей!"
            
    elif ctype == "all":
        text = "⚡ **Кейс 'Всё или ничего'**\nЦена: 5 звезд.\n\nОгромный риск! Самый высокий шанс проиграть, но внутри лежат гарантированные суммы на вывод."
        if c_all > 0:
            kb.append([InlineKeyboardButton(text="🚀 Открыть из инвентаря", callback_data=CaseCB(action="open", type="all").pack())])
        kb.append([InlineKeyboardButton(text="💳 Купить с баланса (5 ⭐)", callback_data=CaseCB(action="buy", type="all", payment="balance").pack())])
        kb.append([InlineKeyboardButton(text="⭐ Купить за Stars (5 ⭐)", callback_data=CaseCB(action="buy", type="all", payment="stars").pack())])

    kb.append([InlineKeyboardButton(text="⬅ Назад", callback_data=MenuCB(target="cases_menu").pack())])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")

@dp.callback_query(CaseCB.filter(F.action == "buy"))
async def case_buy_process(callback: CallbackQuery, callback_data: CaseCB):
    ctype = callback_data.type
    payment = callback_data.payment
    uid = callback.from_user.id
    
    price = 2 if ctype == "common" else 5
    col = "case_common" if ctype == "common" else "case_all_nothing"
    
    if payment == "balance":
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (uid,))
        bal = cursor.fetchone()[0]
        if bal < price:
            await callback.answer("❌ Недостаточно средств на балансе бота!", show_alert=True)
            return
        cursor.execute(f"UPDATE users SET balance = balance - ?, {col} = {col} + 1 WHERE user_id = ?", (price, uid))
        conn.commit()
        await callback.answer("✅ Кейс успешно куплен и добавлен в инвентарь!")
        await case_info_view(callback, callback_data)
        
    elif payment == "stars":
        await callback.answer()
        prices = [LabeledPrice(label=f"Кейс {ctype}", amount=price)]
        await bot.send_invoice(
            chat_id=uid,
            title="Покупка кейса",
            description=f"Приобретение кейса ({ctype}) в боте за Telegram Stars.",
            prices=prices,
            provider_token="",
            currency="XTR",
            payload=f"buycase_{ctype}"
        )

@dp.callback_query(CaseCB.filter(F.action == "open"))
async def case_open_animation(callback: CallbackQuery, callback_data: CaseCB):
    ctype = callback_data.type
    uid = callback.from_user.id
    
    col = "case_common" if ctype == "common" else ("case_ref" if ctype == "ref" else "case_all_nothing")
    
    cursor.execute(f"SELECT {col} FROM users WHERE user_id = ?", (uid,))
    count = cursor.fetchone()[0]
    if count < 1:
        await callback.answer("❌ У вас нет кейсов данного типа!", show_alert=True)
        return
        
    cursor.execute(f"UPDATE users SET {col} = {col} - 1 WHERE user_id = ?", (uid,))
    conn.commit()
    
    frames = ["🎰 [ 🟥 | 🟦 | 🟩 ]", "🎰 [ 🟦 | 🟨 | 🟥 ]", "🎰 [ 🟨 | 🟩 | 🟪 ]", "🎰 Определение награды..."]
    for frame in frames:
        try:
            await callback.message.edit_text(frame)
            await asyncio.sleep(0.4)
        except Exception:
            pass

    # Расчет выигрыша
    reward_text = ""
    if ctype == "common":
        roll = random.random()
        if roll < 0.4:  # Промокод на баланс
            amount = round(random.uniform(0.1, 3.0), 2)
            pcode = f"GIFT-{random.randint(1000,9999)}-{random.randint(1000,9999)}"
            cursor.execute("INSERT INTO promocodes (code, max_activations, type, reward_amount) VALUES (?, 1, 'balance', ?)", (pcode, amount))
            reward_text = f"🎫 Промокод `{pcode}` на {amount} ⭐ на баланс!"
        elif roll < 0.8:  # Прямые звезды на баланс
            amount = round(random.uniform(0.5, 2.0), 2)
            cursor.execute("UPDATE users SET balance = balance + ?, season_earned = season_earned + ? WHERE user_id = ?", (amount, amount, uid))
            reward_text = f"⭐ Напрямую на баланс начислено {amount} звезд!"
        else:
            reward_text = "🧁 Утешительный приз: 0.05 ⭐ зачислены на баланс."
            cursor.execute("UPDATE users SET balance = balance + 0.05 WHERE user_id = ?", (uid,))

    elif ctype == "ref":
        roll = random.random()
        if roll < 0.1:  # Гарантированные 13 звезд
            cursor.execute("UPDATE users SET balance = balance + 13.0, season_earned = season_earned + 13.0 WHERE user_id = ?", (uid,))
            reward_text = "🔥 ДЖЕКПОТ! На баланс зачислено 13 гарантированных звёзд!"
        elif roll < 0.5:  # Сброс ежедневного бонуса
            pcode = f"RESET-{random.randint(1000,9999)}"
            cursor.execute("INSERT INTO promocodes (code, max_activations, type, reward_amount) VALUES (?, 1, 'bonus_reset', 0)", (pcode,))
            reward_text = f"🎫 Промокод на сброс таймера бонуса: `{pcode}`"
        else:  # От 3 до 6 звезд
            amount = round(random.uniform(3.0, 6.0), 2)
            cursor.execute("UPDATE users SET balance = balance + ?, season_earned = season_earned + ? WHERE user_id = ?", (amount, amount, uid))
            reward_text = f"⭐ На баланс начислено {amount} звезд!"

    elif ctype == "all":
        roll = random.random()
        if roll < 0.85:  # Ничего
            reward_text = "💨 К сожалению, в этот раз вам ничего не выпало. Повезет в следующий раз!"
        elif roll < 0.93:  # 13 звезд
            cursor.execute("UPDATE users SET balance = balance + 13.0, season_earned = season_earned + 13.0 WHERE user_id = ?", (uid,))
            reward_text = "🎉 Вы выиграли 13 звезд!"
        elif roll < 0.98:  # 21 звезда
            cursor.execute("UPDATE users SET balance = balance + 21.0, season_earned = season_earned + 21.0 WHERE user_id = ?", (uid,))
            reward_text = "💎 Отлично! Вы выиграли 21 звезду!"
        else:  # 43 звезды
            cursor.execute("UPDATE users SET balance = balance + 43.0, season_earned = season_earned + 43.0 WHERE user_id = ?", (uid,))
            reward_text = "👑 МЕГА УДАЧА! Вы сорвали куш 43 звезды!"

    conn.commit()
    
    kb = [
        [InlineKeyboardButton(text="🔄 Открыть еще раз", callback_data=CaseCB(action="info", type=ctype).pack())],
        [InlineKeyboardButton(text="⬅ В меню кейсов", callback_data=MenuCB(target="cases_menu").pack())]
    ]
    await callback.message.edit_text(f"🎁 **Результат открытия:**\n\n{reward_text}", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")

# --- РАЗДЕЛ: ПОДРОБНАЯ РЕФЕРАЛЬНАЯ СИСТЕМА ---
@dp.callback_query(MenuCB.filter(F.target == "user_ref_menu"))
async def user_ref_menu(callback: CallbackQuery):
    uid = callback.from_user.id
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={uid}"
    
    cursor.execute("SELECT COUNT(user_id) FROM users WHERE referrer_id = ?", (uid,))
    total_refs = cursor.fetchone()[0]
    
    text = (
        "🔗 **Реферальная система**\n\n"
        "Приглашайте друзей и зарабатывайте звёзды вместе! Наша система полностью прозрачна и даёт бонусы за каждого активного игрока.\n\n"
        "📋 **Условия начисления бонусов:**\n"
        "1. **За каждого нового пользователя:** вы моментально получаете **+1.0 ⭐** на свой баланс и в лидерборд.\n"
        "2. **Если у вашего друга есть Telegram Premium:** стартовая награда увеличивается и составляет **+2.0 ⭐**!\n"
        "3. **Пассивный доход для всех:** Вы гарантированно получаете **3%** от суммы каждого ежедневного бонуса, который активируют ваши рефералы (начисляются также в лидерборд).\n"
        "4. **Подарочные кейсы:** За каждые 5 приглашенных друзей вам выдается 1 Реферальный кейс!\n\n"
        f"👥 Всего приглашено друзей: **{total_refs}**\n\n"
        f"📎 Ваша реферальная ссылка:\n`{ref_link}`"
    )
    await callback.message.edit_text(text, reply_markup=get_back_btn("profile"), parse_mode="Markdown")

# --- БОНУС ЗА ПОДПИСКУ НА КАНАЛ ---
@dp.callback_query(MenuCB.filter(F.target == "claim_channel_bonus"))
async def claim_channel_bonus(callback: CallbackQuery):
    uid = callback.from_user.id
    cursor.execute("SELECT channel_bonus_claimed FROM users WHERE user_id = ?", (uid,))
    claimed = cursor.fetchone()[0]
    
    if claimed == 1:
        await callback.answer("Вы уже получили этот бонус за подписку!", show_alert=True)
        return
        
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=uid)
        if member.status in ["member", "administrator", "creator"]:
            cursor.execute("UPDATE users SET balance = balance + 2.0, season_earned = season_earned + 2.0, channel_bonus_claimed = 1 WHERE user_id = ?", (uid,))
            conn.commit()
            await callback.message.edit_text(
                "🎉 Благодарим за подписку! Вам успешно начислено **+2.0 ⭐** на баланс и в сезонный лидерборд.",
                reply_markup=get_back_btn("profile"),
                parse_mode="Markdown"
            )
        else:
            await callback.answer(f"❌ Вы не подписаны на канал {CHANNEL_USERNAME}! Подпишитесь и повторите попытку.", show_alert=True)
    except Exception as e:
        await callback.answer("Не удалось проверить подписку. Убедитесь, что бот добавлен в канал.", show_alert=True)

@dp.callback_query(MenuCB.filter(F.target == "bonus"))
async def get_daily_bonus(callback: CallbackQuery):
    uid = callback.from_user.id
    cursor.execute("SELECT last_bonus, balance, referrer_id FROM users WHERE user_id = ?", (uid,))
    last_bonus, balance, referrer_id = cursor.fetchone()
    
    now = datetime.now()
    if last_bonus:
        last_date = datetime.strptime(last_bonus, "%Y-%m-%d %H:%M:%S")
        if now - last_date < timedelta(hours=24):
            time_left = timedelta(hours=24) - (now - last_date)
            hours, remainder = divmod(time_left.seconds, 3600)
            minutes, _ = divmod(remainder, 60)
            await callback.answer(f"⏳ Бонус уже получен! Приходите через {hours}ч {minutes}м", show_alert=True)
            return

    bonus_amount = round(random.uniform(0.01, 2.0), 2)
    new_balance = balance + bonus_amount
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    
    cursor.execute("UPDATE users SET balance = ?, last_bonus = ?, season_earned = season_earned + ? WHERE user_id = ?", (new_balance, now_str, bonus_amount, uid))
    
    if referrer_id and referrer_id != 0:
        ref_cut = round(bonus_amount * 0.03, 4)
        if ref_cut > 0:
            # ИЗМЕНЕНИЕ: 3% от ежедневного бонуса реферала капают рефереру как на баланс, так и в лидерборд сезона
            cursor.execute("UPDATE users SET balance = balance + ?, season_earned = season_earned + ? WHERE user_id = ?", (ref_cut, ref_cut, referrer_id))
            try:
                await bot.send_message(referrer_id, f"📈 Реферальный бонус! Вы получили {ref_cut} ⭐ (3% от ежедневного бонуса вашего реферала, зачислено в лидерборд).")
            except Exception:
                pass
                
    conn.commit()
    
    await callback.message.edit_text(
        f"🎁 Поздравляем!\n\nВы получили ежедневный бонус в размере {bonus_amount} ⭐\nВаш новый баланс: {round(new_balance, 2)} ⭐",
        reply_markup=get_back_btn()
    )

# ================= РАЗДЕЛ ПРОМОКОДОВ ДЛЯ ЮЗЕРА =================

@dp.callback_query(MenuCB.filter(F.target == "user_promo_menu"))
async def user_promo_menu(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.waiting_for_user_promo_activate)
    await callback.message.edit_text(
        "🎟 Введите действующий промокод в чат:", 
        reply_markup=get_back_btn("profile")
    )

@dp.message(BotStates.waiting_for_user_promo_activate)
async def process_user_promo_activation(message: Message, state: FSMContext):
    uid = message.from_user.id
    promo_entered = message.text.strip()
    
    cursor.execute("SELECT code, max_activations, current_activations, target_user_id, type, reward_amount, expires_at, is_infinite_activations, include_season, case_type, case_count FROM promocodes WHERE code = ?", (promo_entered,))
    promo = cursor.fetchone()
    
    if not promo:
        await message.answer("❌ Такого промокода не существует или он введен неверно.", reply_markup=get_main_menu())
        await state.clear()
        return

    code, max_act, curr_act, target_uid, p_type, reward, expires_at, is_inf, inc_season, c_type, c_count = promo
    
    if expires_at != "never":
        try:
            exp_date = datetime.strptime(expires_at, "%d.%m.%Y %H:%M")
            if datetime.now() > exp_date:
                await message.answer("❌ Срок действия этого промокода уже истёк.", reply_markup=get_main_menu())
                await state.clear()
                return
        except ValueError:
            pass

    if is_inf == 0 and curr_act >= max_act:
        await message.answer("❌ К сожалению, этот промокод уже закончился (достигнут лимит активаций).", reply_markup=get_main_menu())
        await state.clear()
        return
        
    if target_uid != 0 and target_uid != uid:
        await message.answer("❌ Данный промокод предназначен для другого пользователя.", reply_markup=get_main_menu())
        await state.clear()
        return
        
    cursor.execute("SELECT id FROM promo_activations WHERE code = ? AND user_id = ?", (code, uid))
    if cursor.fetchone():
        await message.answer("❌ Вы уже активировали этот промокод ранее!", reply_markup=get_main_menu())
        await state.clear()
        return
        
    if p_type == "balance":
        if inc_season == 1:
            cursor.execute("UPDATE users SET balance = balance + ?, season_earned = season_earned + ? WHERE user_id = ?", (reward, reward, uid))
        else:
            cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (reward, uid))
        text_success = f"🎉 Промокод успешно активирован!\n\nВам на баланс начислено: {round(reward, 2)} ⭐"
    elif p_type == "bonus_reset":
        cursor.execute("UPDATE users SET last_bonus = NULL WHERE user_id = ?", (uid,))
        text_success = "🎉 Промокод успешно активирован!\n\nТаймер ежедневного бонуса сброшен. Можете получить его прямо сейчас!"
    elif p_type == "cases":
        col = "case_common" if c_type == "common" else ("case_ref" if c_type == "ref" else "case_all_nothing")
        cursor.execute(f"UPDATE users SET {col} = {col} + ? WHERE user_id = ?", (c_count, uid))
        text_success = f"🎉 Промокод успешно активирован!\n\nВам начислены кейсы в количестве {c_count} шт.!"
        
    cursor.execute("INSERT INTO promo_activations (code, user_id) VALUES (?, ?)", (code, uid))
    cursor.execute("UPDATE promocodes SET current_activations = current_activations + 1 WHERE code = ?", (code,))
    conn.commit()
    
    await message.answer(text_success, reply_markup=get_main_menu())
    await state.clear()

# ================= СИСТЕМА ВЫВОДА =================

@dp.callback_query(MenuCB.filter(F.target == "withdraw_select"))
async def withdraw_select(callback: CallbackQuery):
    kb = [
        [InlineKeyboardButton(text="13 ⭐", callback_data=WithdrawCB(amount=13.0).pack()),
         InlineKeyboardButton(text="21 ⭐", callback_data=WithdrawCB(amount=21.0).pack()),
         InlineKeyboardButton(text="43 ⭐", callback_data=WithdrawCB(amount=43.0).pack())],
        [InlineKeyboardButton(text="⬅ Назад в профиль", callback_data=MenuCB(target="profile").pack())]
    ]
    await callback.message.edit_text("💳 Выберите сумму для вывода средств:\n*Свою сумму вводить нельзя", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(WithdrawCB.filter())
async def withdraw_confirm(callback: CallbackQuery, callback_data: WithdrawCB):
    amount = callback_data.amount
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (callback.from_user.id,))
    balance = cursor.fetchone()[0]
    
    if balance < amount:
        await callback.answer("❌ Недостаточно звезд на балансе для вывода этой суммы!", show_alert=True)
        return
        
    kb = [
        [InlineKeyboardButton(text="✅ Вывести", callback_data=WithdrawActionCB(action="confirm", amount=amount).pack()),
         InlineKeyboardButton(text="❌ Отменить вывод", callback_data=WithdrawActionCB(action="cancel", amount=amount).pack())]
    ]
    await callback.message.edit_text(f"❓ Вы точно хотите вывести {amount} ⭐?\nСумма будет списана сразу.", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(WithdrawActionCB.filter())
async def withdraw_process(callback: CallbackQuery, callback_data: WithdrawActionCB, state: FSMContext):
    action = callback_data.action
    amount = callback_data.amount
    uid = callback.from_user.id
    
    if action == "cancel":
        await callback.message.edit_text("❌ Вывод успешно отменен.", reply_markup=get_back_btn("profile"))
        return
        
    cursor.execute("SELECT balance, username FROM users WHERE user_id = ?", (uid,))
    balance, uname = cursor.fetchone()
    
    if balance < amount:
        await callback.answer("❌ Ошибка. Недостаточно средств.", show_alert=True)
        return
        
    new_balance = balance - amount
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, uid))
    cursor.execute("INSERT INTO withdraws (user_id, amount, date) VALUES (?, ?, ?)", (uid, amount, now_str))
    wd_id = cursor.lastrowid
    conn.commit()
    
    await callback.message.edit_text(
        f"✅ Заявка создана!\nС баланса списано {amount} ⭐.\nОжидайте проверки администратором.", 
        reply_markup=get_back_btn("profile")
    )
    
    admin_kb = [
        [InlineKeyboardButton(text="✅ Выполнено", callback_data=AdminWdCB(wd_id=wd_id, action="done", text_id=0).pack()),
         InlineKeyboardButton(text="⏳ Отложить", callback_data=AdminWdCB(wd_id=wd_id, action="delay", text_id=0).pack())]
    ]
    await bot.send_message(
        ADMIN_ID,
        f"🚨 Заявка на вывод!\n\nСумма: {amount} ⭐\nПользователь: {uname} (ID: {uid})\nДата и время: {now_str}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=admin_kb)
    )

# ================= КНОПКА ПОЛУЧИТЬ ЗВЕЗДЫ =================

@dp.callback_query(MenuCB.filter(F.target == "get_stars"))
async def get_stars_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.waiting_for_screenshot)
    await callback.message.edit_text(
        "📸 Отправьте скриншот с отправленной заявкой в бота скам базы (@giftez_base_bot) для подтверждения.",
        reply_markup=get_back_btn()
    )

@dp.message(BotStates.waiting_for_screenshot, F.photo)
async def process_screenshot(message: Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    uid = message.from_user.id
    uname = f"@{message.from_user.username}" if message.from_user.username else f"ID: {uid}"
    
    cursor.execute("INSERT INTO review_stars (user_id, screenshot_id) VALUES (?, ?)", (uid, photo_id))
    req_id = cursor.lastrowid
    conn.commit()
    
    await state.clear()
    await message.answer("✅ Скриншот успешно отправлен админу на проверку!", reply_markup=get_main_menu())
    
    admin_kb = [
        [InlineKeyboardButton(text="🟢 Одобрить", callback_data=AdminStarCB(req_id=req_id, action="approve").pack()),
         InlineKeyboardButton(text="🔴 Отказать", callback_data=AdminStarCB(req_id=req_id, action="reject").pack())]
    ]
    await bot.send_photo(
        ADMIN_ID,
        photo_id,
        caption=f"📩 Новая проверка скриншота!\nПользователь: {uname}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=admin_kb)
    )

@dp.message(BotStates.waiting_for_screenshot)
async def process_screenshot_invalid(message: Message):
    await message.answer("❌ Пожалуйста, отправьте именно скриншот (картинку) или вернитесь в меню через /start.")

# ================= АДМИН-ПАНЕЛЬ И ПРАВА =================

@dp.callback_query(MenuCB.filter(F.target == "admin_panel"))
async def admin_panel(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    if not is_any_admin(uid): return
    await state.clear()
    
    kb = [
        [InlineKeyboardButton(text="📥 Список Заявок на Вывод", callback_data=MenuCB(target="admin_wd_list").pack())],
        [InlineKeyboardButton(text="🎟 Создать промокод", callback_data=AdminManageCB(action="create_promo").pack())],
        [InlineKeyboardButton(text="🚫 Забанить юзера", callback_data=MenuCB(target="admin_ban").pack()),
         InlineKeyboardButton(text="🟢 Разбанить юзера", callback_data=MenuCB(target="admin_unban").pack())],
        [InlineKeyboardButton(text="➕ Выдать звёзды", callback_data=AdminManageCB(action="give_stars").pack()),
         InlineKeyboardButton(text="➖ Забрать звёзды", callback_data=AdminManageCB(action="take_stars").pack())],
        [InlineKeyboardButton(text="🎁 Выдать Кейсы", callback_data=AdminManageCB(action="give_cases_admin").pack())],
        [InlineKeyboardButton(text="🏆 Итоги сезона", callback_data=AdminManageCB(action="season_results").pack()),
         InlineKeyboardButton(text="📢 Создать рассылку", callback_data=AdminManageCB(action="start_broadcast").pack())],
        [InlineKeyboardButton(text="👥 Список пользователей", callback_data=AdminManageCB(action="user_list_view").pack())],
        [InlineKeyboardButton(text="📊 Статистика бота", callback_data=AdminManageCB(action="view_stats").pack())],
    ]
    
    if is_super_admin(uid):
        kb.append([InlineKeyboardButton(text="👑 Настройка админов", callback_data=AdminManageCB(action="setup_admins").pack())])
        
    kb.append([InlineKeyboardButton(text="⬅ Назад в профиль", callback_data=MenuCB(target="profile").pack())])
    await callback.message.edit_text("⚙ Панель администратора:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

# --- УПРАВЛЕНИЕ АДМИНИСТРАТОРАМИ ---
@dp.callback_query(AdminManageCB.filter(F.action == "setup_admins"))
async def setup_admins_panel(callback: CallbackQuery, state: FSMContext):
    if not is_super_admin(callback.from_user.id): return
    
    cursor.execute("SELECT user_id FROM bot_admins")
    admins = cursor.fetchall()
    
    text = "👑 **Управление администраторами**\n\nСписок текущих модераторов:\n"
    kb = []
    for a in admins:
        text += f"• Модератор ID: `{a[0]}`\n"
        kb.append([InlineKeyboardButton(text=f"⚙ Права для {a[0]}", callback_data=AdminManageCB(action=f"rights_{a[0]}").pack())])
        
    kb.append([InlineKeyboardButton(text="➕ Добавить админа", callback_data=AdminManageCB(action="add_new_admin").pack())])
    kb.append([InlineKeyboardButton(text="⬅ Назад в панель", callback_data=MenuCB(target="admin_panel").pack())])
    
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")

@dp.callback_query(AdminManageCB.filter(F.action == "add_new_admin"))
async def add_new_admin_start(callback: CallbackQuery, state: FSMContext):
    if not is_super_admin(callback.from_user.id): return
    await state.set_state(BotStates.waiting_for_admin_add_id)
    await callback.message.edit_text("Введите Telegram ID пользователя, которого хотите назначить администратором:", reply_markup=get_back_btn("admin_panel"))

@dp.message(BotStates.waiting_for_admin_add_id)
async def process_admin_add_id(message: Message, state: FSMContext):
    if not is_super_admin(message.from_user.id): return
    try:
        t_id = int(message.text)
        cursor.execute("INSERT OR IGNORE INTO bot_admins (user_id) VALUES (?)", (t_id,))
        conn.commit()
        await state.clear()
        await message.answer(f"✅ Администратор с ID {t_id} успешно добавлен в список. Настройте его права.", reply_markup=get_main_menu())
    except ValueError:
        await message.answer("❌ Введите корректный числовой ID.")

@dp.callback_query(AdminManageCB.filter(F.action.startswith("rights_")))
async def edit_admin_rights(callback: CallbackQuery, callback_data: AdminManageCB):
    if not is_super_admin(callback.from_user.id): return
    t_id = int(callback_data.action.split("_")[1])
    
    cursor.execute("SELECT can_manage_balance, can_manage_promo, can_broadcast, can_ban, can_manage_cases FROM bot_admins WHERE user_id = ?", (t_id,))
    rights = cursor.fetchone()
    
    if not rights:
        await callback.answer("Администратор не найден.")
        return
        
    b_bal = "✅ Разрешено" if rights[0] == 1 else "❌ Запрещено"
    b_promo = "✅ Разрешено" if rights[1] == 1 else "❌ Запрещено"
    b_bc = "✅ Разрешено" if rights[2] == 1 else "❌ Запрещено"
    b_ban = "✅ Разрешено" if rights[3] == 1 else "❌ Запрещено"
    b_case = "✅ Разрешено" if rights[4] == 1 else "❌ Запрещено"
    kb = [
        [InlineKeyboardButton(text=f"Баланс: {b_bal}", callback_data=RightsCB(user_id=t_id, field="can_manage_balance").pack())],
        [InlineKeyboardButton(text=f"Промокоды: {b_promo}", callback_data=RightsCB(user_id=t_id, field="can_manage_promo").pack())],
        [InlineKeyboardButton(text=f"Рассылка: {b_bc}", callback_data=RightsCB(user_id=t_id, field="can_broadcast").pack())],
        [InlineKeyboardButton(text=f"Бан системы: {b_ban}", callback_data=RightsCB(user_id=t_id, field="can_ban").pack())],
        [InlineKeyboardButton(text=f"Кейсы: {b_case}", callback_data=RightsCB(user_id=t_id, field="can_manage_cases").pack())],
        [InlineKeyboardButton(text="🗑 Удалить админа", callback_data=AdminManageCB(action=f"deladmin_{t_id}").pack())],
        [InlineKeyboardButton(text="⬅ Назад к списку", callback_data=AdminManageCB(action="setup_admins").pack())]
    ]
    await callback.message.edit_text(f"⚙ **Настройка прав администратора** `{t_id}`:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")

@dp.callback_query(RightsCB.filter())
async def toggle_right(callback: CallbackQuery, callback_data: RightsCB):
    if not is_super_admin(callback.from_user.id): return
    t_id = callback_data.user_id
    field = callback_data.field
    cursor.execute(f"SELECT {field} FROM bot_admins WHERE user_id = ?", (t_id,))
    cur = cursor.fetchone()[0]
    new_val = 0 if cur == 1 else 1
    cursor.execute(f"UPDATE bot_admins SET {field} = ? WHERE user_id = ?", (new_val, t_id))
    conn.commit()
    
    class MockCB:
        action = f"rights_{t_id}"
    await edit_admin_rights(callback, MockCB())

@dp.callback_query(AdminManageCB.filter(F.action.startswith("deladmin_")))
async def delete_admin_exec(callback: CallbackQuery, callback_data: AdminManageCB):
    if not is_super_admin(callback.from_user.id): return
    t_id = int(callback_data.action.split("_")[1])
    cursor.execute("DELETE FROM bot_admins WHERE user_id = ?", (t_id,))
    conn.commit()
    await callback.answer("Администратор успешно удален из системы.")
    await setup_admins_panel(callback, None)

# --- ИСПОЛНЕНИЕ ДЕЙСТВИЙ ЗАЯВОК НА ВЫВОД ---
@dp.callback_query(AdminWdCB.filter())
async def admin_wd_action(callback: CallbackQuery, callback_data: AdminWdCB):
    if not has_admin_permission(callback.from_user.id, "can_manage_balance"):
        await callback.answer("У вас нет прав на управление балансами!", show_alert=True)
        return
    wd_id = callback_data.wd_id
    action = callback_data.action
    cursor.execute("SELECT user_id, amount, status FROM withdraws WHERE id = ?", (wd_id,))
    wd_data = cursor.fetchone()
    if not wd_data:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return
    uid, amount, current_status = wd_data
    if action == "done":
        if current_status == 'done':
            await callback.answer("Эта заявка уже выполнена!", show_alert=True)
            return
        cursor.execute("UPDATE withdraws SET status = 'done' WHERE id = ?", (wd_id,))
        conn.commit()
        await callback.message.edit_text(f"✅ Вывод #{wd_id} на сумму {amount} ⭐ выполнен!", reply_markup=get_back_btn("admin_panel"))
        try:
            await bot.send_message(uid, f"🥳 Ваша заявка на вывод {amount} ⭐ успешно выполнена!")
        except: pass
    elif action == "delay":
        if current_status == 'delayed':
            await callback.answer("Эта заявка уже имеет статус 'Отложена'.", show_alert=True)
            return
        cursor.execute("UPDATE withdraws SET status = 'delayed' WHERE id = ?", (wd_id,))
        conn.commit()
        await callback.message.edit_text(f"⏳ Вывод #{wd_id} успешно отложен.", reply_markup=get_back_btn("admin_panel"))
        try:
            await bot.send_message(uid, f"⏳ Ваша заявка на вывод {amount} ⭐ отложена администратором. Пожалуйста, ожидайте выплаты (до 7 дней).")
        except: pass

@dp.callback_query(MenuCB.filter(F.target == "admin_wd_list"))
async def admin_wd_list(callback: CallbackQuery):
    if not has_admin_permission(callback.from_user.id, "can_manage_balance"):
        await callback.answer("У вас нет прав на управление балансами!", show_alert=True)
        return
    cursor.execute("SELECT id, user_id, amount, status FROM withdraws WHERE status = 'delayed' OR status = 'pending'")
    rows = cursor.fetchall()
    if not rows:
        await callback.message.edit_text("📥 Активных или отложенных заявок нет.", reply_markup=get_back_btn("admin_panel"))
        return
    text = "📥 Необработанные заявки:\n\n"
    kb = []
    for row in rows[:5]:
        status_icon = "⏳" if row[3] == "delayed" else "🚨"
        text += f"• {status_icon} Заявка #{row[0]} — Сумма: {row[2]} ⭐, Пользователь ID: {row[1]}\n"
        kb.append([
            InlineKeyboardButton(text=f"✅ Выполнить #{row[0]}", callback_data=AdminWdCB(wd_id=row[0], action="done", text_id=0).pack()),
            InlineKeyboardButton(text=f"⏳ Отложить #{row[0]}", callback_data=AdminWdCB(wd_id=row[0], action="delay", text_id=0).pack())
        ])
    kb.append([InlineKeyboardButton(text="⬅ Назад в панель", callback_data=MenuCB(target="admin_panel").pack())])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

# --- ВЫДАЧА КЕЙСОВ ЧЕРЕЗ АДМИНКУ ---
@dp.callback_query(AdminManageCB.filter(F.action == "give_cases_admin"))
async def admin_give_cases_start(callback: CallbackQuery, state: FSMContext):
    if not has_admin_permission(callback.from_user.id, "can_manage_cases"):
        await callback.answer("У вас нет прав на управление кейсами!", show_alert=True)
        return
    await state.set_state(BotStates.waiting_for_admin_case_id)
    await callback.message.edit_text("👤 Введите Telegram ID пользователя, которому хотите выдать кейсы:", reply_markup=get_back_btn("admin_panel"))

@dp.message(BotStates.waiting_for_admin_case_id)
async def admin_case_id_recv(message: Message, state: FSMContext):
    try:
        t_id = int(message.text)
        await state.update_data(target_id=t_id)
        
        kb = [
            [InlineKeyboardButton(text="Обычный кейс", callback_data=AdminPromoCB(action="setcase", value="common").pack())],
            [InlineKeyboardButton(text="Реферальный кейс", callback_data=AdminPromoCB(action="setcase", value="ref").pack())],
            [InlineKeyboardButton(text="Всё или ничего", callback_data=AdminPromoCB(action="setcase", value="all").pack())],
        ]
        await state.set_state(BotStates.waiting_for_admin_case_type)
        await message.answer("📦 Выберите тип кейса для выдачи:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    except ValueError:
        await message.answer("❌ Введите корректный числовой ID:")

@dp.callback_query(BotStates.waiting_for_admin_case_type, AdminPromoCB.filter(F.action == "setcase"))
async def admin_case_type_recv(callback: CallbackQuery, callback_data: AdminPromoCB, state: FSMContext):
    await state.update_data(case_type=callback_data.value)
    await state.set_state(BotStates.waiting_for_admin_case_count)
    await callback.message.edit_text("✏ Введите количество кейсов для выдачи (целое число):")

@dp.message(BotStates.waiting_for_admin_case_count)
async def admin_case_count_recv(message: Message, state: FSMContext):
    try:
        count = int(message.text)
        if count < 1:
            await message.answer("❌ Количество должно быть больше 0.")
            return
        data = await state.get_data()
        t_id = data['target_id']
        ctype = data['case_type']
        
        col = "case_common" if ctype == "common" else ("case_ref" if ctype == "ref" else "case_all_nothing")
        cursor.execute(f"UPDATE users SET {col} = {col} + ? WHERE user_id = ?", (count, t_id))
        conn.commit()
        
        await message.answer(f"✅ Успешно выдано {count} шт. кейсов ({ctype}) пользователю `{t_id}`!", reply_markup=get_main_menu())
        try:
            await bot.send_message(t_id, f"🎁 Администратор начислил вам кейсы в количестве {count} шт. ({ctype})! Проверьте профиль.")
        except: pass
        await state.clear()
    except ValueError:
        await message.answer("❌ Введите корректное число:")

# --- ПОДТВЕРЖДЕНИЕ СКРИНШОТОВ (ОДОБРЕНИЕ/ОТКАЗ) ---
@dp.callback_query(AdminStarCB.filter())
async def process_admin_star_choice(callback: CallbackQuery, callback_data: AdminStarCB, state: FSMContext):
    if not has_admin_permission(callback.from_user.id, "can_manage_balance"):
        await callback.answer("У вас нет прав на управление балансами!", show_alert=True)
        return
    req_id = callback_data.req_id
    action = callback_data.action
    cursor.execute("SELECT user_id, status FROM review_stars WHERE id = ?", (req_id,))
    req = cursor.fetchone()
    if not req:
        await callback.answer("Заявка не найдена.")
        return
    uid, status = req
    if status != 'pending':
        await callback.answer("Эта заявка уже обработана ранее!", show_alert=True)
        return
    if action == "reject":
        cursor.execute("UPDATE review_stars SET status = 'rejected' WHERE id = ?", (req_id,))
        conn.commit()
        await callback.message.edit_caption(caption="❌ Скриншот отклонен.")
        try: await bot.send_message(uid, "❌ Ваш скриншот был отклонен администратором. Проверьте условия.")
        except: pass
    elif action == "approve":
        await state.update_data(req_id=req_id, target_uid=uid, msg_id=callback.message.message_id)
        kb = [
            [InlineKeyboardButton(text="Своя сумма", callback_data=AdminGiveStarCB(req_id=req_id, mode="custom").pack()),
             InlineKeyboardButton(text="Случайная (0.1 - 4)", callback_data=AdminGiveStarCB(req_id=req_id, mode="random").pack())]
        ]
        await callback.message.reply("Выберите режим начисления звезд:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(AdminGiveStarCB.filter())
async def process_admin_giving_mode(callback: CallbackQuery, callback_data: AdminGiveStarCB, state: FSMContext):
    if not has_admin_permission(callback.from_user.id, "can_manage_balance"): return
    mode = callback_data.mode
    data = await state.get_data()
    if mode == "random":
        stars = round(random.uniform(0.1, 4.0), 2)
        uid = data['target_uid']
        req_id = data['req_id']
        cursor.execute("UPDATE users SET balance = balance + ?, season_earned = season_earned + ? WHERE user_id = ?", (stars, stars, uid))
        cursor.execute("UPDATE review_stars SET status = 'approved' WHERE id = ?", (req_id,))
        conn.commit()
        await callback.message.answer(f"✅ Начислено случайно {stars} ⭐ пользователю (ID: {uid})")
        try: await bot.send_message(uid, f"🎉 Ваш скриншот одобрен! Вам начислено +{stars} ⭐")
        except: pass
        await state.clear()
    elif mode == "custom":
        await state.set_state(BotStates.waiting_for_custom_stars)
        await callback.message.answer("Введите точное количество звезд для начисления:")

@dp.message(BotStates.waiting_for_custom_stars)
async def process_custom_stars_input(message: Message, state: FSMContext):
    if not has_admin_permission(message.from_user.id, "can_manage_balance"): return
    try:
        stars = round(float(message.text), 2)
        data = await state.get_data()
        uid = data['target_uid']
        req_id = data['req_id']
        cursor.execute("UPDATE users SET balance = balance + ?, season_earned = season_earned + ? WHERE user_id = ?", (stars, stars, uid))
        cursor.execute("UPDATE review_stars SET status = 'approved' WHERE id = ?", (req_id,))
        conn.commit()
        await message.answer(f"✅ Успешно начислено {stars} ⭐ пользователю (ID: {uid})")
        try: await bot.send_message(uid, f"🎉 Ваш скриншот одобрен! Вам начислено +{stars} ⭐")
        except: pass
        await state.clear()
    except ValueError:
        await message.answer("❌ Введите корректное число:")

# --- БАН СИСТЕМА ---
@dp.callback_query(MenuCB.filter(F.target == "admin_ban"))
async def admin_ban_start(callback: CallbackQuery, state: FSMContext):
    if not has_admin_permission(callback.from_user.id, "can_ban"):
        await callback.answer("Нет прав на блокировки!", show_alert=True)
        return
    await state.set_state(BotStates.waiting_for_ban_id)
    await callback.message.edit_text("🚫 Введите Telegram ID для блокировки в боте:", reply_markup=get_back_btn("admin_panel"))

@dp.message(BotStates.waiting_for_ban_id)
async def process_ban_exec(message: Message, state: FSMContext):
    if not has_admin_permission(message.from_user.id, "can_ban"): return
    try:
        t_id = int(message.text)
        cursor.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (t_id,))
        conn.commit()
        await state.clear()
        await message.answer(f"✅ Пользователь `{t_id}` заблокирован.", reply_markup=get_main_menu())
    except ValueError:
        await message.answer("❌ Введите числовой ID:")

@dp.callback_query(MenuCB.filter(F.target == "admin_unban"))
async def admin_unban_start(callback: CallbackQuery, state: FSMContext):
    if not has_admin_permission(callback.from_user.id, "can_ban"):
        await callback.answer("Нет прав на блокировки!", show_alert=True)
        return
    await state.set_state(BotStates.waiting_for_unban_id)
    await callback.message.edit_text("🟢 Введите Telegram ID для разблокировки:", reply_markup=get_back_btn("admin_panel"))

@dp.message(BotStates.waiting_for_unban_id)
async def process_unban_exec(message: Message, state: FSMContext):
    if not has_admin_permission(message.from_user.id, "can_ban"): return
    try:
        t_id = int(message.text)
        cursor.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (t_id,))
        conn.commit()
        await state.clear()
        await message.answer(f"✅ Пользователь `{t_id}` разблокирован.", reply_markup=get_main_menu())
    except ValueError:
        await message.answer("❌ Введите числовой ID:")

# --- ПРЯМАЯ КОРРЕКТИРОВКА БАЛАНСА ---
@dp.callback_query(AdminManageCB.filter(F.action == "give_stars"))
async def admin_give_stars_start(callback: CallbackQuery, state: FSMContext):
    if not has_admin_permission(callback.from_user.id, "can_manage_balance"):
        await callback.answer("Нет прав!", show_alert=True)
        return
    await state.set_state(BotStates.waiting_for_give_id)
    await callback.message.edit_text("➕ Введите ID юзера для НАЧИСЛЕНИЯ звезд:", reply_markup=get_back_btn("admin_panel"))

@dp.message(BotStates.waiting_for_give_id)
async def process_give_id(message: Message, state: FSMContext):
    try:
        await state.update_data(t_id=int(message.text))
        await state.set_state(BotStates.waiting_for_give_amount)
        await message.answer("Введите количество звезд:")
    except ValueError:
        await message.answer("Введите корректный ID:")

@dp.message(BotStates.waiting_for_give_amount)
async def process_give_amount(message: Message, state: FSMContext):
    try:
        amount = round(float(message.text), 2)
        data = await state.get_data()
        t_id = data['t_id']
        kb = [
            [InlineKeyboardButton(text="Да, учесть в сезон", callback_data=AdminConfirmGiveCB(target_id=t_id, amount=amount, include_season=1).pack())],
            [InlineKeyboardButton(text="Нет, только на баланс", callback_data=AdminConfirmGiveCB(target_id=t_id, amount=amount, include_season=0).pack())]
        ]
        await message.answer(f"Засчитывать {amount} ⭐ в сезонный рейтинг лидиборда?", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    except ValueError:
        await message.answer("Введите число:")

@dp.callback_query(AdminConfirmGiveCB.filter())
async def process_give_confirmed(callback: CallbackQuery, callback_data: AdminConfirmGiveCB, state: FSMContext):
    if not has_admin_permission(callback.from_user.id, "can_manage_balance"): return
    t_id = callback_data.target_id
    amount = callback_data.amount
    inc = callback_data.include_season
    if inc == 1:
        cursor.execute("UPDATE users SET balance = balance + ?, season_earned = season_earned + ? WHERE user_id = ?", (amount, amount, t_id))
    else:
        cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, t_id))
    conn.commit()
    await callback.message.edit_text(f"✅ Выдано {amount} ⭐ пользователю {t_id}")
    try: await bot.send_message(t_id, f"🎁 Администратор зачислил вам {amount} ⭐!")
    except: pass
    await state.clear()

@dp.callback_query(AdminManageCB.filter(F.action == "take_stars"))
async def admin_take_stars_start(callback: CallbackQuery, state: FSMContext):
    if not has_admin_permission(callback.from_user.id, "can_manage_balance"):
        await callback.answer("Нет прав!", show_alert=True)
        return
    await state.set_state(BotStates.waiting_for_take_id)
    await callback.message.edit_text("➖ Введите ID юзера для СПИСАНИЯ звезд:", reply_markup=get_back_btn("admin_panel"))

@dp.message(BotStates.waiting_for_take_id)
async def process_take_id(message: Message, state: FSMContext):
    try:
        await state.update_data(t_id=int(message.text))
        await state.set_state(BotStates.waiting_for_take_amount)
        await message.answer("Введите списываемое количество звезд:")
    except ValueError:
        await message.answer("Введите корректный ID:")

@dp.message(BotStates.waiting_for_take_amount)
async def process_take_amount(message: Message, state: FSMContext):
    try:
        amount = round(float(message.text), 2)
        data = await state.get_data()
        t_id = data['t_id']
        kb = [
            [InlineKeyboardButton(text="Да, уменьшить сезон", callback_data=AdminConfirmTakeCB(target_id=t_id, amount=amount, include_season=1).pack())],
            [InlineKeyboardButton(text="Нет, только с баланса", callback_data=AdminConfirmTakeCB(target_id=t_id, amount=amount, include_season=0).pack())]
        ]
        await message.answer(f"Списывать {amount} ⭐ из сезонного рейтинга?", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    except ValueError:
        await message.answer("Введите число:")

@dp.callback_query(AdminConfirmTakeCB.filter())
async def process_take_confirmed(callback: CallbackQuery, callback_data: AdminConfirmTakeCB, state: FSMContext):
    if not has_admin_permission(callback.from_user.id, "can_manage_balance"): return
    t_id = callback_data.target_id
    amount = callback_data.amount
    inc = callback_data.include_season
    if inc == 1:
        cursor.execute("UPDATE users SET balance = MAX(0, balance - ?), season_earned = MAX(0, season_earned - ?) WHERE user_id = ?", (amount, amount, t_id))
    else:
        cursor.execute("UPDATE users SET balance = MAX(0, balance - ?) WHERE user_id = ?", (amount, t_id))
    conn.commit()
    await callback.message.edit_text(f"✅ Списано {amount} ⭐ у пользователя {t_id}")
    await state.clear()

# --- ИТОГИ СЕЗОНА (ВРУЧНУЮ) ---
@dp.callback_query(AdminManageCB.filter(F.action == "season_results"))
async def admin_season_panel(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    kb = [
        [InlineKeyboardButton(text="🔥 Завершить СЕЗОН прямо сейчас", callback_data=AdminSeasonCB(action="force_close").pack())],
        [InlineKeyboardButton(text="⬅ Назад", callback_data=MenuCB(target="admin_panel").pack())]
    ]
    await callback.message.edit_text("⚠️ Внимание! Вы можете принудительно подвести итоги текущего сезона лидерборда и распределить награды победителям.", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(AdminSeasonCB.filter(F.action == "force_close"))
async def admin_season_force_close(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    await check_and_close_season(force=True)
    await callback.answer("Сезон успешно закрыт!", show_alert=True)
    await admin_panel(callback, None)

# --- СОЗДАНИЕ РАССЫЛКИ ---
@dp.callback_query(AdminManageCB.filter(F.action == "start_broadcast"))
async def broadcast_start_panel(callback: CallbackQuery, state: FSMContext):
    if not has_admin_permission(callback.from_user.id, "can_broadcast"):
        await callback.answer("Нет прав на рассылку!", show_alert=True)
        return
    kb = [
        [InlineKeyboardButton(text="Всем пользователям бота", callback_data=AdminBcCB(mode="all").pack())],
        [InlineKeyboardButton(text="⬅ Назад", callback_data=MenuCB(target="admin_panel").pack())]
    ]
    await callback.message.edit_text("📢 Выберите целевую аудиторию для рекламной рассылки:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(AdminBcCB.filter())
async def broadcast_mode_chosen(callback: CallbackQuery, callback_data: AdminBcCB, state: FSMContext):
    await state.update_data(mode=callback_data.mode)
    await state.set_state(BotStates.waiting_for_broadcast_msg)
    await callback.message.edit_text("📝 Отправьте текст рассылки (поддерживается форматирование):")

@dp.message(BotStates.waiting_for_broadcast_msg)
async def broadcast_execute(message: Message, state: FSMContext):
    if not has_admin_permission(message.from_user.id, "can_broadcast"): return
    text = message.text
    await state.clear()
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    
    sent = 0
    await message.answer(f"🚀 Рассылка запущена на {len(users)} пользователей...")
    for u in users:
        try:
            await bot.send_message(u[0], text)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    await message.answer(f"✅ Рассылка завершена! Успешно доставлено: {sent} сообщений.")

# --- СТАТИСТИКА БОТА ---
@dp.callback_query(AdminManageCB.filter(F.action == "view_stats"))
async def view_bot_statistics(callback: CallbackQuery):
    if not is_any_admin(callback.from_user.id): return
    cursor.execute("SELECT COUNT(user_id) FROM users")
    total = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(user_id) FROM users WHERE is_banned = 1")
    banned = cursor.fetchone()[0]
    cursor.execute("SELECT SUM(balance) FROM users")
    sum_bal = cursor.fetchone()[0] or 0.0
    
    text = (
        "📊 **Статистика системы**\n\n"
        f"👥 Всего зарегистрировано: {total} чел.\n"
        f"🚫 В блокировке: {banned} чел.\n"
        f"💳 Суммарный баланс на руках: {round(sum_bal, 2)} ⭐"
    )
    await callback.message.edit_text(text, reply_markup=get_back_btn("admin_panel"), parse_mode="Markdown")

# --- СИСТЕМА СОЗДАНИЯ ПРОМОКОДОВ ---
@dp.callback_query(AdminManageCB.filter(F.action == "create_promo"))
async def admin_create_promo_start(callback: CallbackQuery, state: FSMContext):
    if not has_admin_permission(callback.from_user.id, "can_manage_promo"):
        await callback.answer("Нет прав на промокоды!", show_alert=True)
        return
    await state.set_state(BotStates.waiting_for_promo_name)
    await callback.message.edit_text("🎟 Введите название для нового промокода (уникальное слово):", reply_markup=get_back_btn("admin_panel"))

@dp.message(BotStates.waiting_for_promo_name)
async def process_p_name(message: Message, state: FSMContext):
    if not has_admin_permission(message.from_user.id, "can_manage_promo"): return
    await state.update_data(p_code=message.text.strip())
    
    kb = [
        [InlineKeyboardButton(text="Лимитированный", callback_data=AdminPromoCB(action="limit_yes").pack()),
         InlineKeyboardButton(text="Бесконечный", callback_data=AdminPromoCB(action="limit_no").pack())]
    ]
    await state.set_state(BotStates.waiting_for_promo_limit_type)
    await message.answer("Выберите тип лимита активаций промокода:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(BotStates.waiting_for_promo_limit_type, AdminPromoCB.filter(F.action.startswith("limit_")))
async def process_p_limit_choice(callback: CallbackQuery, callback_data: AdminPromoCB, state: FSMContext):
    if callback_data.action == "limit_no":
        await state.update_data(is_infinite_activations=1, max_acts=999999)
        await ask_for_target_uid(callback.message, state)
    else:
        await state.update_data(is_infinite_activations=0)
        await state.set_state(BotStates.waiting_for_promo_activations)
        await callback.message.edit_text("Введите максимальное количество активаций (число):")

@dp.message(BotStates.waiting_for_promo_activations)
async def process_p_max_acts(message: Message, state: FSMContext):
    try:
        acts = int(message.text)
        await state.update_data(max_acts=acts)
        await ask_for_target_uid(message, state)
    except ValueError:
        await message.answer("Введите корректное число:")

async def ask_for_target_uid(message: Message, state: FSMContext):
    await state.set_state(BotStates.waiting_for_promo_target_id)
    kb = [[InlineKeyboardButton(text="Для всех (0)", callback_data=AdminPromoCB(action="target_all").pack())]]
    await message.answer("Введите Telegram ID, если промокод персональный, или нажмите кнопку:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(BotStates.waiting_for_promo_target_id, AdminPromoCB.filter(F.action == "target_all"))
async def process_p_target_all(callback: CallbackQuery, state: FSMContext):
    await state.update_data(target_uid=0)
    await ask_for_expiration(callback.message, state)

@dp.message(BotStates.waiting_for_promo_target_id)
async def process_p_target_id_msg(message: Message, state: FSMContext):
    try:
        uid = int(message.text)
        await state.update_data(target_uid=uid)
        await ask_for_expiration(message, state)
    except ValueError:
        await message.answer("Введите числовой ID или 0:")

async def ask_for_expiration(message: Message, state: FSMContext):
    await state.set_state(BotStates.waiting_for_promo_expiration_date)
    kb = [[InlineKeyboardButton(text="Без срока годности", callback_data=AdminPromoCB(action="exp_never").pack())]]
    await message.answer("Введите дату окончания действия в формате `ДД.ММ.ГГГГ ЧЧ:ММ` или выберите бессрочно:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")

@dp.callback_query(BotStates.waiting_for_promo_expiration_date, AdminPromoCB.filter(F.action == "exp_never"))
async def process_p_exp_never(callback: CallbackQuery, state: FSMContext):
    await state.update_data(expires_at="never")
    await ask_for_reward_type(callback.message)

@dp.message(BotStates.waiting_for_promo_expiration_date)
async def process_p_exp_date_text(message: Message, state: FSMContext):
    await state.update_data(expires_at=message.text.strip())
    await ask_for_reward_type(message)

async def ask_for_reward_type(message: Message):
    kb = [
        [InlineKeyboardButton(text="💰 Начисление Звезд", callback_data=AdminPromoCB(action="type_stars").pack())],
        [InlineKeyboardButton(text="⏳ Сброс Таймера Бонуса", callback_data=AdminPromoCB(action="type_bonus").pack())],
        [InlineKeyboardButton(text="📦 Выдача Кейсов", callback_data=AdminPromoCB(action="type_cases").pack())]
    ]
    await message.answer("Выберите тип награды промокода:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(AdminPromoCB.filter(F.action == "type_stars"))
async def process_p_type_stars(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.waiting_for_promo_reward)
    await callback.message.edit_text("Введите сумму начисления звезд (дробное или целое число):")

@dp.message(BotStates.waiting_for_promo_reward)
async def process_p_reward_amount(message: Message, state: FSMContext):
    try:
        reward = float(message.text)
        data = await state.get_data()
        
        cursor.execute(
            "INSERT INTO promocodes (code, max_activations, target_user_id, type, reward_amount, expires_at, is_infinite_activations, include_season) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (data['p_code'], data['max_acts'], data['target_uid'], "balance", reward, data['expires_at'], data.get('is_infinite_activations', 0), 1)
        )
        conn.commit()
        
        await message.answer(f"✅ Промокод `{data['p_code']}` на {reward} ⭐ успешно создан!", reply_markup=get_main_menu())
        await state.clear()
    except ValueError:
        await message.answer("❌ Введите корректное число:")

@dp.callback_query(AdminPromoCB.filter(F.action == "type_bonus"))
async def process_p_type_bonus(callback: CallbackQuery, state: FSMContext):
    if not has_admin_permission(callback.from_user.id, "can_manage_promo"): return
    data = await state.get_data()
    
    cursor.execute(
        "INSERT INTO promocodes (code, max_activations, target_user_id, type, reward_amount, expires_at, is_infinite_activations, include_season) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (data['p_code'], data['max_acts'], data['target_uid'], "bonus_reset", 0.0, data['expires_at'], data.get('is_infinite_activations', 0), 0)
    )
    conn.commit()
    
    await callback.message.edit_text(f"✅ Промокод `{data['p_code']}` (Сброс бонуса) успешно создан!", reply_markup=get_main_menu())
    await state.clear()

@dp.callback_query(AdminPromoCB.filter(F.action == "type_cases"))
async def process_p_type_cases(callback: CallbackQuery, state: FSMContext):
    kb = [
        [InlineKeyboardButton(text="Обычный кейс", callback_data=AdminPromoCB(action="pcase_common").pack())],
        [InlineKeyboardButton(text="Реферальный кейс", callback_data=AdminPromoCB(action="pcase_ref").pack())],
        [InlineKeyboardButton(text="Всё или ничего", callback_data=AdminPromoCB(action="pcase_all").pack())],
    ]
    await state.set_state(BotStates.waiting_for_promo_case_type)
    await callback.message.edit_text("Выберите тип кейса для промокода:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(BotStates.waiting_for_promo_case_type, AdminPromoCB.filter(F.action.startswith("pcase_")))
async def process_p_case_type(callback: CallbackQuery, callback_data: AdminPromoCB, state: FSMContext):
    ctype = callback_data.action.split("_")[1]
    await state.update_data(c_type=ctype)
    await state.set_state(BotStates.waiting_for_promo_case_count)
    await callback.message.edit_text("Введите количество выдаваемых кейсов (целое число):")

@dp.message(BotStates.waiting_for_promo_case_count)
async def process_p_case_count_finish(message: Message, state: FSMContext):
    try:
        count = int(message.text)
        data = await state.get_data()
        
        cursor.execute(
            "INSERT INTO promocodes (code, max_activations, target_user_id, type, reward_amount, expires_at, is_infinite_activations, include_season, case_type, case_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (data['p_code'], data['max_acts'], data['target_uid'], "cases", 0.0, data['expires_at'], data.get('is_infinite_activations', 0), 0, data['c_type'], count)
        )
        conn.commit()
        
        await message.answer(f"✅ Промокод `{data['p_code']}` на выдачу {count} кейсов ({data['c_type']}) создан!", reply_markup=get_main_menu())
        await state.clear()
    except ValueError:
        await message.answer("❌ Введите корректное целое число:")

# --- СПИСОК ПОЛЬЗОВАТЕЛЕЙ БЕЗ БАГОВ HTML ПАРСЕРА ---
@dp.callback_query(AdminManageCB.filter(F.action == "user_list_view"))
async def admin_user_list_view(callback: CallbackQuery):
    if not is_any_admin(callback.from_user.id): return
    
    cursor.execute("SELECT user_id, username, balance FROM users LIMIT 40")
    users = cursor.fetchall()
    
    text = "👥 **Список зарегистрированных пользователей (Топ-40):**\n\n"
    for u in users:
        safe_name = html.quote(u[1] if u[1] else "Без имени")
        text += f"• ID: `{u[0]}` | Логин: {safe_name} | Баланс: {round(u[2], 2)} ⭐\n"
        
    await callback.message.edit_text(text, reply_markup=get_back_btn("admin_panel"), parse_mode="HTML")

# ================= ЗАПУСК БОТА =================
async def main():
    asyncio.create_task(season_checker_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())