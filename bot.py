# coding: utf-8
import telebot
from telebot import types
import sqlite3
import threading
import os
import datetime
import time
from config import BOT_TOKEN, ADMIN_GROUP_ID, BONUS_PERCENT, MAX_DISCOUNT, REFERRAL_BONUS, DB_PATH

bot = telebot.TeleBot(BOT_TOKEN)
local_storage = threading.local()


class DBManager:
    """Управление базой данных."""
    def __init__(self, db_path):
        self.db_path = db_path
        self._init_db()

    def get_conn(self):
        if not hasattr(local_storage, 'conn'):
            local_storage.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        return local_storage.conn

    def _init_db(self, sql_file="models.sql"):
        if not os.path.exists(self.db_path):
            with open(sql_file, 'r', encoding='utf-8') as f:
                script = f.read()
            with self.get_conn() as c:
                c.executescript(script)

    # Пользователи и заказы
    def get_user(self, tg_id):
        cur = self.get_conn().cursor()
        cur.execute("""
            SELECT u.id, u.name, u.points,
                   (SELECT COUNT(*) FROM users WHERE referrer_id = u.id),
                   (SELECT COUNT(*) FROM orders WHERE user_id = u.id)
            FROM users u WHERE telegram_id=?
        """, (tg_id,))
        return cur.fetchone()

    def get_user_id(self, tg_id):
        cur = self.get_conn().cursor()
        cur.execute("SELECT id FROM users WHERE telegram_id=?", (tg_id,))
        r = cur.fetchone()
        return r[0] if r else None

    def add_user(self, tg_id, name, referrer_tg=None):
        conn = self.get_conn()
        cur = conn.cursor()
        ref_id = None
        if referrer_tg:
            cur.execute("SELECT id FROM users WHERE telegram_id=?", (referrer_tg,))
            r = cur.fetchone()
            ref_id = r[0] if r else None
        cur.execute("""
            INSERT OR IGNORE INTO users (telegram_id, name, referrer_id)
            VALUES (?, ?, ?)
        """, (tg_id, name, ref_id))
        conn.commit()

    def update_points(self, tg_id, delta):
        conn = self.get_conn()
        conn.execute("UPDATE users SET points=COALESCE(points,0)+? WHERE telegram_id=?", (delta, tg_id))
        conn.commit()

    # Меню
    def get_menu(self):
        cur = self.get_conn().cursor()
        cur.execute("""
            SELECT category, name, size, has_size, price, quantity
            FROM stock
            ORDER BY category, name, has_size DESC, CAST(size AS REAL)
        """)
        return cur.fetchall()

    def get_distinct_categories(self):
        cur = self.get_conn().cursor()
        cur.execute("SELECT DISTINCT category FROM stock ORDER BY category")
        return [r[0] for r in cur.fetchall()]

    def get_stock_by_name_size(self, name, size):
        cur = self.get_conn().cursor()
        if size:
            cur.execute("""
                SELECT price, quantity FROM stock
                WHERE LOWER(name)=LOWER(?) AND size=?
            """, (name, size))
        else:
            cur.execute("""
                SELECT price, quantity FROM stock
                WHERE LOWER(name)=LOWER(?) AND has_size=0
            """, (name,))
        return cur.fetchone()

    def get_stock_by_category(self, category, limit, offset):
        cur = self.get_conn().cursor()
        cur.execute("""
            SELECT name, size, price, quantity FROM stock
            WHERE category=? ORDER BY has_size DESC, CAST(size AS REAL)
            LIMIT ? OFFSET ?
        """, (category, limit, offset))
        return cur.fetchall()

    def reduce_stock(self, name, size, qty):
        conn = self.get_conn()
        if size:
            conn.execute("""
                UPDATE stock SET quantity=quantity-?
                WHERE name=? AND size=? AND quantity>=?
            """, (qty, name, size, qty))
            conn.commit()
            cur = conn.cursor()
            cur.execute("SELECT quantity FROM stock WHERE name=? AND size=?", (name, size))
        else:
            conn.execute("""
                UPDATE stock SET quantity=quantity-?
                WHERE name=? AND has_size=0 AND quantity>=?
            """, (qty, name, qty))
            conn.commit()
            cur = conn.cursor()
            cur.execute("SELECT quantity FROM stock WHERE name=? AND has_size=0", (name,))
        new_q = cur.fetchone()[0]
        if new_q < 3:
            bot.send_message(ADMIN_GROUP_ID,
                             f"⚠️ Низкий остаток: {name}{' '+size+'л' if size else ''} — {new_q} шт",
                             parse_mode="HTML")

    # Корзина
    def get_cart(self, tg_id):
        cur = self.get_conn().cursor()
        cur.execute("""
            SELECT c.name, c.size, c.price, c.qty
            FROM cart c JOIN users u ON c.user_id=u.id
            WHERE u.telegram_id=?
        """, (tg_id,))
        return [{'name': r[0], 'size': r[1], 'price': r[2], 'qty': r[3]} for r in cur.fetchall()]

    def add_to_cart(self, tg_id, name, size, price, qty):
        uid = self.get_user_id(tg_id)
        conn = self.get_conn()
        conn.execute("""
            INSERT INTO cart (user_id, name, size, price, qty)
            VALUES (?, ?, ?, ?, ?)
        """, (uid, name, size, price, qty))
        conn.commit()

    def clear_cart(self, tg_id):
        conn = self.get_conn()
        conn.execute("""
            DELETE FROM cart WHERE user_id=(SELECT id FROM users WHERE telegram_id=?)
        """, (tg_id,))
        conn.commit()

    # Заказы
    def create_order(self, tg_id, items, total):
        uid = self.get_user_id(tg_id)
        conn = self.get_conn()
        cur = conn.cursor()
        cur.execute("INSERT INTO orders (user_id, total, created_at) VALUES (?,?,?)",
                    (uid, total, datetime.datetime.now()))
        oid = cur.lastrowid
        for it in items:
            cur.execute(
                "INSERT INTO order_items(order_id,name,size,price,qty) VALUES(?,?,?,?,?)",
                (oid, it['name'], it['size'], it['price'], it['qty'])
            )
        conn.commit()
        return oid

    def get_admin_stats(self):
        cur = self.get_conn().cursor()
        cur.execute("SELECT COUNT(*), SUM(total) FROM orders")
        orders, rev = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM users")
        users = cur.fetchone()[0]
        return orders or 0, rev or 0, users

    def get_low_stock(self):
        cur = self.get_conn().cursor()
        cur.execute("""
            SELECT category, name, size, quantity FROM stock
            WHERE quantity<3 ORDER BY quantity
        """)
        return cur.fetchall()

    def get_recent_orders(self, limit=10):
        cur = self.get_conn().cursor()
        cur.execute(f"""
            SELECT o.id, u.name, o.total, o.created_at
            FROM orders o JOIN users u ON o.user_id=u.id
            ORDER BY o.created_at DESC LIMIT {limit}
        """)
        return cur.fetchall()

    # Админ: CRUD для stock
    def admin_update_item(self, cat, name, size, price, qty, has_size):
        conn = self.get_conn()
        conn.execute("""
            INSERT OR REPLACE INTO stock(category,name,size,has_size,price,quantity)
            VALUES(?,?,?,?,?,?)
        """, (cat, name, size, has_size, price, qty))
        conn.commit()

    def admin_delete_item(self, name, size):
        conn = self.get_conn()
        if size:
            cur = conn.execute("DELETE FROM stock WHERE name=? AND size=?", (name, size))
        else:
            cur = conn.execute("DELETE FROM stock WHERE name=? AND has_size=0", (name,))
        conn.commit()
        return cur.rowcount > 0


