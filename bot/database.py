"""
database.py  —  async version (aiosqlite)

Все функции, выполняющие I/O с БД, переписаны как async/await.
Синхронный вариант init_database() сохранён только для однократного
вызова на старте (создание схемы), поскольку это делается до запуска
event-loop в __main__.
"""

import os
import asyncio
import logging
import sqlite3
import uuid
from functools import wraps
from typing import List, Optional

import aiosqlite
from yookassa import Configuration, Payment

import config

DB_NAME = config.DB_PATH

# ── Статусы заказа ──
ORDER_STATUS_NEW              = "new"
ORDER_STATUS_PAID             = "paid"
ORDER_STATUS_PROCESSING       = "processing"
ORDER_STATUS_SHIPPED          = "shipped"
ORDER_STATUS_DELIVERED        = "delivered"
ORDER_STATUS_COMPLETED        = "completed"
ORDER_STATUS_CANCELED         = "canceled"
ORDER_STATUS_REFUND_REQUESTED = "refund_requested"
ORDER_STATUS_REFUNDED         = "refunded"
ORDER_STATUS_DISPUTED         = "disputed"

ORDER_STATUS_LABELS = {
    ORDER_STATUS_NEW:              "🆕 Новый",
    ORDER_STATUS_PAID:             "💰 Оплачен",
    ORDER_STATUS_PROCESSING:       "⚙️ В обработке",
    ORDER_STATUS_SHIPPED:          "📦 Отправлен",
    ORDER_STATUS_DELIVERED:        "🚚 Доставлен",
    ORDER_STATUS_COMPLETED:        "✅ Завершён",
    ORDER_STATUS_CANCELED:         "❌ Отменён",
    ORDER_STATUS_REFUND_REQUESTED: "↩️ Запрошен возврат",
    ORDER_STATUS_REFUNDED:         "💸 Возвращён",
    ORDER_STATUS_DISPUTED:         "⚖️ Спор",
}

# ── Статусы магазина ──
SHOP_STATUS_ACTIVE       = "active"
SHOP_STATUS_UNDER_REVIEW = "under_review"
SHOP_STATUS_BANNED       = "banned"

# ── Статусы спора ──
DISPUTE_STATUS_OPEN     = "open"
DISPUTE_STATUS_RESOLVED = "resolved"
DISPUTE_STATUS_CANCELED = "canceled"

# ─────────────────── ИНИЦИАЛИЗАЦИЯ (sync, вызывается один раз) ───────────────────

def init_database():
    db_dir = os.path.dirname(DB_NAME)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.executescript("""
    PRAGMA journal_mode=WAL;
    PRAGMA foreign_keys=ON;

    CREATE TABLE IF NOT EXISTS shops (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        shop_name TEXT NOT NULL,
        bot_token TEXT,
        payment_method TEXT DEFAULT 'cash_on_delivery',
        welcome_message TEXT DEFAULT 'Добро пожаловать в наш магазин!',
        bot_username TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_running INTEGER DEFAULT 0,
        yookassa_credentials TEXT,
        paymaster_token TEXT
    );

    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        role TEXT,
        tg_id INTEGER UNIQUE
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_users_tg_id ON users(tg_id);

    CREATE TABLE IF NOT EXISTS shop_admins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        FOREIGN KEY (shop_id) REFERENCES shops(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        FOREIGN KEY (shop_id) REFERENCES shops(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        description TEXT,
        price REAL NOT NULL,
        image_path TEXT,
        is_digital BOOLEAN DEFAULT TRUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        popularity_score INTEGER DEFAULT 0,
        sale_price REAL DEFAULT NULL,
        FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        rating INTEGER NOT NULL CHECK (rating >= 1 AND rating <= 5),
        review_text TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (shop_id) REFERENCES shops(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_id INTEGER NOT NULL,
        customer_user_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        quantity INTEGER NOT NULL DEFAULT 1,
        total_price REAL NOT NULL,
        delivery_address TEXT NOT NULL,
        status TEXT DEFAULT 'new',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (shop_id) REFERENCES shops(id) ON DELETE CASCADE,
        FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS cart (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        quantity INTEGER NOT NULL DEFAULT 1,
        UNIQUE (shop_id, user_id, product_id),
        FOREIGN KEY (shop_id) REFERENCES shops(id) ON DELETE CASCADE,
        FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS shop_users (
        shop_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (shop_id, user_id),
        FOREIGN KEY (shop_id) REFERENCES shops(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS promocodes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_id INTEGER NOT NULL,
        code TEXT NOT NULL,
        discount_type TEXT NOT NULL CHECK (discount_type IN ('percent', 'fixed')),
        discount_value REAL NOT NULL,
        max_uses INTEGER,
        uses_count INTEGER DEFAULT 0,
        is_active INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (shop_id, code),
        FOREIGN KEY (shop_id) REFERENCES shops(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS terms_acceptance (
        user_id INTEGER PRIMARY KEY,
        version INTEGER NOT NULL,
        accepted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS complaints (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        reason TEXT NOT NULL,
        status TEXT DEFAULT 'open',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (shop_id, user_id),
        FOREIGN KEY (shop_id) REFERENCES shops(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS disputes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        shop_id INTEGER NOT NULL,
        opened_by INTEGER NOT NULL,
        opener_role TEXT NOT NULL,        -- 'customer' | 'seller'
        reason TEXT NOT NULL,
        status TEXT DEFAULT 'open',       -- 'open' | 'resolved' | 'canceled'
        resolution TEXT,                  -- 'refund' | 'complete' | 'reject' | NULL
        resolution_note TEXT,
        resolved_by INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        closed_at TIMESTAMP,
        FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
        FOREIGN KEY (shop_id) REFERENCES shops(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS dispute_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dispute_id INTEGER NOT NULL,
        author_id INTEGER NOT NULL,
        author_role TEXT NOT NULL,        -- 'customer' | 'seller' | 'moderator'
        body TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (dispute_id) REFERENCES disputes(id) ON DELETE CASCADE
    );

    -- Внутренний баланс продавца (одна строка на магазин). Все суммы в копейках.
    CREATE TABLE IF NOT EXISTS seller_balances (
        shop_id INTEGER PRIMARY KEY,
        amount_kopecks INTEGER NOT NULL DEFAULT 0,
        total_earned_kopecks INTEGER NOT NULL DEFAULT 0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (shop_id) REFERENCES shops(id) ON DELETE CASCADE
    );

    -- Запросы на вывод средств от продавцов.
    CREATE TABLE IF NOT EXISTS withdrawals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_id INTEGER NOT NULL,
        seller_user_id INTEGER NOT NULL,
        amount_kopecks INTEGER NOT NULL,
        method TEXT NOT NULL,             -- 'card' | 'sbp' | 'business' | 'crypto'
        requisites TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'approved',  -- 'approved' | 'paid_out' | 'rejected'
        owner_note TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        processed_at TIMESTAMP,
        FOREIGN KEY (shop_id) REFERENCES shops(id) ON DELETE CASCADE
    );

    -- Служебная таблица для одноразовых миграций.
    CREATE TABLE IF NOT EXISTS db_meta (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """)
    conn.commit()

    # Миграции: добавляем колонки если отсутствуют
    def _add_column(table, col, definition):
        try:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # уже существует

    _add_column("shops", "is_running", "INTEGER DEFAULT 0")
    _add_column("shops", "yookassa_credentials", "TEXT")
    _add_column("shops", "paymaster_token", "TEXT")
    _add_column("shops", "bot_username", "TEXT")
    _add_column("shops", "status", "TEXT DEFAULT 'active'")
    _add_column("shops", "status_reason", "TEXT")
    _add_column("products", "description", "TEXT")
    _add_column("products", "sale_price", "REAL DEFAULT NULL")
    _add_column("products", "digital_content", "TEXT")
    _add_column("products", "digital_content_kind", "TEXT")  # 'text' | 'url' | 'file_id' | 'photo_id'
    _add_column("products", "digital_ttl_hours", "INTEGER")
    _add_column("orders", "updated_at", "TIMESTAMP")
    _add_column("orders", "paid_at", "TIMESTAMP")
    _add_column("orders", "delivered_at", "TIMESTAMP")
    _add_column("orders", "closed_at", "TIMESTAMP")
    _add_column("orders", "payment_method", "TEXT")
    _add_column("orders", "delivery_payload", "TEXT")  # сохранённый цифровой контент, отправленный покупателю
    _add_column("orders", "seller_note", "TEXT")
    _add_column("orders", "order_group_id", "TEXT")

    # Одноразовая миграция: насыпаем seller_balances из истории paid-заказов,
    # чтобы существующие магазины не начинали с 0 после деплоя финансовой части.
    c.execute("SELECT value FROM db_meta WHERE key='balances_backfilled'")
    row = c.fetchone()
    if not row:
        c.execute(f"""
            INSERT OR REPLACE INTO seller_balances (shop_id, amount_kopecks, total_earned_kopecks, updated_at)
            SELECT shop_id,
                   CAST(ROUND(SUM(total_price) * 100) AS INTEGER),
                   CAST(ROUND(SUM(total_price) * 100) AS INTEGER),
                   CURRENT_TIMESTAMP
            FROM orders
            WHERE status IN ('{ORDER_STATUS_PAID}','{ORDER_STATUS_PROCESSING}','{ORDER_STATUS_SHIPPED}',
                             '{ORDER_STATUS_DELIVERED}','{ORDER_STATUS_COMPLETED}')
              AND (payment_method IS NULL OR LOWER(payment_method) <> 'cash_on_delivery')
            GROUP BY shop_id
        """)
        c.execute("INSERT OR REPLACE INTO db_meta (key, value) VALUES ('balances_backfilled', '1')")
        conn.commit()

    conn.close()


