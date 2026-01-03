import sqlite3
import os
import logging
import threading
import uuid

from yookassa import Configuration, Payment
from states import UserState, ShopBotState

db_lock = threading.Lock()
DB_NAME = "db/shop_manager.db"

def init_database():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
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
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        role TEXT,
        tg_id INTEGER
    )
    ''')
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_tg_id ON users(tg_id)")
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS shop_admins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        FOREIGN KEY (shop_id) REFERENCES shops (id) ON DELETE CASCADE
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        FOREIGN KEY (shop_id) REFERENCES shops (id) ON DELETE CASCADE
    )
    ''')
    
    cursor.execute('''
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
        FOREIGN KEY (category_id) REFERENCES categories (id) ON DELETE CASCADE
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        rating INTEGER NOT NULL CHECK (rating >= 1 AND rating <= 5),
        review_text TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (shop_id) REFERENCES shops (id) ON DELETE CASCADE
    )
    ''')
    
    cursor.execute('''
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
            FOREIGN KEY (shop_id) REFERENCES shops (id) ON DELETE CASCADE,
            FOREIGN KEY (product_id) REFERENCES products (id) ON DELETE CASCADE
        )
        ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS cart (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        quantity INTEGER NOT NULL DEFAULT 1,
        UNIQUE (shop_id, user_id, product_id),
        FOREIGN KEY (shop_id) REFERENCES shops (id) ON DELETE CASCADE,
        FOREIGN KEY (product_id) REFERENCES products (id) ON DELETE CASCADE
    )
    ''')

    cursor.execute('''
    CREATE VIEW IF NOT EXISTS shop_similarity AS
    SELECT s1.id AS source_shop, s2.id AS similar_shop,
           COUNT(DISTINCT c1.id) AS common_categories
    FROM shops s1
    JOIN shops s2 ON s1.id != s2.id
    JOIN categories c1 ON c1.shop_id = s1.id
    JOIN categories c2 ON c2.shop_id = s2.id AND c1.name = c2.name
    GROUP BY s1.id, s2.id
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS shop_users (
        shop_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL, -- Telegram ID пользователя
        joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (shop_id, user_id),
        FOREIGN KEY (shop_id) REFERENCES shops (id) ON DELETE CASCADE
    )
    ''')
    conn.commit()
    
    cursor.execute("PRAGMA table_info(shops)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'is_running' not in columns:
        cursor.execute("ALTER TABLE shops ADD COLUMN is_running INTEGER DEFAULT 0")
    if 'yookassa_credentials' not in columns:
        cursor.execute("ALTER TABLE shops ADD COLUMN yookassa_credentials TEXT")
    if 'paymaster_token' not in columns:
        cursor.execute("ALTER TABLE shops ADD COLUMN paymaster_token TEXT")
    if 'bot_username' not in columns:
        cursor.execute("ALTER TABLE shops ADD COLUMN bot_username TEXT")
        
    cursor.execute("PRAGMA table_info(products)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'description' not in columns:
        cursor.execute("ALTER TABLE products ADD COLUMN description TEXT")
    
    cursor.execute("DROP VIEW IF EXISTS shop_similarity")
    conn.commit()
    conn.close()

Configuration.account_id = "YOUR_YOOKASSA_SHOP_ID"
Configuration.secret_key = "YOUR_YOOKASSA_SECRET_KEY"

def search_products(shop_id, query=None, search_type='name', price_min=None, price_max=None, category_id=None, sort_by='name'):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    base_query = "SELECT * FROM products p JOIN categories c ON p.category_id = c.id WHERE c.shop_id = ?"
    params = [shop_id]
    if query:
        if search_type == 'id':
            base_query += " AND p.id = ?"
            params.append(int(query))
        else:
            base_query += " AND p.name LIKE ?"
            params.append(f'%{query}%')
    if price_min is not None:
        base_query += " AND p.price >= ?"
        params.append(price_min)
    if price_max is not None:
        base_query += " AND p.price <= ?"
        params.append(price_max)
    if category_id:
        base_query += " AND p.category_id = ?"
        params.append(category_id)
    if sort_by == 'price_asc':
        base_query += " ORDER BY p.price ASC"
    elif sort_by == 'price_desc':
        base_query += " ORDER BY p.price DESC"
    elif sort_by == 'popularity':
        base_query += " ORDER BY p.popularity_score DESC"
    elif sort_by == 'newest':
        base_query += " ORDER BY p.created_at DESC"
    else:
        base_query += " ORDER BY p.name"
    cursor.execute(base_query, params)
    results = cursor.fetchall()
    conn.close()
    return results

def get_similar_shops(shop_id, limit=5):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT AVG(price) FROM products p
        JOIN categories c ON p.category_id = c.id
        WHERE c.shop_id = ?
    """, (shop_id,))
    res = cursor.fetchone()
    current_avg_price = res[0] if res and res[0] else 0

    query = """
    SELECT 
        s.id, 
        s.shop_name, 
        s.bot_username, 
        COALESCE(AVG(r.rating), 0) AS avg_rating,
        (
            -- Фактор 1: Пересечение покупателей (Orders overlap)
            (SELECT COUNT(DISTINCT o2.customer_user_id) 
             FROM orders o1 
             JOIN orders o2 ON o1.customer_user_id = o2.customer_user_id 
             WHERE o1.shop_id = ? AND o2.shop_id = s.id) * 3
             +
            -- Фактор 2: Пересечение категорий (Category overlap)
            (SELECT COUNT(*) 
             FROM categories c1 
             JOIN categories c2 ON c1.name = c2.name 
             WHERE c1.shop_id = ? AND c2.shop_id = s.id) * 2
        ) as similarity_score,
        -- Фактор 3: Средняя цена (для сортировки, если score одинаковый)
        ABS(IFNULL((SELECT AVG(price) FROM products p2 JOIN categories c3 ON p2.category_id = c3.id WHERE c3.shop_id = s.id), 0) - ?) as price_diff
    FROM shops s
    LEFT JOIN reviews r ON s.id = r.shop_id
    WHERE s.id != ? AND s.is_running = 1
    GROUP BY s.id
    HAVING similarity_score > 0 OR price_diff < 1000 -- Показывать хоть немного похожие
    ORDER BY similarity_score DESC, avg_rating DESC, price_diff ASC
    LIMIT ?
    """
    
    cursor.execute(query, (shop_id, shop_id, current_avg_price, shop_id, limit))
    shops = cursor.fetchall()
    conn.close()

    if not shops:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT s.id, s.shop_name, s.bot_username, COALESCE(AVG(r.rating), 0) as rating, 0, 0
            FROM shops s
            LEFT JOIN reviews r ON s.id = r.shop_id
            WHERE s.id != ? AND s.is_running = 1
            GROUP BY s.id
            ORDER BY rating DESC
            LIMIT ?
        """, (shop_id, limit))
        shops = cursor.fetchall()
        conn.close()
        
    return shops

def get_user_shops(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
    SELECT id, shop_name FROM shops WHERE user_id = ?
    UNION
    SELECT s.id, s.shop_name FROM shops s
    JOIN shop_admins sa ON s.id = sa.shop_id
    WHERE sa.user_id = ?
    """, (user_id, user_id))
    shops = cursor.fetchall()
    conn.close()
    return shops

def create_shop(user_id, shop_name):
    if not isinstance(user_id, int) or user_id <= 0:
        return None
    if not shop_name or not isinstance(shop_name, str) or len(shop_name) < 2:
        return None
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO shops (user_id, shop_name) VALUES (?, ?)", (user_id, shop_name))
    shop_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return shop_id

def get_shop_info(shop_id):
    if not isinstance(shop_id, int) or shop_id <= 0:
        return None
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM shops WHERE id = ?", (shop_id,))
    shop = cursor.fetchone()
    conn.close()
    return shop

def get_all_shop_products(shop_id):
    if not isinstance(shop_id, int) or shop_id <= 0:
        return []
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT c.name, p.name, p.price, p.description
        FROM products p
        JOIN categories c ON p.category_id = c.id
        WHERE c.shop_id = ?
        ORDER BY c.name, p.name
    """, (shop_id,))
    products = cursor.fetchall()
    conn.close()
    return products

def update_shop_token(shop_id, token):
    if not isinstance(shop_id, int) or shop_id <= 0:
        return
    if not token or not isinstance(token, str) or len(token) < 30:
        return
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    bot_username = None
    try:
        import telebot
        temp_bot = telebot.TeleBot(token)
        bot_info = temp_bot.get_me()
        bot_username = bot_info.username
        cursor.execute("UPDATE shops SET bot_token = ?, bot_username = ?, is_running = 1 WHERE id = ?", (token, bot_username, shop_id))
        conn.commit()
        logging.info(f"Токен для магазина {shop_id} обновлён, username: @{bot_username}")
    except Exception as e:
        logging.error(f"Ошибка при проверке токена: {e}")
        cursor.execute("UPDATE shops SET bot_token = ?, bot_username = NULL, is_running = 0 WHERE id = ?", (token, shop_id))
        conn.commit()
    conn.close()
    return bot_username

def update_welcome_message(shop_id, message):
    if not isinstance(shop_id, int) or shop_id <= 0:
        return False
    if not message or not isinstance(message, str) or len(message) < 5:
        return False
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE shops SET welcome_message = ? WHERE id = ?", (message, shop_id))
    conn.commit()
    conn.close()
    return True

def get_shop_categories(shop_id):
    if not isinstance(shop_id, int) or shop_id <= 0:
        return []
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM categories WHERE shop_id = ?", (shop_id,))
    categories = cursor.fetchall()
    conn.close()
    return categories

def create_category(shop_id, name):
    if not isinstance(shop_id, int) or shop_id <= 0:
        return None
    if not name or not isinstance(name, str) or len(name) < 2:
        return None
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO categories (shop_id, name) VALUES (?, ?)", (shop_id, name))
    category_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return category_id

def update_category_name(category_id, new_name):
    if not isinstance(category_id, int) or category_id <= 0:
        return False
    if not new_name or not isinstance(new_name, str) or len(new_name) < 2:
        return False
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE categories SET name = ? WHERE id = ?", (new_name, category_id))
    conn.commit()
    conn.close()
    return True

def delete_category(category_id):
    if not isinstance(category_id, int) or category_id <= 0:
        return False
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, image_path FROM products WHERE category_id = ?", (category_id,))
    products = cursor.fetchall()
    for product_id, image_path in products:
        if image_path and os.path.exists(image_path) and "default_not_image" not in image_path:
            try:
                os.remove(image_path)
            except Exception as e:
                logging.error(f"Ошибка при удалении изображения товара: {e}")
    
    cursor.execute("DELETE FROM products WHERE category_id = ?", (category_id,))
    cursor.execute("DELETE FROM categories WHERE id = ?", (category_id,))
    conn.commit()
    conn.close()
    return True

def get_category_products(category_id):
    if not isinstance(category_id, int) or category_id <= 0:
        return []
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, price, image_path, description FROM products WHERE category_id = ?", (category_id,))
    products = cursor.fetchall()
    conn.close()
    return products

def add_product(category_id, name, price, image_path, is_digital=True, description=None):
    if not isinstance(category_id, int) or category_id <= 0:
        return None
    if not name or not isinstance(name, str) or len(name) < 2:
        return None
    if not isinstance(price, (int, float)) or price <= 0:
        return None
    if description is not None and not isinstance(description, str):
        return None
    if image_path is not None and not isinstance(image_path, str):
        return None
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO products (category_id, name, price, image_path, is_digital, description) VALUES (?, ?, ?, ?, ?, ?)", 
                  (category_id, name, price, image_path, is_digital, description))
    product_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return product_id

