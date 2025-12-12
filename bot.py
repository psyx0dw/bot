# bot.py
# coding: utf-8
import os
import time
import io
import csv
import re
import logging
import threading
from datetime import datetime
from telebot import TeleBot, types, apihelper

from config import BOT_TOKEN, ADMIN_GROUP_ID, BONUS_PERCENT, MAX_DISCOUNT, REFERRAL_BONUS
from db import DBManager
from keyboards import main_keyboard, add_more_kb, admin_keyboard

# ===============================
# ==== ЛОГИРОВАНИЕ ==============
# ===============================
logging.basicConfig(
    filename='bot.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    encoding='utf-8'
)
logger = logging.getLogger(__name__)

bot = TeleBot(BOT_TOKEN, parse_mode="HTML")
db = DBManager()

# ===============================
# ==== RATE LIMITING ============
# ===============================
last_action = {}  # {chat_id: timestamp}
action_lock = threading.Lock()

def check_rate_limit(chat_id, cooldown=1):
    """Проверяет, не спамит ли пользователь (не более 1 действия в секунду)."""
    with action_lock:
        now = time.time()
        if chat_id in last_action and now - last_action[chat_id] < cooldown:
            return False
        last_action[chat_id] = now
        return True

# ===============================
# ==== ВАЛИДАЦИЯ ================
# ===============================
def validate_name(name):
    """Валидирует имя пользователя."""
    name = name.strip()
    if len(name) < 2 or len(name) > 100:
        return None, "Имя должно быть от 2 до 100 символов."
    # Разрешаем кириллицу, латиницу, пробелы, апостроф, дефис
    if not re.match(r"^[а-яА-ЯЁёa-zA-Z\s\-'ґҐєЄиїЇ]+$", name):
        return None, "Имя содержит недопустимые символы (только буквы, пробел, - и ')."
    return name, None

def validate_quantity(qty):
    """Валидирует количество товара."""
    try:
        qty = int(qty)
        if qty < 1 or qty > 999:
            return None, "Количество должно быть от 1 до 999."
        return qty, None
    except ValueError:
        return None, "Количество должно быть числом."

# ===============================
# ==== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===
# ===============================
def is_admin(chat_id):
    return str(chat_id) == str(ADMIN_GROUP_ID)

def admin_only(func):
    def wrapper(m):
        if not is_admin(m.chat.id):
            bot.send_message(m.chat.id, "❌ Доступ запрещён.")
            logger.warning(f"Попытка несанкционированного доступа: {m.chat.id}")
            return
        return func(m)
    return wrapper

def safe_send_message(chat_id, text, **kwargs):
    """Безопасная отправка сообщения с обработкой ошибок."""
    try:
        bot.send_message(chat_id, text, **kwargs)
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения {chat_id}: {e}")
        try:
            bot.send_message(chat_id, "❌ Техническая ошибка. Попробуйте позже.")
        except:
            pass

def calc_discount(total, points):
    """Рассчитывает скидку (не может быть больше MAX_DISCOUNT% от суммы)."""
    max_disc = int(total * MAX_DISCOUNT / 100)
    return min(points, max_disc)

def format_cart_rows(rows):
    """Форматирует строки корзины."""
    lines = []
    for r in rows:
        sz = f" {r['size']}л" if r['size'] else ""
        lines.append(f"• {r['name']}{sz} x{r['qty']} — {r['price']}₽")
    return "\n".join(lines)

# ===============================
# ==== СТАРТ & РЕГИСТРАЦИЯ ======
# ===============================
@bot.message_handler(commands=['start'])
def cmd_start(msg):
    chat_id = msg.chat.id
    
    if not check_rate_limit(chat_id):
        logger.warning(f"Спам /start от {chat_id}")
        return
    
    try:
        tg = str(chat_id)
        ref = None
        
        # Парсим реферальный код
        if msg.text and msg.text.startswith("/start "):
            ref = msg.text.split(" ", 1)[1].strip()
            # Валидируем реферальный код
            if ref == tg or not ref.isdigit() or not db.get_user(ref):
                ref = None
        
        user = db.get_user(tg)
        if user:
            logger.info(f"Повторный вход: {tg} ({user['name']})")
            safe_send_message(
                chat_id,
                f"☕ <b>С возвращением, {user['name']}</b>!",
                reply_markup=main_keyboard(db.get_categories())
            )
            return
        
        logger.info(f"Новый пользователь: {tg}, реферер: {ref}")
        m = bot.send_message(chat_id, "☕ Привет! Как тебя зовут?")
        bot.register_next_step_handler(m, finish_registration, ref)
    
    except Exception as e:
        logger.error(f"Ошибка в cmd_start: {e}", exc_info=True)
        safe_send_message(chat_id, "❌ Ошибка при запуске. Попробуйте позже.")