# ─────────────────── ВСПОМОГАТЕЛЬНЫЙ КОНТЕКСТ-МЕНЕДЖЕР ───────────────────

def _db():
    """Возвращает async-контекст-менеджер aiosqlite-соединения."""
    return aiosqlite.connect(DB_NAME)


# ─────────────────── ПОИСК / РЕКОМЕНДАЦИИ ───────────────────

async def search_products(shop_id: int, query=None, search_type='name',
                          price_min=None, price_max=None,
                          category_id=None, sort_by='name'):
    base = "SELECT * FROM products p JOIN categories c ON p.category_id = c.id WHERE c.shop_id = ?"
    params = [shop_id]
    if query:
        if search_type == 'id':
            base += " AND p.id = ?"
            params.append(int(query))
        else:
            base += " AND p.name LIKE ?"
            params.append(f'%{query}%')
    if price_min is not None:
        base += " AND p.price >= ?"
        params.append(price_min)
    if price_max is not None:
        base += " AND p.price <= ?"
        params.append(price_max)
    if category_id:
        base += " AND p.category_id = ?"
        params.append(category_id)
    order_map = {
        'price_asc':  "ORDER BY p.price ASC",
        'price_desc': "ORDER BY p.price DESC",
        'popularity': "ORDER BY p.popularity_score DESC",
        'newest':     "ORDER BY p.created_at DESC",
    }
    base += " " + order_map.get(sort_by, "ORDER BY p.name")
    async with _db() as db:
        async with db.execute(base, params) as cur:
            return await cur.fetchall()


async def get_similar_shops(shop_id: int, limit: int = 5):
    async with _db() as db:
        async with db.execute(
            "SELECT AVG(price) FROM products p JOIN categories c ON p.category_id = c.id WHERE c.shop_id = ?",
            (shop_id,)
        ) as cur:
            res = await cur.fetchone()
        avg_price = res[0] if res and res[0] else 0

        query = """
        SELECT s.id, s.shop_name, s.bot_username,
               COALESCE(AVG(r.rating), 0) AS avg_rating,
               (
                 (SELECT COUNT(DISTINCT o2.customer_user_id)
                  FROM orders o1 JOIN orders o2 ON o1.customer_user_id = o2.customer_user_id
                  WHERE o1.shop_id = ? AND o2.shop_id = s.id) * 3
                 +
                 (SELECT COUNT(*)
                  FROM categories c1 JOIN categories c2 ON c1.name = c2.name
                  WHERE c1.shop_id = ? AND c2.shop_id = s.id) * 2
               ) AS similarity_score,
               ABS(IFNULL((SELECT AVG(price) FROM products p2
                           JOIN categories c3 ON p2.category_id = c3.id
                           WHERE c3.shop_id = s.id), 0) - ?) AS price_diff
        FROM shops s
        LEFT JOIN reviews r ON s.id = r.shop_id
        WHERE s.id != ? AND s.is_running = 1
        GROUP BY s.id
        HAVING similarity_score > 0 OR price_diff < 1000
        ORDER BY similarity_score DESC, avg_rating DESC, price_diff ASC
        LIMIT ?
        """
        async with db.execute(query, (shop_id, shop_id, avg_price, shop_id, limit)) as cur:
            shops = await cur.fetchall()

        if not shops:
            async with db.execute("""
                SELECT s.id, s.shop_name, s.bot_username,
                       COALESCE(AVG(r.rating), 0) as rating, 0, 0
                FROM shops s LEFT JOIN reviews r ON s.id = r.shop_id
                WHERE s.id != ? AND s.is_running = 1
                GROUP BY s.id ORDER BY rating DESC LIMIT ?
            """, (shop_id, limit)) as cur:
                shops = await cur.fetchall()

    return shops


# ─────────────────── МАГАЗИНЫ ───────────────────

async def get_user_shops(user_id: int):
    async with _db() as db:
        async with db.execute("""
            SELECT id, shop_name FROM shops WHERE user_id = ?
            UNION
            SELECT s.id, s.shop_name FROM shops s
            JOIN shop_admins sa ON s.id = sa.shop_id
            WHERE sa.user_id = ?
        """, (user_id, user_id)) as cur:
            return await cur.fetchall()


async def create_shop(user_id: int, shop_name: str) -> Optional[int]:
    if not isinstance(user_id, int) or user_id <= 0:
        return None
    if not shop_name or len(shop_name) < 2:
        return None
    async with _db() as db:
        async with db.execute(
            "INSERT INTO shops (user_id, shop_name) VALUES (?, ?)", (user_id, shop_name)
        ) as cur:
            shop_id = cur.lastrowid
        await db.commit()
    return shop_id


async def get_shop_info(shop_id: int):
    if not isinstance(shop_id, int) or shop_id <= 0:
        return None
    async with _db() as db:
        async with db.execute("SELECT * FROM shops WHERE id = ?", (shop_id,)) as cur:
            return await cur.fetchone()


async def get_all_shop_products(shop_id: int):
    if not isinstance(shop_id, int) or shop_id <= 0:
        return []
    async with _db() as db:
        async with db.execute("""
            SELECT c.name, p.name, p.price, p.description
            FROM products p JOIN categories c ON p.category_id = c.id
            WHERE c.shop_id = ?
            ORDER BY c.name, p.name
        """, (shop_id,)) as cur:
            return await cur.fetchall()


async def update_shop_token(shop_id: int, token: str) -> Optional[str]:
    """Проверяет токен через aiogram, сохраняет и возвращает username или None."""
    if not isinstance(shop_id, int) or shop_id <= 0:
        return None
    if not token or len(token) < 30:
        return None
    bot_username = None
    try:
        from aiogram import Bot
        tmp = Bot(token=token)
        me = await tmp.get_me()
        await tmp.session.close()
        bot_username = me.username
        async with _db() as db:
            await db.execute(
                "UPDATE shops SET bot_token=?, bot_username=?, is_running=1 WHERE id=?",
                (token, bot_username, shop_id)
            )
            await db.commit()
        logging.info(f"Токен магазина {shop_id} обновлён, @{bot_username}")
    except Exception as e:
        logging.error(f"Ошибка проверки токена: {e}")
        async with _db() as db:
            await db.execute(
                "UPDATE shops SET bot_token=?, bot_username=NULL, is_running=0 WHERE id=?",
                (token, shop_id)
            )
            await db.commit()
    return bot_username


async def update_welcome_message(shop_id: int, message: str) -> bool:
    if not isinstance(shop_id, int) or shop_id <= 0:
        return False
    if not message or len(message) < 5:
        return False
    async with _db() as db:
        await db.execute("UPDATE shops SET welcome_message=? WHERE id=?", (message, shop_id))
        await db.commit()
    return True


async def update_payment_method(shop_id: int, method: str, credentials: Optional[str] = None):
    async with _db() as db:
        if credentials:
            await db.execute(
                "UPDATE shops SET payment_method=?, yookassa_credentials=? WHERE id=?",
                (method, credentials, shop_id)
            )
        else:
            await db.execute("UPDATE shops SET payment_method=? WHERE id=?", (method, shop_id))
        await db.commit()


async def delete_shop(shop_id: int):
    async with _db() as db:
        await db.execute("DELETE FROM shops WHERE id=?", (shop_id,))
        await db.commit()


async def get_shops_with_ratings():
    async with _db() as db:
        async with db.execute("""
            SELECT s.id, s.shop_name, s.bot_username,
                   COALESCE(AVG(r.rating), 0) as avg_rating,
                   COUNT(r.id) as review_count
            FROM shops s LEFT JOIN reviews r ON s.id = r.shop_id
            GROUP BY s.id
            ORDER BY avg_rating DESC, review_count DESC
        """) as cur:
            return await cur.fetchall()


# ─────────────────── КАТЕГОРИИ ───────────────────

