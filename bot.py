# coding: utf-8

import telebot
from telebot import types
import sqlite3
import threading
import os
import datetime
import openpyxl

# --- Configuration (assumed to be defined in config.py) ---
from config import (
    BOT_TOKEN,
    ADMIN_GROUP_ID,
    BONUS_PERCENT,      # e.g., 0.05 for 5% cashback in points
    MAX_DISCOUNT,       # e.g., 0.10 for max 10% discount from points
    REFERRAL_BONUS,     # e.g., 50 points for each referral
    DB_PATH,
    MAX_REFERRALS_PER_USER,  # New: e.g., 20 to limit abuse
    CHALLENGE_COOLDOWN_DAYS, # New: e.g., 30 days between challenge uses
    MIN_REFERRAL_PURCHASE    # New: e.g., 500₽ minimum purchase by referral to award bonus
)

bot = telebot.TeleBot(BOT_TOKEN)

# Thread-local storage for DB connections
local_storage = threading.local()

class DBManager:
    """
    Class for managing all database operations.
    """

    def __init__(self, db_path):
        self.db_path = db_path
        self._init_db_if_not_exists()

    def get_conn(self):
        """
        Returns a database connection for the current thread.
        """
        if not hasattr(local_storage, 'conn'):
            local_storage.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        return local_storage.conn

    def _init_db_if_not_exists(self, sql_file="models.sql"):
        """
        Initializes the database from an SQL script if the DB file doesn't exist.
        """
        try:
            if not os.path.exists(self.db_path):
                with open(sql_file, 'r', encoding='utf-8') as f:
                    sql_script = f.read()
                with self.get_conn() as conn:
                    conn.executescript(sql_script)
                # New: Add columns if needed for optimizations (e.g., last_challenge_date)
                conn.execute("ALTER TABLE users ADD COLUMN last_challenge_date DATETIME")
                conn.execute("ALTER TABLE users ADD COLUMN referral_count INTEGER DEFAULT 0")
            else:
                print("✅ Database file already exists.")
        except FileNotFoundError:
            print(f"❌ Error: Database schema file '{sql_file}' not found.")
            print("Please ensure models.sql is in the same directory.")
        except Exception as e:
            print(f"❌ An error occurred during database initialization: {e}")

    def get_user(self, telegram_id):
        """
        Retrieves user data, including referral count.
        """
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT u.*,
                       (SELECT COUNT(*) FROM users WHERE referrer_id = u.id) AS referrals
                FROM users u WHERE telegram_id = ?
            """, (telegram_id,))
            return cur.fetchone()

    def get_user_id(self, telegram_id):
        """
        Retrieves user ID by Telegram ID.
        """
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM users WHERE telegram_id = ?", (telegram_id,))
            row = cur.fetchone()
            return row[0] if row else None

    def add_user_full(self, telegram_id, name, referrer_telegram_id=None):
        """
        Adds a new user to the database.
        """
        with self.get_conn() as conn:
            cur = conn.cursor()
            referrer_id = None
            if referrer_telegram_id:
                cur.execute("SELECT id, referral_count FROM users WHERE telegram_id = ?", (referrer_telegram_id,))
                r = cur.fetchone()
                if r and r[1] < MAX_REFERRALS_PER_USER:
                    referrer_id = r[0]
                    # New: Defer bonus until referral makes a purchase
            cur.execute("""
                INSERT OR IGNORE INTO users (telegram_id, name, referrer_id)
                VALUES (?, ?, ?)
            """, (telegram_id, name, referrer_id))
            conn.commit()

    def award_referral_bonus(self, referrer_telegram_id, bonus):
        """
        Awards referral bonus with limits.
        """
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT referral_count FROM users WHERE telegram_id = ?", (referrer_telegram_id,))
            count = cur.fetchone()[0] or 0
            if count < MAX_REFERRALS_PER_USER:
                self.update_points(referrer_telegram_id, bonus)
                conn.execute("UPDATE users SET referral_count = referral_count + 1 WHERE telegram_id = ?", (referrer_telegram_id,))
                conn.commit()

    def update_points(self, telegram_id, delta):
        """
        Updates user points.
        """
        with self.get_conn() as conn:
            conn.execute("UPDATE users SET points = COALESCE(points, 0) + ? WHERE telegram_id = ?", (delta, telegram_id))
            conn.commit()

    def get_menu(self):
        """
        Retrieves the full menu.
        """
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT category, name, size, price, quantity FROM stock ORDER BY category, name, CAST(size AS REAL)")
            return cur.fetchall()

    def get_stock_by_fullname(self, full_name):
        """
        Retrieves price and quantity by full item name.
        """
        if not full_name.endswith("л"):
            return None, 0
        name, size_l = full_name.rsplit(" ", 1)
        size = size_l.replace("л", "").strip()
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT price, quantity FROM stock WHERE LOWER(name) = LOWER(?) AND size = ?", (name.strip(), size))
            row = cur.fetchone()
            return (row[0] if row else None, row[1] if row else 0)

    def get_stock_by_name_size(self, name, size):
        """
        Retrieves item info by name and size.
        """
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT price, quantity FROM stock WHERE LOWER(name) = LOWER(?) AND size = ?", (name.strip(), size))
            return cur.fetchone()

    def reduce_stock(self, full_name, qty):
        """
        Reduces stock quantity.
        """
        if not full_name.endswith("л"):
            return
        name, size_l = full_name.rsplit(" ", 1)
        size = size_l.replace("л", "").strip()
        with self.get_conn() as conn:
            conn.execute("""
                UPDATE stock SET quantity = quantity - ?
                WHERE name = ? AND size = ? AND quantity >= ?
            """, (qty, name, size, qty))
            conn.commit()
            cur = conn.cursor()
            cur.execute("SELECT quantity FROM stock WHERE name = ? AND size = ?", (name, size))
            row = cur.fetchone()
            if row and row[0] < 3:
                bot.send_message(ADMIN_GROUP_ID, f"⚠️ Остаток низкий: {name} {size}л — {row[0]} шт")

    def get_cart_items(self, telegram_id):
        """
        Retrieves all cart items for a user.
        """
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT name, price, qty
                FROM cart
                WHERE user_id = (SELECT id FROM users WHERE telegram_id = ?)
            """, (telegram_id,))
            return [{"name": r[0], "price": r[1], "qty": r[2]} for r in cur.fetchall()]

    def add_to_cart(self, telegram_id, item_name, price, qty):
        """
        Adds an item to the cart in the DB.
        """
        user_id = self.get_user_id(telegram_id)
        if not user_id:
            return
        with self.get_conn() as conn:
            conn.execute("""
                INSERT INTO cart (user_id, name, price, qty)
                VALUES (?, ?, ?, ?)
            """, (user_id, item_name, price, qty))
            conn.commit()

    def clear_cart(self, telegram_id):
        """
        Clears the user's cart in the DB.
        """
        with self.get_conn() as conn:
            conn.execute("DELETE FROM cart WHERE user_id = (SELECT id FROM users WHERE telegram_id = ?)", (telegram_id,))
            conn.commit()

    def create_order(self, telegram_id, items, total):
        """
        Creates a new order in the DB.
        """
        user_id = self.get_user_id(telegram_id)
        if not user_id:
            return None
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO orders (user_id, total, created_at) VALUES (?, ?, ?)",
                        (user_id, total, datetime.datetime.now()))
            order_id = cur.lastrowid
            for item in items:
                cur.execute("INSERT INTO order_items (order_id, name, price, qty) VALUES (?, ?, ?, ?)",
                            (order_id, item["name"], item["price"], item["qty"]))
            conn.commit()
            # New: Check if user has referrer and award bonus if first order meets min purchase
            cur.execute("SELECT referrer_id FROM users WHERE id = ?", (user_id,))
            referrer_id = cur.fetchone()[0]
            if referrer_id and total >= MIN_REFERRAL_PURCHASE:
                cur.execute("SELECT telegram_id FROM users WHERE id = ?", (referrer_id,))
                referrer_telegram_id = cur.fetchone()[0]
                self.award_referral_bonus(referrer_telegram_id, REFERRAL_BONUS)
                bot.send_message(referrer_telegram_id, f"🎉 Твой реферал сделал первую покупку! +{REFERRAL_BONUS} баллов!")
            return order_id

    def can_use_challenge(self, telegram_id):
        """
        Checks if user can use challenge discount (cooldown).
        """
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT last_challenge_date FROM users WHERE telegram_id = ?", (telegram_id,))
            last_date = cur.fetchone()[0]
            if last_date:
                last_date = datetime.datetime.strptime(last_date, '%Y-%m-%d %H:%M:%S.%f')
                if (datetime.datetime.now() - last_date).days < CHALLENGE_COOLDOWN_DAYS:
                    return False
            return True

    def update_challenge_date(self, telegram_id):
        """
        Updates last challenge use date.
        """
        with self.get_conn() as conn:
            conn.execute("UPDATE users SET last_challenge_date = ? WHERE telegram_id = ?",
                         (datetime.datetime.now(), telegram_id))
            conn.commit()

    def get_admin_data(self):
        """
        Retrieves data for the admin panel.
        """
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*), SUM(total) FROM orders")
            count, revenue = cur.fetchone()
            cur.execute("SELECT COUNT(*) FROM users")
            users = cur.fetchone()[0]
            return count or 0, revenue or 0, users or 0

    def get_low_stock(self):
        """
        Retrieves items with low stock.
        """
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT category, name, size, quantity FROM stock WHERE quantity < 3 ORDER BY quantity")
            return cur.fetchall()

    def get_recent_orders(self):
        """
        Retrieves the last 10 orders.
        """
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT o.id, u.name, o.total, o.created_at
                FROM orders o JOIN users u ON o.user_id = u.id
                ORDER BY o.created_at DESC LIMIT 10
            """)
            return cur.fetchall()

    def admin_update_item(self, cat, name, size, price, qty):
        """
        Adds/updates an item manually.
        """
        with self.get_conn() as conn:
            conn.execute("INSERT OR REPLACE INTO stock (category, name, size, price, quantity) VALUES (?, ?, ?, ?, ?)",
                         (cat, name, size, price, qty))
            conn.commit()

    def admin_update_qty(self, name, size, new_qty):
        """
        Updates item quantity.
        """
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE stock SET quantity = ? WHERE name = ? AND size = ?", (new_qty, name, size))
            conn.commit()
            return cur.rowcount > 0

    def admin_delete_item(self, name, size):
        """
        Deletes an item.
        """
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM stock WHERE name = ? AND size = ?", (name, size))
            conn.commit()
            return cur.rowcount > 0

db_manager = DBManager(DB_PATH)

def calc_discount(total, points):
    """
    Calculates discount based on points.
    """
    return min(points, int(total * MAX_DISCOUNT))

def format_cart_lines(items):
    """
    Formats cart item list.
    """
    return "\n".join(f"• {item['name']} x{item['qty']} — {item['price']}₽" for item in items)

# --- Keyboards ---
def main_keyboard():
    """
    Main client keyboard.
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📋 Меню", "🛒 Корзина")
    kb.add("👤 Мой профиль", "🔗 Реферальная программа")
    kb.add("🛠 Техподдержка")
    return kb