def update_product(product_id, name=None, price=None, description=None, image_path=None, is_digital=None):
    if not isinstance(product_id, int) or product_id <= 0:
        return
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    updates = []
    params = []
    
    if name is not None:
        if not isinstance(name, str) or len(name) < 2:
            conn.close()
            return
        updates.append("name = ?")
        params.append(name)
    if price is not None:
        if not isinstance(price, (int, float)) or price <= 0:
            conn.close()
            return
        updates.append("price = ?")
        params.append(price)
    if description is not None:
        if not isinstance(description, str):
            conn.close()
            return
        updates.append("description = ?")
        params.append(description)
    if image_path is not None:
        if not isinstance(image_path, str):
            conn.close()
            return
        updates.append("image_path = ?")
        params.append(image_path)
    if is_digital is not None:
        updates.append("is_digital = ?")
        params.append(is_digital)
    
    if updates:
        query = "UPDATE products SET " + ", ".join(updates) + " WHERE id = ?"
        params.append(product_id)
        cursor.execute(query, tuple(params))
        conn.commit()
    
    conn.close()

def delete_product(product_id):
    if not isinstance(product_id, int) or product_id <= 0:
        return
    
    with db_lock:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT image_path FROM products WHERE id = ?", (product_id,))
        result = cursor.fetchone()
        image_path = result[0] if result else None
        cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))
        conn.commit()
        conn.close()
    
    if image_path and os.path.exists(image_path) and "default_not_image" not in image_path:
        try:
            os.remove(image_path)
        except Exception as e:
            logging.error(f"Ошибка при удалении изображения: {e}")