def finish_registration(msg, ref=None):
    """Завершение регистрации."""
    chat_id = msg.chat.id
    
    try:
        name, error = validate_name(msg.text)
        if error:
            logger.warning(f"Невалидное имя от {chat_id}: {msg.text}")
            m = bot.send_message(chat_id, f"❌ {error} Введи ещё раз.")
            bot.register_next_step_handler(m, finish_registration, ref)
            return
        
        tg = str(chat_id)
        db.add_user(tg, name, ref)
        
        logger.info(f"Пользователь зарегистрирован: {tg} ({name})")
        safe_send_message(
            chat_id,
            f"🎉 Добро пожаловать, <b>{name}</b>!",
            reply_markup=main_keyboard(db.get_categories())
        )
    
    except Exception as e:
        logger.error(f"Ошибка регистрации {chat_id}: {e}", exc_info=True)
        safe_send_message(chat_id, "❌ Ошибка при регистрации. Попробуйте /start.")

# ===============================
# ==== ПОКАЗ КАТЕГОРИЙ ==========
# ===============================
@bot.message_handler(func=lambda m: m.text and m.text in db.get_categories())
def show_category(m):
    chat_id = m.chat.id
    
    if not check_rate_limit(chat_id):
        return
    
    try:
        cat_name = m.text
        items = db.get_stock_by_category(cat_name)
        
        if not items:
            logger.info(f"Пустая категория: {cat_name} от {chat_id}")
            safe_send_message(
                chat_id,
                f"😅 В категории <b>{cat_name}</b> пока пусто."
            )
            return
        
        text = f"📂 <b>{cat_name}</b>\n\n"
        kb = types.InlineKeyboardMarkup()
        
        for it in items:
            if it["quantity"] > 0:  # Показываем только доступные товары
                stock_id, name, price = it["id"], it["name"], it["price"]
                sz = f" {it['size']}л" if it["has_size"] else ""
                text += f"• {name}{sz} — {price}₽ (Ост: {it['quantity']} шт)\n"
                kb.add(types.InlineKeyboardButton(
                    f"Добавить {name}{sz}",
                    callback_data=f"add|{stock_id}|1"
                ))
        
        logger.info(f"Показана категория {cat_name} пользователю {chat_id}")
        safe_send_message(chat_id, text, reply_markup=kb)
    
    except Exception as e:
        logger.error(f"Ошибка show_category {chat_id}: {e}", exc_info=True)
        safe_send_message(chat_id, "❌ Ошибка при загрузке категории.")

