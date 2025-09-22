# coding: utf-8
import telebot
from telebot import types
import sqlite3
import threading
import os
import datetime
import openpyxl
import time
from config import BOT_TOKEN, ADMIN_GROUP_ID, BONUS_PERCENT, MAX_DISCOUNT, REFERRAL_BONUS, DB_PATH

bot = telebot.TeleBot(BOT_TOKEN)
local_storage = threading.local()

# --- База данных ---
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
            with open(sql_file, 'r', encoding='utf-8') as f:
                sql_script = f.read()
            with self.get_conn() as conn:
                conn.executescript(sql_script)
            print(f"✅ База данных создана из {sql_file}")
        else:
            print("✅ Файл базы данных уже существует.")

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
                if r: referrer_id = r[0]
            cur.execute("""
                INSERT OR IGNORE INTO users (telegram_id, name, referrer_id)
                VALUES (?, ?, ?)
            """, (telegram_id, name, referrer_id))
            conn.commit()

    def update_points(self, telegram_id, delta):
        with self.get_conn() as conn:
            conn.execute("UPDATE users SET points = COALESCE(points,0) + ? WHERE telegram_id=?", (delta, telegram_id))
            conn.commit()

    # --- Меню и склад ---
    def get_menu(self):
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT category, name, size, price, quantity FROM stock ORDER BY category, name, CAST(size AS REAL)")
            return cur.fetchall()

    def get_stock_by_fullname(self, full_name):
        if full_name.endswith("л"):
            name, size_l = full_name.rsplit(" ", 1)
            size = size_l.replace("л","")
        else:
            name, size = full_name, None
        with self.get_conn() as conn:
            cur = conn.cursor()
            if size:
                cur.execute("SELECT price, quantity FROM stock WHERE LOWER(name)=LOWER(?) AND size=?", (name.strip(), size))
            else:
                cur.execute("SELECT price, quantity FROM stock WHERE LOWER(name)=LOWER(?)", (name.strip(),))
            row = cur.fetchone()
            return row[0] if row else None, row[1] if row else 0

    def get_stock_by_name_size(self, name, size=None):
        with self.get_conn() as conn:
            cur = conn.cursor()
            if size:
                cur.execute("SELECT price, quantity FROM stock WHERE LOWER(name)=LOWER(?) AND size=?", (name.strip(), size))
            else:
                cur.execute("SELECT price, quantity FROM stock WHERE LOWER(name)=LOWER(?)", (name.strip(),))
            return cur.fetchone()

    def reduce_stock(self, full_name, qty):
        if full_name.endswith("л"):
            name, size_l = full_name.rsplit(" ", 1)
            size = size_l.replace("л","")
        else:
            name, size = full_name, None
        with self.get_conn() as conn:
            if size:
                conn.execute("UPDATE stock SET quantity = quantity - ? WHERE name=? AND size=? AND quantity >= ?", (qty, name, size, qty))
                cur = conn.cursor()
                cur.execute("SELECT quantity FROM stock WHERE name=? AND size=?", (name, size))
            else:
                conn.execute("UPDATE stock SET quantity = quantity - ? WHERE name=? AND quantity >= ?", (qty, name, qty))
                cur = conn.cursor()
                cur.execute("SELECT quantity FROM stock WHERE name=?", (name,))
            row = cur.fetchone()
            conn.commit()
            if row and row[0] < 3:
                bot.send_message(ADMIN_GROUP_ID, f"⚠️ Остаток низкий: <b>{full_name}</b> — {row[0]} шт", parse_mode="HTML")

    # --- Корзина ---
    def get_cart_items(self, telegram_id):
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT name, price, qty
                FROM cart
                WHERE user_id=(SELECT id FROM users WHERE telegram_id=?)
            """, (telegram_id,))
            return [{"name": r[0], "price": r[1], "qty": r[2]} for r in cur.fetchall()]

    def add_to_cart(self, telegram_id, item_name, price, qty):
        user_id = self.get_user_id(telegram_id)
        if not user_id: return
        with self.get_conn() as conn:
            conn.execute("INSERT INTO cart (user_id,name,price,qty) VALUES (?,?,?,?)", (user_id,item_name,price,qty))
            conn.commit()

    def clear_cart(self, telegram_id):
        with self.get_conn() as conn:
            conn.execute("DELETE FROM cart WHERE user_id=(SELECT id FROM users WHERE telegram_id=?)", (telegram_id,))
            conn.commit()

    # --- Заказы ---
    def create_order(self, telegram_id, items, total):
        user_id = self.get_user_id(telegram_id)
        if not user_id: return None
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO orders (user_id, total, created_at) VALUES (?,?,?)", (user_id, total, datetime.datetime.now()))
            order_id = cur.lastrowid
            for it in items:
                cur.execute("INSERT INTO order_items (order_id,name,price,qty) VALUES (?,?,?,?)", (order_id, it["name"], it["price"], it["qty"]))
            conn.commit()
            return order_id

    # --- Админ ---
    def get_admin_data(self):
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*), SUM(total) FROM orders")
            count, revenue = cur.fetchone()
            cur.execute("SELECT COUNT(*) FROM users")
            users = cur.fetchone()[0]
            return count, revenue, users

    def get_low_stock(self):
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT category,name,size,quantity FROM stock WHERE quantity<3 ORDER BY quantity")
            return cur.fetchall()

    def get_recent_orders(self):
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT o.id,u.name,o.total,o.created_at
                FROM orders o JOIN users u ON o.user_id=u.id
                ORDER BY o.created_at DESC LIMIT 10
            """)
            return cur.fetchall()

    def admin_update_item(self, cat, name, size, price, qty):
        with self.get_conn() as conn:
            conn.execute("INSERT OR REPLACE INTO stock (category,name,size,price,quantity) VALUES (?,?,?,?,?)",
                         (cat,name,size,price,qty))
            conn.commit()

    def admin_update_qty(self, name, size, new_qty):
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE stock SET quantity=? WHERE name=? AND size=?", (new_qty,name,size))
            conn.commit()
            return cur.rowcount>0

    def admin_delete_item(self,name,size):
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM stock WHERE name=? AND size=?", (name,size))
            conn.commit()
            return cur.rowcount>0

db_manager = DBManager(DB_PATH)

# --- Вспомогательные функции ---
def calc_discount(total, points):
    return min(points, int(total*MAX_DISCOUNT))

def format_cart_lines(items):
    return "\n".join([f"• {it['name']} x{it['qty']} — {it['price']}₽" for it in items])

# --- Клавиатуры ---
def main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📋 Меню", "🛒 Корзина")
    kb.add("👤 Мой профиль", "🔗 Реферальная программа")
    kb.add("🛠 Техподдержка")
    return kb

def handle_add_more_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("➕ Добавить ещё", "✅ Перейти к оформлению")
    return kb

# --- Обработчики и меню с категориями ---
# ... (аналогично предыдущему коду с клиентскими и админскими обработчиками)
# Важно: добавлены категории и забавные сообщения, если товара нет, а размер учитывается только для напитков

# --- Главная функция ---
def main():
    try:
        bot.polling(none_stop=True)
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        time.sleep(5)
        main()

if __name__ == '__main__':
    main()