def get_product_info(product_id):
    if not isinstance(product_id, int) or product_id <= 0:
        return None
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
    product = cursor.fetchone()
    conn.close()
    return product

def get_shop_id_by_category(category_id):
    if not isinstance(category_id, int) or category_id <= 0:
        return None
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT shop_id FROM categories WHERE id = ?", (category_id,))
    result = cursor.fetchone()
    shop_id = result[0] if result else None
    conn.close()
    return shop_id

def get_shops_with_ratings():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
    SELECT s.id, s.shop_name, s.bot_username,
           COALESCE(AVG(r.rating), 0) as avg_rating,
           COUNT(r.id) as review_count
    FROM shops s
    LEFT JOIN reviews r ON s.id = r.shop_id
    GROUP BY s.id
    ORDER BY avg_rating DESC, review_count DESC
    ''')
    shops = cursor.fetchall()
    conn.close()
    return shops

def add_review(shop_id, user_id, rating, review_text):
    if not isinstance(shop_id, int) or shop_id <= 0:
        return False
    if not isinstance(user_id, int) or user_id <= 0:
        return False
    if not isinstance(rating, int) or rating < 1 or rating > 5:
        return False
    if review_text is not None and not isinstance(review_text, str):
        return False
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("SELECT 1 FROM reviews WHERE shop_id = ? AND user_id = ?", (shop_id, user_id))
    if cursor.fetchone():
        conn.close()
        return False
    
    cursor.execute("INSERT INTO reviews (shop_id, user_id, rating, review_text) VALUES (?, ?, ?, ?)",
                  (shop_id, user_id, rating, review_text))
    conn.commit()
    conn.close()
    return True

def add_to_cart(shop_id, user_id, product_id, quantity=1):
    if not isinstance(shop_id, int) or shop_id <= 0:
        return False
    if not isinstance(user_id, int) or user_id <= 0:
        return False
    if not isinstance(product_id, int) or product_id <= 0:
        return False
    if not isinstance(quantity, int) or quantity < 1:
        return False
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO cart (shop_id, user_id, product_id, quantity)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (shop_id, user_id, product_id)
        DO UPDATE SET quantity = quantity + ?
    """, (shop_id, user_id, product_id, quantity, quantity))
    cursor.execute("UPDATE products SET popularity_score = popularity_score + ? WHERE id = ?", (quantity, product_id))
    conn.commit()
    conn.close()
    return True

def get_cart_items(shop_id, user_id):
    if not isinstance(shop_id, int) or shop_id <= 0:
        return []
    if not isinstance(user_id, int) or user_id <= 0:
        return []
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT c.product_id, p.name, p.price, c.quantity
        FROM cart c
        JOIN products p ON c.product_id = p.id
        WHERE c.shop_id = ? AND c.user_id = ?
    """, (shop_id, user_id))
    items = cursor.fetchall()
    conn.close()
    return items

def remove_from_cart(shop_id, user_id, product_id):
    if not isinstance(shop_id, int) or shop_id <= 0:
        return
    if not isinstance(user_id, int) or user_id <= 0:
        return
    if not isinstance(product_id, int) or product_id <= 0:
        return
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM cart WHERE shop_id = ? AND user_id = ? AND product_id = ?", 
                  (shop_id, user_id, product_id))
    conn.commit()
    conn.close()

def clear_cart(shop_id, user_id):
    if not isinstance(shop_id, int) or shop_id <= 0:
        return
    if not isinstance(user_id, int) or user_id <= 0:
        return
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM cart WHERE shop_id = ? AND user_id = ?", (shop_id, user_id))
    conn.commit()
    conn.close()

def create_payment_link(amount, product_id, shop_id):
    if not isinstance(amount, (int, float)) or amount <= 0:
        return None
    if not isinstance(product_id, int) or product_id <= 0:
        return None
    if not isinstance(shop_id, int) or shop_id <= 0:
        return None
    shop_info = get_shop_info(shop_id)
    if not shop_info or not shop_info[9]:
        return None
    
    try:
        shop_id_yk, secret_key = shop_info[9].split(":")
        Configuration.account_id = shop_id_yk
        Configuration.secret_key = secret_key
        
        payment = Payment.create({
            "amount": {
                "value": str(amount),
                "currency": "RUB"
            },
            "confirmation": {
                "type": "redirect",
                "return_url": "https://your-site.com/return"
            },
            "capture": True,
            "description": f"Оплата товара #{product_id}",
            "metadata": {"product_id": product_id}
        }, uuid.uuid4().hex)
        return payment.confirmation.confirmation_url
    except Exception as e:
        logging.error(f"Ошибка при создании платежа: {e}")
        return None

def buy_product(shop_id, user_id, product_id, quantity, total_price, delivery_address='Цифровой товар'):
    if not all([isinstance(x, int) and x > 0 for x in (shop_id, user_id, product_id, quantity)]):
        return False
    if not isinstance(total_price, (int, float)) or total_price <= 0:
        return False
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO orders (shop_id, customer_user_id, product_id, quantity, total_price, delivery_address) VALUES (?, ?, ?, ?, ?, ?)",
            (shop_id, user_id, product_id, quantity, total_price, delivery_address)
        )
        conn.commit()
        return True
    except Exception as e:
        logging.error(f"Ошибка при добавлении заказа: {e}")
        return False
    finally:
        conn.close()
    
def get_user_info_by_id(user_id):
    if not isinstance(user_id, int) or user_id <= 0:
        return False
    try:
        import telebot
        from bot.main import bot
        bot.get_chat(user_id)
        return True
    except:
        return False

def is_shop_admin(shop_id, user_id):
    if not isinstance(shop_id, int) or shop_id <= 0:
        return False
    if not isinstance(user_id, int) or user_id <= 0:
        return False
    shop_info = get_shop_info(shop_id)
    if not shop_info:
        return False
    if shop_info[1] == user_id:
        return True
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM shop_admins WHERE shop_id = ? AND user_id = ?", (shop_id, user_id))
    result = cursor.fetchone()
    conn.close()
    return bool(result)

def exists_user(tg_id):
    if not isinstance(tg_id, int) or tg_id <= 0:
        return False
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT username FROM users WHERE tg_id = ?", (tg_id,))
    username = cursor.fetchone()
    conn.close()
    if username is None:
        return False
    return True

def add_user(tg_id, username=None):
    if not isinstance(tg_id, int) or tg_id <= 0:
        return
    if username is not None and not isinstance(username, str):
        return
    
    with db_lock:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM users WHERE tg_id = ?", (tg_id,))
        exists = cursor.fetchone() is not None
        
        if not exists:
            cursor.execute("INSERT INTO users (tg_id, username) VALUES (?, ?)", (tg_id, username))
        elif username:
            cursor.execute("UPDATE users SET username = ? WHERE tg_id = ?", (username, tg_id))
        conn.commit()
        conn.close()

def get_shop_workers(shop_id):
    if not isinstance(shop_id, int) or shop_id <= 0:
        return []
    
    with db_lock:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM shops WHERE id = ?", (shop_id,))
        result = cursor.fetchone()
        if not result:
            conn.close()
            return []
        owner_id = result[0]
        
        cursor.execute("SELECT user_id FROM shop_admins WHERE shop_id = ?", (shop_id,))
        admins = [row[0] for row in cursor.fetchall()]
        worker_ids = list(set([owner_id] + admins))
        
        workers = []
        for worker_id in worker_ids:
            cursor.execute("SELECT tg_id, username FROM users WHERE tg_id = ?", (worker_id,))
            worker = cursor.fetchone()
            if worker:
                workers.append(worker)
        conn.close()
    return workers

def remove_worker(shop_id, user_id):
    if not isinstance(shop_id, int) or shop_id <= 0:
        return False
    if not isinstance(user_id, int) or user_id <= 0:
        return False
    
    with db_lock:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM shops WHERE id = ?", (shop_id,))
        result = cursor.fetchone()
        if not result:
            conn.close()
            return False
        owner_id = result[0]
        
        if user_id == owner_id:
            conn.close()
            return False
        
        cursor.execute("DELETE FROM shop_admins WHERE shop_id = ? AND user_id = ?", (shop_id, user_id))
        conn.commit()
        conn.close()
    return True

def get_shop_orders(shop_id):
    if not isinstance(shop_id, int) or shop_id <= 0:
        return []
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT o.id, o.customer_user_id, p.name, o.quantity, o.total_price, 
               o.delivery_address, o.status, o.created_at, u.username
        FROM orders o
        JOIN products p ON o.product_id = p.id
        LEFT JOIN users u ON o.customer_user_id = u.tg_id
        WHERE o.shop_id = ?
        ORDER BY o.created_at DESC
    ''', (shop_id,))
    orders = cursor.fetchall()
    conn.close()
    return orders


