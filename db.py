# db.py
# coding: utf-8
import sqlite3
import json
import logging
from datetime import datetime
from contextlib import contextmanager
from threading import Lock

logger = logging.getLogger(__name__)

class DBManager:
    def __init__(self, db_path='data.db'):
        self.db_path = db_path
        self.lock = Lock()  # Защита от race conditions
        self._init_db()
    
    def _init_db(self):
        """Инициализирует БД из models.sql."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute("PRAGMA journal_mode = WAL")
                with open('models.sql', 'r', encoding='utf-8') as f:
                    conn.executescript(f.read())
                conn.commit()
            logger.info("БД инициализирована успешно.")
        except Exception as e:
            logger.error(f"Ошибка инициализации БД: {e}", exc_info=True)
            raise
    
    @contextmanager
    def get_connection(self):
        """Context manager для безопасной работы с БД."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Ошибка БД: {e}", exc_info=True)
            raise
        finally:
            conn.close()
    
    # =============== ПОЛЬЗОВАТЕЛИ ===============
    
    def add_user(self, tg_id, name, referrer_tg_id=None):
        """Добавляет нового пользователя."""
        with self.lock:
            try:
                with self.get_connection() as conn:
                    referrer_id = None
                    if referrer_tg_id:
                        referrer = conn.execute(
                            "SELECT id FROM users WHERE telegram_id = ?",
                            (str(referrer_tg_id),)
                        ).fetchone()
                        if referrer:
                            referrer_id = referrer['id']
                    
                    conn.execute(
                        "INSERT INTO users (telegram_id, name, referrer_id) VALUES (?, ?, ?)",
                        (str(tg_id), name, referrer_id)
                    )
                    
                    logger.info(f"Пользователь добавлен: {tg_id} ({name})")
                    
                    # Логирование в audit_log
                    user_id = conn.execute(
                        "SELECT id FROM users WHERE telegram_id = ?",
                        (str(tg_id),)
                    ).fetchone()['id']
                    
                    conn.execute(
                        "INSERT INTO audit_log (action, user_id, details) VALUES (?, ?, ?)",
                        ('user_created', user_id, json.dumps({'name': name, 'referrer': referrer_id}))
                    )
            except sqlite3.IntegrityError:
                logger.warning(f"Пользователь уже существует: {tg_id}")
            except Exception as e:
                logger.error(f"Ошибка при добавлении пользователя: {e}", exc_info=True)
                raise
    
    def get_user(self, tg_id):
        """Получает пользователя по telegram_id."""
        try:
            with self.get_connection() as conn:
                user = conn.execute(
                    "SELECT * FROM users WHERE telegram_id = ?",
                    (str(tg_id),)
                ).fetchone()
                return dict(user) if user else None
        except Exception as e:
            logger.error(f"Ошибка получения пользователя {tg_id}: {e}", exc_info=True)
            return None
    
    def get_referrer(self, tg_id):
        """Получает telegram_id реферера."""
        try:
            with self.get_connection() as conn:
                user = conn.execute(
                    "SELECT referrer_id FROM users WHERE telegram_id = ?",
                    (str(tg_id),)
                ).fetchone()
                if user and user['referrer_id']:
                    referrer = conn.execute(
                        "SELECT telegram_id FROM users WHERE id = ?",
                        (user['referrer_id'],)
                    ).fetchone()
                    return referrer['telegram_id'] if referrer else None
                return None
        except Exception as e:
            logger.error(f"Ошибка получения реферера {tg_id}: {e}", exc_info=True)
            return None
    
    def update_points(self, tg_id, delta, reason='manual', order_id=None):
        """Обновляет баллы пользователя (атомарно)."""
        with self.lock:
            try:
                with self.get_connection() as conn:
                    user = conn.execute(
                        "SELECT id, points FROM users WHERE telegram_id = ?",
                        (str(tg_id),)
                    ).fetchone()
                    
                    if not user:
                        raise ValueError(f"Пользователь не найден: {tg_id}")
                    
                    user_id = user['id']
                    new_points = max(0, user['points'] + delta)  # Баллы не могут быть отрицательными
                    
                    conn.execute(
                        "UPDATE users SET points = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (new_points, user_id)
                    )
                    
                    # Логирование в points_history
                    conn.execute(
                        "INSERT INTO points_history (user_id, change, reason, order_id) VALUES (?, ?, ?, ?)",
                        (user_id, delta, reason, order_id)
                    )
                    
                    logger.info(f"Баллы обновлены: {tg_id}, изменение: {delta}, новое значение: {new_points}")
            except Exception as e:
                logger.error(f"Ошибка обновления баллов {tg_id}: {e}", exc_info=True)
                raise
    
    # =============== КАТЕГОРИИ ===============
    
    def get_categories(self):
        """Получает список названий категорий."""
        try:
            with self.get_connection() as conn:
                cats = conn.execute(
                    "SELECT name FROM categories ORDER BY id"
                ).fetchall()
                return [c['name'] for c in cats]
        except Exception as e:
            logger.error(f"Ошибка получения категорий: {e}", exc_info=True)
            return []
    
    def get_categories_with_id(self):
        """Получает категории с ID."""
        try:
            with self.get_connection() as conn:
                cats = conn.execute(
                    "SELECT id, name FROM categories ORDER BY id"
                ).fetchall()
                return [(c['id'], c['name']) for c in cats]
        except Exception as e:
            logger.error(f"Ошибка получения категорий с ID: {e}", exc_info=True)
            return []
    
    def get_category_name_by_id(self, cat_id):
        """Получает имя категории по ID."""
        try:
            with self.get_connection() as conn:
                cat = conn.execute(
                    "SELECT name FROM categories WHERE id = ?",
                    (cat_id,)
                ).fetchone()
                return cat['name'] if cat else "Неизвестная"
        except Exception as e:
            logger.error(f"Ошибка получения имени категории {cat_id}: {e}", exc_info=True)
            return "Ошибка"
    
    # =============== ТОВАРЫ ===============
    
    def get_stock_item(self, stock_id):
        """Получает товар по ID."""
        try:
            with self.get_connection() as conn:
                item = conn.execute(
                    "SELECT * FROM stock WHERE id = ?",
                    (stock_id,)
                ).fetchone()
                return dict(item) if item else None
        except Exception as e:
            logger.error(f"Ошибка получения товара {stock_id}: {e}", exc_info=True)
            return None
    
    def get_stock_by_category(self, cat_name):
        """Получает товары по имени категории."""
        try:
            with self.get_connection() as conn:
                items = conn.execute("""
                    SELECT s.* FROM stock s
                    JOIN categories c ON s.category_id = c.id
                    WHERE c.name = ?
                    ORDER BY s.name
                """, (cat_name,)).fetchall()
                return [dict(i) for i in items]
        except Exception as e:
            logger.error(f"Ошибка получения товаров категории {cat_name}: {e}", exc_info=True)
            return []
    
    def get_stock_by_category_id(self, cat_id):
        """Получает товары по ID категории."""
        try:
            with self.get_connection() as conn:
                items = conn.execute(
                    "SELECT * FROM stock WHERE category_id = ? ORDER BY name",
                    (cat_id,)
                ).fetchall()
                return [dict(i) for i in items]
        except Exception as e:
            logger.error(f"Ошибка получения товаров категории {cat_id}: {e}", exc_info=True)
            return []
    
    def reduce_stock(self, stock_id, qty):
        """Уменьшает количество товара на складе."""
        with self.lock:
            try:
                with self.get_connection() as conn:
                    # Проверка наличия перед вычитанием (защита от race condition)
                    item = conn.execute(
                        "SELECT quantity FROM stock WHERE id = ?",
                        (stock_id,)
                    ).fetchone()
                    
                    if not item or item['quantity'] < qty:
                        raise ValueError(f"Недостаточно товара {stock_id} (осталось {item['quantity'] if item else 0}, нужно {qty})")
                    
                    conn.execute(
                        "UPDATE stock SET quantity = quantity - ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (qty, stock_id)
                    )
                    
                    logger.info(f"Склад обновлён: товар {stock_id}, уменьшено на {qty}")
            except Exception as e:
                logger.error(f"Ошибка уменьшения склада {stock_id}: {e}", exc_info=True)
                raise
    
    # =============== КОРЗИНА ===============
    
    def add_to_cart(self, tg_id, stock_id, qty):
        """Добавляет товар в корзину (или увеличивает количество)."""
        with self.lock:
            try:
                with self.get_connection() as conn:
                    user = conn.execute(
                        "SELECT id FROM users WHERE telegram_id = ?",
                        (str(tg_id),)
                    ).fetchone()
                    
                    if not user:
                        raise ValueError("Пользователь не найден")
                    
                    user_id = user['id']
                    item = self.get_stock_item(stock_id)
                    
                    if not item:
                        raise ValueError("Товар не найден")
                    
                    # Проверка наличия
                    if item['quantity'] < qty:
                        raise ValueError(f"Нет в наличии (осталось {item['quantity']}, запрос {qty})")
                    
                    # Если товар уже в корзине, увеличиваем количество
                    existing = conn.execute(
                        "SELECT id, qty FROM cart WHERE user_id = ? AND stock_id = ?",
                        (user_id, stock_id)
                    ).fetchone()
                    
                    if existing:
                        new_qty = existing['qty'] + qty
                        if new_qty > 999:
                            raise ValueError("Максимум 999 товаров одного вида в корзине")
                        conn.execute(
                            "UPDATE cart SET qty = ? WHERE id = ?",
                            (new_qty, existing['id'])
                        )
                        logger.info(f"Увеличено в корзине: пользователь {tg_id}, товар {stock_id}, новое кол-во {new_qty}")
                    else:
                        conn.execute(
                            "INSERT INTO cart (user_id, stock_id, name, size, price, qty) VALUES (?, ?, ?, ?, ?, ?)",
                            (user_id, stock_id, item['name'], item['size'], item['price'], qty)
                        )
                        logger.info(f"Добавлено в корзину: пользователь {tg_id}, товар {stock_id}, кол-во {qty}")
            except Exception as e:
                logger.error(f"Ошибка добавления в корзину {tg_id}: {e}", exc_info=True)
                raise
    
    def get_cart(self, tg_id):
        """Получает содержимое корзины."""
        try:
            with self.get_connection() as conn:
                user = conn.execute(
                    "SELECT id FROM users WHERE telegram_id = ?",
                    (str(tg_id),)
                ).fetchone()
                
                if not user:
                    return []
                
                rows = conn.execute(
                    "SELECT * FROM cart WHERE user_id = ? ORDER BY id",
                    (user['id'],)
                ).fetchall()
                
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Ошибка получения корзины {tg_id}: {e}", exc_info=True)
            return []
    
    def clear_cart(self, tg_id):
        """Очищает корзину пользователя."""
        with self.lock:
            try:
                with self.get_connection() as conn:
                    user = conn.execute(
                        "SELECT id FROM users WHERE telegram_id = ?",
                        (str(tg_id),)
                    ).fetchone()
                    
                    if user:
                        conn.execute("DELETE FROM cart WHERE user_id = ?", (user['id'],))
                        logger.info(f"Корзина очищена: {tg_id}")
            except Exception as e:
                logger.error(f"Ошибка очистки корзины {tg_id}: {e}", exc_info=True)
                raise
    
    # =============== ЗАКАЗЫ (С ТРАНЗАКЦИЯМИ) ===============
    
    def create_order(self, tg_id, items, total):
        """Создаёт заказ (АТОМАРНАЯ ОПЕРАЦИЯ)."""
        with self.lock:
            try:
                with self.get_connection() as conn:
                    user = conn.execute(
                        "SELECT id FROM users WHERE telegram_id = ?",
                        (str(tg_id),)
                    ).fetchone()
                    
                    if not user:
                        raise ValueError("Пользователь не найден")
                    
                    user_id = user['id']
                    
                    # Создание заказа
                    cursor = conn.execute(
                        "INSERT INTO orders (user_id, total, status) VALUES (?, ?, 'pending')",
                        (user_id, total)
                    )
                    order_id = cursor.lastrowid
                    
                    # Добавление позиций заказа
                    for item in items:
                        conn.execute(
                            "INSERT INTO order_items (order_id, name, size, price, qty) VALUES (?, ?, ?, ?, ?)",
                            (order_id, item['name'], item['size'], item['price'], item['qty'])
                        )
                    
                    # Обновление счётчика заказов
                    conn.execute(
                        "UPDATE users SET orders = orders + 1 WHERE id = ?",
                        (user_id,)
                    )
                    
                    # Логирование
                    conn.execute(
                        "INSERT INTO audit_log (action, user_id, details) VALUES (?, ?, ?)",
                        ('order_created', user_id, json.dumps({'order_id': order_id, 'total': total}))
                    )
                    
                    logger.info(f"Заказ создан: {order_id}, пользователь {tg_id}, сумма {total}")
                    
                    return order_id
            except Exception as e:
                logger.error(f"Ошибка создания заказа {tg_id}: {e}", exc_info=True)
                raise