db = DBManager(DB_PATH)


def calc_discount(total, points):
    return min(points, int(total * MAX_DISCOUNT))


def format_cart(items):
    lines = []
    for it in items:
        sz = f" {it['size']}л" if it['size'] else ""
        lines.append(f"• {it['name']}{sz} x{it['qty']} — {it['price']}₽")
    return "\n".join(lines)


# Keyboards
def main_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📋 Меню", "🛒 Корзина")
    kb.add("👤 Профиль", "🔗 Реферальная")
    kb.add("🛠 Техподдержка")
    return kb


def add_more_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("➕ Добавить ещё", "✅ Оформить")
    return kb


# Handlers
@bot.message_handler(commands=["start"])
def start(msg):
    tg = str(msg.chat.id)
    ref = None
    if msg.text.startswith("/start "):
        ref = msg.text.split(" ", 1)[1]
        if ref == tg or not db.get_user(ref):
            ref = None
    user = db.get_user(tg)
    if user:
        bot.send_message(msg.chat.id, f"☕ С возвращением, <b>{user[1]}</b>!",
                         reply_markup=main_kb(), parse_mode="HTML")
    else:
        m = bot.send_message(msg.chat.id, "☕ Привет! Как тебя зовут?", parse_mode="HTML")
        bot.register_next_step_handler(m, finish_reg, ref)