def update_cart_quantity(shop_id, user_id, product_id, delta):
    """Обновляет количество товара в корзине"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Получаем текущее количество
    cursor.execute("SELECT quantity FROM cart WHERE shop_id=? AND user_id=? AND product_id=?", 
                  (shop_id, user_id, product_id))
    result = cursor.fetchone()
    
    if result:
        new_quantity = result[0] + delta
        if new_quantity <= 0:
            # Удаляем товар если количество стало 0 или меньше
            cursor.execute("DELETE FROM cart WHERE shop_id=? AND user_id=? AND product_id=?", 
                          (shop_id, user_id, product_id))
        else:
            # Обновляем количество
            cursor.execute("UPDATE cart SET quantity=? WHERE shop_id=? AND user_id=? AND product_id=?", 
                          (new_quantity, shop_id, user_id, product_id))
    
    conn.commit()
    conn.close()

def get_cart_quantity(shop_id, user_id, product_id):
    """Получает текущее количество товара в корзине"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT quantity FROM cart WHERE shop_id=? AND user_id=? AND product_id=?", 
                  (shop_id, user_id, product_id))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else 0

def update_paymaster_token(shop_id, token):
    if not isinstance(shop_id, int) or shop_id <= 0:
        return False
    if not token or not isinstance(token, str) or len(token) < 10:
        return False
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE shops SET paymaster_token = ? WHERE id = ?", (token, shop_id))
    conn.commit()
    conn.close()
    return True

