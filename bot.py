# -*- coding: utf-8 -*-
import telebot
from telebot import types
import sqlite3
import threading
import os
import datetime
import openpyxl
from openpyxl.utils import get_column_letter

# --- Конфигурация (предполагается, что эти переменные определены в config.py) ---
from config import BOT_TOKEN, ADMIN_GROUP_ID, BONUS_PERCENT, MAX_DISCOUNT, REFERRAL_BONUS, DB_PATH

bot = telebot.TeleBot(BOT_TOKEN)

# Локальное хранилище для потоков для управления соединениями с БД
local_storage = threading.local()

def get_conn():
    """Возвращает соединение с базой данных для текущего потока."""
    if not hasattr(local_storage, 'conn'):
        local_storage.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    return local_storage.conn

# --- Инициализация базы данных ---
def init_db(sql_file):
    """Инициализирует базу данных из SQL-скрипта, если файл БД не существует."""
    try:
        if not os.path.exists(DB_PATH):
            with open(sql_file, 'r', encoding='utf-8') as f:
                sql_script = f.read()
            
            with get_conn() as conn:
                conn.executescript(sql_script)
            print(f"✅ Database tables created from {sql_file}.")
        else:
            print("✅ Database file already exists.")
    except FileNotFoundError:
        print(f"❌ Error: Database schema file '{sql_file}' not found.")
        print("Please ensure models.sql is in the same directory.")
    except Exception as e:
        print(f"❌ An error occurred during database initialization: {e}")

# --- Вспомогательные функции для работы с БД ---
def get_user(telegram_id):
    """Получает данные пользователя, включая количество рефералов."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT u.*, 
                   (SELECT COUNT(*) FROM users WHERE referrer_id = u.id) AS referrals
            FROM users u WHERE telegram_id = ?
        """, (telegram_id,))
        return cur.fetchone()