def finish_reg(msg, ref=None):
    name = msg.text.strip()
    if len(name) < 2:
        m = bot.send_message(msg.chat.id, "❌ Слишком коротко, напиши полное имя.", parse_mode="HTML")
        bot.register_next_step_handler(m, finish_reg, ref)
        return
    tg = str(msg.chat.id)
    db.add_user(tg, name, ref)
    bot.send_message(tg, f"🎉 Добро пожаловать, <b>{name}</b>!", reply_markup=main_kb(), parse_mode="HTML")


@bot.message_handler(func=lambda m: m.text == "📋 Меню")
def show_menu(m):
    rows = db.get_menu()
    if not rows:
        bot.send_message(m.chat.id, "📭 Меню пусто.", parse_mode="HTML")
        return
    text = "📋 <b>Меню:</b>\n"
    cat0 = None
    for cat, name, size, hs, price, qty in rows:
        if cat != cat0:
            text += f"\n🔸 <b>{cat}</b>\n"
            cat0 = cat
        sz = f" {size}л" if hs else ""
        text += f"• {name}{sz} — {price}₽ ({qty})\n"
    bot.send_message(m.chat.id, text, parse_mode="HTML")


@bot.message_handler(func=lambda m: m.text == "🛒 Корзина")
def show_cart(m):
    tg = str(m.chat.id)
    user = db.get_user(tg)
    if not user:
        bot.send_message(m.chat.id, "⚠️ Сначала /start.", parse_mode="HTML")
        return
    items = db.get_cart(tg)
    if not items:
        bot.send_message(m.chat.id, "🛒 Корзина пуста.", parse_mode="HTML")
        return
    total = sum(it['price'] * it['qty'] for it in items)
    points = user[2] or 0
    disc = calc_discount(total, points)
    final = total - disc
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✅ Оформить", callback_data="confirm_std"))
    bot.send_message(m.chat.id,
                     f"🛒 <b>Твоя корзина:</b>\n{format_cart(items)}\n\n"
                     f"💰 Итого: {total}₽\n"
                     f"🎁 Скидка: {disc}₽\n"
                     f"📦 К оплате: {final}₽",
                     reply_markup=kb, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data == "confirm_std")
def confirm_std(c):
    tg = str(c.message.chat.id)
    user = db.get_user(tg)
    items = db.get_cart(tg)
    total = sum(it['price'] * it['qty'] for it in items)
    points = user[2] or 0
    disc = calc_discount(total, points)
    final = total - disc
    process_order(tg, user, items, final, disc)