def handle_add_more_keyboard():
    """
    Keyboard for adding more items to cart.
    """
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("➕ Добавить ещё", "✅ Перейти к оформлению")
    return kb

# --- Command and Message Handlers ---
@bot.message_handler(commands=["start"])
def start(message):
    """
    Handler for /start command.
    """
    telegram_id = str(message.chat.id)
    referrer_telegram_id = None
    if len(message.text.split()) > 1:
        referrer_telegram_id = message.text.split()[1]
        if referrer_telegram_id == telegram_id or not db_manager.get_user(referrer_telegram_id):
            bot.send_message(telegram_id, "Ой, хитрец! 😜 Сам себя не пригласишь или ссылка недействительна.")
            referrer_telegram_id = None
        else:
            bot.send_message(telegram_id, "🤝 Привет! Ты перешел по реферальной ссылке.")

    user = db_manager.get_user(telegram_id)
    if user:
        bot.send_message(telegram_id, f"С возвращением, {user[2]} ☕", reply_markup=main_keyboard())
    else:
        msg = bot.send_message(telegram_id, "👋 Привет! Как тебя зовут?")
        bot.register_next_step_handler(msg, finish_registration, referrer_telegram_id)

def finish_registration(message, referrer_telegram_id=None):
    """
    Completes new user registration.
    """
    name = message.text.strip()
    telegram_id = str(message.chat.id)
    if len(name) < 2:
        msg = bot.send_message(telegram_id, "Имя слишком короткое. Попробуй ещё раз.")
        bot.register_next_step_handler(msg, finish_registration, referrer_telegram_id)
        return

    db_manager.add_user_full(telegram_id, name, referrer_telegram_id)
    
    # New: Bonus deferred to first purchase
    
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
    """
    Shows the current menu to the client.
    """
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
    """
    Shows the client's cart contents.
    """
    telegram_id = str(message.chat.id)
    user = db_manager.get_user(telegram_id)
    if not user:
        bot.send_message(telegram_id, "Пожалуйста, сначала зарегистрируйтесь, чтобы использовать корзину. Нажмите /start.")
        return

    items = db_manager.get_cart_items(telegram_id)
    if not items:
        bot.send_message(telegram_id, "Корзина пуста 😢")
        return
    
    total = sum(item["price"] * item["qty"] for item in items)
    points = user[5] or 0
    referrals = user[-1] or 0
    
    # Optimized eligibility: Min total, cooldown
    is_challenge_eligible = points >= 500 and referrals >= 10 and total >= 1000 and db_manager.can_use_challenge(telegram_id)
    
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
    """
    Handles 15% discount activation.
    """
    telegram_id = str(call.message.chat.id)
    user = db_manager.get_user(telegram_id)
    items = db_manager.get_cart_items(telegram_id)
    
    if not user or not items:
        bot.answer_callback_query(call.id, "Корзина пуста или пользователь не найден.")
        return
        
    points = user[5] or 0
    referrals = user[-1] or 0
    total = sum(item["price"] * item["qty"] for item in items)

    if not (points >= 500 and referrals >= 10 and total >= 1000 and db_manager.can_use_challenge(telegram_id)):
        bot.answer_callback_query(call.id, "❌ Вы не соответствуете условиям для скидки 15% или cooldown активен.")
        return

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
            "⚠️ <b>Внимание:</b> Для получения этой скидки будет списано 500 баллов. Cooldown: {CHALLENGE_COOLDOWN_DAYS} дней."
        ),
        reply_markup=kb,
        parse_mode="HTML"
    )