async def get_shop_categories(shop_id: int):
    if not isinstance(shop_id, int) or shop_id <= 0:
        return []
    async with _db() as db:
        async with db.execute(
            "SELECT id, name FROM categories WHERE shop_id=?", (shop_id,)
        ) as cur:
            return await cur.fetchall()


async def create_category(shop_id: int, name: str) -> Optional[int]:
    if not isinstance(shop_id, int) or shop_id <= 0:
        return None
    if not name or len(name) < 2:
        return None
    async with _db() as db:
        async with db.execute(
            "INSERT INTO categories (shop_id, name) VALUES (?, ?)", (shop_id, name)
        ) as cur:
            cid = cur.lastrowid
        await db.commit()
    return cid


async def update_category_name(category_id: int, new_name: str) -> bool:
    if not isinstance(category_id, int) or category_id <= 0:
        return False
    if not new_name or len(new_name) < 2:
        return False
    async with _db() as db:
        await db.execute("UPDATE categories SET name=? WHERE id=?", (new_name, category_id))
        await db.commit()
    return True


async def delete_category(category_id: int) -> bool:
    if not isinstance(category_id, int) or category_id <= 0:
        return False
    async with _db() as db:
        async with db.execute(
            "SELECT id, image_path FROM products WHERE category_id=?", (category_id,)
        ) as cur:
            products = await cur.fetchall()
        for _pid, image_path in products:
            if image_path and os.path.exists(image_path) and "default_not_image" not in image_path:
                try:
                    os.remove(image_path)
                except Exception as e:
                    logging.error(f"Ошибка удаления изображения: {e}")
        await db.execute("DELETE FROM products WHERE category_id=?", (category_id,))
        await db.execute("DELETE FROM categories WHERE id=?", (category_id,))
        await db.commit()
    return True


