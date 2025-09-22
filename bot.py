# coding: utf-8
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
        """Получает данные пользователя по Telegram ID, включая рефералов и заказы."""
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
        name_parts = full_name.rsplit(" ", 1)
        name = name_parts[0]
        size = None
        if len(name_parts) > 1 and name_parts[1].endswith("л"):
            size = name_parts[1].replace("л", "")
        
        with self.get_conn() as conn:
            cur = conn.cursor()
            if size:
                cur.execute("SELECT price, quantity FROM stock WHERE LOWER(name)=LOWER(?) AND size=?", (name.strip(), size))
            else:
                cur.execute("SELECT price, quantity FROM stock WHERE LOWER(name)=LOWER(?) AND size IS NULL", (name.strip(),))
            row = cur.fetchone()
            return row[0] if row else None, row[1] if row else 0
    def reduce_stock(self, full_name, qty):
        """Уменьшает количество товара на складе и отправляет уведомление, если остаток низкий."""
        name_parts = full_name.rsplit(" ", 1)
        name = name_parts[0]
        size = None
        if len(name_parts) > 1 and name_parts[1].endswith("л"):
            size = name_parts[1].replace("л", "")
        
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
                size_str = f" {size}л" if size else ""
                bot.send_message(ADMIN_GROUP_ID, f"⚠️ Остаток низкий: <b>{name}{size_str}</b> — {row[0]} шт", parse_mode="HTML")
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
def admin_keyboard():
    """Клавиатура для администратора."""
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📊 Статистика", "📦 Остатки", "📋 Меню")
    return kb
# --- Обработчики команд и сообщений ---
@bot.message_handler(commands=["start"])
def start(message):
    """Обработчик команды /start, который теперь проверяет наличие пользователя в базе."""
    telegram_id = str(message.chat.id)
    referrer_telegram_id = None
    if message.text.startswith("/start "):
        try:
            referrer_telegram_id = message.text.split(" ", 1)[1]
            if referrer_telegram_id == telegram_id or not db_manager.get_user(referrer_telegram_id):
                bot.send_message(telegram_id, "😅 Похоже, ссылка некорректна или это твой собственный ID. Регистрация без реферала.", parse_mode="HTML")
                referrer_telegram_id = None
            else:
                bot.send_message(telegram_id, "🤝 Отлично — ты пришёл по реферальной ссылке! Давай познакомимся.", parse_mode="HTML")
        except IndexError:
            pass
    
    user = db_manager.get_user(telegram_id)
    if user:
        bot.send_message(telegram_id, f"☕ С возвращением, <b>{user[2]}</b>!", reply_markup=main_keyboard(), parse_mode="HTML")
    else:
        # Если пользователь не найден, запускаем регистрацию
        msg = bot.send_message(telegram_id, "☕ Привет! Я Кофейный! Для получения бонусов и скидок, пожалуйста, зарегистрируйтесь. Как тебя зовут?", parse_mode="HTML")
        bot.register_next_step_handler(msg, finish_registration, referrer_telegram_id)
@bot.message_handler(commands=["admin_menu"], func=lambda m: str(m.chat.id) == ADMIN_GROUP_ID)
def admin_show_menu(message):
    """Показывает меню для администратора."""
    rows = db_manager.get_menu()
    if not rows:
        bot.send_message(message.chat.id, "📭 Меню пока пустое.", parse_mode="HTML")
        return
    text = "📋 <b>Текущее меню:</b>\n"
    current_cat = None
    for cat, name, size, price, qty in rows:
        if cat != current_cat:
            text += f"\n🔸 <b>{cat}</b>\n"
            current_cat = cat
        size_str = f" {size}л" if size else ""
        text += f"• {name}{size_str} — {price}₽ (осталось: {qty})\n"
    bot.send_message(message.chat.id, text, parse_mode="HTML")
@bot.message_handler(func=lambda m: m.text == "📋 Меню")
def show_menu(message):
    """Показывает клиенту текущее меню."""
    rows = db_manager.get_menu()
    if not rows:
        bot.send_message(message.chat.id, "📭 Меню пока пустое.", parse_mode="HTML")
        return
    text = "📋 <b>Наше меню:</b>\n"
    current_cat = None
    for cat, name, size, price, qty in rows:
        if cat != current_cat:
            text += f"\n🔸 <b>{cat}</b>\n"
            current_cat = cat
        size_str = f" {size}л" if size else ""
        text += f"• {name}{size_str} — {price}₽ (осталось: {qty})\n"
    bot.send_message(message.chat.id, text, parse_mode="HTML")