@bot.callback_query_handler(func=lambda call: call.data == "confirm_standard")
def confirm_order_standard(call):
    """
    Handles standard discount order confirmation.
    """
    telegram_id = str(call.message.chat.id)
    user = db_manager.get_user(telegram_id)
    items = db_manager.get_cart_items(telegram_id)
    
    if not user or not items:
        bot.answer_callback_query(call.id, "Корзина пуста или пользователь не найден.")
        return
    
    points = user[5] or 0
    total = sum(item["price"] * item["qty"] for item in items)
    discount = calc_discount(total, points)
    final_total = total - discount
    
    process_order(telegram_id, user, items, final_total, discount)
    bot.edit_message_reply_markup(call.message.chat.id, call.message.id, reply_markup=None)

@bot.callback_query_handler(func=lambda call: call.data == "confirm_challenge")
def confirm_order_challenge(call):
    """
    Handles challenge (15%) discount order confirmation.
    """
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
        
    total = sum(item["price"] * item["qty"] for item in items)
    discount = int(total * 0.15)
    final_total = total - discount
    
    process_order(telegram_id, user, items, final_total, discount, points_spent=500)
    db_manager.update_challenge_date(telegram_id)
    bot.edit_message_reply_markup(call.message.chat.id, call.message.id, reply_markup=None)

