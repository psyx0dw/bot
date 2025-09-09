# coding: utf-8 -*-
import telebot
from telebot import types
import sqlite3
import threading
import os
import datetime
import openpyxl
from openpyxl.utils import get_column_letter
import time

# --- Конфигурация (предполагается, что эти переменные определены в config.py) ---
from config import BOT_TOKEN, ADMIN_GROUP_ID, BONUS_PERCENT, MAX_DISCOUNT, REFERRAL_BONUS, DB_PATH

bot = telebot.TeleBot(BOT_TOKEN)

# Локальное хранилище для потоков для управления соединениями с БД
local_storage = threading.local()

class DBManager:
    """Класс для управления всеми операциями с базой данных."""
    def __init__(self, db_path):
        self.db_path = db_path
        self._init_db_if_not_exists()

    def get_conn(self):
        """Возвращает соединение с базой данных для текущего потока."""
        if not hasattr(local_storage, 'conn'):
            local_storage.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        return local_storage.conn

    def _init_db_if_not_exists(self, sql_file="models.sql"):
        """Инициализирует базу данных из SQL-скрипта, если файл БД не существует."""
        try:
            if not os.path.exists(self.db_path):
                with open(sql_file, 'r', encoding='utf-8') as f:
                    sql_script = f.read()
                
                with self.get_conn() as conn:
                    conn.executescript(sql_script)
                print(f"✅ Database tables created from {sql_file}.")
            else:
                print("✅ Database file already exists.")
        except FileNotFoundError:
            print(f"❌ Error: Database schema file '{sql_file}' not found.")
            print("Please ensure models.sql is in the same directory.")
        except Exception as e:
            print(f"❌ An error occurred during database initialization: {e}")

    def get_user(self, telegram_id):
        """Получает данные пользователя, включая количество рефералов."""
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT u.*,
                       (SELECT COUNT(*) FROM users WHERE referrer_id = u.id) AS referrals
                FROM users u WHERE telegram_id = ?
            """, (telegram_id,))
            return cur.fetchone()

    def get_user_id(self, telegram_id):
        """Получает ID пользователя по его Telegram ID."""
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM users WHERE telegram_id = ?", (telegram_id,))
            row = cur.fetchone()
            return row[0] if row else None

    def add_user_full(self, telegram_id, name, referrer_telegram_id=None):
        """Добавляет нового пользователя в базу данных."""
        with self.get_conn() as conn:
            cur = conn.cursor()
            referrer_id = None
            if referrer_telegram_id:
                cur.execute("SELECT id FROM users WHERE telegram_id = ?", (referrer_telegram_id,))
                r = cur.fetchone()
                if r:
                    referrer_id = r[0]
            cur.execute("""
                INSERT OR IGNORE INTO users (telegram_id, name, referrer_id)
                VALUES (?, ?, ?)
            """, (telegram_id, name, referrer_id))
            conn.commit()

    def update_points(self, telegram_id, delta):
        """Обновляет баллы пользователя."""
        with self.get_conn() as conn:
            conn.execute("UPDATE users SET points = COALESCE(points, 0) + ? WHERE telegram_id = ?", (delta, telegram_id))
            conn.commit()

    def get_menu(self):
        """Получает всё меню."""
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT category, name, size, price, quantity FROM stock ORDER BY category, name, CAST(size AS REAL)")
            return cur.fetchall()

    def get_stock_by_fullname(self, full_name):
        """Получает количество товара по его полному названию."""
        if not full_name.endswith("л"):
            return 0
        name, size_l = full_name.rsplit(" ", 1)
        size = size_l.replace("л", "")
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT price, quantity FROM stock WHERE LOWER(name)=LOWER(?) AND size=?", (name.strip(), size))
            row = cur.fetchone()
            return row[0] if row else None, row[1] if row else 0

    def get_stock_by_name_size(self, name, size):
        """Получает информацию о товаре по имени и размеру."""
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT price, quantity FROM stock WHERE LOWER(name)=LOWER(?) AND size=?", (name.strip(), size))
            return cur.fetchone()

    def reduce_stock(self, full_name, qty):
        """Уменьшает количество товара на складе."""
        if not full_name.endswith("л"):
            return
        name, size_l = full_name.rsplit(" ", 1)
        size = size_l.replace("л", "")
        with self.get_conn() as conn:
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

    def get_cart_items(self, telegram_id):
        """Получает все позиции из корзины пользователя."""
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT name, price, qty
                FROM cart
                WHERE user_id = (SELECT id FROM users WHERE telegram_id = ?)
            """, (telegram_id,))
            return [{"name": r[0], "price": r[1], "qty": r[2]} for r in cur.fetchall()]

    def add_to_cart(self, telegram_id, item_name, price, qty):
        """Добавляет позицию в корзину в БД."""
        user_id = self.get_user_id(telegram_id)
        if not user_id: return
        with self.get_conn() as conn:
            conn.execute("""
                INSERT INTO cart (user_id, name, price, qty)
                VALUES (?, ?, ?, ?)
            """, (user_id, item_name, price, qty))
            conn.commit()

    def clear_cart(self, telegram_id):
        """Очищает корзину пользователя в БД."""
        with self.get_conn() as conn:
            conn.execute("DELETE FROM cart WHERE user_id = (SELECT id FROM users WHERE telegram_id = ?)", (telegram_id,))
            conn.commit()

    def create_order(self, telegram_id, items, total):
        """Создает новый заказ в БД."""
        user_id = self.get_user_id(telegram_id)
        if not user_id: return None
        with self.get_conn() as conn:
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
    
    def get_admin_data(self):
        """Получает данные для админ-панели."""
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*), SUM(total) FROM orders")
            count, revenue = cur.fetchone()
            cur.execute("SELECT COUNT(*) FROM users")
            users = cur.fetchone()[0]
            return count, revenue, users

    def get_low_stock(self):
        """Получает товары с низким остатком."""
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT category, name, size, quantity FROM stock WHERE quantity < 3 ORDER BY quantity")
            return cur.fetchall()

    def get_recent_orders(self):
        """Получает список последних 10 заказов."""
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT o.id, u.name, o.total, o.created_at
                FROM orders o JOIN users u ON o.user_id = u.id
                ORDER BY o.created_at DESC LIMIT 10
            """)
            return cur.fetchall()

    def admin_update_item(self, cat, name, size, price, qty):
        """Добавляет/обновляет товар вручную."""
        with self.get_conn() as conn:
            conn.execute("INSERT OR REPLACE INTO stock (category, name, size, price, quantity) VALUES (?, ?, ?, ?, ?)",
                         (cat, name, size, price, qty))
            conn.commit()

    def admin_update_qty(self, name, size, new_qty):
        """Обновляет количество товара."""
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE stock SET quantity = ? WHERE name = ? AND size = ?", (new_qty, name, size))
            conn.commit()
            return cur.rowcount > 0

    def admin_delete_item(self, name, size):
        """Удаляет товар."""
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM stock WHERE name = ? AND size = ?", (name, size))
            conn.commit()
            return cur.rowcount > 0

db_manager = DBManager(DB_PATH)

def calc_discount(total, points):
    """Рассчитывает скидку на основе баллов."""
    return min(points, int(total * MAX_DISCOUNT))

def format_cart_lines(items):
    """Форматирует список позиций в корзине."""
    return "\n".join([f"• {it['name']} x{it['qty']} — {it['price']}₽" for it in items])

# --- Клавиатуры ---
def main_keyboard():
    """Основная клавиатура для клиента."""
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📋 Меню", "🛒 Корзина")
    kb.add("👤 Мой профиль", "🔗 Реферальная программа")
    kb.add("🛠 Техподдержка")
    return kb

def handle_add_more_keyboard():
    """Клавиатура для добавления новых позиций в корзину."""
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("➕ Добавить ещё", "✅ Перейти к оформлению")
    return kb

# --- Обработчики команд и сообщений ---
@bot.message_handler(commands=["start"])
def start(message):
    """Обработчик команды /start."""
    telegram_id = str(message.chat.id)
    referrer_telegram_id = None
    if message.text.startswith("/start "):
        try:
            referrer_telegram_id = message.text.split(" ", 1)[1]
            if referrer_telegram_id == telegram_id or not db_manager.get_user(referrer_telegram_id):
                bot.send_message(telegram_id, "Ой, хитрец! 😜 Сам себя не пригласишь или ссылка недействительна.")
                referrer_telegram_id = None
            else:
                bot.send_message(telegram_id, "🤝 Привет! Ты перешел по реферальной ссылке.")
        except IndexError:
            pass
    
    user = db_manager.get_user(telegram_id)
    if user:
        bot.send_message(telegram_id, f"С возвращением, {user[2]} ☕", reply_markup=main_keyboard())
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
    db_manager.add_user_full(telegram_id, name, referrer_telegram_id=referrer_telegram_id)
    
    bot.send_message(
        telegram_id,
        f"🎉 Добро пожаловать, {name}!\n"
        "Я — твой персональный бот для заказов. У меня ты можешь посмотреть меню, оформить заказ и получить приятные бонусы!",
        reply_markup=main_keyboard()
    )
    bot.send_message(
        telegram_id,
        "📌 <b>Как сделать заказ:</b>\n\n"
        "1️⃣ Нажми «📋 Меню», чтобы посмотреть ассортимент\n"
        "2️⃣ Введи название и размер, например: <i>Латте 0.3л</i>\n"
        "3️⃣ Нажми «🛒 Корзина», чтобы проверить и оформить заказ",
        parse_mode="HTML"
    )

@bot.message_handler(func=lambda m: m.text == "📋 Меню")
def show_menu(message):
    """Показывает клиенту текущее меню."""
    rows = db_manager.get_menu()
    if not rows:
        bot.send_message(message.chat.id, "📭 Меню пустое")
        return

    text = "📋 <b>Наше меню:</b>\n"
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
    user = db_manager.get_user(telegram_id)
    if not user:
        bot.send_message(telegram_id, "Пожалуйста, сначала зарегистрируйтесь, чтобы использовать корзину. Нажмите /start.")
        return

    items = db_manager.get_cart_items(telegram_id)
    if not items:
        bot.send_message(telegram_id, "Корзина пуста 😢")
        return
    
    total = sum(it["price"] * it["qty"] for it in items)
    points = user[5] or 0
    referrals = user[-1] or 0
    
    is_challenge_eligible = points >= 500 and referrals >= 10
    
    kb = types.InlineKeyboardMarkup()
    
    if is_challenge_eligible:
        kb.add(types.InlineKeyboardButton("🎁 Активировать 15% скидку", callback_data="activate_15_percent"))
    
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
    user = db_manager.get_user(telegram_id)
    items = db_manager.get_cart_items(telegram_id)
    
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
    user = db_manager.get_user(telegram_id)
    items = db_manager.get_cart_items(telegram_id)
    
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
    user = db_manager.get_user(telegram_id)
    items = db_manager.get_cart_items(telegram_id)

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
        _, qty_in_stock = db_manager.get_stock_by_fullname(it["name"])
        if qty_in_stock < it["qty"]:
            bot.send_message(telegram_id, f"❌ Извините, {it['name']} больше нет в наличии.")
            return
            
    # Проверка на первый заказ для начисления реферального бонуса
    user_id = db_manager.get_user_id(telegram_id)
    with db_manager.get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM orders WHERE user_id = ?", (user_id,))
        orders_count = cur.fetchone()[0]
    
    # Если это первый заказ пользователя, и у него есть реферер
    if orders_count == 0:
        with db_manager.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT referrer_id FROM users WHERE id = ?", (user_id,))
            referrer_id = cur.fetchone()[0]
        
        if referrer_id:
            # Начисление бонусов и уведомление
            db_manager.update_points(telegram_id, 50) # Бонус новому пользователю
            with db_manager.get_conn() as conn:
                cur = conn.cursor()
                cur.execute("SELECT telegram_id FROM users WHERE id = ?", (referrer_id,))
                referrer_telegram_id = cur.fetchone()[0]
            db_manager.update_points(referrer_telegram_id, REFERRAL_BONUS)
            bot.send_message(referrer_telegram_id, f"🎉 Отличная работа! Ваш друг {user[2]} сделал свой первый заказ, и вы получили {REFERRAL_BONUS} баллов!")
            
            # Удаление реферера, чтобы бонус был единоразовым
            with db_manager.get_conn() as conn:
                conn.execute("UPDATE users SET referrer_id = NULL WHERE id = ?", (user_id,))
                conn.commit()

    for it in items:
        db_manager.reduce_stock(it["name"], it["qty"])
    
    earned = int(final_total * BONUS_PERCENT)
    db_manager.update_points(telegram_id, earned - points_spent)
    db_manager.clear_cart(telegram_id)
    
    order_id = db_manager.create_order(telegram_id, items, final_total)
    
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
    
    admin_message_text = (
        f"📦 Новый заказ №{order_id}\n"
        f"👤 {user[2]}\n"
        f"🧾 {items_text}\n"
        f"💰 К оплате: {final_total}₽\n"
        f"📉 Скидка: {discount}₽"
    )
    
    ready_button = types.InlineKeyboardMarkup()
    ready_button.add(types.InlineKeyboardButton("✅ Готов", callback_data=f"ready_{telegram_id}_{order_id}"))
    ready_button.add(types.InlineKeyboardButton("💬 Связаться с клиентом", callback_data=f"contact_{telegram_id}_{order_id}"))
    bot.send_message(
        ADMIN_GROUP_ID,
        admin_message_text,
        reply_markup=ready_button
    )

@bot.callback_query_handler(func=lambda call: call.data == "clear")
def clear_cart_handler(call):
    """Очищает корзину клиента."""
    telegram_id = str(call.message.chat.id)
    db_manager.clear_cart(telegram_id)
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

@bot.message_handler(func=lambda m: m.text == "👤 Мой профиль")
def show_profile(message):
    """Показывает информацию о профиле пользователя."""
    telegram_id = str(message.chat.id)
    user = db_manager.get_user(telegram_id)
    if not user:
        bot.send_message(telegram_id, "Пожалуйста, сначала зарегистрируйтесь, чтобы посмотреть профиль. Нажмите /start.")
        return
        
    points = user[5] or 0
    referrals = user[-1] or 0

    text = (
        f"👤 <b>Профиль пользователя {user[2]}</b>\n\n"
        f"Твои текущие баллы: <b>{points}</b>\n"
        f"Количество приглашённых друзей: <b>{referrals}</b>\n\n"
        "🔗 **Челлендж «15% скидка»**:\n"
        f"• Накопить <b>500</b> баллов (у тебя сейчас: {points})\n"
        f"• Пригласить <b>10</b> друзей (у тебя сейчас: {referrals})\n\n"
        "<i>За активацию скидки 15% будет списано 500 баллов.</i>"
    )
    bot.send_message(telegram_id, text, parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "🔗 Реферальная программа")
def show_referral_program(message):
    """Показывает реферальную программу и ссылку."""
    telegram_id = str(message.chat.id)
    user = db_manager.get_user(telegram_id)
    if not user:
        bot.send_message(telegram_id, "Пожалуйста, сначала зарегистрируйтесь. Нажмите /start.")
        return

    text = (
        "🔗 **Реферальная программа**\n\n"
        f"Приглашай друзей и получай {REFERRAL_BONUS} баллов за каждого, кто сделает свой первый заказ по твоей ссылке!\n\n"
        "Твоя реферальная ссылка:\n"
        f"https://t.me/{bot.get_me().username}?start={telegram_id}\n\n"
        "Просто скопируй и отправь её друзьям!"
    )
    bot.send_message(telegram_id, text, parse_mode="HTML")

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
        "Отлично! Напиши название следующей позиции или выбери из меню 📋",
        reply_markup=main_keyboard()
    )

@bot.message_handler(func=lambda m: m.text == "✅ Перейти к оформлению")
def go_to_checkout(message):
    """Переводит пользователя к оформлению заказа."""
    show_cart(message)

# --- Обработчики для админ-панели ---
@bot.message_handler(commands=["admin"])
def admin_panel(message):
    """Показывает главное меню админ-панели."""
    if message.chat.id != ADMIN_GROUP_ID:
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
        db_manager.admin_update_item(cat, name, size, price, qty)
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
        
        temp_file_path = f"/tmp/{message.document.file_id}.xlsx"
        with open(temp_file_path, 'wb') as f:
            f.write(downloaded_file)
            
        workbook = openpyxl.load_workbook(temp_file_path)
        sheet = workbook.active
        
        items_added = 0
        with db_manager.get_conn() as conn:
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
                        continue
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
        if len(parts) != 2: raise ValueError
        name_size_str, new_qty_str = parts
        name_parts = name_size_str.rsplit(" ", 1)
        if len(name_parts) != 2: raise ValueError
        name, size = name_parts[0], name_parts[1].replace("л", "")
        new_qty = int(new_qty_str)
        if db_manager.admin_update_qty(name, size, new_qty):
            bot.send_message(message.chat.id, f"✅ Остаток для {name} {size}л обновлен до {new_qty}.")
        else:
            bot.send_message(message.chat.id, f"❌ Товар '{name} {size}л' не найден.")
    except Exception:
        bot.send_message(message.chat.id, "❌ Неверный формат. Попробуй еще раз.")

@bot.callback_query_handler(func=lambda call: call.data == "admin_delete")
def admin_delete_prompt(call):
    """Запрашивает данные для удаления товара."""
    msg = bot.send_message(call.message.chat.id, "🗑️ Введи название и размер для удаления:\nНапример: <i>Латте 0.3л</i>", parse_mode="HTML")
    bot.register_next_step_handler(msg, apply_delete_item)

def apply_delete_item(message):
    """Удаляет товар из базы данных."""
    if message.chat.id != ADMIN_GROUP_ID: return
    try:
        name_size_str = message.text.strip()
        name_parts = name_size_str.rsplit(" ", 1)
        if len(name_parts) != 2: raise ValueError
        name, size = name_parts[0], name_parts[1].replace("л", "")
        if db_manager.admin_delete_item(name, size):
            bot.send_message(message.chat.id, f"✅ Товар '{name} {size}л' успешно удален.")
        else:
            bot.send_message(message.chat.id, f"❌ Товар '{name} {size}л' не найден.")
    except Exception:
        bot.send_message(message.chat.id, "❌ Неверный формат. Попробуй еще раз.")

@bot.message_handler(func=lambda m: m.text == "🧾 Последние заказы")
def show_recent_orders(message):
    """Показывает список последних 10 заказов."""
    if message.chat.id != ADMIN_GROUP_ID: return
    rows = db_manager.get_recent_orders()
    if not rows:
        bot.send_message(message.chat.id, "🧾 Нет последних заказов.")
        return
    text = "🧾 <b>Последние 10 заказов:</b>\n"
    for order_id, user_name, total, created_at in rows:
        text += f"• №{order_id} от {user_name} ({created_at.split('.')[0]}) — {total}₽\n"
    bot.send_message(message.chat.id, text, parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "📊 Статистика")
def show_stats(message):
    """Показывает общую статистику по заказам и пользователям."""
    if message.chat.id != ADMIN_GROUP_ID: return
    count, revenue, users = db_manager.get_admin_data()
    text = f"📊 <b>Статистика:</b>\nЗаказов: {count or 0}\nВыручка: {revenue or 0}₽\nПользователей: {users or 0}"
    bot.send_message(message.chat.id, text, parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "⚠️ Низкие остатки")
def show_low_stock(message):
    """Показывает товары с низким остатком."""
    if message.chat.id != ADMIN_GROUP_ID: return
    rows = db_manager.get_low_stock()
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
    
    if text.startswith('/') or not db_manager.get_user(telegram_id) or text in ["➕ Добавить ещё", "✅ Перейти к оформлению"]:
        return

    try:
        parts = text.rsplit(" ", 1)
        if len(parts) != 2 or not parts[1].endswith("л"):
            raise ValueError
            
        name, size = parts[0], parts[1].replace("л", "").strip()
        float(size) # Проверка, что размер - число
    except (ValueError, IndexError):
        bot.send_message(telegram_id, "❌ Неверный формат. Попробуй ввести название и объем, например: <i>Латте 0.3л</i>", parse_mode="HTML")
        return

    price, qty = db_manager.get_stock_by_name_size(name, size)
    
    if qty < 1:
        bot.send_message(telegram_id, f"❌ {name} {size}л закончился.")
        return
    
    db_manager.add_to_cart(telegram_id, f"{name} {size}л", price, 1)
    
    bot.send_message(
        telegram_id,
        f"✅ <b>{name} {size}л</b> добавлен в корзину! Хочешь добавить ещё?",
        reply_markup=handle_add_more_keyboard(),
        parse_mode="HTML"
    )

if __name__ == '__main__':
    while True:
        try:
            bot.polling(none_stop=True)
        except Exception as e:
            print(f"Polling error: {e}")
            time.sleep(15) # Ждём 15 секунд перед перезапуском
