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
from typing import Optional

import aiosqlite
from yookassa import Configuration, Payment

DB_NAME = "db/shop_manager.db"

# ─────────────────── ИНИЦИАЛИЗАЦИЯ (sync, вызывается один раз) ───────────────────

def init_database():
    os.makedirs("db", exist_ok=True)
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
    _add_column("products", "description", "TEXT")
    _add_column("products", "sale_price", "REAL DEFAULT NULL")

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
                      quantity: int, total_price: float, delivery_address: str = 'Цифровой товар') -> bool:
    if not all(isinstance(x, int) and x > 0 for x in (shop_id, user_id, product_id, quantity)):
        return False
    if not isinstance(total_price, (int, float)) or total_price < 0:
        return False
    async with _db() as db:
        try:
            await db.execute(
                "INSERT INTO orders (shop_id, customer_user_id, product_id, quantity, total_price, delivery_address) VALUES (?,?,?,?,?,?)",
                (shop_id, user_id, product_id, quantity, total_price, delivery_address)
            )
            await db.commit()
            return True
        except Exception as e:
            logging.error(f"Ошибка добавления заказа: {e}")
            return False


async def place_cart_order(shop_id: int, customer_id: int, items, total_price: float, delivery_address: str):
    """Записывает все товары из корзины как отдельные заказы. Возвращает список order_id."""
    order_ids = []
    async with _db() as db:
        for pid, name, price, quantity in items:
            async with db.execute(
                "INSERT INTO orders (shop_id, customer_user_id, product_id, quantity, total_price, delivery_address) VALUES (?,?,?,?,?,?)",
                (shop_id, customer_id, pid, quantity, total_price, delivery_address)
            ) as cur:
                order_ids.append(cur.lastrowid)
        await db.commit()
    return order_ids


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