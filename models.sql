-- Оптимизированная схема базы данных для кофейни

PRAGMA foreign_keys = ON;

-- 1. Пользователи
CREATE TABLE IF NOT EXISTS users (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  telegram_id  TEXT    UNIQUE NOT NULL,
  name         TEXT    NOT NULL,
  phone        TEXT,
  email        TEXT,
  points       REAL    NOT NULL DEFAULT 0,
  referrer_id  INTEGER,
  created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (referrer_id)
    REFERENCES users(id)
    ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_users_referrer   ON users(referrer_id);
CREATE INDEX IF NOT EXISTS idx_users_telegram   ON users(telegram_id);

-- 2. Категории товаров
CREATE TABLE IF NOT EXISTS categories (
  id    INTEGER PRIMARY KEY AUTOINCREMENT,
  name  TEXT    UNIQUE NOT NULL
);

-- 3. Товары (с поддержкой напитков с объёмом и прочих товаров)
CREATE TABLE IF NOT EXISTS stock (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  category_id INTEGER NOT NULL,
  name        TEXT    NOT NULL,
  size        TEXT,                -- NULL для товаров без объёма
  has_size    BOOLEAN NOT NULL,    -- 1 = объём есть, 0 = нет
  price       REAL    NOT NULL,
  quantity    INTEGER NOT NULL DEFAULT 0,
  UNIQUE (name, size),
  FOREIGN KEY (category_id)
    REFERENCES categories(id)
    ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_stock_cat      ON stock(category_id);
CREATE INDEX IF NOT EXISTS idx_stock_name     ON stock(name);
CREATE INDEX IF NOT EXISTS idx_stock_has_size ON stock(has_size);

-- 4. Корзина клиентов
CREATE TABLE IF NOT EXISTS cart (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id    INTEGER NOT NULL,
  stock_id   INTEGER NOT NULL,
  qty        INTEGER NOT NULL DEFAULT 1,
  added_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id)
    REFERENCES users(id)
    ON DELETE CASCADE,
  FOREIGN KEY (stock_id)
    REFERENCES stock(id)
    ON DELETE CASCADE,
  UNIQUE (user_id, stock_id)
);
CREATE INDEX IF NOT EXISTS idx_cart_user   ON cart(user_id);
CREATE INDEX IF NOT EXISTS idx_cart_stock  ON cart(stock_id);

-- 5. Заказы
CREATE TABLE IF NOT EXISTS orders (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id     INTEGER NOT NULL,
  total       REAL    NOT NULL,
  created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id)
    REFERENCES users(id)
    ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_date ON orders(created_at);

-- 6. Позиции в заказе
CREATE TABLE IF NOT EXISTS order_items (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id   INTEGER NOT NULL,
  stock_id   INTEGER NOT NULL,
  price      REAL    NOT NULL,   -- цена на момент покупки
  qty        INTEGER NOT NULL DEFAULT 1,
  FOREIGN KEY (order_id)
    REFERENCES orders(id)
    ON DELETE CASCADE,
  FOREIGN KEY (stock_id)
    REFERENCES stock(id)
    ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_items_order ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_items_stock ON order_items(stock_id);