@bot.message_handler(func=lambda m: m.text == "🛒 Корзина")
def show_cart(message):
    """Показывает содержимое корзины клиента."""
    telegram_id = str(message.chat.id)
    user = db_manager.get_user(telegram_id)
    if not user:
        bot.send_message(telegram_id, "⚠️ Пожалуйста, сначала зарегистрируйтесь — нажми /start.", parse_mode="HTML")
        return
    items = db_manager.get_cart_items(telegram_id)
    if not items:
        bot.send_message(telegram_id, "🛒 Твоя корзина пуста. Добавь напитки из меню.", parse_mode="HTML")
        return
    total = sum(it["price"] * it["qty"] for it in items)
    points = user[5] or 0
    referrals = user[-2] or 0
    
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
        f"💰 <b>Итого:</b> {total}₽\n"
        f"🎁 <b>Скидка по баллам:</b> {standard_discount}₽\n"
        f"📦 <b>К оплате:</b> {final_total_standard}₽",
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
    referrals = user[-2] or 0
    if not (points >= 500 and referrals >= 10):
        bot.answer_callback_query(call.id, "❌ Условия для 15% скидки не выполнены.")
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
            f"🎉 <b>Челлендж активирован!</b>\n\n"
            f"🎁 Ты получаешь скидку <b>15%</b> на этот заказ.\n"
            f"💰 Итого: {total}₽\n"
            f"📉 Скидка: {discount}₽\n"
            f"📦 <b>К оплате:</b> {final_total}₽\n\n"
            "⚠️ Для этой операции будет списано <b>500</b> баллов."
        ),
        reply_markup=kb,
        parse_mode="HTML"
    )
def finish_registration(message, referrer_telegram_id):
    """
    Завершает регистрацию, добавляет пользователя в базу.
    Если есть реферер, начисляет ему бонус.
    """
    telegram_id = str(message.chat.id)
    name = message.text.strip()
    db_manager.add_user_full(telegram_id, name, referrer_telegram_id)
    
    if referrer_telegram_id:
        db_manager.update_points(referrer_telegram_id, REFERRAL_BONUS)
        bot.send_message(referrer_telegram_id, f"🎉 Отличная новость! Твой друг зарегистрировался по твоей ссылке, и ты получил {REFERRAL_BONUS} баллов!")
    
    bot.send_message(telegram_id, f"🎉 Поздравляю, <b>{name}</b>! Ты успешно зарегистрирован. Начни свой кофе-путь!", reply_markup=main_keyboard(), parse_mode="HTML")
@bot.message_handler(func=lambda m: m.text == "👤 Мой профиль")
def show_profile(message):
    """Показывает профиль пользователя с баллами и количеством заказов."""
    telegram_id = str(message.chat.id)
    user = db_manager.get_user(telegram_id)
    if not user:
        bot.send_message(telegram_id, "⚠️ Пожалуйста, сначала зарегистрируйтесь — нажми /start.", parse_mode="HTML")
        return
    user_id = user[0]
    name = user[2]
    points = user[5] or 0
    referrals = user[6] or 0
    order_count = user[7] or 0
    
    # Реферальная ссылка
    referral_link = f"https://t.me/Coffee_Teleram_bot?start={telegram_id}"
    
    bot.send_message(
        telegram_id,
        f"<b>☕ Профиль пользователя:</b>\n\n"
        f"✨ Имя: <b>{name}</b>\n"
        f"✨ Баллы: <b>{points}</b>\n"
        f"✨ Приглашено друзей: <b>{referrals}</b>\n"
        f"✨ Количество заказов: <b>{order_count}</b>\n"
        f"✨ Твой ID: <code>{user_id}</code>\n\n"
        f"🔗 <b>Твоя реферальная ссылка:</b>\n"
        f"<code>{referral_link}</code>",
        parse_mode="HTML"
    )
@bot.callback_query_handler(func=lambda call: call.data.startswith("add_to_cart_"))
def add_to_cart_callback(call):
    """Обрабатывает добавление товара в корзину."""
    telegram_id = str(call.message.chat.id)
    item_name = call.data.replace("add_to_cart_", "")
    
    # Проверяем количество товара на складе
    price, stock_qty = db_manager.get_stock_by_fullname(item_name)
    if stock_qty <= 0:
        bot.answer_callback_query(call.id, "❌ Извините, этого товара нет в наличии.")
        return
    
    db_manager.add_to_cart(telegram_id, item_name, price, 1)
    
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("🛒 Перейти в корзину", callback_data="show_cart"),
        types.InlineKeyboardButton("❌ Отмена", callback_data="cancel_add")
    )
    bot.send_message(telegram_id, f"✅ <b>{item_name}</b> добавлен в корзину.", reply_markup=kb, parse_mode="HTML")
    bot.answer_callback_query(call.id)