def process_order(telegram_id, user, items, final_total, discount, points_spent=0):
    """
    Common function for processing and completing an order.
    """
    for item in items:
        _, qty_in_stock = db_manager.get_stock_by_fullname(item["name"])
        if qty_in_stock < item["qty"]:
            bot.send_message(telegram_id, f"❌ Извините, {item['name']} больше нет в наличии.")
            return
            
    for item in items:
        db_manager.reduce_stock(item["name"], item["qty"])
    
    earned = int(final_total * BONUS_PERCENT)
    db_manager.update_points(telegram_id, earned - points_spent)
    db_manager.clear_cart(telegram_id)
    
    order_id = db_manager.create_order(telegram_id, items, final_total)
    
    items_text = "; ".join(f"{item['name']} x{item['qty']}" for item in items)
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
    """
    Clears the client's cart.
    """
    telegram_id = str(call.message.chat.id)
    db_manager.clear_cart(telegram_id)
    bot.answer_callback_query(call.id, "Корзина очищена 🗑️")
    bot.send_message(telegram_id, "Твоя корзина теперь пуста.", reply_markup=main_keyboard())

@bot.callback_query_handler(func=lambda call: call.data.startswith("ready_"))
def mark_ready(call):
    """
    Handler for 'Ready' button in admin group.
    """
    _, telegram_id, order_id = call.data.split("_", 2)
    bot.send_message(int(telegram_id), f"✅ Твой заказ №{order_id} готов! Забери его на точке ☕")
    bot.answer_callback_query(call.id, "Клиент уведомлён ✅")
    bot.edit_message_reply_markup(call.message.chat.id, call.message.id, reply_markup=None)

