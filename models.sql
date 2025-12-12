-- models.sql
-- Полная схема с защитой от race conditions и оптимизацией

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;      -- Write-Ahead Logging (лучшая параллелизация)


-- ======== ТАБЛИЦА ПОЛЬЗОВАТЕЛЕЙ ========
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    points INTEGER DEFAULT 0 CHECK (points >= 0),  -- Баллы не могут быть отрицательными
    referrer_id INTEGER,
    orders INTEGER DEFAULT 0,                       -- Количество заказов
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (referrer_id) REFERENCES users(id)
);

-- Индексы для ускорения поиска
CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id);
CREATE INDEX IF NOT EXISTS idx_users_referrer_id ON users(referrer_id);


-- ======== ТАБЛИЦА КАТЕГОРИЙ ========
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_categories_name ON categories(name);


-- ======== ТАБЛИЦА ТОВАРОВ (СКЛАД/МЕНЮ) ========
CREATE TABLE IF NOT EXISTS stock (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    size TEXT,
    has_size INTEGER DEFAULT 0 CHECK (has_size IN (0, 1)),
    price INTEGER NOT NULL CHECK (price > 0),
    quantity INTEGER DEFAULT 0 CHECK (quantity >= 0),  -- Не может быть отрицательным
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE
);

-- Индексы для ускорения запросов
CREATE INDEX IF NOT EXISTS idx_stock_category_id ON stock(category_id);
CREATE INDEX IF NOT EXISTS idx_stock_quantity ON stock(quantity);  -- Для быстрого поиска доступных товаров


-- ======== ТАБЛИЦА КОРЗИНЫ ========
CREATE TABLE IF NOT EXISTS cart (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    stock_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    size TEXT,
    price INTEGER NOT NULL CHECK (price > 0),
    qty INTEGER NOT NULL CHECK (qty > 0 AND qty <= 999),  -- Защита от спама
    added_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (stock_id) REFERENCES stock(id) ON DELETE CASCADE,
    UNIQUE (user_id, stock_id)  -- Один товар один раз в корзине
);

CREATE INDEX IF NOT EXISTS idx_cart_user_id ON cart(user_id);
CREATE INDEX IF NOT EXISTS idx_cart_stock_id ON cart(stock_id);


-- ======== ТАБЛИЦА ЗАКАЗОВ ========
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    total INTEGER NOT NULL CHECK (total >= 0),
    discount INTEGER DEFAULT 0 CHECK (discount >= 0),  -- Примененная скидка
    paid INTEGER DEFAULT 0 CHECK (paid IN (0, 1)),      -- Статус оплаты
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'completed', 'cancelled')),
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders(created_at);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);


-- ======== ТАБЛИЦА СОДЕРЖИМОГО ЗАКАЗА ========
CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    stock_id INTEGER,
    name TEXT NOT NULL,
    size TEXT,
    price INTEGER NOT NULL CHECK (price > 0),
    qty INTEGER NOT NULL CHECK (qty > 0),
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
    FOREIGN KEY (stock_id) REFERENCES stock(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON order_items(order_id);


-- ======== ТАБЛИЦА ЛОГИРОВАНИЯ ОПЕРАЦИЙ ========
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    user_id INTEGER,
    details TEXT,  -- JSON для сложных данных
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_log_user_id ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON audit_log(created_at);


-- ======== ТАБЛИЦА ТРАНЗАКЦИЙ БАЛЛОВ ========
CREATE TABLE IF NOT EXISTS points_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    change INTEGER NOT NULL,  -- Положительное или отрицательное
    reason TEXT NOT NULL,      -- 'purchase', 'referral', 'bonus', etc.
    order_id INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_points_history_user_id ON points_history(user_id);
CREATE INDEX IF NOT EXISTS idx_points_history_created_at ON points_history(created_at);