def get_user_id(telegram_id):
    """Получает ID пользователя по его Telegram ID."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE telegram_id = ?", (telegram_id,))
        row = cur.fetchone()
        return row[0] if row else None

def add_user_full(telegram_id, name, phone=None, email=None, referrer_telegram_id=None):
    """Добавляет нового пользователя в базу данных."""
    with get_conn() as conn:
        cur = conn.cursor()
        referrer_id = None
        if referrer_telegram_id:
            cur.execute("SELECT id FROM users WHERE telegram_id = ?", (referrer_telegram_id,))
            r = cur.fetchone()
            if r:
                referrer_id = r[0]
        cur.execute("""
            INSERT OR IGNORE INTO users (telegram_id, name, phone, email, referrer_id)
            VALUES (?, ?, ?, ?, ?)
        """, (telegram_id, name, phone, email, referrer_id))
        conn.commit()

# --- Обработчики команд и сообщений ---
@bot.message_handler(commands=["start"])
def start(message):
    """Обработчик команды /start."""
    telegram_id = str(message.chat.id)
    referrer_telegram_id = None
    if message.text.startswith("/start "):
        try:
            referrer_telegram_id = message.text.split(" ", 1)[1]
            
            # --- Защита от абуза ---
            # 1. Запрет на самоприглашение
            if referrer_telegram_id == telegram_id:
                bot.send_message(telegram_id, "Ой, хитрец! 😜 Сам себя не пригласишь. Давай регистрироваться без трюков.")
                referrer_telegram_id = None
            # 2. Проверка, что реферер существует в базе
            elif not get_user(referrer_telegram_id):
                bot.send_message(telegram_id, "Привет! Похоже, ссылка недействительна или её отправил не наш клиент.")
                referrer_telegram_id = None
            else:
                bot.send_message(telegram_id, "🤝 Привет! Ты перешел по реферальной ссылке.")
        except IndexError:
            pass
    
    user = get_user(telegram_id)
    if user:
        bot.send_message(telegram_id, f"Ты уже зарегистрирован, {user[2]} ☕", reply_markup=main_keyboard())
    else:
        msg = bot.send_message(telegram_id, "👋 Привет! Как тебя зовут?")
        bot.register_next_step_handler(msg, finish_registration, referrer_telegram_id)

def finish_registration(message, referrer_telegram_id=None):
    """Завершает регистрацию нового пользователя."""
    name = message.text.strip()
    if len(name) < 2:
        msg = bot.send_message(message.chat.id, "Имя слишком короткое. Попробуй ещё раз.")
        bot.register_next_step_handler(msg, finish_registration, referrer_telegram_id)
        return
    
    telegram_id = str(message.chat.id)
    add_user_full(telegram_id, name, referrer_telegram_id=referrer_telegram_id)
    
    # Если есть реферер, начисляем ему бонус
    if referrer_telegram_id and get_user(referrer_telegram_id):
        update_points(referrer_telegram_id, REFERRAL_BONUS)
        bot.send_message(referrer_telegram_id, f"🎉 Отличная работа! Твой друг {name} зарегистрировался, и ты получил {REFERRAL_BONUS} баллов!")
    
    bot.send_message(
        telegram_id,
        f"🎉 Добро пожаловать, {name}!\nТеперь ты можешь заказывать кофе ☕",
        reply_markup=main_keyboard()
    )
    # Отправляем анимацию после регистрации
    # Замените этот ID на ID вашего стикера или GIF
    # Пример: bot.send_sticker(telegram_id, 'CAACAgIAAxkBAAEEiAABwO-U8f0tK_c')
    bot.send_message(
        telegram_id,
        "📌 <b>Как сделать заказ:</b>\n\n"
        "1️⃣ Нажми «📋 Меню», чтобы посмотреть напитки\n"
        "2️⃣ Напиши название и размер, например: <i>Латте 0.3л</i>\n"
        "3️⃣ Нажми «🛒 Корзина», чтобы проверить заказ\n"
        "4️⃣ Нажми «✅ Оформить заказ» — и приходи на точку через 5–10 минут ☕",
        parse_mode="HTML"
    )

# --- Клавиатуры ---
def main_keyboard():
    """Основная клавиатура для клиента."""
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📋 Меню")
    kb.add("🛒 Корзина")
    kb.add("🎯 Баллы")
    kb.add("🔗 Реферальная ссылка")
    kb.add("🛠 Техподдержка")
    return kb

def handle_add_more_keyboard():
    """Клавиатура для добавления новых позиций в корзину."""
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("➕ Добавить ещё", "✅ Перейти к оформлению")
    return kb

@bot.message_handler(func=lambda m: m.text == "📋 Меню")
def show_menu(message):
    """Показывает клиенту текущее меню."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT category, name, size, price, quantity FROM stock ORDER BY category, name, CAST(size AS REAL)")
        rows = cur.fetchall()
    if not rows:
        bot.send_message(message.chat.id, "📭 Меню пустое")
        return

    text = "📋 <b>Меню:</b>\n"
    current_cat = None
    for cat, name, size, price, qty in rows:
        if cat != current_cat:
            text += f"\n🔸 <b>{cat}</b>\n"
            current_cat = cat
        text += f"• {name} {size}л — {price}₽ (осталось: {qty})\n"

    bot.send_message(message.chat.id, text, parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "🛒 Корзина")
def show_cart(message):
    """Показывает содержимое корзины клиента."""
    telegram_id = str(message.chat.id)
    user = get_user(telegram_id)
    if not user:
        bot.send_message(telegram_id, "Пожалуйста, сначала зарегистрируйтесь, чтобы использовать корзину. Нажмите /start.")
        return

    items = get_cart_items(telegram_id)
    if not items:
        bot.send_message(telegram_id, "Корзина пуста 😢")
        return
    
    total = sum(it["price"] * it["qty"] for it in items)
    points = user[5] or 0
    referrals = user[-1] or 0
    
    # Проверяем, может ли пользователь активировать 15% скидку
    is_challenge_eligible = points >= 500 and referrals >= 10
    
    kb = types.InlineKeyboardMarkup()
    
    if is_challenge_eligible:
        kb.add(types.InlineKeyboardButton("🎁 Активировать 15% скидку", callback_data="activate_15_percent"))
    
    # Стандартная скидка (до 5% баллами)
    standard_discount = calc_discount(total, points)
    final_total_standard = total - standard_discount
    
    kb.add(types.InlineKeyboardButton("✅ Оформить заказ", callback_data="confirm_standard"))
    kb.add(types.InlineKeyboardButton("🧹 Очистить корзину", callback_data="clear"))

    bot.send_message(
        telegram_id,
        f"🛒 <b>Твоя корзина:</b>\n{format_cart_lines(items)}\n\n"
        f"💰 Итого: {total}₽\n"
        f"🎁 Стандартная скидка: {standard_discount}₽\n"
        f"📦 К оплате (стандарт): {final_total_standard}₽",
        reply_markup=kb,
        parse_mode="HTML"
    )

