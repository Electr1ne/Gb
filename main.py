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
# Платформа Bothost автоматически подставит токен сюда
BOT_TOKEN = os.getenv("BOT_TOKEN")

try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", 963968579))
except (ValueError, TypeError):
    ADMIN_ID = 963968579  # Твой ID останется тут как запасной, если в панели не указан

# ================= ИНИЦИАЛИЗАЦИЯ =================
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ================= БАЗА ДАННЫХ =================
conn = sqlite3.connect("bot_database.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    balance REAL DEFAULT 0.0,
    last_bonus TEXT,
    is_banned INTEGER DEFAULT 0
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

# ================= СОСТОЯНИЯ FSM =================
class BotStates(StatesGroup):
    waiting_for_screenshot = State()
    waiting_for_custom_stars = State()
    waiting_for_random_stars = State()
    waiting_for_ban_id = State()
    waiting_for_unban_id = State()

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
        [InlineKeyboardButton(text="👤 Профиль", callback_data=MenuCB(target="profile").pack())],
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

@dp.callback_query(MenuCB.filter(F.target == "profile"))
async def view_profile(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (callback.from_user.id,))
    balance = cursor.fetchone()[0]
    
    kb = [
        [InlineKeyboardButton(text="💳 Вывести", callback_data=MenuCB(target="withdraw_select").pack())],
        [InlineKeyboardButton(text="👨‍💻 Поддержка", url="https://t.me/giftezbase_sup")],
        [InlineKeyboardButton(text="⬅ Назад в меню", callback_data=MenuCB(target="main").pack())]
    ]
    if callback.from_user.id == ADMIN_ID:
        kb.insert(2, [InlineKeyboardButton(text="⚙ Админ-Панель", callback_data=MenuCB(target="admin_panel").pack())])

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
    can_bonus = True
    
    if last_bonus:
        last_date = datetime.strptime(last_bonus, "%Y-%m-%d %H:%M:%S")
        if now - last_date < timedelta(hours=24):
            can_bonus = False
            time_left = timedelta(hours=24) - (now - last_date)
            hours, remainder = divmod(time_left.seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            await callback.answer(f"⏳ Бонус уже получен! Приходите через {hours}ч {minutes}м", show_alert=True)
            return

    if can_bonus:
        bonus_amount = round(random.uniform(0.01, 2.0), 2)
        new_balance = balance + bonus_amount
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        
        cursor.execute("UPDATE users SET balance = ?, last_bonus = ? WHERE user_id = ?", (new_balance, now_str, uid))
        conn.commit()
        
        await callback.message.edit_text(
            f"🎁 Поздравляем!\n\nВы получили ежедневный бонус в размере {bonus_amount} ⭐\nВаш новый баланс: {round(new_balance, 2)} ⭐",
            reply_markup=get_back_btn()
        )

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
        
    # Списание средств до отправки
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
    
    # Уведомление администратора
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
        [InlineKeyboardButton(text="🚫 Забанить юзера", callback_data=MenuCB(target="admin_ban").pack()),
         InlineKeyboardButton(text="🟢 Разбанить юзера", callback_data=MenuCB(target="admin_unban").pack())],
        [InlineKeyboardButton(text="⬅ Назад в профиль", callback_data=MenuCB(target="profile").pack())]
    ]
    await callback.message.edit_text("⚙ Панель администратора:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

# Обработка действий вывода админом
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

# БАГ ИСПРАВЛЕН: Теперь выводится единый список с кнопками точечного управления
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
        
        # Если она уже отложена, даем только кнопку закрытия. Если новая — обе кнопки раздельно.
        if row[3] == "delayed":
            kb.append([InlineKeyboardButton(text=f"✅ Выполнить #{row[0]}", callback_data=AdminWdCB(wd_id=row[0], action="done").pack())])
        else:
            kb.append([
                InlineKeyboardButton(text=f"✅ Выполнить #{row[0]}", callback_data=AdminWdCB(wd_id=row[0], action="done").pack()),
                InlineKeyboardButton(text=f"⏳ Отложить #{row[0]}", callback_data=AdminWdCB(wd_id=row[0], action="delay").pack())
            ])
            
    kb.append([InlineKeyboardButton(text="⬅ Назад", callback_data=MenuCB(target="admin_panel").pack())])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

# Обработка скриншотов звезд админом
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
        try:
            await bot.send_message(uid, "❌ Ваш скриншот для получения звезд был отклонен администратором.")
        except: pass
    else:
        kb = [
            [InlineKeyboardButton(text="1 звезда (Дефолт)", callback_data=AdminGiveStarCB(req_id=req_id, mode="default").pack())],
            [InlineKeyboardButton(text="Своя сумма", callback_data=AdminGiveStarCB(req_id=req_id, mode="custom").pack())],
            [InlineKeyboardButton(text="Рандом", callback_data=AdminGiveStarCB(req_id=req_id, mode="random").pack())]
        ]
        await callback.message.edit_caption(caption="Сколько звезд начислить за этот скриншот?", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(AdminGiveStarCB.filter())
async def admin_give_star_modes(callback: CallbackQuery, callback_data: AdminGiveStarCB, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    req_id = callback_data.req_id
    mode = callback_data.mode
    
    cursor.execute("SELECT user_id FROM review_stars WHERE id = ?", (req_id,))
    uid = cursor.fetchone()[0]
    
    if mode == "default":
        cursor.execute("UPDATE review_stars SET status = 'approved' WHERE id = ?", (req_id,))
        cursor.execute("UPDATE users SET balance = balance + 1.0 WHERE user_id = ?", (uid,))
        conn.commit()
        await callback.message.edit_caption(caption="🟢 Начислена 1 звезда.")
        try:
            await bot.send_message(uid, "🟢 Ваш скриншот одобрен! Вам начислена 1.0 ⭐")
        except: pass
        
    elif mode == "custom":
        await state.set_state(BotStates.waiting_for_custom_stars)
        await state.update_data(req_id=req_id, user_id=uid)
        
        kb = [[InlineKeyboardButton(text="⬅ Отмена (В меню)", callback_data=MenuCB(target="main").pack())]]
        await bot.send_message(ADMIN_ID, "Введите сумму звезд для начисления (можно дробные через точку, например 1.5):", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
        
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
        cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, uid))
        conn.commit()
        await message.answer(f"🟢 Успешно начислено {amount} ⭐", reply_markup=get_main_menu())
        try:
            await bot.send_message(uid, f"🟢 Ваш скриншот одобрен! Вам начислено {amount} ⭐")
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
        cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, uid))
        conn.commit()
        await message.answer(f"🟢 Рандом сработал! Начислено {amount} ⭐", reply_markup=get_main_menu())
        try:
            await bot.send_message(uid, f"🟢 Ваш скриншот одобрен! Вам начислено {amount} ⭐")
        except: pass
        await state.clear()
    except Exception:
        kb = [[InlineKeyboardButton(text="⬅ Отмена (В меню)", callback_data=MenuCB(target="main").pack())]]
        await message.answer("❌ Ошибка ввода. Введите диапазон в формате 'от-до' (например, 0.2-2.5):", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

# ================= БАН-СИСТЕМА =================

@dp.callback_query(MenuCB.filter(F.target == "admin_ban"))
async def admin_ban_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.set_state(BotStates.waiting_for_ban_id)
    
    cursor.execute("SELECT user_id, username FROM users WHERE is_banned = 0")
    users = cursor.fetchall()
    
    user_list_text = "📋 Список зарегистрированных пользователей:\n"
    if users:
        for u in users:
            user_list_text += f"• ID: {u[0]} | {u[1]}\n"
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
        for u in users:
            user_list_text += f"• ID: {u[0]} | {u[1]}\n"
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

# ================= ЗАПУСК БОТА =================
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())