def get_paymaster_token_by_shop_id(shop_id):
    if not isinstance(shop_id, int) or shop_id <= 0:
        return False
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT paymaster_token FROM shops WHERE id=?", (shop_id,))
    res = cursor.fetchone()
    conn.close()
    return res[0] if res else None

def register_shop_user(shop_id, user_id):
    """Регистрирует пользователя, нажавшего /start в конкретном магазине"""
    if not isinstance(shop_id, int) or not isinstance(user_id, int):
        return
    with db_lock:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO shop_users (shop_id, user_id) VALUES (?, ?)", (shop_id, user_id))
        conn.commit()
        conn.close()
        
def get_shop_user_ids(shop_id):
    """Получает всех пользователей магазина для рассылки"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM shop_users WHERE shop_id = ?", (shop_id,))
    users = [row[0] for row in cursor.fetchall()]
    conn.close()
    return users

def get_shop_reviews(shop_id, page=0, per_page=5):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    offset = page * per_page
    cursor.execute("""
        SELECT u.username, r.rating, r.review_text, r.created_at
        FROM reviews r
        LEFT JOIN users u ON r.user_id = u.tg_id
        WHERE r.shop_id = ?
        ORDER BY r.created_at DESC
        LIMIT ? OFFSET ?
    """, (shop_id, per_page, offset))
    
    reviews = cursor.fetchall()
    
    # Получаем общее количество для пагинации
    cursor.execute("SELECT COUNT(*) FROM reviews WHERE shop_id = ?", (shop_id,))
    total_count = cursor.fetchone()[0]
    
    conn.close()
    return reviews, total_count