# ===============================
# ==== ДОБАВЛЕНИЕ В КОРЗИНУ =====
# ===============================
@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("add|"))
def cb_add_to_cart(c):
    chat_id = c.from_user.id
    
    if not check_rate_limit(chat_id):
        bot.answer_callback_query(c.id, "⏳ Не спешите, подождите секунду.")
        return
    
    try:
        parts = c.data.split("|")
        if len(parts) < 3:
            bot.answer_callback_query(c.id, "❌ Ошибка данных.")
            return
        
        stock_id = int(parts[1])
        qty = int(parts[2])
        
        qty, error = validate_quantity(qty)
        if error:
            bot.answer_callback_query(c.id, f"❌ {error}")
            return
        
        item = db.get_stock_item(stock_id)
        if not item:
            logger.warning(f"Товар не найден: {stock_id}")
            bot.answer_callback_query(c.id, "❌ Товар не найден.")
            return
        
        if item["quantity"] < qty:
            bot.answer_callback_query(c.id, f"❌ Осталось только {item['quantity']} шт.")
            logger.info(f"Недостаточно товара: {stock_id}, запрос {qty}, остаток {item['quantity']}")
            return
        
        tg = str(chat_id)
        try:
            db.add_to_cart(tg, stock_id, qty)
        except ValueError as e:
            logger.warning(f"Ошибка добавления в корзину {tg}: {e}")
            bot.answer_callback_query(c.id, str(e))
            return
        
        sz = f" {item['size']}л" if item["has_size"] else ""
        logger.info(f"Товар добавлен в корзину: {tg}, {item['name']}, кол-во {qty}")
        bot.answer_callback_query(c.id, f"✅ Добавлено: {item['name']}{sz} x{qty}")
        safe_send_message(
            chat_id,
            f"✅ {item['name']}{sz} добавлено в корзину",
            reply_markup=add_more_kb()
        )
    
    except ValueError as e:
        logger.error(f"Ошибка парсинга cb_add_to_cart {chat_id}: {e}")
        bot.answer_callback_query(c.id, "❌ Ошибка. Попробуйте позже.")
    except Exception as e:
        logger.error(f"Ошибка cb_add_to_cart {chat_id}: {e}", exc_info=True)
        bot.answer_callback_query(c.id, "❌ Техническая ошибка.")

# ===============================
# ==== КОРЗИНА и ОФОРМЛЕНИЕ =====
# ===============================
@bot.message_handler(func=lambda m: m.text == "🛒 Корзина")
def show_cart(m):
    chat_id = m.chat.id
    
    if not check_rate_limit(chat_id):
        return
    
    try:
        tg = str(chat_id)
        user = db.get_user(tg)
        
        if not user:
            logger.warning(f"Пользователь не найден при показе корзины: {tg}")
            safe_send_message(chat_id, "⚠️ Сначала /start.")
            return
        
        rows = db.get_cart(tg)
        if not rows:
            logger.info(f"Пустая корзина: {tg}")
            safe_send_message(
                chat_id,
                "🛒 Корзина пуста.",
                reply_markup=main_keyboard(db.get_categories())
            )
            return
        
        total = sum(r["price"] * r["qty"] for r in rows)
        points = user["points"] or 0
        disc = calc_discount(total, points)
        final = total - disc
        remaining_points = max(0, points - disc)
        
        text = (
            f"🛒 <b>Твоя корзина:</b>\n{format_cart_rows(rows)}\n\n"
            f"💰 Итого: {total}₽\n"
            f"🎁 Скидка (баллов: {disc}): -{disc}₽\n"
            f"📦 <b>К оплате: {final}₽</b>\n"
            f"💎 Баллов: {points} → {remaining_points} (после использования)"
        )
        
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("✅ Оформить заказ", callback_data="checkout"))
        kb.add(types.InlineKeyboardButton("↩️ Отмена", callback_data="cancel_checkout"))
        
        logger.info(f"Показана корзина: {tg}, сумма {total}, скидка {disc}")
        safe_send_message(chat_id, text, reply_markup=kb)
    
    except Exception as e:
        logger.error(f"Ошибка show_cart {chat_id}: {e}", exc_info=True)
        safe_send_message(chat_id, "❌ Ошибка при загрузке корзины.")

@bot.callback_query_handler(func=lambda c: c.data == "cancel_checkout")
def cb_cancel_checkout(c):
    """Отмена оформления заказа."""
    chat_id = c.from_user.id
    tg = str(chat_id)
    
    try:
        bot.edit_message_reply_markup(
            c.message.chat.id,
            c.message.message_id,
            reply_markup=None
        )
        bot.answer_callback_query(c.id, "❌ Заказ отменён.")
        logger.info(f"Заказ отменён: {tg}")
    except Exception as e:
        logger.error(f"Ошибка отмены: {e}")