def process_order(tg, user, items, final, discount):
    # проверка наличия
    for it in items:
        _, qty = db.get_stock_by_name_size(it['name'], it['size'])
        if qty < it['qty']:
            bot.send_message(tg, f"❌ «{it['name']}» закончился.", parse_mode="HTML")
            return
    # бонусы рефералов
    uid = db.get_user_id(tg)
    cur = db.get_conn().cursor()
    cur.execute("SELECT COUNT(*) FROM orders WHERE user_id=?", (uid,))
    if cur.fetchone()[0] == 0:
        cur.execute("SELECT referrer_id FROM users WHERE id=?", (uid,))
        ref = cur.fetchone()[0]
        if ref:
            db.update_points(tg, 50)
            cur.execute("SELECT telegram_id FROM users WHERE id=?", (ref,))
            rtg = cur.fetchone()[0]
            db.update_points(rtg, REFERRAL_BONUS)
            bot.send_message(rtg,
                             f"🎉 Твой друг {user[1]} сделал первый заказ — тебе +{REFERRAL_BONUS} баллов!",
                             parse_mode="HTML")
            db.get_conn().execute("UPDATE users SET referrer_id=NULL WHERE id=?", (uid,))
            db.get_conn().commit()
    # завершение
    for it in items:
        db.reduce_stock(it['name'], it['size'], it['qty'])
    earned = int(final * BONUS_PERCENT)
    db.update_points(tg, earned)
    db.clear_cart(tg)
    oid = db.create_order(tg, items, final)
    bot.send_message(tg,
                     f"✅ Заказ №{oid} оформлен!\n💳 К оплате: {final}₽\n🎯 Баллы: +{earned}",
                     reply_markup=main_kb(), parse_mode="HTML")
    # уведомление админа
    text = f"📦 Новый заказ №{oid}\n👤 {user[1]}\n💰 {final}₽"
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✅ Готов", callback_data=f"ready|{tg}|{oid}"))
    bot.send_message(ADMIN_GROUP_ID, text, reply_markup=kb, parse_mode="HTML")


@bot.callback_query_handler(func=lambda c: c.data.startswith("ready|"))
def mark_ready(c):
    _, tg, oid = c.data.split("|")
    bot.send_message(int(tg), f"✅ Ваш заказ №{oid} готов!", parse_mode="HTML")
    bot.answer_callback_query(c.id, "Клиент уведомлён")


@bot.message_handler(func=lambda m: m.text == "👤 Профиль")
def profile(m):
    tg = str(m.chat.id)
    user = db.get_user(tg)
    if not user:
        bot.send_message(m.chat.id, "⚠️ Сначала /start.", parse_mode="HTML")
        return
    _, name, pts, refs, orders = user
    bot.send_message(m.chat.id,
                     f"👤 <b>{name}</b>\n• Баллы: {pts}\n• Друзей: {refs}\n• Заказов: {orders}",
                     parse_mode="HTML")


@bot.message_handler(func=lambda m: m.text == "🔗 Реферальная")
def referral(m):
    tg = str(m.chat.id)
    user = db.get_user(tg)
    if not user:
        bot.send_message(m.chat.id, "⚠️ Сначала /start.", parse_mode="HTML")
        return
    link = f"https://t.me/{bot.get_me().username}?start={tg}"
    bot.send_message(m.chat.id,
                     f"🔗 Твоя ссылка:\n{link}\n+{REFERRAL_BONUS} баллов после первого заказа друга",
                     parse_mode="HTML")


@bot.message_handler(func=lambda m: m.text == "🛠 Техподдержка")
def support(m):
    bot.send_message(m.chat.id, "🛠 Техподдержка: @tamiklung", parse_mode="HTML")


# Админ-панель
@bot.message_handler(commands=["admin"])
def admin_panel(m):
    if m.chat.id != ADMIN_GROUP_ID:
        return
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📋 Просмотр меню", "➕ Добавить товар")
    kb.add("📊 Статистика", "⚠️ Низкие остатки", "🧾 Последние")
    bot.send_message(m.chat.id, "🔥 Админ-панель:", reply_markup=kb)


@bot.message_handler(func=lambda m: m.text == "📋 Просмотр меню")
def admin_view_menu(m):
    if m.chat.id != ADMIN_GROUP_ID:
        return
    cats = db.get_distinct_categories()
    kb = types.InlineKeyboardMarkup(row_width=2)
    for c in cats:
        kb.add(types.InlineKeyboardButton(c, callback_data=f"view_cat|{c}|0"))
    bot.send_message(m.chat.id, "Выберите категорию:", reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith("view_cat|"))