@bot.callback_query_handler(func=lambda call: call.data == "activate_15_percent")
def activate_15_percent(call):
    """Обрабатывает активацию 15% скидки."""
    telegram_id = str(call.message.chat.id)
    user = get_user(telegram_id)
    items = get_cart_items(telegram_id)
    
    if not user or not items:
        bot.answer_callback_query(call.id, "Корзина пуста или пользователь не найден.")
        return
        
    points = user[5] or 0
    referrals = user[-1] or 0

    if not (points >= 500 and referrals >= 10):
        bot.answer_callback_query(call.id, "❌ Вы не соответствуете условиям для скидки 15%.")
        return

    total = sum(it["price"] * it["qty"] for it in items)
    discount = int(total * 0.15)
    final_total = total - discount
    
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✅ Подтвердить заказ", callback_data="confirm_challenge"))
    kb.add(types.InlineKeyboardButton("❌ Отмена", callback_data="clear"))
    
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=(
            f"🎉 <b>Отличный выбор!</b>\n\n"
            f"🎁 Активирована скидка <b>15%</b>.\n"
            f"💰 Итого: {total}₽\n"
            f"📉 Скидка: {discount}₽\n"
            f"📦 К оплате: {final_total}₽\n\n"
            "⚠️ <b>Внимание:</b> Для получения этой скидки будет списано 500 баллов."
        ),
        reply_markup=kb,
        parse_mode="HTML"
    )

@bot.callback_query_handler(func=lambda call: call.data == "confirm_standard")
def confirm_order_standard(call):
    """Обрабатывает подтверждение заказа со стандартной скидкой."""
    telegram_id = str(call.message.chat.id)
    user = get_user(telegram_id)
    items = get_cart_items(telegram_id)
    
    if not user or not items:
        bot.answer_callback_query(call.id, "Корзина пуста или пользователь не найден.")
        return
    
    points = user[5] or 0
    total = sum(it["price"] * it["qty"] for it in items)
    discount = calc_discount(total, points)
    final_total = total - discount
    
    process_order(telegram_id, user, items, final_total, discount)
    bot.edit_message_reply_markup(call.message.chat.id, call.message.id, reply_markup=None)

@bot.callback_query_handler(func=lambda call: call.data == "confirm_challenge")
def confirm_order_challenge(call):
    """Обрабатывает подтверждение заказа с 15% скидкой (челлендж)."""
    telegram_id = str(call.message.chat.id)
    user = get_user(telegram_id)
    items = get_cart_items(telegram_id)

    if not user or not items:
        bot.answer_callback_query(call.id, "Корзина пуста или пользователь не найден.")
        return

    points = user[5] or 0
    if points < 500:
        bot.answer_callback_query(call.id, "❌ Недостаточно баллов для активации скидки.")
        return
        
    total = sum(it["price"] * it["qty"] for it in items)
    discount = int(total * 0.15)
    final_total = total - discount
    
    process_order(telegram_id, user, items, final_total, discount, points_spent=500)
    bot.edit_message_reply_markup(call.message.chat.id, call.message.id, reply_markup=None)