@bot.callback_query_handler(func=lambda c: c.data == "checkout")
def cb_checkout(c):
    chat_id = c.from_user.id
    tg = str(chat_id)
    
    if not check_rate_limit(chat_id, cooldown=2):  # 2 сек защита от двойного клика
        bot.answer_callback_query(c.id, "⏳ Подождите...")
        return
    
    try:
        user = db.get_user(tg)
        rows = db.get_cart(tg)
        
        if not rows:
            logger.warning(f"Корзина пуста при оформлении: {tg}")
            bot.answer_callback_query(c.id, "Корзина пуста.")
            bot.edit_message_reply_markup(c.message.chat.id, c.message.message_id, reply_markup=None)
            return
        
        # КРИТИЧЕСКАЯ ПРОВЕРКА: наличие на складе
        unavailable = []
        for r in rows:
            s = db.get_stock_item(r["stock_id"])
            if not s or s["quantity"] < r["qty"]:
                unavailable.append((r['name'], r['qty'], s['quantity'] if s else 0))
        
        if unavailable:
            error_text = "❌ Некоторые товары недоступны:\n"
            for name, needed, available in unavailable:
                error_text += f"• {name}: осталось {available}, нужно {needed}\n"
            bot.answer_callback_query(c.id, error_text[:100])
            logger.warning(f"Недостаток товара при заказе {tg}: {unavailable}")
            return
        
        total = sum(r["price"] * r["qty"] for r in rows)
        points = user["points"] or 0
        disc = calc_discount(total, points)
        final = total - disc
        is_first = user["orders"] == 0
        
        # КРИТИЧЕСКАЯ ПРОВЕРКА: достаточно ли баллов для скидки
        if points < disc:
            bot.answer_callback_query(c.id, "❌ Недостаточно баллов.")
            logger.warning(f"Недостаточно баллов {tg}: есть {points}, нужно {disc}")
            return
        
        # === ИСПОЛЬЗУЕМ ТРАНЗАКЦИЮ ===
        try:
            # Списание баллов
            if disc > 0:
                db.update_points(tg, -disc)
            
            # Уменьшение остатков (повторная проверка перед вычитанием)
            for r in rows:
                s = db.get_stock_item(r["stock_id"])
                if not s or s["quantity"] < r["qty"]:
                    raise ValueError(f"Товар {r['name']} недоступен (race condition)")
                db.reduce_stock(r["stock_id"], r["qty"])
            
            # Создание заказа
            items = [
                {
                    "name": r["name"],
                    "size": r["size"],
                    "price": r["price"],
                    "qty": r["qty"]
                }
                for r in rows
            ]
            oid = db.create_order(tg, items, final)
            
            # Реферальный бонус
            if is_first:
                ref = db.get_referrer(tg)
                if ref:
                    db.update_points(ref, REFERRAL_BONUS)
                    safe_send_message(
                        int(ref),
                        f"🎉 Ваш друг <b>{user['name']}</b> сделал первый заказ! +{REFERRAL_BONUS} 💎"
                    )
                    logger.info(f"Реферальный бонус: {ref} получил {REFERRAL_BONUS}")
            
            # Начисление баллов
            earned = int(final * BONUS_PERCENT / 100)
            db.update_points(tg, earned)
            
            # Очистка корзины
            db.clear_cart(tg)
            
        except ValueError as e:
            logger.error(f"Ошибка при оформлении заказа {tg}: {e}")
            bot.answer_callback_query(c.id, f"❌ {str(e)}")
            return
        
        # Успешное оформление
        logger.info(f"Заказ оформлен: {oid}, пользователь {tg}, сумма {final}₽")
        
        bot.answer_callback_query(c.id, f"✅ Заказ №{oid} оформлен!")
        safe_send_message(
            chat_id,
            (f"✅ <b>Заказ №{oid} оформлен!</b>\n"
             f"💳 К оплате: {final}₽\n"
             f"🎯 Баллы: +{earned} 💎"),
            reply_markup=main_keyboard(db.get_categories())
        )
        
        # Уведомление администратора
        admin_kb = types.InlineKeyboardMarkup()
        admin_kb.add(types.InlineKeyboardButton("✅ Готов", callback_data=f"ready|{tg}|{oid}"))
        admin_text = (
            f"📦 <b>Новый заказ №{oid}</b>\n"
            f"👤 {user['name']} (ID: {tg})\n"
            f"📋 {format_cart_rows(rows)}\n"
            f"💰 <b>К оплате: {final}₽</b>\n"
            f"🎁 Скидка: {disc}₽\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
        safe_send_message(ADMIN_GROUP_ID, admin_text, reply_markup=admin_kb)
    
    except Exception as e:
        logger.error(f"Критическая ошибка checkout {tg}: {e}", exc_info=True)
        bot.answer_callback_query(c.id, "❌ Техническая ошибка при оформлении.")
        safe_send_message(
            ADMIN_GROUP_ID,
            f"🔴 <b>ОШИБКА ЗАКАЗА</b>\nПользователь: {tg}\nОшибка: {str(e)}"
        )

# ===============================
# ==== ДОБАВИТЬ ЕЩЁ ============
# ===============================
@bot.message_handler(func=lambda m: m.text == "➕ Добавить ещё")
def msg_add_more(m):
    chat_id = m.chat.id
    try:
        logger.info(f"Возврат в меню: {chat_id}")
        safe_send_message(
            chat_id,
            "📋 Выбери ещё блюда:",
            reply_markup=main_keyboard(db.get_categories())
        )
    except Exception as e:
        logger.error(f"Ошибка в msg_add_more: {e}")

# ===============================
# ==== АДМИН-ПАНЕЛЬ ============
# ===============================
@bot.message_handler(commands=['admin'])
@admin_only
def admin_panel(m):
    chat_id = m.chat.id
    logger.info(f"Админ вошёл в панель: {chat_id}")
    safe_send_message(chat_id, "🔥 Админ-панель:", reply_markup=admin_keyboard())

@bot.message_handler(func=lambda m: m.text == "📋 Просмотр меню")
@admin_only
def admin_view_menu(m):
    chat_id = m.chat.id
    try:
        start = time.time()
        kb = types.InlineKeyboardMarkup()
        cats = db.get_categories_with_id()
        
        for cat_id, cat_name in cats:
            kb.add(types.InlineKeyboardButton(cat_name[:30], callback_data=f"admin_view|{cat_id}"))
        
        logger.info(f"Админ просмотр меню: {chat_id}, загрузка {time.time() - start:.2f}s")
        safe_send_message(chat_id, "Выберите категорию:", reply_markup=kb)
    
    except Exception as e:
        logger.error(f"Ошибка admin_view_menu: {e}")
        safe_send_message(chat_id, "❌ Ошибка загрузки меню.")

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("admin_view|"))
def cb_admin_view(c):
    chat_id = c.from_user.id
    
    if not is_admin(chat_id):
        bot.answer_callback_query(c.id, "❌ Доступ запрещён.")
        return
    
    try:
        _, cat_id = c.data.split("|")
        cat_id = int(cat_id)
        
        cat_name = db.get_category_name_by_id(cat_id)
        items = db.get_stock_by_category_id(cat_id)
        
        if not items:
            bot.edit_message_text(
                f"📋 <b>{cat_name}</b> — пусто",
                c.message.chat.id,
                c.message.message_id,
                parse_mode="HTML"
            )
            return
        
        text = f"📋 <b>{cat_name}</b>\n\n" + "\n".join(
            f"• {i['name']} {i['size'] if i['has_size'] else ''} — {i['price']}₽ (Ост: {i['quantity']})"
            for i in items
        )
        
        logger.info(f"Админ просмотр категории {cat_name}: {chat_id}")
        bot.edit_message_text(text, c.message.chat.id, c.message.message_id, parse_mode="HTML")
    
    except Exception as e:
        logger.error(f"Ошибка cb_admin_view: {e}")
        bot.answer_callback_query(c.id, "❌ Ошибка.")

# ===============================
# ==== ЗАПУСК ===================
# ===============================
if __name__ == "__main__":
    logger.info("🚀 Бот запускается...")
    print("🚀 Бот запускается...")
    
    apihelper.API_MAX_ASYNC_REQUESTS = 5
    
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=30)
        except KeyboardInterrupt:
            logger.info("🛑 Бот остановлен вручную (Ctrl+C).")
            print("🛑 Бот остановлен.")
            break
        except Exception as e:
            logger.error(f"⚠️ Ошибка polling: {e}", exc_info=True)
            print(f"⚠️ Ошибка: {e}. Перезапуск через 3 сек...")
            time.sleep(3)