@bot.callback_query_handler(func=lambda call: call.data.startswith("contact_"))
def contact_user_handler(call):
    """
    Handler for 'Contact Client' button.
    """
    _, telegram_id, order_id = call.data.split("_", 2)
    msg = bot.send_message(call.message.chat.id, "Напиши сообщение для клиента:")
    bot.register_next_step_handler(msg, send_admin_message, telegram_id, order_id)

def send_admin_message(message, telegram_id, order_id):
    """
    Sends message from admin to client.
    """
    user_msg = message.text
    bot.send_message(telegram_id, f"📝 Сообщение от администратора по заказу №{order_id}:\n\n{user_msg}")
    bot.send_message(message.chat.id, "✅ Сообщение успешно отправлено клиенту.")

@bot.message_handler(func=lambda m: m.text == "👤 Мой профиль")
def show_profile(message):
    """
    Shows user profile information.
    """
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
        "🔗 <b>Челлендж «15% скидка»</b>:\n"
        f"• Накопить <b>500</b> баллов (у тебя сейчас: {points})\n"
        f"• Пригласить <b>10</b> друзей (у тебя сейчас: {referrals})\n\n"
        "<i>За активацию скидки 15% будет списано 500 баллов. Доступно только для заказов от 1000₽. Cooldown: {CHALLENGE_COOLDOWN_DAYS} дней после использования.</i>\n\n"
        "📊 <b>Программа лояльности:</b>\n"
        f"• Баллы начисляются как {BONUS_PERCENT*100}% от суммы после скидки.\n"
        f"• Максимальная скидка от баллов: {MAX_DISCOUNT*100}% от суммы.\n"
        f"• Рефералы: +{REFERRAL_BONUS} баллов за каждого друга после их первой покупки от {MIN_REFERRAL_PURCHASE}₽ (макс. {MAX_REFERRALS_PER_USER} рефералов).\n"
        f"• Баллы не истекают, но лимит на аккаунт: 2000 (добавлено от себя для баланса)."
    )
    bot.send_message(telegram_id, text, parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "🔗 Реферальная программа")