@bot.callback_query_handler(func=lambda call: call.data == "show_cart")
def show_cart_callback(call):
    """Вызывает функцию показа корзины."""
    show_cart(call.message)
    bot.answer_callback_query(call.id)
@bot.callback_query_handler(func=lambda call: call.data == "cancel_add")
def cancel_add_callback(call):
    """Отменяет добавление и удаляет сообщение."""
    bot.delete_message(call.message.chat.id, call.message.message_id)
@bot.callback_query_handler(func=lambda call: call.data == "clear")
def clear_cart_callback(call):
    """Очищает корзину."""
    telegram_id = str(call.message.chat.id)
    db_manager.clear_cart(telegram_id)
    bot.edit_message_text("🗑️ Корзина очищена.", call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id)
@bot.callback_query_handler(func=lambda call: call.data in ["confirm_standard", "confirm_challenge"])
def confirm_order(call):
    """Оформляет заказ, списывает баллы и уменьшает остатки."""
    telegram_id = str(call.message.chat.id)
    items = db_manager.get_cart_items(telegram_id)
    user = db_manager.get_user(telegram_id)
    
    if not items or not user:
        bot.answer_callback_query(call.id, "❌ Не удалось оформить заказ.")
        return
    total = sum(it["price"] * it["qty"] for it in items)
    points = user[5] or 0
    final_total = 0
    discount_amount = 0
    
    if call.data == "confirm_standard":
        discount_amount = calc_discount(total, points)
        final_total = total - discount_amount
        if discount_amount > 0:
            db_manager.update_points(telegram_id, -discount_amount)
    elif call.data == "confirm_challenge":
        discount_amount = int(total * 0.15)
        final_total = total - discount_amount
        db_manager.update_points(telegram_id, -500)
    order_id = db_manager.create_order(telegram_id, items, final_total)
    
    if not order_id:
        bot.answer_callback_query(call.id, "❌ Не удалось создать заказ.")
        return
    # Уменьшаем количество товаров на складе
    for item in items:
        db_manager.reduce_stock(item["name"], item["qty"])
    db_manager.clear_cart(telegram_id)
    # Начисляем бонусные баллы
    points_to_add = int(final_total * BONUS_PERCENT)
    db_manager.update_points(telegram_id, points_to_add)
    order_summary = (
        f"✅ <b>Заказ №{order_id} успешно оформлен!</b>\n\n"
        f"<b>Состав заказа:</b>\n"
        f"{format_cart_lines(items)}\n\n"
        f"💰 <b>Итого:</b> {total}₽\n"
        f"🎁 <b>Скидка:</b> {discount_amount}₽\n"
        f"📦 <b>К оплате:</b> {final_total}₽\n\n"
        f"✨ Начислено баллов: <b>{points_to_add}</b>\n\n"
        f"<i>Спасибо за покупку!</i>"
    )
    bot.edit_message_text(order_summary, call.message.chat.id, call.message.message_id, parse_mode="HTML", reply_markup=None)
    bot.answer_callback_query(call.id, "✅ Заказ оформлен!")
    # Уведомление для администратора
    admin_notification = (
        f"🔔 <b>НОВЫЙ ЗАКАЗ</b>\n\n"
        f"👤 От: <b>{user[2]}</b> ({telegram_id})\n"
        f"🛍️ Заказ №{order_id}\n\n"
        f"<b>Состав:</b>\n"
        f"{format_cart_lines(items)}\n\n"
        f"💰 <b>Общая сумма:</b> {final_total}₽"
    )
    bot.send_message(ADMIN_GROUP_ID, admin_notification, parse_mode="HTML")
@bot.message_handler(func=lambda m: m.text == "🛠 Техподдержка")
def contact_support(message):
    """Отправляет сообщение в техподдержку."""
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Написать в техподдержку", url="https://t.me/admin_chat"))
    bot.send_message(message.chat.id, "🔧 Если у вас возникли вопросы, вы можете связаться с нашей техподдержкой.", reply_markup=kb)
@bot.message_handler(func=lambda m: m.text == "🔗 Реферальная программа")
def show_referral_info(message):
    """Показывает информацию о реферальной программе."""
    bot.send_message(
        message.chat.id,
        "🤝 <b>Реферальная программа</b>\n\n"
        "Приглашайте друзей и получайте бонусы! За каждого нового пользователя, который зарегистрируется по вашей ссылке, вы получите <b>250 баллов</b>.",
        parse_mode="HTML"
    )
    show_profile(message)
# --- Запуск бота ---
if __name__ == '__main__':
    bot.polling(none_stop=True)