async def get_shop_id_by_category(category_id: int) -> Optional[int]:
    if not isinstance(category_id, int) or category_id <= 0:
        return None
    async with _db() as db:
        async with db.execute(
            "SELECT shop_id FROM categories WHERE id=?", (category_id,)
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else None


# ─────────────────── ТОВАРЫ ───────────────────

async def get_category_products(category_id: int):
    if not isinstance(category_id, int) or category_id <= 0:
        return []
    async with _db() as db:
        async with db.execute(
            "SELECT id, name, price, image_path, description FROM products WHERE category_id=?",
            (category_id,)
        ) as cur:
            return await cur.fetchall()


async def add_product(category_id: int, name: str, price: float,
                      image_path: Optional[str], is_digital: bool = True,
                      description: Optional[str] = None) -> Optional[int]:
    if not isinstance(category_id, int) or category_id <= 0:
        return None
    if not name or len(name) < 2:
        return None
    if not isinstance(price, (int, float)) or price <= 0:
        return None
    async with _db() as db:
        async with db.execute(
            "INSERT INTO products (category_id, name, price, image_path, is_digital, description) VALUES (?,?,?,?,?,?)",
            (category_id, name, price, image_path, is_digital, description)
        ) as cur:
            pid = cur.lastrowid
        await db.commit()
    return pid


async def update_product(product_id: int, name=None, price=None,
                         description=None, image_path=None, is_digital=None):
    if not isinstance(product_id, int) or product_id <= 0:
        return
    updates, params = [], []
    if name is not None:
        if not isinstance(name, str) or len(name) < 2:
            return
        updates.append("name=?"); params.append(name)
    if price is not None:
        if not isinstance(price, (int, float)) or price <= 0:
            return
        updates.append("price=?"); params.append(price)
    if description is not None:
        updates.append("description=?"); params.append(description)
    if image_path is not None:
        updates.append("image_path=?"); params.append(image_path)
    if is_digital is not None:
        updates.append("is_digital=?"); params.append(is_digital)
    if not updates:
        return
    params.append(product_id)
    async with _db() as db:
        await db.execute(f"UPDATE products SET {', '.join(updates)} WHERE id=?", params)
        await db.commit()


async def delete_product(product_id: int):
    if not isinstance(product_id, int) or product_id <= 0:
        return
    async with _db() as db:
        async with db.execute("SELECT image_path FROM products WHERE id=?", (product_id,)) as cur:
            row = await cur.fetchone()
        image_path = row[0] if row else None
        await db.execute("DELETE FROM products WHERE id=?", (product_id,))
        await db.commit()
    if image_path and os.path.exists(image_path) and "default_not_image" not in image_path:
        try:
            os.remove(image_path)
        except Exception as e:
            logging.error(f"Ошибка удаления изображения: {e}")


async def get_product_info(product_id: int):
    if not isinstance(product_id, int) or product_id <= 0:
        return None
    async with _db() as db:
        async with db.execute("SELECT * FROM products WHERE id=?", (product_id,)) as cur:
            return await cur.fetchone()


async def set_product_sale_price(product_id: int, sale_price) -> bool:
    if not isinstance(product_id, int) or product_id <= 0:
        return False
    if sale_price is not None and (not isinstance(sale_price, (int, float)) or sale_price < 0):
        return False
    async with _db() as db:
        await db.execute("UPDATE products SET sale_price=? WHERE id=?", (sale_price, product_id))
        await db.commit()
    return True


def get_product_display_price(product):
    """Возвращает (отображаемая_цена, оригинальная_цена, есть_скидка). Синхронная — работает с кортежем."""
    try:
        sale_price = product[9] if len(product) > 9 else None
    except (IndexError, TypeError):
        sale_price = None
    original = product[4]
    if sale_price is not None and 0 < sale_price < original:
        return sale_price, original, True
    return original, original, False


# ─────────────────── ПОЛЬЗОВАТЕЛИ ───────────────────

async def add_user(tg_id: int, username: Optional[str] = None):
    if not isinstance(tg_id, int) or tg_id <= 0:
        return
    async with _db() as db:
        await db.execute(
            """INSERT INTO users (tg_id, username) VALUES (?, ?)
               ON CONFLICT(tg_id) DO UPDATE SET username=COALESCE(excluded.username, username)""",
            (tg_id, username)
        )
        await db.commit()


async def exists_user(tg_id: int) -> bool:
    if not isinstance(tg_id, int) or tg_id <= 0:
        return False
    async with _db() as db:
        async with db.execute("SELECT 1 FROM users WHERE tg_id=?", (tg_id,)) as cur:
            return await cur.fetchone() is not None


async def register_shop_user(shop_id: int, user_id: int):
    if not isinstance(shop_id, int) or not isinstance(user_id, int):
        return
    async with _db() as db:
        await db.execute(
            "INSERT OR IGNORE INTO shop_users (shop_id, user_id) VALUES (?, ?)",
            (shop_id, user_id)
        )
        await db.commit()


async def get_shop_user_ids(shop_id: int):
    async with _db() as db:
        async with db.execute("SELECT user_id FROM shop_users WHERE shop_id=?", (shop_id,)) as cur:
            rows = await cur.fetchall()
    return [r[0] for r in rows]


# ─────────────────── РАБОТНИКИ ───────────────────

async def is_shop_admin(shop_id: int, user_id: int) -> bool:
    if not isinstance(shop_id, int) or not isinstance(user_id, int):
        return False
    shop = await get_shop_info(shop_id)
    if not shop:
        return False
    if shop[1] == user_id:
        return True
    async with _db() as db:
        async with db.execute(
            "SELECT 1 FROM shop_admins WHERE shop_id=? AND user_id=?", (shop_id, user_id)
        ) as cur:
            return await cur.fetchone() is not None


async def get_shop_workers(shop_id: int):
    if not isinstance(shop_id, int) or shop_id <= 0:
        return []
    async with _db() as db:
        async with db.execute("SELECT user_id FROM shops WHERE id=?", (shop_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            return []
        owner_id = row[0]
        async with db.execute("SELECT user_id FROM shop_admins WHERE shop_id=?", (shop_id,)) as cur:
            admin_rows = await cur.fetchall()
        worker_ids = list({owner_id} | {r[0] for r in admin_rows})
        workers = []
        for wid in worker_ids:
            async with db.execute("SELECT tg_id, username FROM users WHERE tg_id=?", (wid,)) as cur:
                w = await cur.fetchone()
            if w:
                workers.append(w)
    return workers


async def add_worker(shop_id: int, admin_user_id: int) -> bool:
    """Добавляет работника. Возвращает True если добавлен, False если уже есть."""
    async with _db() as db:
        async with db.execute(
            "INSERT OR IGNORE INTO shop_admins (shop_id, user_id) VALUES (?, ?)",
            (shop_id, admin_user_id)
        ) as cur:
            added = cur.rowcount > 0
        await db.commit()
    return added


async def remove_worker(shop_id: int, user_id: int) -> bool:
    if not isinstance(shop_id, int) or not isinstance(user_id, int):
        return False
    shop = await get_shop_info(shop_id)
    if not shop or shop[1] == user_id:
        return False  # нельзя уволить владельца
    async with _db() as db:
        await db.execute(
            "DELETE FROM shop_admins WHERE shop_id=? AND user_id=?", (shop_id, user_id)
        )
        await db.commit()
    return True


async def get_or_create_user_by_username(username: str) -> Optional[int]:
    """Ищет пользователя по username, создаёт запись если нет. Возвращает tg_id или None."""
    async with _db() as db:
        async with db.execute("SELECT tg_id FROM users WHERE username=?", (username,)) as cur:
            row = await cur.fetchone()
        if row:
            return row[0]
        # Создаём запись без tg_id — вернём None
        return None


async def get_user_by_username(username: str):
    async with _db() as db:
        async with db.execute(
            "SELECT tg_id, username FROM users WHERE username=?", (username,)
        ) as cur:
            return await cur.fetchone()


async def ensure_user_record(tg_id: Optional[int] = None, username: Optional[str] = None) -> Optional[int]:
    """Возвращает tg_id пользователя, создавая запись при необходимости."""
    if tg_id:
        async with _db() as db:
            await db.execute(
                "INSERT OR IGNORE INTO users (tg_id, username) VALUES (?, ?)", (tg_id, username)
            )
            await db.commit()
        return tg_id
    if username:
        async with _db() as db:
            async with db.execute("SELECT tg_id FROM users WHERE username=?", (username,)) as cur:
                row = await cur.fetchone()
            if row:
                return row[0]
            # Пользователь не зарегистрирован в боте — создаём без tg_id
            async with db.execute(
                "INSERT INTO users (username) VALUES (?)", (username,)
            ) as cur:
                pass
            await db.commit()
        return None
    return None


# ─────────────────── ЗАКАЗЫ ───────────────────

async def get_shop_orders(shop_id: int):
    if not isinstance(shop_id, int) or shop_id <= 0:
        return []
    async with _db() as db:
        async with db.execute("""
            SELECT o.id, o.customer_user_id, p.name, o.quantity, o.total_price,
                   o.delivery_address, o.status, o.created_at, u.username
            FROM orders o
            JOIN products p ON o.product_id = p.id
            LEFT JOIN users u ON o.customer_user_id = u.tg_id
            WHERE o.shop_id=?
            ORDER BY o.created_at DESC
        """, (shop_id,)) as cur:
            return await cur.fetchall()


async def buy_product(shop_id: int, user_id: int, product_id: int,
                      quantity: int, total_price: float,
                      delivery_address: str = 'Цифровой товар',
                      status: str = ORDER_STATUS_NEW,
                      payment_method: Optional[str] = None,
                      group_id: Optional[str] = None) -> Optional[int]:
    """Создаёт одну строку заказа. Возвращает id или None."""
    if not all(isinstance(x, int) and x > 0 for x in (shop_id, user_id, product_id, quantity)):
        return None
    if not isinstance(total_price, (int, float)) or total_price < 0:
        return None
    paid_at_sql = "CURRENT_TIMESTAMP" if status == ORDER_STATUS_PAID else "NULL"
    order_id: Optional[int] = None
    async with _db() as db:
        try:
            async with db.execute(
                f"INSERT INTO orders (shop_id, customer_user_id, product_id, quantity, total_price, "
                f"delivery_address, status, payment_method, order_group_id, updated_at, paid_at) "
                f"VALUES (?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,{paid_at_sql})",
                (shop_id, user_id, product_id, quantity, total_price, delivery_address,
                 status, payment_method, group_id)
            ) as cur:
                order_id = cur.lastrowid
        except Exception as e:
            logging.error(f"Ошибка добавления заказа: {e}")
            return None
        finally:
            await db.commit()
    # Если заказ сразу создан как оплаченный — зачисляем продавцу.
    # Cash-on-delivery идёт мимо платформы и баланса не касается.
    is_cash = (payment_method or "").lower() == "cash_on_delivery"
    if order_id and status == ORDER_STATUS_PAID and total_price > 0 and not is_cash:
        await credit_seller_balance(shop_id, total_price, source="order_created_paid")
    return order_id


async def place_cart_order(shop_id: int, customer_id: int, items, total_price: float,
                           delivery_address: str,
                           status: str = ORDER_STATUS_NEW,
                           payment_method: Optional[str] = None,
                           group_id: Optional[str] = None):
    """Записывает товары из корзины как отдельные заказы (одной группой).

    Каждая строка получает свою стоимость `price * quantity`. Параметр
    `total_price` (общий итог по корзине с учётом промокода) сохраняется
    как поле первой строки группы для удобства отображения.

    Возвращает (group_id, [order_id, ...]).
    """
    if not group_id:
        group_id = uuid.uuid4().hex
    paid_at_sql = "CURRENT_TIMESTAMP" if status == ORDER_STATUS_PAID else "NULL"
    order_ids = []
    # Пропорциональное распределение скидки по строкам корзины: считаем
    # коэффициент total_price / gross и применяем к каждой позиции. Так сумма
    # по строкам всегда совпадает с общим итогом, даже когда скидка превышает
    # стоимость одной из позиций (например, промокод 90% на корзину из двух
    # товаров).
    gross = sum(float(p) * int(q) for _pid, _n, p, q in items)
    ratio = 1.0
    if total_price is not None and gross > 0:
        ratio = max(0.0, float(total_price) / gross)
    line_totals = [round(float(p) * int(q) * ratio, 2)
                   for _pid, _n, p, q in items]
    # Округление по строкам может дать копеечную ошибку относительно total_price —
    # компенсируем разницу на последней строке.
    if total_price is not None:
        diff = round(float(total_price) - sum(line_totals), 2)
        if line_totals:
            line_totals[-1] = round(line_totals[-1] + diff, 2)
            if line_totals[-1] < 0:
                line_totals[-1] = 0
    async with _db() as db:
        for idx, (pid, _name, _price, quantity) in enumerate(items):
            line_total = line_totals[idx] if idx < len(line_totals) else 0
            async with db.execute(
                f"INSERT INTO orders (shop_id, customer_user_id, product_id, quantity, total_price, "
                f"delivery_address, status, payment_method, order_group_id, updated_at, paid_at) "
                f"VALUES (?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,{paid_at_sql})",
                (shop_id, customer_id, pid, quantity, line_total, delivery_address,
                 status, payment_method, group_id)
            ) as cur:
                order_ids.append(cur.lastrowid)
        await db.commit()
    # Зачисляем общий итог корзины продавцу (одна строка баланса на группу),
    # только если оплата прошла онлайн через платформу.
    is_cash = (payment_method or "").lower() == "cash_on_delivery"
    if status == ORDER_STATUS_PAID and total_price and total_price > 0 and not is_cash:
        await credit_seller_balance(shop_id, total_price, source="cart_paid")
    return group_id, order_ids


async def get_shop_admins_ids(shop_id: int):
    async with _db() as db:
        async with db.execute("SELECT user_id FROM shop_admins WHERE shop_id=?", (shop_id,)) as cur:
            rows = await cur.fetchall()
    return [r[0] for r in rows]


# ─────────────────── КОРЗИНА ───────────────────

async def add_to_cart(shop_id: int, user_id: int, product_id: int, quantity: int = 1) -> bool:
    if not all(isinstance(x, int) and x > 0 for x in (shop_id, user_id, product_id, quantity)):
        return False
    async with _db() as db:
        await db.execute("""
            INSERT INTO cart (shop_id, user_id, product_id, quantity)
            VALUES (?,?,?,?)
            ON CONFLICT(shop_id, user_id, product_id)
            DO UPDATE SET quantity = quantity + ?
        """, (shop_id, user_id, product_id, quantity, quantity))
        await db.execute(
            "UPDATE products SET popularity_score = popularity_score + ? WHERE id=?",
            (quantity, product_id)
        )
        await db.commit()
    return True


async def get_cart_items(shop_id: int, user_id: int):
    if not isinstance(shop_id, int) or not isinstance(user_id, int):
        return []
    async with _db() as db:
        async with db.execute("""
            SELECT c.product_id, p.name, p.price, c.quantity
            FROM cart c JOIN products p ON c.product_id = p.id
            WHERE c.shop_id=? AND c.user_id=?
        """, (shop_id, user_id)) as cur:
            return await cur.fetchall()


async def remove_from_cart(shop_id: int, user_id: int, product_id: int):
    async with _db() as db:
        await db.execute(
            "DELETE FROM cart WHERE shop_id=? AND user_id=? AND product_id=?",
            (shop_id, user_id, product_id)
        )
        await db.commit()


async def clear_cart(shop_id: int, user_id: int):
    async with _db() as db:
        await db.execute("DELETE FROM cart WHERE shop_id=? AND user_id=?", (shop_id, user_id))
        await db.commit()


async def update_cart_quantity(shop_id: int, user_id: int, product_id: int, delta: int):
    async with _db() as db:
        async with db.execute(
            "SELECT quantity FROM cart WHERE shop_id=? AND user_id=? AND product_id=?",
            (shop_id, user_id, product_id)
        ) as cur:
            row = await cur.fetchone()
        if row:
            new_q = row[0] + delta
            if new_q <= 0:
                await db.execute(
                    "DELETE FROM cart WHERE shop_id=? AND user_id=? AND product_id=?",
                    (shop_id, user_id, product_id)
                )
            else:
                await db.execute(
                    "UPDATE cart SET quantity=? WHERE shop_id=? AND user_id=? AND product_id=?",
                    (new_q, shop_id, user_id, product_id)
                )
        await db.commit()


async def get_cart_quantity(shop_id: int, user_id: int, product_id: int) -> int:
    async with _db() as db:
        async with db.execute(
            "SELECT quantity FROM cart WHERE shop_id=? AND user_id=? AND product_id=?",
            (shop_id, user_id, product_id)
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else 0


# ─────────────────── ОТЗЫВЫ ───────────────────

async def add_review(shop_id: int, user_id: int, rating: int, review_text: Optional[str]) -> bool:
    if not isinstance(rating, int) or not 1 <= rating <= 5:
        return False
    async with _db() as db:
        async with db.execute(
            "SELECT 1 FROM reviews WHERE shop_id=? AND user_id=?", (shop_id, user_id)
        ) as cur:
            if await cur.fetchone():
                return False
        await db.execute(
            "INSERT INTO reviews (shop_id, user_id, rating, review_text) VALUES (?,?,?,?)",
            (shop_id, user_id, rating, review_text)
        )
        await db.commit()
    return True


async def get_shop_reviews(shop_id: int, page: int = 0, per_page: int = 5):
    offset = page * per_page
    async with _db() as db:
        async with db.execute("""
            SELECT u.username, r.rating, r.review_text, r.created_at
            FROM reviews r LEFT JOIN users u ON r.user_id = u.tg_id
            WHERE r.shop_id=?
            ORDER BY r.created_at DESC LIMIT ? OFFSET ?
        """, (shop_id, per_page, offset)) as cur:
            reviews = await cur.fetchall()
        async with db.execute("SELECT COUNT(*) FROM reviews WHERE shop_id=?", (shop_id,)) as cur:
            total = (await cur.fetchone())[0]
    return reviews, total


async def get_shop_rating(shop_id: int):
    async with _db() as db:
        async with db.execute(
            "SELECT AVG(rating), COUNT(*) FROM reviews WHERE shop_id=?", (shop_id,)
        ) as cur:
            return await cur.fetchone()


async def has_user_reviewed(shop_id: int, user_id: int) -> bool:
    async with _db() as db:
        async with db.execute(
            "SELECT 1 FROM reviews WHERE shop_id=? AND user_id=?", (shop_id, user_id)
        ) as cur:
            return await cur.fetchone() is not None


# ─────────────────── ПЛАТЕЖИ ───────────────────

async def update_paymaster_token(shop_id: int, token: str) -> bool:
    if not isinstance(shop_id, int) or shop_id <= 0:
        return False
    if not token or len(token) < 10:
        return False
    async with _db() as db:
        await db.execute("UPDATE shops SET paymaster_token=? WHERE id=?", (token, shop_id))
        await db.commit()
    return True


async def get_paymaster_token_by_shop_id(shop_id: int) -> Optional[str]:
    if not isinstance(shop_id, int) or shop_id <= 0:
        return None
    async with _db() as db:
        async with db.execute("SELECT paymaster_token FROM shops WHERE id=?", (shop_id,)) as cur:
            row = await cur.fetchone()
    return row[0] if row else None


def create_payment_link(amount: float, product_id: int, shop_id_yk: str, secret_key: str) -> Optional[str]:
    """Синхронная — yookassa SDK не поддерживает async."""
    try:
        Configuration.account_id = shop_id_yk
        Configuration.secret_key = secret_key
        payment = Payment.create({
            "amount": {"value": str(amount), "currency": "RUB"},
            "confirmation": {"type": "redirect", "return_url": "https://your-site.com/return"},
            "capture": True,
            "description": f"Оплата товара #{product_id}",
            "metadata": {"product_id": product_id}
        }, uuid.uuid4().hex)
        return payment.confirmation.confirmation_url
    except Exception as e:
        logging.error(f"Ошибка создания платежа: {e}")
        return None


# ─────────────────── ПРОМОКОДЫ ───────────────────

async def create_promocode(shop_id: int, code: str, discount_type: str,
                           discount_value: float, max_uses=None) -> bool:
    if not isinstance(shop_id, int) or shop_id <= 0:
        return False
    if discount_type not in ('percent', 'fixed'):
        return False
    if not isinstance(discount_value, (int, float)) or discount_value <= 0:
        return False
    code = code.upper().strip()
    async with _db() as db:
        try:
            await db.execute(
                "INSERT INTO promocodes (shop_id, code, discount_type, discount_value, max_uses) VALUES (?,?,?,?,?)",
                (shop_id, code, discount_type, discount_value, max_uses)
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def get_shop_promocodes(shop_id: int):
    if not isinstance(shop_id, int) or shop_id <= 0:
        return []
    async with _db() as db:
        async with db.execute(
            "SELECT id, code, discount_type, discount_value, max_uses, uses_count, is_active "
            "FROM promocodes WHERE shop_id=? ORDER BY created_at DESC",
            (shop_id,)
        ) as cur:
            return await cur.fetchall()


async def validate_promocode(shop_id: int, code: str):
    if not isinstance(shop_id, int) or not code:
        return None
    code = code.upper().strip()
    async with _db() as db:
        async with db.execute(
            "SELECT id, code, discount_type, discount_value, max_uses, uses_count "
            "FROM promocodes WHERE shop_id=? AND code=? AND is_active=1",
            (shop_id, code)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    promo_id, code_, dtype, dvalue, max_uses, uses_count = row
    if max_uses is not None and uses_count >= max_uses:
        return None
    return {'id': promo_id, 'code': code_, 'discount_type': dtype,
            'discount_value': dvalue, 'max_uses': max_uses, 'uses_count': uses_count}


async def use_promocode(promo_id: int):
    if not isinstance(promo_id, int) or promo_id <= 0:
        return
    async with _db() as db:
        await db.execute(
            "UPDATE promocodes SET uses_count = uses_count + 1 WHERE id=?", (promo_id,)
        )
        await db.commit()


async def deactivate_promocode(promo_id: int) -> bool:
    if not isinstance(promo_id, int) or promo_id <= 0:
        return False
    async with _db() as db:
        await db.execute("DELETE FROM promocodes WHERE id=?", (promo_id,))
        await db.commit()
    return True


async def get_shop_products_for_promo_check(shop_id: int):
    """Возвращает товары для проверки бесплатности при создании промокода."""
    async with _db() as db:
        async with db.execute("""
            SELECT p.id, p.name, p.price, p.sale_price
            FROM products p JOIN categories c ON p.category_id = c.id
            WHERE c.shop_id=?
        """, (shop_id,)) as cur:
            return await cur.fetchall()

# ─────────────────── ПРИНЯТИЕ ПРАВИЛ (TERMS) ───────────────────

async def has_accepted_terms(user_id: int, version: int) -> bool:
    if not isinstance(user_id, int) or user_id <= 0:
        return False
    async with _db() as db:
        async with db.execute(
            "SELECT version FROM terms_acceptance WHERE user_id=?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
    return bool(row and row[0] >= version)


async def accept_terms(user_id: int, version: int) -> None:
    if not isinstance(user_id, int) or user_id <= 0:
        return
    async with _db() as db:
        await db.execute(
            "INSERT INTO terms_acceptance (user_id, version) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET version=excluded.version, "
            "accepted_at=CURRENT_TIMESTAMP",
            (user_id, version)
        )
        await db.commit()


# ─────────────────── СТАТУС МАГАЗИНА ───────────────────

async def get_shop_status(shop_id: int) -> Optional[str]:
    async with _db() as db:
        async with db.execute("SELECT status FROM shops WHERE id=?", (shop_id,)) as cur:
            row = await cur.fetchone()
    return row[0] if row else None


async def set_shop_status(shop_id: int, status: str, reason: Optional[str] = None) -> None:
    async with _db() as db:
        await db.execute(
            "UPDATE shops SET status=?, status_reason=? WHERE id=?",
            (status, reason, shop_id)
        )
        await db.commit()


async def is_shop_purchases_blocked(shop_id: int) -> bool:
    """Возвращает True, если в магазине нельзя совершать новые покупки."""
    status = await get_shop_status(shop_id)
    return status in (SHOP_STATUS_UNDER_REVIEW, SHOP_STATUS_BANNED)


# ─────────────────── ЖАЛОБЫ ───────────────────

async def add_complaint(shop_id: int, user_id: int, reason: str) -> Optional[int]:
    """Создаёт жалобу. Возвращает id или None если уже есть от этого пользователя."""
    if not isinstance(shop_id, int) or shop_id <= 0:
        return None
    if not isinstance(user_id, int) or user_id <= 0:
        return None
    reason = (reason or "").strip()
    if len(reason) < 3:
        return None
    async with _db() as db:
        try:
            async with db.execute(
                "INSERT INTO complaints (shop_id, user_id, reason) VALUES (?,?,?)",
                (shop_id, user_id, reason)
            ) as cur:
                cid = cur.lastrowid
            await db.commit()
            return cid
        except aiosqlite.IntegrityError:
            return None


async def count_open_complaints(shop_id: int) -> int:
    async with _db() as db:
        async with db.execute(
            "SELECT COUNT(*) FROM complaints WHERE shop_id=? AND status='open'",
            (shop_id,)
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else 0


async def get_open_complaints(shop_id: int):
    async with _db() as db:
        async with db.execute(
            "SELECT c.id, c.user_id, c.reason, c.created_at, u.username "
            "FROM complaints c LEFT JOIN users u ON c.user_id = u.tg_id "
            "WHERE c.shop_id=? AND c.status='open' ORDER BY c.created_at DESC",
            (shop_id,)
        ) as cur:
            return await cur.fetchall()


async def get_shops_under_review():
    async with _db() as db:
        async with db.execute(
            "SELECT id, shop_name, user_id, status_reason FROM shops "
            "WHERE status=? ORDER BY id DESC", (SHOP_STATUS_UNDER_REVIEW,)
        ) as cur:
            return await cur.fetchall()


async def resolve_complaints(shop_id: int, resolution: str) -> int:
    """resolution: 'dismissed' | 'confirmed'. Возвращает количество затронутых строк."""
    async with _db() as db:
        async with db.execute(
            "UPDATE complaints SET status=? WHERE shop_id=? AND status='open'",
            (resolution, shop_id)
        ) as cur:
            n = cur.rowcount
        await db.commit()
    return n


async def has_complained(shop_id: int, user_id: int) -> bool:
    async with _db() as db:
        async with db.execute(
            "SELECT 1 FROM complaints WHERE shop_id=? AND user_id=?",
            (shop_id, user_id)
        ) as cur:
            return await cur.fetchone() is not None


# ─────────────────── ЦИФРОВАЯ ДОСТАВКА ───────────────────

async def update_product_digital(product_id: int, kind: Optional[str],
                                 content: Optional[str],
                                 ttl_hours: Optional[int]) -> bool:
    """kind: 'text' | 'url' | 'photo_path' | 'file_path' | 'photo_id' | 'file_id' | None.

    photo_path/file_path — путь до файла на диске (новый формат, кросс-бот).
    photo_id/file_id — старый формат, оставлен для обратной совместимости.
    """
    if not isinstance(product_id, int) or product_id <= 0:
        return False
    if kind is not None and kind not in (
        "text", "url", "photo_path", "file_path", "photo_id", "file_id"
    ):
        return False
    async with _db() as db:
        await db.execute(
            "UPDATE products SET digital_content=?, digital_content_kind=?, digital_ttl_hours=? WHERE id=?",
            (content, kind, ttl_hours, product_id)
        )
        await db.commit()
    return True


async def get_product_digital(product_id: int):
    """Возвращает (kind, content, ttl_hours) или None."""
    async with _db() as db:
        async with db.execute(
            "SELECT digital_content_kind, digital_content, digital_ttl_hours, is_digital "
            "FROM products WHERE id=?", (product_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    kind, content, ttl, is_digital = row
    return {"kind": kind, "content": content, "ttl_hours": ttl, "is_digital": bool(is_digital)}


# ─────────────────── ЗАКАЗЫ: ЖИЗНЕННЫЙ ЦИКЛ ───────────────────

async def get_order(order_id: int):
    """Полная строка заказа со всеми колонками + название товара."""
    async with _db() as db:
        async with db.execute(
            "SELECT o.id, o.shop_id, o.customer_user_id, o.product_id, o.quantity, "
            "o.total_price, o.delivery_address, o.status, o.created_at, "
            "o.updated_at, o.paid_at, o.delivered_at, o.closed_at, o.payment_method, "
            "o.delivery_payload, o.seller_note, o.order_group_id, "
            "p.name, p.is_digital, p.digital_content, p.digital_content_kind, p.digital_ttl_hours, "
            "u.username, s.shop_name "
            "FROM orders o "
            "JOIN products p ON o.product_id=p.id "
            "LEFT JOIN users u ON o.customer_user_id=u.tg_id "
            "JOIN shops s ON o.shop_id=s.id "
            "WHERE o.id=?",
            (order_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    keys = ["id", "shop_id", "customer_user_id", "product_id", "quantity",
            "total_price", "delivery_address", "status", "created_at",
            "updated_at", "paid_at", "delivered_at", "closed_at", "payment_method",
            "delivery_payload", "seller_note", "order_group_id",
            "product_name", "is_digital", "digital_content", "digital_content_kind",
            "digital_ttl_hours", "username", "shop_name"]
    return dict(zip(keys, row))


async def get_orders_by_group(group_id: str):
    if not group_id:
        return []
    async with _db() as db:
        async with db.execute(
            "SELECT o.id FROM orders o WHERE o.order_group_id=? ORDER BY o.id",
            (group_id,)
        ) as cur:
            rows = await cur.fetchall()
    out = []
    for (oid,) in rows:
        info = await get_order(oid)
        if info:
            out.append(info)
    return out


async def get_user_orders(user_id: int, limit: int = 50):
    async with _db() as db:
        async with db.execute(
            "SELECT o.id, o.order_group_id, o.shop_id, s.shop_name, p.name, "
            "o.quantity, o.total_price, o.status, o.created_at "
            "FROM orders o "
            "JOIN products p ON o.product_id=p.id "
            "JOIN shops s ON o.shop_id=s.id "
            "WHERE o.customer_user_id=? "
            "ORDER BY o.created_at DESC LIMIT ?",
            (user_id, limit)
        ) as cur:
            return await cur.fetchall()


async def get_user_orders_in_shop(shop_id: int, user_id: int, limit: int = 50):
    async with _db() as db:
        async with db.execute(
            "SELECT o.id, o.order_group_id, o.shop_id, s.shop_name, p.name, "
            "o.quantity, o.total_price, o.status, o.created_at "
            "FROM orders o "
            "JOIN products p ON o.product_id=p.id "
            "JOIN shops s ON o.shop_id=s.id "
            "WHERE o.customer_user_id=? AND o.shop_id=? "
            "ORDER BY o.created_at DESC LIMIT ?",
            (user_id, shop_id, limit)
        ) as cur:
            return await cur.fetchall()


async def update_order_status(order_id: int, new_status: str,
                              note: Optional[str] = None) -> bool:
    if new_status not in ORDER_STATUS_LABELS:
        return False
    # Запоминаем старый статус, чтобы корректно начислять/списывать баланс
    # только на ПЕРЕХОДАХ (а не при перезаписи того же статуса).
    old = await get_order(order_id)
    if not old:
        return False
    old_status = old.get("status")
    # Был ли уже зафиксирован факт оплаты (paid_at != NULL). Если да — значит
    # баланс продавца уже зачислялся на этом заказе, и повторно его трогать
    # нельзя (например, при переходе PAID → REFUND_REQUESTED → PAID или
    # PAID → DISPUTED → PAID после отклонения возврата/спора).
    already_paid_once = bool(old.get("paid_at"))
    fields = ["status=?", "updated_at=CURRENT_TIMESTAMP"]
    params = [new_status]
    # paid_at выставляем только при ПЕРВОМ переходе в PAID (чтобы не путать
    # учёт баланса при возвратах в PAID), сохраняя оригинальную дату оплаты.
    if new_status == ORDER_STATUS_PAID and not already_paid_once:
        fields.append("paid_at=CURRENT_TIMESTAMP")
    if new_status == ORDER_STATUS_DELIVERED:
        fields.append("delivered_at=CURRENT_TIMESTAMP")
    if new_status in (ORDER_STATUS_COMPLETED, ORDER_STATUS_REFUNDED, ORDER_STATUS_CANCELED):
        fields.append("closed_at=CURRENT_TIMESTAMP")
    if note is not None:
        fields.append("seller_note=?")
        params.append(note)
    params.append(order_id)
    async with _db() as db:
        await db.execute(
            f"UPDATE orders SET {', '.join(fields)} WHERE id=?", params
        )
        await db.commit()
    # Финансовые эффекты по переходу статуса:
    total = float(old.get("total_price") or 0.0)
    shop_id = old.get("shop_id")
    pmethod = (old.get("payment_method") or "").lower()
    # Cash-on-delivery — деньги не идут через платформу, поэтому баланс
    # продавца на платформе не меняем (он получит наличные напрямую).
    is_cash = pmethod == "cash_on_delivery"
    if total > 0 and shop_id and not is_cash:
        # Перевод в PAID впервые → +на баланс продавца. На повторных переходах
        # (PAID → REFUND_REQUESTED → PAID, PAID → DISPUTED → PAID) баланс уже
        # начислялся при первом PAID — определяем это по выставленному paid_at.
        if (new_status == ORDER_STATUS_PAID
                and old_status != ORDER_STATUS_PAID
                and not already_paid_once):
            await credit_seller_balance(shop_id, total, source=f"order_paid:{order_id}")
        # Возврат после оплаты → списываем с продавца обратно. Если факт оплаты
        # не был зафиксирован (paid_at is NULL — например, NEW → DISPUTED →
        # REFUNDED), значит баланс продавцу никогда не зачислялся, и списывать
        # тоже нечего; иначе бы у продавца уходили деньги, заработанные на
        # других заказах.
        if (new_status == ORDER_STATUS_REFUNDED
                and already_paid_once
                and old_status in (
                    ORDER_STATUS_PAID, ORDER_STATUS_PROCESSING, ORDER_STATUS_SHIPPED,
                    ORDER_STATUS_DELIVERED, ORDER_STATUS_COMPLETED, ORDER_STATUS_DISPUTED,
                    ORDER_STATUS_REFUND_REQUESTED,
                )):
            await debit_seller_balance(shop_id, _rub_to_kop(total))
    return True


async def set_order_delivery_payload(order_id: int, payload: str) -> None:
    async with _db() as db:
        await db.execute(
            "UPDATE orders SET delivery_payload=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (payload, order_id)
        )
        await db.commit()


async def get_paid_unpaid_count_in_shop(shop_id: int):
    async with _db() as db:
        async with db.execute(
            "SELECT status, COUNT(*) FROM orders WHERE shop_id=? GROUP BY status",
            (shop_id,)
        ) as cur:
            rows = await cur.fetchall()
    return dict(rows)


# ─────────────────── СПОРЫ ───────────────────

async def open_dispute(order_id: int, opened_by: int, opener_role: str,
                       reason: str) -> Optional[int]:
    if opener_role not in ("customer", "seller"):
        return None
    reason = (reason or "").strip()
    if len(reason) < 3:
        return None
    order = await get_order(order_id)
    if not order:
        return None
    async with _db() as db:
        # уже есть открытый спор?
        async with db.execute(
            "SELECT id FROM disputes WHERE order_id=? AND status='open'",
            (order_id,)
        ) as cur:
            existing = await cur.fetchone()
        if existing:
            return existing[0]
        async with db.execute(
            "INSERT INTO disputes (order_id, shop_id, opened_by, opener_role, reason) "
            "VALUES (?,?,?,?,?)",
            (order_id, order["shop_id"], opened_by, opener_role, reason)
        ) as cur:
            did = cur.lastrowid
        await db.execute(
            "UPDATE orders SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (ORDER_STATUS_DISPUTED, order_id)
        )
        await db.commit()
    return did


async def get_dispute(dispute_id: int):
    async with _db() as db:
        async with db.execute(
            "SELECT d.id, d.order_id, d.shop_id, d.opened_by, d.opener_role, "
            "d.reason, d.status, d.resolution, d.resolution_note, d.resolved_by, "
            "d.created_at, d.closed_at, s.shop_name "
            "FROM disputes d JOIN shops s ON d.shop_id=s.id WHERE d.id=?",
            (dispute_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    keys = ["id", "order_id", "shop_id", "opened_by", "opener_role", "reason",
            "status", "resolution", "resolution_note", "resolved_by",
            "created_at", "closed_at", "shop_name"]
    return dict(zip(keys, row))


async def add_dispute_message(dispute_id: int, author_id: int,
                              author_role: str, body: str) -> Optional[int]:
    body = (body or "").strip()
    if not body:
        return None
    if author_role not in ("customer", "seller", "moderator"):
        return None
    async with _db() as db:
        async with db.execute(
            "INSERT INTO dispute_messages (dispute_id, author_id, author_role, body) "
            "VALUES (?,?,?,?)",
            (dispute_id, author_id, author_role, body)
        ) as cur:
            mid = cur.lastrowid
        await db.commit()
    return mid


async def get_dispute_messages(dispute_id: int):
    async with _db() as db:
        async with db.execute(
            "SELECT author_id, author_role, body, created_at "
            "FROM dispute_messages WHERE dispute_id=? ORDER BY id",
            (dispute_id,)
        ) as cur:
            return await cur.fetchall()


async def resolve_dispute(dispute_id: int, resolved_by: int,
                          resolution: str,
                          resolution_note: Optional[str] = None) -> bool:
    """resolution: 'refund' | 'complete' | 'reject'"""
    if resolution not in ("refund", "complete", "reject"):
        return False
    dispute = await get_dispute(dispute_id)
    if not dispute or dispute["status"] != DISPUTE_STATUS_OPEN:
        return False
    # Сначала фиксируем решение по самому спору.
    async with _db() as db:
        await db.execute(
            "UPDATE disputes SET status=?, resolution=?, resolution_note=?, "
            "resolved_by=?, closed_at=CURRENT_TIMESTAMP WHERE id=?",
            (DISPUTE_STATUS_RESOLVED, resolution, resolution_note, resolved_by, dispute_id)
        )
        await db.commit()
    # Дальше меняем статус заказа ЧЕРЕЗ update_order_status — там зашита
    # финансовая логика (списание баланса продавца при возврате,
    # начисление при PAID и т.п.). Прямой UPDATE по orders ломал бы баланс.
    if resolution == "refund":
        new_status = ORDER_STATUS_REFUNDED
    elif resolution == "complete":
        new_status = ORDER_STATUS_COMPLETED
    else:  # reject
        new_status = ORDER_STATUS_PAID
    await update_order_status(dispute["order_id"], new_status)
    return True


async def get_open_disputes(limit: int = 50):
    async with _db() as db:
        async with db.execute(
            "SELECT d.id, d.order_id, d.shop_id, s.shop_name, d.opener_role, "
            "d.reason, d.created_at "
            "FROM disputes d JOIN shops s ON d.shop_id=s.id "
            "WHERE d.status='open' ORDER BY d.created_at LIMIT ?",
            (limit,)
        ) as cur:
            return await cur.fetchall()


async def get_open_disputes_for_shop(shop_id: int):
    async with _db() as db:
        async with db.execute(
            "SELECT d.id, d.order_id, d.opener_role, d.reason, d.created_at "
            "FROM disputes d "
            "WHERE d.shop_id=? AND d.status='open' ORDER BY d.created_at",
            (shop_id,)
        ) as cur:
            return await cur.fetchall()


async def is_user_in_dispute(order_id: int, user_id: int) -> Optional[int]:
    """Если пользователь — сторона открытого спора по этому заказу, вернуть dispute_id."""
    order = await get_order(order_id)
    if not order:
        return None
    async with _db() as db:
        async with db.execute(
            "SELECT d.id, d.opened_by FROM disputes d "
            "WHERE d.order_id=? AND d.status='open'", (order_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    return row[0]


# ─────────────────── ФИНАНСЫ: БАЛАНС И ВЫВОДЫ ───────────────────

# Способы вывода
WITHDRAWAL_METHOD_CARD     = "card"
WITHDRAWAL_METHOD_SBP      = "sbp"
WITHDRAWAL_METHOD_BUSINESS = "business"
WITHDRAWAL_METHOD_CRYPTO   = "crypto"

WITHDRAWAL_METHODS = (
    WITHDRAWAL_METHOD_CARD,
    WITHDRAWAL_METHOD_SBP,
    WITHDRAWAL_METHOD_BUSINESS,
    WITHDRAWAL_METHOD_CRYPTO,
)

WITHDRAWAL_METHOD_LABELS = {
    WITHDRAWAL_METHOD_CARD:     "💳 Банковская карта",
    WITHDRAWAL_METHOD_SBP:      "📱 СБП (телефон)",
    WITHDRAWAL_METHOD_BUSINESS: "🧾 Реквизиты ИП/самозанятого",
    WITHDRAWAL_METHOD_CRYPTO:   "🪙 Криптокошелёк",
}

# Статусы вывода
WITHDRAWAL_STATUS_APPROVED = "approved"
WITHDRAWAL_STATUS_PAID_OUT = "paid_out"
WITHDRAWAL_STATUS_REJECTED = "rejected"

WITHDRAWAL_STATUS_LABELS = {
    WITHDRAWAL_STATUS_APPROVED: "⏳ Одобрен, ждёт выплаты",
    WITHDRAWAL_STATUS_PAID_OUT: "✅ Выплачено",
    WITHDRAWAL_STATUS_REJECTED: "❌ Отклонено",
}


def _rub_to_kop(rub: float) -> int:
    """Превращает рубли в копейки безопасно (округлением)."""
    try:
        return int(round(float(rub) * 100))
    except (TypeError, ValueError):
        return 0


def _kop_to_rub(kop: int) -> float:
    try:
        return round(int(kop) / 100, 2)
    except (TypeError, ValueError):
        return 0.0


async def get_seller_balance(shop_id: int) -> dict:
    """Возвращает {amount_kopecks, total_earned_kopecks, amount_rub, total_earned_rub}."""
    async with _db() as db:
        async with db.execute(
            "SELECT amount_kopecks, total_earned_kopecks FROM seller_balances WHERE shop_id=?",
            (shop_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return {"amount_kopecks": 0, "total_earned_kopecks": 0,
                "amount_rub": 0.0, "total_earned_rub": 0.0}
    amt, tot = int(row[0] or 0), int(row[1] or 0)
    return {
        "amount_kopecks": amt,
        "total_earned_kopecks": tot,
        "amount_rub": _kop_to_rub(amt),
        "total_earned_rub": _kop_to_rub(tot),
    }


async def credit_seller_balance(shop_id: int, amount_rub: float, *,
                                source: str = "order") -> bool:
    """Зачисляет рубли на баланс продавца (через копейки)."""
    if not isinstance(shop_id, int) or shop_id <= 0:
        return False
    kop = _rub_to_kop(amount_rub)
    if kop <= 0:
        return False
    async with _db() as db:
        # UPSERT: создаём строку если ещё нет.
        await db.execute(
            "INSERT INTO seller_balances (shop_id, amount_kopecks, total_earned_kopecks, updated_at) "
            "VALUES (?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(shop_id) DO UPDATE SET "
            "  amount_kopecks=amount_kopecks+excluded.amount_kopecks, "
            "  total_earned_kopecks=total_earned_kopecks+excluded.total_earned_kopecks, "
            "  updated_at=CURRENT_TIMESTAMP",
            (shop_id, kop, kop)
        )
        await db.commit()
    return True


async def debit_seller_balance(shop_id: int, amount_kopecks: int) -> bool:
    """Атомарно снимает копейки с баланса продавца. Возвращает False если средств не хватает."""
    if not isinstance(shop_id, int) or shop_id <= 0:
        return False
    if amount_kopecks <= 0:
        return False
    async with _db() as db:
        async with db.execute(
            "UPDATE seller_balances SET amount_kopecks=amount_kopecks-?, updated_at=CURRENT_TIMESTAMP "
            "WHERE shop_id=? AND amount_kopecks>=?",
            (amount_kopecks, shop_id, amount_kopecks)
        ) as cur:
            ok = cur.rowcount > 0
        await db.commit()
    return ok


async def create_withdrawal(shop_id: int, seller_user_id: int,
                            amount_rub: float, method: str,
                            requisites: str) -> Optional[int]:
    """Создаёт запрос на вывод и атомарно списывает деньги с баланса.

    Возвращает id записи или None, если средств не хватает / валидация не прошла.
    Статус выставляется сразу 'approved' (по решению пользователя — авто-одобрение).
    """
    if method not in WITHDRAWAL_METHODS:
        return None
    requisites = (requisites or "").strip()
    if len(requisites) < 4:
        return None
    kop = _rub_to_kop(amount_rub)
    if kop <= 0:
        return None
    # Сначала пытаемся списать; если не хватило — отбой.
    if not await debit_seller_balance(shop_id, kop):
        return None
    async with _db() as db:
        async with db.execute(
            "INSERT INTO withdrawals (shop_id, seller_user_id, amount_kopecks, method, requisites, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (shop_id, seller_user_id, kop, method, requisites, WITHDRAWAL_STATUS_APPROVED)
        ) as cur:
            wid = cur.lastrowid
        await db.commit()
    return wid


async def get_withdrawal(wid: int) -> Optional[dict]:
    async with _db() as db:
        async with db.execute(
            "SELECT id, shop_id, seller_user_id, amount_kopecks, method, requisites, "
            "       status, owner_note, created_at, processed_at "
            "FROM withdrawals WHERE id=?", (wid,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0], "shop_id": row[1], "seller_user_id": row[2],
        "amount_kopecks": row[3], "amount_rub": _kop_to_rub(row[3]),
        "method": row[4], "requisites": row[5],
        "status": row[6], "owner_note": row[7],
        "created_at": row[8], "processed_at": row[9],
    }


async def list_shop_withdrawals(shop_id: int, limit: int = 20) -> List[dict]:
    async with _db() as db:
        async with db.execute(
            "SELECT id, amount_kopecks, method, requisites, status, created_at, processed_at "
            "FROM withdrawals WHERE shop_id=? ORDER BY id DESC LIMIT ?",
            (shop_id, limit)
        ) as cur:
            rows = await cur.fetchall()
    return [{
        "id": r[0], "amount_kopecks": r[1], "amount_rub": _kop_to_rub(r[1]),
        "method": r[2], "requisites": r[3], "status": r[4],
        "created_at": r[5], "processed_at": r[6],
    } for r in rows]


async def list_pending_withdrawals(limit: int = 50) -> List[dict]:
    """Список выводов, ожидающих фактической выплаты владельцем."""
    async with _db() as db:
        async with db.execute(
            "SELECT w.id, w.shop_id, s.shop_name, w.seller_user_id, w.amount_kopecks, "
            "       w.method, w.requisites, w.created_at "
            "FROM withdrawals w LEFT JOIN shops s ON w.shop_id=s.id "
            "WHERE w.status=? ORDER BY w.id ASC LIMIT ?",
            (WITHDRAWAL_STATUS_APPROVED, limit)
        ) as cur:
            rows = await cur.fetchall()
    return [{
        "id": r[0], "shop_id": r[1], "shop_name": r[2],
        "seller_user_id": r[3], "amount_kopecks": r[4], "amount_rub": _kop_to_rub(r[4]),
        "method": r[5], "requisites": r[6], "created_at": r[7],
    } for r in rows]


async def mark_withdrawal_paid_out(wid: int, owner_note: Optional[str] = None) -> bool:
    """Отмечает вывод как фактически выплаченный."""
    async with _db() as db:
        async with db.execute(
            "UPDATE withdrawals SET status=?, owner_note=?, processed_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND status=?",
            (WITHDRAWAL_STATUS_PAID_OUT, owner_note, wid, WITHDRAWAL_STATUS_APPROVED)
        ) as cur:
            ok = cur.rowcount > 0
        await db.commit()
    return ok


async def reject_withdrawal(wid: int, owner_note: Optional[str] = None) -> bool:
    """Отклоняет вывод и возвращает деньги на баланс продавца. Идемпотентно
    и устойчиво к гонкам: статус меняем атомарно (status='approved' → 'rejected'),
    и зачисляем средства только если строка реально перешла в rejected. Если
    параллельный вызов уже обработал тот же wid (rowcount=0), деньги не
    возвращаем повторно."""
    w = await get_withdrawal(wid)
    if not w or w["status"] != WITHDRAWAL_STATUS_APPROVED:
        return False
    shop_id = w["shop_id"]
    amount_kop = int(w["amount_kopecks"] or 0)
    async with _db() as db:
        # Сначала атомарный UPDATE с проверкой исходного статуса.
        async with db.execute(
            "UPDATE withdrawals SET status=?, owner_note=?, processed_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND status=?",
            (WITHDRAWAL_STATUS_REJECTED, owner_note, wid, WITHDRAWAL_STATUS_APPROVED)
        ) as cur:
            changed = cur.rowcount > 0
        if not changed:
            # Кто-то уже обработал этот вывод — ничего не возвращаем повторно.
            await db.commit()
            return False
        # Только после успешного перевода в rejected возвращаем деньги на баланс.
        if amount_kop > 0:
            await db.execute(
                "INSERT INTO seller_balances (shop_id, amount_kopecks, total_earned_kopecks, updated_at) "
                "VALUES (?, ?, 0, CURRENT_TIMESTAMP) "
                "ON CONFLICT(shop_id) DO UPDATE SET "
                "  amount_kopecks=amount_kopecks+excluded.amount_kopecks, "
                "  updated_at=CURRENT_TIMESTAMP",
                (shop_id, amount_kop)
            )
        await db.commit()
    return True