def show_referral_program(message):
    """
    Shows referral program and link.
    """
    telegram_id = str(message.chat.id)
    user = db_manager.get_user(telegram_id)
    if not user:
        bot.send_message(telegram_id, "Пожалуйста, сначала зарегистрируйтесь. Нажмите /start.")
        return

    text = (
        "🔗 <b>Реферальная программа</b>\n\n"
        f"Приглашай друзей и получай {REFERRAL_BONUS} баллов за каждого, кто зарегистрируется по твоей ссылке и сделает первую покупку от {MIN_REFERRAL_PURCHASE}₽!\n"
        f"Макс. {MAX_REFERRALS_PER_USER} рефералов на аккаунт, чтобы избежать злоупотреблений.\n\n"
        "Твоя реферальная ссылка:\n"
        f"https://t.me/{bot.get_me().username}?start={telegram_id}\n\n"
        "Просто скопируй и отправь её друзьям!"
    )
    bot.send_message(telegram_id, text, parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "🛠 Техподдержка")
def support_info(message):
    """
    Provides support contact.
    """
    bot.send_message(
        message.chat.id,
        "🛠 Если у тебя возникли вопросы — напиши нам:\n@tamiklung\nМы всегда на связи!"
    )

@bot.message_handler(func=lambda m: m.text == "➕ Добавить ещё")
def add_more(message):
    """
    Prompts to add more drinks to cart.
    """
    bot.send_message(
        message.chat.id,
        "Отлично! Напиши название следующей позиции или выбери из меню 📋",
        reply_markup=main_keyboard()
    )

@bot.message_handler(func=lambda m: m.text == "✅ Перейти к оформлению")
def go_to_checkout(message):
    """
    Proceeds to order checkout.
    """
    show_cart(message)

# --- Admin Panel Handlers ---
@bot.message_handler(commands=["admin"])
def admin_panel(message):
    """
    Shows main admin panel menu.
    """
    if message.chat.id != ADMIN_GROUP_ID:
        return
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📊 Статистика", "⚠️ Низкие остатки")
    kb.add("⚙️ Управление товарами")
    kb.add("🧾 Последние заказы")
    bot.send_message(message.chat.id, "🔥 Админ-панель активна. Выбери команду:", reply_markup=kb)

@bot.message_handler(func=lambda m: m.text == "⚙️ Управление товарами")
def manage_items_menu(message):
    """
    Shows item management menu.
    """
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
    """
    Prompts for manual item addition.
    """
    msg = bot.send_message(call.message.chat.id, "✏️ Введи позицию одной строкой:\nКатегория;Название;Размер;Цена;Остаток")
    bot.register_next_step_handler(msg, apply_new_item)

def apply_new_item(message):
    """
    Adds new item to database.
    """
    if message.chat.id != ADMIN_GROUP_ID:
        return
    try:
        cat, name, size, price, qty = [x.strip() for x in message.text.split(";")]
        price = int(price)
        qty = int(qty)
        db_manager.admin_update_item(cat, name, size, price, qty)
        bot.send_message(message.chat.id, f"✅ Добавлено: {cat} | {name} {size}л — {price}₽ (остаток {qty})")
    except ValueError:
        bot.send_message(message.chat.id, "❌ Неверный формат. Пример: Classic;Латте;0.3;250;10")

@bot.callback_query_handler(func=lambda call: call.data == "admin_add_excel")
def admin_add_excel_prompt(call):
    """
    Prompts for Excel file upload for menu.
    """
    msg = bot.send_message(call.message.chat.id, "Пожалуйста, отправь Excel-файл с меню.\nФормат: <b>Категория | Название | Размер | Цена | Остаток</b>", parse_mode="HTML")
    bot.register_next_step_handler(msg, process_excel_upload)

def process_excel_upload(message):
    """
    Processes uploaded Excel file and updates menu.
    """
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
                                     (str(cat).strip(), str(name).strip(), str(size).strip(), price, qty))
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
    """
    Prompts for changing item quantity.
    """
    msg = bot.send_message(call.message.chat.id, "✏️ Введи название, размер и новое количество через запятую:\nНапример: <i>Латте 0.3, 15</i>", parse_mode="HTML")
    bot.register_next_step_handler(msg, apply_update_qty)