def admin_cat_page(c):
    _, cat, off = c.data.split("|")
    off = int(off)
    items = db.get_stock_by_category(cat, 5, off)
    text = f"📋 <b>{cat}</b> (страница {off//5+1}):\n"
    for i, (n, s, p, q) in enumerate(items, 1):
        sz = f" {s}л" if s else ""
        text += f"{i}. {n}{sz} — {p}₽ ({q})\n"
    kb = types.InlineKeyboardMarkup()
    if off >= 5:
        kb.add(types.InlineKeyboardButton("◀️", callback_data=f"view_cat|{cat}|{off-5}"))
    if len(items) == 5:
        kb.add(types.InlineKeyboardButton("▶️", callback_data=f"view_cat|{cat}|{off+5}"))
    bot.edit_message_text(text, c.message.chat.id, c.message.message_id,
                          reply_markup=kb, parse_mode="HTML")


@bot.message_handler(func=lambda m: m.text == "➕ Добавить товар")
def admin_add_prompt(m):
    if m.chat.id != ADMIN_GROUP_ID:
        return
    msg = bot.send_message(m.chat.id,
                           "Введите: Категория;Название;[Объём л если напиток или пусто];Цена;Остаток",
                           parse_mode="HTML")
    bot.register_next_step_handler(msg, admin_add_apply)


def admin_add_apply(m):
    cat, name, size, price, qty = [x.strip() for x in m.text.split(";")]
    has_size = 1 if size else 0
    sz = size if has_size else None
    db.admin_update_item(cat, name, sz, int(price), int(qty), has_size)
    bot.send_message(m.chat.id, f"✅ {name}{' '+size+'л' if size else ''} добавлен.")


@bot.message_handler(func=lambda m: m.text == "📊 Статистика")
def admin_stats(m):
    if m.chat.id != ADMIN_GROUP_ID:
        return
    cnt, rev, users = db.get_admin_stats()
    bot.send_message(m.chat.id,
                     f"📊 Заказы: {cnt}\n💰 Выручка: {rev}₽\n👥 Пользователи: {users}",
                     parse_mode="HTML")


@bot.message_handler(func=lambda m: m.text == "⚠️ Низкие остатки")
def admin_low(m):
    if m.chat.id != ADMIN_GROUP_ID:
        return
    items = db.get_low_stock()
    if not items:
        bot.send_message(m.chat.id, "✅ Всё в норме.", parse_mode="HTML")
        return
    txt = "⚠️ Низкий остаток:\n"
    for cat, n, s, q in items:
        sz = f" {s}л" if s else ""
        txt += f"• {n}{sz} — {q}\n"
    bot.send_message(m.chat.id, txt, parse_mode="HTML")


@bot.message_handler(func=lambda m: m.text == "🧾 Последние")
def admin_recent(m):
    if m.chat.id != ADMIN_GROUP_ID:
        return
    orders = db.get_recent_orders()
    if not orders:
        bot.send_message(m.chat.id, "🧾 Нет заказов.", parse_mode="HTML")
        return
    txt = "🧾 Последние:\n"
    for oid, uname, tot, dt in orders:
        t = dt.split('.')[0]
        txt += f"№{oid} — {uname} — {tot}₽ — {t}\n"
    bot.send_message(m.chat.id, txt, parse_mode="HTML")


@bot.message_handler(content_types=["text"])
def add_to_cart_handler(m):
    tg = str(m.chat.id)
    txt = m.text.strip()
    parts = txt.rsplit(" ", 1)
    name, size = txt, None
    if len(parts) == 2 and parts[1].endswith("л"):
        name, size = parts[0], parts[1].replace("л", "")
    pq = db.get_stock_by_name_size(name, size)
    if not pq:
        return
    price, qty = pq
    if qty < 1:
        bot.send_message(tg, f"❌ «{txt}» нет в наличии.", parse_mode="HTML")
        return
    db.add_to_cart(tg, name, size, price, 1)
    bot.send_message(tg, f"✅ Добавил {name}{' '+size+'л' if size else ''}.",
                     reply_markup=add_more_kb(), parse_mode="HTML")


def main():
    while True:
        try:
            bot.polling(none_stop=True)
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(5)


if __name__ == '__main__':
    main()
