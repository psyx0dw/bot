# keyboards.py
# coding: utf-8
from telebot import types


def main_keyboard(categories):
    """Главная клавиатура с категориями и основными командами."""
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    
    # Категории товаров (по 2 в ряд)
    row = []
    for i, c in enumerate(categories, 1):
        row.append(types.KeyboardButton(c))
        if i % 2 == 0:
            kb.row(*row)
            row = []
    if row:
        kb.row(*row)
    
    # Основные команды
    kb.row(
        types.KeyboardButton("🛒 Корзина"),
        types.KeyboardButton("👤 Профиль")
    )
    kb.row(
        types.KeyboardButton("🔗 Реферальная"),
        types.KeyboardButton("🛠 Техподдержка")
    )
    
    return kb


def add_more_kb():
    """Клавиатура после добавления товара в корзину."""
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(
        types.KeyboardButton("➕ Добавить ещё"),
        types.KeyboardButton("🛒 Корзина")
    )
    kb.add(types.KeyboardButton("🏠 В меню"))
    return kb


def profile_keyboard():
    """Клавиатура профиля пользователя."""
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(
        types.KeyboardButton("💎 Мои баллы"),
        types.KeyboardButton("📦 История заказов")
    )
    kb.add(
        types.KeyboardButton("🔗 Реферальная"),
        types.KeyboardButton("📱 Мои данные")
    )
    kb.add(types.KeyboardButton("🏠 В меню"))
    return kb


def referral_keyboard():
    """Клавиатура реферальной программы."""
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton("📋 Показать ссылку"))
    kb.add(types.KeyboardButton("👥 Мои рефереры"))
    kb.add(types.KeyboardButton("💰 История бонусов"))
    kb.add(types.KeyboardButton("🏠 В меню"))
    return kb


def support_keyboard():
    """Клавиатура техподдержки."""
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton("❓ FAQ"))
    kb.add(types.KeyboardButton("💬 Написать в поддержку"))
    kb.add(types.KeyboardButton("📞 Контакты"))
    kb.add(types.KeyboardButton("🏠 В меню"))
    return kb


def admin_keyboard():
    """Клавиатура администратора."""
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    
    # Управление меню
    kb.add(
        types.KeyboardButton("📋 Просмотр меню"),
        types.KeyboardButton("📥 Загрузить Excel")
    )
    
    # Управление товарами
    kb.add(
        types.KeyboardButton("➕ Добавить товар"),
        types.KeyboardButton("➕ Добавить категорию")
    )
    
    # Статистика
    kb.add(
        types.KeyboardButton("📊 Статистика"),
        types.KeyboardButton("⚠️ Низкие остатки")
    )
    
    # Заказы и логи
    kb.add(
        types.KeyboardButton("🧾 Последние заказы"),
        types.KeyboardButton("📋 Логи операций")
    )
    
    # Пользователи
    kb.add(
        types.KeyboardButton("👥 Топ рефереров"),
        types.KeyboardButton("💎 Топ по баллам")
    )
    
    # Другое
    kb.add(types.KeyboardButton("🔴 Выход"))
    
    return kb


def order_confirmation_keyboard():
    """Клавиатура подтверждения заказа."""
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("✅ Подтвердить", callback_data="confirm_order"),
        types.InlineKeyboardButton("❌ Отмена", callback_data="cancel_order")
    )
    return kb


def admin_order_actions_keyboard(order_id):
    """Клавиатура действий с заказом для админа."""
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("✅ Готов", callback_data=f"ready|{order_id}"),
        types.InlineKeyboardButton("❌ Отмена", callback_data=f"cancel|{order_id}")
    )
    kb.add(types.InlineKeyboardButton("📞 Позвонить", callback_data=f"call|{order_id}"))
    return kb


def discount_confirmation_keyboard(final_price, discount, points_left):
    """Клавиатура подтверждения скидки."""
    kb = types.InlineKeyboardMarkup()
    
    text = (
        f"💰 Финальная цена: {final_price}₽\n"
        f"🎁 Скидка: {discount}₽\n"
        f"💎 Баллов останется: {points_left}"
    )
    
    kb.add(
        types.InlineKeyboardButton("✅ Оформить с скидкой", callback_data="checkout_with_discount"),
        types.InlineKeyboardButton("❌ Без скидки", callback_data="checkout_no_discount")
    )
    
    return kb, text


