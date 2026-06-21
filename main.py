import asyncio
import logging
import os
import random
import sqlite3
from datetime import datetime, timedelta

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
)
from aiogram.filters.callback_data import CallbackData

# ================= НАСТРОЙКИ БОТА =================

BOT_TOKEN = os.getenv("BOT_TOKEN")

try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", 963968579))
except (ValueError, TypeError):
    ADMIN_ID = 963968579

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
    season_earned REAL DEFAULT 0.0
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

# НОВЫЕ ТАБЛИЦЫ ДЛЯ ПРОМОКОДОВ
cursor.execute("""
CREATE TABLE IF NOT EXISTS promocodes (
    code TEXT PRIMARY KEY,
    max_activations INTEGER,
    current_activations INTEGER DEFAULT 0,
    target_user_id INTEGER DEFAULT 0,
    type TEXT,
    reward_amount REAL DEFAULT 0.0
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS promo_activations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT,
    user_id INTEGER
)
""")
conn.commit()

cursor.execute("SELECT end_date FROM season_info ORDER BY id DESC LIMIT 1")
if not cursor.fetchone():
    default_end = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("INSERT INTO season_info (end_date) VALUES (?)", (default_end,))
    conn.commit()

# ================= ФАБРИКИ КОЛБЭКОВ =================
class MenuCB(CallbackData, prefix="menu"):
    target: str

class WithdrawCB(CallbackData, prefix="wd"):
    amount: float

class WithdrawActionCB(CallbackData, prefix="wda"):
    action: str
    amount: float

class AdminWdCB(CallbackData, prefix="awd"):
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

# Фабрики для интерактивного выбора режима изменения баланса (сезон или только баланс)
class AdminConfirmGiveCB(CallbackData, prefix="cfgive"):
    target_id: int
    amount: float
    include_season: int  # 1 - да, 0 - нет

class AdminConfirmTakeCB(CallbackData, prefix="cftake"):
    target_id: int
    amount: float
    include_season: int  # 1 - да, 0 - нет

# Фабрика для настройки промокодов админом
class AdminPromoCB(CallbackData, prefix="apromo"):
    action: str
    value: str = ""

# ================= СОСТОЯНИЯ FSM =================
class BotStates(StatesGroup):
    waiting_for_screenshot = State()
    waiting_for_custom_stars = State()
    waiting_for_random_stars = State()
    waiting_for_ban_id = State()
    waiting_for_unban_id = State()
    waiting_for_broadcast = State()
    waiting_for_give_id = State()
    waiting_for_give_amount = State()
    waiting_for_take_id = State()
    waiting_for_take_amount = State()
    
    # Состояния для промокодов
    waiting_for_promo_name = State()
    waiting_for_promo_activations = State()
    waiting_for_promo_target_id = State()
    waiting_for_promo_reward = State()
    waiting_for_user_promo_activate = State()

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
        [InlineKeyboardButton(text="📢 Скам база", url="https://t.me/giftez_base"),
         InlineKeyboardButton(text="🤖 Внести скамера", url="https://t.me/giftez_base_bot")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_back_btn(target="main"):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⬅ Назад в меню", callback_data=MenuCB(target=target).pack())
    ]])

# ================= ВСПУМОГАТЕЛЬНАЯ ФУНКЦИЯ: ЗАВЕРШЕНИЕ СЕЗОНА =================
async def check_and_close_season(force=False):
    cursor.execute("SELECT end_date FROM season_info ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    if not row:
         return
    
    end_date = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
    if datetime.now() >= end_date or force:
        # Берем Топ-3 игроков сезона
        cursor.execute("SELECT user_id, username, season_earned FROM users WHERE is_banned = 0 AND season_earned > 0 ORDER BY season_earned DESC LIMIT 3")
        winners = cursor.fetchall()
        
        report_text = "🏆 Сезон лидерборда успешно завершен!\n\n"
        
        if winners:
            # --- ТОП-1 ---
            w1_id, w1_name, w1_earned = winners[0]
            report_text += f"🥇 Топ-1: {w1_name} (ID: {w1_id}) | За сезон: {round(w1_earned, 2)} ⭐\n(Награда: Приз от Администратора, звезды автоматически не начислялись)\n\n"
            try:
                await bot.send_message(
                    w1_id,
                    f"🏆 Поздравляем! Вы заняли Топ-1 в этом сезоне лидерборда, заработав {round(w1_earned, 2)} ⭐!\n\n"
                    f"ℹ Для получения главного приза от администрации, пожалуйста, ожидайте выплаты/выдачи."
                )
            except Exception: pass

            # Генерируем случайные награды до 10 звезд для Топ-2 и Топ-3 (Топ-2 всегда строго больше Топ-3)
            # Например, Топ-2 получает от 5.0 до 10.0, Топ-3 от 1.0 до 4.9
            top2_prize = round(random.uniform(5.0, 10.0), 2)
            top3_prize = round(random.uniform(1.0, 4.9), 2)

            # --- ТОП-2 ---
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

            # --- ТОП-3 ---
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
                
        # Сброс сезона
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
    
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (uid,))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (user_id, username) VALUES (?, ?)", (uid, uname))
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