def apply_update_qty(message):
    """
    Updates item quantity in database.
    """
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
        if db_manager.admin_update_qty(name, size, new_qty):
            bot.send_message(message.chat.id, f"✅ Остаток для {name} {size}л обновлен до {new_qty}.")
        else:
            bot.send_message(message.chat.id, f"❌ Товар '{name} {size}л' не найден.")
    except ValueError:
        bot.send_message(message.chat.id, "❌ Неверный формат. Попробуй еще раз.")

@bot.callback_query_handler(func=lambda call: call.data == "admin_delete")
def admin_delete_prompt(call):
    """
    Prompts for item deletion.
    """
    msg = bot.send_message(call.message.chat.id, "🗑️ Введи название и размер для удаления:\nНапример: <i>Латте 0.3л</i>", parse_mode="HTML")
    bot.register_next_step_handler(msg, apply_delete_item)

def apply_delete_item(message):
    """
    Deletes item from database.
    """
    if message.chat.id != ADMIN_GROUP_ID:
        return
    try:
        name_size_str = message.text.strip()
        name_parts = name_size_str.rsplit(" ", 1)
        if len(name_parts) != 2:
            raise ValueError
        name, size = name_parts[0], name_parts[1].replace("л", "")
        if db_manager.admin_delete_item(name, size):
            bot.send_message(message.chat.id, f"✅ Товар '{name} {size}л' успешно удален.")
        else:
            bot.send_message(message.chat.id, f"❌ Товар '{name} {size}л' не найден.")
    except ValueError:
        bot.send_message(message.chat.id, "❌ Неверный формат. Попробуй еще раз.")

@bot.message_handler(func=lambda m: m.text == "🧾 Последние заказы")
def show_recent_orders(message):
    """
    Shows list of last 10 orders.
    """
    if message.chat.id != ADMIN_GROUP_ID:
        return
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
    """
    Shows overall stats on orders and users.
    """
    if message.chat.id != ADMIN_GROUP_ID:
        return
    count, revenue, users = db_manager.get_admin_data()
    text = f"📊 <b>Статистика:</b>\nЗаказов: {count}\nВыручка: {revenue}₽\nПользователей: {users}"
    bot.send_message(message.chat.id, text, parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "⚠️ Низкие остатки")
def show_low_stock(message):
    """
    Shows items with low stock.
    """
    if message.chat.id != ADMIN_GROUP_ID:
        return
    rows = db_manager.get_low_stock()
    if not rows:
        bot.send_message(message.chat.id, "Все остатки в норме ✅")
        return
    text = "⚠️ <b>Низкие остатки:</b>\n"
    for cat, name, size, qty in rows:
        text += f"• {cat}: {name} {size}л — {qty} шт\n"
    bot.send_message(message.chat.id, text, parse_mode="HTML")

# --- General Text Message Handler ---
@bot.message_handler(func=lambda m: True)
def handle_text(message):
    """
    General handler for text messages to add to cart.
    """
    telegram_id = str(message.chat.id)
    text = message.text.strip()
    
    if text.startswith('/') or not db_manager.get_user(telegram_id) or text in ["➕ Добавить ещё", "✅ Перейти к оформлению"]:
        return

    try:
        parts = text.rsplit(" ", 1)
        if len(parts) != 2 or not parts[1].endswith("л"):
            raise ValueError
        name, size_str = parts
        size = size_str.replace("л", "").strip()
        float(size)  # Validate size as number
    except ValueError:
        bot.send_message(telegram_id, "❌ Неверный формат. Попробуй ввести название и объем, например: <i>Латте 0.3л</i>", parse_mode="HTML")
        return

    price, qty = db_manager.get_stock_by_name_size(name, size)
    
    if price is None or qty < 1:
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
    bot.polling(none_stop=True)