def process_order(telegram_id, user, items, final_total, discount, points_spent=0):
    """Общая функция для обработки и завершения заказа."""
    for it in items:
        if not is_available(it["name"], it["qty"]):
            bot.send_message(telegram_id, f"❌ Извините, {it['name']} больше нет в наличии.")
            return
            
    for it in items:
        reduce_stock(it["name"], it["qty"])
    
    earned = int(final_total * BONUS_PERCENT)
    update_points(telegram_id, earned - points_spent)
    clear_cart_db(telegram_id)
    
    order_id = create_order(telegram_id, items, final_total)
    
    items_text = "; ".join([f"{it['name']} x{it['qty']}" for it in items])
    bot.send_message(
        telegram_id,
        f"✅ Заказ №{order_id} оформлен!\n"
        f"🧾 Позиции: {items_text}\n"
        f"💳 К оплате: {final_total}₽\n"
        f"🎯 Баллы: +{earned}, -{points_spent}\n\n"
        f"☕ Заказ готовится! Подойди на точку через 5–10 минут, чтобы забрать его.",
        reply_markup=main_keyboard()
    )
    # Отправляем анимацию после успешного заказа
    # Замените этот ID на ID вашего стикера или GIF
    # Пример: bot.send_sticker(telegram_id, 'CAACAgIAAxkBAAEEiAABwO-U8f0tK_c')
    
    ready_button = types.InlineKeyboardMarkup()
    ready_button.add(types.InlineKeyboardButton("✅ Готов", callback_data=f"ready_{telegram_id}_{order_id}"))
    ready_button.add(types.InlineKeyboardButton("💬 Связаться с клиентом", callback_data=f"contact_{telegram_id}_{order_id}"))
    bot.send_message(
        ADMIN_GROUP_ID,
        f"📦 Новый заказ №{order_id}\n👤 {user[2]}\n🧾 {items_text}\n💰 К оплате: {final_total}₽",
        reply_markup=ready_button
    )

@bot.callback_query_handler(func=lambda call: call.data == "clear")
def clear_cart_handler(call):
    """Очищает корзину клиента."""
    telegram_id = str(call.message.chat.id)
    clear_cart_db(telegram_id)
    bot.answer_callback_query(call.id, "Корзина очищена 🗑️")
    bot.send_message(telegram_id, "Твоя корзина теперь пуста.", reply_markup=main_keyboard())

@bot.callback_query_handler(func=lambda call: call.data.startswith("ready_"))
def mark_ready(call):
    """Обработчик для кнопки 'Готов' в админ-группе."""
    _, telegram_id, order_id = call.data.split("_", 2)
    bot.send_message(int(telegram_id), f"✅ Твой заказ №{order_id} готов! Забери его на точке ☕")
    bot.answer_callback_query(call.id, "Клиент уведомлён ✅")
    bot.edit_message_reply_markup(call.message.chat.id, call.message.id, reply_markup=None)

@bot.callback_query_handler(func=lambda call: call.data.startswith("contact_"))
def contact_user_handler(call):
    """Обработчик для кнопки 'Связаться с клиентом'."""
    try:
        _, telegram_id, order_id = call.data.split("_", 2)
        msg = bot.send_message(call.message.chat.id, f"Напиши сообщение для клиента:")
        bot.register_next_step_handler(msg, send_admin_message, telegram_id, order_id)
    except Exception as e:
        bot.send_message(call.message.chat.id, f"❌ Ошибка при попытке связаться: {e}")

def send_admin_message(message, telegram_id, order_id):
    """Отправляет сообщение от админа клиенту."""
    try:
        user_msg = message.text
        bot.send_message(telegram_id, f"📝 Сообщение от администратора по заказу №{order_id}:\n\n{user_msg}")
        bot.send_message(message.chat.id, "✅ Сообщение успешно отправлено клиенту.")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Не удалось отправить сообщение: {e}")