# ПРОСМОТР ЛИДЕРБОРДА ТОП-10 ДЛЯ ВСЕХ ПОЛЬЗОВАТЕЛЕЙ
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
        text += "Рейтинг пока пуст. Станьте первым, заработав звёзды!"
        
    await callback.message.edit_text(text, reply_markup=get_back_btn("main"))

@dp.callback_query(MenuCB.filter(F.target == "profile"))
async def view_profile(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (callback.from_user.id,))
    balance = cursor.fetchone()[0]
    
    kb = [
        [InlineKeyboardButton(text="💳 Вывести", callback_data=MenuCB(target="withdraw_select").pack()),
         InlineKeyboardButton(text="🎟 Промокод", callback_data=MenuCB(target="user_promo_menu").pack())],
        [InlineKeyboardButton(text="👨‍💻 Поддержка", url="https://t.me/giftezbase_sup")],
        [InlineKeyboardButton(text="⬅ Назад в меню", callback_data=MenuCB(target="main").pack())]
    ]
    if callback.from_user.id == ADMIN_ID:
        kb.insert(1, [InlineKeyboardButton(text="⚙ Админ-Панель", callback_data=MenuCB(target="admin_panel").pack())])

    await callback.message.edit_text(
        f"👤 Ваш профиль\n\n🆔 Ваш ID: {callback.from_user.id}\n⭐ Баланс: {round(balance, 2)} звезд",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )

@dp.callback_query(MenuCB.filter(F.target == "bonus"))
async def get_daily_bonus(callback: CallbackQuery):
    uid = callback.from_user.id
    cursor.execute("SELECT last_bonus, balance FROM users WHERE user_id = ?", (uid,))
    last_bonus, balance = cursor.fetchone()
    
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
    
    cursor.execute("SELECT code, max_activations, current_activations, target_user_id, type, reward_amount FROM promocodes WHERE code = ?", (promo_entered,))
    promo = cursor.fetchone()
    
    if not promo:
        await message.answer("❌ Такого промокода не существует или он введен неверно.", reply_markup=get_main_menu())
        await state.clear()
        return

    code, max_act, curr_act, target_uid, p_type, reward = promo
    
    # Проверка на лимит использований общего пула
    if curr_act >= max_act:
        await message.answer("❌ К сожалению, этот промокод уже закончился (достигнут лимит активаций).", reply_markup=get_main_menu())
        await state.clear()
        return
        
    # Проверка на индивидуальность промокода
    if target_uid != 0 and target_uid != uid:
        await message.answer("❌ Данный промокод предназначен для другого пользователя.", reply_markup=get_main_menu())
        await state.clear()
        return
        
    # Проверка на повторное использование пользователем
    cursor.execute("SELECT id FROM promo_activations WHERE code = ? AND user_id = ?", (code, uid))
    if cursor.fetchone():
        await message.answer("❌ Вы уже активировали этот промокод ранее!", reply_markup=get_main_menu())
        await state.clear()
        return
        
    # Применение промокода в зависимости от типа
    if p_type == "balance":
        cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (reward, uid))
        text_success = f"🎉 Промокод успешно активирован!\n\nВам на баланс начислено: {round(reward, 2)} ⭐"
    elif p_type == "bonus_reset":
        cursor.execute("UPDATE users SET last_bonus = NULL WHERE user_id = ?", (uid,))
        text_success = "🎉 Промокод успешно активирован!\n\nТаймер ежедневного бонуса сброшен. Можете получить его прямо сейчас!"
        
    # Записываем активацию
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
        [InlineKeyboardButton(text="✅ Выполнено", callback_data=AdminWdCB(wd_id=wd_id, action="done").pack()),
         InlineKeyboardButton(text="⏳ Отложить", callback_data=AdminWdCB(wd_id=wd_id, action="delay").pack())]
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

# ================= АДМИН-ПАНЕЛЬ И ОБРАБОТКА ЗАЯВОК =================

