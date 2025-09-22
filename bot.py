# coding: utf-8
import telebot
from telebot import types
import sqlite3
import threading
import os
import datetime
import random

# --- Конфигурация ---
from config import BOT_TOKEN, ADMIN_GROUP_ID, BONUS_PERCENT, MAX_DISCOUNT, REFERRAL_BONUS, DB_PATH

bot = telebot.TeleBot(BOT_TOKEN)
local_storage = threading.local()


# --- Работа с базой ---
class DBManager:
    def __init__(self, db_path):
        self.db_path = db_path
        self._init_db_if_not_exists()

    def get_conn(self):
        if not hasattr(local_storage, 'conn'):
            local_storage.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        return local_storage.conn

    def _init_db_if_not_exists(self, sql_file="models.sql"):
        if not os.path.exists(self.db_path):
            try:
                with open(sql_file, 'r', encoding='utf-8') as f:
                    sql_script = f.read()
                with self.get_conn() as conn:
                    conn.executescript(sql_script)
                print(f"✅ Database created from {sql_file}")
            except Exception as e:
                print(f"❌ DB init error: {e}")
        else:
            print("✅ Database already exists")

    # --- Пользователи ---
    def get_user(self, telegram_id):
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT u.*,
                       (SELECT COUNT(*) FROM users WHERE referrer_id = u.id) AS referrals,
                       (SELECT COUNT(*) FROM orders WHERE user_id = u.id) AS order_count
                FROM users u WHERE telegram_id = ?
            """, (telegram_id,))
            return cur.fetchone()

    def get_user_id(self, telegram_id):
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM users WHERE telegram_id = ?", (telegram_id,))
            row = cur.fetchone()
            return row[0] if row else None

    def add_user_full(self, telegram_id, name, referrer_telegram_id=None):
        with self.get_conn() as conn:
            cur = conn.cursor()
            referrer_id = None
            if referrer_telegram_id:
                cur.execute("SELECT id FROM users WHERE telegram_id = ?", (referrer_telegram_id,))
                r = cur.fetchone()
                if r:
                    referrer_id = r[0]
            cur.execute("INSERT OR IGNORE INTO users (telegram_id, name, referrer_id) VALUES (?, ?, ?)",
                        (telegram_id, name, referrer_id))
            conn.commit()

    def update_points(self, telegram_id, delta):
        with self.get_conn() as conn:
            conn.execute("UPDATE users SET points = COALESCE(points, 0) + ? WHERE telegram_id = ?", (delta, telegram_id))
            conn.commit()

    # --- Меню и склад ---
    def get_menu(self):
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT category, name, size, price, quantity FROM stock ORDER BY category, name, CAST(size AS REAL)")
            return cur.fetchall()

    def get_categories(self):
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT category FROM stock ORDER BY category")
            return [r[0] for r in cur.fetchall()]

    def get_stock_by_fullname(self, full_name):
        if full_name.endswith("л"):
            name, size_l = full_name.rsplit(" ", 1)
            size = size_l.replace("л", "")
            with self.get_conn() as conn:
                cur = conn.cursor()
                cur.execute("SELECT price, quantity FROM stock WHERE LOWER(name)=LOWER(?) AND size=?", (name.strip(), size))
                row = cur.fetchone()
                return row[0] if row else None, row[1] if row else 0
        else:
            with self.get_conn() as conn:
                cur = conn.cursor()
                cur.execute("SELECT price, quantity FROM stock WHERE LOWER(name)=LOWER(?)", (full_name.strip(),))
                row = cur.fetchone()
                return row[0] if row else None, row[1] if row else 0

    def reduce_stock(self, full_name, qty):
        if full_name.endswith("л"):
            name, size_l = full_name.rsplit(" ", 1)
            size = size_l.replace("л", "")
            with self.get_conn() as conn:
                conn.execute("UPDATE stock SET quantity = quantity - ? WHERE name=? AND size=? AND quantity >= ?", (qty, name, size, qty))
                conn.commit()
                cur = conn.cursor()
                cur.execute("SELECT quantity FROM stock WHERE name=? AND size=?", (name, size))
                row = cur.fetchone()
                if row and row[0] < 3:
                    bot.send_message(ADMIN_GROUP_ID, f"⚠️ Остаток низкий: <b>{name} {size}л</b> — {row[0]} шт", parse_mode="HTML")
        else:
            with self.get_conn() as conn:
                conn.execute("UPDATE stock SET quantity = quantity - ? WHERE name=? AND quantity >= ?", (qty, full_name, qty))
                conn.commit()

    # --- Корзина ---
    def get_cart_items(self, telegram_id):
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT name, price, qty
                FROM cart
                WHERE user_id = (SELECT id FROM users WHERE telegram_id = ?)
            """, (telegram_id,))
            return [{"name": r[0], "price": r[1], "qty": r[2]} for r in cur.fetchall()]

    def add_to_cart(self, telegram_id, item_name, price, qty):
        user_id = self.get_user_id(telegram_id)
        if not user_id: return
        with self.get_conn() as conn:
            conn.execute("INSERT INTO cart (user_id, name, price, qty) VALUES (?, ?, ?, ?)", (user_id, item_name, price, qty))
            conn.commit()

    def clear_cart(self, telegram_id):
        with self.get_conn() as conn:
            conn.execute("DELETE FROM cart WHERE user_id = (SELECT id FROM users WHERE telegram_id = ?)", (telegram_id,))
            conn.commit()

    # --- Заказы ---
    def create_order(self, telegram_id, items, total):
        user_id = self.get_user_id(telegram_id)
        if not user_id: return None
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO orders (user_id, total, created_at) VALUES (?, ?, ?)", (user_id, total, datetime.datetime.now()))
            order_id = cur.lastrowid
            for it in items:
                cur.execute("INSERT INTO order_items (order_id, name, price, qty) VALUES (?, ?, ?, ?)", (order_id, it["name"], it["price"], it["qty"]))
            conn.commit()
            return order_id