@bot.message_handler(func=lambda m: m.text == "🎯 Баллы")
def show_points(message):
    """Показывает информацию о баллах и скидках."""
    telegram_id = str(message.chat.id)
    user = get_user(telegram_id)
    if not user:
        bot.send_message(telegram_id, "Пожалуйста, сначала зарегистрируйтесь, чтобы посмотреть баллы. Нажмите /start.")
        return
        
    points = user[5] or 0
    referrals = user[-1] or 0

    text = (
        "🎯 <b>Программа лояльности:</b>\n\n"
        "За каждый заказ ты получаешь баллы. Ими можно оплатить до <b>5%</b> от суммы заказа!\n"
        f"Твои текущие баллы: <b>{points}</b>\n\n"
        "🔗 <b>Челлендж «15% скидка»</b>:\n"
        "Чтобы получить скидку в 15% на любой заказ, тебе нужно:\n"
        f"• Накопить <b>500</b> баллов (у тебя сейчас: {points})\n"
        f"• Пригласить <b>10</b> друзей (у тебя сейчас: {referrals})\n\n"
        "<i>За активацию скидки 15% будет списано 500 баллов.</i>"
    )
    bot.send_message(telegram_id, text, parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "🔗 Реферальная ссылка")
def show_referral(message):
    """Показывает реферальную ссылку пользователя."""
    telegram_id = str(message.chat.id)
    bot.send_message(
        telegram_id, 
        f"🔗 <b>Приглашай друзей и получай баллы!</b>\n\n"
        "Когда твой друг зарегистрируется по этой ссылке, ты получишь бонусные баллы.\n"
        f"Твоя реферальная ссылка:\nhttps://t.me/{bot.get_me().username}?start={telegram_id}",
        parse_mode="HTML"
    )

@bot.message_handler(func=lambda m: m.text == "🛠 Техподдержка")
def support_info(message):
    """Предоставляет контакт техподдержки."""
    bot.send_message(
        message.chat.id,
        "🛠 Если у тебя возникли вопросы — напиши нам:\n@tamiklung\nМы всегда на связи!"
    )

@bot.message_handler(func=lambda m: m.text == "➕ Добавить ещё")
def add_more(message):
    """Предлагает добавить еще напитки в корзину."""
    bot.send_message(
        message.chat.id,
        "Отлично! Напиши название следующего напитка или выбери из меню 📋",
        reply_markup=main_keyboard()
    )

@bot.message_handler(func=lambda m: m.text == "✅ Перейти к оформлению")
def go_to_checkout(message):
    """Переводит пользователя к оформлению заказа."""
    show_cart(message)