@dp.callback_query(MenuCB.filter(F.target == "admin_panel"))
async def admin_panel(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.clear()
    
    kb = [
        [InlineKeyboardButton(text="📥 Список Заявок на Вывод", callback_data=MenuCB(target="admin_wd_list").pack())],
        [InlineKeyboardButton(text="🎟 Создать промокод", callback_data=AdminManageCB(action="create_promo").pack())],
        [InlineKeyboardButton(text="🚫 Забанить юзера", callback_data=MenuCB(target="admin_ban").pack()),
         InlineKeyboardButton(text="🟢 Разбанить юзера", callback_data=MenuCB(target="admin_unban").pack())],
        [InlineKeyboardButton(text="➕ Выдать звёзды", callback_data=AdminManageCB(action="give_stars").pack()),
         InlineKeyboardButton(text="➖ Забрать звёзды", callback_data=AdminManageCB(action="take_stars").pack())],
        [InlineKeyboardButton(text="🏆 Итоги сезона", callback_data=AdminManageCB(action="season_results").pack()),
         InlineKeyboardButton(text="📢 Создать рассылку", callback_data=AdminManageCB(action="start_broadcast").pack())],
        [InlineKeyboardButton(text="⬅ Назад в профиль", callback_data=MenuCB(target="profile").pack())]
    ]
    await callback.message.edit_text("⚙ Панель администратора:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(AdminWdCB.filter())
async def admin_wd_action(callback: CallbackQuery, callback_data: AdminWdCB):
    if callback.from_user.id != ADMIN_ID: return
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
        try: await bot.send_message(uid, f"🥳 Ваша заявка на вывод {amount} ⭐ успешно выполнена!")
        except: pass
        
    elif action == "delay":
        if current_status == 'delayed':
            await callback.answer("Эта заявка уже имеет статус 'Отложена'.", show_alert=True)
            return
        cursor.execute("UPDATE withdraws SET status = 'delayed' WHERE id = ?", (wd_id,))
        conn.commit()
        await callback.message.edit_text(f"⏳ Вывод #{wd_id} успешно отложен.", reply_markup=get_back_btn("admin_panel"))
        try: await bot.send_message(uid, f"⏳ Ваша заявка на вывод {amount} ⭐ отложена администратором. Пожалуйста, ожидайте выплаты (до 7 дней).")
        except: pass

@dp.callback_query(MenuCB.filter(F.target == "admin_wd_list"))
async def admin_wd_list(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    cursor.execute("SELECT id, user_id, amount, status FROM withdraws WHERE status = 'delayed' OR status = 'pending'")
    rows = cursor.fetchall()
    
    if not rows:
        await callback.message.edit_text("📥 Активных или отложенных заявок нет.", reply_markup=get_back_btn("admin_panel"))
        return
        
    text = "📥 Необработанные заявки:\n\n"
    kb = []
    for row in rows[:5]:
        status_icon = "⏳" if row[3] == "delayed" else "🚨"
        text += f"• {status_icon} Заявка #{row[0]} | ID: {row[1]} | Сумма: {row[2]} ⭐\n"
        
        if row[3] == "delayed":
            kb.append([InlineKeyboardButton(text=f"✅ Выполнить #{row[0]}", callback_data=AdminWdCB(wd_id=row[0], action="done").pack())])
        else:
            kb.append([
                InlineKeyboardButton(text=f"✅ Выполнить #{row[0]}", callback_data=AdminWdCB(wd_id=row[0], action="done").pack()),
                InlineKeyboardButton(text=f"⏳ Отложить #{row[0]}", callback_data=AdminWdCB(wd_id=row[0], action="delay").pack())
            ])
            
    kb.append([InlineKeyboardButton(text="⬅ Назад", callback_data=MenuCB(target="admin_panel").pack())])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(AdminStarCB.filter())
async def admin_star_action(callback: CallbackQuery, callback_data: AdminStarCB):
    if callback.from_user.id != ADMIN_ID: return
    req_id = callback_data.req_id
    action = callback_data.action
    
    cursor.execute("SELECT user_id, status FROM review_stars WHERE id = ?", (req_id,))
    res = cursor.fetchone()
    if not res or res[1] != 'pending':
        await callback.answer("Уже проверено.", show_alert=True)
        return
        
    uid = res[0]
    
    if action == "reject":
        cursor.execute("UPDATE review_stars SET status = 'rejected' WHERE id = ?", (req_id,))
        conn.commit()
        await callback.message.edit_caption(caption="🔴 Скриншот отклонен администратором.")
        try: await bot.send_message(uid, "❌ Ваш скриншот для получения звезд был отклонен администратором.")
        except: pass
    else:
        kb = [
            [InlineKeyboardButton(text="1 звезда (Дефолт)", callback_data=AdminGiveStarCB(req_id=req_id, mode="default").pack())],
            [InlineKeyboardButton(text="Своя сумма", callback_data=AdminGiveStarCB(req_id=req_id, mode="custom").pack())],
            [InlineKeyboardButton(text="Рандом", callback_data=AdminGiveStarCB(req_id=req_id, mode="random").pack())]
        ]
        await callback.message.edit_caption(caption="Сколько звезд начислеть за этот скриншот?", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(AdminGiveStarCB.filter())
async def admin_give_star_modes(callback: CallbackQuery, callback_data: AdminGiveStarCB, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    req_id = callback_data.req_id
    mode = callback_data.mode
    
    cursor.execute("SELECT user_id FROM review_stars WHERE id = ?", (req_id,))
    uid = cursor.fetchone()[0]
    
    if mode == "default":
        cursor.execute("UPDATE review_stars SET status = 'approved' WHERE id = ?", (req_id,))
        cursor.execute("UPDATE users SET balance = balance + 1.0, season_earned = season_earned + 1.0 WHERE user_id = ?", (uid,))
        conn.commit()
        await callback.message.edit_caption(caption="🟢 Начислена 1 звезда (Баланс + Сезон).")
        try: await bot.send_message(uid, "🟢 Ваш скриншот одобрен! Вам начислена 1.0 ⭐")
        except: pass
        
    elif mode == "custom":
        await state.set_state(BotStates.waiting_for_custom_stars)
        await state.update_data(req_id=req_id, user_id=uid)
        kb = [[InlineKeyboardButton(text="⬅ Отмена (В меню)", callback_data=MenuCB(target="main").pack())]]
        await bot.send_message(ADMIN_ID, "Введите сумму звезд для начисления (например 1.5):", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
        
    elif mode == "random":
        await state.set_state(BotStates.waiting_for_random_stars)
        await state.update_data(req_id=req_id, user_id=uid)
        kb = [[InlineKeyboardButton(text="⬅ Отмена (В меню)", callback_data=MenuCB(target="main").pack())]]
        await bot.send_message(ADMIN_ID, "Введите диапазон рандома через дефис (например, 0.5-3.5):", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.message(BotStates.waiting_for_custom_stars)
async def process_custom_stars(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    data = await state.get_data()
    req_id, uid = data['req_id'], data['user_id']
    try:
        amount = float(message.text.replace(',', '.'))
        cursor.execute("UPDATE review_stars SET status = 'approved' WHERE id = ?", (req_id,))
        cursor.execute("UPDATE users SET balance = balance + ?, season_earned = season_earned + ? WHERE user_id = ?", (amount, amount, uid))
        conn.commit()
        await message.answer(f"🟢 Успешно начислено {amount} ⭐ (Баланс + Сезон)", reply_markup=get_main_menu())
        try: await bot.send_message(uid, f"🟢 Ваш скриншот одобрен! Вам начислено {amount} ⭐")
        except: pass
        await state.clear()
    except ValueError:
        kb = [[InlineKeyboardButton(text="⬅ Отмена (В меню)", callback_data=MenuCB(target="main").pack())]]
        await message.answer("❌ Ошибка ввода. Введите число (например, 2.5):", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.message(BotStates.waiting_for_random_stars)
async def process_random_stars(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    data = await state.get_data()
    req_id, uid = data['req_id'], data['user_id']
    try:
        parts = message.text.split('-')
        val_from = float(parts[0].replace(',', '.'))
        val_to = float(parts[1].replace(',', '.'))
        amount = round(random.uniform(val_from, val_to), 2)
        
        cursor.execute("UPDATE review_stars SET status = 'approved' WHERE id = ?", (req_id,))
        cursor.execute("UPDATE users SET balance = balance + ?, season_earned = season_earned + ? WHERE user_id = ?", (amount, amount, uid))
        conn.commit()
        await message.answer(f"🟢 Рандом сработал! Начислено {amount} ⭐ (Баланс + Сезон)", reply_markup=get_main_menu())
        try: await bot.send_message(uid, f"🟢 Ваш скриншот одобрен! Вам начислено {amount} ⭐")
        except: pass
        await state.clear()
    except Exception:
        kb = [[InlineKeyboardButton(text="⬅ Отмена (В меню)", callback_data=MenuCB(target="main").pack())]]
        await message.answer("❌ Ошибка ввода. Введите диапазон в формате 'от-до':", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

# ================= БАН-СИСТЕМА =================

@dp.callback_query(MenuCB.filter(F.target == "admin_ban"))
async def admin_ban_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.set_state(BotStates.waiting_for_ban_id)
    cursor.execute("SELECT user_id, username FROM users WHERE is_banned = 0")
    users = cursor.fetchall()
    
    user_list_text = "📋 Список зарегистрированных пользователей:\n"
    if users:
        for u in users: user_list_text += f"• ID: {u[0]} | {u[1]}\n"
    else:
        user_list_text += "Активных пользователей нет.\n"
    user_list_text += "\n🚫 Введите Telegram ID пользователя для блокировки:"
    await callback.message.edit_text(user_list_text, reply_markup=get_back_btn("admin_panel"))

@dp.message(BotStates.waiting_for_ban_id)
async def process_ban(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try:
        target_id = int(message.text)
        cursor.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (target_id,))
        conn.commit()
        await message.answer(f"🚫 Пользователь {target_id} успешно заблокирован.", reply_markup=get_main_menu())
        await state.clear()
    except ValueError:
        kb = [[InlineKeyboardButton(text="⬅ Отмена (В меню)", callback_data=MenuCB(target="main").pack())]]
        await message.answer("❌ Введите корректный числовой ID:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(MenuCB.filter(F.target == "admin_unban"))
async def admin_unban_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.set_state(BotStates.waiting_for_unban_id)
    cursor.execute("SELECT user_id, username FROM users WHERE is_banned = 1")
    users = cursor.fetchall()
    
    user_list_text = "📋 Список заблокированных пользователей:\n"
    if users:
        for u in users: user_list_text += f"• ID: {u[0]} | {u[1]}\n"
    else:
        user_list_text += "Заблокированных пользователей нет.\n"
    user_list_text += "\n🟢 Введите Telegram ID пользователя для разблокировки:"
    await callback.message.edit_text(user_list_text, reply_markup=get_back_btn("admin_panel"))

@dp.message(BotStates.waiting_for_unban_id)
async def process_unban(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try:
        target_id = int(message.text)
        cursor.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (target_id,))
        conn.commit()
        await message.answer(f"🟢 Пользователь {target_id} успешно разблокирован.", reply_markup=get_main_menu())
        await state.clear()
    except ValueError:
        kb = [[InlineKeyboardButton(text="⬅ Отмена (В меню)", callback_data=MenuCB(target="main").pack())]]
        await message.answer("❌ Введите корректный числовой ID:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

# ================= УПРАВЛЕНИЕ БАЛАНСОМ, СЕЗОНАМИ И РАССЫЛКОЙ =================

@dp.callback_query(AdminManageCB.filter())
async def process_admin_management(callback: CallbackQuery, callback_data: AdminManageCB, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    action = callback_data.action
    
    if action == "start_broadcast":
        await state.set_state(BotStates.waiting_for_broadcast)
        await callback.message.edit_text("📢 Режим рассылки\n\nОтправьте текст или фото с подписью:", reply_markup=get_back_btn("admin_panel"))
        
    elif action == "season_results":
        cursor.execute("SELECT end_date FROM season_info ORDER BY id DESC LIMIT 1")
        end_str = cursor.fetchone()[0]
        end_date = datetime.strptime(end_str, "%Y-%m-%d %H:%M:%S")
        time_left = end_date - datetime.now()
        
        cursor.execute("SELECT user_id, username, season_earned FROM users WHERE is_banned = 0 AND season_earned > 0 ORDER BY season_earned DESC LIMIT 3")
        top_users = cursor.fetchall()
        
        days_left = max(0, time_left.days)
        hours_left = max(0, time_left.seconds // 3600)
        
        text = f"🏆 Текущие итоги сезона лидерборда:\n\n📅 Окончание сезона: {end_str}\n⏳ Осталось: {days_left}д {hours_left}ч\n\n"
        if top_users:
            for i, u in enumerate(top_users):
                text += f"Топ-{i+1}: {u[1]} (ID: {u[0]}) | За сезон: {round(u[2], 2)} звезд\n"
        else:
            text += "Лидеры не определены."
            
        kb = [
            [InlineKeyboardButton(text="🛑 Завершить сезон сейчас", callback_data=AdminSeasonCB(action="force_end").pack())],
            [InlineKeyboardButton(text="⬅ Назад", callback_data=MenuCB(target="admin_panel").pack())]
        ]
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

    elif action == "give_stars":
        await state.set_state(BotStates.waiting_for_give_id)
        cursor.execute("SELECT user_id, username, balance, season_earned FROM users")
        users = cursor.fetchall()
        text = "📋 Список пользователей:\n"
        for u in users:
            text += f"• ID: {u[0]} | {u[1]} | Бал: {round(u[2], 2)} | Сез: {round(u[3], 2)} ⭐\n"
        text += "\n➕ Введите Telegram ID пользователя, которому хотите ВЫДАТЬ звёзды:"
        await callback.message.edit_text(text, reply_markup=get_back_btn("admin_panel"))

    elif action == "take_stars":
        await state.set_state(BotStates.waiting_for_take_id)
        cursor.execute("SELECT user_id, username, balance, season_earned FROM users")
        users = cursor.fetchall()
        text = "📋 Список пользователей:\n"
        for u in users:
            text += f"• ID: {u[0]} | {u[1]} | Бал: {round(u[2], 2)} | Сез: {round(u[3], 2)} ⭐\n"
        text += "\n➖ Введите Telegram ID пользователя, у которого хотите ЗАБРАТЬ звёзды:"
        await callback.message.edit_text(text, reply_markup=get_back_btn("admin_panel"))

    # СТЕП 1 ДЛЯ СОЗДАНИЯ ПРОМОКОДА
    elif action == "create_promo":
        await state.set_state(BotStates.waiting_for_promo_name)
        await callback.message.edit_text("🎟 Создание промокода\n\nВведите сам текст промокода (например, SECRET2026):", reply_markup=get_back_btn("admin_panel"))

@dp.callback_query(AdminSeasonCB.filter(F.action == "force_end"))
async def process_force_end_season(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    await check_and_close_season(force=True)
    await callback.answer("Сезон принудительно завершен!", show_alert=True)
    await callback.message.edit_text("✅ Итоги сезона подведены, призёры получили уведомления, запущен новый сезон.", reply_markup=get_back_btn("admin_panel"))

@dp.message(BotStates.waiting_for_broadcast)
async def process_broadcast_message(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    await state.clear()
    cursor.execute("SELECT user_id FROM users WHERE is_banned = 0")
    users = cursor.fetchall()
    
    success_count = 0
    await message.answer("🚀 Рассылка запущена...")
    for row in users:
        uid = row[0]
        try:
            if message.photo:
                await bot.send_photo(uid, message.photo[-1].file_id, caption=message.caption)
            else:
                await bot.send_message(uid, message.text)
            success_count += 1
            await asyncio.sleep(0.05)
        except Exception: pass
    await message.answer(f"📢 Рассылка завершена! Получили {success_count} пользователей.", reply_markup=get_main_menu())

# --- ВЫДАЧА ЗВЕЗД (ВВОД ID И СУММЫ) ---
@dp.message(BotStates.waiting_for_give_id)
async def process_give_id(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try:
        target_id = int(message.text)
        cursor.execute("SELECT username FROM users WHERE user_id = ?", (target_id,))
        if not cursor.fetchone():
            await message.answer("❌ Такого пользователя нет в базе. Попробуйте еще раз:")
            return
        await state.update_data(target_id=target_id)
        await state.set_state(BotStates.waiting_for_give_amount)
        await message.answer("Введите количество звёзд, которое нужно ВЫДАТЬ:")
    except ValueError:
        await message.answer("❌ Введите числовой ID:")

@dp.message(BotStates.waiting_for_give_amount)
async def process_give_amount(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try:
        amount = float(message.text.replace(',', '.'))
        if amount <= 0:
            await message.answer("❌ Сумма должна быть больше 0:")
            return
        data = await state.get_data()
        target_id = data['target_id']
        await state.clear()
        
        # Вместо мгновенного начисления отправляем выбор админу
        kb = [
            [InlineKeyboardButton(text="💎 Баланс + Сезон", callback_data=AdminConfirmGiveCB(target_id=target_id, amount=amount, include_season=1).pack())],
            [InlineKeyboardButton(text="💳 Только на баланс", callback_data=AdminConfirmGiveCB(target_id=target_id, amount=amount, include_season=0).pack())]
        ]
        await message.answer(f"❓ Куда зачислить {amount} ⭐ для пользователя {target_id}?", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    except ValueError:
        await message.answer("❌ Введите корректное число:")

# Подтверждение выдачи админом
@dp.callback_query(AdminConfirmGiveCB.filter())
async def execute_give_stars(callback: CallbackQuery, callback_data: AdminConfirmGiveCB):
    if callback.from_user.id != ADMIN_ID: return
    uid = callback_data.target_id
    amount = callback_data.amount
    inc_season = callback_data.include_season
    
    if inc_season == 1:
        cursor.execute("UPDATE users SET balance = balance + ?, season_earned = season_earned + ? WHERE user_id = ?", (amount, amount, uid))
        text_res = f"✅ Начислено {amount} ⭐ на баланс и в сезонный топ игроку {uid}."
    else:
        cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, uid))
        text_res = f"✅ Начислено {amount} ⭐ ТОЛЬКО на баланс игроку {uid}."
        
    conn.commit()
    await callback.message.edit_text(text_res, reply_markup=get_main_menu())
    try: await bot.send_message(uid, f"💳 Администратор начислил вам {amount} ⭐ на баланс!")
    except: pass

# --- СПИСАНИЕ ЗВЕЗД (ВВОД ID И СУММЫ) ---
@dp.message(BotStates.waiting_for_take_id)
async def process_take_id(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try:
        target_id = int(message.text)
        cursor.execute("SELECT username FROM users WHERE user_id = ?", (target_id,))
        if not cursor.fetchone():
            await message.answer("❌ Такого пользователя нет в базе. Попробуйте еще раз:")
            return
        await state.update_data(target_id=target_id)
        await state.set_state(BotStates.waiting_for_take_amount)
        await message.answer("Введите количество звёзд, которое нужно ЗАБРАТЬ:")
    except ValueError:
        await message.answer("❌ Введите числовой ID:")

@dp.message(BotStates.waiting_for_take_amount)
async def process_take_amount(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try:
        amount = float(message.text.replace(',', '.'))
        if amount <= 0:
            await message.answer("❌ Сумма должна быть больше 0:")
            return
        data = await state.get_data()
        target_id = data['target_id']
        await state.clear()
        
        # Отправляем выбор админу для списания
        kb = [
            [InlineKeyboardButton(text="📉 Баланс + Сезон", callback_data=AdminConfirmTakeCB(target_id=target_id, amount=amount, include_season=1).pack())],
            [InlineKeyboardButton(text="💳 Только с баланса", callback_data=AdminConfirmTakeCB(target_id=target_id, amount=amount, include_season=0).pack())]
        ]
        await message.answer(f"❓ Откуда списать {amount} ⭐ у пользователя {target_id}?", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    except ValueError:
        await message.answer("❌ Введите корректное число:")

# Подтверждение списания админом
@dp.callback_query(AdminConfirmTakeCB.filter())
async def execute_take_stars(callback: CallbackQuery, callback_data: AdminConfirmTakeCB):
    if callback.from_user.id != ADMIN_ID: return
    uid = callback_data.target_id
    amount = callback_data.amount
    inc_season = callback_data.include_season
    
    cursor.execute("SELECT balance, season_earned FROM users WHERE user_id = ?", (uid,))
    res = cursor.fetchone()
    if not res:
        await callback.answer("Пользователь не найден.")
        return
    cur_bal, cur_seas = res
    
    new_bal = max(0.0, cur_bal - amount)
    
    if inc_season == 1:
        new_seas = max(0.0, cur_seas - amount)
        cursor.execute("UPDATE users SET balance = ?, season_earned = ? WHERE user_id = ?", (new_bal, new_seas, uid))
        text_res = f"✅ У ID {uid} списано {amount} ⭐ с баланса и из сезона. Итог — Бал: {round(new_bal,2)}, Сез: {round(new_seas,2)}"
    else:
        cursor.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_bal, uid))
        text_res = f"✅ У ID {uid} списано {amount} ⭐ ТОЛЬКО с баланса. Итог — Бал: {round(new_bal,2)}, Сез: {round(cur_seas,2)}"
        
    conn.commit()
    await callback.message.edit_text(text_res, reply_markup=get_main_menu())
    try: await bot.send_message(uid, f"⚠️ Администратор списал с вашего баланса {amount} ⭐.")
    except: pass


# ================= ПОШАГОВЫЙ СУРС СОЗДАНИЯ ПРОМОКОДОВ ДЛЯ АДМИНИСТРАТОРА =================

# СТЕП 2: Получили имя промокода -> Запрашиваем кол-во активаций
@dp.message(BotStates.waiting_for_promo_name)
async def process_p_name(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    p_code = message.text.strip()
    
    # Экспресс-проверка на уникальность
    cursor.execute("SELECT code FROM promocodes WHERE code = ?", (p_code,))
    if cursor.fetchone():
        await message.answer("❌ Такой промокод уже существует! Введите другое название:")
        return
        
    await state.update_data(p_code=p_code)
    await state.set_state(BotStates.waiting_for_promo_activations)
    await message.answer("Введите максимальное количество активаций промокода (число):")

# СТЕП 3: Кол-во активаций -> Выбор ЦА (Все или Только один)
@dp.message(BotStates.waiting_for_promo_activations)
async def process_p_acts(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try:
        max_acts = int(message.text)
        if max_acts <= 0:
            await message.answer("❌ Количество активаций должно быть больше 0. Введите еще раз:")
            return
            
        await state.update_data(max_acts=max_acts)
        
        kb = [
            [InlineKeyboardButton(text="🌍 Для всех", callback_data=AdminPromoCB(action="target_all").pack())],
            [InlineKeyboardButton(text="👤 Для определенного пользователя", callback_data=AdminPromoCB(action="target_user").pack())]
        ]
        await message.answer("Для кого предназначен этот промокод?", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    except ValueError:
        await message.answer("❌ Введите целое число:")

# СТЕП 4 (Вариант А): Выбрали "Для всех" -> Сразу перекидываем на выбор типа промокода
@dp.callback_query(AdminPromoCB.filter(F.action == "target_all"))
async def process_p_target_all(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.update_data(target_uid=0) # 0 означает для всех
    
    kb = [
        [InlineKeyboardButton(text="💎 На баланс", callback_data=AdminPromoCB(action="type_balance").pack())],
        [InlineKeyboardButton(text="🎁 Ежедневный бонус (Сброс)", callback_data=AdminPromoCB(action="type_bonus").pack())]
    ]
    await callback.message.edit_text("Выберите тип действия промокода:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

# СТЕП 4 (Вариант Б): Выбрали "Для определенного пользователя" -> Показываем список юзеров и ждем ID
@dp.callback_query(AdminPromoCB.filter(F.action == "target_user"))
async def process_p_target_user(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.set_state(BotStates.waiting_for_promo_target_id)
    
    cursor.execute("SELECT user_id, username FROM users")
    users = cursor.fetchall()
    
    text = "📋 Список зарегистрированных пользователей:\n"
    for u in users:
        text += f"• ID: {u[0]} | {u[1]}\n"
    text += "\n👤 Введите Telegram ID пользователя, который сможет его активировать:"
    await callback.message.edit_text(text, reply_markup=get_back_btn("admin_panel"))

# СТЕП 5: Получили ID юзера -> Перекидываем на выбор типа промокода
@dp.message(BotStates.waiting_for_promo_target_id)
async def process_p_target_id_input(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try:
        t_id = int(message.text)
        cursor.execute("SELECT username FROM users WHERE user_id = ?", (t_id,))
        if not cursor.fetchone():
            await message.answer("❌ Такого пользователя нет в базе. Введите корректный ID:")
            return
            
        await state.update_data(target_uid=t_id)
        
        kb = [
            [InlineKeyboardButton(text="💎 На баланс", callback_data=AdminPromoCB(action="type_balance").pack())],
            [InlineKeyboardButton(text="🎁 Ежедневный бонус (Сброс)", callback_data=AdminPromoCB(action="type_bonus").pack())]
        ]
        await message.answer("Выберите тип действия промокода:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    except ValueError:
        await message.answer("❌ Введите числовой Telegram ID:")

# СТЕП 6 (Тип "На Баланс"): Просим ввести сумму
@dp.callback_query(AdminPromoCB.filter(F.action == "type_balance"))
async def process_p_type_bal(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.update_data(p_type="balance")
    await state.set_state(BotStates.waiting_for_promo_reward)
    await callback.message.edit_text("Введите сумму звезд, которая зачислится при активации (например, 5.5):")

# СТЕП 7 (Финальный для "На баланс"): Получили сумму и сохраняем всё в БД
@dp.message(BotStates.waiting_for_promo_reward)
async def process_p_reward_balance(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try:
        reward = float(message.text.replace(',', '.'))
        if reward <= 0:
            await message.answer("❌ Сумма должна быть больше 0. Введите заново:")
            return
            
        data = await state.get_data()
        cursor.execute(
            "INSERT INTO promocodes (code, max_activations, target_user_id, type, reward_amount) VALUES (?, ?, ?, ?, ?)",
            (data['p_code'], data['max_acts'], data['target_uid'], data['p_type'], reward)
        )
        conn.commit()
        
        await message.answer(f"✅ Промокод `{data['p_code']}` на {reward} ⭐ успешно создан!", reply_markup=get_main_menu())
        await state.clear()
    except ValueError:
        await message.answer("❌ Введите корректное число:")

# СТЕП 6 (Финальный для "Ежедневный бонус"): Не требует ввода сумм, сразу сохраняем в БД
@dp.callback_query(AdminPromoCB.filter(F.action == "type_bonus"))
async def process_p_type_bonus(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    data = await state.get_data()
    
    cursor.execute(
        "INSERT INTO promocodes (code, max_activations, target_user_id, type, reward_amount) VALUES (?, ?, ?, ?, ?)",
        (data['p_code'], data['max_acts'], data['target_uid'], "bonus_reset", 0.0)
    )
    conn.commit()
    
    await callback.message.edit_text(f"✅ Промокод `{data['p_code']}` (Сброс бонуса) успешно создан!", reply_markup=get_main_menu())
    await state.clear()

# ================= ЗАПУСК БОТА =================
async def main():
    await check_and_close_season(force=False)
    asyncio.create_task(season_checker_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())