db_manager = DBManager(DB_PATH)


# --- Вспомогательные функции ---
def main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📋 Меню", "🛒 Корзина")
    kb.add("👤 Мой профиль", "🔗 Реферальная программа")
    kb.add("🛠 Техподдержка")
    return kb

def category_keyboard(categories):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for cat in categories:
        kb.add(cat)
    kb.add("⬅️ Назад")
    return kb

def items_keyboard(items):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for it in items:
        kb.add(it)
    kb.add("⬅️ Назад")
    return kb

def handle_add_more_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("➕ Добавить ещё", "✅ Перейти к оформлению")
    return kb

def funny_not_found_message():
    msgs = [
        "😅 Ой, этого пока нет… но желание учтено!",
        "🤔 Кажется, этого мы ещё не варим…",
        "🚫 Хм, такой позиции нет в меню. Может, попробуем что-то другое?"
    ]
    return random.choice(msgs)


# --- Обработчики ---
@bot.message_handler(commands=["start"])
def start_handler(message):
    telegram_id = message.from_user.id
    user = db_manager.get_user(telegram_id)
    ref = None
    if message.text.startswith("/start "):
        ref = message.text.split()[1]
    if not user:
        name = message.from_user.first_name
        db_manager.add_user_full(telegram_id, name, ref)
    bot.send_message(telegram_id, f"Привет, {message.from_user.first_name}! Добро пожаловать в наш магазин.", reply_markup=main_keyboard())


@bot.message_handler(func=lambda m: m.text == "📋 Меню")
def show_menu(message):
    categories = db_manager.get_categories()
    if categories:
        bot.send_message(message.chat.id, "Выберите категорию:", reply_markup=category_keyboard(categories))
    else:
        bot.send_message(message.chat.id, "Меню пустое 😅")


@bot.message_handler(func=lambda m: True)
def generic_handler(message):
    text = message.text
    telegram_id = message.from_user.id

    # Назад
    if text == "⬅️ Назад":
        bot.send_message(telegram_id, "Главное меню", reply_markup=main_keyboard())
        return

    # Категория
    categories = db_manager.get_categories()
    if text in categories:
        # Показать товары
        items = db_manager.get_menu()
        items_list = [f"{r[1]} {r[2]}л — {r[3]}₽" if r[2] else f"{r[1]} — {r[3]}₽" for r in items if r[0]==text]
        if items_list:
            bot.send_message(telegram_id, f"Выберите товар из категории {text}:", reply_markup=items_keyboard(items_list))
        else:
            bot.send_message(telegram_id, funny_not_found_message())
        return

    # Товар
    price, qty = db_manager.get_stock_by_fullname(text)
    if price is not None:
        bot.send_message(telegram_id, f"{text} — {price}₽, в наличии: {qty} шт\nСколько добавим в корзину?")
        bot.register_next_step_handler_by_chat_id(telegram_id, add_to_cart_step, text, price)
        return

    # Корзина
    if text == "🛒 Корзина":
        items = db_manager.get_cart_items(telegram_id)
        if items:
            cart_text = "\n".join([f"{it['name']} x{it['qty']} — {it['price']}₽" for it in items])
            bot.send_message(telegram_id, f"Ваша корзина:\n{cart_text}", reply_markup=handle_add_more_keyboard())
        else:
            bot.send_message(telegram_id, "Ваша корзина пока пуста 😅")
        return

    # Добавить или оформить
    if text == "➕ Добавить ещё":
        bot.send_message(telegram_id, "Выберите категорию:", reply_markup=category_keyboard(categories))
        return
    if text == "✅ Перейти к оформлению":
        items = db_manager.get_cart_items(telegram_id)
        if not items:
            bot.send_message(telegram_id, "Корзина пустая 😅")
            return
        total = sum(it['price']*it['qty'] for it in items)
        order_id = db_manager.create_order(telegram_id, items, total)
        for it in items:
            db_manager.reduce_stock(it['name'], it['qty'])
        db_manager.clear_cart(telegram_id)
        bot.send_message(telegram_id, f"Заказ оформлен! Сумма: {total}₽")
        bot.send_message(ADMIN_GROUP_ID, f"Новый заказ от {telegram_id}, сумма {total}₽")
        return

    # Рефералы и профиль
    if text == "👤 Мой профиль":
        user = db_manager.get_user(telegram_id)
        if user:
            bot.send_message(telegram_id, f"Имя: {user[2]}\nБаллы: {user[3]}\nЗаказы: {user[-1]}\nРефералы: {user[-2]}")
        else:
            bot.send_message(telegram_id, "Вы не зарегистрированы 😅")
        return
    if text == "🔗 Реферальная программа":
        bot.send_message(telegram_id, f"Ваш реферальный код: {telegram_id}\nДайте его друзьям для бонуса!")


def add_to_cart_step(message, item_name, price):
    try:
        qty = int(message.text)
        if qty <= 0:
            raise ValueError
    except ValueError:
        bot.send_message(message.chat.id, "Введите корректное число 😅")
        bot.register_next_step_handler_by_chat_id(message.chat.id, add_to_cart_step, item_name, price)
        return
    db_manager.add_to_cart(message.chat.id, item_name, price, qty)
    bot.send_message(message.chat.id, f"{qty} x {item_name} добавлено в корзину!", reply_markup=handle_add_more_keyboard())


# --- Запуск бота ---
print("Бот запущен")
bot.infinity_polling()