# --- Функции для работы с корзиной и заказами ---
def add_to_cart_db(telegram_id, item):
    """Добавляет позицию в корзину в БД."""
    user_id = get_user_id(telegram_id)
    if not user_id: return
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO cart (user_id, name, price, qty)
            VALUES (?, ?, ?, ?)
        """, (user_id, item["name"], item["price"], item["qty"]))
        conn.commit()

def get_cart_items(telegram_id):
    """Получает все позиции из корзины пользователя."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT name, price, qty
            FROM cart
            WHERE user_id = (SELECT id FROM users WHERE telegram_id = ?)
        """, (telegram_id,))
        return [{"name": r[0], "price": r[1], "qty": r[2]} for r in cur.fetchall()]

def clear_cart_db(telegram_id):
    """Очищает корзину пользователя в БД."""
    with get_conn() as conn:
        conn.execute("DELETE FROM cart WHERE user_id = (SELECT id FROM users WHERE telegram_id = ?)", (telegram_id,))
        conn.commit()

def create_order(telegram_id, items, total):
    """Создает новый заказ в БД."""
    user_id = get_user_id(telegram_id)
    if not user_id: return None
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO orders (user_id, total, created_at) VALUES (?, ?, ?)", (user_id, total, datetime.datetime.now()))
        order_id = cur.lastrowid
        for it in items:
            cur.execute(
                "INSERT INTO order_items (order_id, name, price, qty) VALUES (?, ?, ?, ?)",
                (order_id, it["name"], it["price"], it["qty"])
            )
        conn.commit()
        return order_id

def update_points(telegram_id, delta):
    """Обновляет баллы пользователя."""
    with get_conn() as conn:
        conn.execute("UPDATE users SET points = points + ? WHERE telegram_id = ?", (delta, telegram_id))
        conn.commit()

def calc_discount(total, points):
    """Рассчитывает скидку на основе баллов."""
    return min(points, int(total * 0.05))

def format_cart_lines(items):
    """Форматирует список позиций в корзине."""
    return "\n".join([f"• {it['name']} x{it['qty']} — {it['price']}₽" for it in items])

def is_available(full_name, qty=1):
    """Проверяет наличие товара на складе."""
    return get_stock_by_fullname(full_name) >= qty

def get_stock_by_fullname(full_name):
    """Получает количество товара по его полному названию."""
    if not full_name.endswith("л"):
        return 0
    name, size_l = full_name.rsplit(" ", 1)
    size = size_l.replace("л", "")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT quantity FROM stock WHERE name=? AND size=?", (name, size))
        row = cur.fetchone()
        return row[0] if row else 0

def reduce_stock(full_name, qty):
    """Уменьшает количество товара на складе."""
    if not full_name.endswith("л"):
        return
    name, size_l = full_name.rsplit(" ", 1)
    size = size_l.replace("л", "")
    with get_conn() as conn:
        conn.execute("""
            UPDATE stock SET quantity = quantity - ?
            WHERE name = ? AND size = ? AND quantity >= ?
        """, (qty, name, size, qty))
        conn.commit()
        cur = conn.cursor()
        cur.execute("SELECT quantity FROM stock WHERE name=? AND size=?", (name, size))
        row = cur.fetchone()
        if row and row[0] < 3:
            bot.send_message(ADMIN_GROUP_ID, f"⚠️ Остаток низкий: {name} {size}л — {row[0]} шт")

# --- Обработчики для админ-панели ---
@bot.message_handler(commands=["admin"])
def admin_panel(message):
    """Показывает главное меню админ-панели."""
    if message.chat.type not in ["group", "supergroup"] or message.chat.id != ADMIN_GROUP_ID:
        return
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📊 Статистика", "⚠️ Низкие остатки")
    kb.add("⚙️ Управление товарами")
    kb.add("🧾 Последние заказы")
    bot.send_message(message.chat.id, "🔥 Админ-панель активна. Выбери команду:", reply_markup=kb)

@bot.message_handler(func=lambda m: m.text == "⚙️ Управление товарами")
def manage_items_menu(message):
    """Показывает меню управления товарами."""
    if message.chat.id != ADMIN_GROUP_ID:
        return
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("➕ Добавить вручную", callback_data="admin_add_manual"))
    kb.add(types.InlineKeyboardButton("⬆️ Загрузить из Excel", callback_data="admin_add_excel"))
    kb.add(types.InlineKeyboardButton("✏️ Изменить количество", callback_data="admin_update_qty"))
    kb.add(types.InlineKeyboardButton("🗑️ Удалить", callback_data="admin_delete"))
    bot.send_message(message.chat.id, "Выбери действие для управления товарами:", reply_markup=kb)

@bot.callback_query_handler(func=lambda call: call.data == "admin_add_manual")
def admin_add_prompt_callback(call):
    """Запрашивает данные для добавления товара вручную."""
    msg = bot.send_message(call.message.chat.id, "✏️ Введи позицию одной строкой:\nКатегория;Название;Размер;Цена;Остаток")
    bot.register_next_step_handler(msg, apply_new_item)

def apply_new_item(message):
    """Добавляет новый товар в базу данных."""
    if message.chat.id != ADMIN_GROUP_ID:
        return
    try:
        cat, name, size, price, qty = [x.strip() for x in message.text.split(";")]
        price = int(price); qty = int(qty)
        with get_conn() as conn:
            conn.execute("INSERT OR REPLACE INTO stock (category, name, size, price, quantity) VALUES (?, ?, ?, ?, ?)",
                         (cat, name, size, price, qty))
            conn.commit()
        bot.send_message(message.chat.id, f"✅ Добавлено: {cat} | {name} {size}л — {price}₽ (остаток {qty})")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Ошибка: {e}\nПример: Classic;Латте;0.3;250;10")

@bot.callback_query_handler(func=lambda call: call.data == "admin_add_excel")
def admin_add_excel_prompt(call):
    """Запрашивает Excel-файл для загрузки меню."""
    msg = bot.send_message(call.message.chat.id, "Пожалуйста, отправь Excel-файл с меню.\nФормат: <b>Категория | Название | Размер | Цена | Остаток</b>", parse_mode="HTML")
    bot.register_next_step_handler(msg, process_excel_upload)

def process_excel_upload(message):
    """Обрабатывает загруженный Excel-файл и обновляет меню."""
    if message.chat.id != ADMIN_GROUP_ID:
        return
    if not message.document or not message.document.file_name.endswith(('.xlsx', '.xls')):
        bot.send_message(message.chat.id, "❌ Это не Excel-файл. Попробуй ещё раз.")
        return
    
    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        # Временно сохраняем файл
        temp_file_path = f"/tmp/{message.document.file_id}.xlsx"
        with open(temp_file_path, 'wb') as f:
            f.write(downloaded_file)
            
        workbook = openpyxl.load_workbook(temp_file_path)
        sheet = workbook.active
        
        items_added = 0
        
        with get_conn() as conn:
            cur = conn.cursor()
            for row in sheet.iter_rows(min_row=2, values_only=True):
                if len(row) >= 5:
                    cat, name, size, price, qty = row[:5]
                    try:
                        price = int(price)
                        qty = int(qty)
                        cur.execute("INSERT OR REPLACE INTO stock (category, name, size, price, quantity) VALUES (?, ?, ?, ?, ?)",
                                    (cat.strip(), name.strip(), str(size).strip(), price, qty))
                        items_added += 1
                    except (ValueError, TypeError):
                        continue # Пропускаем некорректные строки
            conn.commit()
        
        os.remove(temp_file_path)
        bot.send_message(message.chat.id, f"✅ Успешно обновлено {items_added} позиций из файла.")
        
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Произошла ошибка при обработке файла: {e}")

@bot.callback_query_handler(func=lambda call: call.data == "admin_update_qty")
def admin_update_qty_prompt(call):
    """Запрашивает данные для изменения количества товара."""
    msg = bot.send_message(call.message.chat.id, "✏️ Введи название, размер и новое количество через запятую:\nНапример: <i>Латте 0.3, 15</i>", parse_mode="HTML")
    bot.register_next_step_handler(msg, apply_update_qty)

def apply_update_qty(message):
    """Обновляет количество товара в базе данных."""
    if message.chat.id != ADMIN_GROUP_ID:
        return
    try:
        parts = [p.strip() for p in message.text.split(",")]
        if len(parts) != 2:
            raise ValueError
        
        name_size_str, new_qty_str = parts
        name_parts = name_size_str.rsplit(" ", 1)
        if len(name_parts) != 2:
            raise ValueError
        
        name, size = name_parts[0], name_parts[1].replace("л", "")
        new_qty = int(new_qty_str)
        
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE stock SET quantity = ? WHERE name = ? AND size = ?", (new_qty, name, size))
            conn.commit()
            if cur.rowcount > 0:
                bot.send_message(message.chat.id, f"✅ Остаток для {name} {size}л обновлен до {new_qty}.")
            else:
                bot.send_message(message.chat.id, f"❌ Товар '{name} {size}л' не найден.")
    except Exception as e:
        bot.send_message(message.chat.id, "❌ Неверный формат. Попробуй еще раз.")

@bot.callback_query_handler(func=lambda call: call.data == "admin_delete")
def admin_delete_prompt(call):
    """Запрашивает данные для удаления товара."""
    msg = bot.send_message(call.message.chat.id, "🗑️ Введи название и размер для удаления:\nНапример: <i>Латте 0.3л</i>", parse_mode="HTML")
    bot.register_next_step_handler(msg, apply_delete_item)

def apply_delete_item(message):
    """Удаляет товар из базы данных."""
    if message.chat.id != ADMIN_GROUP_ID:
        return
    try:
        name_size_str = message.text.strip()
        name_parts = name_size_str.rsplit(" ", 1)
        if len(name_parts) != 2:
            raise ValueError
        
        name, size = name_parts[0], name_parts[1].replace("л", "")
        
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM stock WHERE name = ? AND size = ?", (name, size))
            conn.commit()
            if cur.rowcount > 0:
                bot.send_message(message.chat.id, f"✅ Товар '{name} {size}л' успешно удален.")
            else:
                bot.send_message(message.chat.id, f"❌ Товар '{name} {size}л' не найден.")
    except Exception as e:
        bot.send_message(message.chat.id, "❌ Неверный формат. Попробуй еще раз.")

@bot.message_handler(func=lambda m: m.text == "🧾 Последние заказы")
def show_recent_orders(message):
    """Показывает список последних 10 заказов."""
    if message.chat.id != ADMIN_GROUP_ID:
        return
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT o.id, u.name, o.total, o.created_at
            FROM orders o JOIN users u ON o.user_id = u.id
            ORDER BY o.created_at DESC LIMIT 10
        """)
        rows = cur.fetchall()
    if not rows:
        bot.send_message(message.chat.id, "🧾 Нет последних заказов.")
        return
    text = "🧾 <b>Последние 10 заказов:</b>\n"
    for order_id, user_name, total, created_at in rows:
        text += f"• №{order_id} от {user_name} ({created_at}) — {total}₽\n"
    bot.send_message(message.chat.id, text, parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "📊 Статистика")