def stock_management_keyboard():
    """Клавиатура управления товарами для админа."""
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(
        types.KeyboardButton("✏️ Редактировать товар"),
        types.KeyboardButton("🗑 Удалить товар")
    )
    kb.add(
        types.KeyboardButton("📈 Увеличить остатки"),
        types.KeyboardButton("📉 Уменьшить остатки")
    )
    kb.add(types.KeyboardButton("🔴 Отмена"))
    return kb


def price_update_keyboard():
    """Клавиатура обновления цен."""
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton("🔺 Увеличить цену"))
    kb.add(types.KeyboardButton("🔻 Уменьшить цену"))
    kb.add(types.KeyboardButton("⚖️ Установить цену"))
    kb.add(types.KeyboardButton("🔴 Отмена"))
    return kb


def payment_method_keyboard():
    """Клавиатура выбора способа оплаты."""
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("💳 Карта", callback_data="pay_card"))
    kb.add(types.InlineKeyboardButton("📱 СМС-платеж", callback_data="pay_sms"))
    kb.add(types.InlineKeyboardButton("💰 Наличные", callback_data="pay_cash"))
    kb.add(types.InlineKeyboardButton("❌ Отмена", callback_data="cancel_payment"))
    return kb


def back_to_menu_keyboard():
    """Минимальная клавиатура для возврата в меню."""
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton("🏠 В меню"))
    return kb


def yes_no_keyboard():
    """Клавиатура да/нет."""
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("✅ Да", callback_data="yes"),
        types.InlineKeyboardButton("❌ Нет", callback_data="no")
    )
    return kb


def pagination_keyboard(page, total_pages, callback_prefix):
    """Клавиатура пагинации."""
    kb = types.InlineKeyboardMarkup()
    
    # Кнопки навигации
    buttons = []
    if page > 1:
        buttons.append(types.InlineKeyboardButton("◀️ Назад", callback_data=f"{callback_prefix}|{page-1}"))
    
    buttons.append(types.InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    
    if page < total_pages:
        buttons.append(types.InlineKeyboardButton("Вперёд ▶️", callback_data=f"{callback_prefix}|{page+1}"))
    
    kb.row(*buttons)
    
    return kb


# ===============================
# ==== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===
# ===============================

def build_category_inline_keyboard(categories):
    """Строит inline-клавиатуру категорий (для быстрого выбора)."""
    kb = types.InlineKeyboardMarkup()
    for cat_id, cat_name in categories:
        kb.add(types.InlineKeyboardButton(cat_name, callback_data=f"cat|{cat_id}"))
    return kb


def build_item_inline_keyboard(items, callback_prefix="select_item"):
    """Строит inline-клавиатуру товаров для выбора."""
    kb = types.InlineKeyboardMarkup()
    for item in items:
        sz = f" {item['size']}л" if item['size'] else ""
        text = f"{item['name']}{sz} — {item['price']}₽"
        kb.add(types.InlineKeyboardButton(text, callback_data=f"{callback_prefix}|{item['id']}"))
    return kb


def build_quantity_selector_keyboard(stock_id, max_qty=10):
    """Строит inline-клавиатуру для выбора количества товара."""
    kb = types.InlineKeyboardMarkup()
    
    # Максимум 5 кнопок
    step = max(1, max_qty // 5)
    quantities = [i for i in range(step, max_qty + 1, step)]
    if max_qty not in quantities:
        quantities.append(max_qty)
    
    for qty in quantities[:5]:  # Максимум 5 кнопок в ряд
        kb.add(types.InlineKeyboardButton(f"x{qty}", callback_data=f"add|{stock_id}|{qty}"))
    
    kb.add(types.InlineKeyboardButton("❌ Отмена", callback_data="cancel"))
    
    return kb


def build_admin_user_actions(user_id):
    """Клавиатура действий над пользователем для админа."""
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("📊 Статистика", callback_data=f"user_stats|{user_id}"),
        types.InlineKeyboardButton("📋 Заказы", callback_data=f"user_orders|{user_id}")
    )
    kb.add(
        types.InlineKeyboardButton("💎 Баллы", callback_data=f"user_points|{user_id}"),
        types.InlineKeyboardButton("🚫 Блокировать", callback_data=f"user_block|{user_id}")
    )
    return kb