def show_stats(message):
    """Показывает общую статистику по заказам и пользователям."""
    if message.chat.id != ADMIN_GROUP_ID:
        return
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*), SUM(total) FROM orders")
        count, revenue = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM users")
        users = cur.fetchone()[0]
    text = f"📊 <b>Статистика:</b>\nЗаказов: {count or 0}\nВыручка: {revenue or 0}₽\nПользователей: {users or 0}"
    bot.send_message(message.chat.id, text, parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "⚠️ Низкие остатки")
def show_low_stock(message):
    """Показывает товары с низким остатком."""
    if message.chat.id != ADMIN_GROUP_ID:
        return
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT category, name, size, quantity FROM stock WHERE quantity < 3 ORDER BY quantity")
        rows = cur.fetchall()
    if not rows:
        bot.send_message(message.chat.id, "Все остатки в норме ✅")
        return
    text = "⚠️ <b>Низкие остатки:</b>\n"
    for cat, name, size, qty in rows:
        text += f"• {cat}: {name} {size}л — {qty} шт\n"
    bot.send_message(message.chat.id, text, parse_mode="HTML")

# --- Общий обработчик текстовых сообщений ---
@bot.message_handler(func=lambda m: True)
def handle_text(message):
    """Общий обработчик текстовых сообщений для добавления в корзину."""
    telegram_id = str(message.chat.id)
    text = message.text.strip()
    
    if text.startswith('/') or not get_user(telegram_id) or text in ["➕ Добавить ещё", "✅ Перейти к оформлению"]:
        return

    try:
        parts = text.rsplit(" ", 1)
        if len(parts) != 2:
            raise ValueError("Неверный формат")
        
        name, size_str = parts
        size = size_str.replace("л", "").strip()
        float(size) # Проверяем, что размер - число
    except (ValueError, IndexError):
        bot.send_message(telegram_id, "❌ Неверный формат. Попробуй ввести название и объем, например: <i>Латте 0.3л</i>", parse_mode="HTML")
        return

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT price, quantity FROM stock WHERE LOWER(name)=LOWER(?) AND size=?", (name, size))
        row = cur.fetchone()

    if not row:
        bot.send_message(telegram_id, "❌ Такой напиток не найден. Попробуй ещё раз.")
        return

    price, qty = row
    if qty < 1:
        bot.send_message(telegram_id, f"❌ {name} {size}л закончился.")
        return

    item = {"name": f"{name} {size}л", "price": price, "qty": 1}
    add_to_cart_db(telegram_id, item)

    bot.send_message(
        telegram_id,
        f"✅ {item['name']} добавлен в корзину.",
        reply_markup=handle_add_more_keyboard()
    )

# 🚀 Запуск бота
if __name__ == "__main__":
    init_db('models.sql')
    print("✅ Бот запущен. Готов принимать заказы ☕")
    bot.infinity_polling()
